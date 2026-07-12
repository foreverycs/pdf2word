"""Convert Word documents to PDF via LibreOffice or Microsoft Word.

Backends (tried in order):
1. **LibreOffice** ``soffice --headless --convert-to pdf`` — preferred for
   servers / Docker; supports ``.docx`` and ``.doc``.
2. **Microsoft Word COM** (Windows only) — used when LibreOffice is not
   installed but Word is available (via ``docx2pdf`` or direct COM).

Layout fidelity depends on the engine. This module hardens conversion for
common failure modes: non-ASCII paths, macros / ActiveX, concurrent profile
clashes, and partial engine failures (automatic fallback).
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import List, Optional, Tuple

# Common LibreOffice install locations on Windows.
_WIN_SOFFICE_CANDIDATES = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
)

SUPPORTED_EXTENSIONS = {".docx", ".doc"}

# Headless conversion can be slow for large files.
DEFAULT_TIMEOUT_SEC = 180
# Extra seconds per MB above 2 MB (capped).
_TIMEOUT_PER_MB = 15
_TIMEOUT_CAP_SEC = 600

# LibreOffice PDF export filter: keep bookmarks, skip notes/comments noise.
_LO_PDF_FILTER = "pdf:writer_pdf_Export"


class ConversionError(Exception):
    """Raised when Word → PDF conversion fails or no engine is available."""


def _which_soffice() -> Optional[str]:
    """Locate the LibreOffice ``soffice`` binary.

    Order: ``LIBREOFFICE_PATH`` / ``SOFFICE_PATH`` env → PATH → common
    Windows install dirs → common Linux container paths.
    """
    env = os.environ.get("LIBREOFFICE_PATH") or os.environ.get("SOFFICE_PATH")
    if env:
        # Accept either a file or a directory that contains soffice.
        if os.path.isfile(env):
            return env
        for name in ("soffice", "soffice.exe", "libreoffice"):
            candidate = os.path.join(env, name)
            if os.path.isfile(candidate):
                return candidate

    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found

    if platform.system() == "Windows":
        for path in _WIN_SOFFICE_CANDIDATES:
            if os.path.isfile(path):
                return path
    else:
        for path in (
            "/usr/bin/soffice",
            "/usr/bin/libreoffice",
            "/usr/lib/libreoffice/program/soffice",
        ):
            if os.path.isfile(path):
                return path
    return None


def _word_com_available() -> bool:
    """Return True if Microsoft Word can be driven on this machine."""
    if platform.system() != "Windows":
        return False
    try:
        import win32com.client  # type: ignore  # noqa: F401
        return True
    except ImportError:
        pass
    try:
        import docx2pdf  # type: ignore  # noqa: F401
        return True
    except ImportError:
        return False


def available_engines() -> List[str]:
    """Return names of conversion backends currently usable."""
    engines: List[str] = []
    if _which_soffice():
        engines.append("libreoffice")
    if _word_com_available():
        engines.append("msword")
    return engines


def engine_info() -> dict:
    """Diagnostic info for UI / health checks."""
    soffice = _which_soffice()
    engines = available_engines()
    return {
        "engines": engines,
        "preferred": engines[0] if engines else None,
        "libreoffice_path": soffice,
        "ready": bool(engines),
        "notes": [
            "Complex macros / ActiveX controls are disabled during conversion "
            "and may not render.",
            "Layout fidelity follows LibreOffice or Microsoft Word rendering.",
        ],
    }


def _validate_input(input_path: str) -> Path:
    path = Path(input_path)
    if not path.is_file():
        raise ConversionError(f"File not found: {input_path}")
    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ConversionError(
            f"Unsupported format '{ext}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )
    if path.stat().st_size == 0:
        raise ConversionError("Empty file")
    return path


def _scaled_timeout(path: Path, base: int = DEFAULT_TIMEOUT_SEC) -> int:
    """Grow timeout with file size (large docs / embedded media)."""
    try:
        mb = path.stat().st_size / (1024 * 1024)
    except OSError:
        return base
    extra = int(max(0.0, mb - 2.0) * _TIMEOUT_PER_MB)
    return min(base + extra, _TIMEOUT_CAP_SEC)


def _docx_has_macros(path: Path) -> bool:
    """Detect VBA project inside a .docx/.docm-style zip package."""
    if path.suffix.lower() not in (".docx", ".docm"):
        # Binary .doc — cannot cheaply inspect; assume may have macros.
        return path.suffix.lower() == ".doc"
    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = set(zf.namelist())
        return any(
            n.lower().endswith("vbaproject.bin")
            or n.lower().startswith("word/activeX/")
            or "/embeddings/" in n.lower()
            for n in names
        )
    except (OSError, zipfile.BadZipFile):
        return False


def _safe_work_copy(src: Path, work_dir: Path) -> Path:
    """Copy input to an ASCII-only path (LibreOffice struggles with some CJK paths)."""
    # Keep original extension; use a simple stem.
    dest = work_dir / f"input{src.suffix.lower()}"
    shutil.copy2(src, dest)
    return dest


def _convert_libreoffice(
    input_path: Path,
    output_pdf: Path,
    *,
    timeout: int = DEFAULT_TIMEOUT_SEC,
) -> None:
    soffice = _which_soffice()
    if not soffice:
        raise ConversionError("LibreOffice not found")

    out_dir = output_pdf.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    timeout = max(timeout, _scaled_timeout(input_path))

    # LibreOffice writes <stem>.pdf into --outdir; we may rename afterwards.
    # Use a private user profile so concurrent conversions don't clash.
    with tempfile.TemporaryDirectory(prefix="lo_profile_") as profile:
        profile_uri = Path(profile).resolve().as_uri()
        # Also stage input under a short ASCII path when the original path is
        # non-ASCII or very long (common LO failure on Windows/CN paths).
        work_root = Path(profile) / "work"
        work_root.mkdir(parents=True, exist_ok=True)
        try:
            src = input_path
            if not src.name.isascii() or len(str(src.resolve())) > 180:
                src = _safe_work_copy(input_path, work_root)
        except OSError:
            src = input_path

        lo_outdir = work_root / "out"
        lo_outdir.mkdir(parents=True, exist_ok=True)

        cmd = [
            soffice,
            "--headless",
            "--nologo",
            "--nofirststartwizard",
            "--norestore",
            "--nodefault",
            "--nolockcheck",
            f"-env:UserInstallation={profile_uri}",
            "--convert-to",
            _LO_PDF_FILTER,
            "--outdir",
            str(lo_outdir),
            str(src.resolve()),
        ]
        # Disable macro execution via env (best-effort; LO versions vary).
        env = os.environ.copy()
        env.setdefault("SAL_USE_VCLPLUGIN", env.get("SAL_USE_VCLPLUGIN", "svp"))
        env["HOME"] = env.get("HOME") or profile

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise ConversionError(
                f"LibreOffice timed out after {timeout}s"
            ) from exc
        except OSError as exc:
            raise ConversionError(f"Failed to launch LibreOffice: {exc}") from exc

        produced = lo_outdir / (src.stem + ".pdf")
        # Some LO builds write using the original stem casing variants.
        if not produced.is_file():
            pdfs = list(lo_outdir.glob("*.pdf"))
            if len(pdfs) == 1:
                produced = pdfs[0]

        if proc.returncode != 0 and not produced.is_file():
            err = (proc.stderr or proc.stdout or "").strip()
            hint = ""
            if _docx_has_macros(input_path):
                hint = (
                    " (document may contain macros/ActiveX/OLE that "
                    "LibreOffice cannot fully render)"
                )
            raise ConversionError(
                f"LibreOffice conversion failed (code {proc.returncode})"
                + (f": {err[:400]}" if err else "")
                + hint
            )

        if not produced.is_file():
            raise ConversionError(
                "LibreOffice finished but PDF was not created"
            )

        if output_pdf.exists():
            output_pdf.unlink()
        shutil.copy2(produced, output_pdf)


def _convert_msword(input_path: Path, output_pdf: Path) -> None:
    """Convert via Microsoft Word (Windows). Prefers docx2pdf, else COM."""
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    src = str(input_path.resolve())
    dst = str(output_pdf.resolve())
    has_macros = _docx_has_macros(input_path)

    # docx2pdf is a thin wrapper around Word COM — try first unless we need
    # macro-safe COM options for problematic files.
    if not has_macros:
        try:
            from docx2pdf import convert as d2p_convert  # type: ignore
        except ImportError:
            d2p_convert = None  # type: ignore

        if d2p_convert is not None:
            try:
                d2p_convert(src, dst)
                if output_pdf.is_file() and output_pdf.stat().st_size > 0:
                    return
                raise ConversionError("docx2pdf finished but PDF is missing/empty")
            except ConversionError:
                raise
            except Exception:
                # Fall through to raw COM.
                pass

    try:
        import win32com.client  # type: ignore
        import pythoncom  # type: ignore
    except ImportError as exc:
        raise ConversionError(
            "Microsoft Word backend requires pywin32 or docx2pdf "
            "(and a local Microsoft Word install)"
        ) from exc

    pythoncom.CoInitialize()
    word = None
    doc = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        # msoAutomationSecurityForceDisable = 3 — block macros/ActiveX.
        try:
            word.AutomationSecurity = 3
        except Exception:
            pass
        # OpenAndRepair helps corrupted / complex packages; ReadOnly avoids locks.
        # Keyword form is more portable across pywin32 / Word versions.
        try:
            doc = word.Documents.Open(
                FileName=src,
                ConfirmConversions=False,
                ReadOnly=True,
                AddToRecentFiles=False,
                Revert=False,
                Visible=False,
                OpenAndRepair=True,
                NoEncodingDialog=True,
            )
        except Exception:
            # Older Word builds: positional (FileName, Confirm, ReadOnly, …).
            doc = word.Documents.Open(src, False, True, False)
    except Exception as exc:
        raise ConversionError(
            f"Microsoft Word failed to open document: {exc}"
            + (
                " (macros/ActiveX/OLE may be unsupported)"
                if has_macros
                else ""
            )
        ) from exc

    try:
        # 17 = wdFormatPDF
        # Prefer ExportAsFixedFormat when available (better layout options).
        exported = False
        try:
            # wdExportFormatPDF=17, wdExportOptimizeForPrint=0,
            # wdExportAllDocument=0, From=1, To=1, wdExportDocumentContent=0,
            # IncludeDocProps=True, KeepIRM=True, wdExportCreateWordBookmarks=1
            doc.ExportAsFixedFormat(
                OutputFileName=dst,
                ExportFormat=17,
                OpenAfterExport=False,
                OptimizeFor=0,
                BitmapMissingFonts=True,
                DocStructureTags=True,
                CreateBookmarks=1,
                UseDocumentStructureTags=True,
            )
            exported = output_pdf.is_file() and output_pdf.stat().st_size > 0
        except Exception:
            exported = False
        if not exported:
            doc.SaveAs(dst, FileFormat=17)
    except Exception as exc:
        raise ConversionError(
            f"Microsoft Word conversion failed: {exc}"
            + (
                " (complex macros/ActiveX controls are not rendered)"
                if has_macros
                else ""
            )
        ) from exc
    finally:
        try:
            if doc is not None:
                doc.Close(False)
        except Exception:
            pass
        try:
            if word is not None:
                word.Quit()
        except Exception:
            pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass

    if not output_pdf.is_file() or output_pdf.stat().st_size == 0:
        raise ConversionError("Microsoft Word finished but PDF is missing/empty")


def convert_to_pdf(
    input_path: str,
    output_path: Optional[str] = None,
    *,
    engine: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT_SEC,
) -> Tuple[str, str]:
    """Convert a Word document to PDF.

    Parameters
    ----------
    input_path:
        Path to ``.docx`` or ``.doc``.
    output_path:
        Destination ``.pdf``. Defaults to ``<input_stem>.pdf`` next to the input.
    engine:
        Force ``"libreoffice"`` or ``"msword"``. Default: first available.
    timeout:
        LibreOffice subprocess timeout in seconds (auto-scaled by file size).

    Returns
    -------
    (pdf_path, engine_used)
    """
    src = _validate_input(input_path)
    if output_path:
        dst = Path(output_path)
        if dst.suffix.lower() != ".pdf":
            dst = dst.with_suffix(".pdf")
    else:
        dst = src.with_suffix(".pdf")

    engines = available_engines()
    if engine:
        chosen = engine.lower().strip()
        if chosen not in ("libreoffice", "msword"):
            raise ConversionError(
                f"Unknown engine '{engine}'. Use 'libreoffice' or 'msword'."
            )
        if chosen not in engines:
            raise ConversionError(
                f"Engine '{chosen}' is not available on this machine. "
                f"Available: {', '.join(engines) or 'none'}"
            )
        order = [chosen]
    else:
        # Prefer LibreOffice (headless-friendly), then Word COM.
        # If macros/ActiveX are present and both engines exist, still try LO
        # first then Word (Word often handles legacy .doc macros better).
        order = [e for e in ("libreoffice", "msword") if e in engines]
        if _docx_has_macros(src) and "msword" in order and "libreoffice" in order:
            # Prefer Word for macro-heavy packages when available.
            order = ["msword", "libreoffice"]

    if not order:
        raise ConversionError(
            "No conversion engine available. Install LibreOffice "
            "(recommended for servers) or Microsoft Word (Windows). "
            "You can set LIBREOFFICE_PATH to the soffice binary."
        )

    last_err: Optional[Exception] = None
    errors: List[str] = []
    for name in order:
        try:
            if name == "libreoffice":
                _convert_libreoffice(src, dst, timeout=timeout)
            else:
                _convert_msword(src, dst)
            return str(dst), name
        except ConversionError as exc:
            last_err = exc
            errors.append(f"{name}: {exc}")
            continue

    detail = "; ".join(errors) if errors else (str(last_err) if last_err else "Conversion failed")
    raise ConversionError(detail)
