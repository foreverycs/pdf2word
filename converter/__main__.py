"""CLI: convert PDF to Word without the web UI.

Usage:
  python -m converter input.pdf
  python -m converter input.pdf -o out.docx
  python -m converter input.pdf --pages 1-3,5 --no-page-breaks
"""
from __future__ import annotations

import argparse
import os
import sys

from . import content_warnings, count_blocks, extract_document, write_document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m converter",
        description="Convert a text/table PDF to a high-fidelity Word document.",
    )
    parser.add_argument("input", help="Input PDF path")
    parser.add_argument(
        "-o", "--output",
        help="Output .docx path (default: <input>.docx)",
    )
    parser.add_argument(
        "--pages",
        dest="page_range",
        default=None,
        help="Page range, e.g. 1-3,5 (1-based; default: all)",
    )
    parser.add_argument(
        "--no-page-breaks",
        action="store_true",
        help="Do not insert Word page breaks between PDF pages",
    )
    args = parser.parse_args(argv)

    pdf_path = args.input
    if not os.path.isfile(pdf_path):
        print(f"error: file not found: {pdf_path}", file=sys.stderr)
        return 2
    if not pdf_path.lower().endswith(".pdf"):
        print("error: input must be a .pdf file", file=sys.stderr)
        return 2

    out = args.output
    if not out:
        out = os.path.splitext(pdf_path)[0] + ".docx"

    try:
        pages = extract_document(pdf_path, page_range=args.page_range)
        if not pages:
            print("error: no pages extracted", file=sys.stderr)
            return 1
        write_document(pages, out, page_breaks=not args.no_page_breaks)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"error: conversion failed: {exc}", file=sys.stderr)
        return 1

    stats = count_blocks(pages)
    warns = content_warnings(pages)
    print(
        f"wrote {out}  "
        f"(pages={stats['pages']}, tables={stats['tables']}, "
        f"text={stats['text_blocks']}, images={stats['images']}, "
        f"lines={stats.get('lines', 0)})"
    )
    if "image_only" in warns:
        print(
            "warning: pages look image-only / scanned; "
            "embedded as images (no OCR)",
            file=sys.stderr,
        )
    if "empty" in warns:
        print("warning: little or no content extracted", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
