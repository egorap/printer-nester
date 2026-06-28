from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path

import fitz
from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QFont,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QTransform,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
)
from PySide6.QtWidgets import QGraphicsEllipseItem
from PySide6.QtWidgets import QApplication, QProgressDialog

from printer_nester.core.artboard import DEFAULT_ARTBOARD, ArtboardSettings
from printer_nester.core.artwork_import import (
    SUPPORTED_ARTWORK_EXTENSIONS,
    ArtworkKind,
    read_artwork_import_info,
)
from printer_nester.core.auto_nest import (
    PREDEFINED_SAFE_AREA_HEIGHT_IN,
    PREDEFINED_SAFE_AREA_WIDTH_IN,
    choose_layouts_for_counts,
    classify_preset_size,
    load_layout_catalog,
)
from printer_nester.core.cut_paths import (
    CutRect,
    CutSegment,
    cut_segments_for_rects,
    dedupe_cut_segments,
)
from printer_nester.core.grid_nest import GridItem, group_grid_placements
from printer_nester.core.item_transform import ItemTransform
from printer_nester.core.markers import (
    MARKER_DIAMETER_MM,
    MM_PER_INCH,
    sheet_marker_layout,
)
from printer_nester.core.pdf_export import (
    ExportArtwork,
    ExportMarker,
    ExportSegment,
    ExportSheet,
)
from printer_nester.core.space_nest import SpaceItem, SpaceRect, fill_space_placements
from printer_nester.ui.qt_settings import configure_qt_image_limits


POINTS_PER_INCH = 72
PASTEBOARD_SIZE_INCHES = 2_000
PASTEBOARD_TEXTURE_STEPS_INCHES = (1, 2, 4, 6, 12, 24, 48, 96)
GRID_STEPS_INCHES = (0.125, 0.25, 0.5, 1, 2, 4, 6, 12, 24, 48)
AUTO_NEST_LAYOUT_PATH = Path("data/layout_search_results.json")
AUTO_NEST_SHEET_GAP_IN = 8.0
AUTO_NEST_SHEET_COLUMNS = 3
ITEM_MARKER_GAP_IN = 0.5
MIN_VIEWPORT_ZOOM = 0.01
MAX_VIEWPORT_ZOOM = 8.0
ITEM_ROTATION_DATA_KEY = 12


@dataclass(frozen=True, slots=True)
class SheetPreset:
    name: str
    width_in: float
    height_in: float

    @property
    def rect_points(self) -> QRectF:
        width = self.width_in * POINTS_PER_INCH
        height = self.height_in * POINTS_PER_INCH
        return QRectF(-width / 2, -height / 2, width, height)


DEFAULT_SHEET = SheetPreset(
    name=f"{DEFAULT_ARTBOARD.width_in:g} x {DEFAULT_ARTBOARD.height_in:g} in",
    width_in=DEFAULT_ARTBOARD.width_in,
    height_in=DEFAULT_ARTBOARD.height_in,
)


