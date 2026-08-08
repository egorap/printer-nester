# Hot Folder Order Packages

This app accepts jig-layout orders from a hot folder as ZIP packages. Each ZIP must contain a manifest named `order.json` at the root and all referenced artwork files inside the same ZIP.

## Folder Layout

Configure one hot-folder root in the app. The app creates these child folders:

```text
hot-folder/
  incoming/    # Submit new ZIP packages here
  processing/  # App-owned temporary working area
  done/        # Accepted packages and extracted artwork
  error/       # Rejected packages and error reports
```

Submitters should only write to `incoming/`. Do not write directly to `processing/`, `done/`, or `error/`.

When a Jig tab is active and a hot-folder root is configured, the app polls `incoming/` automatically. The Process Orders button can still be used to force an immediate scan.

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

The app does not generate packing labels yet. It only tracks which order numbers landed on each exported jig sheet.

## Nesting Policy

Hot-folder processing is only for jig profiles.

Orders are grouped by `order_number`. Before importing an order, the app counts the remaining jig slots on the current sheet:

If the whole order fits in the remaining slots, it is placed there.

If it does not fit, the app reserves the remaining slots and starts the order on the next jig sheet.

If one order is larger than a single jig sheet, it fills consecutive sheets in item order. Later orders may use remaining slots only after the oversized order has finished.

## Output Correlation

When a jig sheet is exported, the app writes the PDF and, when that sheet contains hot-folder orders, a sidecar JSON file beside it:

```text
jig_sheet_001.pdf
jig_sheet_001_orders.json
```

Example sidecar:

```json
{
  "sheet_index": 0,
  "sheet_number": 1,
  "sheet_file": "jig_sheet_001.pdf",
  "orders": ["10482", "10483"]
}
```

The PDF exporter never overwrites existing files. If a name already exists, the new export receives a numeric suffix, and the sidecar follows that actual PDF name.
## Label Printing

Jig sheets with hot-folder orders show a `Print Labels` button beside `Export Jig`. The button prints labels for the order numbers currently placed on that sheet.

Required environment variables:

```text
API_URL          # Base OrderFeeder API URL. The app calls {API_URL}/order-labels2/{order_numbers}
PRINTNODE_KEY   # PrintNode API key used for printer lookup and print job submission
```

Use `Refresh Printers` in the Jig layout panel to pull the available PrintNode printers. The selected printer id is saved in app settings and reused in future sessions. A printer id can also be typed manually if the API key is not available on the setup workstation.

The app submits a PrintNode `pdf_uri` job for the same order-label PDF endpoint used by OrderFeeder. Label generation is still owned by that service; Printer Nester only selects the sheet order numbers and sends the print job.
