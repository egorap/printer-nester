from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
import os
import re
import shutil
import tempfile
import uuid

import fitz
from printer_nester.core.artboard import SlotLayoutSettings
from printer_nester.core.artwork_import import ArtworkKind
from printer_nester.core.markers import MARKER_DIAMETER_MM, MM_PER_INCH


MIN_FREE_SPACE_BYTES = 10 * 1024 * 1024
EXPORT_MARKER_PADDING_IN = 0.5
MARKER_TEXT_GAP_POINTS = 8.0
JIG_LAYOUT_EXPORT_DPI = 300
SPOT_COLOR_NAME = "RDG_WHITE"
INVERT_BYTE_TABLE = bytes(range(255, -1, -1))
SPOT_COLOR_PDF_LITERAL = (
    b"(\xfe\xff\x00R\x00D\x00G\x00_\x00W\x00H\x00I\x00T\x00E\x00\x00)"
)


class ExportKind(StrEnum):
    PRINT = "print"
    CUT = "cut"


@dataclass(frozen=True, slots=True)
class ExportSettings:
    sheet_print_directory: Path
    jig_print_directory: Path
    cut_directory: Path
    local_temp_directory: Path


@dataclass(frozen=True, slots=True)
class ExportArtwork:
    path: Path
    kind: ArtworkKind
    rect_points: tuple[float, float, float, float]
    rotation: int = 0
    page_index: int = 0
    image_bytes: bytes | None = None


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


def export_sheet_pdf(
    sheet: ExportSheet,
    kind: ExportKind,
    settings: ExportSettings,
    export_number: int | None = None,
) -> ExportResult:
    destination_dir = (
        settings.sheet_print_directory
        if kind == ExportKind.PRINT
        else settings.cut_directory
    )
    file_number = (
        sheet.sheet_index + 1
        if export_number is None
        else max(1, int(export_number))
    )
    destination_name = f"sheet_{file_number:03d}_{kind.value}.pdf"
    destination = destination_dir / destination_name

    _ensure_configured_directory(destination_dir, f"{kind.value} export directory")
    _ensure_configured_directory(
        settings.local_temp_directory, "local temp export directory"
    )
    settings.local_temp_directory.mkdir(parents=True, exist_ok=True)
    destination_dir.mkdir(parents=True, exist_ok=True)
    _ensure_directory_writable(settings.local_temp_directory)
    _ensure_directory_writable(destination_dir)

    cut_filename = f"sheet_{file_number:03d}_{ExportKind.CUT.value}.pdf"
    local_path = _write_local_pdf(
        sheet,
        kind,
        settings.local_temp_directory,
        cut_filename=cut_filename if kind == ExportKind.PRINT else None,
    )
    try:
        final_path = _commit_file(local_path, destination)
    finally:
        if local_path.exists():
            local_path.unlink()

    return ExportResult(path=final_path, bytes_written=final_path.stat().st_size)


def _ensure_configured_directory(directory: Path, label: str) -> None:
    if not str(directory).strip() or directory == Path("."):
        raise ValueError(f"{label} is not configured")


def _write_local_pdf(
    sheet: ExportSheet,
    kind: ExportKind,
    temp_dir: Path,
    cut_filename: str | None = None,
) -> Path:
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
    if kind == ExportKind.PRINT and cut_filename:
        _draw_cut_filename_labels(page, sheet.markers, cut_filename, marker_oc)

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
                image_bytes=artwork.image_bytes,
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


def _draw_artwork_layer(
    page: fitz.Page, artworks: list[ExportArtwork], oc: int | None = None
) -> None:
    for artwork in artworks:
        rect = fitz.Rect(*artwork.rect_points)
        options = {
            "keep_proportion": False,
            "overlay": True,
            "rotate": artwork.rotation,
        }
        if oc is not None:
            options["oc"] = oc
        if artwork.image_bytes is not None:
            page.insert_image(rect, stream=artwork.image_bytes, **options)
        elif artwork.kind == ArtworkKind.PDF:
            with fitz.open(artwork.path) as source:
                page.show_pdf_page(
                    rect,
                    source,
                    pno=artwork.page_index,
                    **options,
                )
        else:
            page.insert_image(rect, filename=str(artwork.path), **options)


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


