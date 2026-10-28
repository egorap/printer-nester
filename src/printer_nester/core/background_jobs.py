"""Picklable worker entry points; no scene objects are passed into workers."""
from printer_nester.core.pdf_export import export_jig_layout_pdf, export_sheet_pdf, export_jig_mockup_pdf
from printer_nester.core.hot_folder import record_exported_items
from printer_nester.core.local_labels import download_labels, print_label_pdf

_progress_queue = None


def initialize_worker(queue):
    global _progress_queue
    _progress_queue = queue


def run_job(job_id: str, task: dict):
    def progress(value, stage):
        if _progress_queue is not None:
            _progress_queue.put((job_id, value, stage))

    operation = task["operation"]
    if operation == "receipt":
        record_exported_items(task.get("receipts", []), task["saved_result"].path)
        return {"result": task["saved_result"], "receipt_error": ""}
    if operation == "download":
        progress(0, "Downloading labels")
        return download_labels(task["orders"], progress, task.get("title", ""))
    if operation == "print":
        progress(0, "Sending to printer")
        return print_label_pdf(task["path"], task["printer"], task["orders"], progress)
    if operation == "mockup":
        progress(10, "Rendering jig mockup")
        result = export_jig_mockup_pdf(task["layout"], task["destination"], task["settings"].local_temp_directory)
    elif operation == "jig":
        result = export_jig_layout_pdf([task["sheet"]], task["destination"],
                                       task["settings"].local_temp_directory, progress=progress)
    else:
        result = export_sheet_pdf(task["sheet"], task["kind"], task["settings"],
                                  export_number=task["number"], progress=progress)
    progress(95, "Recording completed items")
    try:
        record_exported_items(task.get("receipts", []), result.path)
    except Exception as error:
        # Return the committed PDF even if bookkeeping needs a retry.
        return {"result": result, "receipt_error": str(error)}
    return {"result": result, "receipt_error": ""}
