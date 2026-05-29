from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ArtboardSettings:
    width_in: float
    height_in: float
    margin_in: float


DEFAULT_ARTBOARD = ArtboardSettings(width_in=48.0, height_in=96.0, margin_in=0.25)
