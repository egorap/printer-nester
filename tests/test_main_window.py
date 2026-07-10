from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import fitz
import pytest
from PIL import Image
from PySide6.QtCore import QPointF, Qt
from printer_nester.core.artboard import SlotLayoutSettings
from PySide6.QtWidgets import QApplication

from printer_nester.core.nest_profile import NestProfileKind
from printer_nester.core.pdf_export import ExportKind
from printer_nester.ui.main_window import MainWindow


def _test_image(name: str) -> Path:
    output_dir = Path("test_outputs") / name
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / "art.png"
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(image_path, dpi=(10, 10))
    return image_path


def test_main_window_starts_with_sheet_profile_tab() -> None:
    QApplication.instance() or QApplication([])

    window = MainWindow()

    assert window._tab_widget.count() == 1
    assert window._tab_widget.tabText(0) == "Sheet 1"
    assert window._active_profile().kind == NestProfileKind.SHEET
    assert window.viewport is window._active_profile().viewport
    assert all(action.isEnabled() for action in window._sheet_only_actions)

    window.close()


def test_main_window_adds_jig_profile_tab_and_disables_sheet_nesting_actions() -> None:
    QApplication.instance() or QApplication([])
    window = MainWindow()

    jig_profile = window._add_profile(NestProfileKind.JIG, switch=True)

    assert window._tab_widget.count() == 2
    assert window._tab_widget.currentWidget() is jig_profile.widget
    assert window._tab_widget.tabText(window._tab_widget.currentIndex()) == "Jig 1"
    assert window._active_profile().kind == NestProfileKind.JIG
    assert window.viewport.property("profileKind") == NestProfileKind.JIG.value
    assert not any(action.isEnabled() for action in window._sheet_only_actions)

    sheet_profile = window._add_profile(NestProfileKind.SHEET, switch=True)

    assert window._tab_widget.currentWidget() is sheet_profile.widget
    assert window._tab_widget.tabText(window._tab_widget.currentIndex()) == "Sheet 2"
    assert window._active_profile().kind == NestProfileKind.SHEET
    assert all(action.isEnabled() for action in window._sheet_only_actions)

    window.close()


def test_main_window_rebinds_item_panel_when_switching_profile_tabs() -> None:
    QApplication.instance() or QApplication([])
    image_path = _test_image("profile_tabs")
    window = MainWindow()
    first_profile = window._active_profile()

    first_item = window.viewport.add_image_file(image_path, QPointF(0, 0))
    assert first_item is not None
    assert window.item_panel._list.count() == 1
    assert window.item_panel._list.item(0).data(Qt.ItemDataRole.UserRole) is first_item

    window._add_profile(NestProfileKind.JIG, switch=True)
    assert window.item_panel._list.count() == 0

    second_item = window.viewport.add_image_file(image_path, QPointF(100, 0))
    assert second_item is not None
    assert window.item_panel._list.count() == 1
    assert window.item_panel._list.item(0).data(Qt.ItemDataRole.UserRole) is second_item

    window._tab_widget.setCurrentIndex(0)

    assert window.viewport is first_profile.viewport
    assert window.item_panel._list.count() == 1
    assert window.item_panel._list.item(0).data(Qt.ItemDataRole.UserRole) is first_item

    window.close()


def test_main_window_jig_imports_default_rounding_off() -> None:
    QApplication.instance() or QApplication([])
    output_dir = Path("test_outputs") / "jig_rounding_default"
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / "art.png"
    Image.new("RGBA", (825, 390), (255, 0, 0, 255)).save(image_path, dpi=(300, 300))
    window = MainWindow()
    window._add_profile(NestProfileKind.JIG, switch=True)

    item = window.viewport.add_image_file(image_path, QPointF(0, 0))

    assert item is not None
    assert item.data(9) is False
    assert item.data(3) == pytest.approx(2.75)
    assert item.data(4) == pytest.approx(1.3)

    window.close()


def test_main_window_closes_active_profile_tab_and_keeps_registered_viewport() -> None:
    QApplication.instance() or QApplication([])
    window = MainWindow()
    sheet_profile = window._active_profile()

    window._add_profile(NestProfileKind.JIG, switch=True)
    window._close_profile_tab(window._tab_widget.currentIndex())

    assert window._tab_widget.count() == 1
    assert window._active_profile() is sheet_profile
    assert window.viewport is sheet_profile.viewport
    assert all(action.isEnabled() for action in window._sheet_only_actions)

    window.close()


