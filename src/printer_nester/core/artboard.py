from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ArtboardSettings:
    width_in: float
    height_in: float
    margin_in: float


@dataclass(frozen=True, slots=True)
class SlotLayoutSettings:
    slot_width_in: float
    slot_height_in: float
    slot_margin_in: float
    horizontal_spacing_in: float
    vertical_spacing_in: float
    columns: int
    rows: int
    outside_margin_in: float

    @property
    def total_width_in(self) -> float:
        return (
            self.outside_margin_in * 2
            + self.slot_width_in * self.columns
            + self.horizontal_spacing_in * max(0, self.columns - 1)
        )

    @property
    def total_height_in(self) -> float:
        return (
            self.outside_margin_in * 2
            + self.slot_height_in * self.rows
            + self.vertical_spacing_in * max(0, self.rows - 1)
        )


DEFAULT_ARTBOARD = ArtboardSettings(width_in=48.0, height_in=96.0, margin_in=0.25)
DEFAULT_SLOT_LAYOUT = SlotLayoutSettings(
    slot_width_in=4.0,
    slot_height_in=4.0,
    slot_margin_in=0.0,
    horizontal_spacing_in=0.5,
    vertical_spacing_in=0.5,
    columns=3,
    rows=2,
    outside_margin_in=0.5,
)
