from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CutSegment:
    x1: float
    y1: float
    x2: float
    y2: float


@dataclass(frozen=True, slots=True)
class CutRect:
    left: float
    top: float
    right: float
    bottom: float


def cut_segments_for_rects(rects: list[CutRect], tolerance: float = 0.01) -> list[CutSegment]:
    segments: list[CutSegment] = []
    for rect in rects:
        if rect.right - rect.left <= tolerance or rect.bottom - rect.top <= tolerance:
            continue

        segments.extend(
            [
                CutSegment(rect.left, rect.top, rect.right, rect.top),
                CutSegment(rect.right, rect.top, rect.right, rect.bottom),
                CutSegment(rect.right, rect.bottom, rect.left, rect.bottom),
                CutSegment(rect.left, rect.bottom, rect.left, rect.top),
            ]
        )

    return _dedupe_segments(segments, tolerance)


def dedupe_cut_segments(segments: list[CutSegment], tolerance: float = 0.01) -> list[CutSegment]:
    return _dedupe_segments(segments, tolerance)


def _dedupe_segments(segments: list[CutSegment], tolerance: float) -> list[CutSegment]:
    unique: list[CutSegment] = []
    seen: set[tuple[int, int, int, int]] = set()
    for segment in segments:
        key = _segment_key(segment, tolerance)
        if key in seen:
            continue
        seen.add(key)
        unique.append(segment)
    return unique


def _segment_key(segment: CutSegment, tolerance: float) -> tuple[int, int, int, int]:
    first = (_bucket(segment.x1, tolerance), _bucket(segment.y1, tolerance))
    second = (_bucket(segment.x2, tolerance), _bucket(segment.y2, tolerance))
    start, end = sorted([first, second])
    return start[0], start[1], end[0], end[1]


def _bucket(value: float, tolerance: float) -> int:
    return round(value / tolerance)
