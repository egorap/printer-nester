from __future__ import annotations

import json
import os
import time
from io import BytesIO
from pathlib import Path
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import fitz
import pytest
from PIL import Image
from PySide6.QtCore import QPointF, Qt
from printer_nester.core.artboard import SlotLayoutSettings
from PySide6.QtWidgets import QApplication

from printer_nester.core.label_print import PrintNodePrinter

from printer_nester.core.nest_profile import NestProfileKind
from printer_nester.core.pdf_export import ExportKind
from printer_nester.ui.main_window import MainWindow
from printer_nester.ui.viewport import HOT_FOLDER_ORDER_DATA_KEY


def _test_image(name: str) -> Path:
    output_dir = Path("test_outputs") / name
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / "art.png"
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(image_path, dpi=(10, 10))
    return image_path


def _png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(
        buffer, format="PNG", dpi=(300, 300)
    )
    return buffer.getvalue()


def _write_hot_order_zip(
    hot_root: Path, filename: str, order_number: str, quantity: int
) -> None:
    incoming = hot_root / "incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    artwork_name = f"artwork/{order_number}.png"
    manifest = {
        "order_number": order_number,
        "items": [{"file": artwork_name, "quantity": quantity}],
    }
    with zipfile.ZipFile(incoming / filename, "w") as archive:
        archive.writestr("order.json", json.dumps(manifest))
        archive.writestr(artwork_name, _png_bytes())

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


def test_main_window_rebuilds_sheet_rows_once_after_batch_import(
    monkeypatch, tmp_path
) -> None:
    QApplication.instance() or QApplication([])
    image_path = tmp_path / "batch.png"
    Image.new("RGBA", (20, 20), (255, 0, 0, 255)).save(
        image_path, dpi=(10, 10)
    )
    window = MainWindow()
    refreshes = []
    original = window.artboard_panel.set_sheet_rows

    def record_refresh(rows):
        refreshes.append(rows)
        original(rows)

    monkeypatch.setattr(window.artboard_panel, "set_sheet_rows", record_refresh)

    class Progress:
        def setValue(self, _value):
            pass

        def setLabelText(self, _text):
            pass

        def wasCanceled(self):
            return False

    added = window.viewport._import_artwork_batch(
        [image_path] * 5, QPointF(0, 0), Progress()
    )

    assert added == 5
    assert len(refreshes) == 1
    assert window.item_panel._list.count() == 5

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



def test_main_window_processes_hot_folder_orders_as_groups(tmp_path) -> None:
    QApplication.instance() or QApplication([])
    hot_root = tmp_path / "hot-folder"
    _write_hot_order_zip(hot_root, "001-order-a.zip", "A100", 1)
    _write_hot_order_zip(hot_root, "002-order-b.zip", "B200", 2)
    window = MainWindow()
    slot_layout = SlotLayoutSettings(
        slot_width_in=1,
        slot_height_in=1,
        slot_margin_in=0,
        horizontal_spacing_in=0,
        vertical_spacing_in=0,
        columns=2,
        rows=1,
        outside_margin_in=0,
    )
    window._add_profile(NestProfileKind.JIG, switch=True, slot_layout=slot_layout)
    window.artboard_panel.set_hot_folder_root(hot_root)

    window._process_hot_folder()

    assert len(window.viewport.image_items()) == 3
    assert window._sheet_order_numbers(0) == ["A100"]
    assert window._sheet_order_numbers(1) == ["B200"]
    assert window.viewport.sheet_item_summary()[0][1] == 1
    assert window.viewport.sheet_item_summary()[1][1] == 2
    assert len(list((hot_root / "done").glob("*.zip"))) == 2
    assert not list((hot_root / "processing").glob("*.zip"))
    assert all(
        (hot_root / "done") in Path(str(item.data(0))).parents
        for item in window.viewport.image_items()
    )

    window.close()


