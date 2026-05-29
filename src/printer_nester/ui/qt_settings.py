from __future__ import annotations

from PySide6.QtGui import QImageReader


IMAGE_ALLOCATION_LIMIT_MB = 2048


def configure_qt_image_limits() -> None:
    if QImageReader.allocationLimit() < IMAGE_ALLOCATION_LIMIT_MB:
        QImageReader.setAllocationLimit(IMAGE_ALLOCATION_LIMIT_MB)
