from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


PRESET_SIZES: tuple[tuple[str, float, float], ...] = (
    ("36x24", 36.0, 24.0),
    ("20x30", 20.0, 30.0),
    ("24x18", 24.0, 18.0),
)
PREDEFINED_SAFE_AREA_WIDTH_IN = 94.0
PREDEFINED_SAFE_AREA_HEIGHT_IN = 46.0


@dataclass(frozen=True, slots=True)
class PresetMatch:
    name: str
    rotated: bool


def classify_preset_size(width_in: float, height_in: float, tolerance: float = 0.01) -> PresetMatch | None:
    for name, preset_width, preset_height in PRESET_SIZES:
        if _close(width_in, preset_width, tolerance) and _close(height_in, preset_height, tolerance):
            return PresetMatch(name=name, rotated=False)
        if _close(width_in, preset_height, tolerance) and _close(height_in, preset_width, tolerance):
            return PresetMatch(name=name, rotated=True)

    return None


def load_layout_catalog(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    layouts = payload.get("layouts", [])
    if not isinstance(layouts, list):
        return []

    return layouts


def choose_layouts_for_counts(available_counts: dict[str, int], catalog: list[dict]) -> list[dict]:
    artwork_order = tuple(name for name, _width, _height in PRESET_SIZES)
    start = tuple(available_counts.get(artwork, 0) for artwork in artwork_order)
    layout_options = _layout_options(catalog, artwork_order)
    memo: dict[tuple[int, ...], tuple[dict, ...] | None] = {tuple(0 for _ in artwork_order): ()}

    def solve(state: tuple[int, ...]) -> tuple[dict, ...] | None:
        if state in memo:
            return memo[state]

        best: tuple[dict, ...] | None = None
        for vector, layout in layout_options:
            if not _vector_fits(vector, state):
                continue
            next_state = tuple(current - used for current, used in zip(state, vector, strict=True))
            suffix = solve(next_state)
            if suffix is None:
                continue
            candidate = (layout,) + suffix
            if _layout_sequence_is_better(candidate, best):
                best = candidate

        memo[state] = best
        return best

    return list(solve(start) or [])


def _layout_options(catalog: list[dict], artwork_order: tuple[str, ...]) -> list[tuple[tuple[int, ...], dict]]:
    options = []
    for layout in catalog:
        layout_counts = _layout_counts_by_name(layout)
        if not layout_counts:
            continue
        if any(artwork not in artwork_order for artwork in layout_counts):
            continue
        vector = tuple(layout_counts.get(artwork, 0) for artwork in artwork_order)
        options.append((vector, layout))

    options.sort(
        key=lambda option: (
            sum(option[0]),
            float(option[1].get("utilization", 0)),
            float(option[1].get("score", 0)),
        ),
        reverse=True,
    )
    return options


def _vector_fits(vector: tuple[int, ...], state: tuple[int, ...]) -> bool:
    return any(vector) and all(used <= available for used, available in zip(vector, state, strict=True))


def _layout_sequence_is_better(candidate: tuple[dict, ...], current: tuple[dict, ...] | None) -> bool:
    if current is None:
        return True
    if len(candidate) != len(current):
        return len(candidate) < len(current)

    candidate_non_last_util = _non_last_utilization(candidate)
    current_non_last_util = _non_last_utilization(current)
    if candidate_non_last_util != current_non_last_util:
        return candidate_non_last_util > current_non_last_util

    candidate_score = sum(float(layout.get("score", 0)) for layout in candidate)
    current_score = sum(float(layout.get("score", 0)) for layout in current)
    return candidate_score > current_score


def _non_last_utilization(layouts: tuple[dict, ...]) -> float:
    if len(layouts) <= 1:
        return 0.0

    return sum(float(layout.get("utilization", 0)) for layout in layouts[:-1])


def _layout_counts_by_name(layout: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    placements = layout.get("placements", [])
    if not isinstance(placements, list):
        return counts

    for placement in placements:
        if not isinstance(placement, dict):
            continue
        artwork = placement.get("artwork")
        if isinstance(artwork, str):
            counts[artwork] = counts.get(artwork, 0) + 1

    return counts


def _close(first: float, second: float, tolerance: float) -> bool:
    return abs(first - second) <= tolerance
