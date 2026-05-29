from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, TextIO

from PIL import Image, ImageDraw, ImageFont


SAFE_AREA_WIDTH_IN = 94.0
SAFE_AREA_HEIGHT_IN = 46.0
DEFAULT_ITEM_GAP_IN = 0.2
COMMON_ARTWORK_SIZES_IN = ((36.0, 24.0), (20.0, 30.0), (24.0, 18.0))
PREVIEW_SCALE_PX_PER_IN = 8
PREVIEW_PADDING_PX = 28
PREVIEW_COLORS = {
    "36x24": "#6aaed6",
    "20x30": "#7cc47f",
    "24x18": "#d99a5f",
}


@dataclass(frozen=True, slots=True)
class ArtworkType:
    name: str
    width_in: float
    height_in: float

    @property
    def area_in2(self) -> float:
        return self.width_in * self.height_in


@dataclass(frozen=True, slots=True)
class PlacedItem:
    artwork: str
    x_in: float
    y_in: float
    width_in: float
    height_in: float
    rotated: bool

    @property
    def right_in(self) -> float:
        return self.x_in + self.width_in

    @property
    def bottom_in(self) -> float:
        return self.y_in + self.height_in


@dataclass(frozen=True, slots=True)
class LayoutCandidate:
    placements: tuple[PlacedItem, ...]
    counts: tuple[int, ...]
    used_area_in2: float
    used_width_in: float
    used_height_in: float
    utilization: float
    score: float
    gap_in: float
    method: str


@dataclass(frozen=True, slots=True)
class RowPattern:
    placements: tuple[PlacedItem, ...]
    counts: tuple[int, ...]
    width_in: float
    height_in: float


@dataclass(frozen=True, slots=True)
class SearchStats:
    iterations: int
    quantity_combos_checked: int
    quantity_combos_total: int
    current_counts: tuple[int, ...] | None
    elapsed_s: float
    iterations_per_s: float
    best_score: float
    best_utilization: float
    best_count: int
    results_kept: int


def default_artwork_types() -> tuple[ArtworkType, ...]:
    return tuple(ArtworkType(f"{width:g}x{height:g}", width, height) for width, height in COMMON_ARTWORK_SIZES_IN)


def run_layout_search(
    output_path: Path,
    time_limit_s: float | None = None,
    max_quantity_per_type: int = 12,
    max_results: int = 100,
    stats_interval_s: float = 0.5,
    preview_count: int = 10,
    stream: TextIO | None = None,
) -> list[LayoutCandidate]:
    start = time.monotonic()
    last_stats = start
    iterations = 0
    combo_index = 0
    current_counts: tuple[int, ...] | None = None
    best: list[LayoutCandidate] = []
    artwork_types = default_artwork_types()
    quantity_combinations = tuple(_quantity_combinations(len(artwork_types), max_quantity_per_type))
    quantity_combo_total = len(quantity_combinations)

    try:
        for counts in quantity_combinations:
            combo_index += 1
            current_counts = counts
            now = time.monotonic()
            if stream is not None and now - last_stats >= stats_interval_s:
                _print_stats(stream, _stats(iterations, start, best, combo_index, quantity_combo_total, current_counts))
                last_stats = now

            for candidate in _candidate_layouts_for_counts(artwork_types, counts):
                iterations += 1
                best = _record_candidate(best, candidate, max_results)

                now = time.monotonic()
                if stream is not None and now - last_stats >= stats_interval_s:
                    _print_stats(stream, _stats(iterations, start, best, combo_index, quantity_combo_total, current_counts))
                    last_stats = now

                if time_limit_s is not None and now - start >= time_limit_s:
                    raise TimeoutError
    except (KeyboardInterrupt, TimeoutError):
        pass
    finally:
        write_layout_results(output_path, best, _stats(iterations, start, best, combo_index, quantity_combo_total, current_counts))
        write_layout_previews(output_path, best[:preview_count])
        if stream is not None:
            _print_stats(stream, _stats(iterations, start, best, combo_index, quantity_combo_total, current_counts), final=True)

    return best


def write_layout_results(output_path: Path, layouts: list[LayoutCandidate], stats: SearchStats) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "safe_area": {"width_in": SAFE_AREA_WIDTH_IN, "height_in": SAFE_AREA_HEIGHT_IN},
        "stats": asdict(stats),
        "layouts": [_layout_to_dict(layout) for layout in layouts],
    }
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temp_path.replace(output_path)


