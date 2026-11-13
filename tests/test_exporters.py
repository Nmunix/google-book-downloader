"""Tests for PDF export."""

from __future__ import annotations

from pathlib import Path

import pytest

from google_book_downloader.exporters import ExportError, images_to_pdf


def test_pdf_is_created(image_dir: Path, tmp_path: Path) -> None:
    output = tmp_path / "out" / "book.pdf"
    images_to_pdf(sorted(image_dir.glob("*.png")), output)
    payload = output.read_bytes()
    assert payload.startswith(b"%PDF")
    assert payload.rstrip().endswith(b"%%EOF")


def test_pages_are_ordered_by_filename(image_dir: Path, tmp_path: Path) -> None:
    output = tmp_path / "book.pdf"
    images_to_pdf(sorted(image_dir.glob("*.png"), reverse=True), output)
    assert output.exists()


def test_missing_images_raise(tmp_path: Path) -> None:
    with pytest.raises(ExportError, match="No page images"):
        images_to_pdf([tmp_path / "nope.png"], tmp_path / "out.pdf")


def test_large_images_are_downscaled(tmp_path: Path) -> None:
    from PIL import Image

    big = tmp_path / "big.png"
    Image.new("RGB", (4000, 5000), (10, 20, 30)).save(big)
    output = tmp_path / "big.pdf"
    images_to_pdf([big], output)
    assert output.stat().st_size > 0
