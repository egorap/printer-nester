from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QDockWidget, QFrame, QGridLayout, QLabel, QMainWindow, QStatusBar, QToolBar, QWidget

from printer_nester.core.pdf_export import ExportKind, export_sheet_pdf
from printer_nester.ui.artboard_panel import ArtboardPanel, SheetPanelRow
from printer_nester.ui.item_panel import ItemPanel
from printer_nester.ui.preferences import load_default_artboard
from printer_nester.ui.ruler import RulerWidget
from printer_nester.ui.viewport import PrintViewport, SheetPreset


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("Printer Nester")
        self.resize(QSize(1100, 760))

        artboard = load_default_artboard()
        sheet = SheetPreset(name=f"{artboard.width_in:g} x {artboard.height_in:g} in", width_in=artboard.width_in, height_in=artboard.height_in)
        self.viewport = PrintViewport(sheet=sheet, margin_in=artboard.margin_in)
        self.setCentralWidget(self._build_canvas_area())

        self.item_panel = ItemPanel()
        self.item_panel.image_selected.connect(self.viewport.select_image_item)
        self.item_panel.image_rounding_changed.connect(self.viewport.set_item_rounding)

        item_dock = QDockWidget("Items", self)
        item_dock.setObjectName("itemsDock")
        item_dock.setWidget(self.item_panel)
        item_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, item_dock)

        self.artboard_panel = ArtboardPanel(artboard)
        self.artboard_panel.artboard_changed.connect(self.viewport.set_artboard)
        self.artboard_panel.export_sheet_requested.connect(self._export_sheet)
        self.artboard_panel.export_all_requested.connect(self._export_all_pending)
        self._exported_sheets: set[tuple[int, ExportKind]] = set()

        artboard_dock = QDockWidget("Artboard", self)
        artboard_dock.setObjectName("artboardDock")
        artboard_dock.setWidget(self.artboard_panel)
        artboard_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, artboard_dock)

        self.zoom_label = QLabel("100%")
        self.zoom_label.setMinimumWidth(56)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.viewport.zoom_changed.connect(self._set_zoom_label)
        self.viewport.image_added.connect(self.item_panel.add_image_item)
        self.viewport.image_added.connect(self._show_image_added)
        self.viewport.image_removed.connect(self.item_panel.remove_image_item)
        self.viewport.sheet_layout_changed.connect(self._mark_sheet_exports_stale)

        toolbar = QToolBar("Viewport", self)
        toolbar.setMovable(False)
        toolbar.addAction("Zoom In", self.viewport.zoom_in).setShortcut("Ctrl++")
        toolbar.addAction("Zoom Out", self.viewport.zoom_out).setShortcut("Ctrl+-")
        toolbar.addAction("Fit Sheet", self.viewport.fit_sheet).setShortcut("Ctrl+0")
        toolbar.addAction("Actual Size", self.viewport.actual_size).setShortcut("Ctrl+1")
        toolbar.addSeparator()
        toolbar.addAction("Auto Nest", self._auto_nest_predefined)
        toolbar.addAction("Grid Nest", self._grid_nest)
        toolbar.addAction("Fill Nest", self._fill_space_nest)
        ready_action = toolbar.addAction("Highlight Ready")
        ready_action.setCheckable(True)
        ready_action.toggled.connect(self.viewport.set_ready_highlight_enabled)
        self.addToolBar(toolbar)

        status = QStatusBar(self)
        status.addPermanentWidget(self.zoom_label)
        status.showMessage("Space + drag or middle-drag to pan. Wheel or Ctrl +/- to zoom.")
        self.setStatusBar(status)

        self._refresh_sheet_panel()
        self.viewport.fit_sheet()

    def _set_zoom_label(self, value: int) -> None:
        self.zoom_label.setText(f"{value}%")

    def _show_image_added(self, _item, path: str) -> None:  # type: ignore[no-untyped-def]
        self.statusBar().showMessage(f"Added image: {path}", 3500)

    def _auto_nest_predefined(self) -> None:
        placed, unmatched = self.viewport.auto_nest_predefined()
        self._refresh_sheet_panel()
        self.statusBar().showMessage(f"Auto nest placed {placed} item(s); ignored {unmatched} unmatched item(s).", 5000)

    def _grid_nest(self) -> None:
        placed = self.viewport.grid_nest()
        self._refresh_sheet_panel()
        self.statusBar().showMessage(f"Grid nest placed {placed} item(s).", 5000)

    def _fill_space_nest(self) -> None:
        placed = self.viewport.fill_space_nest()
        self._refresh_sheet_panel()
        self.statusBar().showMessage(f"Fill nest placed {placed} item(s).", 5000)

    def _export_sheet(self, sheet_index: int, kind_name: str) -> None:
        kind = ExportKind(kind_name)
        try:
            result = export_sheet_pdf(self.viewport.export_sheet_data(sheet_index), kind, self.artboard_panel.export_settings())
        except Exception as error:
            self.statusBar().showMessage(f"Export failed for sheet {sheet_index + 1} {kind.value}: {error}", 8000)
            return

        self._exported_sheets.add((sheet_index, kind))
        self._refresh_sheet_panel()
        self.statusBar().showMessage(f"Exported {kind.value} sheet {sheet_index + 1}: {result.path}", 7000)

    def _export_all_pending(self) -> None:
        exported = 0
        for sheet_index in self.viewport.exportable_sheet_indices():
            for kind in (ExportKind.PRINT, ExportKind.CUT):
                if (sheet_index, kind) in self._exported_sheets:
                    continue
                try:
                    result = export_sheet_pdf(self.viewport.export_sheet_data(sheet_index), kind, self.artboard_panel.export_settings())
                except Exception as error:
                    self.statusBar().showMessage(f"Export all stopped on sheet {sheet_index + 1} {kind.value}: {error}", 9000)
                    return
                self._exported_sheets.add((sheet_index, kind))
                exported += 1

        self._refresh_sheet_panel()
        self.statusBar().showMessage(f"Export all complete: {exported} file(s) written.", 7000)

    def _refresh_sheet_panel(self) -> None:
        rows = [
            SheetPanelRow(
                sheet_index=sheet_index,
                item_count=item_count,
                ready=ready,
                print_exported=(sheet_index, ExportKind.PRINT) in self._exported_sheets,
                cut_exported=(sheet_index, ExportKind.CUT) in self._exported_sheets,
                thumbnail=self.viewport.sheet_thumbnail(sheet_index),
            )
            for sheet_index, item_count, ready in self.viewport.sheet_item_summary()
        ]
        self.artboard_panel.set_sheet_rows(rows)

    def _mark_sheet_exports_stale(self) -> None:
        self._exported_sheets.clear()
        self._refresh_sheet_panel()

    def _build_canvas_area(self) -> QWidget:
        canvas = QWidget(self)
        layout = QGridLayout(canvas)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        corner = QFrame(canvas)
        corner.setFixedSize(42, 28)
        corner.setStyleSheet("background: #eceff1; border-right: 1px solid #b6bdc4; border-bottom: 1px solid #b6bdc4;")

        top_ruler = RulerWidget(Qt.Orientation.Horizontal, self.viewport)
        left_ruler = RulerWidget(Qt.Orientation.Vertical, self.viewport)

        layout.addWidget(corner, 0, 0)
        layout.addWidget(top_ruler, 0, 1)
        layout.addWidget(left_ruler, 1, 0)
        layout.addWidget(self.viewport, 1, 1)
        layout.setColumnStretch(1, 1)
        layout.setRowStretch(1, 1)

        return canvas