def test_hot_folder_label_order_matches_print_import_order(
    monkeypatch, tmp_path
) -> None:
    QApplication.instance() or QApplication([])
    hot_root = tmp_path / "ordered-hot-folder"
    _write_hot_order_zip(hot_root, "001-order-b.zip", "B200", 1)
    _write_hot_order_zip(hot_root, "002-order-a.zip", "A100", 1)
    window = MainWindow()
    window._add_profile(
        NestProfileKind.JIG,
        switch=True,
        slot_layout=SlotLayoutSettings(
            slot_width_in=1,
            slot_height_in=1,
            slot_margin_in=0,
            horizontal_spacing_in=0,
            vertical_spacing_in=0,
            columns=2,
            rows=1,
            outside_margin_in=0,
        ),
    )
    window.artboard_panel.set_hot_folder_root(hot_root)
    window.artboard_panel.set_label_printer_id("123")
    submitted = []
    monkeypatch.setattr(
        "printer_nester.ui.main_window.print_order_labels",
        lambda printer_id, orders: submitted.append((printer_id, orders)) or 1,
    )
    monkeypatch.setattr(
        "printer_nester.ui.main_window.save_label_printer_id",
        lambda _printer_id: None,
    )

    window._process_hot_folder()
    window._print_jig_sheet_labels(0)

    assert window._sheet_order_numbers(0) == ["B200", "A100"]
    assert submitted == [("123", ["B200", "A100"])]

    window.close()


def test_main_window_poll_hot_folder_imports_ready_orders(tmp_path) -> None:
    QApplication.instance() or QApplication([])
    hot_root = tmp_path / "poll-hot-folder"
    _write_hot_order_zip(hot_root, "001-order-a.zip", "A100", 1)
    zip_path = hot_root / "incoming" / "001-order-a.zip"
    window = MainWindow()
    slot_layout = SlotLayoutSettings(
        slot_width_in=1,
        slot_height_in=1,
        slot_margin_in=0,
        horizontal_spacing_in=0,
        vertical_spacing_in=0,
        columns=2,
        rows=1,
        outside_margin_in=0,
    )
    window._add_profile(NestProfileKind.JIG, switch=True, slot_layout=slot_layout)
    window.artboard_panel.set_hot_folder_root(hot_root)

    window._poll_hot_folder()

    assert len(window.viewport.image_items()) == 0
    assert zip_path.exists()

    old_time = time.time() - 5
    os.utime(zip_path, (old_time, old_time))

    window._poll_hot_folder()

    assert len(window.viewport.image_items()) == 1
    assert window._sheet_order_numbers(0) == ["A100"]
    assert not zip_path.exists()
    assert len(list((hot_root / "done").glob("*.zip"))) == 1

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

def test_main_window_exports_jig_sheet_pdf(monkeypatch, tmp_path) -> None:
    QApplication.instance() or QApplication([])
    output_dir = tmp_path / "main_window_jig_sheet"
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / "art.png"
    Image.new("RGBA", (100, 100), (255, 255, 255, 255)).save(
        image_path, dpi=(100, 100)
    )
    monkeypatch.setattr(
        "printer_nester.ui.main_window.load_export_counter", lambda: 42
    )
    monkeypatch.setattr(
        "printer_nester.ui.main_window.save_export_counter", lambda _value: None
    )
    destination = output_dir / "jig_sheet_042.pdf"
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
    item = window.viewport.add_image_file(image_path, QPointF(0, 0))
    assert item is not None
    item.setData(HOT_FOLDER_ORDER_DATA_KEY, "ORDER-1")
    window.artboard_panel._jig_print_export_input.setText(str(output_dir))
    window.artboard_panel._temp_export_input.setText(str(output_dir / "temp"))

    window._export_sheet(0, "jig")

    assert destination.exists()
    assert (0, ExportKind.PRINT) in window._active_profile().exported_sheets
    assert window.viewport.is_jig_sheet_locked(0)
    assert window._export_counter_input.value() == 43
    assert not list(output_dir.glob("*_orders.json"))
    next_item = window.viewport.add_image_file(image_path, QPointF(0, 0))
    assert next_item is not None
    assert window.viewport.item_sheet_index(next_item) == 1
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



