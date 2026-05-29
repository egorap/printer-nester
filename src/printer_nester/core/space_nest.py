from __future__ import annotations

from dataclasses import dataclass


DEFAULT_ITEM_GAP_IN = 0.2


@dataclass(frozen=True, slots=True)
class SpaceItem:
    key: str
    width_in: float
    height_in: float


@dataclass(frozen=True, slots=True)
class SpaceRect:
    x_in: float
    y_in: float
    width_in: float
    height_in: float

    @property
    def right_in(self) -> float:
        return self.x_in + self.width_in

    @property
    def bottom_in(self) -> float:
        return self.y_in + self.height_in

    @property
    def area(self) -> float:
        return self.width_in * self.height_in


@dataclass(frozen=True, slots=True)
class SpacePlacement:
    key: str
    sheet_index: int
    x_in: float
    y_in: float
    width_in: float
    height_in: float
    rotated: bool


def fill_space_placements(
    items: list[SpaceItem],
    sheet_count: int,
    safe_width_in: float,
    safe_height_in: float,
    occupied_by_sheet: dict[int, list[SpaceRect]],
    gap_in: float = DEFAULT_ITEM_GAP_IN,
) -> list[SpacePlacement]:
    if safe_width_in <= 0 or safe_height_in <= 0:
        return []

    free_by_sheet = {
        sheet_index: _free_rects_after_occupancy(
            SpaceRect(0, 0, safe_width_in, safe_height_in),
            occupied_by_sheet.get(sheet_index, []),
        )
        for sheet_index in range(max(1, sheet_count))
    }
    obstacles_by_sheet = {
        sheet_index: list(occupied_by_sheet.get(sheet_index, []))
        for sheet_index in range(max(1, sheet_count))
    }
    ordered_items = sorted(items, key=lambda item: item.width_in * item.height_in, reverse=True)
    placements: list[SpacePlacement] = []

    for item in ordered_items:
        placement = _place_item(item, free_by_sheet, obstacles_by_sheet, safe_width_in, safe_height_in, gap_in)
        if placement is None:
            sheet_index = len(free_by_sheet)
            free_by_sheet[sheet_index] = [SpaceRect(0, 0, safe_width_in, safe_height_in)]
            obstacles_by_sheet[sheet_index] = []
            placement = _place_item(item, free_by_sheet, obstacles_by_sheet, safe_width_in, safe_height_in, gap_in)
        if placement is not None:
            placements.append(placement)

    return placements


def _place_item(
    item: SpaceItem,
    free_by_sheet: dict[int, list[SpaceRect]],
    obstacles_by_sheet: dict[int, list[SpaceRect]],
    safe_width_in: float,
    safe_height_in: float,
    gap_in: float,
) -> SpacePlacement | None:
    best: tuple[tuple[float, float, float, float, int], int, int, SpaceRect, bool] | None = None
    for sheet_index in sorted(free_by_sheet):
        for free_index, free_rect in enumerate(free_by_sheet[sheet_index]):
            for width_in, height_in, rotated in (
                (item.width_in, item.height_in, False),
                (item.height_in, item.width_in, True),
            ):
                for candidate in _candidate_rects(free_rect, width_in, height_in, gap_in):
                    if not _candidate_is_valid(
                        candidate,
                        obstacles_by_sheet.get(sheet_index, []),
                        safe_width_in,
                        safe_height_in,
                        gap_in,
                    ):
                        continue

                    leftover_area = free_rect.area - width_in * height_in
                    short_side_leftover = min(free_rect.width_in - width_in, free_rect.height_in - height_in)
                    score = (leftover_area, short_side_leftover, candidate.y_in, candidate.x_in, sheet_index)
                    if best is None or score < best[0]:
                        best = (score, sheet_index, free_index, candidate, rotated)

    if best is None:
        return None

    _score, sheet_index, _free_index, placed_rect, rotated = best
    free_by_sheet[sheet_index] = _split_free_rects(free_by_sheet[sheet_index], placed_rect)
    obstacles_by_sheet.setdefault(sheet_index, []).append(placed_rect)
    return SpacePlacement(
        key=item.key,
        sheet_index=sheet_index,
        x_in=placed_rect.x_in,
        y_in=placed_rect.y_in,
        width_in=placed_rect.width_in,
        height_in=placed_rect.height_in,
        rotated=rotated,
    )


def _free_rects_after_occupancy(sheet_rect: SpaceRect, occupied_rects: list[SpaceRect]) -> list[SpaceRect]:
    free_rects = [sheet_rect]
    for occupied_rect in occupied_rects:
        free_rects = _split_free_rects(free_rects, occupied_rect)
    return free_rects


