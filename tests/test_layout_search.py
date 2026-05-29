from __future__ import annotations

import io
import json
from pathlib import Path

from printer_nester.core.layout_search import (
    DEFAULT_ITEM_GAP_IN,
    SAFE_AREA_HEIGHT_IN,
    SAFE_AREA_WIDTH_IN,
    PlacedItem,
    _guillotine_layouts_for_counts,
    _horizontal_gap,
    _pack_shelf,
    _candidate_layouts_for_counts,
    _quantity_combinations,
    _row_grouped_layouts_for_counts,
    default_artwork_types,
    render_layout_preview,
    run_layout_search,
    write_layout_previews,
)
from printer_nester.core.auto_nest import choose_layouts_for_counts, classify_preset_size


def _test_output_dir(name: str) -> Path:
    path = Path("test_outputs") / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_quantity_combinations_include_mixed_counts() -> None:
    combinations = list(_quantity_combinations(type_count=3, max_quantity_per_type=1))

    assert (1, 0, 0) in combinations
    assert (0, 1, 1) in combinations
    assert (1, 1, 1) in combinations
    assert (0, 0, 0) not in combinations


def test_classify_preset_size_matches_rotated_dimensions() -> None:
    match = classify_preset_size(24, 36)

    assert match is not None
    assert match.name == "36x24"
    assert match.rotated is True


def test_choose_layouts_for_counts_uses_largest_fitting_layouts() -> None:
    catalog = [
        {
            "score": 10,
            "utilization": 0.4,
            "placements": [{"artwork": "36x24"}, {"artwork": "36x24"}],
        },
        {
            "score": 20,
            "utilization": 0.7,
            "placements": [{"artwork": "36x24"}, {"artwork": "20x30"}, {"artwork": "24x18"}],
        },
        {
            "score": 5,
            "utilization": 0.2,
            "placements": [{"artwork": "36x24"}],
        },
    ]

    chosen = choose_layouts_for_counts({"36x24": 3, "20x30": 1, "24x18": 1}, catalog)

    assert len(chosen) == 2
    assert len(chosen[0]["placements"]) == 3
    assert len(chosen[1]["placements"]) == 2


def test_choose_layouts_prefers_fewer_sheets_over_greedy_fill() -> None:
    single = {"score": 1, "utilization": 0.1, "placements": [{"artwork": "36x24"}]}
    six_up = {"score": 100, "utilization": 0.8, "placements": [{"artwork": "36x24"} for _ in range(6)]}
    five_up = {"score": 50, "utilization": 0.7, "placements": [{"artwork": "36x24"} for _ in range(5)]}

    chosen = choose_layouts_for_counts({"36x24": 10}, [six_up, five_up, single])

    assert len(chosen) == 2
    assert all(len(layout["placements"]) == 5 for layout in chosen)


def test_choose_layouts_maximizes_non_last_sheet_utilization_after_sheet_count() -> None:
    high_util_four = {
        "score": 1,
        "utilization": 0.95,
        "placements": [{"artwork": "36x24"} for _ in range(4)],
    }
    low_util_four = {
        "score": 100,
        "utilization": 0.55,
        "placements": [{"artwork": "36x24"} for _ in range(4)],
    }
    two_up = {
        "score": 1,
        "utilization": 0.2,
        "placements": [{"artwork": "36x24"} for _ in range(2)],
    }

    chosen = choose_layouts_for_counts({"36x24": 6}, [low_util_four, two_up, high_util_four])

    assert len(chosen) == 2
    assert chosen[0] is high_util_four
    assert chosen[1] is two_up


def test_candidate_layouts_stay_inside_safe_area() -> None:
    layouts = list(_candidate_layouts_for_counts(default_artwork_types(), (1, 1, 1)))

    assert layouts
    for layout in layouts:
        assert layout.used_width_in <= SAFE_AREA_WIDTH_IN
        assert layout.used_height_in <= SAFE_AREA_HEIGHT_IN
        assert 0 < layout.utilization <= 1
        for item in layout.placements:
            assert item.x_in >= 0
            assert item.y_in >= 0
            assert item.right_in <= SAFE_AREA_WIDTH_IN
            assert item.bottom_in <= SAFE_AREA_HEIGHT_IN


def test_row_grouping_generator_produces_valid_layouts() -> None:
    layouts = list(_row_grouped_layouts_for_counts(default_artwork_types(), (2, 1, 1), max_layouts=20))

    assert layouts
    assert all(layout.method == "row_grouping" for layout in layouts)
    for layout in layouts:
        assert layout.used_width_in <= SAFE_AREA_WIDTH_IN
        assert layout.used_height_in <= SAFE_AREA_HEIGHT_IN
        assert sum(layout.counts) == len(layout.placements)


