from __future__ import annotations

import tkinter as tk
from typing import Dict, Optional, Tuple


class TextureSheet:
    """Slices a PNG sheet into 16x16 tiles and provides scaled tiles for the current grid."""

    def __init__(self, root: tk.Misc, path: str, tile_px: int = 16) -> None:
        self._root = root
        self._path = path
        self.tile_px = tile_px
        self._src = tk.PhotoImage(file=path)
        self._cols = self._src.width() // tile_px
        self._rows = self._src.height() // tile_px
        self._cache: Dict[Tuple[int, int, int], tk.PhotoImage] = {}

    @property
    def cols(self) -> int:
        return self._cols

    @property
    def rows(self) -> int:
        return self._rows

    def _scale_factors(self, cell_px: int) -> Tuple[int, int]:
        """Return (zoom, subsample) factors so: tile_px * zoom / subsample == cell_px."""
        # In this app, cell_px is always 40 (16x16 grid) or 20 (32x32 grid).
        if cell_px == 40:
            return 5, 2  # 16*5/2=40
        if cell_px == 20:
            return 5, 4  # 16*5/4=20

        # Fallback: keep native size (will look small) instead of crashing.
        return 1, 1

    def get_tile(self, col: int, row: int, cell_px: int) -> Optional[tk.PhotoImage]:
        if col < 0 or row < 0 or col >= self._cols or row >= self._rows:
            return None

        key = (col, row, cell_px)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        x0 = col * self.tile_px
        y0 = row * self.tile_px
        x1 = x0 + self.tile_px
        y1 = y0 + self.tile_px

        tile = tk.PhotoImage(width=self.tile_px, height=self.tile_px)
        # Crop using Tk's internal copy command.
        tile.tk.call(tile, "copy", self._src, "-from", x0, y0, x1, y1, "-to", 0, 0)

        zoom, subsample = self._scale_factors(cell_px)
        if zoom != 1 or subsample != 1:
            tile = tile.zoom(zoom, zoom).subsample(subsample, subsample)

        self._cache[key] = tile
        return tile