def test_main_window_jig_profile_uses_slot_layout_and_updates_viewport() -> None:
    QApplication.instance() or QApplication([])
    window = MainWindow()
    slot_layout = SlotLayoutSettings(
        slot_width_in=2,
        slot_height_in=3,
        slot_margin_in=0.125,
        horizontal_spacing_in=0.5,
        vertical_spacing_in=0.75,
        columns=2,
        rows=2,
        outside_margin_in=0.5,
    )

    jig_profile = window._add_profile(
        NestProfileKind.JIG, switch=True, slot_layout=slot_layout
    )

    assert window._active_profile() is jig_profile
    assert window.viewport.slot_layout_settings() == slot_layout
    assert window.viewport.artboard_settings().width_in == pytest.approx(5.5)
    assert window.viewport.artboard_settings().height_in == pytest.approx(7.75)
    assert len(window.viewport.slot_rects()) == 4
    assert window.artboard_panel._sheet_artboard_section.isHidden()
    assert not window.artboard_panel._slot_layout_section.isHidden()

    window.artboard_panel._slot_columns_input.setValue(3)

    updated_layout = window.viewport.slot_layout_settings()
    assert updated_layout is not None
    assert updated_layout.columns == 3
    assert window.viewport.artboard_settings().width_in == pytest.approx(8.0)
    assert len(window.viewport.slot_rects()) == 6

    window.close()


def test_main_window_applies_default_jig_mask_path(monkeypatch, tmp_path) -> None:
    QApplication.instance() or QApplication([])
    mask_path = tmp_path / "default-jig-mask.png"
    Image.new("L", (100, 100), 0).save(mask_path)
    monkeypatch.setattr(
        "printer_nester.ui.main_window.load_default_slot_mask_path",
        lambda: mask_path,
    )
    window = MainWindow()

    jig_profile = window._add_profile(NestProfileKind.JIG, switch=True)

    assert jig_profile.viewport.slot_mask_path() == mask_path
    assert window.artboard_panel.jig_mask_path() == mask_path

    window.close()

def test_main_window_exports_jig_sheet_pdf(tmp_path) -> None:
    QApplication.instance() or QApplication([])
    output_dir = tmp_path / "main_window_jig_sheet"
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / "art.png"
    Image.new("RGBA", (100, 100), (255, 255, 255, 255)).save(
        image_path, dpi=(100, 100)
    )
    destination = output_dir / "jig_sheet_001.pdf"
    window = MainWindow()
    window.artboard_panel.set_jig_mask_path(None)
    slot_layout = SlotLayoutSettings(
        slot_width_in=1,
        slot_height_in=1,
        slot_margin_in=0,
        horizontal_spacing_in=0,
        vertical_spacing_in=0,
        columns=1,
        rows=1,
        outside_margin_in=0,
    )
    window._add_profile(NestProfileKind.JIG, switch=True, slot_layout=slot_layout)
    assert window.viewport.add_image_file(image_path, QPointF(0, 0)) is not None
    window.artboard_panel._print_export_input.setText(str(output_dir))
    window.artboard_panel._temp_export_input.setText(str(output_dir / "temp"))

    window._export_sheet(0, "jig")

    assert destination.exists()
    assert (0, ExportKind.PRINT) in window._active_profile().exported_sheets
    with fitz.open(destination) as document:
        assert document.page_count == 1
        content = b"\n".join(
            document.xref_stream(xref) for xref in document[0].get_contents()
        ).decode("latin1")
        all_objects = "\n".join(
            document.xref_object(xref, compressed=False)
            for xref in range(1, document.xref_length())
        )
        assert "/SpotImage" in content
        assert "/GS0 gs" in content
        assert "RDG_WHITE" in all_objects

    window.close()
def test_main_window_exports_jig_mockup_pdf(monkeypatch) -> None:
    QApplication.instance() or QApplication([])
    output_dir = Path("test_outputs") / "main_window_jig_mockup"
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / "mockup.pdf"
    if destination.exists():
        destination.unlink()
    window = MainWindow()
    slot_layout = SlotLayoutSettings(
        slot_width_in=2,
        slot_height_in=3,
        slot_margin_in=0,
        horizontal_spacing_in=0.5,
        vertical_spacing_in=0.75,
        columns=2,
        rows=2,
        outside_margin_in=0.5,
    )
    window._add_profile(NestProfileKind.JIG, switch=True, slot_layout=slot_layout)
    window.artboard_panel._print_export_input.setText(str(output_dir))
    window.artboard_panel._temp_export_input.setText(str(output_dir / "temp"))
    monkeypatch.setattr(
        "printer_nester.ui.main_window.QFileDialog.getSaveFileName",
        lambda *args, **kwargs: (str(destination), "PDF Files (*.pdf)"),
    )

    window._export_jig_mockup()

    assert destination.exists()
    with fitz.open(destination) as document:
        assert document.page_count == 1
        assert document[0].rect.width == pytest.approx(slot_layout.total_width_in * 72)
        assert document[0].get_images() == []

    window.close()