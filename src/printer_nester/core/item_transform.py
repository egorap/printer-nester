from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ItemTransform:
    x_in: float
    y_in: float
    width_in: float
    height_in: float
    rotation_deg: float