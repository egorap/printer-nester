from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)


THUMBNAIL_SIZE = 52
ROW_HEIGHT = 104


class ItemPanel(QWidget):
    image_selected = Signal(object)
    image_rounding_changed = Signal(object, bool)

    def __init__(self) -> None:
        super().__init__()

        self.setMinimumWidth(360)
        self.setMaximumWidth(560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        title = QLabel("Items")
        title.setObjectName("panelTitle")

        self._list = QListWidget()
        self._list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._list.verticalScrollBar().setSingleStep(12)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setSpacing(6)
        self._list.itemClicked.connect(self._handle_item_clicked)
        self._list.setStyleSheet(
            """
            QListWidget {
                border: 1px solid #c9cfd6;
                background: #f7f8fa;
            }
            QListWidget::item {
                border: none;
                padding: 0;
            }
            QListWidget::item:selected {
                background: transparent;
            }
            """
        )

        layout.addWidget(title)
        layout.addWidget(self._list, 1)

    def add_image_item(self, graphics_item, path: str) -> None:  # type: ignore[no-untyped-def]
        row = ItemRowWidget(graphics_item, path)
        row.rounding_changed.connect(self._handle_rounding_changed)
        list_item = QListWidgetItem(Path(path).name)
        list_item.setToolTip(path)
        list_item.setData(Qt.ItemDataRole.UserRole, graphics_item)
        list_item.setSizeHint(QSize(0, ROW_HEIGHT))
        self._list.addItem(list_item)
        self._list.setItemWidget(list_item, row)
        self._handle_rounding_changed(graphics_item, True)
        row.refresh_dimensions()

    def remove_image_item(self, graphics_item) -> None:  # type: ignore[no-untyped-def]
        for row in range(self._list.count()):
            list_item = self._list.item(row)
            if list_item.data(Qt.ItemDataRole.UserRole) is graphics_item:
                self._list.takeItem(row)
                return

    def _handle_item_clicked(self, item: QListWidgetItem) -> None:
        graphics_item = item.data(Qt.ItemDataRole.UserRole)
        if graphics_item is not None:
            self.image_selected.emit(graphics_item)

    def _handle_rounding_changed(self, graphics_item, enabled: bool) -> None:  # type: ignore[no-untyped-def]
        self.image_rounding_changed.emit(graphics_item, enabled)


class ItemRowWidget(QFrame):
    rounding_changed = Signal(object, bool)

    def __init__(self, graphics_item, path: str) -> None:  # type: ignore[no-untyped-def]
        super().__init__()
        self._graphics_item = graphics_item

        self.setObjectName("itemRow")
        self.setFixedHeight(ROW_HEIGHT)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            """
            QFrame#itemRow {
                background: #ffffff;
                border: 1px solid #d4d9df;
                border-radius: 6px;
            }
            QLabel#itemName {
                color: #202428;
                font-weight: 600;
            }
            QLabel#itemMeta {
                color: #5d6670;
                font-size: 11px;
            }
            QCheckBox {
                color: #47515b;
                font-size: 11px;
                spacing: 4px;
            }
            """
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        thumbnail = QLabel()
        thumbnail.setFixedSize(QSize(64, 64))
        thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumbnail.setPixmap(self._thumbnail_for_item(graphics_item))
        thumbnail.setStyleSheet("background: #eef1f4; border: 1px solid #d4d9df; border-radius: 4px;")

        info_layout = QVBoxLayout()
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(4)

        name = QLabel(Path(path).name)
        name.setObjectName("itemName")
        name.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        name.setWordWrap(True)
        name.setMaximumHeight(46)
        name.setToolTip(path)

        self._dimensions = QLabel(self._dimensions_text(graphics_item))
        self._dimensions.setObjectName("itemMeta")

        dpi = QLabel(self._dpi_text(graphics_item))
        dpi.setObjectName("itemMeta")

        info_layout.addWidget(name)
        info_layout.addWidget(self._dimensions)
        info_layout.addWidget(dpi)
        info_layout.addStretch(1)

        toggle_layout = QVBoxLayout()
        toggle_layout.setContentsMargins(0, 0, 0, 0)
        toggle_layout.setSpacing(6)

        round_toggle = QCheckBox("Round")
        round_toggle.setChecked(True)
        round_toggle.setToolTip("Round this item to the nearest whole inch.")
        round_toggle.toggled.connect(self._handle_rounding_toggled)

        toggle_layout.addWidget(round_toggle)
        toggle_layout.addStretch(1)

        layout.addWidget(thumbnail)
        layout.addLayout(info_layout, 1)
        layout.addLayout(toggle_layout, 0)

    def _thumbnail_for_item(self, graphics_item) -> QPixmap:  # type: ignore[no-untyped-def]
        return graphics_item.pixmap().scaled(
            60,
            60,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    def _dimensions_text(self, graphics_item) -> str:  # type: ignore[no-untyped-def]
        width = float(graphics_item.data(3) or 0)
        height = float(graphics_item.data(4) or 0)
        return f"{width:.2f} x {height:.2f} in"

    def _dpi_text(self, graphics_item) -> str:  # type: ignore[no-untyped-def]
        kind = graphics_item.data(5)
        dpi_x = float(graphics_item.data(1) or 0)
        dpi_y = float(graphics_item.data(2) or 0)

        if kind == "pdf":
            return f"PDF preview {dpi_x:.0f} dpi"
        if abs(dpi_x - dpi_y) < 0.01:
            return f"{dpi_x:.0f} dpi"

        return f"{dpi_x:.0f} x {dpi_y:.0f} dpi"

    def refresh_dimensions(self) -> None:
        self._dimensions.setText(self._dimensions_text(self._graphics_item))

    def _handle_rounding_toggled(self, enabled: bool) -> None:
        self.rounding_changed.emit(self._graphics_item, enabled)
        self.refresh_dimensions()
