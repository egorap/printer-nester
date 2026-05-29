from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
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
    QVBoxLayout,
    QWidget,
)

from printer_nester.core.artboard import ArtboardSettings
from printer_nester.core.pdf_export import ExportSettings
from printer_nester.ui.preferences import load_export_settings, save_default_artboard, save_export_settings


@dataclass(frozen=True, slots=True)
class SheetPanelRow:
    sheet_index: int
    item_count: int
    ready: bool
    print_exported: bool
    cut_exported: bool
    thumbnail: QPixmap | None = None


class ArtboardPanel(QWidget):
    artboard_changed = Signal(object)
    export_all_requested = Signal()
    export_sheet_requested = Signal(int, str)

    def __init__(self, artboard: ArtboardSettings) -> None:
        super().__init__()

        self._updating = False
        self._sheet_list_layout: QVBoxLayout | None = None
        self.setMinimumWidth(360)
        self.setMaximumWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        title = QLabel("Artboard")
        title.setObjectName("panelTitle")

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self._width_input = self._make_spinbox(minimum=1, maximum=1_000, value=artboard.width_in)
        self._height_input = self._make_spinbox(minimum=1, maximum=1_000, value=artboard.height_in)
        self._margin_input = self._make_spinbox(minimum=0, maximum=24, value=artboard.margin_in)

        form.addRow("Width (in)", self._width_input)
        form.addRow("Height (in)", self._height_input)
        form.addRow("Margin (in)", self._margin_input)

        save_button = QPushButton("Set as Default")
        save_button.clicked.connect(self._save_default)

        layout.addWidget(title)
        layout.addLayout(form)
        layout.addWidget(save_button)
        layout.addWidget(self._build_export_settings_section())
        layout.addSpacing(8)
        layout.addWidget(self._build_sheet_export_section(), 1)

        self._width_input.valueChanged.connect(self._emit_artboard_changed)
        self._height_input.valueChanged.connect(self._emit_artboard_changed)
        self._margin_input.valueChanged.connect(self._emit_artboard_changed)

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

    def _make_spinbox(self, minimum: float, maximum: float, value: float) -> QDoubleSpinBox:
        spinbox = QDoubleSpinBox()
        spinbox.setRange(minimum, maximum)
        spinbox.setDecimals(3)
        spinbox.setSingleStep(0.125)
        spinbox.setValue(value)
        return spinbox

    def _emit_artboard_changed(self) -> None:
        if not self._updating:
            self.artboard_changed.emit(self.artboard())

    def _save_default(self) -> None:
        save_default_artboard(self.artboard())

    def export_settings(self) -> ExportSettings:
        return ExportSettings(
            print_directory=Path(self._print_export_input.text().strip()),
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

        title = QLabel("Export Folders")
        title.setObjectName("sectionTitle")
        self._print_export_input = QLineEdit(str(settings.print_directory))
        self._cut_export_input = QLineEdit(str(settings.cut_directory))
        self._temp_export_input = QLineEdit(str(settings.local_temp_directory))
        self._print_export_input.setPlaceholderText("Print PDF folder")
        self._cut_export_input.setPlaceholderText("Cut PDF folder")
        self._temp_export_input.setPlaceholderText("Local temp folder")

        layout.addWidget(title)
        layout.addLayout(self._folder_row("Print", self._print_export_input))
        layout.addLayout(self._folder_row("Cut", self._cut_export_input))
        layout.addLayout(self._folder_row("Temp", self._temp_export_input))

        save_button = QPushButton("Save Export Settings")
        save_button.clicked.connect(self._save_export_settings)
        layout.addWidget(save_button)
        return section

    def _folder_row(self, label: str, line_edit: QLineEdit) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(QLabel(label))
        row.addWidget(line_edit, 1)
        browse = QPushButton("Browse")
        browse.clicked.connect(lambda _checked=False, target=line_edit: self._browse_for_folder(target))
        row.addWidget(browse)
        return row

    def _browse_for_folder(self, target: QLineEdit) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose Export Folder", target.text())
        if folder:
            target.setText(folder)

    def _save_export_settings(self) -> None:
        save_export_settings(self.export_settings())

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
        export_all = QPushButton("Export All")
        export_all.setToolTip("Exports every pending print and cut PDF using the configured folders.")
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
        preview = sheet.thumbnail if sheet.thumbnail is not None else self._sheet_thumbnail(sheet.ready, sheet.print_exported, sheet.cut_exported)
        thumbnail.setPixmap(preview)

        content = QVBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(6)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        name = f"Sheet {sheet.sheet_index + 1}"
        meta = "Empty" if sheet.item_count == 0 else f"{sheet.item_count} item{'s' if sheet.item_count != 1 else ''}"
        name_label = QLabel(name)
        name_label.setObjectName("sheetName")
        meta_label = QLabel(meta)
        meta_label.setObjectName("sheetMeta")
        top.addWidget(name_label)
        top.addStretch(1)
        top.addWidget(meta_label)

        states = QGridLayout()
        states.setContentsMargins(0, 0, 0, 0)
        states.setHorizontalSpacing(8)
        states.setVerticalSpacing(2)
        for column, (label, ok) in enumerate(
            [
                ("Ready", sheet.ready),
                ("Print", sheet.print_exported),
                ("Cut", sheet.cut_exported),
            ]
        ):
            dot = QLabel("●")
            dot.setProperty("stateDot", "ready" if ok else "warning" if label == "Ready" else "pending")
            dot.setText("*")
            dot.setToolTip(f"{label}: {'done' if ok else 'pending'}")
            text = QLabel(label)
            text.setObjectName("sheetMeta")
            states.addWidget(dot, 0, column)
            states.addWidget(text, 1, column)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        print_button = QPushButton("Export Print")
        cut_button = QPushButton("Export Cut")
        for button in (print_button, cut_button):
            button.setObjectName("smallExportButton")
            button.setToolTip("Exports this sheet using the configured folders.")
            button.setEnabled(sheet.item_count > 0)
        print_button.clicked.connect(lambda _checked=False, index=sheet.sheet_index: self.export_sheet_requested.emit(index, "print"))
        cut_button.clicked.connect(lambda _checked=False, index=sheet.sheet_index: self.export_sheet_requested.emit(index, "cut"))
        actions.addWidget(print_button)
        actions.addWidget(cut_button)

        content.addLayout(top)
        content.addLayout(states)
        content.addLayout(actions)
        content.addStretch(1)
        layout.addWidget(thumbnail)
        layout.addLayout(content, 1)
        return row

    def _sheet_thumbnail(self, ready: bool, print_exported: bool, cut_exported: bool) -> QPixmap:
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