def write_layout_previews(output_path: Path, layouts: list[LayoutCandidate]) -> list[Path]:
    preview_dir = output_path.with_suffix("").parent / f"{output_path.stem}_previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    for old_preview in preview_dir.glob("*.png"):
        old_preview.unlink()

    written: list[Path] = []
    for index, layout in enumerate(layouts, start=1):
        preview_path = preview_dir / f"layout_{index:03d}_{_counts_slug(layout.counts)}.png"
        render_layout_preview(layout, preview_path)
        written.append(preview_path)

    return written


def render_layout_preview(
    layout: LayoutCandidate,
    output_path: Path,
    scale_px_per_in: int = PREVIEW_SCALE_PX_PER_IN,
) -> None:
    width_px = round(SAFE_AREA_WIDTH_IN * scale_px_per_in) + PREVIEW_PADDING_PX * 2
    height_px = round(SAFE_AREA_HEIGHT_IN * scale_px_per_in) + PREVIEW_PADDING_PX * 2 + 34
    image = Image.new("RGB", (width_px, height_px), "#f4f6f8")
    draw = ImageDraw.Draw(image)

    origin_x = PREVIEW_PADDING_PX
    origin_y = PREVIEW_PADDING_PX + 34
    safe_rect = (
        origin_x,
        origin_y,
        origin_x + SAFE_AREA_WIDTH_IN * scale_px_per_in,
        origin_y + SAFE_AREA_HEIGHT_IN * scale_px_per_in,
    )
    draw.rectangle(safe_rect, fill="#ffffff", outline="#30363d", width=2)

    title = (
        f"counts={layout.counts}  util={layout.utilization:.1%}  "
        f"used={layout.used_width_in:.2f}x{layout.used_height_in:.2f} in"
    )
    draw.text((origin_x, PREVIEW_PADDING_PX), title, fill="#202428", font=_preview_font())

    for item in layout.placements:
        x1 = origin_x + item.x_in * scale_px_per_in
        y1 = origin_y + item.y_in * scale_px_per_in
        x2 = origin_x + item.right_in * scale_px_per_in
        y2 = origin_y + item.bottom_in * scale_px_per_in
        color = PREVIEW_COLORS.get(item.artwork, "#b0bec5")
        draw.rectangle((x1, y1, x2, y2), fill=color, outline="#263238", width=1)
        label = f"{item.artwork}{' R' if item.rotated else ''}"
        if x2 - x1 >= 42 and y2 - y1 >= 20:
            draw.text((x1 + 4, y1 + 4), label, fill="#102027", font=_preview_font())

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    image.save(temp_path, format="PNG")
    temp_path.replace(output_path)


def _candidate_layouts_for_counts(
    artwork_types: tuple[ArtworkType, ...],
    counts: tuple[int, ...],
) -> Iterable[LayoutCandidate]:
    items = tuple(
        artwork
        for artwork, count in zip(artwork_types, counts, strict=True)
        for _ in range(count)
    )
    if not items:
        return

    orderings = _practical_orderings(items)
    for ordering in orderings:
        for rotated_types in itertools.product((False, True), repeat=len(artwork_types)):
            rotation_by_name = {
                artwork.name: rotated
                for artwork, rotated in zip(artwork_types, rotated_types, strict=True)
            }
            oriented = tuple(_oriented_item(item, rotation_by_name[item.name]) for item in ordering)
            layout = _pack_shelf(oriented, counts, method="shelf")
            if layout is not None:
                yield layout

    yield from _row_grouped_layouts_for_counts(artwork_types, counts)
    yield from _guillotine_layouts_for_counts(artwork_types, counts)


