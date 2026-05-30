from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
import os
import shutil
import tempfile
import uuid

import fitz

from printer_nester.core.artwork_import import ArtworkKind
from printer_nester.core.markers import MARKER_DIAMETER_MM, MM_PER_INCH


MIN_FREE_SPACE_BYTES = 10 * 1024 * 1024
EXPORT_MARKER_PADDING_IN = 0.5


class ExportKind(StrEnum):
    PRINT = "print"
    CUT = "cut"


@dataclass(frozen=True, slots=True)
class ExportSettings:
    print_directory: Path
    cut_directory: Path
    local_temp_directory: Path


@dataclass(frozen=True, slots=True)
class ExportArtwork:
    path: Path
    kind: ArtworkKind
    rect_points: tuple[float, float, float, float]
    rotation: int = 0
    page_index: int = 0


@dataclass(frozen=True, slots=True)
class ExportSegment:
    x1: float
    y1: float
    x2: float
    y2: float


@dataclass(frozen=True, slots=True)
class ExportMarker:
    center_x: float
    center_y: float
    diameter_mm: float = MARKER_DIAMETER_MM


@dataclass(frozen=True, slots=True)
class ExportSheet:
    sheet_index: int
    width_points: float
    height_points: float
    artworks: list[ExportArtwork]
    cut_segments: list[ExportSegment]
    markers: list[ExportMarker]


@dataclass(frozen=True, slots=True)
class ExportResult:
    path: Path
    bytes_written: int


def export_sheet_pdf(sheet: ExportSheet, kind: ExportKind, settings: ExportSettings) -> ExportResult:
    destination_dir = settings.print_directory if kind == ExportKind.PRINT else settings.cut_directory
    destination_name = f"sheet_{sheet.sheet_index + 1:03d}_{kind.value}.pdf"
    destination = destination_dir / destination_name

    _ensure_configured_directory(destination_dir, f"{kind.value} export directory")
    _ensure_configured_directory(settings.local_temp_directory, "local temp export directory")
    settings.local_temp_directory.mkdir(parents=True, exist_ok=True)
    destination_dir.mkdir(parents=True, exist_ok=True)
    _ensure_directory_writable(settings.local_temp_directory)
    _ensure_directory_writable(destination_dir)

    local_path = _write_local_pdf(sheet, kind, settings.local_temp_directory)
    try:
        final_path = _commit_file(local_path, destination)
    finally:
        if local_path.exists():
            local_path.unlink()

    return ExportResult(path=final_path, bytes_written=final_path.stat().st_size)


def _ensure_configured_directory(directory: Path, label: str) -> None:
    if not str(directory).strip() or directory == Path("."):
        raise ValueError(f"{label} is not configured")


def _write_local_pdf(sheet: ExportSheet, kind: ExportKind, temp_dir: Path) -> Path:
    _ensure_free_space(temp_dir, MIN_FREE_SPACE_BYTES)
    local_path = temp_dir / f"printer_nester_{kind.value}_{uuid.uuid4().hex}.pdf"
    sheet = _trim_sheet_to_markers(sheet)

    document = fitz.open()
    page = document.new_page(width=sheet.width_points, height=sheet.height_points)
    marker_oc = document.add_ocg("Markers")
    if kind == ExportKind.PRINT:
        artwork_oc = document.add_ocg("Artwork")
        _draw_artwork_layer(page, sheet.artworks, artwork_oc)
    else:
        cut_oc = document.add_ocg("Cut Lines")
        _draw_cut_layer(page, sheet.cut_segments, cut_oc)
    _draw_marker_layer(page, sheet.markers, marker_oc)

    document.save(local_path)
    document.close()
    _validate_pdf(local_path)
    return local_path


