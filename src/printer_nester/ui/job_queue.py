from concurrent.futures import ProcessPoolExecutor, Future
import multiprocessing
from queue import Empty
from uuid import uuid4

from PySide6.QtCore import QTimer, Signal, Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QTreeWidget, QTreeWidgetItem, QProgressBar, QPushButton

from printer_nester.core.background_jobs import initialize_worker, run_job


class JobQueue(QWidget):
    finished = Signal(str, object, str)
    restarted = Signal(str)
    cleared = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.jobs = {}
        self._pool = None
        self._print_pool = None
        self._messages = None
        self._label_order = []
        layout = QVBoxLayout(self)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Job", "Progress", "Status"])
        self.tree.setColumnWidth(0, 280)
        self.tree.setColumnWidth(1, 160)
        self.tree.setRootIsDecorated(False)
        layout.addWidget(self.tree)
        retry = QPushButton("Retry Selected")
        retry.clicked.connect(self.retry_selected)
        layout.addWidget(retry, alignment=Qt.AlignmentFlag.AlignRight)
        clear = QPushButton("Clear Completed")
        clear.clicked.connect(self.clear_completed)
        layout.addWidget(clear, alignment=Qt.AlignmentFlag.AlignRight)
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._poll)

    def _start_pools(self):
        if self._pool is not None:
            return
        context = multiprocessing.get_context("spawn")
        self._messages = context.Queue()
        # Two renderers limit peak memory for large 300-DPI jig pages.
        self._pool = ProcessPoolExecutor(2, mp_context=context, initializer=initialize_worker, initargs=(self._messages,))
        self._print_pool = ProcessPoolExecutor(1, mp_context=context, initializer=initialize_worker, initargs=(self._messages,))
        self._timer.start()

    def add(self, title, task, *, dependency=None):
        self._start_pools()
        key = uuid4().hex
        row = QTreeWidgetItem([title, "", "Waiting for PDF" if dependency else "Queued"])
        self.tree.addTopLevelItem(row)
        bar = QProgressBar()
        bar.setRange(0, 100)
        self.tree.setItemWidget(row, 1, bar)
        self.jobs[key] = dict(task=task, row=row, bar=bar, future=None,
                              done=False, error="", dependency=dependency, result=None)
        if task["operation"] == "download":
            self._label_order.append(key)
        if dependency is None:
            self._submit(key)
        return key

    def _submit(self, key):
        job = self.jobs[key]
        pool = self._print_pool if job["task"]["operation"] == "print" else self._pool
        try:
            job["future"] = pool.submit(run_job, key, job["task"])
        except Exception as error:
            future = Future()
            future.set_exception(error)
            job["future"] = future

    def _finish(self, key, result, error):
        job = self.jobs[key]
        job.update(done=True, error=error, result=result, future=None)
        job["row"].setText(2, error or ("Sent to printer" if job["task"]["operation"] == "print" else "Exported"))
        job["row"].setToolTip(2, error or str(result))
        if not error:
            job["bar"].setValue(100)
        self.finished.emit(key, result, error)

    def _poll(self):
        try:
            while True:
                key, value, stage = self._messages.get_nowait()
                job = self.jobs.get(key)
                if job and not job["done"]:
                    job["bar"].setValue(value)
                    job["row"].setText(2, stage)
        except Empty:
            pass
        for key, job in list(self.jobs.items()):
            if job["done"]:
                continue
            dependency = job["dependency"]
            if dependency:
                previous = self.jobs[dependency]
                if not previous["done"]:
                    continue
                if previous["error"]:
                    self._finish(key, None, "PDF export failed; labels not sent")
                    continue
                job["dependency"] = None
                if job["task"].get("title_page"):
                    job["task"]["title"] = previous["result"]["result"].path.name
                self._submit(key)
            future = job["future"]
            if future is None or not future.done():
                continue
            job["future"] = None
            try:
                result = future.result()
            except Exception as error:
                self._finish(key, None, str(error))
                continue
            if job["task"]["operation"] == "download":
                job["task"] = {**job["task"], "operation": "print", "path": result}
                job["row"].setText(2, "Downloaded; waiting for printer")
                job["bar"].setValue(0)
                job["ready_to_print"] = True
            else:
                receipt_error = result.get("receipt_error", "") if isinstance(result, dict) else ""
                if receipt_error:
                    job["task"] = {**job["task"], "operation": "receipt", "saved_result": result["result"]}
                self._finish(key, result, f"PDF saved; recovery update failed: {receipt_error}" if receipt_error else "")
                if job["task"]["operation"] == "print":
                    job["task"]["path"].unlink(missing_ok=True)
        # Download in parallel, submit to the local spooler in request order.
        while self._label_order:
            key = self._label_order[0]
            job = self.jobs[key]
            if job["done"]:
                self._label_order.pop(0)
            elif job.pop("ready_to_print", False):
                self._label_order.pop(0)
                self._submit(key)
            else:
                break

    def busy(self):
        return any(not job["done"] for job in self.jobs.values())

    def retry_selected(self):
        row = self.tree.currentItem()
        for key, job in self.jobs.items():
            if job["row"] is row and job["done"] and job["error"]:
                job.update(done=False, error="", result=None)
                job["row"].setText(2, "Queued")
                job["bar"].setValue(0)
                if job["task"]["operation"] == "download":
                    self._label_order.append(key)
                self.restarted.emit(key)
                if job["dependency"] is None:
                    self._submit(key)
                break

    def clear_completed(self):
        # Retain dependency records while jobs are running.
        if self.busy():
            return
        dependencies = {job["dependency"] for job in self.jobs.values() if job["error"]}
        removed = []
        for key, job in list(self.jobs.items()):
            if not job["error"] and key not in dependencies:
                self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(job["row"]))
                del self.jobs[key]
                removed.append(key)
        self.cleared.emit(removed)

    def shutdown(self):
        self._timer.stop()
        for pool in (self._pool, self._print_pool):
            if pool is not None:
                pool.shutdown(wait=False)
        if self._messages is not None:
            self._messages.close()
