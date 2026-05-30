from __future__ import annotations

import os
import json
from pathlib import Path
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QImage, QImageReader, QKeyEvent, QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QAbstractItemView, QApplication, QCheckBox, QGraphicsItem, QLabel, QPushButton

import fitz
from PIL import Image
import pytest

from printer_nester.core.artboard import ArtboardSettings
from printer_nester.core.artwork_import import ArtworkKind, read_artwork_import_info
from printer_nester.core.auto_nest import classify_preset_size
from printer_nester.core.cut_paths import CutRect, cut_segments_for_rects
from printer_nester.core.grid_nest import GridItem, group_grid_placements
from printer_nester.core.image_import import DEFAULT_IMAGE_DPI, read_image_import_info
from printer_nester.core.markers import MARKER_DIAMETER_MM, MAX_MARKER_GAP_IN, sheet_marker_layout
from printer_nester.core.pdf_export import EXPORT_MARKER_PADDING_IN, ExportKind, ExportSettings, export_sheet_pdf
from printer_nester.core.space_nest import SpaceItem, SpaceRect, fill_space_placements
from printer_nester.ui.artboard_panel import ArtboardPanel, SheetPanelRow
from printer_nester.ui.item_panel import ROW_HEIGHT, ItemPanel, ItemRowWidget
from printer_nester.ui.qt_settings import IMAGE_ALLOCATION_LIMIT_MB, configure_qt_image_limits
from printer_nester.ui.ruler import RulerWidget
from printer_nester.ui.viewport import (
    AUTO_NEST_SHEET_COLUMNS,
    AUTO_NEST_SHEET_GAP_IN,
    ITEM_MARKER_GAP_IN,
    MIN_VIEWPORT_ZOOM,
    PrintViewport,
    _rects_overlap_with_area,
)


def test_wheel_event_zooms_viewport() -> None:
    app = QApplication.instance() or QApplication([])
    viewport = PrintViewport()
    viewport.resize(800, 600)
    viewport.show()
    app.processEvents()

    before = viewport.transform().m11()
    event = QWheelEvent(
        QPointF(400, 300),
        QPointF(400, 300),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )

    viewport.wheelEvent(event)

    assert viewport.transform().m11() > before


def test_viewport_can_zoom_out_to_one_percent() -> None:
    QApplication.instance() or QApplication([])
    viewport = PrintViewport()

    for _ in range(40):
        viewport.zoom_out()

    assert viewport.transform().m11() == pytest.approx(MIN_VIEWPORT_ZOOM)
    assert viewport.transform().m11() < 0.08


def test_right_click_drag_pans_viewport() -> None:
    app = QApplication.instance() or QApplication([])
    viewport = PrintViewport()
    viewport.resize(800, 600)
    viewport.show()
    app.processEvents()

    press = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(400, 300),
        Qt.MouseButton.RightButton,
        Qt.MouseButton.RightButton,
        Qt.KeyboardModifier.NoModifier,
    )
    move = QMouseEvent(
        QMouseEvent.Type.MouseMove,
        QPointF(360, 300),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.RightButton,
        Qt.KeyboardModifier.NoModifier,
    )
    release = QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease,
        QPointF(360, 300),
        Qt.MouseButton.RightButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )

    before = viewport.horizontalScrollBar().value()
    viewport.mousePressEvent(press)
    viewport.mouseMoveEvent(move)
    viewport.mouseReleaseEvent(release)

    assert press.isAccepted()
    assert move.isAccepted()
    assert release.isAccepted()
    assert viewport.horizontalScrollBar().value() != before


def test_add_image_file_creates_movable_item(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    image_path = tmp_path / "art.png"
    Image.new("RGBA", (20, 10), (255, 0, 0, 255)).save(image_path)

    viewport = PrintViewport()
    item = viewport.add_image_file(image_path, QPointF(12, 34))
    app.processEvents()

    assert item is not None
    assert item.pos() == QPointF(12, 34)
    assert item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable
    assert item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
    assert item.cacheMode() == QGraphicsItem.CacheMode.DeviceCoordinateCache
    assert item.data(0) == str(image_path)
    assert item.data(1) == DEFAULT_IMAGE_DPI
    assert item.data(2) == DEFAULT_IMAGE_DPI


def test_select_image_item_selects_scene_item(tmp_path) -> None:
    QApplication.instance() or QApplication([])
    image_path = tmp_path / "art.png"
    image = QImage(20, 10, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.red)
    assert image.save(str(image_path))

    viewport = PrintViewport()
    item = viewport.add_image_file(image_path, QPointF(12, 34))
    assert item is not None

    viewport.select_image_item(item)

    assert item.isSelected()


def test_area_select_selects_items_touched_by_rect() -> None:
    QApplication.instance() or QApplication([])
    output_dir = Path("test_outputs") / "area_select"
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / "select.png"
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(image_path, dpi=(10, 10))

    viewport = PrintViewport()
    first = viewport.add_image_file(image_path, QPointF(0, 0))
    second = viewport.add_image_file(image_path, QPointF(1000, 0))
    assert first is not None
    assert second is not None

    selection_rect = first.sceneBoundingRect().adjusted(-10, -10, -first.sceneBoundingRect().width() + 1, 10)
    selected = viewport.select_items_in_rect(selection_rect)

    assert selected == [first]
    assert first.isSelected()
    assert not second.isSelected()


def test_shift_area_select_toggles_items_touched_by_rect() -> None:
    QApplication.instance() or QApplication([])
    output_dir = Path("test_outputs") / "shift_area_select"
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / "select.png"
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(image_path, dpi=(10, 10))

    viewport = PrintViewport()
    first = viewport.add_image_file(image_path, QPointF(0, 0))
    second = viewport.add_image_file(image_path, QPointF(1000, 0))
    assert first is not None
    assert second is not None
    first.setSelected(True)

    selection_rect = QRectF(-500, -500, 2000, 1000)
    touched = viewport.select_items_in_rect(selection_rect, toggle=True, initial_selection={first})

    assert touched == [first, second]
    assert not first.isSelected()
    assert second.isSelected()


def test_shift_click_toggles_single_item_selection() -> None:
    app = QApplication.instance() or QApplication([])
    output_dir = Path("test_outputs") / "shift_click_select"
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / "select.png"
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(image_path, dpi=(10, 10))

    viewport = PrintViewport()
    viewport.resize(800, 600)
    viewport.show()
    item = viewport.add_image_file(image_path, QPointF(0, 0))
    assert item is not None
    viewport.centerOn(item)
    app.processEvents()

    position = QPointF(viewport.mapFromScene(item.pos()))
    event = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        position,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ShiftModifier,
    )

    viewport.mousePressEvent(event)

    assert event.isAccepted()
    assert item.isSelected()

    second_event = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        position,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ShiftModifier,
    )

    viewport.mousePressEvent(second_event)

    assert second_event.isAccepted()
    assert not item.isSelected()