def test_export_all_writes_jig_pdf_and_prints_labels(monkeypatch, tmp_path) -> None:
    QApplication.instance() or QApplication([])
    output_dir = tmp_path / "export_all_jig"
    output_dir.mkdir()
    image_path = output_dir / "art.png"
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(
        image_path, dpi=(100, 100)
    )
    monkeypatch.setattr(
        "printer_nester.ui.main_window.load_export_counter", lambda: 90
    )
    monkeypatch.setattr(
        "printer_nester.ui.main_window.save_export_counter", lambda _value: None
    )
    calls = []
    monkeypatch.setattr(
        "printer_nester.ui.main_window.print_order_labels",
        lambda printer_id, orders: calls.append((printer_id, orders)) or 987,
    )
    monkeypatch.setattr(
        "printer_nester.ui.main_window.save_label_printer_id",
        lambda _printer_id: None,
    )
    window = MainWindow()
    window.artboard_panel.set_jig_mask_path(None)
    window._add_profile(
        NestProfileKind.JIG,
        switch=True,
        slot_layout=SlotLayoutSettings(
            slot_width_in=1,
            slot_height_in=1,
            slot_margin_in=0,
            horizontal_spacing_in=0,
            vertical_spacing_in=0,
            columns=1,
            rows=1,
            outside_margin_in=0,
        ),
    )
    item = window.viewport.add_image_file(image_path, QPointF(0, 0))
    assert item is not None
    item.setData(HOT_FOLDER_ORDER_DATA_KEY, "ORDER-1")
    window.artboard_panel._jig_print_export_input.setText(str(output_dir))
    window.artboard_panel._temp_export_input.setText(str(output_dir / "temp"))
    window.artboard_panel.set_label_printer_id("123")

    window._export_all_pending()

    profile = window._active_profile()
    assert (output_dir / "jig_sheet_090.pdf").exists()
    assert not list(output_dir.glob("*.json"))
    assert calls == [("123", ["ORDER-1"])]
    assert (0, ExportKind.PRINT) in profile.exported_sheets
    assert window.viewport.is_jig_sheet_locked(0)
    assert 0 in profile.labels_printed_sheets
    assert "1 label job(s) submitted" in window.statusBar().currentMessage()

    window.close()


def test_export_all_retries_failed_labels_without_duplicate_pdf(
    monkeypatch, tmp_path
) -> None:
    QApplication.instance() or QApplication([])
    output_dir = tmp_path / "retry_jig_labels"
    output_dir.mkdir()
    image_path = output_dir / "art.png"
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(
        image_path, dpi=(100, 100)
    )
    monkeypatch.setattr(
        "printer_nester.ui.main_window.load_export_counter", lambda: 91
    )
    monkeypatch.setattr(
        "printer_nester.ui.main_window.save_export_counter", lambda _value: None
    )
    calls = []

    def print_labels(printer_id, orders):
        calls.append((printer_id, orders))
        if len(calls) == 1:
            raise RuntimeError("printer offline")
        return 988

    monkeypatch.setattr(
        "printer_nester.ui.main_window.print_order_labels", print_labels
    )
    monkeypatch.setattr(
        "printer_nester.ui.main_window.save_label_printer_id",
        lambda _printer_id: None,
    )
    window = MainWindow()
    window.artboard_panel.set_jig_mask_path(None)
    window._add_profile(
        NestProfileKind.JIG,
        switch=True,
        slot_layout=SlotLayoutSettings(
            slot_width_in=1,
            slot_height_in=1,
            slot_margin_in=0,
            horizontal_spacing_in=0,
            vertical_spacing_in=0,
            columns=1,
            rows=1,
            outside_margin_in=0,
        ),
    )
    item = window.viewport.add_image_file(image_path, QPointF(0, 0))
    assert item is not None
    item.setData(HOT_FOLDER_ORDER_DATA_KEY, "ORDER-1")
    window.artboard_panel._jig_print_export_input.setText(str(output_dir))
    window.artboard_panel._temp_export_input.setText(str(output_dir / "temp"))
    window.artboard_panel.set_label_printer_id("123")

    window._export_all_pending()

    profile = window._active_profile()
    assert (0, ExportKind.PRINT) in profile.exported_sheets
    assert 0 not in profile.labels_printed_sheets
    assert len(list(output_dir.glob("*.pdf"))) == 1
    assert "label print failed" in window.statusBar().currentMessage()

    window._export_all_pending()

    assert len(list(output_dir.glob("*.pdf"))) == 1
    assert len(calls) == 2
    assert 0 in profile.labels_printed_sheets

    window.close()
