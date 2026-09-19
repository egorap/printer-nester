from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from printer_nester.core.artboard import (
    DEFAULT_SLOT_LAYOUT,
    ArtboardSettings,
    SlotLayoutSettings,
)
from printer_nester.core.nest_profile import NestProfileKind
from printer_nester.core.item_transform import ItemTransform
from printer_nester.core.pdf_export import ExportSettings
from printer_nester.ui.preferences import (
    load_export_settings,
    load_hot_folder_root,
    load_label_printer_id,
    save_default_artboard,
    save_default_slot_layout,
    save_default_slot_mask_path,
    save_export_settings,
    save_hot_folder_root,
    save_label_printer_id,
)


@dataclass(frozen=True, slots=True)
class SheetPanelRow:
    sheet_index: int
    item_count: int
    ready: bool
    print_exported: bool
    cut_exported: bool
    thumbnail: QPixmap | None = None
    order_numbers: tuple[str, ...] = ()
    locked: bool = False


class ArtboardPanel(QWidget):
    artboard_changed = Signal(object)
    export_all_requested = Signal()
    export_jig_mockup_requested = Signal()
    process_hot_folder_requested = Signal()
    refresh_label_printers_requested = Signal()
    print_labels_requested = Signal(int)
    copy_order_numbers_requested = Signal(int)
    slot_layout_changed = Signal(object)
    jig_mask_changed = Signal(object)
    export_sheet_requested = Signal(int, str)
    item_transform_changed = Signal(object, object)

    def __init__(
        self,
        artboard: ArtboardSettings,
        slot_layout: SlotLayoutSettings = DEFAULT_SLOT_LAYOUT,
    ) -> None:
        super().__init__()

        self._updating = False
        self._updating_transform = False
        self._selected_transform_item = None
        self._transform_aspect_ratio = 1.0
        self._sheet_list_layout: QVBoxLayout | None = None
        self._slot_mask_input: QLineEdit | None = None
        self._hot_folder_input: QLineEdit | None = None
        self._label_printer_combo: QComboBox | None = None
        self._profile_kind = NestProfileKind.SHEET
        self.setMinimumWidth(360)
        self.setMaximumWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        self._title = QLabel("Artboard")
        self._title.setObjectName("panelTitle")
        self._sheet_artboard_section = self._build_sheet_artboard_section(artboard)
        self._slot_layout_section = self._build_slot_layout_section(slot_layout)

        self._save_default_button = QPushButton("Set as Default")
        self._save_default_button.clicked.connect(self._save_default)

        layout.addWidget(self._title)
        layout.addWidget(self._sheet_artboard_section)
        layout.addWidget(self._slot_layout_section)
        layout.addWidget(self._save_default_button)
        self._transform_section = self._build_transform_section()
        layout.addWidget(self._transform_section)
        layout.addWidget(self._build_export_settings_section())
        layout.addSpacing(8)
        layout.addWidget(self._build_sheet_export_section(), 1)
        self.set_profile_kind(NestProfileKind.SHEET)

    def artboard(self) -> ArtboardSettings:
        return ArtboardSettings(
            width_in=self._width_input.value(),
            height_in=self._height_input.value(),
            margin_in=self._margin_input.value(),
        )

    def set_artboard(self, artboard: ArtboardSettings) -> None:
        self._updating = True
        self._width_input.setValue(artboard.width_in)
        self._height_input.setValue(artboard.height_in)
        self._margin_input.setValue(artboard.margin_in)
        self._updating = False

    def slot_layout(self) -> SlotLayoutSettings:
        return SlotLayoutSettings(
            slot_width_in=self._slot_width_input.value(),
            slot_height_in=self._slot_height_input.value(),
            slot_margin_in=0.0,
            horizontal_spacing_in=self._slot_horizontal_spacing_input.value(),
            vertical_spacing_in=self._slot_vertical_spacing_input.value(),
            columns=self._slot_columns_input.value(),
            rows=self._slot_rows_input.value(),
            outside_margin_in=self._slot_outside_margin_input.value(),
        )

    def set_slot_layout(self, slot_layout: SlotLayoutSettings) -> None:
        self._updating = True
        self._slot_width_input.setValue(slot_layout.slot_width_in)
        self._slot_height_input.setValue(slot_layout.slot_height_in)
        self._slot_horizontal_spacing_input.setValue(slot_layout.horizontal_spacing_in)
        self._slot_vertical_spacing_input.setValue(slot_layout.vertical_spacing_in)
        self._slot_columns_input.setValue(slot_layout.columns)
        self._slot_rows_input.setValue(slot_layout.rows)
        self._slot_outside_margin_input.setValue(slot_layout.outside_margin_in)
        self._updating = False

    def jig_mask_path(self) -> Path | None:
        if self._slot_mask_input is None:
            return None
        value = self._slot_mask_input.text().strip()
        return Path(value) if value else None

    def set_jig_mask_path(self, path: Path | None) -> None:
        if self._slot_mask_input is None:
            return
        self._slot_mask_input.setText(str(path) if path is not None else "")

    def hot_folder_root(self) -> Path:
        if self._hot_folder_input is None:
            return Path()
        value = self._hot_folder_input.text().strip()
        return Path(value) if value else Path()

    def set_hot_folder_root(self, root: Path) -> None:
        if self._hot_folder_input is None:
            return
        self._hot_folder_input.setText("" if root == Path(".") else str(root))

    def label_printer_id(self) -> str:
        if self._label_printer_combo is None:
            return ""
        current_text = self._label_printer_combo.currentText().strip()
        current_index = self._label_printer_combo.currentIndex()
        if (
            current_index >= 0
            and self._label_printer_combo.itemText(current_index) == current_text
        ):
            current_data = self._label_printer_combo.currentData()
            if current_data is not None:
                return str(current_data).strip()
        return current_text

    def set_label_printer_id(self, printer_id: str) -> None:
        if self._label_printer_combo is None:
            return
        printer_id = str(printer_id).strip()
        index = self._label_printer_combo.findData(printer_id)
        if index >= 0:
            self._label_printer_combo.setCurrentIndex(index)
            return
        if printer_id:
            self._label_printer_combo.setCurrentIndex(-1)
            self._label_printer_combo.setEditText(printer_id)
        else:
            self._label_printer_combo.setEditText("")

    def set_label_printers(self, printers: list[object]) -> None:
        if self._label_printer_combo is None:
            return

        selected_id = self.label_printer_id() or load_label_printer_id()
        self._label_printer_combo.blockSignals(True)
        self._label_printer_combo.clear()
        for printer in printers:
            printer_id = str(getattr(printer, "printer_id", "")).strip()
            if not printer_id:
                continue
            display_name = str(getattr(printer, "display_name", printer_id)).strip()
            self._label_printer_combo.addItem(
                f"{printer_id} - {display_name}", printer_id
            )
        if selected_id and self._label_printer_combo.findData(selected_id) < 0:
            self._label_printer_combo.addItem(selected_id, selected_id)
        self._label_printer_combo.blockSignals(False)
        if selected_id:
            self.set_label_printer_id(selected_id)
        elif self._label_printer_combo.count() > 0:
            self._label_printer_combo.setCurrentIndex(0)

    def set_profile_kind(self, kind: NestProfileKind | str) -> None:
        self._profile_kind = NestProfileKind(kind)
        is_jig = self._profile_kind == NestProfileKind.JIG
        self._title.setText("Jig Slots" if is_jig else "Artboard")
        self._sheet_artboard_section.setVisible(not is_jig)
        self._slot_layout_section.setVisible(is_jig)
        self._transform_section.setVisible(not is_jig)
        self._export_settings_title.setText(
            "Jig Print Export" if is_jig else "Sheet Export Folders"
        )
        self._sheet_print_export_row.setVisible(not is_jig)
        self._jig_print_export_row.setVisible(is_jig)
        self._cut_export_row.setVisible(not is_jig)
        self._temp_export_row.setVisible(not is_jig)
        self._save_default_button.setToolTip(
            "Saves this jig slot layout and mask path as the default."
            if is_jig
            else "Saves this sheet artboard as the default."
        )
        if hasattr(self, "_sheet_export_title"):
            self._sheet_export_title.setText("Jig Sheets" if is_jig else "Sheets")
        if hasattr(self, "_sheet_export_all_button"):
            self._sheet_export_all_button.setToolTip(
                "Exports every pending jig PDF using the configured print folder."
                if is_jig
                else "Exports every pending print and cut PDF using the configured folders."
            )

    def set_sheet_rows(self, rows: list[SheetPanelRow]) -> None:
        if self._sheet_list_layout is None:
            return

        while self._sheet_list_layout.count():
            item = self._sheet_list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        for row in rows:
            self._sheet_list_layout.addWidget(self._build_sheet_row(row))
        self._sheet_list_layout.addStretch(1)

    def selected_transform_item(self):  # type: ignore[no-untyped-def]
        return self._selected_transform_item

    def set_selected_item_transform(
        self, item, transform: ItemTransform | None
    ) -> None:  # type: ignore[no-untyped-def]
        self._selected_transform_item = item
        self._updating_transform = True
        enabled = item is not None and transform is not None
        self._set_transform_controls_enabled(enabled)

        if transform is None:
            self._transform_x_input.setValue(0)
            self._transform_y_input.setValue(0)
            self._transform_width_input.setValue(0.001)
            self._transform_height_input.setValue(0.001)
            self._transform_rotation_input.setValue(0)
        else:
            self._transform_aspect_ratio = (
                transform.width_in / transform.height_in
                if transform.height_in > 0
                else 1.0
            )
            self._transform_x_input.setValue(transform.x_in)
            self._transform_y_input.setValue(transform.y_in)
            self._transform_width_input.setValue(transform.width_in)
            self._transform_height_input.setValue(transform.height_in)
            self._transform_rotation_input.setValue(transform.rotation_deg)

        self._updating_transform = False

    def transform_values(self) -> ItemTransform:
        return ItemTransform(
            x_in=self._transform_x_input.value(),
            y_in=self._transform_y_input.value(),
            width_in=self._transform_width_input.value(),
            height_in=self._transform_height_input.value(),
            rotation_deg=self._transform_rotation_input.value(),
        )

    def _build_sheet_artboard_section(self, artboard: ArtboardSettings) -> QWidget:
        section = QFrame()
        section.setObjectName("sheetArtboardSection")
        section.setStyleSheet(
            """
            QFrame#sheetArtboardSection {
                border-top: 1px solid #d3d8de;
                padding-top: 8px;
            }
            QLabel#sectionTitle {
                color: #202428;
                font-weight: 600;
            }
            """
        )
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        title = QLabel("Sheet")
        title.setObjectName("sectionTitle")
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self._width_input = self._make_spinbox(
            minimum=1, maximum=1_000, value=artboard.width_in
        )
        self._height_input = self._make_spinbox(
            minimum=1, maximum=1_000, value=artboard.height_in
        )
        self._margin_input = self._make_spinbox(
            minimum=0, maximum=24, value=artboard.margin_in
        )
        form.addRow("Width (in)", self._width_input)
        form.addRow("Height (in)", self._height_input)
        form.addRow("Margin (in)", self._margin_input)
        layout.addWidget(title)
        layout.addLayout(form)
        self._width_input.valueChanged.connect(self._emit_artboard_changed)
        self._height_input.valueChanged.connect(self._emit_artboard_changed)
        self._margin_input.valueChanged.connect(self._emit_artboard_changed)
        return section

    def _build_slot_layout_section(self, slot_layout: SlotLayoutSettings) -> QWidget:
        section = QFrame()
        section.setObjectName("slotLayoutSection")
        section.setStyleSheet(
            """
            QFrame#slotLayoutSection {
                border-top: 1px solid #d3d8de;
                padding-top: 8px;
            }
            QLabel#sectionTitle {
                color: #202428;
                font-weight: 600;
            }
            """
        )
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        title = QLabel("Slot Layout")
        title.setObjectName("sectionTitle")
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        self._slot_width_input = self._make_spinbox(
            minimum=0.001, maximum=1_000, value=slot_layout.slot_width_in
        )
        self._slot_height_input = self._make_spinbox(
            minimum=0.001, maximum=1_000, value=slot_layout.slot_height_in
        )

        self._slot_horizontal_spacing_input = self._make_spinbox(
            minimum=0, maximum=100, value=slot_layout.horizontal_spacing_in
        )
        self._slot_vertical_spacing_input = self._make_spinbox(
            minimum=0, maximum=100, value=slot_layout.vertical_spacing_in
        )
        self._slot_columns_input = self._make_int_spinbox(
            minimum=1, maximum=100, value=slot_layout.columns
        )
        self._slot_rows_input = self._make_int_spinbox(
            minimum=1, maximum=100, value=slot_layout.rows
        )
        self._slot_outside_margin_input = self._make_spinbox(
            minimum=0, maximum=100, value=slot_layout.outside_margin_in
        )
        grid.addWidget(QLabel("Slot W"), 0, 0)
        grid.addWidget(self._slot_width_input, 0, 1)
        grid.addWidget(QLabel("Slot H"), 0, 2)
        grid.addWidget(self._slot_height_input, 0, 3)
        grid.addWidget(QLabel("Slots X"), 1, 0)
        grid.addWidget(self._slot_columns_input, 1, 1)
        grid.addWidget(QLabel("Slots Y"), 1, 2)
        grid.addWidget(self._slot_rows_input, 1, 3)
        grid.addWidget(QLabel("H Spacing"), 2, 0)
        grid.addWidget(self._slot_horizontal_spacing_input, 2, 1)
        grid.addWidget(QLabel("V Spacing"), 2, 2)
        grid.addWidget(self._slot_vertical_spacing_input, 2, 3)
        grid.addWidget(QLabel("Artboard Margin"), 3, 0)
        grid.addWidget(self._slot_outside_margin_input, 3, 1, 1, 3)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        mask_title = QLabel("Slot Mask")
        mask_title.setObjectName("sectionTitle")
        self._slot_mask_input = QLineEdit()
        self._slot_mask_input.setReadOnly(True)
        self._slot_mask_input.setPlaceholderText("No mask selected")
        browse_mask_button = QPushButton("Browse")
        clear_mask_button = QPushButton("Clear")
        browse_mask_button.clicked.connect(self._browse_slot_mask)
        clear_mask_button.clicked.connect(self._clear_slot_mask)
        mask_row = QHBoxLayout()
        mask_row.setContentsMargins(0, 0, 0, 0)
        mask_row.setSpacing(6)
        mask_row.addWidget(self._slot_mask_input, 1)
        mask_row.addWidget(browse_mask_button)
        mask_row.addWidget(clear_mask_button)

        layout.addWidget(title)
        layout.addLayout(grid)
        layout.addSpacing(6)
        mockup_button = QPushButton("Export Jig Mockup PDF")
        mockup_button.setToolTip(
            "Exports this jig artboard and slot layout as a vector PDF."
        )
        mockup_button.clicked.connect(self.export_jig_mockup_requested.emit)

        hot_folder_title = QLabel("Hot Folder")
        hot_folder_title.setObjectName("sectionTitle")
        hot_folder_root = load_hot_folder_root()
        self._hot_folder_input = QLineEdit(
            "" if hot_folder_root == Path(".") else str(hot_folder_root)
        )
        self._hot_folder_input.setPlaceholderText("Order package folder")
        browse_hot_folder_button = QPushButton("Browse")
        browse_hot_folder_button.clicked.connect(
            lambda _checked=False: self._browse_for_folder(self._hot_folder_input)
        )
        process_hot_folder_button = QPushButton("Process Orders")
        process_hot_folder_button.setToolTip(
            "Imports ready order ZIPs from the configured hot folder."
        )
        process_hot_folder_button.clicked.connect(self._process_hot_folder)
        hot_folder_row = QHBoxLayout()
        hot_folder_row.setContentsMargins(0, 0, 0, 0)
        hot_folder_row.setSpacing(6)
        hot_folder_row.addWidget(self._hot_folder_input, 1)
        hot_folder_row.addWidget(browse_hot_folder_button)

        label_printer_title = QLabel("Label Printer")
        label_printer_title.setObjectName("sectionTitle")
        self._label_printer_combo = QComboBox()
        self._label_printer_combo.setEditable(True)
        self._label_printer_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._label_printer_combo.setPlaceholderText("Printer id")
        saved_label_printer_id = load_label_printer_id()
        if saved_label_printer_id:
            self._label_printer_combo.addItem(
                saved_label_printer_id, saved_label_printer_id
            )
            self._label_printer_combo.setCurrentIndex(0)
        self._label_printer_combo.currentTextChanged.connect(
            self._save_label_printer_id
        )
        refresh_printers_button = QPushButton("Refresh Printers")
        refresh_printers_button.setToolTip(
            "Loads available printers from PrintNode using PRINTNODE_KEY."
        )
        refresh_printers_button.clicked.connect(
            self.refresh_label_printers_requested.emit
        )
        label_printer_row = QHBoxLayout()
        label_printer_row.setContentsMargins(0, 0, 0, 0)
        label_printer_row.setSpacing(6)
        label_printer_row.addWidget(self._label_printer_combo, 1)
        label_printer_row.addWidget(refresh_printers_button)

        layout.addWidget(mask_title)
        layout.addLayout(mask_row)
        layout.addSpacing(6)
        layout.addWidget(mockup_button)
        layout.addSpacing(6)
        layout.addWidget(hot_folder_title)
        layout.addLayout(hot_folder_row)
        layout.addWidget(process_hot_folder_button)
        layout.addSpacing(6)
        layout.addWidget(label_printer_title)
        layout.addLayout(label_printer_row)
        for input_widget in (
            self._slot_width_input,
            self._slot_height_input,
            self._slot_horizontal_spacing_input,
            self._slot_vertical_spacing_input,
            self._slot_columns_input,
            self._slot_rows_input,
            self._slot_outside_margin_input,
        ):
            input_widget.valueChanged.connect(self._emit_slot_layout_changed)
        return section

    def _browse_slot_mask(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Slot Mask",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff)",
        )
        if not path:
            return
        if self._slot_mask_input is not None:
            self._slot_mask_input.setText(path)
        self.jig_mask_changed.emit(Path(path))

    def _clear_slot_mask(self) -> None:
        if self._slot_mask_input is not None:
            self._slot_mask_input.clear()
        self.jig_mask_changed.emit(None)

    def _build_transform_section(self) -> QWidget:
        section = QFrame()
        section.setObjectName("transformSection")
        section.setStyleSheet(
            """
            QFrame#transformSection {
                border-top: 1px solid #d3d8de;
                padding-top: 8px;
            }
            QLabel#sectionTitle {
                color: #202428;
                font-weight: 600;
            }
            """
        )
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        title = QLabel("Transform")
        title.setObjectName("sectionTitle")
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)

        self._transform_x_input = self._make_transform_spinbox(
            -100_000, 100_000, 0, 0.125, " in"
        )
        self._transform_y_input = self._make_transform_spinbox(
            -100_000, 100_000, 0, 0.125, " in"
        )
        self._transform_width_input = self._make_transform_spinbox(
            0.001, 100_000, 1, 0.125, " in"
        )
        self._transform_height_input = self._make_transform_spinbox(
            0.001, 100_000, 1, 0.125, " in"
        )
        self._transform_rotation_input = self._make_transform_spinbox(
            -3600, 3600, 0, 1, " deg"
        )
        self._transform_lock_input = QCheckBox("Lock proportions")
        self._transform_lock_input.setChecked(True)
        self._transform_lock_input.toggled.connect(self._handle_transform_lock_toggled)

        grid.addWidget(QLabel("X"), 0, 0)
        grid.addWidget(self._transform_x_input, 0, 1)
        grid.addWidget(QLabel("Y"), 0, 2)
        grid.addWidget(self._transform_y_input, 0, 3)
        grid.addWidget(QLabel("W"), 1, 0)
        grid.addWidget(self._transform_width_input, 1, 1)
        grid.addWidget(QLabel("H"), 1, 2)
        grid.addWidget(self._transform_height_input, 1, 3)
        grid.addWidget(QLabel("Rotation"), 2, 0)
        grid.addWidget(self._transform_rotation_input, 2, 1, 1, 3)
        grid.addWidget(self._transform_lock_input, 3, 0, 1, 4)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

        self._transform_x_input.valueChanged.connect(self._emit_item_transform_changed)
        self._transform_y_input.valueChanged.connect(self._emit_item_transform_changed)
        self._transform_width_input.valueChanged.connect(
            self._handle_transform_width_changed
        )
        self._transform_height_input.valueChanged.connect(
            self._handle_transform_height_changed
        )
        self._transform_rotation_input.valueChanged.connect(
            self._emit_item_transform_changed
        )

        layout.addWidget(title)
        layout.addLayout(grid)
        self._set_transform_controls_enabled(False)
        return section

    def _make_transform_spinbox(
        self, minimum: float, maximum: float, value: float, step: float, suffix: str
    ) -> QDoubleSpinBox:
        spinbox = QDoubleSpinBox()
        spinbox.setRange(minimum, maximum)
        spinbox.setDecimals(2)
        spinbox.setSingleStep(step)
        spinbox.setSuffix(suffix)
        spinbox.setKeyboardTracking(False)
        spinbox.setValue(value)
        return spinbox

    def _transform_spinboxes(
        self,
    ) -> tuple[
        QDoubleSpinBox, QDoubleSpinBox, QDoubleSpinBox, QDoubleSpinBox, QDoubleSpinBox
    ]:
        return (
            self._transform_x_input,
            self._transform_y_input,
            self._transform_width_input,
            self._transform_height_input,
            self._transform_rotation_input,
        )

    def _set_transform_controls_enabled(self, enabled: bool) -> None:
        for spinbox in self._transform_spinboxes():
            spinbox.setEnabled(enabled)
        self._transform_lock_input.setEnabled(enabled)

    def _handle_transform_lock_toggled(self, checked: bool) -> None:
        if checked:
            height = self._transform_height_input.value()
            self._transform_aspect_ratio = (
                self._transform_width_input.value() / height if height > 0 else 1.0
            )

    def _handle_transform_width_changed(self, value: float) -> None:
        if (
            not self._updating_transform
            and self._transform_lock_input.isChecked()
            and self._transform_aspect_ratio > 0
        ):
            self._updating_transform = True
            self._transform_height_input.setValue(
                max(0.001, value / self._transform_aspect_ratio)
            )
            self._updating_transform = False
        self._emit_item_transform_changed()

    def _handle_transform_height_changed(self, value: float) -> None:
        if (
            not self._updating_transform
            and self._transform_lock_input.isChecked()
            and self._transform_aspect_ratio > 0
        ):
            self._updating_transform = True
            self._transform_width_input.setValue(
                max(0.001, value * self._transform_aspect_ratio)
            )
            self._updating_transform = False
        self._emit_item_transform_changed()

    def _emit_item_transform_changed(self) -> None:
        if self._updating_transform or self._selected_transform_item is None:
            return
        if not self._transform_lock_input.isChecked():
            height = self._transform_height_input.value()
            self._transform_aspect_ratio = (
                self._transform_width_input.value() / height if height > 0 else 1.0
            )
        self.item_transform_changed.emit(
            self._selected_transform_item, self.transform_values()
        )

    def _make_spinbox(
        self, minimum: float, maximum: float, value: float
    ) -> QDoubleSpinBox:
        spinbox = QDoubleSpinBox()
        spinbox.setRange(minimum, maximum)
        spinbox.setDecimals(3)
        spinbox.setSingleStep(0.125)
        spinbox.setValue(value)
        return spinbox

    def _make_int_spinbox(self, minimum: int, maximum: int, value: int) -> QSpinBox:
        spinbox = QSpinBox()
        spinbox.setRange(minimum, maximum)
        spinbox.setSingleStep(1)
        spinbox.setKeyboardTracking(False)
        spinbox.setValue(value)
        return spinbox

    def _emit_artboard_changed(self) -> None:
        if not self._updating:
            self.artboard_changed.emit(self.artboard())

    def _emit_slot_layout_changed(self) -> None:
        if not self._updating:
            self.slot_layout_changed.emit(self.slot_layout())

    def _save_default(self) -> None:
        if self._profile_kind == NestProfileKind.JIG:
            save_default_slot_layout(self.slot_layout())
            save_default_slot_mask_path(self.jig_mask_path())
        else:
            save_default_artboard(self.artboard())

    def export_settings(self) -> ExportSettings:
        return ExportSettings(
            sheet_print_directory=Path(
                self._sheet_print_export_input.text().strip()
            ),
            jig_print_directory=Path(
                self._jig_print_export_input.text().strip()
            ),
            cut_directory=Path(self._cut_export_input.text().strip()),
            local_temp_directory=Path(self._temp_export_input.text().strip()),
        )

    def _build_export_settings_section(self) -> QWidget:
        settings = load_export_settings()
        section = QFrame()
        section.setObjectName("exportSettingsSection")
        section.setStyleSheet(
            """
            QFrame#exportSettingsSection {
                border-top: 1px solid #d3d8de;
                padding-top: 8px;
            }
            QLabel#sectionTitle {
                color: #202428;
                font-weight: 600;
            }
            QLineEdit {
                min-height: 24px;
            }
            """
        )
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        title = QLabel("Sheet Export Folders")
        title.setObjectName("sectionTitle")
        self._export_settings_title = title
        self._sheet_print_export_input = QLineEdit(
            str(settings.sheet_print_directory)
        )
        self._jig_print_export_input = QLineEdit(
            str(settings.jig_print_directory)
        )
        self._cut_export_input = QLineEdit(str(settings.cut_directory))
        self._temp_export_input = QLineEdit(str(settings.local_temp_directory))
        self._sheet_print_export_input.setPlaceholderText(
            "Sheet print PDF folder"
        )
        self._jig_print_export_input.setPlaceholderText(
            "Jig print PDF folder"
        )
        self._cut_export_input.setPlaceholderText("Cut PDF folder")
        self._temp_export_input.setPlaceholderText("Local temp folder")

        self._sheet_print_export_row = self._folder_row(
            "Print", self._sheet_print_export_input
        )
        self._jig_print_export_row = self._folder_row(
            "Jig Print", self._jig_print_export_input
        )
        self._cut_export_row = self._folder_row("Cut", self._cut_export_input)
        self._temp_export_row = self._folder_row("Temp", self._temp_export_input)
        layout.addWidget(title)
        layout.addWidget(self._sheet_print_export_row)
        layout.addWidget(self._jig_print_export_row)
        layout.addWidget(self._cut_export_row)
        layout.addWidget(self._temp_export_row)

        save_button = QPushButton("Save Export Settings")
        save_button.clicked.connect(self._save_export_settings)
        layout.addWidget(save_button)
        return section

    def _folder_row(self, label: str, line_edit: QLineEdit) -> QWidget:
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(QLabel(label))
        row.addWidget(line_edit, 1)
        browse = QPushButton("Browse")
        browse.clicked.connect(
            lambda _checked=False, target=line_edit: self._browse_for_folder(target)
        )
        row.addWidget(browse)
        return widget

    def _browse_for_folder(self, target: QLineEdit) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Choose Export Folder", target.text()
        )
        if folder:
            target.setText(folder)

    def _save_export_settings(self) -> None:
        save_export_settings(self.export_settings())

    def _process_hot_folder(self) -> None:
        if self._hot_folder_input is not None and self._hot_folder_input.text().strip():
            save_hot_folder_root(self.hot_folder_root())
        self.process_hot_folder_requested.emit()

    def _save_label_printer_id(self, *_args) -> None:  # type: ignore[no-untyped-def]
        save_label_printer_id(self.label_printer_id())

    def _build_sheet_export_section(self) -> QWidget:
        section = QFrame()
        section.setObjectName("sheetExportSection")
        section.setStyleSheet(
            """
            QFrame#sheetExportSection {
                border-top: 1px solid #d3d8de;
                padding-top: 8px;
            }
            QLabel#sectionTitle {
                color: #202428;
                font-weight: 600;
            }
            QLabel#sheetName {
                color: #202428;
                font-weight: 600;
            }
            QLabel#sheetMeta {
                color: #5d6670;
                font-size: 11px;
            }
            QLabel#sheetThumb {
                background: #eef1f4;
                border: 1px solid #cbd2d9;
                border-radius: 4px;
            }
            QLabel[stateDot="ready"] {
                color: #16a34a;
                font-weight: 700;
            }
            QLabel[stateDot="pending"] {
                color: #9aa3ad;
                font-weight: 700;
            }
            QLabel[stateDot="warning"] {
                color: #dc2626;
                font-weight: 700;
            }
            QLabel[stateDot="locked"] {
                color: #2563eb;
                font-weight: 700;
            }
            QFrame#sheetRow {
                background: #ffffff;
                border: 1px solid #d4d9df;
                border-radius: 6px;
            }
            QPushButton#smallExportButton {
                padding: 3px 6px;
                font-size: 11px;
            }
            """
        )

        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Sheets")
        title.setObjectName("sectionTitle")
        self._sheet_export_title = title
        export_all = QPushButton("Export All")
        self._sheet_export_all_button = export_all
        export_all.setToolTip(
            "Exports every pending print and cut PDF using the configured folders."
        )
        export_all.clicked.connect(self.export_all_requested.emit)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(export_all)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        scroll.setMinimumHeight(320)

        list_widget = QWidget()
        list_layout = QVBoxLayout(list_widget)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(6)
        self._sheet_list_layout = list_layout
        self.set_sheet_rows([SheetPanelRow(0, 0, True, False, False)])

        scroll.setWidget(list_widget)
        layout.addLayout(header)
        layout.addWidget(scroll)
        return section

    def _build_sheet_row(self, sheet: SheetPanelRow) -> QWidget:
        row = QFrame()
        row.setObjectName("sheetRow")
        row.setMinimumHeight(128)

        layout = QHBoxLayout(row)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        thumbnail = QLabel()
        thumbnail.setObjectName("sheetThumb")
        thumbnail.setFixedSize(96, 72)
        thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview = (
            sheet.thumbnail
            if sheet.thumbnail is not None
            else self._sheet_thumbnail(
                sheet.ready, sheet.print_exported, sheet.cut_exported
            )
        )
        thumbnail.setPixmap(preview)

        content = QVBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(6)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        name = f"Sheet {sheet.sheet_index + 1}"
        meta = (
            "Empty"
            if sheet.item_count == 0
            else f"{sheet.item_count} item{'s' if sheet.item_count != 1 else ''}"
        )
        name_label = QLabel(name)
        name_label.setObjectName("sheetName")
        meta_label = QLabel(meta)
        meta_label.setObjectName("sheetMeta")
        top.addWidget(name_label)
        top.addStretch(1)
        top.addWidget(meta_label)

        is_jig = self._profile_kind == NestProfileKind.JIG
        state_values = (
            [
                ("Ready", sheet.ready),
                ("Jig PDF", sheet.print_exported),
                ("Locked", sheet.locked),
            ]
            if is_jig
            else [
                ("Ready", sheet.ready),
                ("Print", sheet.print_exported),
                ("Cut", sheet.cut_exported),
            ]
        )
        states = QGridLayout()
        states.setContentsMargins(0, 0, 0, 0)
        states.setHorizontalSpacing(8)
        states.setVerticalSpacing(2)
        for column, (label, ok) in enumerate(state_values):
            dot = QLabel("*")
            dot.setProperty(
                "stateDot",
                (
                    "locked"
                    if label == "Locked" and ok
                    else "ready"
                    if ok
                    else "warning"
                    if label == "Ready"
                    else "pending"
                ),
            )
            dot.setToolTip(f"{label}: {'done' if ok else 'pending'}")
            text = QLabel(label)
            text.setObjectName("sheetMeta")
            states.addWidget(dot, 0, column)
            states.addWidget(text, 1, column)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        if is_jig:
            jig_button = QPushButton("Export Jig")
            jig_button.setObjectName("smallExportButton")
            jig_button.setToolTip(
                "Exports this jig sheet using the configured print folder."
            )
            jig_button.setEnabled(sheet.item_count > 0)
            jig_button.clicked.connect(
                lambda _checked=False,
                index=sheet.sheet_index: self.export_sheet_requested.emit(index, "jig")
            )
            label_button = QPushButton("Print Labels")
            label_button.setObjectName("smallExportButton")
            label_button.setToolTip(
                "Prints labels for hot-folder orders on this jig sheet."
            )
            label_button.setEnabled(bool(sheet.order_numbers))
            label_button.clicked.connect(
                lambda _checked=False,
                index=sheet.sheet_index: self.print_labels_requested.emit(index)
            )
            copy_orders_button = QPushButton("Copy Orders")
            copy_orders_button.setObjectName("smallExportButton")
            copy_orders_button.setToolTip(
                "Copies this jig sheet's order numbers separated by semicolons."
            )
            copy_orders_button.setEnabled(bool(sheet.order_numbers))
            copy_orders_button.clicked.connect(
                lambda _checked=False,
                index=sheet.sheet_index: self.copy_order_numbers_requested.emit(index)
            )
            actions.addWidget(jig_button)
            actions.addWidget(label_button)
            actions.addWidget(copy_orders_button)
        else:
            print_button = QPushButton("Export Print")
            cut_button = QPushButton("Export Cut")
            for button in (print_button, cut_button):
                button.setObjectName("smallExportButton")
                button.setToolTip("Exports this sheet using the configured folders.")
                button.setEnabled(sheet.item_count > 0)
            print_button.clicked.connect(
                lambda _checked=False,
                index=sheet.sheet_index: self.export_sheet_requested.emit(index, "print")
            )
            cut_button.clicked.connect(
                lambda _checked=False,
                index=sheet.sheet_index: self.export_sheet_requested.emit(index, "cut")
            )
            actions.addWidget(print_button)
            actions.addWidget(cut_button)
        content.addLayout(top)
        content.addLayout(states)
        content.addLayout(actions)
        content.addStretch(1)
        layout.addWidget(thumbnail)
        layout.addLayout(content, 1)
        return row

    def _sheet_thumbnail(
        self, ready: bool, print_exported: bool, cut_exported: bool
    ) -> QPixmap:
        pixmap = QPixmap(90, 66)
        pixmap.fill(QColor("#eef1f4"))

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor("#9aa3ad"), 1))
        painter.setBrush(QColor("#ffffff"))
        painter.drawRect(6, 9, 78, 48)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#dbeafe") if print_exported else QColor("#edf2f7"))
        painter.drawRect(15, 17, 24, 14)
        painter.drawRect(45, 17, 24, 14)
        painter.setBrush(QColor("#fecaca") if cut_exported else QColor("#f1f5f9"))
        painter.drawRect(15, 39, 54, 8)

        painter.setBrush(QColor("#16a34a") if ready else QColor("#dc2626"))
        painter.drawEllipse(75, 12, 7, 7)
        painter.end()
        return pixmap
