"""File express (取件码) — temporary share packages with pickup codes.

Layout under the upload archive root::

    file/
      express/
        express.db
        2026-07-16/
          ab12cd34_payload.bin

Packages expire by ``expires_at``; cleanup runs on write and at app startup.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import shutil
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.settings import get_settings

logger = logging.getLogger("toolkit.express")

_SAFE_RE = re.compile(r"[^\w\u4e00-\u9fff.\-]+", re.UNICODE)
_CODE_RE = re.compile(r"^\d{6}$")
_lock = threading.RLock()
_last_cleanup_ts: float = 0.0
_CLEANUP_INTERVAL = 120.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS packages (
    id              TEXT PRIMARY KEY,
    code            TEXT NOT NULL UNIQUE,
    original_name   TEXT NOT NULL DEFAULT '',
    stored_rel      TEXT NOT NULL DEFAULT '',
    size_bytes      INTEGER NOT NULL DEFAULT 0,
    content_type    TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT '',
    expires_at      TEXT NOT NULL DEFAULT '',
    max_downloads   INTEGER NOT NULL DEFAULT 0,
    download_count  INTEGER NOT NULL DEFAULT 0,
    note            TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_express_code ON packages(code);
CREATE INDEX IF NOT EXISTS idx_express_expires ON packages(expires_at);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        raw = s.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def express_root() -> Path:
    """Absolute directory for express packages (under upload archive)."""
    from storage.history import file_dir

    configured = (os.environ.get("EXPRESS_DIR") or "").strip()
    if configured:
        return Path(configured)
    return file_dir() / "express"


def express_max_bytes() -> int:
    s = get_settings()
    return int(getattr(s, "express_max_bytes", 0) or s.max_upload_bytes)


def express_default_ttl_hours() -> int:
    return int(getattr(get_settings(), "express_default_ttl_hours", 24) or 24)


def express_max_ttl_hours() -> int:
    return int(getattr(get_settings(), "express_max_ttl_hours", 168) or 168)


def ensure_express_dir() -> Path:
    path = express_root()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _db_path() -> Path:
    return ensure_express_dir() / "express.db"


def _get_conn() -> sqlite3.Connection:
    path = _db_path()
    conn = sqlite3.connect(str(path), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _safe_name(name: str, default: str = "file") -> str:
    base = os.path.basename(name or default)
    stem, ext = os.path.splitext(base)
    stem = _SAFE_RE.sub("_", stem).strip("._") or default
    ext = re.sub(r"[^\w.]", "", ext)[:20]
    return (stem[:100] + ext) if ext else stem[:100]


def _normalize_code(code: str) -> str:
    return re.sub(r"\s+", "", (code or "").strip())


def is_valid_code_format(code: str) -> bool:
    return bool(_CODE_RE.match(_normalize_code(code)))


def _generate_code(conn: sqlite3.Connection) -> str:
    """Allocate a unique 6-digit pickup code."""
    for _ in range(64):
        code = f"{secrets.randbelow(1_000_000):06d}"
        exists = conn.execute(
            "SELECT 1 FROM packages WHERE code = ?", (code,)
        ).fetchone()
        if not exists:
            return code
    # Extremely unlikely collision storm
    raise RuntimeError("Unable to allocate pickup code")


def _row_public(row: sqlite3.Row | Dict[str, Any], *, include_path: bool = False) -> Dict[str, Any]:
    d = dict(row) if not isinstance(row, dict) else dict(row)
    expires = _parse_iso(str(d.get("expires_at") or ""))
    now = _now()
    remaining = None
    expired = True
    if expires is not None:
        expired = expires <= now
        remaining = max(0, int((expires - now).total_seconds()))
    max_dl = int(d.get("max_downloads") or 0)
    used = int(d.get("download_count") or 0)
    exhausted = max_dl > 0 and used >= max_dl
    out: Dict[str, Any] = {
        "id": d.get("id"),
        "code": d.get("code"),
        "original_name": d.get("original_name") or "",
        "size_bytes": int(d.get("size_bytes") or 0),
        "content_type": d.get("content_type") or "",
        "created_at": d.get("created_at") or "",
        "expires_at": d.get("expires_at") or "",
        "max_downloads": max_dl,
        "download_count": used,
        "downloads_left": None if max_dl <= 0 else max(0, max_dl - used),
        "note": d.get("note") or "",
        "expired": expired,
        "exhausted": exhausted,
        "available": (not expired) and (not exhausted),
        "seconds_remaining": remaining,
    }
    if include_path:
        out["stored_rel"] = d.get("stored_rel") or ""
    return out


def _unlink_package_file(stored_rel: str) -> None:
    if not stored_rel:
        return
    root = ensure_express_dir().resolve()
    candidate = (root / str(stored_rel)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return
    try:
        if candidate.is_file():
            candidate.unlink()
        # prune empty day dir
        parent = candidate.parent
        if parent != root and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        pass


def cleanup_express(*, force: bool = False) -> int:
    """Remove expired / exhausted packages. Returns deleted count."""
    global _last_cleanup_ts
    now_mono = time.monotonic()
    if not force and now_mono - _last_cleanup_ts < _CLEANUP_INTERVAL:
        return 0
    _last_cleanup_ts = now_mono
    return _do_cleanup()


def _do_cleanup() -> int:
    """Drop packages past expires_at.

    Exhausted (download limit) packages are kept until TTL so callers can still
    receive a clear ``exhausted`` error instead of a generic invalid code.
    """
    now_iso = _iso(_now())
    removed = 0
    with _lock:
        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM packages WHERE expires_at <= ?",
                (now_iso,),
            ).fetchall()
            for row in rows:
                _unlink_package_file(str(row["stored_rel"] or ""))
            removed = len(rows)
            if removed:
                conn.execute(
                    "DELETE FROM packages WHERE expires_at <= ?",
                    (now_iso,),
                )
                conn.commit()
                logger.info("express cleanup removed=%s", removed)
        finally:
            conn.close()
    return removed


def create_package(
    source_path: str | Path,
    original_name: str,
    *,
    content_type: str = "",
    ttl_hours: Optional[int] = None,
    max_downloads: int = 0,
    note: str = "",
) -> Dict[str, Any]:
    """Store a file and return public package info including pickup code."""
    cleanup_express()
    src = Path(source_path)
    if not src.is_file() or src.stat().st_size <= 0:
        raise ValueError("empty or missing file")

    max_ttl = express_max_ttl_hours()
    default_ttl = express_default_ttl_hours()
    hours = int(ttl_hours if ttl_hours is not None else default_ttl)
    hours = max(1, min(max_ttl, hours))
    max_dl = max(0, min(1000, int(max_downloads or 0)))
    note_s = (note or "").strip()[:200]
    name = _safe_name(original_name, "file")
    size = int(src.stat().st_size)
    limit = express_max_bytes()
    if size > limit:
        raise ValueError(f"file too large (max {limit // (1024 * 1024)} MB)")

    pkg_id = uuid.uuid4().hex[:16]
    day = _now().strftime("%Y-%m-%d")
    day_dir = ensure_express_dir() / day
    day_dir.mkdir(parents=True, exist_ok=True)
    # Keep original extension for download Content-Disposition friendliness
    _, ext = os.path.splitext(name)
    dest_name = f"{pkg_id}{ext}" if ext else pkg_id
    dest = day_dir / dest_name
    shutil.copy2(str(src), str(dest))
    stored_rel = f"{day}/{dest_name}"

    created = _now()
    expires = created + timedelta(hours=hours)
    created_iso = _iso(created)
    expires_iso = _iso(expires)

    with _lock:
        conn = _get_conn()
        try:
            code = _generate_code(conn)
            conn.execute(
                """
                INSERT INTO packages (
                    id, code, original_name, stored_rel, size_bytes, content_type,
                    created_at, expires_at, max_downloads, download_count, note
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    pkg_id,
                    code,
                    name,
                    stored_rel,
                    size,
                    (content_type or "")[:120],
                    created_iso,
                    expires_iso,
                    max_dl,
                    note_s,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM packages WHERE id = ?", (pkg_id,)
            ).fetchone()
        finally:
            conn.close()

    assert row is not None
    return _row_public(row)