def test_selected_items_have_blue_highlight_above_cut_paths() -> None:
    QApplication.instance() or QApplication([])
    output_dir = Path("test_outputs") / "selected_highlight"
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / "selected.png"
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(image_path, dpi=(10, 10))

    viewport = PrintViewport()
    item = viewport.add_image_file(image_path, QPointF(0, 0))
    assert item is not None

    item.setSelected(True)
    viewport.refresh_selection_highlights()

    assert len(viewport._selection_highlight_items) == 1
    assert viewport._selection_highlight_items[0].pen().color().name() == "#2563eb"
    assert viewport._selection_highlight_items[0].brush().color().alpha() > 0
    assert viewport._selection_highlight_items[0].zValue() > viewport._cut_path_items[0].zValue()


def test_ctrl_a_selects_all_image_items() -> None:
    QApplication.instance() or QApplication([])
    output_dir = Path("test_outputs") / "ctrl_a_select"
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / "select.png"
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(image_path, dpi=(10, 10))

    viewport = PrintViewport()
    first = viewport.add_image_file(image_path, QPointF(0, 0))
    second = viewport.add_image_file(image_path, QPointF(1000, 0))
    assert first is not None
    assert second is not None

    event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    viewport.keyPressEvent(event)

    assert event.isAccepted()
    assert first.isSelected()
    assert second.isSelected()


def test_ctrl_i_inverts_image_selection() -> None:
    QApplication.instance() or QApplication([])
    output_dir = Path("test_outputs") / "ctrl_i_select"
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / "select.png"
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(image_path, dpi=(10, 10))

    viewport = PrintViewport()
    first = viewport.add_image_file(image_path, QPointF(0, 0))
    second = viewport.add_image_file(image_path, QPointF(1000, 0))
    assert first is not None
    assert second is not None
    first.setSelected(True)

    event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_I, Qt.KeyboardModifier.ControlModifier)
    viewport.keyPressEvent(event)

    assert event.isAccepted()
    assert not first.isSelected()
    assert second.isSelected()


def test_delete_selected_items_removes_items_and_refreshes_cut_paths() -> None:
    QApplication.instance() or QApplication([])
    output_dir = Path("test_outputs") / "delete_items"
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / "delete.png"
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(image_path, dpi=(10, 10))

    viewport = PrintViewport()
    first = viewport.add_image_file(image_path, QPointF(0, 0))
    second = viewport.add_image_file(image_path, QPointF(1000, 0))
    assert first is not None
    assert second is not None
    first.setSelected(True)
    removed = []
    viewport.image_removed.connect(removed.append)

    event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier)
    viewport.keyPressEvent(event)

    assert event.isAccepted()
    assert removed == [first]
    assert first.scene() is None
    assert second.scene() is viewport.scene()
    assert len(viewport._image_items()) == 1
    assert len(viewport.cut_segments()) == 4


def test_item_panel_emits_graphics_item_when_clicked(tmp_path) -> None:
    QApplication.instance() or QApplication([])
    image_path = tmp_path / "panel-art.png"
    image = QImage(20, 10, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.blue)
    assert image.save(str(image_path))

    viewport = PrintViewport()
    item = viewport.add_image_file(image_path, QPointF(12, 34))
    assert item is not None

    panel = ItemPanel()
    selected = []
    panel.image_selected.connect(selected.append)
    panel.add_image_item(item, str(image_path))

    list_item = panel._list.item(0)
    panel._handle_item_clicked(list_item)

    assert list_item.text() == image_path.name
    assert selected == [item]


def test_item_panel_removes_deleted_image_row() -> None:
    QApplication.instance() or QApplication([])
    output_dir = Path("test_outputs") / "panel_delete"
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / "panel-delete.png"
    image = QImage(20, 10, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.blue)
    assert image.save(str(image_path))

    viewport = PrintViewport()
    item = viewport.add_image_file(image_path, QPointF(12, 34))
    assert item is not None

    panel = ItemPanel()
    panel.add_image_item(item, str(image_path))

    panel.remove_image_item(item)

    assert panel._list.count() == 0


def test_item_panel_uses_pixel_scroll_mode() -> None:
    QApplication.instance() or QApplication([])
    panel = ItemPanel()

    assert panel._list.verticalScrollMode() == QAbstractItemView.ScrollMode.ScrollPerPixel
    assert panel._list.verticalScrollBar().singleStep() == 12


def test_item_panel_row_displays_dimensions_dpi_and_toggles(tmp_path) -> None:
    QApplication.instance() or QApplication([])
    image_path = tmp_path / "panel-info.png"
    Image.new("RGBA", (600, 300), (0, 0, 255, 255)).save(image_path, dpi=(300, 300))

    viewport = PrintViewport()
    item = viewport.add_image_file(image_path, QPointF(12, 34))
    assert item is not None

    panel = ItemPanel()
    panel.add_image_item(item, str(image_path))
    row = panel._list.itemWidget(panel._list.item(0))
    assert isinstance(row, ItemRowWidget)

    labels = [label.text() for label in row.findChildren(QLabel)]
    toggles = {toggle.text(): toggle.isChecked() for toggle in row.findChildren(QCheckBox)}

    assert row.height() == ROW_HEIGHT
    assert panel._list.item(0).sizeHint().height() == ROW_HEIGHT
    assert image_path.name in labels
    assert "2.00 x 1.00 in" in labels
    assert "300 dpi" in labels
    assert toggles == {"Round": True}


def test_image_import_reads_dpi_metadata(tmp_path) -> None:
    image_path = tmp_path / "dpi-art.png"
    Image.new("RGBA", (600, 300), (255, 0, 0, 255)).save(image_path, dpi=(300, 300))

    info = read_image_import_info(image_path)

    assert info.pixel_width == 600
    assert info.pixel_height == 300
    assert round(info.dpi_x) == 300
    assert round(info.dpi_y) == 300
    assert round(info.width_in, 3) == 2
    assert round(info.height_in, 3) == 1


def test_add_image_file_uses_dpi_for_scene_size(tmp_path) -> None:
    QApplication.instance() or QApplication([])
    image_path = tmp_path / "sized-art.png"
    Image.new("RGBA", (600, 300), (0, 0, 255, 255)).save(image_path, dpi=(300, 300))

    viewport = PrintViewport()
    item = viewport.add_image_file(image_path, QPointF(0, 0))

    assert item is not None
    assert round(item.transform().m11(), 3) == 0.24
    assert round(item.transform().m22(), 3) == 0.24
    assert round(item.data(3), 3) == 2
    assert round(item.data(4), 3) == 1


def test_item_rounding_resizes_to_nearest_inch_and_restores(tmp_path) -> None:
    QApplication.instance() or QApplication([])
    image_path = tmp_path / "roundable.png"
    Image.new("RGBA", (825, 390), (0, 0, 255, 255)).save(image_path, dpi=(300, 300))

    viewport = PrintViewport()
    item = viewport.add_image_file(image_path, QPointF(0, 0))
    assert item is not None

    assert round(item.data(3), 2) == 2.75
    assert round(item.data(4), 2) == 1.30

    viewport.set_item_rounding(item, True)

    assert item.data(3) == 3
    assert item.data(4) == 1
    assert item.data(9) is True
    assert round(item.transform().m11(), 3) == round((3 * 72) / 825, 3)
    assert round(item.transform().m22(), 3) == round((1 * 72) / 390, 3)

    viewport.set_item_rounding(item, False)

    assert round(item.data(3), 2) == 2.75
    assert round(item.data(4), 2) == 1.30
    assert item.data(9) is False


