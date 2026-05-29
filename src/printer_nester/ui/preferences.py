from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings

from printer_nester.core.artboard import DEFAULT_ARTBOARD, ArtboardSettings
from printer_nester.core.pdf_export import ExportSettings


DEFAULT_TEMP_EXPORT_DIR = "C:/tmp/printer-nester-exports"


def load_default_artboard() -> ArtboardSettings:
    settings = QSettings()
    return ArtboardSettings(
        width_in=float(settings.value("artboard/width_in", DEFAULT_ARTBOARD.width_in)),
        height_in=float(settings.value("artboard/height_in", DEFAULT_ARTBOARD.height_in)),
        margin_in=float(settings.value("artboard/margin_in", DEFAULT_ARTBOARD.margin_in)),
    )


def save_default_artboard(artboard: ArtboardSettings) -> None:
    settings = QSettings()
    settings.setValue("artboard/width_in", artboard.width_in)
    settings.setValue("artboard/height_in", artboard.height_in)
    settings.setValue("artboard/margin_in", artboard.margin_in)
    settings.sync()


def load_export_settings() -> ExportSettings:
    settings = QSettings()
    return ExportSettings(
        print_directory=Path(str(settings.value("export/print_directory", ""))),
        cut_directory=Path(str(settings.value("export/cut_directory", ""))),
        local_temp_directory=Path(str(settings.value("export/local_temp_directory", DEFAULT_TEMP_EXPORT_DIR))),
    )


def save_export_settings(export_settings: ExportSettings) -> None:
    settings = QSettings()
    settings.setValue("export/print_directory", str(export_settings.print_directory))
    settings.setValue("export/cut_directory", str(export_settings.cut_directory))
    settings.setValue("export/local_temp_directory", str(export_settings.local_temp_directory))
    settings.sync()