def _pack_shelf(
    oriented_items: tuple[PlacedItem, ...],
    counts: tuple[int, ...],
    gap_in: float = DEFAULT_ITEM_GAP_IN,
    method: str = "shelf",
) -> LayoutCandidate | None:
    x = 0.0
    y = 0.0
    row_height = 0.0
    previous_row_height = 0.0
    row_items: list[PlacedItem] = []
    placements: list[PlacedItem] = []

    for item in oriented_items:
        if item.width_in > SAFE_AREA_WIDTH_IN or item.height_in > SAFE_AREA_HEIGHT_IN:
            return None

        item_gap = _horizontal_gap(row_items[-1], item, gap_in) if row_items else 0.0
        candidate_x = x + item_gap

        if candidate_x + item.width_in > SAFE_AREA_WIDTH_IN:
            x = 0.0
            y += previous_row_height + gap_in
            row_height = 0.0
            previous_row_height = 0.0
            row_items = []
            candidate_x = 0.0

        if y + item.height_in > SAFE_AREA_HEIGHT_IN:
            return None

        placed = PlacedItem(
            artwork=item.artwork,
            x_in=candidate_x,
            y_in=y,
            width_in=item.width_in,
            height_in=item.height_in,
            rotated=item.rotated,
        )
        placements.append(placed)
        row_items.append(placed)
        x = candidate_x + item.width_in
        row_height = max(row_height, item.height_in)
        previous_row_height = row_height

    used_width = max((item.right_in for item in placements), default=0.0)
    used_height = max((item.bottom_in for item in placements), default=0.0)
    used_area = sum(item.width_in * item.height_in for item in placements)
    utilization = used_area / (SAFE_AREA_WIDTH_IN * SAFE_AREA_HEIGHT_IN)
    footprint_area = max(1.0, used_width * used_height)
    footprint_fill = used_area / footprint_area
    score = utilization * 10_000 + footprint_fill * 1_000 + len(placements)

    return LayoutCandidate(
        placements=tuple(placements),
        counts=counts,
        used_area_in2=used_area,
        used_width_in=used_width,
        used_height_in=used_height,
        utilization=utilization,
        score=score,
        gap_in=gap_in,
        method=method,
    )


def _row_grouped_layouts_for_counts(
    artwork_types: tuple[ArtworkType, ...],
    counts: tuple[int, ...],
    max_layouts: int = 500,
) -> Iterable[LayoutCandidate]:
    row_patterns = _row_patterns_for_counts(artwork_types, counts)
    emitted = 0

    def place_rows(
        remaining: tuple[int, ...],
        start_pattern_index: int,
        y: float,
        rows: tuple[RowPattern, ...],
    ) -> Iterable[LayoutCandidate]:
        nonlocal emitted
        if emitted >= max_layouts:
            return

        if not any(remaining):
            placements: list[PlacedItem] = []
            current_y = 0.0
            for row in rows:
                for item in row.placements:
                    placements.append(
                        PlacedItem(
                            artwork=item.artwork,
                            x_in=item.x_in,
                            y_in=current_y,
                            width_in=item.width_in,
                            height_in=item.height_in,
                            rotated=item.rotated,
                        )
                    )
                current_y += row.height_in + DEFAULT_ITEM_GAP_IN

            emitted += 1
            layout = _candidate_from_placements(
                tuple(placements),
                counts,
                method="row_grouping",
                gap_in=DEFAULT_ITEM_GAP_IN,
            )
            if layout is not None:
                yield layout
            return

        for index in range(start_pattern_index, len(row_patterns)):
            pattern = row_patterns[index]
            if not _counts_fit(pattern.counts, remaining):
                continue
            row_y = y if not rows else y + DEFAULT_ITEM_GAP_IN
            if row_y + pattern.height_in > SAFE_AREA_HEIGHT_IN:
                continue
            next_remaining = _subtract_counts(remaining, pattern.counts)
            yield from place_rows(next_remaining, index, row_y + pattern.height_in, rows + (pattern,))

    yield from place_rows(counts, 0, 0.0, ())


def _row_patterns_for_counts(
    artwork_types: tuple[ArtworkType, ...],
    counts: tuple[int, ...],
) -> tuple[RowPattern, ...]:
    patterns: list[RowPattern] = []
    seen: set[tuple[tuple[int, ...], tuple[tuple[str, bool], ...]]] = set()
    ranges = [range(count + 1) for count in counts]
    for pattern_counts in itertools.product(*ranges):
        if not any(pattern_counts):
            continue

        pattern_items = tuple(
            artwork
            for artwork, count in zip(artwork_types, pattern_counts, strict=True)
            for _ in range(count)
        )
        for rotated_types in itertools.product((False, True), repeat=len(artwork_types)):
            rotation_by_name = {
                artwork.name: rotated
                for artwork, rotated in zip(artwork_types, rotated_types, strict=True)
            }
            for ordering in _practical_orderings(pattern_items):
                oriented = tuple(_oriented_item(item, rotation_by_name[item.name]) for item in ordering)
                pattern = _pack_row_pattern(oriented, pattern_counts)
                if pattern is None:
                    continue

                signature = (
                    pattern.counts,
                    tuple((item.artwork, item.rotated) for item in pattern.placements),
                )
                if signature in seen:
                    continue
                seen.add(signature)
                patterns.append(pattern)

    patterns.sort(key=lambda pattern: (-sum(pattern.counts), pattern.height_in, pattern.width_in, pattern.counts))
    return tuple(patterns)


