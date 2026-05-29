from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import fitz
from printer_nester.core.image_import import DEFAULT_IMAGE_DPI, read_image_import_info


PDF_PREVIEW_DPI = 144
RASTER_EXTENSIONS = {".jpg", ".jpeg", ".png"}
PDF_LIKE_EXTENSIONS = {".pdf", ".ai"}
SUPPORTED_ARTWORK_EXTENSIONS = RASTER_EXTENSIONS | PDF_LIKE_EXTENSIONS


class ArtworkKind(StrEnum):
    RASTER = "raster"
    PDF = "pdf"


@dataclass(frozen=True, slots=True)
class ArtworkImportInfo:
    path: Path
    kind: ArtworkKind
    width_points: float
    height_points: float
    preview_png: bytes | None
    preview_dpi: float
    page_index: int = 0

    @property
    def width_in(self) -> float:
        return self.width_points / 72

    @property
    def height_in(self) -> float:
        return self.height_points / 72


def read_artwork_import_info(path: Path) -> ArtworkImportInfo:
    extension = path.suffix.lower()
    if extension in RASTER_EXTENSIONS:
        return _read_raster_info(path)
    if extension in PDF_LIKE_EXTENSIONS:
        return _read_pdf_like_info(path)

    raise ValueError(f"Unsupported artwork format: {extension}")


def _read_raster_info(path: Path) -> ArtworkImportInfo:
    image_info = read_image_import_info(path)
    return ArtworkImportInfo(
        path=path,
        kind=ArtworkKind.RASTER,
        width_points=image_info.width_in * 72,
        height_points=image_info.height_in * 72,
        preview_png=None,
        preview_dpi=DEFAULT_IMAGE_DPI,
    )


def _read_pdf_like_info(path: Path) -> ArtworkImportInfo:
    with fitz.open(path) as document:
        if document.page_count == 0:
            raise ValueError(f"Document has no pages: {path}")

        page = document.load_page(0)
        page_rect = page.rect
        width_points = page_rect.width
        height_points = page_rect.height
        zoom = PDF_PREVIEW_DPI / 72
        pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=True)
        preview_png = pixmap.tobytes("png")

    return ArtworkImportInfo(
        path=path,
        kind=ArtworkKind.PDF,
        width_points=width_points,
        height_points=height_points,
        preview_png=preview_png,
        preview_dpi=PDF_PREVIEW_DPI,
    )