class PrintViewport(QGraphicsView):
    zoom_changed = Signal(int)
    viewport_changed = Signal()
    sheet_layout_changed = Signal()
    image_added = Signal(object, str)
    image_removed = Signal(object)
    image_selection_changed = Signal(object)
    image_transform_changed = Signal(object)

    def __init__(
        self,
        sheet: SheetPreset = DEFAULT_SHEET,
        margin_in: float = DEFAULT_ARTBOARD.margin_in,
    ) -> None:
        super().__init__()

        self._sheet = sheet
        self._margin_in = margin_in
        self._zoom = 1.0
        self._is_panning = False
        self._is_area_selecting = False
        self._area_select_toggles = False
        self._space_pan_active = False
        self._last_pan_point = QPoint()
        self._area_select_origin = QPointF()
        self._area_select_initial_selection: set[QGraphicsPixmapItem] = set()
        self._area_select_item: QGraphicsRectItem | None = None
        self._fast_view_active = False

        self._scene = QGraphicsScene(self)
        self._scene.setSceneRect(self._pasteboard_rect())
        self.setScene(self._scene)

        shadow_offset = 12
        self._sheet_shadow = QGraphicsRectItem(
            sheet.rect_points.translated(shadow_offset, shadow_offset)
        )
        self._sheet_shadow.setBrush(QColor(0, 0, 0, 38))
        self._sheet_shadow.setPen(Qt.PenStyle.NoPen)
        self._sheet_shadow.setZValue(-20)
        self._scene.addItem(self._sheet_shadow)

        self._sheet_item = QGraphicsRectItem(sheet.rect_points)
        self._sheet_item.setBrush(QColor("#ffffff"))
        self._sheet_item.setPen(QPen(QColor("#9aa0a6"), 1.25))
        self._sheet_item.setZValue(-10)
        self._scene.addItem(self._sheet_item)

        self._margin_item = QGraphicsRectItem(self._margin_rect())
        margin_pen = QPen(QColor("#d45545"), 0.9, Qt.PenStyle.DashLine)
        margin_pen.setCosmetic(True)
        self._margin_item.setBrush(Qt.BrushStyle.NoBrush)
        self._margin_item.setPen(margin_pen)
        self._margin_item.setZValue(-5)
        self._margin_item.setVisible(not self._margin_rect().isEmpty())
        self._scene.addItem(self._margin_item)
        self._extra_sheet_items: list[
            tuple[QGraphicsRectItem, QGraphicsRectItem, QGraphicsRectItem]
        ] = []
        self._marker_items: list[QGraphicsEllipseItem] = []
        self._cut_path_items: list[QGraphicsLineItem] = []
        self._selection_highlight_items: list[QGraphicsRectItem] = []
        self._ready_highlight_enabled = False
        self._ready_highlight_items: list[QGraphicsRectItem] = []
        self._refresh_sheet_markers()

        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.TextAntialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setViewportUpdateMode(
            QGraphicsView.ViewportUpdateMode.MinimalViewportUpdate
        )
        self.setOptimizationFlag(
            QGraphicsView.OptimizationFlag.DontSavePainterState, True
        )
        self.setOptimizationFlag(
            QGraphicsView.OptimizationFlag.DontAdjustForAntialiasing, True
        )
        self.setCacheMode(QGraphicsView.CacheModeFlag.CacheNone)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setBackgroundBrush(QColor("#cfd3d7"))
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self._scene.selectionChanged.connect(self.refresh_selection_highlights)
        self._scene.selectionChanged.connect(self._emit_image_selection_changed)
        self.horizontalScrollBar().valueChanged.connect(
            lambda _value: self.viewport_changed.emit()
        )
        self.verticalScrollBar().valueChanged.connect(
            lambda _value: self.viewport_changed.emit()
        )

    def fit_sheet(self) -> None:
        self.fitInView(
            self._sheet.rect_points.adjusted(-48, -48, 48, 48),
            Qt.AspectRatioMode.KeepAspectRatio,
        )
        self._zoom = self.transform().m11()
        self._emit_zoom()
        self.viewport_changed.emit()

    def actual_size(self) -> None:
        self.resetTransform()
        self._zoom = 1.0
        self.centerOn(self._sheet.rect_points.center())
        self._emit_zoom()
        self.viewport_changed.emit()

    def zoom_in(self) -> None:
        self._scale_view(1.2)

    def zoom_out(self) -> None:
        self._scale_view(1 / 1.2)

    def artboard_settings(self) -> ArtboardSettings:
        return ArtboardSettings(
            width_in=self._sheet.width_in,
            height_in=self._sheet.height_in,
            margin_in=self._margin_in,
        )

    def set_artboard(self, artboard: ArtboardSettings) -> None:
        preserved_positions = self._sheet_relative_item_positions()
        width = max(1.0, artboard.width_in)
        height = max(1.0, artboard.height_in)
        margin = max(0.0, min(artboard.margin_in, min(width, height) / 2))

        self._sheet = SheetPreset(
            name=f"{width:g} x {height:g} in", width_in=width, height_in=height
        )
        self._margin_in = margin

        shadow_offset = 12
        self._sheet_shadow.setRect(
            self._sheet.rect_points.translated(shadow_offset, shadow_offset)
        )
        self._sheet_item.setRect(self._sheet.rect_points)
        margin_rect = self._margin_rect()
        self._margin_item.setRect(margin_rect)
        self._margin_item.setVisible(not margin_rect.isEmpty())

        self._refresh_extra_sheet_rects()
        self._restore_sheet_relative_item_positions(preserved_positions)
        self._refresh_sheet_markers()
        self._scene.update()
        self.viewport_changed.emit()
        self.refresh_cut_paths()
        self.refresh_selection_highlights()
        self.refresh_ready_highlights()
        self.sheet_layout_changed.emit()

    def add_image_file(
        self, path: Path, scene_position: QPointF
    ) -> QGraphicsPixmapItem | None:
        return self.add_artwork_file(path, scene_position)

    def add_artwork_file(
        self, path: Path, scene_position: QPointF
    ) -> QGraphicsPixmapItem | None:
        configure_qt_image_limits()

        try:
            import_info = read_artwork_import_info(path)
        except (RuntimeError, ValueError, fitz.FileDataError):
            return None

        pixmap = QPixmap()
        if import_info.kind == ArtworkKind.PDF and import_info.preview_png is not None:
            pixmap.loadFromData(import_info.preview_png, "PNG")
        else:
            pixmap.load(str(path))

        if pixmap.isNull():
            return None

        x_scale = import_info.width_points / pixmap.width()
        y_scale = import_info.height_points / pixmap.height()

        item = QGraphicsPixmapItem(pixmap)
        item.setOffset(-pixmap.width() / 2, -pixmap.height() / 2)
        item.setPos(scene_position)
        item.setTransform(QTransform.fromScale(x_scale, y_scale))
        item.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        item.setShapeMode(QGraphicsPixmapItem.ShapeMode.BoundingRectShape)
        item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        item.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
        item.setData(0, str(path))
        item.setData(1, import_info.preview_dpi)
        item.setData(2, import_info.preview_dpi)
        item.setData(3, import_info.width_in)
        item.setData(4, import_info.height_in)
        item.setData(5, import_info.kind.value)
        item.setData(6, import_info.page_index)
        item.setData(7, import_info.width_in)
        item.setData(8, import_info.height_in)
        item.setData(10, import_info.width_in)
        item.setData(11, import_info.height_in)
        item.setZValue(10)

        self._scene.addItem(item)
        self.image_added.emit(item, str(path))
        self._ensure_trailing_empty_sheet()
        self.refresh_cut_paths()
        self.refresh_ready_highlights()
        self.viewport_changed.emit()
        self.sheet_layout_changed.emit()
        return item

    def select_image_item(self, item: QGraphicsPixmapItem) -> None:
        self._scene.clearSelection()
        item.setSelected(True)
        self.centerOn(item)
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def select_items_in_rect(
        self,
        rect: QRectF,
        toggle: bool = False,
        initial_selection: set[QGraphicsPixmapItem] | None = None,
    ) -> list[QGraphicsPixmapItem]:
        selection_rect = rect.normalized()
        touched: list[QGraphicsPixmapItem] = []
        for item in self._image_items():
            if _rects_touch_or_overlap(selection_rect, item.sceneBoundingRect()):
                touched.append(item)

        if toggle:
            base_selection = (
                initial_selection
                if initial_selection is not None
                else set(self._selected_image_items())
            )
            touched_set = set(touched)
            for item in self._image_items():
                item.setSelected((item in base_selection) ^ (item in touched_set))
        else:
            self._scene.clearSelection()
            for item in touched:
                item.setSelected(True)

        return touched

    def delete_selected_items(self) -> int:
        selected_items = [item for item in self._image_items() if item.isSelected()]
        for item in selected_items:
            self._scene.removeItem(item)
            self.image_removed.emit(item)

        if selected_items:
            self._ensure_trailing_empty_sheet()
            self.refresh_cut_paths()
            self.refresh_ready_highlights()
            self.viewport_changed.emit()
            self.sheet_layout_changed.emit()

        return len(selected_items)

    def select_all_image_items(self) -> int:
        items = self._image_items()
        for item in items:
            item.setSelected(True)
        return len(items)

    def invert_image_selection(self) -> int:
        selected_count = 0
        for item in self._image_items():
            item.setSelected(not item.isSelected())
            if item.isSelected():
                selected_count += 1
        return selected_count

    def align_selected_items(self, mode: str) -> int:
        items = self._selected_image_items()
        if len(items) < 2:
            return 0

        bounds = _united_item_bounds(
            [self._item_footprint_rect(item) for item in items]
        )
        if bounds is None:
            return 0

        moved_items: list[QGraphicsPixmapItem] = []
        for item in items:
            item_rect = self._item_footprint_rect(item)
            delta = QPointF(0, 0)
            if mode == "left":
                delta.setX(bounds.left() - item_rect.left())
            elif mode == "center_x":
                delta.setX(bounds.center().x() - item_rect.center().x())
            elif mode == "right":
                delta.setX(bounds.right() - item_rect.right())
            elif mode == "top":
                delta.setY(bounds.top() - item_rect.top())
            elif mode == "center_y":
                delta.setY(bounds.center().y() - item_rect.center().y())
            elif mode == "bottom":
                delta.setY(bounds.bottom() - item_rect.bottom())
            else:
                return 0

            if _is_meaningful_delta(delta):
                item.setPos(item.pos() + delta)
                moved_items.append(item)

        self._refresh_after_items_moved(moved_items)
        return len(moved_items)

    def close_selected_item_gaps(self, axis: str) -> int:
        items = self._selected_image_items()
        if len(items) < 2:
            return 0

        if axis == "horizontal":
            sorted_items = sorted(
                items,
                key=lambda item: (
                    self._item_footprint_rect(item).left(),
                    self._item_footprint_rect(item).top(),
                ),
            )
            cursor = self._item_footprint_rect(sorted_items[0]).right()
            moved_items: list[QGraphicsPixmapItem] = []
            for item in sorted_items[1:]:
                item_rect = self._item_footprint_rect(item)
                delta = QPointF(cursor - item_rect.left(), 0)
                if _is_meaningful_delta(delta):
                    item.setPos(item.pos() + delta)
                    moved_items.append(item)
                    item_rect = self._item_footprint_rect(item)
                cursor = item_rect.right()
        elif axis == "vertical":
            sorted_items = sorted(
                items,
                key=lambda item: (
                    self._item_footprint_rect(item).top(),
                    self._item_footprint_rect(item).left(),
                ),
            )
            cursor = self._item_footprint_rect(sorted_items[0]).bottom()
            moved_items = []
            for item in sorted_items[1:]:
                item_rect = self._item_footprint_rect(item)
                delta = QPointF(0, cursor - item_rect.top())
                if _is_meaningful_delta(delta):
                    item.setPos(item.pos() + delta)
                    moved_items.append(item)
                    item_rect = self._item_footprint_rect(item)
                cursor = item_rect.bottom()
        else:
            return 0

        self._refresh_after_items_moved(moved_items)
        return len(moved_items)

    def _emit_image_selection_changed(self) -> None:
        selected = self._selected_image_items()
        self.image_selection_changed.emit(selected[0] if selected else None)

    def item_transform(self, item: QGraphicsPixmapItem) -> ItemTransform:
        item_rect = self._item_logical_rect(item)
        sheet_index = self._sheet_index_for_item(item)
        sheet_rect = self._sheet_rect_for_index(
            sheet_index if sheet_index is not None else 0
        )
        return ItemTransform(
            x_in=(item_rect.left() - sheet_rect.left()) / POINTS_PER_INCH,
            y_in=(item_rect.top() - sheet_rect.top()) / POINTS_PER_INCH,
            width_in=item_rect.width() / POINTS_PER_INCH,
            height_in=item_rect.height() / POINTS_PER_INCH,
            rotation_deg=_item_rotation(item),
        )

    def set_item_transform(
        self, item: QGraphicsPixmapItem, transform: ItemTransform
    ) -> None:
        if item is None or item.scene() is not self._scene:
            return

        sheet_index = self._sheet_index_for_item(item)
        sheet_index = sheet_index if sheet_index is not None else 0
        self._ensure_sheet_count(sheet_index + 1)
        sheet_rect = self._sheet_rect_for_index(sheet_index)
        width_in = max(0.001, transform.width_in)
        height_in = max(0.001, transform.height_in)

        rotation = transform.rotation_deg
        if not _apply_item_geometry(item, width_in, height_in, rotation):
            return

        item.setPos(
            QPointF(
                sheet_rect.left() + (transform.x_in + width_in / 2) * POINTS_PER_INCH,
                sheet_rect.top() + (transform.y_in + height_in / 2) * POINTS_PER_INCH,
            )
        )
        item.setData(3, width_in)
        item.setData(4, height_in)
        item.setData(10, width_in)
        item.setData(11, height_in)
        self._refresh_after_item_transform(item)

    def _refresh_after_item_transform(self, item: QGraphicsPixmapItem) -> None:
        self._ensure_trailing_empty_sheet()
        self.refresh_cut_paths()
        self.refresh_selection_highlights()
        self._refresh_sheet_markers()
        self.refresh_ready_highlights()
        self.viewport_changed.emit()
        self.sheet_layout_changed.emit()
        self.image_transform_changed.emit(item)

    def _refresh_after_items_moved(self, items: list[QGraphicsPixmapItem]) -> None:
        if not items:
            return

        self._ensure_trailing_empty_sheet()
        self.refresh_cut_paths()
        self.refresh_selection_highlights()
        self._refresh_sheet_markers()
        self.refresh_ready_highlights()
        self.viewport_changed.emit()
        self.sheet_layout_changed.emit()
        for item in items:
            if item.scene() is self._scene:
                self.image_transform_changed.emit(item)

    def set_item_rounding(self, item: QGraphicsPixmapItem, enabled: bool) -> None:
        original_width = float(item.data(7) or item.data(3) or 0)
        original_height = float(item.data(8) or item.data(4) or 0)
        if original_width <= 0 or original_height <= 0:
            return

        if enabled:
            width_in = self._round_to_nearest_inch(original_width)
            height_in = self._round_to_nearest_inch(original_height)
        else:
            width_in = original_width
            height_in = original_height

        if not _apply_item_geometry(item, width_in, height_in, _item_rotation(item)):
            return

        item.setData(9, enabled)
        item.setData(10, width_in)
        item.setData(11, height_in)
        self.refresh_cut_paths()
        self.refresh_selection_highlights()
        self._refresh_sheet_markers()
        self.refresh_ready_highlights()
        self.sheet_layout_changed.emit()
        self.image_transform_changed.emit(item)

    def auto_nest_predefined(
        self, layout_path: Path = AUTO_NEST_LAYOUT_PATH
    ) -> tuple[int, int]:
        if not layout_path.exists():
            return 0, 0

        catalog = load_layout_catalog(layout_path)
        target_items = self._nest_target_items()
        matching_items, unmatched_count = self._matching_preset_items(target_items)
        placeable_items = {item for items in matching_items.values() for item in items}
        available_counts = {
            artwork: len(items) for artwork, items in matching_items.items()
        }
        chosen_layouts = choose_layouts_for_counts(available_counts, catalog)
        if not chosen_layouts:
            return 0, unmatched_count

        self._ensure_predefined_sheet_size()
        sheet_indices = self._empty_sheet_indices(
            len(chosen_layouts), excluded_items=placeable_items
        )
        pools = {artwork: list(items) for artwork, items in matching_items.items()}
        placed = 0

        for sheet_index, layout in enumerate(chosen_layouts):
            sheet_top_left = self._sheet_safe_area_top_left(sheet_indices[sheet_index])
            for placement in layout.get("placements", []):
                if not isinstance(placement, dict):
                    continue
                artwork = placement.get("artwork")
                if not isinstance(artwork, str) or not pools.get(artwork):
                    continue

                item = pools[artwork].pop(0)
                x = float(placement.get("x_in", 0))
                y = float(placement.get("y_in", 0))
                width = float(placement.get("width_in", 0))
                height = float(placement.get("height_in", 0))
                self._place_item_on_sheet(item, sheet_top_left, x, y, width, height)
                placed += 1

        self._ensure_trailing_empty_sheet()
        self.viewport_changed.emit()
        self.sheet_layout_changed.emit()
        self._scene.update()
        self.refresh_cut_paths()
        self.refresh_selection_highlights()
        self.refresh_ready_highlights()
        return placed, unmatched_count

    def grid_nest(self) -> int:
        items = self._nest_target_items()
        if not items:
            return 0

        grid_items: list[GridItem] = []
        item_by_key: dict[str, QGraphicsPixmapItem] = {}
        for index, item in enumerate(items):
            width = float(item.data(3) or 0)
            height = float(item.data(4) or 0)
            if width <= 0 or height <= 0:
                continue

            key = str(index)
            grid_items.append(GridItem(key=key, width_in=width, height_in=height))
            item_by_key[key] = item

        safe_rect = self._margin_rect()
        safe_width_in = safe_rect.width() / POINTS_PER_INCH
        safe_height_in = safe_rect.height() / POINTS_PER_INCH
        placements = group_grid_placements(grid_items, safe_width_in, safe_height_in)
        if not placements:
            return 0

        sheet_indices = self._empty_sheet_indices(
            max(placement.sheet_index for placement in placements) + 1,
            excluded_items=set(items),
        )
        for placement in placements:
            item = item_by_key.get(placement.key)
            if item is None:
                continue
            self._place_item_on_sheet(
                item,
                self._sheet_safe_area_top_left(sheet_indices[placement.sheet_index]),
                placement.x_in,
                placement.y_in,
                placement.width_in,
                placement.height_in,
            )

        self._ensure_trailing_empty_sheet()
        self.viewport_changed.emit()
        self.sheet_layout_changed.emit()
        self._scene.update()
        self.refresh_cut_paths()
        self.refresh_selection_highlights()
        self.refresh_ready_highlights()
        return len(placements)

    def fill_space_nest(self) -> int:
        items = self._nest_target_items()
        if not items:
            return 0

        space_items: list[SpaceItem] = []
        item_by_key: dict[str, QGraphicsPixmapItem] = {}
        for index, item in enumerate(items):
            width = float(item.data(3) or 0)
            height = float(item.data(4) or 0)
            if width <= 0 or height <= 0:
                continue

            key = str(index)
            space_items.append(SpaceItem(key=key, width_in=width, height_in=height))
            item_by_key[key] = item

        safe_rect = self._margin_rect()
        safe_width_in = safe_rect.width() / POINTS_PER_INCH
        safe_height_in = safe_rect.height() / POINTS_PER_INCH
        placements = fill_space_placements(
            space_items,
            self._sheet_count(),
            safe_width_in,
            safe_height_in,
            self._occupied_space_rects_by_sheet(excluded_items=set(items)),
        )
        if not placements:
            return 0

        self._ensure_sheet_count(
            max(placement.sheet_index for placement in placements) + 1
        )
        for placement in placements:
            item = item_by_key.get(placement.key)
            if item is None:
                continue
            self._place_item_on_sheet(
                item,
                self._sheet_safe_area_top_left(placement.sheet_index),
                placement.x_in,
                placement.y_in,
                placement.width_in,
                placement.height_in,
            )

        self._ensure_trailing_empty_sheet()
        self.viewport_changed.emit()
        self.sheet_layout_changed.emit()
        self._scene.update()
        self.refresh_cut_paths()
        self.refresh_selection_highlights()
        self.refresh_ready_highlights()
        return len(placements)

    def cut_segments(self):
        return self._cut_segments_for_items(self._image_items())

    def _cut_segments_for_items(
        self, items: list[QGraphicsPixmapItem]
    ) -> list[CutSegment]:
        rects: list[CutRect] = []
        segments: list[CutSegment] = []
        for item in items:
            if _is_orthogonal_rotation(_item_rotation(item)):
                bounds = self._item_footprint_rect(item)
                rects.append(
                    CutRect(
                        bounds.left(), bounds.top(), bounds.right(), bounds.bottom()
                    )
                )
            else:
                segments.extend(self._item_polygon_cut_segments(item))

        return dedupe_cut_segments([*cut_segments_for_rects(rects), *segments])

    def _item_polygon_cut_segments(self, item: QGraphicsPixmapItem) -> list[CutSegment]:
        polygon = item.mapToScene(_item_local_pixmap_rect(item))
        if polygon.count() < 4:
            return []

        points = [polygon.at(index) for index in range(4)]
        return [
            CutSegment(
                points[index].x(),
                points[index].y(),
                points[(index + 1) % 4].x(),
                points[(index + 1) % 4].y(),
            )
            for index in range(4)
        ]

    def exportable_sheet_indices(self) -> list[int]:
        return sorted(self._occupied_sheet_indices())

    def sheet_item_summary(self) -> list[tuple[int, int, bool]]:
        ready_items = set(self.print_ready_items())
        rows: list[tuple[int, int, bool]] = []
        for sheet_index in range(self._sheet_count()):
            sheet_rect = self._sheet_rect_for_index(sheet_index)
            items = [
                item
                for item in self._image_items()
                if _rects_overlap_with_area(self._item_footprint_rect(item), sheet_rect)
            ]
            rows.append(
                (sheet_index, len(items), all(item in ready_items for item in items))
            )

        return rows

    def sheet_thumbnail(self, sheet_index: int, size: QSize = QSize(96, 72)) -> QPixmap:
        pixmap = QPixmap(size)
        pixmap.fill(QColor("#eef1f4"))

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self._scene.render(
            painter,
            QRectF(0, 0, size.width(), size.height()),
            self._sheet_rect_for_index(sheet_index),
            Qt.AspectRatioMode.KeepAspectRatio,
        )
        painter.end()
        return pixmap

    def export_sheet_data(self, sheet_index: int) -> ExportSheet:
        sheet_rect = self._sheet_rect_for_index(sheet_index)
        sheet_top_left = sheet_rect.topLeft()
        artworks: list[ExportArtwork] = []
        sheet_items: list[QGraphicsPixmapItem] = []

        for item in self._image_items():
            item_rect = self._item_cut_bounds(item)
            if not _rects_overlap_with_area(item_rect, sheet_rect):
                continue

            sheet_items.append(item)
            local_rect = item_rect.translated(
                QPointF(-sheet_top_left.x(), -sheet_top_left.y())
            )
            artworks.append(
                ExportArtwork(
                    path=Path(str(item.data(0))),
                    kind=ArtworkKind(str(item.data(5) or ArtworkKind.RASTER.value)),
                    rect_points=(
                        local_rect.left(),
                        local_rect.top(),
                        local_rect.right(),
                        local_rect.bottom(),
                    ),
                    rotation=int(_item_rotation(item)) % 360,
                    page_index=int(item.data(6) or 0),
                )
            )

        cut_segments = [
            ExportSegment(
                segment.x1 - sheet_top_left.x(),
                segment.y1 - sheet_top_left.y(),
                segment.x2 - sheet_top_left.x(),
                segment.y2 - sheet_top_left.y(),
            )
            for segment in self._cut_segments_for_items(sheet_items)
        ]
        markers = [
            ExportMarker(
                center_x=position.x() - sheet_top_left.x(),
                center_y=position.y() - sheet_top_left.y(),
                diameter_mm=MARKER_DIAMETER_MM,
            )
            for position in self._sheet_marker_positions(sheet_index)
        ]
        return ExportSheet(
            sheet_index=sheet_index,
            width_points=sheet_rect.width(),
            height_points=sheet_rect.height(),
            artworks=artworks,
            cut_segments=cut_segments,
            markers=markers,
        )

    def refresh_cut_paths(self) -> None:
        self._clear_cut_paths()
        pen = QPen(QColor("#ff0000"), 1.5, Qt.PenStyle.SolidLine)
        pen.setCosmetic(True)
        for segment in self.cut_segments():
            item = QGraphicsLineItem(segment.x1, segment.y1, segment.x2, segment.y2)
            item.setPen(pen)
            item.setZValue(900)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
            self._scene.addItem(item)
            self._cut_path_items.append(item)

    def refresh_selection_highlights(self) -> None:
        self._clear_selection_highlights()
        pen = QPen(QColor("#2563eb"), 3, Qt.PenStyle.SolidLine)
        pen.setCosmetic(True)
        for item in self._selected_image_items():
            highlight = QGraphicsRectItem(item.sceneBoundingRect())
            highlight.setBrush(QColor(37, 99, 235, 42))
            highlight.setPen(pen)
            highlight.setZValue(950)
            highlight.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
            self._scene.addItem(highlight)
            self._selection_highlight_items.append(highlight)

    def print_ready_items(self) -> list[QGraphicsPixmapItem]:
        print_areas = self._print_area_rects()
        if not print_areas:
            return []

        ready: list[QGraphicsPixmapItem] = []
        for item in self._scene.items():
            if not isinstance(item, QGraphicsPixmapItem):
                continue

            bounds = item.sceneBoundingRect()
            if bounds.isEmpty():
                continue

            if any(_contains_rect_with_tolerance(area, bounds) for area in print_areas):
                ready.append(item)

        return ready

    def set_ready_highlight_enabled(self, enabled: bool) -> None:
        self._ready_highlight_enabled = enabled
        self.refresh_ready_highlights()

    def refresh_ready_highlights(self) -> None:
        self._clear_ready_highlights()
        if not self._ready_highlight_enabled:
            return

        intersecting_items = set(self.intersecting_image_items())

        pen = QPen(QColor("#00a651"), 3, Qt.PenStyle.SolidLine)
        pen.setCosmetic(True)
        for item in self.print_ready_items():
            if item in intersecting_items:
                continue
            self._add_ready_highlight(
                item.sceneBoundingRect(), pen, QColor(0, 166, 81, 42)
            )

        red_pen = QPen(QColor("#dc2626"), 3, Qt.PenStyle.SolidLine)
        red_pen.setCosmetic(True)
        for item in intersecting_items:
            self._add_ready_highlight(
                item.sceneBoundingRect(), red_pen, QColor(220, 38, 38, 46)
            )

    def intersecting_image_items(self) -> list[QGraphicsPixmapItem]:
        image_items = [
            item
            for item in self._image_items()
            if not self._item_footprint_rect(item).isEmpty()
        ]
        intersecting: list[QGraphicsPixmapItem] = []
        for index, item in enumerate(image_items):
            bounds = self._item_footprint_rect(item)
            if any(
                _rects_overlap_with_area(bounds, self._item_footprint_rect(other))
                for other in image_items[index + 1 :]
            ):
                intersecting.append(item)
                continue
            if any(
                _rects_overlap_with_area(bounds, self._item_footprint_rect(other))
                for other in image_items[:index]
            ):
                intersecting.append(item)

        return intersecting

    def _ensure_predefined_sheet_size(self) -> None:
        required_width = PREDEFINED_SAFE_AREA_WIDTH_IN + self._margin_in * 2
        required_height = PREDEFINED_SAFE_AREA_HEIGHT_IN + self._margin_in * 2
        if (
            self._sheet.width_in >= required_width
            and self._sheet.height_in >= required_height
        ):
            return

        self.set_artboard(
            ArtboardSettings(
                width_in=max(self._sheet.width_in, required_width),
                height_in=max(self._sheet.height_in, required_height),
                margin_in=self._margin_in,
            )
        )

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        super().drawBackground(painter, rect)
        self._draw_pasteboard_texture(painter, rect)
        self._draw_grid(painter, rect)
        self._draw_sheet_labels(painter)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._has_supported_image(event.mimeData()):
            event.acceptProposedAction()
            return

        event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if self._has_supported_image(event.mimeData()):
            event.acceptProposedAction()
            return

        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = self._supported_image_paths(event.mimeData())
        if not paths:
            event.ignore()
            return

        scene_position = self.mapToScene(event.position().toPoint())
        progress = self._make_import_progress_dialog(len(paths))
        added = self._import_artwork_batch(paths, scene_position, progress)

        if added:
            event.acceptProposedAction()
            return

        event.ignore()

    def wheelEvent(self, event: QWheelEvent) -> None:
        wheel_delta = event.angleDelta().y() or event.pixelDelta().y()
        if wheel_delta == 0:
            event.ignore()
            return

        factor = 1.15 if wheel_delta > 0 else 1 / 1.15
        self._set_fast_view(True)
        self._scale_view(factor, event.position())
        self._set_fast_view(False)
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() in {
            Qt.MouseButton.MiddleButton,
            Qt.MouseButton.RightButton,
        } or (event.button() == Qt.MouseButton.LeftButton and self._space_pan_active):
            self._start_pan(event.position().toPoint())
            event.accept()
            return

        if (
            event.button() == Qt.MouseButton.LeftButton
            and event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            item = self._image_item_at(event.position().toPoint())
            if item is not None:
                item.setSelected(not item.isSelected())
                self.setFocus(Qt.FocusReason.MouseFocusReason)
                event.accept()
                return

        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._should_start_area_select(event.position().toPoint())
        ):
            self._start_area_select(
                self.mapToScene(event.position().toPoint()),
                toggle=bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier),
            )
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._is_panning:
            current = event.position().toPoint()
            delta = current - self._last_pan_point
            self._last_pan_point = current
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - delta.x()
            )
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - delta.y()
            )
            event.accept()
            return

        if self._is_area_selecting:
            self._update_area_select(self.mapToScene(event.position().toPoint()))
            event.accept()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._is_panning and event.button() in {
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.MiddleButton,
            Qt.MouseButton.RightButton,
        }:
            self._stop_pan()
            event.accept()
            return

        if self._is_area_selecting and event.button() == Qt.MouseButton.LeftButton:
            self._finish_area_select(self.mapToScene(event.position().toPoint()))
            event.accept()
            return

        super().mouseReleaseEvent(event)
        self._ensure_trailing_empty_sheet()
        self.refresh_cut_paths()
        self.refresh_selection_highlights()
        self._refresh_sheet_markers()
        self.refresh_ready_highlights()
        self.viewport_changed.emit()
        self.sheet_layout_changed.emit()
        for item in self._selected_image_items():
            self.image_transform_changed.emit(item)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in {Qt.Key.Key_Delete, Qt.Key.Key_Backspace}:
            if self.delete_selected_items():
                event.accept()
                return

        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_pan_active = True
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return

        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.key() == Qt.Key.Key_A:
                self.select_all_image_items()
                event.accept()
                return
            if event.key() == Qt.Key.Key_I:
                self.invert_image_selection()
                event.accept()
                return
            if event.key() in {Qt.Key.Key_Plus, Qt.Key.Key_Equal}:
                self.zoom_in()
                event.accept()
                return
            if event.key() == Qt.Key.Key_Minus:
                self.zoom_out()
                event.accept()
                return
            if event.key() == Qt.Key.Key_0:
                self.fit_sheet()
                event.accept()
                return
            if event.key() == Qt.Key.Key_1:
                self.actual_size()
                event.accept()
                return

        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_pan_active = False
            if not self._is_panning:
                self.unsetCursor()
            event.accept()
            return

        super().keyReleaseEvent(event)

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        if self._zoom == 1.0:
            self.centerOn(self._sheet.rect_points.center())
        self.viewport_changed.emit()

    def _scale_view(
        self, factor: float, viewport_anchor: QPointF | None = None
    ) -> None:
        next_zoom = max(MIN_VIEWPORT_ZOOM, min(MAX_VIEWPORT_ZOOM, self._zoom * factor))
        factor = next_zoom / self._zoom
        if factor == 1:
            return

        anchor = (
            viewport_anchor
            if viewport_anchor is not None
            else QPointF(
                self.viewport().width() / 2,
                self.viewport().height() / 2,
            )
        )
        scene_anchor = self.mapToScene(anchor.toPoint())

        self._zoom = next_zoom
        self.scale(factor, factor)
        viewport_delta = self.mapFromScene(scene_anchor) - anchor.toPoint()
        self.horizontalScrollBar().setValue(
            self.horizontalScrollBar().value() + viewport_delta.x()
        )
        self.verticalScrollBar().setValue(
            self.verticalScrollBar().value() + viewport_delta.y()
        )
        self._emit_zoom()
        self.viewport_changed.emit()

    def _emit_zoom(self) -> None:
        self.zoom_changed.emit(round(self._zoom * 100))

    def _start_pan(self, position: QPoint) -> None:
        self._is_panning = True
        self._last_pan_point = position
        self._set_fast_view(True)
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def _stop_pan(self) -> None:
        self._is_panning = False
        self._set_fast_view(False)
        self.setCursor(
            Qt.CursorShape.OpenHandCursor
            if self._space_pan_active
            else Qt.CursorShape.ArrowCursor
        )

    def _should_start_area_select(self, viewport_position: QPoint) -> bool:
        return self._image_item_at(viewport_position) is None

    def _start_area_select(self, scene_position: QPointF, toggle: bool = False) -> None:
        self._is_area_selecting = True
        self._area_select_toggles = toggle
        self._area_select_origin = scene_position
        self._area_select_initial_selection = set(self._selected_image_items())
        if not toggle:
            self._scene.clearSelection()

        pen = QPen(QColor("#2563eb"), 1.2, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        self._area_select_item = QGraphicsRectItem(
            QRectF(scene_position, scene_position)
        )
        self._area_select_item.setBrush(QColor(37, 99, 235, 34))
        self._area_select_item.setPen(pen)
        self._area_select_item.setZValue(1100)
        self._area_select_item.setFlag(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False
        )
        self._scene.addItem(self._area_select_item)

    def _update_area_select(self, scene_position: QPointF) -> None:
        if self._area_select_item is None:
            return

        rect = QRectF(self._area_select_origin, scene_position).normalized()
        self._area_select_item.setRect(rect)
        self.select_items_in_rect(
            rect,
            toggle=self._area_select_toggles,
            initial_selection=self._area_select_initial_selection,
        )

    def _finish_area_select(self, scene_position: QPointF) -> None:
        rect = QRectF(self._area_select_origin, scene_position).normalized()
        self.select_items_in_rect(
            rect,
            toggle=self._area_select_toggles,
            initial_selection=self._area_select_initial_selection,
        )
        if self._area_select_item is not None:
            self._scene.removeItem(self._area_select_item)
            self._area_select_item = None
        self._is_area_selecting = False
        self._area_select_toggles = False
        self._area_select_initial_selection = set()

    def _set_fast_view(self, enabled: bool) -> None:
        if self._fast_view_active == enabled:
            return

        self._fast_view_active = enabled
        if enabled:
            self.setRenderHints(QPainter.RenderHint.TextAntialiasing)
            transform_mode = Qt.TransformationMode.FastTransformation
        else:
            self.setRenderHints(
                QPainter.RenderHint.Antialiasing
                | QPainter.RenderHint.TextAntialiasing
                | QPainter.RenderHint.SmoothPixmapTransform
            )
            transform_mode = Qt.TransformationMode.SmoothTransformation

        for item in self._scene.items():
            if isinstance(item, QGraphicsPixmapItem):
                item.setTransformationMode(transform_mode)

    def _draw_pasteboard_texture(self, painter: QPainter, rect: QRectF) -> None:
        pixels_per_inch = max(1.0, self.transform().m11() * POINTS_PER_INCH)
        step_in = self._pasteboard_texture_step_for_zoom(pixels_per_inch)
        step = step_in * POINTS_PER_INCH

        first_x = int(rect.left() // step) * step
        first_y = int(rect.top() // step) * step

        dot_pen = QPen(QColor("#7f8891"), 1)
        dot_pen.setCosmetic(True)
        major_pen = QPen(QColor("#5f6871"), 1)
        major_pen.setCosmetic(True)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        x = first_x
        while x <= rect.right():
            y = first_y
            while y <= rect.bottom():
                is_foot_mark = self._is_scene_multiple(
                    x, 12 * POINTS_PER_INCH
                ) and self._is_scene_multiple(
                    y,
                    12 * POINTS_PER_INCH,
                )
                painter.setPen(major_pen if is_foot_mark else dot_pen)
                mark_size = 4 if is_foot_mark else 2
                painter.drawLine(QPointF(x - mark_size, y), QPointF(x + mark_size, y))
                painter.drawLine(QPointF(x, y - mark_size), QPointF(x, y + mark_size))
                y += step
            x += step

        painter.restore()

    def _draw_grid(self, painter: QPainter, rect: QRectF) -> None:
        sheet_rect = self._sheet.rect_points
        visible = rect.intersected(sheet_rect)
        if visible.isEmpty():
            return

        pixels_per_inch = max(1.0, self.transform().m11() * POINTS_PER_INCH)
        minor_step = (
            self._inch_step_for_min_pixels(pixels_per_inch, minimum_pixels=12)
            * POINTS_PER_INCH
        )
        major_step = (
            self._inch_step_for_min_pixels(pixels_per_inch, minimum_pixels=48)
            * POINTS_PER_INCH
        )

        minor_pen = QPen(QColor("#edf0f2"), 0)
        major_pen = QPen(QColor("#d7dce0"), 0)

        self._draw_grid_lines(painter, visible, minor_step, minor_pen)
        if major_step != minor_step:
            self._draw_grid_lines(painter, visible, major_step, major_pen)

    def _draw_grid_lines(
        self, painter: QPainter, rect: QRectF, step: float, pen: QPen
    ) -> None:
        painter.setPen(pen)

        left = int(rect.left() // step) * step
        top = int(rect.top() // step) * step

        x = left
        while x <= rect.right():
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            x += step

        y = top
        while y <= rect.bottom():
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            y += step

    def _draw_sheet_labels(self, painter: QPainter) -> None:
        painter.save()
        painter.resetTransform()

        top_left = self.mapFromScene(self._sheet.rect_points.topLeft())
        painter.setPen(QPen(QColor("#5f6368")))
        painter.setFont(QFont("Segoe UI", 9))
        painter.drawText(
            top_left.x(), top_left.y() - 12, f"Artboard - {self._sheet.name}"
        )
        painter.restore()

    def _pasteboard_rect(self) -> QRectF:
        size = PASTEBOARD_SIZE_INCHES * POINTS_PER_INCH
        return QRectF(-size / 2, -size / 2, size, size)

    def _margin_rect(self) -> QRectF:
        inset = self._margin_in * POINTS_PER_INCH
        rect = self._sheet.rect_points.adjusted(inset, inset, -inset, -inset)
        if rect.width() <= 0 or rect.height() <= 0:
            return QRectF()

        return rect

    def _pasteboard_texture_step_for_zoom(self, pixels_per_inch: float) -> float:
        for step in PASTEBOARD_TEXTURE_STEPS_INCHES:
            if step * pixels_per_inch >= 28:
                return float(step)

        return float(PASTEBOARD_TEXTURE_STEPS_INCHES[-1])

    def _inch_step_for_min_pixels(
        self, pixels_per_inch: float, minimum_pixels: float
    ) -> float:
        for step in GRID_STEPS_INCHES:
            if step * pixels_per_inch >= minimum_pixels:
                return float(step)

        return float(GRID_STEPS_INCHES[-1])

    def _is_scene_multiple(self, value: float, step: float) -> bool:
        return abs((value / step) - round(value / step)) < 0.001

    def _round_to_nearest_inch(self, value: float) -> float:
        return max(1.0, float(math.floor(value + 0.5)))

    def _matching_preset_items(
        self, items: list[QGraphicsPixmapItem] | None = None
    ) -> tuple[dict[str, list[QGraphicsPixmapItem]], int]:
        matching: dict[str, list[QGraphicsPixmapItem]] = {}
        unmatched = 0
        for item in items if items is not None else self._image_items():
            width = float(item.data(3) or 0)
            height = float(item.data(4) or 0)
            match = classify_preset_size(width, height)
            if match is None:
                unmatched += 1
                continue
            matching.setdefault(match.name, []).append(item)

        return matching, unmatched

    def _image_items(self) -> list[QGraphicsPixmapItem]:
        items = [
            item
            for item in self._scene.items()
            if isinstance(item, QGraphicsPixmapItem)
        ]
        items.sort(key=lambda item: id(item))
        return items

    def _selected_image_items(self) -> list[QGraphicsPixmapItem]:
        return [item for item in self._image_items() if item.isSelected()]

    def _nest_target_items(self) -> list[QGraphicsPixmapItem]:
        selected = self._selected_image_items()
        return selected if selected else self._image_items()

    def _sheet_relative_item_positions(
        self,
    ) -> dict[QGraphicsPixmapItem, tuple[int, float, float]]:
        positions: dict[QGraphicsPixmapItem, tuple[int, float, float]] = {}
        for item in self._image_items():
            for sheet_index in range(self._sheet_count()):
                sheet_rect = self._sheet_rect_for_index(sheet_index)
                if sheet_rect.contains(item.pos()):
                    x_ratio = (item.pos().x() - sheet_rect.left()) / sheet_rect.width()
                    y_ratio = (item.pos().y() - sheet_rect.top()) / sheet_rect.height()
                    positions[item] = (sheet_index, x_ratio, y_ratio)
                    break

        return positions

    def _restore_sheet_relative_item_positions(
        self, positions: dict[QGraphicsPixmapItem, tuple[int, float, float]]
    ) -> None:
        for item, (sheet_index, x_ratio, y_ratio) in positions.items():
            if item.scene() is not self._scene:
                continue
            sheet_rect = self._sheet_rect_for_index(sheet_index)
            item.setPos(
                QPointF(
                    sheet_rect.left() + sheet_rect.width() * x_ratio,
                    sheet_rect.top() + sheet_rect.height() * y_ratio,
                )
            )

    def _image_item_at(self, viewport_position: QPoint) -> QGraphicsPixmapItem | None:
        for item in self.items(viewport_position):
            if isinstance(item, QGraphicsPixmapItem):
                return item
        return None

    def _sheet_count(self) -> int:
        return 1 + len(self._extra_sheet_items)

    def _occupied_sheet_indices(
        self, excluded_items: set[QGraphicsPixmapItem] | None = None
    ) -> set[int]:
        excluded_items = excluded_items or set()
        occupied: set[int] = set()
        for item in self._image_items():
            if item in excluded_items:
                continue

            item_rect = self._item_footprint_rect(item)
            for sheet_index in range(self._sheet_count()):
                if _rects_overlap_with_area(
                    item_rect, self._sheet_rect_for_index(sheet_index)
                ):
                    occupied.add(sheet_index)

        return occupied

    def _occupied_space_rects_by_sheet(
        self,
        excluded_items: set[QGraphicsPixmapItem] | None = None,
    ) -> dict[int, list[SpaceRect]]:
        excluded_items = excluded_items or set()
        occupied_by_sheet: dict[int, list[SpaceRect]] = {}
        for item in self._image_items():
            if item in excluded_items:
                continue

            item_rect = self._item_footprint_rect(item)
            for sheet_index in range(self._sheet_count()):
                safe_rect = self._margin_rect_for_sheet_rect(
                    self._sheet_rect_for_index(sheet_index)
                )
                intersection = item_rect.intersected(safe_rect)
                if intersection.width() <= 0.001 or intersection.height() <= 0.001:
                    continue

                occupied_by_sheet.setdefault(sheet_index, []).append(
                    SpaceRect(
                        x_in=(intersection.left() - safe_rect.left()) / POINTS_PER_INCH,
                        y_in=(intersection.top() - safe_rect.top()) / POINTS_PER_INCH,
                        width_in=intersection.width() / POINTS_PER_INCH,
                        height_in=intersection.height() / POINTS_PER_INCH,
                    )
                )

        return occupied_by_sheet

    def _first_empty_sheet_index(
        self, excluded_items: set[QGraphicsPixmapItem] | None = None
    ) -> int:
        occupied = self._occupied_sheet_indices(excluded_items=excluded_items)
        for sheet_index in range(self._sheet_count()):
            if sheet_index not in occupied:
                return sheet_index

        sheet_index = self._sheet_count()
        self._ensure_sheet_count(sheet_index + 1)
        return sheet_index

    def _empty_sheet_indices(
        self,
        count: int,
        excluded_items: set[QGraphicsPixmapItem] | None = None,
    ) -> list[int]:
        if count <= 0:
            return []

        sheet_indices: list[int] = []
        while len(sheet_indices) < count:
            occupied = self._occupied_sheet_indices(excluded_items=excluded_items)
            for sheet_index in range(self._sheet_count()):
                if sheet_index in occupied or sheet_index in sheet_indices:
                    continue
                sheet_indices.append(sheet_index)
                if len(sheet_indices) == count:
                    break

            if len(sheet_indices) < count:
                self._ensure_sheet_count(self._sheet_count() + 1)

        return sheet_indices

    def _ensure_trailing_empty_sheet(self) -> None:
        occupied = self._occupied_sheet_indices()
        if not occupied:
            self._set_sheet_count(1)
            self._ensure_sheet_count(1)
            return

        self._compact_occupied_sheets(occupied)
        occupied = self._occupied_sheet_indices()
        self._set_sheet_count(max(occupied) + 2 if occupied else 1)

    def _compact_occupied_sheets(self, occupied: set[int]) -> None:
        sheet_map = {
            old_index: new_index for new_index, old_index in enumerate(sorted(occupied))
        }
        if all(old_index == new_index for old_index, new_index in sheet_map.items()):
            return

        positions: list[tuple[QGraphicsPixmapItem, int, float, float]] = []
        for item in self._image_items():
            sheet_index = self._sheet_index_for_item(item)
            if sheet_index is None or sheet_index not in sheet_map:
                continue

            sheet_rect = self._sheet_rect_for_index(sheet_index)
            x_ratio = (item.pos().x() - sheet_rect.left()) / sheet_rect.width()
            y_ratio = (item.pos().y() - sheet_rect.top()) / sheet_rect.height()
            positions.append((item, sheet_map[sheet_index], x_ratio, y_ratio))

        for item, sheet_index, x_ratio, y_ratio in positions:
            sheet_rect = self._sheet_rect_for_index(sheet_index)
            item.setPos(
                QPointF(
                    sheet_rect.left() + sheet_rect.width() * x_ratio,
                    sheet_rect.top() + sheet_rect.height() * y_ratio,
                )
            )

    def _item_logical_rect(self, item: QGraphicsPixmapItem) -> QRectF:
        width = float(item.data(3) or 0) * POINTS_PER_INCH
        height = float(item.data(4) or 0) * POINTS_PER_INCH
        if width <= 0 or height <= 0:
            return item.sceneBoundingRect()

        center = item.pos()
        return QRectF(center.x() - width / 2, center.y() - height / 2, width, height)

    def _item_footprint_rect(self, item: QGraphicsPixmapItem) -> QRectF:
        return item.mapToScene(_item_local_pixmap_rect(item)).boundingRect()

    def _item_cut_bounds(self, item: QGraphicsPixmapItem) -> QRectF:
        return self._item_footprint_rect(item)

    def _sheet_index_for_item(self, item: QGraphicsPixmapItem) -> int | None:
        for sheet_index in range(self._sheet_count()):
            if self._sheet_rect_for_index(sheet_index).contains(item.pos()):
                return sheet_index

        item_rect = self._item_cut_bounds(item)
        for sheet_index in range(self._sheet_count()):
            if _rects_overlap_with_area(
                item_rect, self._sheet_rect_for_index(sheet_index)
            ):
                return sheet_index

        return None

    def _place_item_on_sheet(
        self,
        item: QGraphicsPixmapItem,
        safe_area_top_left: QPointF,
        x_in: float,
        y_in: float,
        width_in: float,
        height_in: float,
    ) -> None:
        source_width = float(item.data(10) or item.data(3) or 0)
        source_height = float(item.data(11) or item.data(4) or 0)
        needs_rotation = _same_size(source_width, height_in) and _same_size(
            source_height, width_in
        )
        rotation = 90 if needs_rotation else 0
        geometry_width = source_width if source_width > 0 else width_in
        geometry_height = source_height if source_height > 0 else height_in
        if not _apply_item_geometry(item, geometry_width, geometry_height, rotation):
            return

        center = QPointF(
            safe_area_top_left.x() + (x_in + width_in / 2) * POINTS_PER_INCH,
            safe_area_top_left.y() + (y_in + height_in / 2) * POINTS_PER_INCH,
        )
        item.setPos(center)
        item.setData(3, geometry_width)
        item.setData(4, geometry_height)

    def _ensure_sheet_count(self, count: int) -> None:
        while len(self._extra_sheet_items) < max(0, count - 1):
            sheet_index = len(self._extra_sheet_items) + 1
            self._extra_sheet_items.append(self._create_extra_sheet(sheet_index))
        self._refresh_sheet_markers()

    def _set_sheet_count(self, count: int) -> None:
        count = max(1, count)
        while len(self._extra_sheet_items) > max(0, count - 1):
            sheet_items = self._extra_sheet_items.pop()
            for item in sheet_items:
                self._scene.removeItem(item)

        self._ensure_sheet_count(count)

    def _create_extra_sheet(
        self, sheet_index: int
    ) -> tuple[QGraphicsRectItem, QGraphicsRectItem, QGraphicsRectItem]:
        rect = self._sheet_rect_for_index(sheet_index)
        shadow_offset = 12
        shadow = QGraphicsRectItem(rect.translated(shadow_offset, shadow_offset))
        shadow.setBrush(QColor(0, 0, 0, 38))
        shadow.setPen(Qt.PenStyle.NoPen)
        shadow.setZValue(-20)

        sheet = QGraphicsRectItem(rect)
        sheet.setBrush(QColor("#ffffff"))
        sheet.setPen(QPen(QColor("#9aa0a6"), 1.25))
        sheet.setZValue(-10)

        margin = QGraphicsRectItem(self._margin_rect_for_sheet_rect(rect))
        margin_pen = QPen(QColor("#d45545"), 0.9, Qt.PenStyle.DashLine)
        margin_pen.setCosmetic(True)
        margin.setBrush(Qt.BrushStyle.NoBrush)
        margin.setPen(margin_pen)
        margin.setZValue(-5)

        self._scene.addItem(shadow)
        self._scene.addItem(sheet)
        self._scene.addItem(margin)
        return shadow, sheet, margin

    def _refresh_extra_sheet_rects(self) -> None:
        shadow_offset = 12
        for index, (shadow, sheet, margin) in enumerate(
            self._extra_sheet_items, start=1
        ):
            rect = self._sheet_rect_for_index(index)
            shadow.setRect(rect.translated(shadow_offset, shadow_offset))
            sheet.setRect(rect)
            margin_rect = self._margin_rect_for_sheet_rect(rect)
            margin.setRect(margin_rect)
            margin.setVisible(not margin_rect.isEmpty())

    def _sheet_rect_for_index(self, sheet_index: int) -> QRectF:
        column = sheet_index % AUTO_NEST_SHEET_COLUMNS
        row = sheet_index // AUTO_NEST_SHEET_COLUMNS
        offset_x = (
            column * (self._sheet.width_in + AUTO_NEST_SHEET_GAP_IN) * POINTS_PER_INCH
        )
        offset_y = (
            row * (self._sheet.height_in + AUTO_NEST_SHEET_GAP_IN) * POINTS_PER_INCH
        )
        return self._sheet.rect_points.translated(offset_x, offset_y)

    def _sheet_safe_area_top_left(self, sheet_index: int) -> QPointF:
        rect = self._sheet_rect_for_index(sheet_index)
        return QPointF(
            rect.left() + self._margin_in * POINTS_PER_INCH,
            rect.top() + self._margin_in * POINTS_PER_INCH,
        )

    def _margin_rect_for_sheet_rect(self, rect: QRectF) -> QRectF:
        inset = self._margin_in * POINTS_PER_INCH
        margin_rect = rect.adjusted(inset, inset, -inset, -inset)
        if margin_rect.width() <= 0 or margin_rect.height() <= 0:
            return QRectF()
        return margin_rect

    def _print_area_rects(self) -> list[QRectF]:
        rects: list[QRectF] = []
        sheet_count = 1 + len(self._extra_sheet_items)
        for sheet_index in range(sheet_count):
            margin_rect = self._margin_rect_for_sheet_rect(
                self._sheet_rect_for_index(sheet_index)
            )
            if not margin_rect.isEmpty():
                rects.append(margin_rect)
        return rects

    def _clear_ready_highlights(self) -> None:
        for item in self._ready_highlight_items:
            self._scene.removeItem(item)
        self._ready_highlight_items.clear()

    def _add_ready_highlight(self, rect: QRectF, pen: QPen, brush: QColor) -> None:
        highlight = QGraphicsRectItem(rect)
        highlight.setBrush(brush)
        highlight.setPen(pen)
        highlight.setZValue(1000)
        highlight.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self._scene.addItem(highlight)
        self._ready_highlight_items.append(highlight)

    def _refresh_sheet_markers(self) -> None:
        self._clear_sheet_markers()

        marker_diameter_points = (MARKER_DIAMETER_MM / MM_PER_INCH) * POINTS_PER_INCH
        marker_radius_points = marker_diameter_points / 2
        pen = QPen(QColor("#000000"), 0)
        pen.setCosmetic(True)
        brush = QColor("#000000")

        for sheet_index in range(self._sheet_count()):
            for center in self._sheet_marker_positions(sheet_index):
                marker_rect = QRectF(
                    center.x() - marker_radius_points,
                    center.y() - marker_radius_points,
                    marker_diameter_points,
                    marker_diameter_points,
                )
                item = QGraphicsEllipseItem(marker_rect)
                item.setBrush(brush)
                item.setPen(pen)
                item.setZValue(-4)
                item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
                self._scene.addItem(item)
                self._marker_items.append(item)

    def _sheet_marker_positions(self, sheet_index: int) -> list[QPointF]:
        bounds = self._sheet_content_bounds(sheet_index)
        if bounds is None:
            return []

        sheet_rect = self._sheet_rect_for_index(sheet_index)
        marker_diameter_points = (MARKER_DIAMETER_MM / MM_PER_INCH) * POINTS_PER_INCH
        marker_radius_points = marker_diameter_points / 2
        clamped_sheet = sheet_rect.adjusted(
            marker_radius_points,
            marker_radius_points,
            -marker_radius_points,
            -marker_radius_points,
        )
        if clamped_sheet.width() <= 0 or clamped_sheet.height() <= 0:
            return []

        padded_bounds = bounds.adjusted(
            -ITEM_MARKER_GAP_IN * POINTS_PER_INCH,
            -ITEM_MARKER_GAP_IN * POINTS_PER_INCH,
            ITEM_MARKER_GAP_IN * POINTS_PER_INCH,
            ITEM_MARKER_GAP_IN * POINTS_PER_INCH,
        ).intersected(clamped_sheet)
        if padded_bounds.width() <= 0 or padded_bounds.height() <= 0:
            return []

        markers = sheet_marker_layout(
            padded_bounds.width() / POINTS_PER_INCH,
            padded_bounds.height() / POINTS_PER_INCH,
            0,
        )
        return [
            QPointF(
                padded_bounds.left() + marker.center_x_in * POINTS_PER_INCH,
                padded_bounds.top() + marker.center_y_in * POINTS_PER_INCH,
            )
            for marker in markers
        ]

    def _sheet_content_bounds(self, sheet_index: int) -> QRectF | None:
        sheet_rect = self._sheet_rect_for_index(sheet_index)
        bounds: QRectF | None = None
        for item in self._image_items():
            item_rect = self._item_cut_bounds(item)
            if not _rects_overlap_with_area(item_rect, sheet_rect):
                continue

            clipped_rect = item_rect.intersected(sheet_rect)
            bounds = (
                QRectF(clipped_rect) if bounds is None else bounds.united(clipped_rect)
            )

        return bounds

    def _clear_sheet_markers(self) -> None:
        for item in self._marker_items:
            self._scene.removeItem(item)
        self._marker_items.clear()

    def _clear_cut_paths(self) -> None:
        for item in self._cut_path_items:
            self._scene.removeItem(item)
        self._cut_path_items.clear()

    def _clear_selection_highlights(self) -> None:
        for item in self._selection_highlight_items:
            self._scene.removeItem(item)
        self._selection_highlight_items.clear()

    def _import_artwork_batch(
        self, paths: list[Path], scene_position: QPointF, progress
    ) -> int:  # type: ignore[no-untyped-def]
        spacing = 24
        added = 0

        progress.setValue(0)
        QApplication.processEvents()

        for index, path in enumerate(paths):
            if progress.wasCanceled():
                break

            progress.setLabelText(f"Importing {path.name}")
            QApplication.processEvents()

            offset_position = scene_position + QPointF(index * spacing, index * spacing)
            if self.add_artwork_file(path, offset_position) is not None:
                added += 1

            progress.setValue(index + 1)
            QApplication.processEvents()

        if not progress.wasCanceled():
            progress.setValue(len(paths))
        return added

    def _make_import_progress_dialog(self, total: int) -> QProgressDialog:
        progress = QProgressDialog("Importing artwork", "Cancel", 0, total, self)
        progress.setWindowTitle("Import")
        progress.setMinimumDuration(0)
        progress.setAutoClose(True)
        progress.setAutoReset(True)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        return progress

    def _has_supported_image(self, mime_data) -> bool:  # type: ignore[no-untyped-def]
        return bool(self._supported_artwork_paths(mime_data))

    def _supported_image_paths(self, mime_data) -> list[Path]:  # type: ignore[no-untyped-def]
        return self._supported_artwork_paths(mime_data)

    def _supported_artwork_paths(self, mime_data) -> list[Path]:  # type: ignore[no-untyped-def]
        if not mime_data.hasUrls():
            return []

        paths: list[Path] = []
        for url in mime_data.urls():
            if not url.isLocalFile():
                continue

            path = Path(url.toLocalFile())
            if path.suffix.lower() in SUPPORTED_ARTWORK_EXTENSIONS and path.is_file():
                paths.append(path)

        return paths


def _item_rotation(item: QGraphicsPixmapItem) -> float:
    value = item.data(ITEM_ROTATION_DATA_KEY)
    if value is None:
        return float(item.rotation())
    return float(value)


def _item_local_pixmap_rect(item: QGraphicsPixmapItem) -> QRectF:
    pixmap = item.pixmap()
    offset = item.offset()
    return QRectF(offset.x(), offset.y(), pixmap.width(), pixmap.height())


def _apply_item_geometry(
    item: QGraphicsPixmapItem, width_in: float, height_in: float, rotation: float
) -> bool:
    pixmap = item.pixmap()
    if pixmap.isNull():
        return False

    item.setRotation(0)
    item.setTransform(
        _item_geometry_transform(
            pixmap.width(), pixmap.height(), width_in, height_in, rotation
        )
    )
    item.setData(3, width_in)
    item.setData(4, height_in)
    item.setData(ITEM_ROTATION_DATA_KEY, rotation)
    return True


def _item_geometry_transform(
    pixel_width: int,
    pixel_height: int,
    width_in: float,
    height_in: float,
    rotation: float,
) -> QTransform:
    x_scale = (width_in * POINTS_PER_INCH) / max(1, pixel_width)
    y_scale = (height_in * POINTS_PER_INCH) / max(1, pixel_height)
    transform = QTransform()
    transform.rotate(rotation)
    transform.scale(x_scale, y_scale)
    return transform


def _is_orthogonal_rotation(rotation: float) -> bool:
    normalized = rotation % 360
    nearest = round(normalized / 90) * 90
    return abs(normalized - nearest) <= 0.01


def _same_size(first: float, second: float) -> bool:
    return abs(first - second) <= 0.01


def _united_item_bounds(rects: list[QRectF]) -> QRectF | None:
    bounds: QRectF | None = None
    for rect in rects:
        if rect.isEmpty():
            continue
        bounds = QRectF(rect) if bounds is None else bounds.united(rect)

    return bounds


def _is_meaningful_delta(delta: QPointF) -> bool:
    return abs(delta.x()) > 0.001 or abs(delta.y()) > 0.001


def _contains_rect_with_tolerance(container: QRectF, rect: QRectF) -> bool:
    tolerance = 1.0
    return (
        rect.left() >= container.left() - tolerance
        and rect.top() >= container.top() - tolerance
        and rect.right() <= container.right() + tolerance
        and rect.bottom() <= container.bottom() + tolerance
    )


def _rects_overlap_with_area(first: QRectF, second: QRectF) -> bool:
    tolerance = 1.0
    intersection = first.intersected(second)
    return intersection.width() > tolerance and intersection.height() > tolerance


def _rects_touch_or_overlap(first: QRectF, second: QRectF) -> bool:
    tolerance = 0.01
    return not (
        first.right() < second.left() - tolerance
        or first.left() > second.right() + tolerance
        or first.bottom() < second.top() - tolerance
        or first.top() > second.bottom() + tolerance
    )
