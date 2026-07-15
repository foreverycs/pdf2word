"""Shared conversion pipeline helpers."""

from __future__ import annotations

from fastapi import HTTPException

from core.errors import ConversionError, PDFParseError, ValidationError
from tools.pipeline import TempWorkspace, map_conversion_error
from word2pdf import ConversionError as WordConversionError


def test_map_toolkit_error_preserves_status():
    exc = map_conversion_error(PDFParseError("bad pdf"))
    assert isinstance(exc, HTTPException)
    assert exc.status_code == 422
    assert exc.detail == "bad pdf"


def test_map_value_error_is_400():
    exc = map_conversion_error(ValueError("range"))
    assert exc.status_code == 400
    assert exc.detail == "range"


def test_map_word_conversion_error_is_400():
    exc = map_conversion_error(WordConversionError("no engine"))
    assert exc.status_code == 400
    assert "no engine" in exc.detail


def test_map_generic_is_500():
    exc = map_conversion_error(RuntimeError("boom"), label="Merge failed")
    assert exc.status_code == 500
    assert exc.detail == "Merge failed: boom"


def test_map_name_prefix():
    exc = map_conversion_error(ValueError("x"), name_prefix="a.pdf")
    assert exc.detail == "a.pdf: x"


def test_map_http_exception_passthrough():
    original = HTTPException(status_code=413, detail="too big")
    assert map_conversion_error(original) is original


def test_map_core_conversion_error():
    exc = map_conversion_error(ConversionError("empty"))
    assert exc.status_code == 500
    assert exc.detail == "empty"


def test_map_validation_error():
    exc = map_conversion_error(ValidationError("dims"))
    assert exc.status_code == 400
    assert exc.detail == "dims"


def test_temp_workspace_create_and_cleanup(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tools.pipeline.tempfile.mkdtemp",
        lambda prefix="": str(tmp_path / "ws"),
    )
    (tmp_path / "ws").mkdir()
    ws = TempWorkspace("x_")
    path = ws.create()
    assert path == str(tmp_path / "ws")
    f = ws.join("a.txt")
    with open(f, "w", encoding="utf-8") as fh:
        fh.write("ok")
    assert (tmp_path / "ws" / "a.txt").is_file()
    ws.cleanup_now()
    assert ws.path is None
