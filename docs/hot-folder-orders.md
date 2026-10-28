# Hot Folder Order Packages

This app accepts jig-layout orders from a hot folder as ZIP packages. Each ZIP must contain a manifest named `order.json` at the root and all referenced artwork files inside the same ZIP.

## Folder Layout

Configure one hot-folder root in the app. The app creates these child folders:

```text
hot-folder/
  incoming/    # Submit new ZIP packages here
  processing/  # Pending orders, extracted sources, and export-progress.sqlite3
  done/        # ZIP packages whose copies have all been exported
  error/       # Rejected packages and error reports
```

Submitters should only write to `incoming/`. Do not write directly to `processing/`, `done/`, or `error/`.

When a Jig tab is active and a hot-folder root is configured, the app polls `incoming/` automatically. The Process Orders button can still be used to force an immediate scan.

Import is not completion. Pending packages stay in `processing/` and are restored
automatically on startup: if the configured hot folder contains pending ZIPs,
the app opens a Jig tab using the saved default jig settings and resumes them.
They are also restored when a Jig tab polls the same configured hot folder. Each expanded quantity
copy has its own durable export receipt. For an order spanning multiple sheets,
only copies not yet successfully exported are restored. Deleting artwork from the
viewport does not complete it; unexported copies return in the next session.

Once all copies have been exported, the ZIP moves to `done/`. Extracted artwork
and the progress database remain in `processing/` so open sheets and concurrent
exports retain stable source paths. Do not clear this directory between sessions.
Older ZIPs already in `done/` are not automatically replayed, since their past
export status cannot be inferred.

## Atomic Submission

Build the complete ZIP outside `incoming/`, then move or rename the finished file into `incoming/`.

Do not stream a ZIP directly into `incoming/`. The app treats any `*.zip` in that folder as ready to process.

Recommended:

```text
build/order-10482.zip.tmp
rename or move -> hot-folder/incoming/order-10482.zip
```

## ZIP Structure

Example package:

```text
order-10482.zip
  order.json
  artwork/front.png
  artwork/back.pdf
```

Supported artwork extensions are:

```text
.png .jpg .jpeg .pdf .ai
```

`.ai` files are treated as PDF-compatible artwork.

## Manifest Schema

`order.json`:

```json
{
  "order_number": "10482",
  "items": [
    {
      "file": "artwork/front.png",
      "quantity": 1
    },
    {
      "file": "artwork/back.pdf",
      "quantity": 2
    }
  ],
  "label": {
    "copies": 1,
    "notes": "Optional future label metadata"
  }
}
```

Required fields:

`order_number`: String or number. It is stored as text and used for sheet correlation.

`items`: Non-empty array of artwork records.

`items[].file`: Relative path inside the ZIP. Absolute paths and `..` paths are rejected.

`items[].quantity`: Optional positive integer. Defaults to `1`.

The app tracks which order numbers land on each jig sheet and prints their packing labels on a locally installed printer.

## Nesting Policy

Hot-folder processing is only for jig profiles.

Orders are grouped by `order_number`. Before importing an order, the app counts the remaining jig slots on the current sheet:

If the whole order fits in the remaining slots, it is placed there.

If it does not fit, the app reserves the remaining slots and starts the order on the next jig sheet.

If one order is larger than a single jig sheet, it fills consecutive sheets in item order. Later orders may use remaining slots only after the oversized order has finished.

## Label Printing

Jig sheets with hot-folder orders show a `Print Labels` button beside `Export Jig`. The button prints labels for the order numbers currently placed on that sheet.

`Export All` writes each pending jig PDF and then submits the labels for the orders on that sheet. If label submission fails, the PDF remains marked as exported and the next `Export All` retries only the missing label job, avoiding a duplicate print file. No order-correlation JSON files are written to the export folder.

Jig PDFs use index-first names such as `042_jig_sheet.pdf`. Existing files are
never overwritten; collisions receive a suffix such as `042_jig_sheet_001.pdf`.

Optional environment variable:

```text
API_URL          # Defaults to https://api.apkordertracker.com
```

Use `Refresh Printers` in the Jig panel to list printers installed on this computer.
Select the label printer by name; this selection is saved separately from the old
PrintNode configuration. `PRINTNODE_KEY` is no longer needed for local printing.

The app downloads `/order-labels2/{order_numbers}` with order numbers in sheet
placement order. Enable `Label title page (print filename)` to pass the actual
committed PDF basename through the endpoint's `header` query parameter. A title
page requires a successful export so that any collision suffix is known.

## Background Queue

The Export / Label Queue opens when work is queued and is also available from
the View menu. Two PDF rendering/download workers run in separate processes,
keeping the editor responsive and limiting the memory used by large jig renders.
Masking and PDF creation run in these workers. Progress shows rendering, saving,
and completion stages. Label jobs show download progress and page submission.
Label downloads can run concurrently; printer submissions preserve queue order.
`Sent to printer` means accepted by the local spooler, not confirmed on paper.

Failed jobs remain visible with an error and can be retried using `Retry Selected`.
An export receipt failure after a successful file save retries the receipt only,
without writing another PDF. The app waits for active jobs before allowing the
window to close. Unexported hot-folder copies remain recoverable if the process
is forcibly terminated. A crash between destination commit and receipt commit
can cause that copy to be offered again; it is never silently discarded.
