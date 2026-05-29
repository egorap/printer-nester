# Printer Nester

Python desktop app for preparing artwork for print and cut workflows.

The project is intentionally split so UI code stays separate from the core print/cut logic.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
python -m printer_nester
```

## Project Layout

```text
src/printer_nester/
  app.py              PySide6 application bootstrap
  __main__.py         python -m printer_nester entry point
  core/               UI-independent print/cut/nesting logic
  ui/                 PySide6 windows and widgets
```
