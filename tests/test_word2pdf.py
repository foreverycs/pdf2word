"""Tests for Word → PDF conversion tool."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from word2pdf.converter import (
    ConversionError,
    _validate_input,
    available_engines,
    convert_to_pdf,
    engine_info,
)


def _make_docx(path: str) -> None:
    from docx import Document

    doc = Document()
    doc.add_paragraph("Hello Word2PDF")
    doc.save(path)


def test_validate_input_rejects_missing():
    with pytest.raises(ConversionError, match="not found"):
        _validate_input("no_such_file.docx")


def test_validate_input_rejects_bad_ext(tmp_path: Path):
    p = tmp_path / "x.txt"
    p.write_text("hi", encoding="utf-8")
    with pytest.raises(ConversionError, match="Unsupported"):
        _validate_input(str(p))


def test_validate_input_rejects_empty(tmp_path: Path):
    p = tmp_path / "empty.docx"
    p.write_bytes(b"")
    with pytest.raises(ConversionError, match="Empty"):
        _validate_input(str(p))


def test_validate_input_accepts_docx(tmp_path: Path):
    p = tmp_path / "ok.docx"
    _make_docx(str(p))
    got = _validate_input(str(p))
    assert got.suffix.lower() == ".docx"


def test_engine_info_shape():
    info = engine_info()
    assert "engines" in info
    assert "ready" in info
    assert "preferred" in info
    assert isinstance(info["engines"], list)
    assert info["ready"] is bool(info["engines"])
    assert "notes" in info


def test_docx_macro_detection_clean(tmp_path: Path):
    from word2pdf.converter import _docx_has_macros

    p = tmp_path / "clean.docx"
    _make_docx(str(p))
    assert _docx_has_macros(p) is False


def test_scaled_timeout_grows_with_size(tmp_path: Path):
    from word2pdf.converter import _scaled_timeout, DEFAULT_TIMEOUT_SEC

    small = tmp_path / "s.docx"
    _make_docx(str(small))
    t_small = _scaled_timeout(small, base=DEFAULT_TIMEOUT_SEC)
    assert t_small >= DEFAULT_TIMEOUT_SEC

    big = tmp_path / "b.docx"
    # ~3 MB payload so timeout scales above base.
    big.write_bytes(b"PK" + b"\0" * (3 * 1024 * 1024))
    t_big = _scaled_timeout(big, base=DEFAULT_TIMEOUT_SEC)
    assert t_big > t_small


def test_convert_falls_back_to_second_engine(tmp_path: Path):
    src = tmp_path / "sample.docx"
    _make_docx(str(src))
    out = tmp_path / "sample.pdf"

    def fail_lo(input_path, output_pdf, timeout=180):
        raise ConversionError("LibreOffice boom")

    def ok_ms(input_path, output_pdf):
        output_pdf.write_bytes(b"%PDF-1.4 via-word")

    with mock.patch(
        "word2pdf.converter.available_engines",
        return_value=["libreoffice", "msword"],
    ), mock.patch(
        "word2pdf.converter._convert_libreoffice",
        side_effect=fail_lo,
    ), mock.patch(
        "word2pdf.converter._convert_msword",
        side_effect=ok_ms,
    ), mock.patch(
        "word2pdf.converter._docx_has_macros",
        return_value=False,
    ):
        path, engine = convert_to_pdf(str(src), str(out))
    assert engine == "msword"
    assert Path(path).read_bytes().startswith(b"%PDF")


def test_convert_no_engine_raises(tmp_path: Path):
    p = tmp_path / "a.docx"
    _make_docx(str(p))
    with mock.patch("word2pdf.converter.available_engines", return_value=[]):
        with pytest.raises(ConversionError, match="No conversion engine"):
            convert_to_pdf(str(p))


def test_convert_libreoffice_mocked(tmp_path: Path):
    src = tmp_path / "sample.docx"
    _make_docx(str(src))
    out = tmp_path / "sample.pdf"

    def fake_lo(input_path, output_pdf, timeout=180):
        output_pdf.write_bytes(b"%PDF-1.4 fake")

    with mock.patch(
        "word2pdf.converter.available_engines",
        return_value=["libreoffice"],
    ), mock.patch(
        "word2pdf.converter._convert_libreoffice",
        side_effect=fake_lo,
    ):
        path, engine = convert_to_pdf(str(src), str(out))
    assert engine == "libreoffice"
    assert Path(path).is_file()
    assert Path(path).read_bytes().startswith(b"%PDF")


def test_convert_force_unavailable_engine(tmp_path: Path):
    src = tmp_path / "sample.docx"
    _make_docx(str(src))
    with mock.patch(
        "word2pdf.converter.available_engines",
        return_value=["libreoffice"],
    ):
        with pytest.raises(ConversionError, match="not available"):
            convert_to_pdf(str(src), engine="msword")


def test_cli_info_exits():
    from word2pdf.__main__ import main

    code = main(["--info"])
    # 0 if engine present, 1 if not — both valid
    assert code in (0, 1)


def test_api_status_and_page():
    from fastapi.testclient import TestClient
    from app import app

    client = TestClient(app)
    r = client.get("/tools/word2pdf/status")
    assert r.status_code == 200
    body = r.json()
    assert "ready" in body
    assert "engines" in body

    page = client.get("/tools/word2pdf")
    assert page.status_code == 200
    assert "Word" in page.text


def test_api_convert_without_engine_returns_503(tmp_path: Path):
    from fastapi.testclient import TestClient
    from app import app

    src = tmp_path / "a.docx"
    _make_docx(str(src))

    client = TestClient(app)
    with mock.patch("tools.word2pdf.engine_info", return_value={
        "engines": [],
        "preferred": None,
        "libreoffice_path": None,
        "ready": False,
    }):
        with open(src, "rb") as f:
            r = client.post(
                "/tools/word2pdf/convert",
                files={
                    "file": (
                        "a.docx",
                        f,
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
            )
    assert r.status_code == 503


def test_api_convert_mocked_engine(tmp_path: Path):
    from fastapi.testclient import TestClient
    from app import app

    src = tmp_path / "a.docx"
    _make_docx(str(src))

    def fake_convert(docx_path, pdf_path):
        Path(pdf_path).write_bytes(b"%PDF-1.4 mock")
        return {"engine": "libreoffice", "bytes": 12}

    client = TestClient(app)
    with mock.patch("tools.word2pdf.engine_info", return_value={
        "engines": ["libreoffice"],
        "preferred": "libreoffice",
        "libreoffice_path": "/usr/bin/soffice",
        "ready": True,
    }), mock.patch("tools.word2pdf._convert_one", side_effect=fake_convert):
        with open(src, "rb") as f:
            r = client.post(
                "/tools/word2pdf/convert",
                files={
                    "file": (
                        "a.docx",
                        f,
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
            )
    assert r.status_code == 200
    assert r.headers.get("content-type", "").startswith("application/pdf")
    assert r.headers.get("X-Engine") == "libreoffice"
    assert r.content.startswith(b"%PDF")


def test_home_lists_word2pdf():
    from fastapi.testclient import TestClient
    from app import app

    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "Word 转 PDF" in r.text
    assert "/tools/word2pdf" in r.text


@pytest.mark.skipif(
    "libreoffice" not in available_engines() and "msword" not in available_engines(),
    reason="no local conversion engine installed",
)
def test_real_convert_if_engine_present(tmp_path: Path):
    src = tmp_path / "real.docx"
    out = tmp_path / "real.pdf"
    _make_docx(str(src))
    path, engine = convert_to_pdf(str(src), str(out))
    assert engine in ("libreoffice", "msword")
    assert Path(path).is_file()
    assert Path(path).stat().st_size > 100