def _pack_row_pattern(oriented_items: tuple[PlacedItem, ...], counts: tuple[int, ...]) -> RowPattern | None:
    x = 0.0
    row_items: list[PlacedItem] = []
    for item in oriented_items:
        item_gap = _horizontal_gap(row_items[-1], item, DEFAULT_ITEM_GAP_IN) if row_items else 0.0
        candidate_x = x + item_gap
        if candidate_x + item.width_in > SAFE_AREA_WIDTH_IN:
            return None

        placed = PlacedItem(
            artwork=item.artwork,
            x_in=candidate_x,
            y_in=0.0,
            width_in=item.width_in,
            height_in=item.height_in,
            rotated=item.rotated,
        )
        row_items.append(placed)
        x = placed.right_in

    return RowPattern(
        placements=tuple(row_items),
        counts=counts,
        width_in=max((item.right_in for item in row_items), default=0.0),
        height_in=max((item.height_in for item in row_items), default=0.0),
    )


def _guillotine_layouts_for_counts(
    artwork_types: tuple[ArtworkType, ...],
    counts: tuple[int, ...],
    max_split_layouts: int = 200,
) -> Iterable[LayoutCandidate]:
    emitted = 0
    for left_counts in _proper_count_splits(counts):
        if emitted >= max_split_layouts:
            return

        right_counts = _subtract_counts(counts, left_counts)
        for left in _best_local_layouts(artwork_types, left_counts, limit=4):
            for right in _best_local_layouts(artwork_types, right_counts, limit=4):
                layout = _combine_vertical_guillotine(left, right, counts)
                if layout is not None:
                    emitted += 1
                    yield layout
                layout = _combine_horizontal_guillotine(left, right, counts)
                if layout is not None:
                    emitted += 1
                    yield layout


def _best_local_layouts(
    artwork_types: tuple[ArtworkType, ...],
    counts: tuple[int, ...],
    limit: int,
) -> tuple[LayoutCandidate, ...]:
    items = tuple(
        artwork
        for artwork, count in zip(artwork_types, counts, strict=True)
        for _ in range(count)
    )
    if not items:
        return ()

    layouts: list[LayoutCandidate] = []
    for ordering in _practical_orderings(items):
        for rotated_types in itertools.product((False, True), repeat=len(artwork_types)):
            rotation_by_name = {
                artwork.name: rotated
                for artwork, rotated in zip(artwork_types, rotated_types, strict=True)
            }
            oriented = tuple(_oriented_item(item, rotation_by_name[item.name]) for item in ordering)
            layout = _pack_shelf(oriented, counts, method="guillotine_part")
            if layout is not None:
                layouts.append(layout)

    layouts.sort(key=lambda layout: (layout.score, layout.used_area_in2), reverse=True)
    return tuple(layouts[:limit])


def _combine_vertical_guillotine(
    left: LayoutCandidate,
    right: LayoutCandidate,
    counts: tuple[int, ...],
) -> LayoutCandidate | None:
    split_gap = DEFAULT_ITEM_GAP_IN
    offset_x = left.used_width_in + split_gap
    if offset_x + right.used_width_in > SAFE_AREA_WIDTH_IN:
        return None
    if max(left.used_height_in, right.used_height_in) > SAFE_AREA_HEIGHT_IN:
        return None

    placements = left.placements + _translate_placements(right.placements, dx=offset_x, dy=0.0)
    return _candidate_from_placements(placements, counts, method="guillotine_vertical", gap_in=DEFAULT_ITEM_GAP_IN)


