from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class ArtworkItem:
    source_path: Path
    name: str


@dataclass(slots=True)
class PrintProject:
    artwork: list[ArtworkItem] = field(default_factory=list)

    def add_artwork(self, path: Path) -> ArtworkItem:
        item = ArtworkItem(source_path=path, name=path.stem)
        self.artwork.append(item)
        return item
