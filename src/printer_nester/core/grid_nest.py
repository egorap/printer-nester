from __future__ import annotations

from dataclasses import dataclass
import math


DEFAULT_ITEM_GAP_IN = 0.2


@dataclass(frozen=True, slots=True)
class GridItem:
    key: str
    width_in: float
    height_in: float


@dataclass(frozen=True, slots=True)
class GridPlacement:
    key: str
    sheet_index: int
    x_in: float
    y_in: float
    width_in: float
    height_in: float
    rotated: bool


def group_grid_placements(
    items: list[GridItem],
    safe_width_in: float,
    safe_height_in: float,
    gap_in: float = DEFAULT_ITEM_GAP_IN,
) -> list[GridPlacement]:
    groups = _groups_by_size(items)
    placements: list[GridPlacement] = []
    sheet_index = 0
    cursor_y = 0.0

    for group_index, group in enumerate(groups):
        if group_index > 0 and cursor_y > 0:
            cursor_y += gap_in
            if cursor_y >= safe_height_in:
                sheet_index += 1
                cursor_y = 0.0

        remaining = list(group)
        while remaining:
            available_height = safe_height_in - cursor_y
            option = _best_orientation(remaining, safe_width_in, available_height)
            if option is None:
                if cursor_y == 0:
                    break
                sheet_index += 1
                cursor_y = 0.0
                continue

            width_in, height_in, rotated, columns, rows, _placed_count, occupied_rows = option
            count = min(len(remaining), columns * rows)

            for index, item in enumerate(remaining[:count]):
                row = index % rows
                column = index // rows
                placements.append(
                    GridPlacement(
                        key=item.key,
                        sheet_index=sheet_index,
                        x_in=column * width_in,
                        y_in=cursor_y + row * height_in,
                        width_in=width_in,
                        height_in=height_in,
                        rotated=rotated,
                    )
                )

            del remaining[:count]
            cursor_y += occupied_rows * height_in
            if remaining:
                sheet_index += 1
                cursor_y = 0.0

        if cursor_y >= safe_height_in:
            sheet_index += 1
            cursor_y = 0.0

    return placements


def _groups_by_size(items: list[GridItem]) -> list[list[GridItem]]:
    groups: dict[tuple[float, float], list[GridItem]] = {}
    for item in items:
        width = round(item.width_in, 2)
        height = round(item.height_in, 2)
        key = tuple(sorted((width, height)))
        groups.setdefault(key, []).append(item)

    return sorted(groups.values(), key=lambda group: group[0].width_in * group[0].height_in, reverse=True)


def _best_orientation(
    items: list[GridItem],
    safe_width_in: float,
    available_height_in: float,
) -> tuple[float, float, bool, int, int, int, int] | None:
    item = items[0]
    options = [
        _orientation_option(len(items), safe_width_in, available_height_in, item.width_in, item.height_in, False),
        _orientation_option(len(items), safe_width_in, available_height_in, item.height_in, item.width_in, True),
    ]
    valid = [option for option in options if option is not None]
    if not valid:
        return None

    return max(valid, key=_orientation_score)


def _orientation_option(
    count: int,
    safe_width_in: float,
    available_height_in: float,
    width_in: float,
    height_in: float,
    rotated: bool,
) -> tuple[float, float, bool, int, int, int, int] | None:
    if width_in <= 0 or height_in <= 0:
        return None

    columns = math.floor(safe_width_in / width_in)
    rows = math.floor(available_height_in / height_in)
    if columns <= 0 or rows <= 0:
        return None

    placed_count = min(count, columns * rows)
    occupied_rows = min(rows, placed_count)
    return width_in, height_in, rotated, columns, rows, placed_count, occupied_rows


def _orientation_score(option: tuple[float, float, bool, int, int, int, int]) -> tuple[float, int, int, bool]:
    width_in, height_in, rotated, columns, _rows, placed_count, occupied_rows = option
    vertical_used = occupied_rows * height_in
    return vertical_used, placed_count, columns, not rotated