def test_auto_nest_predefined_places_matching_items_and_ignores_other_sizes() -> None:
    app = QApplication.instance() or QApplication([])
    output_dir = Path("test_outputs") / "auto_nest_viewport"
    output_dir.mkdir(parents=True, exist_ok=True)
    layout_path = output_dir / "layouts.json"
    layout_path.write_text(
        json.dumps(
            {
                "layouts": [
                    {
                        "score": 100,
                        "utilization": 0.5,
                        "placements": [
                            {
                                "artwork": "36x24",
                                "x_in": 0,
                                "y_in": 0,
                                "width_in": 36,
                                "height_in": 24,
                                "rotated": False,
                            },
                            {
                                "artwork": "36x24",
                                "x_in": 36,
                                "y_in": 0,
                                "width_in": 36,
                                "height_in": 24,
                                "rotated": False,
                            },
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    art_path = output_dir / "36x24.png"
    other_path = output_dir / "other.png"
    Image.new("RGBA", (360, 240), (0, 0, 255, 255)).save(art_path, dpi=(10, 10))
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(other_path, dpi=(10, 10))

    viewport = PrintViewport()
    first = viewport.add_image_file(art_path, QPointF(0, 0))
    second = viewport.add_image_file(art_path, QPointF(0, 0))
    other = viewport.add_image_file(other_path, QPointF(0, 0))
    app.processEvents()

    placed, unmatched = viewport.auto_nest_predefined(layout_path)

    assert placed == 2
    assert unmatched == 1
    assert first is not None
    assert second is not None
    assert other is not None
    assert first.pos() != QPointF(0, 0)
    assert second.pos() != QPointF(0, 0)
    assert other.pos() == QPointF(0, 0)
    assert viewport.artboard_settings().width_in >= 94


def test_auto_nest_predefined_is_stable_when_run_twice_on_rotated_item() -> None:
    app = QApplication.instance() or QApplication([])
    output_dir = Path("test_outputs") / "auto_nest_repeat"
    output_dir.mkdir(parents=True, exist_ok=True)
    layout_path = output_dir / "layouts.json"
    layout_path.write_text(
        json.dumps(
            {
                "layouts": [
                    {
                        "score": 100,
                        "utilization": 0.5,
                        "placements": [
                            {
                                "artwork": "36x24",
                                "x_in": 0,
                                "y_in": 0,
                                "width_in": 36,
                                "height_in": 24,
                                "rotated": False,
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    portrait_path = output_dir / "24x36.png"
    Image.new("RGBA", (240, 360), (0, 0, 255, 255)).save(portrait_path, dpi=(10, 10))

    viewport = PrintViewport()
    item = viewport.add_image_file(portrait_path, QPointF(0, 0))
    assert item is not None
    app.processEvents()

    first_result = viewport.auto_nest_predefined(layout_path)
    first_rotation = item.rotation()
    first_transform = (round(item.transform().m11(), 6), round(item.transform().m22(), 6))
    first_pos = item.pos()

    second_result = viewport.auto_nest_predefined(layout_path)
    second_rotation = item.rotation()
    second_transform = (round(item.transform().m11(), 6), round(item.transform().m22(), 6))
    second_pos = item.pos()

    assert first_result == (1, 0)
    assert second_result == (1, 0)
    assert first_rotation == 90
    assert second_rotation == first_rotation
    assert second_transform == first_transform
    assert second_pos == first_pos


def test_auto_nest_predefined_only_places_selected_items_when_selection_exists() -> None:
    app = QApplication.instance() or QApplication([])
    output_dir = Path("test_outputs") / "auto_nest_selected"
    output_dir.mkdir(parents=True, exist_ok=True)
    layout_path = output_dir / "layouts.json"
    layout_path.write_text(
        json.dumps(
            {
                "layouts": [
                    {
                        "score": 100,
                        "utilization": 0.5,
                        "placements": [
                            {
                                "artwork": "36x24",
                                "x_in": 0,
                                "y_in": 0,
                                "width_in": 36,
                                "height_in": 24,
                                "rotated": False,
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    art_path = output_dir / "36x24.png"
    Image.new("RGBA", (360, 240), (0, 0, 255, 255)).save(art_path, dpi=(10, 10))

    viewport = PrintViewport()
    selected = viewport.add_image_file(art_path, QPointF(0, 0))
    unselected = viewport.add_image_file(art_path, QPointF(500, 500))
    assert selected is not None
    assert unselected is not None
    selected.setSelected(True)
    app.processEvents()

    placed, unmatched = viewport.auto_nest_predefined(layout_path)

    assert placed == 1
    assert unmatched == 0
    assert selected.pos() != QPointF(0, 0)
    assert viewport._sheet_rect_for_index(1).contains(selected.pos())
    assert viewport._sheet_rect_for_index(0).contains(unselected.pos())


def test_auto_nest_predefined_does_not_treat_unmatched_targets_as_movable_for_occupancy() -> None:
    app = QApplication.instance() or QApplication([])
    output_dir = Path("test_outputs") / "auto_nest_unmatched_occupancy"
    output_dir.mkdir(parents=True, exist_ok=True)
    layout_path = output_dir / "layouts.json"
    layout_path.write_text(
        json.dumps(
            {
                "layouts": [
                    {
                        "score": 100,
                        "utilization": 0.5,
                        "placements": [
                            {
                                "artwork": "36x24",
                                "x_in": 0,
                                "y_in": 0,
                                "width_in": 36,
                                "height_in": 24,
                                "rotated": False,
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    preset_path = output_dir / "36x24.png"
    unmatched_path = output_dir / "10x10.png"
    Image.new("RGBA", (360, 240), (0, 0, 255, 255)).save(preset_path, dpi=(10, 10))
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(unmatched_path, dpi=(10, 10))

    viewport = PrintViewport()
    preset = viewport.add_image_file(preset_path, QPointF(0, 0))
    unmatched = viewport.add_image_file(unmatched_path, viewport._sheet_rect_for_index(0).center())
    assert preset is not None
    assert unmatched is not None
    app.processEvents()

    placed, unmatched_count = viewport.auto_nest_predefined(layout_path)

    assert placed == 1
    assert unmatched_count == 1
    assert viewport._sheet_rect_for_index(1).contains(preset.pos())
    assert viewport._sheet_rect_for_index(0).contains(unmatched.pos())


def test_grid_then_auto_nest_does_not_mix_test_set_unmatched_item_with_auto_nested_items() -> None:
    QApplication.instance() or QApplication([])
    image_dir = Path(r"C:\Users\Egor\Desktop\test-set")
    if not image_dir.exists():
        pytest.skip("requires local Desktop test-set fixture")

    viewport = PrintViewport()
    imported = 0
    for path in sorted(image_dir.iterdir()):
        if path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
            continue

        item = viewport.add_image_file(path, QPointF(0, 0))
        if item is None:
            continue

        viewport.set_item_rounding(item, True)
        imported += 1

    assert imported == 70

    viewport.grid_nest()
    placed, unmatched = viewport.auto_nest_predefined()

    assert placed > 0
    assert unmatched > 0

    target = next(
        item
        for item in viewport._image_items()
        if Path(item.data(0)).name == "(4068468644)-_-Graphic#815-17 copy.jpg"
    )
    target_sheets = [
        sheet_index
        for sheet_index in range(viewport._sheet_count())
        if _rects_overlap_with_area(viewport._item_logical_rect(target), viewport._sheet_rect_for_index(sheet_index))
    ]
    assert len(target_sheets) == 1

    target_sheet = target_sheets[0]
    preset_items_on_target_sheet = []
    for item in viewport._image_items():
        if item is target:
            continue
        if not _rects_overlap_with_area(viewport._item_logical_rect(item), viewport._sheet_rect_for_index(target_sheet)):
            continue
        if classify_preset_size(float(item.data(3) or 0), float(item.data(4) or 0)) is not None:
            preset_items_on_target_sheet.append(Path(item.data(0)).name)

    assert preset_items_on_target_sheet == []


def test_auto_nest_sheet_rects_are_arranged_three_columns_wide() -> None:
    QApplication.instance() or QApplication([])
    viewport = PrintViewport()
    sheet_width_points = viewport._sheet.width_in * 72
    sheet_height_points = viewport._sheet.height_in * 72
    gap_points = AUTO_NEST_SHEET_GAP_IN * 72

    first = viewport._sheet_rect_for_index(0)
    second = viewport._sheet_rect_for_index(1)
    third = viewport._sheet_rect_for_index(2)
    fourth = viewport._sheet_rect_for_index(AUTO_NEST_SHEET_COLUMNS)

    assert second.left() - first.left() == sheet_width_points + gap_points
    assert third.left() - second.left() == sheet_width_points + gap_points
    assert fourth.left() == first.left()
    assert fourth.top() - first.top() == sheet_height_points + gap_points


def test_grid_nest_prefers_orientation_that_uses_more_vertical_space() -> None:
    placements = group_grid_placements(
        [
            GridItem(key="first", width_in=36, height_in=24),
            GridItem(key="second", width_in=36, height_in=24),
        ],
        safe_width_in=94,
        safe_height_in=46,
    )

    assert len(placements) == 2
    assert all(placement.rotated for placement in placements)
    assert all(placement.width_in == 24 for placement in placements)
    assert all(placement.height_in == 36 for placement in placements)
    assert placements[1].x_in == 24


def test_grid_nest_fills_vertical_space_before_next_column() -> None:
    placements = group_grid_placements(
        [
            GridItem(key="one", width_in=10, height_in=10),
            GridItem(key="two", width_in=10, height_in=10),
            GridItem(key="three", width_in=10, height_in=10),
            GridItem(key="four", width_in=10, height_in=10),
            GridItem(key="five", width_in=10, height_in=10),
        ],
        safe_width_in=30,
        safe_height_in=40,
    )

    assert [(placement.x_in, placement.y_in) for placement in placements] == [
        (0, 0),
        (0, 10),
        (0, 20),
        (0, 30),
        (10, 0),
    ]


def test_grid_nest_adds_gap_between_different_size_groups() -> None:
    placements = group_grid_placements(
        [
            GridItem(key="large", width_in=20, height_in=20),
            GridItem(key="small", width_in=10, height_in=10),
        ],
        safe_width_in=40,
        safe_height_in=40,
    )

    assert placements[0].key == "large"
    assert placements[1].key == "small"
    assert placements[1].y_in >= placements[0].height_in + 0.2


def test_viewport_grid_nest_groups_same_size_items_in_rotated_grid() -> None:
    QApplication.instance() or QApplication([])
    output_dir = Path("test_outputs") / "grid_nest"
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / "36x24.png"
    Image.new("RGBA", (360, 240), (255, 0, 0, 255)).save(image_path, dpi=(10, 10))

    viewport = PrintViewport()
    viewport.set_artboard(ArtboardSettings(width_in=94.5, height_in=46.5, margin_in=0.25))
    first = viewport.add_image_file(image_path, QPointF(0, 0))
    second = viewport.add_image_file(image_path, QPointF(0, 0))
    assert first is not None
    assert second is not None

    placed = viewport.grid_nest()

    assert placed == 2
    assert first.rotation() == 90
    assert second.rotation() == 90
    assert first.pos().y() == second.pos().y()
    assert abs(first.pos().x() - second.pos().x()) == 24 * 72
    assert len(viewport.cut_segments()) == 7


def test_grid_nest_only_places_selected_items_when_selection_exists() -> None:
    QApplication.instance() or QApplication([])
    output_dir = Path("test_outputs") / "grid_nest_selected"
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / "10x10.png"
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(image_path, dpi=(10, 10))

    viewport = PrintViewport()
    first = viewport.add_image_file(image_path, QPointF(100, 100))
    second = viewport.add_image_file(image_path, QPointF(900, 900))
    assert first is not None
    assert second is not None
    first.setSelected(True)

    placed = viewport.grid_nest()

    assert placed == 1
    assert first.pos() != QPointF(100, 100)
    assert second.pos() == QPointF(900, 900)
    assert viewport._sheet_rect_for_index(1).contains(first.pos())
    assert viewport._sheet_rect_for_index(0).contains(second.pos())


def test_fill_space_nest_places_largest_items_first_into_existing_free_space() -> None:
    placements = fill_space_placements(
        [
            SpaceItem(key="small", width_in=10, height_in=10),
            SpaceItem(key="large", width_in=20, height_in=20),
        ],
        sheet_count=1,
        safe_width_in=40.2,
        safe_height_in=20,
        occupied_by_sheet={0: [SpaceRect(0, 0, 20, 20)]},
    )

    assert [placement.key for placement in placements] == ["large", "small"]
    assert placements[0].x_in == 20
    assert placements[0].y_in == 0
    assert placements[1].sheet_index == 1


def test_fill_space_nest_keeps_gap_when_adjacent_edges_have_different_lengths() -> None:
    placements = fill_space_placements(
        [SpaceItem(key="large", width_in=20, height_in=20)],
        sheet_count=1,
        safe_width_in=40.2,
        safe_height_in=20,
        occupied_by_sheet={0: [SpaceRect(0, 0, 20, 10)]},
    )

    assert len(placements) == 1
    assert placements[0].x_in == 20.2
    assert placements[0].y_in == 0


def test_viewport_fill_space_nest_fills_existing_sheet_hole_with_selected_item() -> None:
    QApplication.instance() or QApplication([])
    output_dir = Path("test_outputs") / "fill_space_nest"
    output_dir.mkdir(parents=True, exist_ok=True)
    blocker_path = output_dir / "blocker.png"
    target_path = output_dir / "target.png"
    Image.new("RGBA", (200, 460), (0, 0, 255, 255)).save(blocker_path, dpi=(10, 10))
    Image.new("RGBA", (200, 460), (255, 0, 0, 255)).save(target_path, dpi=(10, 10))

    viewport = PrintViewport()
    viewport.set_artboard(ArtboardSettings(width_in=41.0, height_in=46.5, margin_in=0.25))
    blocker = viewport.add_image_file(blocker_path, viewport._margin_rect().center())
    target = viewport.add_image_file(target_path, viewport._sheet_rect_for_index(1).center())
    assert blocker is not None
    assert target is not None
    viewport.set_item_rounding(blocker, True)
    viewport.set_item_rounding(target, True)
    blocker.setPos(QPointF(viewport._margin_rect().left() + 10 * 72, viewport._margin_rect().center().y()))
    target.setSelected(True)

    placed = viewport.fill_space_nest()

    assert placed == 1
    assert viewport._sheet_rect_for_index(0).contains(target.pos())
    assert target.pos().x() > blocker.pos().x()
    assert viewport.intersecting_image_items() == []


def test_viewport_keeps_one_empty_sheet_after_last_sheet_is_occupied() -> None:
    QApplication.instance() or QApplication([])
    output_dir = Path("test_outputs") / "sheet_management"
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / "occupy.png"
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(image_path, dpi=(10, 10))

    viewport = PrintViewport()
    item = viewport.add_image_file(image_path, viewport._sheet_rect_for_index(0).center())
    assert item is not None
    assert viewport._sheet_count() == 2

    item.setPos(viewport._sheet_rect_for_index(1).center())
    viewport._ensure_trailing_empty_sheet()

    assert viewport._sheet_count() == 2
    assert viewport._sheet_rect_for_index(0).contains(item.pos())
    assert viewport._occupied_sheet_indices() == {0}


def test_viewport_removes_intermediate_blank_sheets() -> None:
    QApplication.instance() or QApplication([])
    output_dir = Path("test_outputs") / "sheet_cleanup"
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / "cleanup.png"
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(image_path, dpi=(10, 10))

    viewport = PrintViewport()
    first = viewport.add_image_file(image_path, viewport._sheet_rect_for_index(0).center())
    second = viewport.add_image_file(image_path, viewport._sheet_rect_for_index(0).center())
    assert first is not None
    assert second is not None

    viewport._ensure_sheet_count(5)
    second.setPos(viewport._sheet_rect_for_index(3).center())
    viewport._ensure_trailing_empty_sheet()

    assert viewport._sheet_count() == 3
    assert viewport._sheet_rect_for_index(0).contains(first.pos())
    assert viewport._sheet_rect_for_index(1).contains(second.pos())
    assert viewport._occupied_sheet_indices() == {0, 1}


def test_existing_extra_sheets_update_when_artboard_changes() -> None:
    QApplication.instance() or QApplication([])
    viewport = PrintViewport()
    viewport._ensure_sheet_count(2)

    viewport.set_artboard(ArtboardSettings(width_in=94.5, height_in=46.5, margin_in=0.25))

    _shadow, sheet, margin = viewport._extra_sheet_items[0]
    assert sheet.rect() == viewport._sheet_rect_for_index(1)
    assert margin.rect() == viewport._margin_rect_for_sheet_rect(viewport._sheet_rect_for_index(1))


def test_items_keep_sheet_relative_position_when_artboard_changes() -> None:
    QApplication.instance() or QApplication([])
    output_dir = Path("test_outputs") / "sheet_resize_items"
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / "resize.png"
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(image_path, dpi=(10, 10))

    viewport = PrintViewport()
    viewport._ensure_sheet_count(2)
    old_rect = viewport._sheet_rect_for_index(1)
    item = viewport.add_image_file(image_path, old_rect.center())
    assert item is not None

    viewport.set_artboard(ArtboardSettings(width_in=94.5, height_in=96, margin_in=0.25))

    assert viewport._sheet_rect_for_index(1).contains(item.pos())
    assert item.pos() == viewport._sheet_rect_for_index(1).center()


def test_sheet_marker_layout_places_corners_asymmetry_marker_and_limits_gaps() -> None:
    markers = sheet_marker_layout(width_in=48, height_in=96, margin_in=0.25)
    positions = {(round(marker.center_x_in, 3), round(marker.center_y_in, 3)) for marker in markers}

    assert all(marker.diameter_mm == MARKER_DIAMETER_MM for marker in markers)
    assert (0.125, 0.125) in positions
    assert (47.875, 0.125) in positions
    assert (47.875, 95.875) in positions
    assert (0.125, 95.875) in positions
    assert (0.125, 91.875) in positions

    left_edge_y = sorted(marker.center_y_in for marker in markers if round(marker.center_x_in, 3) == 0.125)
    assert max(second - first for first, second in zip(left_edge_y, left_edge_y[1:])) <= MAX_MARKER_GAP_IN


def test_viewport_draws_markers_around_items_on_occupied_sheets() -> None:
    QApplication.instance() or QApplication([])
    output_dir = Path("test_outputs") / "content_markers"
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / "marker.png"
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(image_path, dpi=(10, 10))

    viewport = PrintViewport()

    assert len(viewport._marker_items) == 0

    item = viewport.add_image_file(image_path, viewport._sheet_rect_for_index(0).center())
    assert item is not None

    assert len(viewport._marker_items) == 5
    assert viewport._marker_items[0].brush().color().name() == "#000000"
    assert round(viewport._marker_items[0].rect().width(), 3) == round((MARKER_DIAMETER_MM / 25.4) * 72, 3)
    item_bounds = viewport._item_logical_rect(item)
    marker_padding = ITEM_MARKER_GAP_IN * 72
    marker_bounds = item_bounds.adjusted(-marker_padding, -marker_padding, marker_padding, marker_padding)
    for marker in viewport._marker_items:
        assert marker_bounds.contains(marker.rect().center())

    viewport._ensure_sheet_count(4)

    assert len(viewport._marker_items) == 5


def test_cut_segments_dedupe_shared_touching_edge() -> None:
    segments = cut_segments_for_rects(
        [
            CutRect(left=0, top=0, right=10, bottom=10),
            CutRect(left=10, top=0, right=20, bottom=10),
        ]
    )
    keys = {
        tuple(round(value, 3) for value in (segment.x1, segment.y1, segment.x2, segment.y2))
        for segment in segments
    }
    reversed_shared_key = (10, 10, 10, 0)

    assert len(segments) == 7
    assert reversed_shared_key not in keys


def test_viewport_draws_cut_paths_around_images() -> None:
    QApplication.instance() or QApplication([])
    output_dir = Path("test_outputs") / "cut_paths"
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / "cut.png"
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(image_path, dpi=(10, 10))

    viewport = PrintViewport()
    first = viewport.add_image_file(image_path, QPointF(0, 0))
    assert first is not None
    second = viewport.add_image_file(image_path, QPointF(float(first.data(3)) * 72, 0))
    assert second is not None

    viewport.refresh_cut_paths()
    assert len(viewport.cut_segments()) == 7
    assert len(viewport._cut_path_items) == 7
    assert viewport._cut_path_items[0].pen().color().name() == "#ff0000"


def test_fast_view_mode_switches_pixmaps_to_fast_transform(tmp_path) -> None:
    QApplication.instance() or QApplication([])
    image_path = tmp_path / "fast-view.png"
    Image.new("RGBA", (20, 10), (255, 0, 0, 255)).save(image_path)

    viewport = PrintViewport()
    item = viewport.add_image_file(image_path, QPointF(0, 0))
    assert item is not None
    assert item.transformationMode() == Qt.TransformationMode.SmoothTransformation

    viewport._set_fast_view(True)
    assert item.transformationMode() == Qt.TransformationMode.FastTransformation

    viewport._set_fast_view(False)
    assert item.transformationMode() == Qt.TransformationMode.SmoothTransformation


def test_print_ready_items_are_fully_inside_print_area_and_can_be_highlighted() -> None:
    QApplication.instance() or QApplication([])
    output_dir = Path("test_outputs") / "ready_highlight"
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / "ready.png"
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(image_path, dpi=(10, 10))

    viewport = PrintViewport()
    print_area = viewport._print_area_rects()[0]
    ready = viewport.add_image_file(image_path, print_area.center())
    outside = viewport.add_image_file(image_path, QPointF(print_area.right() + 500, print_area.center().y()))

    assert ready is not None
    assert outside is not None
    assert viewport.print_ready_items() == [ready]

    viewport.set_ready_highlight_enabled(True)
    assert len(viewport._ready_highlight_items) == 1
    assert viewport._ready_highlight_items[0].rect() == ready.sceneBoundingRect()

    viewport.set_ready_highlight_enabled(False)
    assert viewport._ready_highlight_items == []


def test_print_ready_items_can_touch_print_area_edges() -> None:
    QApplication.instance() or QApplication([])
    output_dir = Path("test_outputs") / "ready_highlight_edges"
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / "edge-touching.png"
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(image_path, dpi=(10, 10))

    viewport = PrintViewport()
    print_area = viewport._print_area_rects()[0]
    item = viewport.add_image_file(image_path, QPointF(0, 0))
    assert item is not None

    half_width = item.sceneBoundingRect().width() / 2
    half_height = item.sceneBoundingRect().height() / 2
    item.setPos(QPointF(print_area.left() + half_width, print_area.top() + half_height))

    assert viewport.print_ready_items() == [item]


def test_intersecting_image_items_exclude_margin_and_edge_touching_items() -> None:
    QApplication.instance() or QApplication([])
    output_dir = Path("test_outputs") / "ready_highlight_intersections"
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / "intersecting.png"
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(image_path, dpi=(10, 10))

    viewport = PrintViewport()
    print_area = viewport._print_area_rects()[0]

    margin_crossing = viewport.add_image_file(image_path, QPointF(0, 0))
    first_overlap = viewport.add_image_file(image_path, QPointF(print_area.center().x(), print_area.center().y()))
    second_overlap = viewport.add_image_file(image_path, QPointF(print_area.center().x() + 10, print_area.center().y()))
    touching = viewport.add_image_file(image_path, QPointF(0, 0))
    assert margin_crossing is not None
    assert first_overlap is not None
    assert second_overlap is not None
    assert touching is not None

    half_width = touching.sceneBoundingRect().width() / 2
    half_height = touching.sceneBoundingRect().height() / 2
    margin_crossing.setPos(QPointF(print_area.right() + half_width - 10, print_area.top() + half_width))
    touching.setPos(QPointF(first_overlap.sceneBoundingRect().right() + half_width, print_area.bottom() - half_height))

    assert viewport.intersecting_image_items() == [second_overlap, first_overlap]

    viewport.set_ready_highlight_enabled(True)
    highlight_colors = [highlight.pen().color().name() for highlight in viewport._ready_highlight_items]
    assert highlight_colors.count("#dc2626") == 2
    assert "#00a651" in highlight_colors


def test_intersecting_image_items_ignore_tiny_edge_overlap() -> None:
    QApplication.instance() or QApplication([])
    output_dir = Path("test_outputs") / "ready_highlight_tiny_overlap"
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / "tiny-overlap.png"
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(image_path, dpi=(10, 10))

    viewport = PrintViewport()
    print_area = viewport._print_area_rects()[0]
    first = viewport.add_image_file(image_path, print_area.center())
    second = viewport.add_image_file(image_path, QPointF(0, 0))
    assert first is not None
    assert second is not None

    half_width = second.sceneBoundingRect().width() / 2
    second.setPos(QPointF(first.sceneBoundingRect().right() + half_width - 0.5, first.pos().y()))

    assert viewport.intersecting_image_items() == []


def test_item_panel_round_checkbox_defaults_on_and_can_restore(tmp_path) -> None:
    QApplication.instance() or QApplication([])
    image_path = tmp_path / "panel-round.png"
    Image.new("RGBA", (825, 390), (0, 0, 255, 255)).save(image_path, dpi=(300, 300))

    viewport = PrintViewport()
    item = viewport.add_image_file(image_path, QPointF(0, 0))
    assert item is not None

    panel = ItemPanel()
    panel.image_rounding_changed.connect(viewport.set_item_rounding)
    panel.add_image_item(item, str(image_path))
    row = panel._list.itemWidget(panel._list.item(0))
    assert isinstance(row, ItemRowWidget)

    labels = [label.text() for label in row.findChildren(QLabel)]
    assert "3.00 x 1.00 in" in labels
    assert item.data(3) == 3
    assert item.data(4) == 1

    round_toggle = next(toggle for toggle in row.findChildren(QCheckBox) if toggle.text() == "Round")
    assert round_toggle.isChecked()
    round_toggle.setChecked(False)

    labels = [label.text() for label in row.findChildren(QLabel)]
    assert "2.75 x 1.30 in" in labels


def test_pdf_import_reads_page_size_and_preview(tmp_path) -> None:
    pdf_path = tmp_path / "art.pdf"
    document = fitz.open()
    document.new_page(width=144, height=72)
    document.save(pdf_path)
    document.close()

    info = read_artwork_import_info(pdf_path)

    assert info.kind == ArtworkKind.PDF
    assert info.width_points == 144
    assert info.height_points == 72
    assert info.preview_png is not None
    assert info.width_in == 2
    assert info.height_in == 1


def test_add_pdf_file_uses_page_size_for_scene_size(tmp_path) -> None:
    QApplication.instance() or QApplication([])
    pdf_path = tmp_path / "sized-art.pdf"
    document = fitz.open()
    document.new_page(width=144, height=72)
    document.save(pdf_path)
    document.close()

    viewport = PrintViewport()
    item = viewport.add_artwork_file(pdf_path, QPointF(0, 0))

    assert item is not None
    assert item.data(5) == ArtworkKind.PDF.value
    assert item.data(6) == 0
    assert round(item.transform().m11(), 3) == 0.5
    assert round(item.transform().m22(), 3) == 0.5
    assert round(item.data(3), 3) == 2
    assert round(item.data(4), 3) == 1


def test_pdf_compatible_ai_imports_as_pdf(tmp_path) -> None:
    ai_path = tmp_path / "compatible.ai"
    document = fitz.open()
    document.new_page(width=216, height=144)
    document.save(ai_path)
    document.close()

    info = read_artwork_import_info(ai_path)

    assert info.kind == ArtworkKind.PDF
    assert info.width_in == 3
    assert info.height_in == 2


class FakeProgress:
    def __init__(self, cancel_at_value: int | None = None) -> None:
        self.cancel_at_value = cancel_at_value
        self.value = 0
        self.labels: list[str] = []

    def setValue(self, value: int) -> None:
        self.value = value

    def setLabelText(self, text: str) -> None:
        self.labels.append(text)

    def wasCanceled(self) -> bool:
        return self.cancel_at_value is not None and self.value >= self.cancel_at_value


def test_import_artwork_batch_reports_progress_and_offsets_items(monkeypatch) -> None:
    QApplication.instance() or QApplication([])
    viewport = PrintViewport()
    imported: list[tuple[Path, QPointF]] = []

    def fake_add(path: Path, position: QPointF):
        imported.append((path, position))
        return object()

    monkeypatch.setattr(viewport, "add_artwork_file", fake_add)
    paths = [Path("one.png"), Path("two.pdf")]
    progress = FakeProgress()

    added = viewport._import_artwork_batch(paths, QPointF(10, 20), progress)

    assert added == 2
    assert imported == [(paths[0], QPointF(10, 20)), (paths[1], QPointF(34, 44))]
    assert progress.labels == ["Importing one.png", "Importing two.pdf"]
    assert progress.value == 2


def test_import_artwork_batch_can_be_canceled_between_files(monkeypatch) -> None:
    QApplication.instance() or QApplication([])
    viewport = PrintViewport()
    imported: list[Path] = []

    def fake_add(path: Path, _position: QPointF):
        imported.append(path)
        return object()

    monkeypatch.setattr(viewport, "add_artwork_file", fake_add)
    paths = [Path("one.png"), Path("two.pdf")]
    progress = FakeProgress(cancel_at_value=1)

    added = viewport._import_artwork_batch(paths, QPointF(0, 0), progress)

    assert added == 1
    assert imported == [paths[0]]
    assert progress.value == 1


def test_qt_image_allocation_limit_is_raised() -> None:
    QImageReader.setAllocationLimit(256)

    configure_qt_image_limits()

    assert QImageReader.allocationLimit() == IMAGE_ALLOCATION_LIMIT_MB


def test_ruler_spacing_gets_coarser_when_zoomed_out() -> None:
    QApplication.instance() or QApplication([])
    viewport = PrintViewport()
    ruler = RulerWidget(Qt.Orientation.Horizontal, viewport)

    zoomed_in_step = ruler._step_for_min_pixels(pixels_per_inch=144, minimum_pixels=64)
    zoomed_out_step = ruler._step_for_min_pixels(pixels_per_inch=6, minimum_pixels=64)

    assert zoomed_in_step < zoomed_out_step
    assert zoomed_out_step >= 25


def test_pasteboard_texture_spacing_gets_coarser_when_zoomed_out() -> None:
    QApplication.instance() or QApplication([])
    viewport = PrintViewport()

    actual_size_step = viewport._pasteboard_texture_step_for_zoom(pixels_per_inch=72)
    large_sheet_fit_step = viewport._pasteboard_texture_step_for_zoom(pixels_per_inch=8)
    far_zoomed_out_step = viewport._pasteboard_texture_step_for_zoom(pixels_per_inch=2)

    assert actual_size_step == 1
    assert large_sheet_fit_step >= 4
    assert far_zoomed_out_step >= large_sheet_fit_step


def test_artboard_grid_spacing_gets_coarser_when_zoomed_out() -> None:
    QApplication.instance() or QApplication([])
    viewport = PrintViewport()

    actual_size_step = viewport._inch_step_for_min_pixels(pixels_per_inch=72, minimum_pixels=12)
    large_sheet_fit_step = viewport._inch_step_for_min_pixels(pixels_per_inch=8, minimum_pixels=12)

    assert actual_size_step < large_sheet_fit_step
    assert large_sheet_fit_step >= 2


def test_viewport_updates_artboard_and_margin() -> None:
    QApplication.instance() or QApplication([])
    viewport = PrintViewport()

    viewport.set_artboard(ArtboardSettings(width_in=48, height_in=96, margin_in=0.5))

    settings = viewport.artboard_settings()
    assert settings.width_in == 48
    assert settings.height_in == 96
    assert settings.margin_in == 0.5
    assert viewport._sheet_item.rect().width() == 48 * 72
    assert viewport._sheet_item.rect().height() == 96 * 72
    assert viewport._margin_item.rect().width() == 47 * 72
    assert viewport._margin_item.rect().height() == 95 * 72


def test_viewport_clamps_margin_to_artboard() -> None:
    QApplication.instance() or QApplication([])
    viewport = PrintViewport()

    viewport.set_artboard(ArtboardSettings(width_in=10, height_in=8, margin_in=20))

    assert viewport.artboard_settings().margin_in == 4
    assert viewport._margin_item.rect().isEmpty()


def test_artboard_panel_emits_changed_settings() -> None:
    QApplication.instance() or QApplication([])
    panel = ArtboardPanel(ArtboardSettings(width_in=48, height_in=96, margin_in=0.25))
    changes = []
    panel.artboard_changed.connect(changes.append)

    panel._width_input.setValue(60)

    assert changes[-1].width_in == 60
    assert changes[-1].height_in == 96
    assert changes[-1].margin_in == 0.25


def test_artboard_panel_updates_sheet_export_ui() -> None:
    QApplication.instance() or QApplication([])
    panel = ArtboardPanel(ArtboardSettings(width_in=48, height_in=96, margin_in=0.25))
    panel.set_sheet_rows(
        [
            SheetPanelRow(0, 12, True, True, False),
            SheetPanelRow(1, 0, True, False, False),
        ]
    )

    labels = [label.text() for label in panel.findChildren(QLabel)]
    buttons = [button.text() for button in panel.findChildren(QPushButton)]
    thumbnails = [label for label in panel.findChildren(QLabel) if label.objectName() == "sheetThumb"]

    assert panel.minimumWidth() >= 360
    assert "Sheets" in labels
    assert "Sheet 1" in labels
    assert "Sheet 2" in labels
    assert "12 items" in labels
    assert "Empty" in labels
    assert "Ready" in labels
    assert "Print" in labels
    assert "Cut" in labels
    assert "Export All" in buttons
    assert buttons.count("Export Print") == 2
    assert buttons.count("Export Cut") == 2
    assert len(thumbnails) == 2
    assert all(not thumbnail.pixmap().isNull() for thumbnail in thumbnails)
    assert "Export Folders" in labels


def test_artboard_panel_exports_settings_from_inputs() -> None:
    QApplication.instance() or QApplication([])
    panel = ArtboardPanel(ArtboardSettings(width_in=48, height_in=96, margin_in=0.25))

    panel._print_export_input.setText("C:/print")
    panel._cut_export_input.setText("C:/cut")
    panel._temp_export_input.setText("C:/tmp/printer")

    settings = panel.export_settings()

    assert settings.print_directory.as_posix() == "C:/print"
    assert settings.cut_directory.as_posix() == "C:/cut"
    assert settings.local_temp_directory.as_posix() == "C:/tmp/printer"


def test_item_panel_is_wide_enough_for_item_rows() -> None:
    QApplication.instance() or QApplication([])
    panel = ItemPanel()

    assert panel.minimumWidth() >= 360
    assert panel.maximumWidth() >= 520


def test_export_sheet_pdf_writes_print_and_cut_layers() -> None:
    QApplication.instance() or QApplication([])
    output_dir = Path("test_outputs") / f"pdf_export_{uuid4().hex}"
    print_dir = output_dir / "print"
    cut_dir = output_dir / "cut"
    temp_dir = output_dir / "temp"
    image_path = output_dir / "art.png"
    output_dir.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(image_path, dpi=(10, 10))

    viewport = PrintViewport()
    item = viewport.add_image_file(image_path, viewport._sheet_rect_for_index(0).center())
    assert item is not None
    sheet = viewport.export_sheet_data(0)
    settings = ExportSettings(print_directory=print_dir, cut_directory=cut_dir, local_temp_directory=temp_dir)

    print_result = export_sheet_pdf(sheet, ExportKind.PRINT, settings)
    cut_result = export_sheet_pdf(sheet, ExportKind.CUT, settings)

    assert print_result.path == print_dir / "sheet_001_print.pdf"
    assert cut_result.path == cut_dir / "sheet_001_cut.pdf"
    with fitz.open(print_result.path) as document:
        assert document.page_count == 1
        assert document[0].rect.width < sheet.width_points
        assert document[0].rect.height < sheet.height_points
        layer_names = {layer["name"] for layer in document.get_ocgs().values()}
        assert layer_names == {"Artwork", "Markers"}
    with fitz.open(cut_result.path) as document:
        assert document.page_count == 1
        layer_names = {layer["name"] for layer in document.get_ocgs().values()}
        assert layer_names == {"Cut Lines", "Markers"}


def test_export_sheet_pdf_never_overwrites_existing_file() -> None:
    QApplication.instance() or QApplication([])
    output_dir = Path("test_outputs") / f"pdf_export_no_overwrite_{uuid4().hex}"
    print_dir = output_dir / "print"
    cut_dir = output_dir / "cut"
    temp_dir = output_dir / "temp"
    image_path = output_dir / "art.png"
    print_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(image_path, dpi=(10, 10))
    existing_path = print_dir / "sheet_001_print.pdf"
    existing_bytes = b"existing file must stay untouched"
    existing_path.write_bytes(existing_bytes)

    viewport = PrintViewport()
    item = viewport.add_image_file(image_path, viewport._sheet_rect_for_index(0).center())
    assert item is not None
    settings = ExportSettings(print_directory=print_dir, cut_directory=cut_dir, local_temp_directory=temp_dir)

    result = export_sheet_pdf(viewport.export_sheet_data(0), ExportKind.PRINT, settings)

    assert result.path == print_dir / "sheet_001_print_001.pdf"
    assert existing_path.read_bytes() == existing_bytes
    with fitz.open(result.path) as document:
        assert document.page_count == 1


def test_export_sheet_pdf_trims_to_half_inch_outside_markers() -> None:
    QApplication.instance() or QApplication([])
    output_dir = Path("test_outputs") / f"pdf_export_trim_{uuid4().hex}"
    print_dir = output_dir / "print"
    cut_dir = output_dir / "cut"
    temp_dir = output_dir / "temp"
    image_path = output_dir / "art.png"
    output_dir.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(image_path, dpi=(10, 10))

    viewport = PrintViewport()
    item = viewport.add_image_file(image_path, viewport._sheet_rect_for_index(0).center())
    assert item is not None
    sheet = viewport.export_sheet_data(0)
    settings = ExportSettings(print_directory=print_dir, cut_directory=cut_dir, local_temp_directory=temp_dir)

    result = export_sheet_pdf(sheet, ExportKind.PRINT, settings)

    marker_radius = (MARKER_DIAMETER_MM / 25.4) * 72 / 2
    trim_padding = EXPORT_MARKER_PADDING_IN * 72
    marker_left = min(marker.center_x - marker_radius - trim_padding for marker in sheet.markers)
    marker_top = min(marker.center_y - marker_radius - trim_padding for marker in sheet.markers)
    marker_right = max(marker.center_x + marker_radius + trim_padding for marker in sheet.markers)
    marker_bottom = max(marker.center_y + marker_radius + trim_padding for marker in sheet.markers)
    expected_width = min(sheet.width_points, marker_right) - max(0, marker_left)
    expected_height = min(sheet.height_points, marker_bottom) - max(0, marker_top)

    with fitz.open(result.path) as document:
        assert document[0].rect.width == pytest.approx(expected_width)
        assert document[0].rect.height == pytest.approx(expected_height)


def test_viewport_reports_sheet_item_summary() -> None:
    QApplication.instance() or QApplication([])
    output_dir = Path("test_outputs") / "sheet_summary"
    image_path = output_dir / "art.png"
    output_dir.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(image_path, dpi=(10, 10))

    viewport = PrintViewport()
    item = viewport.add_image_file(image_path, viewport._sheet_rect_for_index(0).center())
    assert item is not None

    rows = viewport.sheet_item_summary()

    assert rows[0] == (0, 1, True)
    assert rows[1] == (1, 0, True)


def test_viewport_sheet_thumbnail_renders_artwork_preview() -> None:
    QApplication.instance() or QApplication([])
    output_dir = Path("test_outputs") / "sheet_thumbnail"
    image_path = output_dir / "art.png"
    output_dir.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(image_path, dpi=(10, 10))

    viewport = PrintViewport()
    item = viewport.add_image_file(image_path, viewport._sheet_rect_for_index(0).center())
    assert item is not None

    thumbnail = viewport.sheet_thumbnail(0, QSize(96, 72)).toImage()

    red_pixels = 0
    for y in range(thumbnail.height()):
        for x in range(thumbnail.width()):
            color = thumbnail.pixelColor(x, y)
            if color.red() > 180 and color.green() < 80 and color.blue() < 80:
                red_pixels += 1

    assert red_pixels > 0


def test_export_sheet_pdf_rejects_unconfigured_destination() -> None:
    sheet = PrintViewport().export_sheet_data(0)
    settings = ExportSettings(print_directory=Path(""), cut_directory=Path("test_outputs/cut"), local_temp_directory=Path("test_outputs/temp"))

    with pytest.raises(ValueError, match="print export directory"):
        export_sheet_pdf(sheet, ExportKind.PRINT, settings)