def _draw_cut_filename_labels(
    page: fitz.Page,
    markers: list[ExportMarker],
    cut_filename: str,
    oc: int,
) -> None:
    if len(markers) < 2:
        return

    font = fitz.Font(fontname="helv")
    marker_height = (MARKER_DIAMETER_MM / MM_PER_INCH) * 72
    font_size = marker_height / (font.ascender - font.descender)
    text_width = font.text_length(cut_filename, fontsize=font_size)
    top_right = min(markers, key=lambda marker: (marker.center_y, -marker.center_x))
    bottom_left = max(
        markers, key=lambda marker: (marker.center_y, -marker.center_x)
    )
    baseline_offset = font_size * (font.ascender + font.descender) / 2
    top_right_radius = (top_right.diameter_mm / MM_PER_INCH) * 72 / 2
    bottom_left_radius = (bottom_left.diameter_mm / MM_PER_INCH) * 72 / 2

    page.insert_text(
        fitz.Point(
            max(
                1.0,
                top_right.center_x
                - top_right_radius
                - MARKER_TEXT_GAP_POINTS
                - text_width,
            ),
            top_right.center_y + baseline_offset,
        ),
        cut_filename,
        fontsize=font_size,
        fontname="helv",
        color=(0, 0, 0),
        overlay=True,
        oc=oc,
    )
    page.insert_text(
        fitz.Point(
            bottom_left.center_x
            + bottom_left_radius
            + MARKER_TEXT_GAP_POINTS,
            bottom_left.center_y + baseline_offset,
        ),
        cut_filename,
        fontsize=font_size,
        fontname="helv",
        color=(0, 0, 0),
        overlay=True,
        oc=oc,
    )


