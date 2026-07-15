"""Persist recent upload records under ``file/`` (input files only).

Layout::

    file/
      records.db            # SQLite metadata (WAL mode)
      2026-07-12/
        20260712T153045_a1b2_in.pdf

Only the uploaded input is archived. Conversion outputs are not stored.
Files and index entries older than the configured retention window are
deleted on each write and can also be purged explicitly.

Paths and retention come from ``core.settings.get_settings()`` so env
changes (and test cache clears) stay consistent with the rest of the app.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.settings import get_settings

logger = logging.getLogger("toolkit.storage")

BASE_DIR = Path(__file__).resolve().parent.parent
DB_NAME = "records.db"
LEGACY_JSON_NAME = "records.json"

_SAFE_RE = re.compile(r"[^\w\u4e00-\u9fff.\-]+", re.UNICODE)
_lock = threading.Lock()
_last_cleanup_ts: float = 0.0
_CLEANUP_INTERVAL: float = 300.0  # seconds

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id          TEXT PRIMARY KEY,
    tool        TEXT NOT NULL DEFAULT '',
    original_name TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT '',
    input_rel   TEXT NOT NULL DEFAULT '',
    input_bytes INTEGER NOT NULL DEFAULT 0,
    extra_json  TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_records_created ON records(created_at DESC);
"""


def file_dir() -> Path:
    """Absolute directory for archived uploads (from settings)."""
    configured = get_settings().upload_file_dir
    if configured:
        return Path(configured)
    return BASE_DIR / "file"


def retention_days() -> int:
    """How many days to keep archived inputs."""
    return max(1, int(get_settings().upload_retention_days))


# Backward-compatible names used by tests / older imports.
def __getattr__(name: str) -> Any:
    if name == "FILE_DIR":
        return file_dir()
    if name == "RETENTION_DAYS":
        return retention_days()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _safe_name(name: str, default: str = "file") -> str:
    base = os.path.basename(name or default)
    stem, ext = os.path.splitext(base)
    stem = _SAFE_RE.sub("_", stem).strip("._") or default
    ext = re.sub(r"[^\w.]", "", ext)[:12]
    return (stem[:80] + ext) if ext else stem[:80]


def ensure_file_dir() -> Path:
    path = file_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _db_path() -> Path:
    return ensure_file_dir() / DB_NAME


