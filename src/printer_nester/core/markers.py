from __future__ import annotations

from dataclasses import dataclass
import math


MM_PER_INCH = 25.4
MARKER_DIAMETER_MM = 5.0
ASYMMETRY_MARKER_OFFSET_IN = 4.0
MAX_MARKER_GAP_IN = 20.0


@dataclass(frozen=True, slots=True)
class SheetMarker:
    center_x_in: float
    center_y_in: float
    diameter_mm: float = MARKER_DIAMETER_MM


def sheet_marker_layout(width_in: float, height_in: float, margin_in: float) -> list[SheetMarker]:
    if width_in <= 0 or height_in <= 0:
        return []

    radius_in = (MARKER_DIAMETER_MM / MM_PER_INCH) / 2
    inset = max(radius_in, margin_in / 2)
    inset = min(inset, width_in / 2, height_in / 2)
    left = inset
    top = inset
    right = width_in - inset
    bottom = height_in - inset

    positions: list[tuple[float, float]] = []
    positions.extend(
        [
            (left, top),
            (right, top),
            (right, bottom),
            (left, bottom),
        ]
    )
    positions.extend(_edge_positions((left, top), (right, top)))
    positions.extend(_edge_positions((right, top), (right, bottom)))
    positions.extend(_edge_positions((right, bottom), (left, bottom)))
    positions.extend(_edge_positions((left, bottom), (left, top)))

    asymmetry_y = bottom - ASYMMETRY_MARKER_OFFSET_IN
    if top < asymmetry_y < bottom:
        positions.append((left, asymmetry_y))

    return [SheetMarker(x, y) for x, y in _dedupe_positions(positions)]


def _edge_positions(start: tuple[float, float], end: tuple[float, float]) -> list[tuple[float, float]]:
    start_x, start_y = start
    end_x, end_y = end
    length = math.hypot(end_x - start_x, end_y - start_y)
    segment_count = max(1, math.ceil(length / MAX_MARKER_GAP_IN))
    if segment_count <= 1:
        return []

    positions: list[tuple[float, float]] = []
    for index in range(1, segment_count):
        ratio = index / segment_count
        positions.append(
            (
                start_x + (end_x - start_x) * ratio,
                start_y + (end_y - start_y) * ratio,
            )
        )
    return positions


def _dedupe_positions(positions: list[tuple[float, float]]) -> list[tuple[float, float]]:
    seen: set[tuple[float, float]] = set()
    unique: list[tuple[float, float]] = []
    for x, y in positions:
        key = (round(x, 4), round(y, 4))
        if key in seen:
            continue
        seen.add(key)
        unique.append((x, y))
    return unique
