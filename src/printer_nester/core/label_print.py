from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import os
from urllib import error, request
from urllib.parse import quote


PRINTNODE_API_URL = "https://api.printnode.com"


@dataclass(frozen=True, slots=True)
class PrintNodePrinter:
    printer_id: str
    name: str
    computer_name: str = ""
    state: str = ""

    @property
    def display_name(self) -> str:
        parts = [self.name]
        if self.computer_name:
            parts.append(self.computer_name)
        if self.state:
            parts.append(self.state)
        return " - ".join(parts)


def list_printnode_printers(api_key: str | None = None) -> list[PrintNodePrinter]:
    response = _printnode_request("GET", "/printers", api_key=api_key)
    if not isinstance(response, list):
        raise ValueError("PrintNode printers response was not a list")

    printers: list[PrintNodePrinter] = []
    for printer in response:
        if not isinstance(printer, dict):
            continue
        printer_id = str(printer.get("id", "")).strip()
        if not printer_id:
            continue
        computer = printer.get("computer")
        computer_name = (
            str(computer.get("name", "")).strip()
            if isinstance(computer, dict)
            else ""
        )
        printers.append(
            PrintNodePrinter(
                printer_id=printer_id,
                name=str(printer.get("name", printer_id)).strip() or printer_id,
                computer_name=computer_name,
                state=str(printer.get("state", "")).strip(),
            )
        )

    return printers


def order_labels_url(order_numbers: list[str], api_url: str | None = None) -> str:
    if not order_numbers:
        raise ValueError("No order numbers are available for label printing")

    base_url = (api_url if api_url is not None else os.getenv("API_URL", "")).strip()
    if not base_url:
        raise ValueError("API_URL is not configured")

    order_segment = "_".join(
        quote(str(order_number), safe="") for order_number in order_numbers
    )
    return f"{base_url.rstrip('/')}/order-labels2/{order_segment}"


def print_order_labels(
    printer_id: str,
    order_numbers: list[str],
    *,
    api_key: str | None = None,
    api_url: str | None = None,
) -> object:
    if not str(printer_id).strip():
        raise ValueError("Label printer is not configured")

    pdf_url = order_labels_url(order_numbers, api_url=api_url)
    payload = {
        "printerId": _json_printer_id(printer_id),
        "title": f"Order labels {'_'.join(order_numbers)}",
        "source": "Printer Nester",
        "contentType": "pdf_uri",
        "content": pdf_url,
    }
    return _printnode_request("POST", "/printjobs", payload=payload, api_key=api_key)


def _printnode_request(
    method: str,
    path: str,
    *,
    payload: dict[str, object] | None = None,
    api_key: str | None = None,
) -> object:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request_object = request.Request(
        f"{PRINTNODE_API_URL}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": _authorization_header(api_key),
            "Content-Type": "application/json",
        },
    )
    try:
        with request.urlopen(request_object, timeout=15) as response:
            body = response.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"PrintNode request failed: {exc.code} {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"PrintNode request failed: {exc.reason}") from exc

    return json.loads(body) if body.strip() else None


def _authorization_header(api_key: str | None = None) -> str:
    key = (api_key if api_key is not None else os.getenv("PRINTNODE_KEY", "")).strip()
    if not key:
        raise ValueError("PRINTNODE_KEY is not configured")
    if key.lower().startswith("basic "):
        return key
    token = base64.b64encode(f"{key}:".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def _json_printer_id(printer_id: str) -> int | str:
    printer_id = str(printer_id).strip()
    return int(printer_id) if printer_id.isdecimal() else printer_id