def _get_conn() -> sqlite3.Connection:
    """Return a connection to the records DB (creates if needed)."""
    path = _db_path()
    conn = sqlite3.connect(str(path), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _migrate_json_if_needed(conn: sqlite3.Connection) -> None:
    """Import legacy records.json into SQLite (one-time migration)."""
    json_path = ensure_file_dir() / LEGACY_JSON_NAME
    if not json_path.is_file():
        return
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, list):
        return
    # Check if DB already has records (migration already done)
    count = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    if count > 0:
        # Already migrated; remove legacy file
        try:
            json_path.unlink()
        except OSError:
            pass
        return
    rows = []
    for rec in data:
        if not isinstance(rec, dict):
            continue
        extra = {k: v for k, v in rec.items()
                 if k not in ("id", "tool", "original_name", "created_at",
                              "input_rel", "input_bytes")}
        rows.append((
            rec.get("id", ""),
            rec.get("tool", ""),
            rec.get("original_name", ""),
            rec.get("created_at", ""),
            rec.get("input_rel", ""),
            int(rec.get("input_bytes") or 0),
            json.dumps(extra, ensure_ascii=False),
        ))
    if rows:
        conn.executemany(
            "INSERT OR IGNORE INTO records "
            "(id, tool, original_name, created_at, input_rel, input_bytes, extra_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    try:
        json_path.unlink()
    except OSError:
        pass
    logger.info("Migrated %d records from legacy JSON to SQLite", len(rows))


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    extra = {}
    try:
        extra = json.loads(d.get("extra_json") or "{}")
    except (json.JSONDecodeError, TypeError):
        pass
    d.pop("extra_json", None)
    d.update(extra)
    return d


def _cutoff() -> datetime:
    return _now() - timedelta(days=retention_days())


def _is_expired(record: Dict[str, Any], cutoff: datetime) -> bool:
    ts = _parse_iso(str(record.get("created_at") or ""))
    if ts is None:
        return True
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts < cutoff


def _unlink_quiet(path: Path) -> None:
    try:
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass


def _remove_record_files(record: Dict[str, Any]) -> None:
    root = file_dir()
    for key in ("input_rel", "output_rel"):
        rel = record.get(key)
        if not rel:
            continue
        candidate = (root / str(rel)).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            continue
        _unlink_quiet(candidate)


def cleanup_expired() -> int:
    """Delete records/files older than retention. Returns removed count."""
    global _last_cleanup_ts
    import time

    now_ts = time.monotonic()
    if now_ts - _last_cleanup_ts < _CLEANUP_INTERVAL:
        return 0
    _last_cleanup_ts = now_ts
    return _do_cleanup()


def _do_cleanup() -> int:
    root = ensure_file_dir()
    cutoff = _cutoff()
    cutoff_iso = _iso(cutoff)
    removed = 0
    with _lock:
        conn = _get_conn()
        try:
            _migrate_json_if_needed(conn)
            expired = conn.execute(
                "SELECT * FROM records WHERE created_at < ? OR created_at = ''",
                (cutoff_iso,),
            ).fetchall()
            for row in expired:
                rec = _row_to_dict(row)
                _remove_record_files(rec)
            removed = len(expired)
            if removed:
                conn.execute(
                    "DELETE FROM records WHERE created_at < ? OR created_at = ''",
                    (cutoff_iso,),
                )
                conn.commit()

            for child in list(root.iterdir()):
                if not child.is_dir():
                    continue
                try:
                    day = datetime.strptime(child.name, "%Y-%m-%d").replace(
                        tzinfo=timezone.utc
                    )
                except ValueError:
                    continue
                day_start = cutoff.replace(hour=0, minute=0, second=0, microsecond=0)
                if day >= day_start:
                    continue
                try:
                    newest = max(
                        (p.stat().st_mtime for p in child.rglob("*") if p.is_file()),
                        default=0,
                    )
                except OSError:
                    newest = 0
                if newest == 0 or datetime.fromtimestamp(
                    newest, tz=timezone.utc
                ) < cutoff:
                    shutil.rmtree(child, ignore_errors=True)
        finally:
            conn.close()
    return removed


def archive_conversion(
    *,
    tool: str,
    original_name: str,
    input_path: str,
    extra: Optional[Dict[str, Any]] = None,
    **_ignored: Any,
) -> Optional[Dict[str, Any]]:
    """Copy the uploaded input into ``file/`` and append a record.

    Conversion outputs are intentionally not stored. Extra keyword args
    (e.g. legacy ``output_path``) are ignored for compatibility.

    Never raises to conversion callers — history failures must not break downloads.
    """
    try:
        return _archive_conversion(
            tool=tool,
            original_name=original_name,
            input_path=input_path,
            extra=extra,
        )
    except Exception:
        logger.warning(
            "archive_conversion failed tool=%s name=%s",
            tool,
            original_name,
            exc_info=True,
        )
        return None


def _archive_conversion(
    *,
    tool: str,
    original_name: str,
    input_path: str,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    src_in = Path(input_path)
    if not src_in.is_file():
        raise FileNotFoundError(input_path)

    now = _now()
    day = now.strftime("%Y-%m-%d")
    stamp = now.strftime("%Y%m%dT%H%M%S")
    short = uuid.uuid4().hex[:6]
    uid = f"{stamp}_{short}"

    day_dir = ensure_file_dir() / day
    day_dir.mkdir(parents=True, exist_ok=True)

    in_name = _safe_name(original_name, "input")
    in_ext = Path(in_name).suffix or src_in.suffix
    stored_in = f"{uid}_in{in_ext}"
    dest_in = day_dir / stored_in
    shutil.copy2(src_in, dest_in)

    record: Dict[str, Any] = {
        "id": uid,
        "tool": tool,
        "original_name": original_name or in_name,
        "created_at": _iso(now),
        "input_rel": f"{day}/{stored_in}",
        "input_bytes": dest_in.stat().st_size,
    }
    # Collect extra fields that are not in the core schema.
    extra_fields: Dict[str, Any] = {}
    if extra:
        for k, v in extra.items():
            if k in record:
                continue
            if isinstance(v, (str, int, float, bool)) or v is None:
                extra_fields[k] = v
            else:
                extra_fields[k] = str(v)

    with _lock:
        conn = _get_conn()
        try:
            _migrate_json_if_needed(conn)
            conn.execute(
                "INSERT INTO records "
                "(id, tool, original_name, created_at, input_rel, input_bytes, extra_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    uid, tool, record["original_name"], record["created_at"],
                    record["input_rel"], record["input_bytes"],
                    json.dumps(extra_fields, ensure_ascii=False),
                ),
            )
            # Purge expired records inline.
            cutoff = _cutoff()
            cutoff_iso = _iso(cutoff)
            expired = conn.execute(
                "SELECT * FROM records WHERE created_at < ? OR created_at = ''",
                (cutoff_iso,),
            ).fetchall()
            for row in expired:
                rec = _row_to_dict(row)
                if rec.get("id") != uid:
                    _remove_record_files(rec)
            conn.execute(
                "DELETE FROM records WHERE created_at < ? OR created_at = ''",
                (cutoff_iso,),
            )
            conn.commit()
        finally:
            conn.close()

    try:
        _do_cleanup()
    except Exception:
        logger.warning("post-archive cleanup failed", exc_info=True)

    record.update(extra_fields)
    return record


def list_records(limit: int = 50) -> List[Dict[str, Any]]:
    """Return recent records (newest first), purging expired first."""
    try:
        cleanup_expired()
    except Exception:
        logger.warning("list_records cleanup failed", exc_info=True)
    root = file_dir()
    limit = max(1, min(int(limit), 200))
    with _lock:
        conn = _get_conn()
        try:
            _migrate_json_if_needed(conn)
            rows = conn.execute(
                "SELECT * FROM records ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            conn.close()
    out: List[Dict[str, Any]] = []
    for row in rows:
        item = _row_to_dict(row)
        rel = item.get("input_rel")
        item["input_exists"] = bool(rel) and (root / str(rel)).is_file()
        out.append(item)
    return out


def record_count() -> int:
    """Number of non-expired history records (lightweight; no per-file stat)."""
    try:
        cleanup_expired()
    except Exception:
        pass
    with _lock:
        conn = _get_conn()
        try:
            _migrate_json_if_needed(conn)
            return conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        finally:
            conn.close()


def resolve_stored(rel: str) -> Optional[Path]:
    """Resolve a relative stored path under ``file/`` safely."""
    if not rel:
        return None
    parts = Path(rel.replace("\\", "/")).parts
    if ".." in parts:
        return None
    root = file_dir()
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    if candidate.is_file():
        return candidate
    return None


def get_record(record_id: str) -> Optional[Dict[str, Any]]:
    """Return one record by id (with ``input_exists``), or None."""
    if not record_id:
        return None
    root = file_dir()
    with _lock:
        conn = _get_conn()
        try:
            _migrate_json_if_needed(conn)
            row = conn.execute(
                "SELECT * FROM records WHERE id = ?", (record_id,)
            ).fetchone()
        finally:
            conn.close()
    if row is None:
        return None
    item = _row_to_dict(row)
    rel = item.get("input_rel")
    item["input_exists"] = bool(rel) and (root / str(rel)).is_file()
    return item


def delete_record(record_id: str) -> bool:
    """Delete one history record and its stored input file. Returns True if removed."""
    if not record_id:
        return False
    return delete_records([record_id]) == 1


def delete_records(record_ids: List[str]) -> int:
    """Delete multiple history records and their files. Returns removed count.

    Unknown or empty ids are skipped. Deduplicates while preserving order.
    """
    if not record_ids:
        return 0
    seen: set[str] = set()
    ids: List[str] = []
    for rid in record_ids:
        text = str(rid or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ids.append(text)
    if not ids:
        return 0

    removed = 0
    with _lock:
        conn = _get_conn()
        try:
            _migrate_json_if_needed(conn)
            for record_id in ids:
                row = conn.execute(
                    "SELECT * FROM records WHERE id = ?", (record_id,)
                ).fetchone()
                if row is None:
                    continue
                rec = _row_to_dict(row)
                _remove_record_files(rec)
                conn.execute("DELETE FROM records WHERE id = ?", (record_id,))
                removed += 1
            if removed:
                conn.commit()
        finally:
            conn.close()
    return removed


def storage_stats() -> Dict[str, Any]:
    """Aggregate stats for admin dashboard."""
    try:
        cleanup_expired()
    except Exception:
        logger.warning("storage_stats cleanup failed", exc_info=True)
    root = file_dir()
    with _lock:
        conn = _get_conn()
        try:
            _migrate_json_if_needed(conn)
            rows = conn.execute(
                "SELECT * FROM records ORDER BY created_at DESC"
            ).fetchall()
        finally:
            conn.close()

    records = [_row_to_dict(r) for r in rows]
    by_tool: Dict[str, int] = {}
    total_bytes = 0
    with_file = 0
    for rec in records:
        tool = str(rec.get("tool") or "unknown")
        by_tool[tool] = by_tool.get(tool, 0) + 1
        try:
            total_bytes += int(rec.get("input_bytes") or 0)
        except (TypeError, ValueError):
            pass
        rel = rec.get("input_rel")
        if rel and (root / str(rel)).is_file():
            with_file += 1

    disk_bytes = 0
    try:
        for p in root.rglob("*"):
            if p.is_file():
                try:
                    disk_bytes += p.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass

    return {
        "retention_days": retention_days(),
        "file_dir": str(root),
        "record_count": len(records),
        "files_present": with_file,
        "bytes_indexed": total_bytes,
        "bytes_on_disk": disk_bytes,
        "by_tool": by_tool,
        "latest": records[0] if records else None,
    }