def _combine_horizontal_guillotine(
    top: LayoutCandidate,
    bottom: LayoutCandidate,
    counts: tuple[int, ...],
) -> LayoutCandidate | None:
    split_gap = DEFAULT_ITEM_GAP_IN
    offset_y = top.used_height_in + split_gap
    if offset_y + bottom.used_height_in > SAFE_AREA_HEIGHT_IN:
        return None
    if max(top.used_width_in, bottom.used_width_in) > SAFE_AREA_WIDTH_IN:
        return None

    placements = top.placements + _translate_placements(bottom.placements, dx=0.0, dy=offset_y)
    return _candidate_from_placements(placements, counts, method="guillotine_horizontal", gap_in=DEFAULT_ITEM_GAP_IN)


def _translate_placements(placements: tuple[PlacedItem, ...], dx: float, dy: float) -> tuple[PlacedItem, ...]:
    return tuple(
        PlacedItem(
            artwork=item.artwork,
            x_in=item.x_in + dx,
            y_in=item.y_in + dy,
            width_in=item.width_in,
            height_in=item.height_in,
            rotated=item.rotated,
        )
        for item in placements
    )


def _candidate_from_placements(
    placements: tuple[PlacedItem, ...],
    counts: tuple[int, ...],
    method: str,
    gap_in: float,
) -> LayoutCandidate | None:
    if not placements:
        return None
    if any(item.x_in < 0 or item.y_in < 0 or item.right_in > SAFE_AREA_WIDTH_IN or item.bottom_in > SAFE_AREA_HEIGHT_IN for item in placements):
        return None

    used_width = max(item.right_in for item in placements)
    used_height = max(item.bottom_in for item in placements)
    used_area = sum(item.width_in * item.height_in for item in placements)
    utilization = used_area / (SAFE_AREA_WIDTH_IN * SAFE_AREA_HEIGHT_IN)
    footprint_area = max(1.0, used_width * used_height)
    footprint_fill = used_area / footprint_area
    method_bonus = 2 if method.startswith("guillotine") else 1 if method == "row_grouping" else 0
    score = utilization * 10_000 + footprint_fill * 1_000 + len(placements) + method_bonus

    return LayoutCandidate(
        placements=placements,
        counts=counts,
        used_area_in2=used_area,
        used_width_in=used_width,
        used_height_in=used_height,
        utilization=utilization,
        score=score,
        gap_in=gap_in,
        method=method,
    )


def _oriented_item(artwork: ArtworkType, rotated: bool) -> PlacedItem:
    width = artwork.height_in if rotated else artwork.width_in
    height = artwork.width_in if rotated else artwork.height_in
    return PlacedItem(artwork=artwork.name, x_in=0.0, y_in=0.0, width_in=width, height_in=height, rotated=rotated)


def _horizontal_gap(left: PlacedItem, right: PlacedItem, default_gap_in: float) -> float:
    if _same_length(left.height_in, right.height_in):
        return 0.0

    return default_gap_in


def _same_length(first: float, second: float) -> bool:
    return abs(first - second) < 0.001


def _quantity_combinations(type_count: int, max_quantity_per_type: int) -> Iterable[tuple[int, ...]]:
    ranges = [range(max_quantity_per_type + 1) for _ in range(type_count)]
    combinations = sorted(
        (counts for counts in itertools.product(*ranges) if any(counts)),
        key=lambda counts: (sum(counts), counts),
    )
    return combinations


def _proper_count_splits(counts: tuple[int, ...]) -> Iterable[tuple[int, ...]]:
    ranges = [range(count + 1) for count in counts]
    for split in itertools.product(*ranges):
        if not any(split):
            continue
        if split == counts:
            continue
        yield split


def _counts_fit(counts: tuple[int, ...], remaining: tuple[int, ...]) -> bool:
    return all(count <= available for count, available in zip(counts, remaining, strict=True))


def _subtract_counts(counts: tuple[int, ...], subtract: tuple[int, ...]) -> tuple[int, ...]:
    return tuple(count - remove for count, remove in zip(counts, subtract, strict=True))