def _trim_sheet_to_markers(sheet: ExportSheet) -> ExportSheet:
    if not sheet.markers:
        return sheet

    padding_points = EXPORT_MARKER_PADDING_IN * 72
    marker_bounds = []
    for marker in sheet.markers:
        radius = (marker.diameter_mm / MM_PER_INCH) * 72 / 2
        marker_bounds.append(
            (
                marker.center_x - radius - padding_points,
                marker.center_y - radius - padding_points,
                marker.center_x + radius + padding_points,
                marker.center_y + radius + padding_points,
            )
        )

    left = max(0.0, min(bounds[0] for bounds in marker_bounds))
    top = max(0.0, min(bounds[1] for bounds in marker_bounds))
    right = min(sheet.width_points, max(bounds[2] for bounds in marker_bounds))
    bottom = min(sheet.height_points, max(bounds[3] for bounds in marker_bounds))
    if right <= left or bottom <= top:
        return sheet

    return ExportSheet(
        sheet_index=sheet.sheet_index,
        width_points=right - left,
        height_points=bottom - top,
        artworks=[
            ExportArtwork(
                path=artwork.path,
                kind=artwork.kind,
                rect_points=(
                    artwork.rect_points[0] - left,
                    artwork.rect_points[1] - top,
                    artwork.rect_points[2] - left,
                    artwork.rect_points[3] - top,
                ),
                rotation=artwork.rotation,
                page_index=artwork.page_index,
            )
            for artwork in sheet.artworks
        ],
        cut_segments=[
            ExportSegment(
                segment.x1 - left,
                segment.y1 - top,
                segment.x2 - left,
                segment.y2 - top,
            )
            for segment in sheet.cut_segments
        ],
        markers=[
            ExportMarker(
                center_x=marker.center_x - left,
                center_y=marker.center_y - top,
                diameter_mm=marker.diameter_mm,
            )
            for marker in sheet.markers
        ],
    )


def _draw_artwork_layer(page: fitz.Page, artworks: list[ExportArtwork], oc: int) -> None:
    for artwork in artworks:
        rect = fitz.Rect(*artwork.rect_points)
        if artwork.kind == ArtworkKind.PDF:
            with fitz.open(artwork.path) as source:
                page.show_pdf_page(
                    rect,
                    source,
                    pno=artwork.page_index,
                    keep_proportion=False,
                    overlay=True,
                    oc=oc,
                    rotate=artwork.rotation,
                )
        else:
            page.insert_image(
                rect,
                filename=str(artwork.path),
                keep_proportion=False,
                overlay=True,
                oc=oc,
                rotate=artwork.rotation,
            )


def _draw_cut_layer(page: fitz.Page, segments: list[ExportSegment], oc: int) -> None:
    for segment in segments:
        page.draw_line(
            fitz.Point(segment.x1, segment.y1),
            fitz.Point(segment.x2, segment.y2),
            color=(1, 0, 0),
            width=0.5,
            overlay=True,
            oc=oc,
        )


def _draw_marker_layer(page: fitz.Page, markers: list[ExportMarker], oc: int) -> None:
    for marker in markers:
        radius = (marker.diameter_mm / MM_PER_INCH) * 72 / 2
        page.draw_circle(
            fitz.Point(marker.center_x, marker.center_y),
            radius,
            color=(0, 0, 0),
            fill=(0, 0, 0),
            width=0.1,
            overlay=True,
            oc=oc,
        )


def _commit_file(local_path: Path, destination: Path) -> Path:
    _ensure_free_space(destination.parent, local_path.stat().st_size + MIN_FREE_SPACE_BYTES)
    for _attempt in range(10_000):
        final_path = _next_available_destination(destination)
        created_final = False
        try:
            with local_path.open("rb") as source, final_path.open("xb") as target:
                created_final = True
                shutil.copyfileobj(source, target)
                target.flush()
                os.fsync(target.fileno())
            _validate_pdf(final_path)
            return final_path
        except FileExistsError:
            continue
        except Exception:
            if created_final and final_path.exists():
                final_path.unlink()
            raise

    raise FileExistsError(f"No available export filename near {destination}")


def _next_available_destination(destination: Path) -> Path:
    if not destination.exists():
        return destination

    stem = destination.stem
    suffix = destination.suffix
    for index in range(1, 10_000):
        candidate = destination.with_name(f"{stem}_{index:03d}{suffix}")
        if not candidate.exists():
            return candidate

    raise FileExistsError(f"No available export filename near {destination}")


def _ensure_directory_writable(directory: Path) -> None:
    probe = directory / f".printer_nester_write_test_{uuid.uuid4().hex}"
    try:
        probe.write_text("ok", encoding="utf-8")
    finally:
        if probe.exists():
            probe.unlink()


def _ensure_free_space(directory: Path, required_bytes: int) -> None:
    free_bytes = shutil.disk_usage(directory).free
    if free_bytes < required_bytes:
        raise OSError(f"Not enough free space in {directory}: {free_bytes} bytes free, {required_bytes} required")


def _fsync_file(path: Path) -> None:
    with path.open("rb+") as handle:
        handle.flush()
        os.fsync(handle.fileno())


def _validate_pdf(path: Path) -> None:
    if path.stat().st_size <= 0:
        raise OSError(f"Exported PDF is empty: {path}")
    with fitz.open(path) as document:
        if document.page_count == 0:
            raise OSError(f"Exported PDF has no pages: {path}")