def _clearance_rect(rect: SpaceRect, safe_width_in: float, safe_height_in: float, gap_in: float) -> SpaceRect:
    x = max(0.0, rect.x_in - gap_in)
    y = max(0.0, rect.y_in - gap_in)
    right = min(safe_width_in, rect.right_in + gap_in)
    bottom = min(safe_height_in, rect.bottom_in + gap_in)
    return SpaceRect(x, y, right - x, bottom - y)


def _candidate_rects(free_rect: SpaceRect, width_in: float, height_in: float, gap_in: float) -> list[SpaceRect]:
    candidates: list[SpaceRect] = []
    for x_in in (free_rect.x_in, free_rect.x_in + gap_in):
        for y_in in (free_rect.y_in, free_rect.y_in + gap_in):
            candidate = SpaceRect(x_in, y_in, width_in, height_in)
            if candidate.right_in <= free_rect.right_in + 0.001 and candidate.bottom_in <= free_rect.bottom_in + 0.001:
                candidates.append(candidate)
    return candidates


def _candidate_is_valid(
    candidate: SpaceRect,
    obstacles: list[SpaceRect],
    safe_width_in: float,
    safe_height_in: float,
    gap_in: float,
) -> bool:
    for obstacle in obstacles:
        if _rects_overlap(candidate, obstacle):
            return False

        clearance = _clearance_rect(obstacle, safe_width_in, safe_height_in, gap_in)
        if _rects_overlap(candidate, clearance) and not _shares_same_length_edge(candidate, obstacle):
            return False

    return True


def _shares_same_length_edge(first: SpaceRect, second: SpaceRect) -> bool:
    tolerance = 0.001
    vertical_touch = abs(first.right_in - second.x_in) <= tolerance or abs(second.right_in - first.x_in) <= tolerance
    if vertical_touch and abs(first.y_in - second.y_in) <= tolerance and abs(first.height_in - second.height_in) <= tolerance:
        return True

    horizontal_touch = abs(first.bottom_in - second.y_in) <= tolerance or abs(second.bottom_in - first.y_in) <= tolerance
    return horizontal_touch and abs(first.x_in - second.x_in) <= tolerance and abs(first.width_in - second.width_in) <= tolerance


def _split_free_rects(free_rects: list[SpaceRect], used_rect: SpaceRect) -> list[SpaceRect]:
    next_rects: list[SpaceRect] = []
    for free_rect in free_rects:
        if not _rects_overlap(free_rect, used_rect):
            next_rects.append(free_rect)
            continue

        if used_rect.x_in > free_rect.x_in:
            next_rects.append(
                SpaceRect(free_rect.x_in, free_rect.y_in, used_rect.x_in - free_rect.x_in, free_rect.height_in)
            )
        if used_rect.right_in < free_rect.right_in:
            next_rects.append(
                SpaceRect(used_rect.right_in, free_rect.y_in, free_rect.right_in - used_rect.right_in, free_rect.height_in)
            )
        if used_rect.y_in > free_rect.y_in:
            next_rects.append(
                SpaceRect(free_rect.x_in, free_rect.y_in, free_rect.width_in, used_rect.y_in - free_rect.y_in)
            )
        if used_rect.bottom_in < free_rect.bottom_in:
            next_rects.append(
                SpaceRect(
                    free_rect.x_in,
                    used_rect.bottom_in,
                    free_rect.width_in,
                    free_rect.bottom_in - used_rect.bottom_in,
                )
            )

    return _prune_free_rects(next_rects)


def _prune_free_rects(rects: list[SpaceRect]) -> list[SpaceRect]:
    valid = [rect for rect in rects if rect.width_in > 0.001 and rect.height_in > 0.001]
    pruned: list[SpaceRect] = []
    for index, rect in enumerate(valid):
        if any(index != other_index and _contains_rect(other, rect) for other_index, other in enumerate(valid)):
            continue
        if rect not in pruned:
            pruned.append(rect)
    return sorted(pruned, key=lambda rect: (rect.y_in, rect.x_in, rect.area))


def _rects_overlap(first: SpaceRect, second: SpaceRect) -> bool:
    return (
        first.x_in < second.right_in - 0.001
        and first.right_in > second.x_in + 0.001
        and first.y_in < second.bottom_in - 0.001
        and first.bottom_in > second.y_in + 0.001
    )


def _contains_rect(container: SpaceRect, rect: SpaceRect) -> bool:
    return (
        rect.x_in >= container.x_in - 0.001
        and rect.y_in >= container.y_in - 0.001
        and rect.right_in <= container.right_in + 0.001
        and rect.bottom_in <= container.bottom_in + 0.001
    )
