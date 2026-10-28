import os
import time
from concurrent.futures import Future
from queue import Queue

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import fitz
from PIL import Image
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from printer_nester.core.artwork_import import ArtworkKind
from printer_nester.core.pdf_export import ExportArtwork, ExportSheet, ExportSettings
from printer_nester.ui.job_queue import JobQueue


def test_real_process_exports_remain_responsive_and_preserve_white_and_mask(tmp_path):
    app = QApplication.instance() or QApplication([])
    artwork = tmp_path / "art.png"
    mask = tmp_path / "mask.png"
    Image.new("RGB", (100, 100), "red").save(artwork)
    mask_image = Image.new("L", (100, 100), 255)
    mask_image.paste(0, (0, 0, 50, 100))
    mask_image.save(mask)
    sheet = ExportSheet(0, 72, 72, [ExportArtwork(artwork, ArtworkKind.RASTER,
        (0, 0, 72, 72), mask_path=mask, mask_relative_rect=(0, 0, 1, 1))], [], [])
    settings = ExportSettings(tmp_path, tmp_path, tmp_path, tmp_path / "temp")
    queue = JobQueue()
    ticks = []
    heartbeat = QTimer()
    heartbeat.setInterval(10)
    heartbeat.timeout.connect(lambda: ticks.append(True))
    heartbeat.start()
    try:
        keys = [queue.add(str(index), dict(operation="jig", sheet=sheet,
                    destination=tmp_path / f"{index:03d}_jig_sheet.pdf", settings=settings))
                for index in (1, 2)]
        deadline = time.monotonic() + 45
        while queue.busy() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert not queue.busy()
        assert len(ticks) >= 2
        for key in keys:
            assert queue.jobs[key]["error"] == ""
            assert queue.jobs[key]["bar"].value() == 100
            with fitz.open(queue.jobs[key]["result"]["result"].path) as document:
                objects = "".join(document.xref_object(i) for i in range(1, document.xref_length()))
                assert "RDG_WHITE" in objects
                pixmap = document[0].get_pixmap()
                assert pixmap.pixel(18, 36)[0] > 200
                assert pixmap.pixel(54, 36)[:3] == (255, 255, 255)
    finally:
        heartbeat.stop()
        queue.shutdown()


def test_label_downloads_finishing_out_of_order_are_printed_in_order(tmp_path):
    QApplication.instance() or QApplication([])

    class Pool:
        def __init__(self):
            self.calls = []

        def submit(self, function, key, task):
            future = Future()
            self.calls.append((key, dict(task), future))
            return future

    queue = JobQueue()
    queue._pool = Pool()
    queue._print_pool = Pool()
    queue._messages = Queue()
    first = queue.add("1", dict(operation="download", orders=["B"], printer="local"))
    second = queue.add("2", dict(operation="download", orders=["A"], printer="local"))
    queue._pool.calls[1][2].set_result(tmp_path / "second.pdf")
    queue._poll()
    assert not queue._print_pool.calls
    queue._pool.calls[0][2].set_result(tmp_path / "first.pdf")
    queue._poll()
    assert [call[0] for call in queue._print_pool.calls] == [first, second]


def test_download_labels_encodes_header_and_keeps_order(monkeypatch, tmp_path):
    from io import BytesIO
    from urllib.parse import parse_qs, urlsplit
    from printer_nester.core import local_labels
    seen = []

    class Response(BytesIO):
        headers = {"Content-Length": "9"}

    def fetch(request, timeout):
        seen.append(request.full_url)
        return Response(b"%PDF-test")

    monkeypatch.setattr(local_labels, "urlopen", fetch)
    path = local_labels.download_labels(["200", "100"], lambda *_: None, "042_jig sheet & print.pdf")
    try:
        url = urlsplit(seen[0])
        assert url.path.endswith("/order-labels2/200_100")
        assert parse_qs(url.query)["header"] == ["042_jig sheet & print.pdf"]
    finally:
        path.unlink()


def test_receipt_retry_does_not_export_a_second_pdf(tmp_path):
    from printer_nester.core.pdf_export import ExportResult
    QApplication.instance() or QApplication([])

    class Pool:
        def __init__(self):
            self.calls = []

        def submit(self, function, key, task):
            future = Future()
            self.calls.append((dict(task), future))
            return future

    queue = JobQueue()
    queue._pool = Pool()
    queue._messages = Queue()
    key = queue.add("Export", {"operation": "jig", "receipts": []})
    result = ExportResult(tmp_path / "042_jig_sheet.pdf", 100)
    queue._pool.calls[0][1].set_result({"result": result, "receipt_error": "disk full"})
    queue._poll()
    assert queue.jobs[key]["error"]
    queue.tree.setCurrentItem(queue.jobs[key]["row"])
    queue.retry_selected()
    assert queue._pool.calls[1][0]["operation"] == "receipt"
    assert queue._pool.calls[1][0]["saved_result"].path == result.path
    queue._pool.calls[1][1].set_result({"result": result, "receipt_error": ""})
    queue._poll()
    assert not queue.jobs[key]["error"]
