from __future__ import annotations

from collections.abc import Callable
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, QSignalBlocker, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QApplication,
    QDockWidget,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QSpinBox,
    QStatusBar,
    QTabWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from printer_nester.core.artwork_import import read_artwork_import_info
from printer_nester.core.hot_folder import (
    PreparedHotFolderPackage,
    complete_hot_folder_package,
    fail_hot_folder_package,
    prepare_hot_folder_packages,
)
from printer_nester.core.label_print import list_printnode_printers, print_order_labels
from printer_nester.core.nest_profile import NestProfileKind
from printer_nester.core.pdf_export import (
    ExportKind,
    export_jig_layout_pdf,
    export_jig_mockup_pdf,
    export_sheet_pdf,
)
from printer_nester.ui.artboard_panel import ArtboardPanel, SheetPanelRow
from printer_nester.ui.item_panel import ItemPanel
from printer_nester.ui.preferences import (
    load_default_artboard,
    load_default_slot_layout,
    load_default_slot_mask_path,
    load_export_counter,
    save_export_counter,
    save_label_printer_id,
)
from printer_nester.ui.ruler import RulerWidget
from printer_nester.ui.viewport import HOT_FOLDER_ORDER_DATA_KEY, PrintViewport, SheetPreset


TOOLBAR_ICON_SIZE = 22
HOT_FOLDER_POLL_INTERVAL_MS = 2_000
HOT_FOLDER_AUTO_IMPORT_MIN_AGE_SECONDS = 1.0


