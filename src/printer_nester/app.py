from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from printer_nester.ui.main_window import MainWindow
from printer_nester.ui.qt_settings import configure_qt_image_limits


def main() -> int:
    configure_qt_image_limits()

    app = QApplication(sys.argv)
    app.setOrganizationName("PrinterNester")
    app.setApplicationName("Printer Nester")

    window = MainWindow()
    window.show()

    return app.exec()