def get_package_by_code(code: str) -> Optional[Dict[str, Any]]:
    """Lookup by pickup code (does not consume a download)."""
    c = _normalize_code(code)
    if not is_valid_code_format(c):
        return None
    cleanup_express()
    with _lock:
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM packages WHERE code = ?", (c,)
            ).fetchone()
            if row is None:
                return None
            info = _row_public(row, include_path=True)
        finally:
            conn.close()
    return info


def resolve_package_file(info: Dict[str, Any]) -> Optional[Path]:
    rel = str(info.get("stored_rel") or "")
    if not rel:
        return None
    root = ensure_express_dir().resolve()
    path = (root / rel).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    if not path.is_file():
        return None
    return path


def claim_download(code: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Atomically check availability and increment download_count.

    Returns ``(info, error_code)`` where error_code is one of:
    ``invalid``, ``expired``, ``exhausted``, ``missing``, or None on success.
    """
    c = _normalize_code(code)
    if not is_valid_code_format(c):
        return None, "invalid"

    cleanup_express()
    with _lock:
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM packages WHERE code = ?", (c,)
            ).fetchone()
            if row is None:
                return None, "invalid"
            info = _row_public(row, include_path=True)
            if info["expired"]:
                return info, "expired"
            if info["exhausted"]:
                return info, "exhausted"
            path = resolve_package_file(info)
            if path is None:
                return info, "missing"
            conn.execute(
                "UPDATE packages SET download_count = download_count + 1 WHERE id = ?",
                (info["id"],),
            )
            conn.commit()
            row2 = conn.execute(
                "SELECT * FROM packages WHERE id = ?", (info["id"],)
            ).fetchone()
            out = _row_public(row2, include_path=True) if row2 else info
            out["_abs_path"] = str(path)
            return out, None
        finally:
            conn.close()


def express_stats() -> Dict[str, Any]:
    cleanup_express()
    with _lock:
        conn = _get_conn()
        try:
            total = conn.execute("SELECT COUNT(*) FROM packages").fetchone()[0]
            bytes_sum = conn.execute(
                "SELECT COALESCE(SUM(size_bytes), 0) FROM packages"
            ).fetchone()[0]
        finally:
            conn.close()
    return {
        "package_count": int(total or 0),
        "bytes_stored": int(bytes_sum or 0),
        "express_dir": str(express_root()),
        "max_bytes": express_max_bytes(),
        "default_ttl_hours": express_default_ttl_hours(),
        "max_ttl_hours": express_max_ttl_hours(),
    }


def _package_with_file(row: sqlite3.Row | Dict[str, Any]) -> Dict[str, Any]:
    """Public package dict plus admin file-path fields."""
    info = _row_public(row, include_path=True)
    path = resolve_package_file(info)
    info["file_exists"] = path is not None
    return info


def get_package_by_id(package_id: str) -> Optional[Dict[str, Any]]:
    """Lookup by package id (admin). Includes stored_rel / file_exists."""
    pid = (package_id or "").strip()
    if not pid:
        return None
    cleanup_express()
    with _lock:
        conn = _get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM packages WHERE id = ?", (pid,)
            ).fetchone()
            if row is None:
                return None
            return _package_with_file(row)
        finally:
            conn.close()


def list_packages(
    limit: int = 50,
    *,
    q: Optional[str] = None,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List packages for admin (newest first).

    Optional filters:
    - ``q``: substring match on code / original_name / note / id
    - ``status``: ``available`` | ``expired`` | ``exhausted`` | ``missing``
    """
    cleanup_express()
    limit = max(1, min(500, int(limit)))
    q_f = (q or "").strip().lower()
    status_f = (status or "").strip().lower()
    with _lock:
        conn = _get_conn()
        try:
            # Fetch a wider window when filtering so status/q still see enough rows.
            fetch_n = limit if not (q_f or status_f) else min(500, max(limit * 5, 200))
            rows = conn.execute(
                "SELECT * FROM packages ORDER BY created_at DESC LIMIT ?",
                (fetch_n,),
            ).fetchall()
            items = [_package_with_file(r) for r in rows]
        finally:
            conn.close()

    if q_f:
        items = [
            p
            for p in items
            if q_f in str(p.get("code") or "").lower()
            or q_f in str(p.get("original_name") or "").lower()
            or q_f in str(p.get("note") or "").lower()
            or q_f in str(p.get("id") or "").lower()
        ]
    if status_f == "available":
        items = [p for p in items if p.get("available") and p.get("file_exists")]
    elif status_f == "expired":
        items = [p for p in items if p.get("expired")]
    elif status_f == "exhausted":
        items = [p for p in items if p.get("exhausted") and not p.get("expired")]
    elif status_f == "missing":
        items = [p for p in items if not p.get("file_exists")]

    return items[:limit]


def delete_package(package_id: str) -> bool:
    """Delete one package and its file. Returns True if a row was removed."""
    return delete_packages([package_id]) == 1


def delete_packages(package_ids: List[str]) -> int:
    """Delete multiple packages by id. Returns number of rows removed."""
    ids = []
    seen = set()
    for raw in package_ids or []:
        pid = str(raw or "").strip()
        if not pid or pid in seen:
            continue
        seen.add(pid)
        ids.append(pid)
    if not ids:
        return 0

    removed = 0
    with _lock:
        conn = _get_conn()
        try:
            for pid in ids:
                row = conn.execute(
                    "SELECT * FROM packages WHERE id = ?", (pid,)
                ).fetchone()
                if row is None:
                    continue
                _unlink_package_file(str(row["stored_rel"] or ""))
                conn.execute("DELETE FROM packages WHERE id = ?", (pid,))
                removed += 1
            if removed:
                conn.commit()
                logger.info("express delete packages removed=%s", removed)
        finally:
            conn.close()
    return removed
