from __future__ import annotations

import base64

import pytest

from printer_nester.core import label_print


def test_order_labels_url_uses_api_url_and_order_numbers(monkeypatch) -> None:
    monkeypatch.setenv("API_URL", "https://orders.example/api/")

    assert (
        label_print.order_labels_url(["1001", "1002"])
        == "https://orders.example/api/order-labels2/1001_1002"
    )


def test_authorization_header_accepts_raw_printnode_key(monkeypatch) -> None:
    monkeypatch.setenv("PRINTNODE_KEY", "secret-key")

    assert label_print._authorization_header() == (
        "Basic "
        + base64.b64encode("secret-key:".encode("utf-8")).decode("ascii")
    )


def test_print_order_labels_posts_pdf_url(monkeypatch) -> None:
    calls = []

    def fake_request(method, path, *, payload=None, api_key=None):
        calls.append((method, path, payload, api_key))
        return 12345

    monkeypatch.setattr(label_print, "_printnode_request", fake_request)

    result = label_print.print_order_labels(
        "456",
        ["1001", "1002"],
        api_key="secret",
        api_url="https://orders.example/api",
    )

    assert result == 12345
    assert calls == [
        (
            "POST",
            "/printjobs",
            {
                "printerId": 456,
                "title": "Order labels 1001_1002",
                "source": "Printer Nester",
                "contentType": "pdf_uri",
                "content": "https://orders.example/api/order-labels2/1001_1002",
            },
            "secret",
        )
    ]


def test_order_labels_url_requires_orders() -> None:
    with pytest.raises(ValueError, match="No order numbers"):
        label_print.order_labels_url([], api_url="https://orders.example")