def test_guillotine_generator_produces_valid_layouts() -> None:
    layouts = list(_guillotine_layouts_for_counts(default_artwork_types(), (2, 1, 1), max_split_layouts=20))

    assert layouts
    assert all(layout.method.startswith("guillotine_") for layout in layouts)
    for layout in layouts:
        assert layout.used_width_in <= SAFE_AREA_WIDTH_IN
        assert layout.used_height_in <= SAFE_AREA_HEIGHT_IN
        assert sum(layout.counts) == len(layout.placements)


def test_same_length_sides_can_touch_horizontally() -> None:
    left = PlacedItem("a", 0, 0, 10, 20, False)
    right = PlacedItem("b", 0, 0, 12, 20, False)

    assert _horizontal_gap(left, right, DEFAULT_ITEM_GAP_IN) == 0


def test_different_length_sides_get_default_gap() -> None:
    left = PlacedItem("a", 0, 0, 10, 20, False)
    right = PlacedItem("b", 0, 0, 12, 18, False)

    assert _horizontal_gap(left, right, DEFAULT_ITEM_GAP_IN) == DEFAULT_ITEM_GAP_IN


def test_shelf_pack_applies_gap_for_different_height_neighbors() -> None:
    items = (
        PlacedItem("a", 0, 0, 10, 20, False),
        PlacedItem("b", 0, 0, 12, 18, False),
    )

    layout = _pack_shelf(items, counts=(1, 1), gap_in=DEFAULT_ITEM_GAP_IN)

    assert layout is not None
    assert layout.placements[1].x_in == 10 + DEFAULT_ITEM_GAP_IN


def test_shelf_pack_allows_touching_for_same_height_neighbors() -> None:
    items = (
        PlacedItem("a", 0, 0, 10, 20, False),
        PlacedItem("b", 0, 0, 12, 20, False),
    )

    layout = _pack_shelf(items, counts=(1, 1), gap_in=DEFAULT_ITEM_GAP_IN)

    assert layout is not None
    assert layout.placements[1].x_in == 10


def test_shelf_pack_applies_gap_between_rows() -> None:
    items = (
        PlacedItem("a", 0, 0, 80, 20, False),
        PlacedItem("b", 0, 0, 30, 10, False),
    )

    layout = _pack_shelf(items, counts=(1, 1), gap_in=DEFAULT_ITEM_GAP_IN)

    assert layout is not None
    assert layout.placements[1].x_in == 0
    assert layout.placements[1].y_in == 20 + DEFAULT_ITEM_GAP_IN


def test_layout_search_writes_best_results_and_stats() -> None:
    output_root = _test_output_dir("layout_search")
    output_path = output_root / "layouts.json"
    stream = io.StringIO()

    layouts = run_layout_search(
        output_path=output_path,
        time_limit_s=0.02,
        max_quantity_per_type=4,
        max_results=10,
        preview_count=3,
        stats_interval_s=0,
        stream=stream,
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    preview_dir = output_root / "layouts_previews"

    assert layouts
    assert payload["safe_area"] == {"width_in": SAFE_AREA_WIDTH_IN, "height_in": SAFE_AREA_HEIGHT_IN}
    assert payload["stats"]["iterations"] > 0
    assert payload["layouts"]
    assert payload["layouts"][0]["gap_in"] == DEFAULT_ITEM_GAP_IN
    assert "method" in payload["layouts"][0]
    assert len(payload["layouts"]) <= 10
    assert preview_dir.exists()
    assert len(list(preview_dir.glob("*.png"))) == 3
    assert "iterations=" in stream.getvalue()
    assert "best_util=" in stream.getvalue()
    assert "FINAL" in stream.getvalue()


def test_render_layout_preview_writes_png() -> None:
    layout = next(iter(_candidate_layouts_for_counts(default_artwork_types(), (1, 1, 1))))
    preview_path = _test_output_dir("layout_preview") / "preview.png"

    render_layout_preview(layout, preview_path)

    assert preview_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_write_layout_previews_names_by_rank_and_counts() -> None:
    layouts = list(_candidate_layouts_for_counts(default_artwork_types(), (1, 1, 1)))[:2]
    output_path = _test_output_dir("layout_previews") / "best_layouts.json"

    written = write_layout_previews(output_path, layouts)

    assert len(written) == 2
    assert written[0].name.startswith("layout_001_")
    assert written[0].suffix == ".png"
    assert written[0].exists()