def _toolbar_icon(name: str) -> QIcon:
    pixmap = QPixmap(TOOLBAR_ICON_SIZE, TOOLBAR_ICON_SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    ink = QColor("#1f2933")
    blue = QColor("#2563eb")
    red = QColor("#dc2626")
    amber = QColor("#d97706")
    muted = QColor("#64748b")

    def line(x1: int, y1: int, x2: int, y2: int, color: QColor | None = None) -> None:
        draw_pen = QPen(color or ink, 2)
        draw_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        draw_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(draw_pen)
        painter.drawLine(x1, y1, x2, y2)

    def rect(
        x: int, y: int, width: int, height: int, color: QColor | None = None
    ) -> None:
        painter.setPen(QPen(color or ink, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(x, y, width, height, 2, 2)

    def plus(cx: int = 11, cy: int = 11, color: QColor = blue) -> None:
        line(cx - 4, cy, cx + 4, cy, color)
        line(cx, cy - 4, cx, cy + 4, color)

    def minus(cx: int = 11, cy: int = 11, color: QColor = blue) -> None:
        line(cx - 4, cy, cx + 4, cy, color)

    def magnifier() -> None:
        painter.setPen(QPen(ink, 2))
        painter.drawEllipse(4, 4, 10, 10)
        line(13, 13, 18, 18)

    if name == "zoom-in":
        magnifier()
        plus(9, 9)
    elif name == "zoom-out":
        magnifier()
        minus(9, 9)
    elif name == "fit":
        rect(4, 4, 14, 14)
        line(2, 7, 7, 7, blue)
        line(7, 2, 7, 7, blue)
        line(20, 7, 15, 7, blue)
        line(15, 2, 15, 7, blue)
        line(2, 15, 7, 15, blue)
        line(7, 20, 7, 15, blue)
        line(20, 15, 15, 15, blue)
        line(15, 20, 15, 15, blue)
    elif name == "actual-size":
        rect(5, 4, 12, 15)
        line(8, 8, 12, 6, blue)
        line(12, 6, 12, 16, blue)
    elif name == "auto-nest":
        rect(3, 4, 16, 14, muted)
        rect(5, 6, 5, 4, blue)
        rect(11, 6, 6, 4, blue)
        rect(5, 11, 7, 5, blue)
        rect(13, 11, 4, 5, blue)
    elif name == "grid-nest":
        for x in (4, 10, 16):
            for y in (4, 10, 16):
                rect(x, y, 4, 4, blue)
    elif name == "fill-nest":
        rect(3, 4, 16, 14, muted)
        painter.setBrush(QColor("#bfdbfe"))
        painter.setPen(QPen(blue, 1))
        painter.drawRect(5, 6, 6, 10)
        painter.drawRect(12, 6, 5, 4)
        painter.drawRect(12, 11, 5, 5)
        painter.setBrush(Qt.BrushStyle.NoBrush)
    elif name == "align-left":
        line(5, 4, 5, 18, blue)
        rect(8, 6, 8, 4)
        rect(8, 13, 11, 4)
    elif name == "align-center-x":
        line(11, 3, 11, 19, blue)
        rect(6, 6, 10, 4)
        rect(4, 13, 14, 4)
    elif name == "align-right":
        line(17, 4, 17, 18, blue)
        rect(7, 6, 8, 4)
        rect(4, 13, 11, 4)
    elif name == "align-top":
        line(4, 5, 18, 5, blue)
        rect(6, 8, 4, 8)
        rect(13, 8, 4, 11)
    elif name == "align-center-y":
        line(3, 11, 19, 11, blue)
        rect(6, 6, 4, 10)
        rect(13, 4, 4, 14)
    elif name == "align-bottom":
        line(4, 17, 18, 17, blue)
        rect(6, 7, 4, 8)
        rect(13, 4, 4, 11)
    elif name == "close-h-gap":
        rect(3, 7, 5, 8)
        rect(14, 7, 5, 8)
        line(9, 11, 13, 11, amber)
        line(9, 11, 11, 9, amber)
        line(9, 11, 11, 13, amber)
        line(13, 11, 11, 9, amber)
        line(13, 11, 11, 13, amber)
    elif name == "close-v-gap":
        rect(7, 3, 8, 5)
        rect(7, 14, 8, 5)
        line(11, 9, 11, 13, amber)
        line(11, 9, 9, 11, amber)
        line(11, 9, 13, 11, amber)
        line(11, 13, 9, 11, amber)
        line(11, 13, 13, 11, amber)
    elif name == "highlight-ready":
        painter.setPen(QPen(red, 2))
        painter.setBrush(QColor(220, 38, 38, 42))
        painter.drawRoundedRect(4, 5, 14, 12, 2, 2)
        line(7, 11, 10, 14, red)
        line(10, 14, 16, 8, red)
    elif name == "jig-mask":
        painter.setPen(QPen(ink, 2))
        painter.setBrush(QColor("#ffffff"))
        painter.drawRoundedRect(4, 5, 14, 12, 2, 2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#111827"))
        painter.drawRect(5, 6, 7, 10)
        painter.setPen(QPen(blue, 2))
        painter.drawRoundedRect(4, 5, 14, 12, 2, 2)
    elif name == "white-layer":
        painter.setPen(QPen(blue, 2))
        painter.setBrush(QColor(224, 247, 255))
        painter.drawRoundedRect(4, 5, 14, 12, 2, 2)
        painter.setPen(QPen(ink, 2))
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(7, 8, 8, 8)
    else:
        rect(4, 4, 14, 14)

    painter.end()
    return QIcon(pixmap)


def _profile_kind_icon(kind: NestProfileKind) -> QIcon:
    pixmap = QPixmap(128, 82)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor("#334155"), 2))
    painter.setBrush(QColor("#ffffff"))
    painter.drawRoundedRect(15, 9, 98, 64, 4, 4)

    if kind == NestProfileKind.SHEET:
        painter.setPen(QPen(QColor("#94a3b8"), 1))
        painter.drawRect(25, 18, 78, 46)
        painter.setPen(QPen(QColor("#2563eb"), 2))
        painter.setBrush(QColor("#dbeafe"))
        painter.drawRoundedRect(32, 25, 24, 16, 2, 2)
        painter.drawRoundedRect(62, 25, 30, 16, 2, 2)
        painter.drawRoundedRect(32, 46, 39, 12, 2, 2)
    else:
        painter.setPen(QPen(QColor("#64748b"), 1))
        painter.drawRect(25, 18, 78, 46)
        painter.setPen(QPen(QColor("#2563eb"), 2))
        for row in range(2):
            for column in range(3):
                painter.drawRoundedRect(31 + column * 22, 24 + row * 20, 15, 12, 2, 2)
        painter.setPen(QPen(QColor("#d97706"), 1, Qt.PenStyle.DashLine))
        for row in range(2):
            for column in range(3):
                painter.drawRoundedRect(34 + column * 22, 27 + row * 20, 9, 6, 1, 1)

    painter.end()
    return QIcon(pixmap)


class ProfileKindDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New Profile")
        self.setModal(True)
        self.setMinimumWidth(360)
        self._selected_kind: NestProfileKind | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        title = QLabel("Choose profile type", self)
        title.setObjectName("profileKindTitle")
        layout.addWidget(title)

        options = QHBoxLayout()
        options.setSpacing(12)
        options.addWidget(self._option_button(NestProfileKind.SHEET, "Sheet"))
        options.addWidget(self._option_button(NestProfileKind.JIG, "Jig"))
        layout.addLayout(options)

        self.setStyleSheet(
            """
            QDialog {
                background: #f8fafc;
            }
            QLabel#profileKindTitle {
                color: #0f172a;
                font-size: 14px;
                font-weight: 600;
            }
            QToolButton#profileKindOption {
                background: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 7px;
                color: #0f172a;
                font-weight: 600;
                min-width: 142px;
                padding: 12px;
            }
            QToolButton#profileKindOption:hover {
                background: #f1f5f9;
                border-color: #94a3b8;
            }
            QToolButton#profileKindOption:pressed {
                background: #dbeafe;
                border-color: #93c5fd;
            }
            """
        )

    def selected_kind(self) -> NestProfileKind | None:
        return self._selected_kind

    def _option_button(self, kind: NestProfileKind, text: str) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("profileKindOption")
        button.setText(text)
        button.setIcon(_profile_kind_icon(kind))
        button.setIconSize(QSize(128, 82))
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        button.clicked.connect(lambda checked=False, kind=kind: self._accept_kind(kind))
        return button

    def _accept_kind(self, kind: NestProfileKind) -> None:
        self._selected_kind = kind
        self.accept()


@dataclass(slots=True)
class ProfileTab:
    kind: NestProfileKind
    title: str
    viewport: PrintViewport
    widget: QWidget
    exported_sheets: set[tuple[int, ExportKind]]
    labels_printed_sheets: set[int]
    export_numbers: dict[int, int]


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("Printer Nester")
        self.resize(QSize(1100, 760))

        slot_layout = load_default_slot_layout()
        self._profiles: list[ProfileTab] = []
        self._sheet_profile_count = 0
        self._jig_profile_count = 0
        self._profile_add_button: QToolButton | None = None
        self._hot_folder_processing = False
        self._importing_viewports: set[PrintViewport] = set()
        self.viewport: PrintViewport

        artboard = load_default_artboard()

        self.item_panel = ItemPanel()
        self.item_panel.image_selected.connect(self._select_image_item)
        self.item_panel.image_rounding_changed.connect(self._set_item_rounding)

        item_dock = QDockWidget("Items", self)
        item_dock.setObjectName("itemsDock")
        item_dock.setWidget(self.item_panel)
        item_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, item_dock)

        self.artboard_panel = ArtboardPanel(artboard, slot_layout)
        self.artboard_panel.set_jig_mask_path(load_default_slot_mask_path())
        self.artboard_panel.artboard_changed.connect(self._set_active_artboard)
        self.artboard_panel.slot_layout_changed.connect(self._set_active_slot_layout)
        self.artboard_panel.jig_mask_changed.connect(self._set_active_jig_mask)
        self.artboard_panel.item_transform_changed.connect(
            self._set_active_item_transform
        )
        self.artboard_panel.export_sheet_requested.connect(self._export_sheet)
        self.artboard_panel.export_all_requested.connect(self._export_all_pending)
        self.artboard_panel.export_jig_mockup_requested.connect(
            self._export_jig_mockup
        )
        self.artboard_panel.process_hot_folder_requested.connect(
            self._process_hot_folder
        )
        self.artboard_panel.refresh_label_printers_requested.connect(
            self._refresh_label_printers
        )
        self.artboard_panel.print_labels_requested.connect(
            self._print_jig_sheet_labels
        )
        self.artboard_panel.copy_order_numbers_requested.connect(
            self._copy_jig_sheet_order_numbers
        )
        artboard_dock = QDockWidget("Artboard", self)
        artboard_dock.setObjectName("artboardDock")
        artboard_dock.setWidget(self.artboard_panel)
        artboard_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, artboard_dock)

        self._tab_widget = self._build_profile_tabs()
        self.setCentralWidget(self._tab_widget)

        self.zoom_label = QLabel("100%")
        self.zoom_label.setMinimumWidth(56)
        self.zoom_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        self._toolbar = self._build_toolbar()
        self.addToolBar(self._toolbar)

        status = QStatusBar(self)
        status.addPermanentWidget(self.zoom_label)
        status.showMessage(
            "Space + drag or middle-drag to pan. Wheel or Ctrl +/- to zoom."
        )
        self.setStatusBar(status)

        self._add_profile(NestProfileKind.SHEET, switch=True, artboard=artboard)
        self.viewport.fit_sheet()

        self._hot_folder_timer = QTimer(self)
        self._hot_folder_timer.setInterval(HOT_FOLDER_POLL_INTERVAL_MS)
        self._hot_folder_timer.timeout.connect(self._poll_hot_folder)
        self._hot_folder_timer.start()

    def _build_profile_tabs(self) -> QTabWidget:
        tabs = QTabWidget(self)
        tabs.setObjectName("profileTabs")
        tabs.setDocumentMode(True)
        tabs.setMovable(True)
        tabs.setTabsClosable(True)
        tabs.tabCloseRequested.connect(self._close_profile_tab)
        tabs.currentChanged.connect(self._handle_profile_tab_changed)
        tabs.tabBar().setExpanding(False)
        tabs.setStyleSheet(
            """
            QTabWidget#profileTabs::pane {
                border: 0;
                border-top: 1px solid #cbd5e1;
                background: #ffffff;
            }
            QTabWidget#profileTabs > QTabBar {
                background: #f8fafc;
            }
            QTabBar::tab {
                background: transparent;
                border: 1px solid transparent;
                border-radius: 5px;
                color: #475569;
                margin: 4px 2px 4px 2px;
                min-width: 104px;
                padding: 6px 12px;
            }
            QTabBar::tab:selected {
                background: #e2e8f0;
                border-color: #cbd5e1;
                color: #0f172a;
            }
            QTabBar::tab:!selected:hover {
                background: #edf2f7;
                color: #1e293b;
            }
            QTabBar::tab:only-one {
                padding-right: 14px;
            }
            QTabBar::close-button {
                margin-left: 7px;
                subcontrol-position: right;
            }
            QToolButton#profileAddButton {
                background: transparent;
                border: 1px solid transparent;
                border-radius: 5px;
                color: #334155;
                font-weight: 600;
                margin: 4px 8px 4px 4px;
                min-width: 28px;
                padding: 5px 8px;
            }
            QToolButton#profileAddButton:hover {
                background: #e2e8f0;
                border-color: #cbd5e1;
            }
            QToolButton#profileAddButton:pressed {
                background: #dbeafe;
                border-color: #93c5fd;
            }
            """
        )

        add_button = QToolButton(tabs.tabBar())
        add_button.setObjectName("profileAddButton")
        add_button.setText("+")
        add_button.setToolTip("Add a profile")
        add_button.setAutoRaise(True)
        add_button.clicked.connect(self._show_new_profile_dialog)
        self._profile_add_button = add_button
        tabs.tabBar().installEventFilter(self)
        return tabs

    def eventFilter(self, watched, event) -> bool:  # type: ignore[no-untyped-def]
        if watched is self._tab_widget.tabBar() and event.type() in {
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.LayoutRequest,
            QEvent.Type.Move,
        }:
            self._position_profile_add_button()
        return super().eventFilter(watched, event)

    def _show_new_profile_dialog(self) -> None:
        dialog = ProfileKindDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        kind = dialog.selected_kind()
        if kind is not None:
            self._add_profile(kind, switch=True)

    def _position_profile_add_button(self) -> None:
        if self._profile_add_button is None:
            return
        tab_bar = self._tab_widget.tabBar()
        button = self._profile_add_button
        button_size = button.sizeHint()
        button.resize(button_size)

        if tab_bar.count() > 0:
            last_tab = tab_bar.tabRect(tab_bar.count() - 1)
            x = last_tab.right() + 6
        else:
            x = 4
        x = min(x, max(4, tab_bar.width() - button.width() - 4))
        y = max(0, (tab_bar.height() - button.height()) // 2)
        button.move(x, y)
        button.raise_()

    def _build_toolbar(self) -> QToolBar:
        toolbar = QToolBar("Viewport", self)
        toolbar.setObjectName("mainToolbar")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(TOOLBAR_ICON_SIZE, TOOLBAR_ICON_SIZE))
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        toolbar.setStyleSheet(
            """
            QToolBar#mainToolbar {
                background: #f8fafc;
                border: 0;
                border-bottom: 1px solid #cbd5e1;
                spacing: 3px;
                padding: 4px 6px;
            }
            QToolBar#mainToolbar QToolButton {
                border: 1px solid transparent;
                border-radius: 5px;
                padding: 4px;
                min-width: 28px;
                min-height: 28px;
            }
            QToolBar#mainToolbar QToolButton:hover {
                background: #e2e8f0;
                border-color: #cbd5e1;
            }
            QToolBar#mainToolbar QToolButton:pressed,
            QToolBar#mainToolbar QToolButton:checked {
                background: #dbeafe;
                border-color: #93c5fd;
            }
            QToolBar#mainToolbar QToolButton:disabled {
                background: transparent;
                border-color: transparent;
            }
            """
        )

        self._add_toolbar_action(
            toolbar, "zoom-in", "Zoom In", self._zoom_in, shortcut="Ctrl++"
        )
        self._add_toolbar_action(
            toolbar, "zoom-out", "Zoom Out", self._zoom_out, shortcut="Ctrl+-"
        )
        self._add_toolbar_action(
            toolbar, "fit", "Fit Sheet", self._fit_sheet, shortcut="Ctrl+0"
        )
        self._add_toolbar_action(
            toolbar,
            "actual-size",
            "Actual Size",
            self._actual_size,
            shortcut="Ctrl+1",
        )
        toolbar.addSeparator()
        self._auto_nest_action = self._add_toolbar_action(
            toolbar,
            "auto-nest",
            "Auto Nest",
            self._auto_nest_predefined,
            show_text=True,
        )
        self._grid_nest_action = self._add_toolbar_action(
            toolbar, "grid-nest", "Grid Nest", self._grid_nest, show_text=True
        )
        self._fill_nest_action = self._add_toolbar_action(
            toolbar, "fill-nest", "Fill Nest", self._fill_space_nest, show_text=True
        )
        self._sheet_only_actions = [
            self._auto_nest_action,
            self._grid_nest_action,
            self._fill_nest_action,
        ]
        toolbar.addSeparator()
        self._add_toolbar_action(
            toolbar,
            "align-left",
            "Align Left",
            lambda: self._align_selected_items("left", "left edges"),
        )
        self._add_toolbar_action(
            toolbar,
            "align-center-x",
            "Align Horizontal Center",
            lambda: self._align_selected_items("center_x", "horizontal centers"),
        )
        self._add_toolbar_action(
            toolbar,
            "align-right",
            "Align Right",
            lambda: self._align_selected_items("right", "right edges"),
        )
        self._add_toolbar_action(
            toolbar,
            "align-top",
            "Align Top",
            lambda: self._align_selected_items("top", "top edges"),
        )
        self._add_toolbar_action(
            toolbar,
            "align-center-y",
            "Align Vertical Center",
            lambda: self._align_selected_items("center_y", "vertical centers"),
        )
        self._add_toolbar_action(
            toolbar,
            "align-bottom",
            "Align Bottom",
            lambda: self._align_selected_items("bottom", "bottom edges"),
        )
        toolbar.addSeparator()
        self._add_toolbar_action(
            toolbar,
            "close-h-gap",
            "Close Horizontal Gap",
            lambda: self._close_selected_item_gaps("horizontal"),
        )
        self._add_toolbar_action(
            toolbar,
            "close-v-gap",
            "Close Vertical Gap",
            lambda: self._close_selected_item_gaps("vertical"),
        )
        toolbar.addSeparator()
        self._ready_action = self._add_toolbar_action(
            toolbar, "highlight-ready", "Highlight Ready Items", checkable=True
        )
        self._ready_action.toggled.connect(self._set_ready_highlight_enabled)
        self._mask_action = self._add_toolbar_action(
            toolbar, "jig-mask", "Show Jig Mask", checkable=True
        )
        self._mask_action.toggled.connect(self._set_jig_mask_preview_enabled)
        self._white_layer_action = self._add_toolbar_action(
            toolbar, "white-layer", "Preview White Layer", checkable=True
        )
        self._white_layer_action.toggled.connect(self._set_white_layer_preview_enabled)

        toolbar.addSeparator()
        counter_label = QLabel("Next sheet #")
        counter_label.setToolTip("Number assigned to the next sheet export.")
        toolbar.addWidget(counter_label)
        self._export_counter_input = QSpinBox()
        self._export_counter_input.setRange(1, 999_999_999)
        self._export_counter_input.setValue(load_export_counter())
        self._export_counter_input.setMinimumWidth(86)
        self._export_counter_input.setToolTip(
            "Sets the number used by the next sheet that is exported."
        )
        self._export_counter_input.valueChanged.connect(
            lambda value: save_export_counter(value)
        )
        toolbar.addWidget(self._export_counter_input)
        return toolbar

    def _add_toolbar_action(
        self,
        toolbar: QToolBar,
        icon_name: str,
        text: str,
        callback: Callable[[], None] | None = None,
        *,
        shortcut: str | None = None,
        checkable: bool = False,
        show_text: bool = False,
    ) -> QAction:
        action = toolbar.addAction(_toolbar_icon(icon_name), text)
        if callback is not None:
            action.triggered.connect(callback)
        action.setCheckable(checkable)
        if show_text:
            button = toolbar.widgetForAction(action)
            if isinstance(button, QToolButton):
                button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
                button.setMinimumWidth(92)
        if shortcut is not None:
            action.setShortcut(shortcut)
            action.setToolTip(f"{text} ({shortcut})")
        else:
            action.setToolTip(text)
        action.setStatusTip(text)
        return action

    def _add_profile(
        self,
        kind: NestProfileKind,
        switch: bool = True,
        artboard=None,  # type: ignore[no-untyped-def]
        slot_layout=None,  # type: ignore[no-untyped-def]
        slot_mask_path=None,  # type: ignore[no-untyped-def]
    ) -> ProfileTab:
        if kind == NestProfileKind.SHEET:
            artboard = (
                artboard if artboard is not None else self.artboard_panel.artboard()
            )
            self._sheet_profile_count += 1
            title = f"Sheet {self._sheet_profile_count}"
            sheet = SheetPreset(
                name=f"{artboard.width_in:g} x {artboard.height_in:g} in",
                width_in=artboard.width_in,
                height_in=artboard.height_in,
            )
            viewport = PrintViewport(sheet=sheet, margin_in=artboard.margin_in)
        else:
            slot_layout = (
                slot_layout
                if slot_layout is not None
                else self.artboard_panel.slot_layout()
            )
            self._jig_profile_count += 1
            title = f"Jig {self._jig_profile_count}"
            sheet = SheetPreset(
                name=(
                    f"{slot_layout.total_width_in:g} x "
                    f"{slot_layout.total_height_in:g} in"
                ),
                width_in=slot_layout.total_width_in,
                height_in=slot_layout.total_height_in,
            )
            viewport = PrintViewport(
                sheet=sheet,
                margin_in=slot_layout.outside_margin_in,
                slot_layout=slot_layout,
            )
            if slot_mask_path is None:
                slot_mask_path = self.artboard_panel.jig_mask_path()
            if slot_mask_path is not None:
                viewport.set_slot_mask_path(Path(slot_mask_path))
        viewport.setProperty("profileKind", kind.value)
        widget = self._build_canvas_area(viewport, kind)
        profile = ProfileTab(
            kind=kind,
            title=title,
            viewport=viewport,
            widget=widget,
            exported_sheets=set(),
            labels_printed_sheets=set(),
            export_numbers={},
        )
        self._profiles.append(profile)
        self._connect_profile_viewport(profile)

        index = self._tab_widget.addTab(widget, title)
        self._tab_widget.setTabToolTip(index, f"{kind.value.title()} profile")
        self._position_profile_add_button()
        if switch:
            if self._tab_widget.currentIndex() == index:
                self._activate_profile(profile)
            else:
                self._tab_widget.setCurrentIndex(index)
        return profile

    def _connect_profile_viewport(self, profile: ProfileTab) -> None:
        viewport = profile.viewport
        viewport.zoom_changed.connect(
            lambda value, viewport=viewport: self._handle_viewport_zoom_changed(
                viewport, value
            )
        )
        viewport.image_added.connect(
            lambda item, path, viewport=viewport: self._handle_viewport_image_added(
                viewport, item, path
            )
        )
        viewport.images_removed.connect(
            lambda items, viewport=viewport: self._handle_viewport_images_removed(
                viewport, items
            )
        )
        viewport.artwork_import_started.connect(
            lambda viewport=viewport: self._handle_artwork_import_started(viewport)
        )
        viewport.artwork_import_finished.connect(
            lambda viewport=viewport: self._handle_artwork_import_finished(viewport)
        )
        viewport.image_selection_changed.connect(
            lambda item, viewport=viewport: self._handle_viewport_selection_changed(
                viewport, item
            )
        )
        viewport.image_transform_changed.connect(
            lambda item, viewport=viewport: self._handle_viewport_transform_changed(
                viewport, item
            )
        )
        viewport.sheet_layout_changed.connect(
            lambda viewport=viewport: self._handle_viewport_layout_changed(viewport)
        )
        viewport.jig_sheets_remapped.connect(
            lambda sheet_map, viewport=viewport: self._handle_jig_sheets_remapped(
                viewport, sheet_map
            )
        )

    def _active_profile(self) -> ProfileTab:
        if not self._profiles:
            raise RuntimeError("No profile tabs are open.")
        widget = self._tab_widget.currentWidget()
        if widget is None:
            return self._profiles[0]
        return self._profile_for_widget(widget)

    def _profile_for_widget(self, widget: QWidget) -> ProfileTab:
        for profile in self._profiles:
            if profile.widget is widget:
                return profile
        raise RuntimeError("Profile tab widget is not registered.")

    def _profile_for_viewport(self, viewport: PrintViewport) -> ProfileTab | None:
        for profile in self._profiles:
            if profile.viewport is viewport:
                return profile
        return None

    def _handle_profile_tab_changed(self, index: int) -> None:
        if index < 0 or not self._profiles:
            return
        self._activate_profile(self._profile_for_widget(self._tab_widget.widget(index)))

    def _activate_profile(self, profile: ProfileTab) -> None:
        self.viewport = profile.viewport
        self._refresh_item_panel()
        self.artboard_panel.set_profile_kind(profile.kind)
        if profile.kind == NestProfileKind.SHEET:
            self.artboard_panel.set_artboard(self.viewport.artboard_settings())
        else:
            slot_layout = self.viewport.slot_layout_settings()
            if slot_layout is not None:
                self.artboard_panel.set_slot_layout(slot_layout)
            self.artboard_panel.set_jig_mask_path(self.viewport.slot_mask_path())
        self._set_transform_selection(self.viewport.selected_image_item())
        self._refresh_sheet_panel()
        self._sync_ready_action()
        self._sync_mask_action()
        self._sync_white_layer_action()
        self._sync_profile_actions()
        self._set_zoom_label(round(self.viewport.transform().m11() * 100))
        if profile.kind == NestProfileKind.JIG:
            self.statusBar().showMessage(
                "Jig profile selected. Slot layout controls are active.", 3500
            )

    def _close_profile_tab(self, index: int) -> None:
        if self._tab_widget.count() <= 1:
            self.statusBar().showMessage(
                "At least one profile tab must stay open.", 3500
            )
            return

        widget = self._tab_widget.widget(index)
        profile = self._profile_for_widget(widget)
        self._tab_widget.removeTab(index)
        self._profiles.remove(profile)
        profile.widget.deleteLater()
        self._position_profile_add_button()
        if self._tab_widget.count() > 0:
            self._handle_profile_tab_changed(self._tab_widget.currentIndex())

    def _active_profile_if(
        self, kind: NestProfileKind, message: str, timeout_ms: int = 4000
    ) -> ProfileTab | None:
        profile = self._active_profile()
        if profile.kind == kind:
            return profile
        self.statusBar().showMessage(message, timeout_ms)
        return None

    def _sync_profile_actions(self) -> None:
        is_sheet = self._active_profile().kind == NestProfileKind.SHEET
        for action in self._sheet_only_actions:
            action.setEnabled(is_sheet)

    def _sync_ready_action(self) -> None:
        with QSignalBlocker(self._ready_action):
            self._ready_action.setChecked(self.viewport.ready_highlight_enabled())

    def _sync_mask_action(self) -> None:
        is_jig = self._active_profile().kind == NestProfileKind.JIG
        self._mask_action.setEnabled(is_jig)
        with QSignalBlocker(self._mask_action):
            self._mask_action.setChecked(self.viewport.jig_mask_preview_enabled())

    def _sync_white_layer_action(self) -> None:
        is_jig = self._active_profile().kind == NestProfileKind.JIG
        self._white_layer_action.setEnabled(is_jig)
        with QSignalBlocker(self._white_layer_action):
            self._white_layer_action.setChecked(
                self.viewport.white_layer_preview_enabled()
            )

    def _refresh_item_panel(self) -> None:
        self.item_panel.set_image_items(self.viewport.image_items())
        self.item_panel.select_image_item(self.viewport.selected_image_item())

    def _zoom_in(self) -> None:
        self.viewport.zoom_in()

    def _zoom_out(self) -> None:
        self.viewport.zoom_out()

    def _fit_sheet(self) -> None:
        self.viewport.fit_sheet()

    def _actual_size(self) -> None:
        self.viewport.actual_size()

    def _set_ready_highlight_enabled(self, enabled: bool) -> None:
        self.viewport.set_ready_highlight_enabled(enabled)

    def _set_jig_mask_preview_enabled(self, enabled: bool) -> None:
        if self._active_profile().kind != NestProfileKind.JIG:
            return
        self.viewport.set_jig_mask_preview_enabled(enabled)

    def _set_white_layer_preview_enabled(self, enabled: bool) -> None:
        if self._active_profile().kind != NestProfileKind.JIG:
            return
        self.viewport.set_white_layer_preview_enabled(enabled)

    def _set_zoom_label(self, value: int) -> None:
        self.zoom_label.setText(f"{value}%")

    def _handle_viewport_zoom_changed(
        self, viewport: PrintViewport, value: int
    ) -> None:
        if viewport is self.viewport:
            self._set_zoom_label(value)

    def _handle_viewport_image_added(
        self, viewport: PrintViewport, item, path: str
    ) -> None:  # type: ignore[no-untyped-def]
        if viewport is not self.viewport:
            return
        self.item_panel.add_image_item(
            item,
            path,
            apply_default_rounding=self._active_profile().kind == NestProfileKind.SHEET,
        )
        self._show_image_added(item, path)

    def _handle_artwork_import_started(self, viewport: PrintViewport) -> None:
        self._importing_viewports.add(viewport)

    def _handle_artwork_import_finished(self, viewport: PrintViewport) -> None:
        self._importing_viewports.discard(viewport)
        if viewport is self.viewport:
            self._refresh_sheet_panel()

    def _handle_viewport_images_removed(self, viewport: PrintViewport, items) -> None:  # type: ignore[no-untyped-def]
        if viewport is not self.viewport:
            return
        self.item_panel.remove_image_items(items)

    def _handle_viewport_selection_changed(self, viewport: PrintViewport, item) -> None:  # type: ignore[no-untyped-def]
        if viewport is not self.viewport:
            return
        self.item_panel.select_image_item(item)
        self._set_transform_selection(item)

    def _handle_viewport_transform_changed(self, viewport: PrintViewport, item) -> None:  # type: ignore[no-untyped-def]
        if viewport is not self.viewport:
            return
        self.item_panel.refresh_image_item(item)
        self._refresh_transform_selection(item)

    def _handle_jig_sheets_remapped(
        self, viewport: PrintViewport, sheet_map: dict[int, int]
    ) -> None:
        profile = self._profile_for_viewport(viewport)
        if profile is None:
            return
        profile.exported_sheets = {
            (sheet_map[index], kind) for index, kind in profile.exported_sheets
            if index in sheet_map
        }
        profile.labels_printed_sheets = {
            sheet_map[index] for index in profile.labels_printed_sheets
            if index in sheet_map
        }
        profile.export_numbers = {
            sheet_map[index]: number for index, number in profile.export_numbers.items()
            if index in sheet_map
        }

    def _handle_viewport_layout_changed(self, viewport: PrintViewport) -> None:
        profile = self._profile_for_viewport(viewport)
        if profile is not None and profile.kind == NestProfileKind.SHEET:
            profile.exported_sheets.clear()
            profile.labels_printed_sheets.clear()
            profile.export_numbers.clear()
        if viewport is self.viewport and viewport not in self._importing_viewports:
            self._refresh_sheet_panel()

    def _select_image_item(self, item) -> None:  # type: ignore[no-untyped-def]
        if item is not None and item.scene() is self.viewport.scene():
            self.viewport.select_image_item(item)

    def _set_item_rounding(self, item, enabled: bool) -> None:  # type: ignore[no-untyped-def]
        if item is not None and item.scene() is self.viewport.scene():
            self.viewport.set_item_rounding(item, enabled)

    def _set_active_artboard(self, artboard) -> None:  # type: ignore[no-untyped-def]
        if self._active_profile().kind == NestProfileKind.SHEET:
            self.viewport.set_artboard(artboard)

    def _set_active_slot_layout(self, slot_layout) -> None:  # type: ignore[no-untyped-def]
        if self._active_profile().kind != NestProfileKind.JIG:
            return
        profile = self._active_profile()
        profile.exported_sheets.clear()
        profile.labels_printed_sheets.clear()
        profile.export_numbers.clear()
        self.viewport.clear_jig_sheet_locks()
        self.viewport.set_slot_layout(slot_layout)
        self._refresh_sheet_panel()

    def _set_active_jig_mask(self, path) -> None:  # type: ignore[no-untyped-def]
        if self._active_profile().kind != NestProfileKind.JIG:
            return
        if not self.viewport.set_slot_mask_path(path):
            self.artboard_panel.set_jig_mask_path(self.viewport.slot_mask_path())
            self.statusBar().showMessage("Could not load jig slot mask image.", 5000)
            return
        self._sync_mask_action()
        self._sync_white_layer_action()
        self.statusBar().showMessage(
            "Jig slot mask cleared." if path is None else f"Jig slot mask loaded: {path}",
            5000,
        )

    def _set_active_item_transform(self, item, transform) -> None:  # type: ignore[no-untyped-def]
        if item is not None and item.scene() is self.viewport.scene():
            self.viewport.set_item_transform(item, transform)

    def _show_image_added(self, _item, path: str) -> None:  # type: ignore[no-untyped-def]
        self.statusBar().showMessage(f"Added image: {path}", 3500)

    def _set_transform_selection(self, item) -> None:  # type: ignore[no-untyped-def]
        if item is None:
            self.artboard_panel.set_selected_item_transform(None, None)
            return
        self.artboard_panel.set_selected_item_transform(
            item, self.viewport.item_transform(item)
        )

    def _refresh_transform_selection(self, item) -> None:  # type: ignore[no-untyped-def]
        if self.artboard_panel.selected_transform_item() is item:
            self.artboard_panel.set_selected_item_transform(
                item, self.viewport.item_transform(item)
            )

    def _auto_nest_predefined(self) -> None:
        if self._active_profile_if(
            NestProfileKind.SHEET, "Auto nest is only available for sheet profiles."
        ) is None:
            return
        placed, unmatched = self.viewport.auto_nest_predefined()
        self._refresh_sheet_panel()
        self.statusBar().showMessage(
            f"Auto nest placed {placed} item(s); ignored {unmatched} unmatched item(s).",
            5000,
        )

    def _align_selected_items(self, mode: str, label: str) -> None:
        moved = self.viewport.align_selected_items(mode)
        self._refresh_sheet_panel()
        if moved:
            self.statusBar().showMessage(f"Aligned {moved} item(s) by {label}.", 4000)
        else:
            self.statusBar().showMessage(
                "Select at least two unaligned items to align.", 4000
            )

    def _close_selected_item_gaps(self, axis: str) -> None:
        moved = self.viewport.close_selected_item_gaps(axis)
        self._refresh_sheet_panel()
        label = "horizontal" if axis == "horizontal" else "vertical"
        self.statusBar().showMessage(
            f"Closed {label} gap for {moved} item(s)."
            if moved
            else "Select at least two separated items to close gaps.",
            4000,
        )

    def _grid_nest(self) -> None:
        if self._active_profile_if(
            NestProfileKind.SHEET, "Grid nest is only available for sheet profiles."
        ) is None:
            return
        placed = self.viewport.grid_nest()
        self._refresh_sheet_panel()
        self.statusBar().showMessage(f"Grid nest placed {placed} item(s).", 5000)

    def _fill_space_nest(self) -> None:
        if self._active_profile_if(
            NestProfileKind.SHEET, "Fill nest is only available for sheet profiles."
        ) is None:
            return
        placed = self.viewport.fill_space_nest()
        self._refresh_sheet_panel()
        self.statusBar().showMessage(f"Fill nest placed {placed} item(s).", 5000)

    def _poll_hot_folder(self) -> None:
        self._process_hot_folder(
            show_empty_status=False,
            min_package_age_seconds=HOT_FOLDER_AUTO_IMPORT_MIN_AGE_SECONDS,
        )

    def _process_hot_folder(
        self,
        *,
        show_empty_status: bool = True,
        min_package_age_seconds: float = 0.0,
    ) -> None:
        if self._hot_folder_processing:
            return

        profile = self._active_profile()
        if profile.kind != NestProfileKind.JIG:
            if show_empty_status:
                self.statusBar().showMessage(
                    "Hot folder processing is only available for jig profiles.", 5000
                )
            return

        root = self.artboard_panel.hot_folder_root()
        if root == Path("."):
            if show_empty_status:
                self.statusBar().showMessage(
                    "Configure a hot folder before processing orders.", 6000
                )
            return

        self._hot_folder_processing = True
        try:
            try:
                scan = prepare_hot_folder_packages(
                    root, min_age_seconds=min_package_age_seconds
                )
            except Exception as error:
                if show_empty_status:
                    self.statusBar().showMessage(f"Hot folder scan failed: {error}", 8000)
                return

            imported_orders = 0
            imported_items = 0
            failed_packages = len(scan.errors)
            self.viewport.begin_artwork_import()
            try:
                for package in scan.packages:
                    package_items = []
                    try:
                        package_items = self._import_hot_folder_package(package)
                        completed = complete_hot_folder_package(package, root)
                        self._retarget_hot_folder_items(
                            package_items, package.extract_dir, completed.extract_dir
                        )
                    except Exception as error:
                        if package_items:
                            self.viewport.remove_image_items(package_items)
                        fail_hot_folder_package(package, root, str(error))
                        failed_packages += 1
                        continue

                    imported_orders += 1
                    imported_items += len(package_items)
            finally:
                self.viewport.end_artwork_import()

            if imported_orders == 0 and failed_packages == 0:
                if show_empty_status:
                    self.statusBar().showMessage(
                        "No ready order ZIPs found in the hot folder.", 5000
                    )
                return

            message = (
                f"Hot folder imported {imported_orders} order(s), "
                f"{imported_items} item(s)."
            )
            if failed_packages:
                message += f" {failed_packages} package(s) failed."
            self.statusBar().showMessage(message, 8000)
        finally:
            self._hot_folder_processing = False

    def _import_hot_folder_package(self, package: PreparedHotFolderPackage) -> list[object]:
        order = package.order
        artwork_paths = order.expanded_artwork_paths()
        if not artwork_paths:
            raise ValueError(f"Order {order.order_number} has no artwork items")

        for artwork_path in artwork_paths:
            read_artwork_import_info(artwork_path)

        slots_per_sheet = self.viewport.jig_slots_per_sheet()
        if slots_per_sheet <= 0:
            raise ValueError("The active jig layout has no slots")

        remaining_slots = self.viewport.remaining_jig_slots_on_next_sheet()
        if remaining_slots and len(artwork_paths) > remaining_slots:
            self.viewport.reserve_remaining_jig_slots_on_next_sheet()

        imported_items = []
        try:
            for artwork_path in artwork_paths:
                item = self.viewport.add_artwork_file(artwork_path, QPointF(0, 0))
                if item is None:
                    raise ValueError(f"Could not import artwork: {artwork_path}")
                item.setData(HOT_FOLDER_ORDER_DATA_KEY, order.order_number)
                imported_items.append(item)
        except Exception:
            self.viewport.remove_image_items(imported_items)
            raise

        return imported_items

    def _retarget_hot_folder_items(
        self, items: Iterable[object], old_root: Path, new_root: Path
    ) -> None:
        for item in items:
            old_path = Path(str(item.data(0)))
            try:
                relative_path = old_path.relative_to(old_root)
            except ValueError:
                continue
            item.setData(0, str(new_root / relative_path))
            self.item_panel.refresh_image_item(item)

    def _refresh_label_printers(self) -> None:
        try:
            printers = list_printnode_printers()
        except Exception as error:
            self.statusBar().showMessage(
                f"Could not load PrintNode printers: {error}", 8000
            )
            return

        self.artboard_panel.set_label_printers(printers)
        self.statusBar().showMessage(
            f"Loaded {len(printers)} PrintNode printer(s).", 5000
        )

    def _print_jig_sheet_labels(self, sheet_index: int) -> None:
        profile = self._active_profile_if(
            NestProfileKind.JIG,
            "Label printing is only available for jig profiles.",
            5000,
        )
        if profile is None:
            return

        order_numbers = self._sheet_order_numbers(sheet_index)
        if not order_numbers:
            self.statusBar().showMessage(
                f"Sheet {sheet_index + 1} has no hot-folder orders to print.", 5000
            )
            return

        printer_id = self.artboard_panel.label_printer_id()
        if not printer_id:
            self.statusBar().showMessage(
                "Select or enter a PrintNode label printer before printing labels.",
                7000,
            )
            return

        save_label_printer_id(printer_id)
        try:
            print_job_id = print_order_labels(printer_id, order_numbers)
        except Exception as error:
            self.statusBar().showMessage(
                f"Label print failed for sheet {sheet_index + 1}: {error}", 9000
            )
            return

        profile.labels_printed_sheets.add(sheet_index)
        self.statusBar().showMessage(
            f"Submitted labels for sheet {sheet_index + 1}: job {print_job_id}", 7000
        )

    def _copy_jig_sheet_order_numbers(self, sheet_index: int) -> None:
        profile = self._active_profile_if(
            NestProfileKind.JIG,
            "Order numbers are only available for jig profiles.",
            5000,
        )
        if profile is None:
            return

        order_numbers = self._sheet_order_numbers(sheet_index)
        if not order_numbers:
            self.statusBar().showMessage(
                f"Sheet {sheet_index + 1} has no hot-folder orders to copy.", 5000
            )
            return

        QApplication.clipboard().setText("; ".join(order_numbers))
        self.statusBar().showMessage(
            f"Copied {len(order_numbers)} order number(s) from sheet "
            f"{sheet_index + 1}.",
            5000,
        )

    def _export_jig_mockup(self) -> None:
        profile = self._active_profile_if(
            NestProfileKind.JIG,
            "Jig mockup export is only available for jig profiles.",
            5000,
        )
        if profile is None:
            return

        slot_layout = self.viewport.slot_layout_settings()
        if slot_layout is None:
            self.statusBar().showMessage("No jig slot layout is active.", 5000)
            return

        settings = self.artboard_panel.export_settings()
        default_dir = (
            settings.jig_print_directory
            if str(settings.jig_print_directory).strip()
            else Path.cwd()
        )
        default_path = default_dir / "jig_mockup.pdf"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Jig Mockup PDF",
            str(default_path),
            "PDF Files (*.pdf)",
        )
        if not path:
            return

        destination = Path(path)
        if destination.suffix.lower() != ".pdf":
            destination = destination.with_suffix(".pdf")

        try:
            result = export_jig_mockup_pdf(
                slot_layout,
                destination,
                settings.local_temp_directory,
            )
        except Exception as error:
            self.statusBar().showMessage(f"Jig mockup export failed: {error}", 8000)
            return

        self.statusBar().showMessage(f"Exported jig mockup: {result.path}", 7000)

    def _export_sheet(self, sheet_index: int, kind_name: str) -> None:
        profile = self._active_profile()
        if profile.kind == NestProfileKind.JIG:
            if kind_name != "jig":
                self.statusBar().showMessage(
                    "Jig profiles only export jig PDFs from this panel.", 5000
                )
                return
            self._export_jig_sheet(sheet_index)
            return

        if profile.kind != NestProfileKind.SHEET:
            self.statusBar().showMessage(
                "Sheet export is only available for sheet profiles.", 5000
            )
            return

        kind = ExportKind(kind_name)
        export_number = self._export_number_for_sheet(profile, sheet_index)
        try:
            result = export_sheet_pdf(
                self.viewport.export_sheet_data(
                    sheet_index, marker_pattern=export_number
                ),
                kind,
                self.artboard_panel.export_settings(),
                export_number=export_number,
            )
            self._commit_export_number(profile, sheet_index, export_number)
        except Exception as error:
            self.statusBar().showMessage(
                f"Export failed for sheet {sheet_index + 1} {kind.value}: {error}", 8000
            )
            return

        profile.exported_sheets.add((sheet_index, kind))
        self._refresh_sheet_panel()
        self.statusBar().showMessage(
            f"Exported {kind.value} sheet {sheet_index + 1}: {result.path}", 7000
        )

    def _export_jig_sheet(self, sheet_index: int) -> None:
        profile = self._active_profile_if(
            NestProfileKind.JIG,
            "Jig export is only available for jig profiles.",
            5000,
        )
        if profile is None:
            return

        settings = self.artboard_panel.export_settings()
        export_number = self._export_number_for_sheet(profile, sheet_index)
        try:
            destination = self._jig_sheet_destination(settings, export_number)
            result = export_jig_layout_pdf(
                [self.viewport.export_sheet_data(sheet_index)],
                destination,
                settings.local_temp_directory,
            )
            self._commit_export_number(profile, sheet_index, export_number)
        except Exception as error:
            self.statusBar().showMessage(
                f"Jig export failed for sheet {sheet_index + 1}: {error}", 8000
            )
            return

        profile.exported_sheets.add((sheet_index, ExportKind.PRINT))
        self.viewport.set_jig_sheet_locked(sheet_index)
        self._refresh_sheet_panel()
        self.statusBar().showMessage(
            f"Exported jig sheet {sheet_index + 1}: {result.path}", 7000
        )

    def _jig_sheet_destination(self, settings, export_number: int) -> Path:  # type: ignore[no-untyped-def]
        if (
            not str(settings.jig_print_directory).strip()
            or settings.jig_print_directory == Path(".")
        ):
            raise ValueError("jig print export directory is not configured")
        return (
            settings.jig_print_directory
            / f"jig_sheet_{export_number:03d}.pdf"
        )

    def _export_number_for_sheet(self, profile: ProfileTab, sheet_index: int) -> int:
        return profile.export_numbers.get(
            sheet_index, self._export_counter_input.value()
        )

    def _commit_export_number(
        self, profile: ProfileTab, sheet_index: int, export_number: int
    ) -> None:
        if sheet_index in profile.export_numbers:
            return
        profile.export_numbers[sheet_index] = export_number
        if self._export_counter_input.value() <= export_number:
            self._export_counter_input.setValue(export_number + 1)

    def _sheet_order_numbers(self, sheet_index: int) -> list[str]:
        order_numbers: list[str] = []
        seen: set[str] = set()
        for item in self.viewport.image_items():
            if self.viewport.item_sheet_index(item) != sheet_index:
                continue
            order_number = item.data(HOT_FOLDER_ORDER_DATA_KEY)
            value = str(order_number).strip() if order_number else ""
            if value and value not in seen:
                seen.add(value)
                order_numbers.append(value)
        return order_numbers

    def _export_all_pending(self) -> None:
        profile = self._active_profile()
        if profile.kind == NestProfileKind.JIG:
            exported = 0
            labels_printed = 0
            settings = self.artboard_panel.export_settings()
            sheet_indices = self.viewport.exportable_sheet_indices()
            labels_pending = any(
                self._sheet_order_numbers(sheet_index)
                and sheet_index not in profile.labels_printed_sheets
                for sheet_index in sheet_indices
            )
            printer_id = self.artboard_panel.label_printer_id()
            if labels_pending and not printer_id:
                self.statusBar().showMessage(
                    "Select or enter a PrintNode label printer before exporting all.",
                    7000,
                )
                return
            if labels_pending:
                save_label_printer_id(printer_id)

            for sheet_index in sheet_indices:
                pdf_pending = (
                    sheet_index,
                    ExportKind.PRINT,
                ) not in profile.exported_sheets
                order_numbers = self._sheet_order_numbers(sheet_index)
                sheet_labels_pending = (
                    bool(order_numbers)
                    and sheet_index not in profile.labels_printed_sheets
                )
                if not pdf_pending and not sheet_labels_pending:
                    continue

                if pdf_pending:
                    export_number = self._export_number_for_sheet(profile, sheet_index)
                    try:
                        export_jig_layout_pdf(
                            [self.viewport.export_sheet_data(sheet_index)],
                            self._jig_sheet_destination(settings, export_number),
                            settings.local_temp_directory,
                        )
                        self._commit_export_number(profile, sheet_index, export_number)
                    except Exception as error:
                        self._refresh_sheet_panel()
                        self.statusBar().showMessage(
                            f"Export all stopped on jig sheet {sheet_index + 1}: {error}",
                            9000,
                        )
                        return
                    profile.exported_sheets.add((sheet_index, ExportKind.PRINT))
                    self.viewport.set_jig_sheet_locked(sheet_index)
                    exported += 1

                if sheet_labels_pending:
                    try:
                        print_order_labels(printer_id, order_numbers)
                    except Exception as error:
                        self._refresh_sheet_panel()
                        self.statusBar().showMessage(
                            "Export all stopped after writing jig sheet "
                            f"{sheet_index + 1}; label print failed: {error}",
                            9000,
                        )
                        return
                    profile.labels_printed_sheets.add(sheet_index)
                    labels_printed += 1

            self._refresh_sheet_panel()
            self.statusBar().showMessage(
                "Export all complete: "
                f"{exported} jig file(s) written, "
                f"{labels_printed} label job(s) submitted.",
                7000,
            )
            return
        if profile.kind != NestProfileKind.SHEET:
            self.statusBar().showMessage(
                "Sheet export is only available for sheet profiles.", 5000
            )
            return

        exported = 0
        for sheet_index in self.viewport.exportable_sheet_indices():
            export_number = self._export_number_for_sheet(profile, sheet_index)
            for kind in (ExportKind.PRINT, ExportKind.CUT):
                if (sheet_index, kind) in profile.exported_sheets:
                    continue
                try:
                    export_sheet_pdf(
                        self.viewport.export_sheet_data(
                            sheet_index, marker_pattern=export_number
                        ),
                        kind,
                        self.artboard_panel.export_settings(),
                        export_number=export_number,
                    )
                    self._commit_export_number(
                        profile, sheet_index, export_number
                    )
                except Exception as error:
                    self.statusBar().showMessage(
                        f"Export all stopped on sheet {sheet_index + 1} {kind.value}: {error}",
                        9000,
                    )
                    return
                profile.exported_sheets.add((sheet_index, kind))
                exported += 1

        self._refresh_sheet_panel()
        self.statusBar().showMessage(
            f"Export all complete: {exported} file(s) written.", 7000
        )

    def _refresh_sheet_panel(self) -> None:
        profile = self._active_profile()
        rows: list[SheetPanelRow] = []
        for sheet_index, item_count, ready in self.viewport.sheet_item_summary():
            rows.append(
                SheetPanelRow(
                    sheet_index=sheet_index,
                    item_count=item_count,
                    ready=ready,
                    print_exported=(sheet_index, ExportKind.PRINT)
                    in profile.exported_sheets,
                    cut_exported=(sheet_index, ExportKind.CUT)
                    in profile.exported_sheets,
                    thumbnail=self.viewport.sheet_thumbnail(sheet_index),
                    order_numbers=tuple(
                        self._sheet_order_numbers(sheet_index)
                        if profile.kind == NestProfileKind.JIG
                        else []
                    ),
                    locked=(
                        profile.kind == NestProfileKind.JIG
                        and self.viewport.is_jig_sheet_locked(sheet_index)
                    ),
                )
            )
        self.artboard_panel.set_sheet_rows(rows)

    def _build_canvas_area(
        self, viewport: PrintViewport, kind: NestProfileKind
    ) -> QWidget:
        canvas = QWidget(self)
        layout = QGridLayout(canvas)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        corner = QFrame(canvas)
        corner.setFixedSize(42, 28)
        corner.setToolTip(f"{kind.value.title()} profile")
        corner.setStyleSheet(
            "background: #eceff1; border-right: 1px solid #b6bdc4; border-bottom: 1px solid #b6bdc4;"
        )

        top_ruler = RulerWidget(Qt.Orientation.Horizontal, viewport)
        left_ruler = RulerWidget(Qt.Orientation.Vertical, viewport)

        layout.addWidget(corner, 0, 0)
        layout.addWidget(top_ruler, 0, 1)
        layout.addWidget(left_ruler, 1, 0)
        layout.addWidget(viewport, 1, 1)
        layout.setColumnStretch(1, 1)
        layout.setRowStretch(1, 1)

        return canvas
