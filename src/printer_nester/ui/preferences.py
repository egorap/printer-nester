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
DEFAULT_EXPORT_COUNTER = 1


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
    legacy_print_directory = str(settings.value("export/print_directory", ""))
    return ExportSettings(
        sheet_print_directory=Path(
            str(
                settings.value(
                    "export/sheet_print_directory", legacy_print_directory
                )
            )
        ),
        jig_print_directory=Path(
            str(
                settings.value(
                    "export/jig_print_directory", legacy_print_directory
                )
            )
        ),
        cut_directory=Path(str(settings.value("export/cut_directory", ""))),
        local_temp_directory=Path(
            str(settings.value("export/local_temp_directory", DEFAULT_TEMP_EXPORT_DIR))
        ),
    )


def save_export_settings(export_settings: ExportSettings) -> None:
    settings = QSettings()
    settings.setValue(
        "export/sheet_print_directory", str(export_settings.sheet_print_directory)
    )
    settings.setValue(
        "export/jig_print_directory", str(export_settings.jig_print_directory)
    )
    settings.setValue("export/cut_directory", str(export_settings.cut_directory))
    settings.setValue(
        "export/local_temp_directory", str(export_settings.local_temp_directory)
    )
    settings.sync()


def load_export_counter() -> int:
    try:
        value = int(QSettings().value("export/next_counter", DEFAULT_EXPORT_COUNTER))
    except (TypeError, ValueError):
        return DEFAULT_EXPORT_COUNTER
    return max(DEFAULT_EXPORT_COUNTER, value)


def save_export_counter(value: int) -> None:
    settings = QSettings()
    settings.setValue("export/next_counter", max(DEFAULT_EXPORT_COUNTER, int(value)))
    settings.sync()


def load_hot_folder_root() -> Path:
    return Path(str(QSettings().value("hot_folder/root", "")))


def save_hot_folder_root(root: Path) -> None:
    settings = QSettings()
    settings.setValue("hot_folder/root", str(root))
    settings.sync()


def load_label_printer_id() -> str:
    return str(QSettings().value("label_printer/id", "")).strip()


def save_label_printer_id(printer_id: str) -> None:
    settings = QSettings()
    value = str(printer_id).strip()
    if value:
        settings.setValue("label_printer/id", value)
    else:
        settings.remove("label_printer/id")
    settings.sync()
