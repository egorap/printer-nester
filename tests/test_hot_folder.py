from __future__ import annotations

import json
import os
import time
import zipfile
from pathlib import Path

from PIL import Image

from printer_nester.core.hot_folder import (
    complete_hot_folder_package,
    prepare_hot_folder_packages,
)


def _write_image(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (24, 24), (255, 0, 0, 255)).save(path, dpi=(300, 300))


def _write_order_zip(zip_path: Path, order_number: str = "1001") -> None:
    source_dir = zip_path.parent / "source"
    artwork_path = source_dir / "front.png"
    _write_image(artwork_path)
    manifest = {
        "order_number": order_number,
        "items": [{"file": "artwork/front.png", "quantity": 2}],
    }
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("order.json", json.dumps(manifest))
        archive.write(artwork_path, "artwork/front.png")


def test_prepare_hot_folder_package_extracts_order_and_completes(tmp_path) -> None:
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    _write_order_zip(incoming / "order-1001.zip")

    result = prepare_hot_folder_packages(tmp_path)

    assert result.errors == ()
    assert len(result.packages) == 1
    package = result.packages[0]
    assert package.order.order_number == "1001"
    assert package.order.total_quantity == 2
    assert package.order.items[0].extracted_path.exists()
    assert package.zip_path.parent == tmp_path / "processing"

    completed = complete_hot_folder_package(package, tmp_path)

    assert completed.zip_path.parent == tmp_path / "done"
    assert completed.extract_dir.parent == tmp_path / "done"
    assert (completed.extract_dir / "artwork" / "front.png").exists()


def test_prepare_hot_folder_rejects_unsafe_zip_paths(tmp_path) -> None:
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    with zipfile.ZipFile(incoming / "bad.zip", "w") as archive:
        archive.writestr("order.json", "{}")
        archive.writestr("../escape.png", "not an image")

    result = prepare_hot_folder_packages(tmp_path)

    assert result.packages == ()
    assert len(result.errors) == 1
    assert not (tmp_path / "incoming" / "bad.zip").exists()
    assert any(path.name.startswith("bad") for path in (tmp_path / "error").glob("*.zip"))
    assert any((tmp_path / "error").glob("*.error.txt"))


def test_prepare_hot_folder_skips_packages_younger_than_min_age(tmp_path) -> None:
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    zip_path = incoming / "order-1001.zip"
    _write_order_zip(zip_path)

    too_new = prepare_hot_folder_packages(tmp_path, min_age_seconds=60)

    assert too_new.packages == ()
    assert zip_path.exists()

    old_time = time.time() - 120
    os.utime(zip_path, (old_time, old_time))

    ready = prepare_hot_folder_packages(tmp_path, min_age_seconds=60)

    assert len(ready.packages) == 1
    assert not zip_path.exists()