from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings

from printer_nester.core.artboard import (
    DEFAULT_ARTBOARD,
    DEFAULT_SLOT_LAYOUT,
    ArtboardSettings,
    SlotLayoutSettings,
)
from printer_nester.core.pdf_export import ExportSettings


DEFAULT_TEMP_EXPORT_DIR = "C:/tmp/printer-nester-exports"


def load_default_artboard() -> ArtboardSettings:
    settings = QSettings()
    return ArtboardSettings(
        width_in=float(settings.value("artboard/width_in", DEFAULT_ARTBOARD.width_in)),
        height_in=float(
            settings.value("artboard/height_in", DEFAULT_ARTBOARD.height_in)
        ),
        margin_in=float(
            settings.value("artboard/margin_in", DEFAULT_ARTBOARD.margin_in)
        ),
    )


def save_default_artboard(artboard: ArtboardSettings) -> None:
    settings = QSettings()
    settings.setValue("artboard/width_in", artboard.width_in)
    settings.setValue("artboard/height_in", artboard.height_in)
    settings.setValue("artboard/margin_in", artboard.margin_in)
    settings.sync()


def load_default_slot_layout() -> SlotLayoutSettings:
    settings = QSettings()
    return SlotLayoutSettings(
        slot_width_in=float(
            settings.value("jig_slot/slot_width_in", DEFAULT_SLOT_LAYOUT.slot_width_in)
        ),
        slot_height_in=float(
            settings.value(
                "jig_slot/slot_height_in", DEFAULT_SLOT_LAYOUT.slot_height_in
            )
        ),
        slot_margin_in=0.0,
        horizontal_spacing_in=float(
            settings.value(
                "jig_slot/horizontal_spacing_in",
                DEFAULT_SLOT_LAYOUT.horizontal_spacing_in,
            )
        ),
        vertical_spacing_in=float(
            settings.value(
                "jig_slot/vertical_spacing_in", DEFAULT_SLOT_LAYOUT.vertical_spacing_in
            )
        ),
        columns=int(settings.value("jig_slot/columns", DEFAULT_SLOT_LAYOUT.columns)),
        rows=int(settings.value("jig_slot/rows", DEFAULT_SLOT_LAYOUT.rows)),
        outside_margin_in=float(
            settings.value(
                "jig_slot/outside_margin_in", DEFAULT_SLOT_LAYOUT.outside_margin_in
            )
        ),
    )


def save_default_slot_layout(slot_layout: SlotLayoutSettings) -> None:
    settings = QSettings()
    settings.setValue("jig_slot/slot_width_in", slot_layout.slot_width_in)
    settings.setValue("jig_slot/slot_height_in", slot_layout.slot_height_in)
    settings.setValue(
        "jig_slot/horizontal_spacing_in", slot_layout.horizontal_spacing_in
    )
    settings.setValue("jig_slot/vertical_spacing_in", slot_layout.vertical_spacing_in)
    settings.setValue("jig_slot/columns", slot_layout.columns)
    settings.setValue("jig_slot/rows", slot_layout.rows)
    settings.setValue("jig_slot/outside_margin_in", slot_layout.outside_margin_in)
    settings.sync()


def load_default_slot_mask_path() -> Path | None:
    value = str(QSettings().value("jig_slot/mask_path", "")).strip()
    return Path(value) if value else None


def save_default_slot_mask_path(path: Path | None) -> None:
    settings = QSettings()
    if path is None:
        settings.remove("jig_slot/mask_path")
    else:
        settings.setValue("jig_slot/mask_path", str(path))
    settings.sync()


def load_export_settings() -> ExportSettings:
    settings = QSettings()
    return ExportSettings(
        print_directory=Path(str(settings.value("export/print_directory", ""))),
        cut_directory=Path(str(settings.value("export/cut_directory", ""))),
        local_temp_directory=Path(
            str(settings.value("export/local_temp_directory", DEFAULT_TEMP_EXPORT_DIR))
        ),
    )


def save_export_settings(export_settings: ExportSettings) -> None:
    settings = QSettings()
    settings.setValue("export/print_directory", str(export_settings.print_directory))
    settings.setValue("export/cut_directory", str(export_settings.cut_directory))
    settings.setValue(
        "export/local_temp_directory", str(export_settings.local_temp_directory)
    )
    settings.sync()