def export_jig_layout_pdf(
    sheets: list[ExportSheet],
    destination: Path,
    local_temp_directory: Path,
    dpi: int = JIG_LAYOUT_EXPORT_DPI,
) -> ExportResult:
    if not sheets:
        raise ValueError("jig layout has no sheets to export")
    if not str(destination).strip():
        raise ValueError("jig layout destination is not configured")

    destination = Path(destination)
    if destination.suffix.lower() != ".pdf":
        destination = destination.with_suffix(".pdf")

    _ensure_configured_directory(
        local_temp_directory, "local temp export directory"
    )
    local_temp_directory.mkdir(parents=True, exist_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _ensure_directory_writable(local_temp_directory)
    _ensure_directory_writable(destination.parent)

    local_path = _write_local_jig_layout_pdf(sheets, local_temp_directory, dpi)
    try:
        final_path = _commit_file(local_path, destination)
    finally:
        if local_path.exists():
            local_path.unlink()

    return ExportResult(path=final_path, bytes_written=final_path.stat().st_size)


def _write_local_jig_layout_pdf(
    sheets: list[ExportSheet], temp_dir: Path, dpi: int
) -> Path:
    _ensure_free_space(temp_dir, MIN_FREE_SPACE_BYTES)
    local_path = temp_dir / f"printer_nester_jig_layout_{uuid.uuid4().hex}.pdf"

    document = fitz.open()
    spot_color_space_xref = _add_spot_color_space(document)
    overprint_state_xref = _add_spot_overprint_graphics_state(document)
    for sheet in sheets:
        page = document.new_page(width=sheet.width_points, height=sheet.height_points)
        color_pixmap, spot_samples = _render_jig_sheet_images(sheet, dpi)
        _draw_jig_multichannel_page(
            document,
            page,
            color_pixmap,
            spot_samples,
            spot_color_space_xref,
            overprint_state_xref,
        )

    document.save(local_path, deflate=True, deflate_images=True)
    document.close()
    _validate_pdf(local_path)
    return local_path


def _render_jig_sheet_images(
    sheet: ExportSheet, dpi: int
) -> tuple[fitz.Pixmap, bytes]:
    scale = max(1, dpi) / 72
    document = fitz.open()
    page = document.new_page(width=sheet.width_points, height=sheet.height_points)
    _draw_artwork_layer(page, sheet.artworks)
    pixmap = page.get_pixmap(
        matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB, alpha=True
    )
    spot_samples = pixmap.samples[3::4].translate(INVERT_BYTE_TABLE)
    document.close()
    return pixmap, spot_samples

def _draw_jig_multichannel_page(
    document: fitz.Document,
    page: fitz.Page,
    color_pixmap: fitz.Pixmap,
    spot_samples: bytes,
    spot_color_space_xref: int,
    overprint_state_xref: int,
) -> None:
    page_rect = fitz.Rect(0, 0, page.rect.width, page.rect.height)
    spot_xref = _add_spot_image(
        document,
        spot_samples,
        color_pixmap.width,
        color_pixmap.height,
        spot_color_space_xref,
    )
    color_xref = page.insert_image(
        page_rect, pixmap=color_pixmap, keep_proportion=False, overlay=True
    )
    _set_jig_page_resources(
        document,
        page,
        spot_xref,
        color_xref,
        spot_color_space_xref,
        overprint_state_xref,
    )
    _add_photoshop_spot_channel_info(
        document, page, spot_samples, color_pixmap.width, color_pixmap.height
    )
    _replace_page_content_with_multichannel_image(
        document, page, page.rect.width, page.rect.height
    )

def _add_spot_color_space(document: fitz.Document) -> int:
    alternate_color_space_xref = document.get_new_xref()
    document.update_object(
        alternate_color_space_xref,
        """[ /CalRGB <<
    /BlackPoint [ 0 0 0 ]
    /Gamma [ 2.2 2.2 2.2 ]
    /Matrix [ .412384 .212631 .0193329 .35759 .715164 .119186 .180481 .0721893 .950516 ]
    /WhitePoint [ .950455 1 1.08905 ]
  >> ]""",
    )

    tint_transform_xref = document.get_new_xref()
    document.update_object(
        tint_transform_xref,
        "<< /FunctionType 4 /Domain [0 1] /Range [0 1 0 1 0 1] >>",
    )
    document.update_stream(
        tint_transform_xref,
        b"{dup dup -1.000000 mul 1.0 add 3 1 roll -1.000000 mul "
        b"1.0 add 3 1 roll 0.000000 mul 1.0 add 3 1 roll}",
    )

    separation_xref = document.get_new_xref()
    document.update_object(
        separation_xref,
        f"[ /Separation /{SPOT_COLOR_NAME} "
        f"{alternate_color_space_xref} 0 R {tint_transform_xref} 0 R ]",
    )

    lookup_xref = document.get_new_xref()
    document.update_object(lookup_xref, "<< >>")
    document.update_stream(lookup_xref, bytes(range(255, -1, -1)))

    indexed_xref = document.get_new_xref()
    document.update_object(
        indexed_xref, f"[ /Indexed {separation_xref} 0 R 255 {lookup_xref} 0 R ]"
    )
    return indexed_xref


def _add_spot_overprint_graphics_state(document: fitz.Document) -> int:
    overprint_xref = document.get_new_xref()
    document.update_object(
        overprint_xref,
        "<< /Type /ExtGState /op true /OP true /OPM 1 >>",
    )
    return overprint_xref



def _add_spot_image(
    document: fitz.Document,
    samples: bytes,
    width: int,
    height: int,
    spot_color_space_xref: int,
) -> int:
    image_xref = document.get_new_xref()
    document.update_object(
        image_xref,
        f"""<<
  /BitsPerComponent 8
  /ColorSpace {spot_color_space_xref} 0 R
  /Height {height}
  /Name /X
  /Subtype /Image
  /Type /XObject
  /Width {width}
>>""",
    )
    document.update_stream(image_xref, samples)
    return image_xref

def _set_jig_page_resources(
    document: fitz.Document,
    page: fitz.Page,
    spot_xref: int,
    color_xref: int,
    spot_color_space_xref: int,
    overprint_state_xref: int,
) -> None:
    resource_xref = _page_resource_xref(document, page)
    document.update_object(
        resource_xref,
        f"""<<
  /ColorSpace << /CS0 {spot_color_space_xref} 0 R >>
  /ExtGState << /GS0 {overprint_state_xref} 0 R >>
  /ProcSet [ /PDF /ImageC /ImageI ]
  /XObject << /Im0 {spot_xref} 0 R /Im1 {color_xref} 0 R >>
>>""",
    )


def _add_photoshop_spot_channel_info(
    document: fitz.Document,
    page: fitz.Page,
    spot_samples: bytes,
    image_width: int,
    image_height: int,
) -> None:
    spot_mask_xref = _add_device_gray_image(
        document, spot_samples, image_width, image_height
    )
    spot_xobject_xref = document.get_new_xref()
    document.update_object(spot_xobject_xref, f"<< /Im1 {spot_mask_xref} 0 R >>")

    private_xref = document.get_new_xref()
    document.update_object(
        private_xref,
        f"<< /SpotChannels << /N 1 /XObject {spot_xobject_xref} 0 R >> >>",
    )
    _set_page_piece_info(document, page, private_xref)


def _add_device_gray_image(
    document: fitz.Document, samples: bytes, width: int, height: int
) -> int:
    image_xref = document.get_new_xref()
    document.update_object(
        image_xref,
        f"""<<
  /BitsPerComponent 8
  /ColorSpace /DeviceGray
  /Height {height}
  /Name /X
  /Subtype /Image
  /Type /XObject
  /Width {width}
>>""",
    )
    document.update_stream(image_xref, samples)
    return image_xref


def _set_page_piece_info(
    document: fitz.Document, page: fitz.Page, private_xref: int
) -> None:
    page_object = document.xref_object(page.xref, compressed=False)
    piece_info = (
        f"/PieceInfo << /AdobePhotoshop << /Private {private_xref} 0 R >> >>"
    )
    if "/PieceInfo" in page_object:
        page_object = re.sub(
            r"/PieceInfo\s*<<.*?>>\s*(?=/Resources)",
            piece_info + "\n  ",
            page_object,
            count=1,
            flags=re.DOTALL,
        )
    else:
        page_object = page_object.replace("/Resources", piece_info + "\n  /Resources", 1)
    document.update_object(page.xref, page_object)


def _page_resource_xref(document: fitz.Document, page: fitz.Page) -> int:
    page_object = document.xref_object(page.xref, compressed=False)
    match = re.search(r"/Resources\s+(\d+)\s+0\s+R", page_object)
    if match is None:
        raise RuntimeError("Jig layout page has no indirect resources dictionary")
    return int(match.group(1))



def _replace_page_content_with_multichannel_image(
    document: fitz.Document,
    page: fitz.Page,
    width_points: float,
    height_points: float,
) -> None:
    width = f"{width_points:.6f}".encode("ascii")
    height = f"{height_points:.6f}".encode("ascii")
    content = b"".join(
        [
            b"/MultiChannelImage <</MainChannel /RGBColorMode /Spot [",
            SPOT_COLOR_PDF_LITERAL,
            b"]>>BDC \n",
            b"/SeparationImages BMC \n",
            b"/SpotImage <</Ink ",
            SPOT_COLOR_PDF_LITERAL,
            b">>BDC \n",
            b"q\n/GS0 gs\n/RelativeColorimetric ri\n",
            width,
            b" 0 0 ",
            height,
            b" 0 0 cm\n/Im0 Do\nQ\nEMC \n",
            b"/MainChannelImage BMC \n",
            b"q\n/GS0 gs\n/RelativeColorimetric ri\n",
            width,
            b" 0 0 ",
            height,
            b" 0 0 cm\n/Im1 Do\nQ\nEMC \nEMC \nEMC \n",
        ]
    )
    content_xrefs = page.get_contents()
    if not content_xrefs:
        raise RuntimeError("Jig layout page has no content stream")
    document.update_stream(content_xrefs[0], content)
    for content_xref in content_xrefs[1:]:
        document.update_stream(content_xref, b"")

def export_jig_mockup_pdf(
    slot_layout: SlotLayoutSettings,
    destination: Path,
    local_temp_directory: Path,
) -> ExportResult:
    if not str(destination).strip():
        raise ValueError("jig mockup destination is not configured")

    destination = Path(destination)
    if destination.suffix.lower() != ".pdf":
        destination = destination.with_suffix(".pdf")

    _ensure_configured_directory(
        local_temp_directory, "local temp export directory"
    )
    local_temp_directory.mkdir(parents=True, exist_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _ensure_directory_writable(local_temp_directory)
    _ensure_directory_writable(destination.parent)

    local_path = _write_local_jig_mockup_pdf(slot_layout, local_temp_directory)
    try:
        final_path = _commit_file(local_path, destination)
    finally:
        if local_path.exists():
            local_path.unlink()

    return ExportResult(path=final_path, bytes_written=final_path.stat().st_size)


def _write_local_jig_mockup_pdf(
    slot_layout: SlotLayoutSettings, temp_dir: Path
) -> Path:
    _ensure_free_space(temp_dir, MIN_FREE_SPACE_BYTES)
    local_path = temp_dir / f"printer_nester_jig_mockup_{uuid.uuid4().hex}.pdf"
    width_points = max(1.0, slot_layout.total_width_in * 72)
    height_points = max(1.0, slot_layout.total_height_in * 72)

    document = fitz.open()
    page = document.new_page(width=width_points, height=height_points)
    artboard_oc = document.add_ocg("Artboard")
    margin_oc = document.add_ocg("Outside Margin")
    slot_oc = document.add_ocg("Slots")
    center_oc = document.add_ocg("Slot Centers")

    artboard_rect = fitz.Rect(0, 0, width_points, height_points)
    page.draw_rect(
        artboard_rect,
        color=(0.05, 0.06, 0.07),
        width=1.0,
        overlay=True,
        oc=artboard_oc,
    )

    outside_margin = max(0.0, slot_layout.outside_margin_in) * 72
    margin_rect = fitz.Rect(
        outside_margin,
        outside_margin,
        width_points - outside_margin,
        height_points - outside_margin,
    )
    if outside_margin > 0 and not margin_rect.is_empty:
        page.draw_rect(
            margin_rect,
            color=(0.55, 0.6, 0.65),
            width=0.5,
            overlay=True,
            oc=margin_oc,
        )

    slot_rects = _jig_mockup_slot_rects(slot_layout)
    for slot_rect in slot_rects:
        page.draw_rect(
            slot_rect,
            color=(0.0, 0.45, 0.75),
            fill=(0.9, 0.97, 1.0),
            width=0.75,
            overlay=True,
            oc=slot_oc,
        )
        center = slot_rect.tl + (slot_rect.br - slot_rect.tl) * 0.5
        mark_half = max(
            3.0,
            min(9.0, slot_rect.width * 0.06, slot_rect.height * 0.06),
        )
        page.draw_line(
            fitz.Point(center.x - mark_half, center.y),
            fitz.Point(center.x + mark_half, center.y),
            color=(0.0, 0.45, 0.75),
            width=0.35,
            overlay=True,
            oc=center_oc,
        )
        page.draw_line(
            fitz.Point(center.x, center.y - mark_half),
            fitz.Point(center.x, center.y + mark_half),
            color=(0.0, 0.45, 0.75),
            width=0.35,
            overlay=True,
            oc=center_oc,
        )

    document.save(local_path)
    document.close()
    _validate_pdf(local_path)
    return local_path


def _jig_mockup_slot_rects(slot_layout: SlotLayoutSettings) -> list[fitz.Rect]:
    width = max(0.001, slot_layout.slot_width_in) * 72
    height = max(0.001, slot_layout.slot_height_in) * 72
    h_spacing = max(0.0, slot_layout.horizontal_spacing_in) * 72
    v_spacing = max(0.0, slot_layout.vertical_spacing_in) * 72
    columns = max(1, int(slot_layout.columns))
    rows = max(1, int(slot_layout.rows))
    outside = max(0.0, slot_layout.outside_margin_in) * 72

    rects: list[fitz.Rect] = []
    for row in range(rows):
        y = outside + row * (height + v_spacing)
        for column in range(columns):
            x = outside + column * (width + h_spacing)
            rects.append(fitz.Rect(x, y, x + width, y + height))
    return rects


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
