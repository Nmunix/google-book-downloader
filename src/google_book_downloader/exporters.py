"""Export downloaded page images to a single PDF.

The original README told users to upload their scans to a random online
converter.  Pillow can assemble the PDF locally, and its PDF plugin accepts a
*lazy generator* for the extra pages, so a 900-page book never has all of its
bitmaps in memory at once.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from pathlib import Path

from PIL import Image

from google_book_downloader.errors import GoogleBooksError

logger = logging.getLogger(__name__)

#: Above this many pixels Pillow's PDF writer gets slow and memory-hungry;
#: scans are typically ~1600 px on the long edge, so only huge books hit it.
_MAX_EDGE = 3500


class ExportError(GoogleBooksError):
    """The PDF could not be produced."""


def _iter_pages(paths: list[Path], dpi: int) -> Iterator[Image.Image]:
    for path in paths:
        with Image.open(path) as image:
            image.load()
            page = image.convert("RGB")
            if max(page.size) > _MAX_EDGE:
                scale = _MAX_EDGE / max(page.size)
                new_size = (round(page.width * scale), round(page.height * scale))
                page = page.resize(new_size, Image.Resampling.LANCZOS)
            page.info["dpi"] = (dpi, dpi)
            yield page


def images_to_pdf(images: Iterable[Path], output: Path, *, dpi: int = 150) -> Path:
    """Combine ``images`` into a single PDF at ``output``.

    Args:
        images: Image files, in page order.
        output: Destination ``.pdf`` path.
        dpi: Resolution tag embedded in the PDF.

    Returns:
        The written path.

    Raises:
        ExportError: If there is nothing to write or Pillow fails.
    """
    ordered = [Path(path) for path in images if Path(path).exists()]
    if not ordered:
        raise ExportError("No page images were found, so no PDF could be created.")
    ordered.sort(key=lambda path: path.name)
    output.parent.mkdir(parents=True, exist_ok=True)

    pages = _iter_pages(ordered, dpi)
    try:
        first = next(pages)
    except StopIteration as exc:  # pragma: no cover - guarded by the check above
        raise ExportError("No readable page images were found.") from exc

    try:
        first.save(
            output,
            "PDF",
            resolution=float(dpi),
            save_all=True,
            append_images=pages,
            title=output.stem,
        )
    except OSError as exc:
        raise ExportError(f"Could not write {output}: {exc}") from exc
    finally:
        first.close()
    logger.debug("Wrote %s (%s pages)", output, len(ordered))
    return output
