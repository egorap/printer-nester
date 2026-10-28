"""Local printer support using the same Qt PDF path as RadianFeeder."""
from pathlib import Path
import os
import tempfile
from urllib.request import Request, urlopen
from urllib.parse import urlencode

from printer_nester.core.label_print import order_labels_url

_application = None


def available_printers() -> list[str]:
    from PySide6.QtPrintSupport import QPrinterInfo
    return sorted(QPrinterInfo.availablePrinterNames(), key=str.casefold)


def download_labels(orders: list[str], progress, title: str = "") -> Path:
    url = order_labels_url(orders, os.getenv("API_URL") or "https://api.apkordertracker.com")
    if title:
        url += "?" + urlencode({"header": title})
    descriptor, name = tempfile.mkstemp(prefix="nester-labels-", suffix=".pdf")
    path = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as target:
            with urlopen(Request(url, headers={"Accept": "application/pdf"}), timeout=60) as response:
                total = int(response.headers.get("Content-Length", 0))
                received = 0
                while chunk := response.read(64 * 1024):
                    target.write(chunk)
                    received += len(chunk)
                    progress(min(99, int(received * 100 / total)) if total else 0, "Downloading labels")
        with path.open("rb") as source:
            if source.read(4) != b"%PDF":
                raise ValueError("Label server did not return a PDF")
        return path
    except Exception:
        path.unlink(missing_ok=True)
        raise


def print_label_pdf(path: Path, printer_name: str, orders: list[str], progress) -> str:
    from PySide6.QtCore import QRect, QSize, Qt
    from PySide6.QtGui import QGuiApplication, QPageSize, QPainter
    from PySide6.QtPdf import QPdfDocument
    from PySide6.QtPrintSupport import QPrinter, QPrinterInfo

    global _application
    _application = QGuiApplication.instance() or QGuiApplication([])
    if printer_name not in QPrinterInfo.availablePrinterNames():
        raise ValueError(f"Printer unavailable: {printer_name}")
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setPrinterName(printer_name)
    printer.setResolution(300)
    printer.setDocName(f"Order labels {'; '.join(orders)}")
    printer.setFullPage(True)
    document = QPdfDocument()
    painter = QPainter()
    try:
        if document.load(str(path)) != QPdfDocument.Error.None_ or document.pageCount() < 1:
            raise ValueError("Could not open label PDF")
        printer.setPageSize(QPageSize(document.pagePointSize(0), QPageSize.Unit.Point))
        if not painter.begin(printer):
            raise RuntimeError(f"Could not start print job on {printer_name}")
        for index in range(document.pageCount()):
            if index and not printer.newPage():
                raise RuntimeError("Printer could not start the next page")
            viewport = painter.viewport()
            image = document.render(index, viewport.size().scaled(QSize(4000, 4000), Qt.AspectRatioMode.KeepAspectRatio))
            if image.isNull():
                raise RuntimeError(f"Could not render label page {index + 1}")
            size = image.size().scaled(viewport.size(), Qt.AspectRatioMode.KeepAspectRatio)
            target = QRect(viewport.x() + (viewport.width() - size.width()) // 2,
                           viewport.y() + (viewport.height() - size.height()) // 2,
                           size.width(), size.height())
            painter.drawImage(target, image)
            progress(int(100 * (index + 1) / document.pageCount()), "Sending to printer")
        if not painter.end():
            raise RuntimeError("Printer did not accept the completed job")
        return f"Sent to {printer_name}"
    finally:
        if painter.isActive():
            painter.end()
        document.close()