def test_sheet_print_and_cut_share_counter_number(monkeypatch, tmp_path) -> None:
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        "printer_nester.ui.main_window.load_export_counter", lambda: 75
    )
    saved_counters = []
    monkeypatch.setattr(
        "printer_nester.ui.main_window.save_export_counter",
        saved_counters.append,
    )
    image_path = tmp_path / "paired-art.png"
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(
        image_path, dpi=(100, 100)
    )
    print_dir = tmp_path / "print"
    cut_dir = tmp_path / "cut"
    temp_dir = tmp_path / "temp"

    window = MainWindow()
    item = window.viewport.add_image_file(image_path, QPointF(0, 0))
    assert item is not None
    window.artboard_panel._sheet_print_export_input.setText(str(print_dir))
    window.artboard_panel._cut_export_input.setText(str(cut_dir))
    window.artboard_panel._temp_export_input.setText(str(temp_dir))

    window._export_sheet(0, "print")
    window._export_sheet(0, "cut")

    assert (print_dir / "sheet_075_print.pdf").exists()
    assert (cut_dir / "sheet_075_cut.pdf").exists()
    assert window._export_counter_input.value() == 76
    assert saved_counters == [76]

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
    window.artboard_panel._jig_print_export_input.setText(str(output_dir))
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

def test_main_window_refreshes_printnode_printer_choices(monkeypatch) -> None:
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        "printer_nester.ui.artboard_panel.load_label_printer_id", lambda: ""
    )
    window = MainWindow()
    window._add_profile(NestProfileKind.JIG, switch=True)
    monkeypatch.setattr(
        "printer_nester.ui.main_window.list_printnode_printers",
        lambda: [
            PrintNodePrinter(
                "123",
                "Shipping Labels",
                computer_name="Pack Station",
                state="online",
            )
        ],
    )

    window._refresh_label_printers()

    assert window.artboard_panel.label_printer_id() == "123"
    assert "Loaded 1 PrintNode printer" in window.statusBar().currentMessage()

    window.close()


def test_main_window_prints_jig_sheet_order_labels(monkeypatch, tmp_path) -> None:
    QApplication.instance() or QApplication([])
    image_path = tmp_path / "art.png"
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(
        image_path, dpi=(100, 100)
    )
    window = MainWindow()
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
    item = window.viewport.add_image_file(image_path, QPointF(0, 0))
    assert item is not None
    item.setData(HOT_FOLDER_ORDER_DATA_KEY, "ORDER-1")
    window.artboard_panel.set_label_printer_id("123")
    calls = []
    saved = []
    monkeypatch.setattr(
        "printer_nester.ui.main_window.print_order_labels",
        lambda printer_id, orders: calls.append((printer_id, orders)) or 987,
    )
    monkeypatch.setattr(
        "printer_nester.ui.main_window.save_label_printer_id",
        lambda printer_id: saved.append(printer_id),
    )

    window._print_jig_sheet_labels(0)

    assert calls == [("123", ["ORDER-1"])]
    assert saved == ["123"]
    assert "Submitted labels for sheet 1: job 987" in window.statusBar().currentMessage()

    window.close()


def test_main_window_copies_jig_sheet_order_numbers_in_print_order(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    image_path = tmp_path / "art.png"
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(
        image_path, dpi=(100, 100)
    )
    window = MainWindow()
    window._add_profile(
        NestProfileKind.JIG,
        switch=True,
        slot_layout=SlotLayoutSettings(
            slot_width_in=1,
            slot_height_in=1,
            slot_margin_in=0,
            horizontal_spacing_in=0,
            vertical_spacing_in=0,
            columns=2,
            rows=1,
            outside_margin_in=0,
        ),
    )
    for order_number in ("ORDER-20", "ORDER-10"):
        item = window.viewport.add_image_file(image_path, QPointF(0, 0))
        assert item is not None
        item.setData(HOT_FOLDER_ORDER_DATA_KEY, order_number)

    window._copy_jig_sheet_order_numbers(0)

    assert app.clipboard().text() == "ORDER-20; ORDER-10"
    assert "Copied 2 order number(s) from sheet 1" in window.statusBar().currentMessage()

    window.close()
