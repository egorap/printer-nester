from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image


DEFAULT_IMAGE_DPI = 300.0
MIN_REASONABLE_DPI = 10.0


@dataclass(frozen=True, slots=True)
class ImageImportInfo:
    path: Path
    pixel_width: int
    pixel_height: int
    dpi_x: float
    dpi_y: float

    @property
    def width_in(self) -> float:
        return self.pixel_width / self.dpi_x

    @property
    def height_in(self) -> float:
        return self.pixel_height / self.dpi_y


def read_image_import_info(path: Path) -> ImageImportInfo:
    with Image.open(path) as image:
        dpi_x, dpi_y = _read_dpi(image)
        return ImageImportInfo(
            path=path,
            pixel_width=image.width,
            pixel_height=image.height,
            dpi_x=dpi_x,
            dpi_y=dpi_y,
        )


def _read_dpi(image: Image.Image) -> tuple[float, float]:
    dpi = image.info.get("dpi")
    if isinstance(dpi, tuple) and len(dpi) >= 2:
        return _clean_dpi(dpi[0], dpi[1])

    return DEFAULT_IMAGE_DPI, DEFAULT_IMAGE_DPI


def _clean_dpi(raw_x: object, raw_y: object) -> tuple[float, float]:
    try:
        dpi_x = float(raw_x)
        dpi_y = float(raw_y)
    except (TypeError, ValueError):
        return DEFAULT_IMAGE_DPI, DEFAULT_IMAGE_DPI

    if dpi_x < MIN_REASONABLE_DPI or dpi_y < MIN_REASONABLE_DPI:
        return DEFAULT_IMAGE_DPI, DEFAULT_IMAGE_DPI

    return _normalize_dpi(dpi_x), _normalize_dpi(dpi_y)


def _normalize_dpi(dpi: float) -> float:
    rounded = round(dpi)
    if abs(dpi - rounded) <= 0.75:
        return float(rounded)

    return dpi
