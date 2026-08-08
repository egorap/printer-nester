from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import shutil
import time
import zipfile

from printer_nester.core.artwork_import import SUPPORTED_ARTWORK_EXTENSIONS


MANIFEST_NAME = "order.json"


@dataclass(frozen=True, slots=True)
class HotFolderPaths:
    root: Path
    incoming: Path
    processing: Path
    done: Path
    error: Path


@dataclass(frozen=True, slots=True)
class HotFolderArtwork:
    relative_path: str
    extracted_path: Path
    quantity: int = 1


@dataclass(frozen=True, slots=True)
class HotFolderOrder:
    order_number: str
    items: tuple[HotFolderArtwork, ...]

    @property
    def total_quantity(self) -> int:
        return sum(item.quantity for item in self.items)

    def expanded_artwork_paths(self) -> list[Path]:
        paths: list[Path] = []
        for item in self.items:
            paths.extend([item.extracted_path] * item.quantity)
        return paths


@dataclass(frozen=True, slots=True)
class PreparedHotFolderPackage:
    order: HotFolderOrder
    zip_path: Path
    extract_dir: Path


@dataclass(frozen=True, slots=True)
class CompletedHotFolderPackage:
    zip_path: Path
    extract_dir: Path


@dataclass(frozen=True, slots=True)
class HotFolderScanResult:
    packages: tuple[PreparedHotFolderPackage, ...]
    errors: tuple[str, ...]