def _practical_orderings(items: tuple[ArtworkType, ...]) -> tuple[tuple[ArtworkType, ...], ...]:
    key_functions = (
        lambda item: (-item.area_in2, -max(item.width_in, item.height_in), item.name),
        lambda item: (-max(item.width_in, item.height_in), -item.area_in2, item.name),
        lambda item: (-item.width_in, -item.height_in, item.name),
        lambda item: (-item.height_in, -item.width_in, item.name),
        lambda item: (item.name,),
    )
    orderings: list[tuple[ArtworkType, ...]] = []
    seen: set[tuple[str, ...]] = set()
    for key_function in key_functions:
        ordering = tuple(sorted(items, key=key_function))
        signature = tuple(item.name for item in ordering)
        if signature not in seen:
            seen.add(signature)
            orderings.append(ordering)

    return tuple(orderings)


def _record_candidate(
    best: list[LayoutCandidate],
    candidate: LayoutCandidate,
    max_results: int,
) -> list[LayoutCandidate]:
    best.append(candidate)
    best.sort(key=lambda layout: (layout.score, layout.utilization, sum(layout.counts)), reverse=True)

    deduped: list[LayoutCandidate] = []
    seen: set[tuple[int, ...]] = set()
    for layout in best:
        if layout.counts in seen:
            continue
        seen.add(layout.counts)
        deduped.append(layout)
        if len(deduped) >= max_results:
            break

    return deduped


def _stats(
    iterations: int,
    start: float,
    best: list[LayoutCandidate],
    quantity_combos_checked: int,
    quantity_combos_total: int,
    current_counts: tuple[int, ...] | None,
) -> SearchStats:
    elapsed = max(0.000001, time.monotonic() - start)
    best_layout = best[0] if best else None
    return SearchStats(
        iterations=iterations,
        quantity_combos_checked=quantity_combos_checked,
        quantity_combos_total=quantity_combos_total,
        current_counts=current_counts,
        elapsed_s=elapsed,
        iterations_per_s=iterations / elapsed,
        best_score=best_layout.score if best_layout else 0.0,
        best_utilization=best_layout.utilization if best_layout else 0.0,
        best_count=sum(best_layout.counts) if best_layout else 0,
        results_kept=len(best),
    )


def _print_stats(stream: TextIO, stats: SearchStats, final: bool = False) -> None:
    prefix = "FINAL" if final else "STATS"
    combo_text = (
        f"{stats.quantity_combos_checked}/{stats.quantity_combos_total}"
        if stats.quantity_combos_total
        else "0/0"
    )
    current_counts = stats.current_counts if stats.current_counts is not None else "-"
    stream.write(
        f"{prefix} elapsed={stats.elapsed_s:.1f}s "
        f"combos={combo_text} "
        f"current_counts={current_counts} "
        f"iterations={stats.iterations} "
        f"iter/s={stats.iterations_per_s:.0f} "
        f"results={stats.results_kept} "
        f"best_util={stats.best_utilization:.2%} "
        f"best_count={stats.best_count} "
        f"best_score={stats.best_score:.1f}\n"
    )
    stream.flush()


def _layout_to_dict(layout: LayoutCandidate) -> dict[str, object]:
    return {
        "counts": layout.counts,
        "used_area_in2": layout.used_area_in2,
        "used_width_in": layout.used_width_in,
        "used_height_in": layout.used_height_in,
        "utilization": layout.utilization,
        "score": layout.score,
        "gap_in": layout.gap_in,
        "method": layout.method,
        "placements": [asdict(item) for item in layout.placements],
    }


def _counts_slug(counts: tuple[int, ...]) -> str:
    return "x".join(str(count) for count in counts)


def _preview_font() -> ImageFont.ImageFont:
    return ImageFont.load_default()


def main() -> int:
    parser = argparse.ArgumentParser(description="Search predefined print nesting layouts.")
    parser.add_argument("--output", type=Path, default=Path("data/layout_search_results.json"))
    parser.add_argument("--time-limit", type=float, default=None)
    parser.add_argument("--max-quantity", type=int, default=12)
    parser.add_argument("--max-results", type=int, default=100)
    parser.add_argument("--stats-interval", type=float, default=0.5)
    parser.add_argument("--preview-count", type=int, default=10)
    args = parser.parse_args()

    run_layout_search(
        output_path=args.output,
        time_limit_s=args.time_limit,
        max_quantity_per_type=args.max_quantity,
        max_results=args.max_results,
        stats_interval_s=args.stats_interval,
        preview_count=args.preview_count,
        stream=sys.stdout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