def ensure_hot_folder_structure(root: Path) -> HotFolderPaths:
    root = Path(root)
    paths = HotFolderPaths(
        root=root,
        incoming=root / "incoming",
        processing=root / "processing",
        done=root / "done",
        error=root / "error",
    )
    for directory in (
        paths.root,
        paths.incoming,
        paths.processing,
        paths.done,
        paths.error,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    return paths


def prepare_hot_folder_packages(
    root: Path, min_age_seconds: float = 0.0
) -> HotFolderScanResult:
    paths = ensure_hot_folder_structure(root)
    packages: list[PreparedHotFolderPackage] = []
    errors: list[str] = []

    for source_zip in sorted(paths.incoming.glob("*.zip")):
        if not _is_ready_package(source_zip, min_age_seconds):
            continue
        processing_zip: Path | None = None
        extract_dir: Path | None = None
        try:
            processing_zip = _move_unique(source_zip, paths.processing)
            extract_dir = _unique_directory(paths.processing / processing_zip.stem)
            order = _extract_order_zip(processing_zip, extract_dir)
            packages.append(
                PreparedHotFolderPackage(
                    order=order,
                    zip_path=processing_zip,
                    extract_dir=extract_dir,
                )
            )
        except Exception as error:
            message = f"{source_zip.name}: {error}"
            errors.append(message)
            if extract_dir is not None and extract_dir.exists():
                shutil.rmtree(extract_dir)
            failed_zip = processing_zip if processing_zip is not None else source_zip
            if failed_zip.exists():
                _move_failed_zip(failed_zip, paths.error, message)

    return HotFolderScanResult(packages=tuple(packages), errors=tuple(errors))


def complete_hot_folder_package(
    package: PreparedHotFolderPackage, root: Path
) -> CompletedHotFolderPackage:
    paths = ensure_hot_folder_structure(root)
    completed_zip = _move_unique(package.zip_path, paths.done)
    completed_extract_dir = _move_unique_directory(package.extract_dir, paths.done)
    return CompletedHotFolderPackage(
        zip_path=completed_zip,
        extract_dir=completed_extract_dir,
    )


def fail_hot_folder_package(
    package: PreparedHotFolderPackage, root: Path, message: str
) -> None:
    paths = ensure_hot_folder_structure(root)
    if package.extract_dir.exists():
        shutil.rmtree(package.extract_dir)
    if package.zip_path.exists():
        _move_failed_zip(package.zip_path, paths.error, message)



def _is_ready_package(path: Path, min_age_seconds: float) -> bool:
    if min_age_seconds <= 0:
        return True
    try:
        return time.time() - path.stat().st_mtime >= min_age_seconds
    except FileNotFoundError:
        return False
def _extract_order_zip(zip_path: Path, extract_dir: Path) -> HotFolderOrder:
    with zipfile.ZipFile(zip_path) as archive:
        _validate_zip_members(archive)
        if MANIFEST_NAME not in archive.namelist():
            raise ValueError(f"Missing required {MANIFEST_NAME}")

        manifest = json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
        archive.extractall(extract_dir)

    return _read_manifest(manifest, extract_dir)


def _read_manifest(manifest: object, extract_dir: Path) -> HotFolderOrder:
    if not isinstance(manifest, dict):
        raise ValueError("Manifest must be a JSON object")

    order_number = str(manifest.get("order_number", "")).strip()
    if not order_number:
        raise ValueError("Manifest order_number is required")

    raw_items = manifest.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("Manifest items must be a non-empty list")

    items: list[HotFolderArtwork] = []
    for index, raw_item in enumerate(raw_items, start=1):
        if not isinstance(raw_item, dict):
            raise ValueError(f"Manifest item {index} must be an object")

        relative_path = _safe_manifest_path(str(raw_item.get("file", "")).strip())
        quantity = int(raw_item.get("quantity", 1))
        if quantity < 1:
            raise ValueError(f"Manifest item {index} quantity must be at least 1")
        if Path(relative_path).suffix.lower() not in SUPPORTED_ARTWORK_EXTENSIONS:
            raise ValueError(f"Unsupported artwork format: {relative_path}")

        extracted_path = extract_dir / Path(relative_path)
        if not extracted_path.is_file():
            raise ValueError(f"Manifest item file is missing: {relative_path}")
        items.append(
            HotFolderArtwork(
                relative_path=relative_path,
                extracted_path=extracted_path,
                quantity=quantity,
            )
        )

    return HotFolderOrder(order_number=order_number, items=tuple(items))


def _validate_zip_members(archive: zipfile.ZipFile) -> None:
    for member_name in archive.namelist():
        if member_name.endswith("/"):
            continue
        _safe_manifest_path(member_name)


def _safe_manifest_path(path: str) -> str:
    if not path:
        raise ValueError("Artwork file path is required")

    normalized = PurePosixPath(path.replace("\\", "/"))
    has_unsafe_part = any(
        part in ("", ".", "..") or ":" in part for part in normalized.parts
    )
    if normalized.is_absolute() or has_unsafe_part:
        raise ValueError(f"Unsafe package path: {path}")
    return normalized.as_posix()


def _unique_directory(base: Path) -> Path:
    for candidate in _path_candidates(base):
        try:
            candidate.mkdir(parents=True, exist_ok=False)
            return candidate
        except FileExistsError:
            continue
    raise FileExistsError(f"No available folder near {base}")


def _move_unique(source: Path, destination_dir: Path) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    base = destination_dir / source.name
    for candidate in _path_candidates(base):
        try:
            source.rename(candidate)
            return candidate
        except FileExistsError:
            continue
    raise FileExistsError(f"No available destination for {source.name}")


def _move_unique_directory(source: Path, destination_dir: Path) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    base = destination_dir / source.name
    for candidate in _path_candidates(base):
        try:
            source.rename(candidate)
            return candidate
        except FileExistsError:
            continue
    raise FileExistsError(f"No available destination for {source.name}")


def _move_failed_zip(source: Path, error_dir: Path, message: str) -> None:
    failed_zip = _move_unique(source, error_dir)
    error_path = _next_available_path(failed_zip.with_suffix(".error.txt"))
    error_path.write_text(message + "\n", encoding="utf-8")


def _next_available_path(base: Path) -> Path:
    for candidate in _path_candidates(base):
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"No available path near {base}")


def _path_candidates(base: Path):
    yield base
    for index in range(1, 10_000):
        yield base.with_name(f"{base.stem}_{index:03d}{base.suffix}")
