"""Texture atlas mapping for GUI_CTM.png.

This is the intended single place to tweak when your atlas layout changes.
All coordinates are in TILE UNITS (16x16), not pixels.

Assumptions (current default):
- Each connected texture variant is a 4x4 block.
- That 4x4 block is addressed by a 4-neighbor bitmask.

If your atlas uses a different ordering for the 16 CTM variants,
adjust `ctm_tile_offset()`.
"""

from __future__ import annotations

from typing import Dict, Tuple

TILE_PX = 16
TEXTURE_SHEET_FILENAME = "GUI_CTM.png"

# Connected texture layout assumptions (tile coordinates, NOT pixels).
# Each state is expected to be a 4x4 CTM block, addressed by a 4-neighbor bitmask.
CTM_ORIGINS: Dict[str, Tuple[int, int]] = {
    # Buttons (connected-texture)
    "button_unpressed": (0, 0),
    "button_hover": (4, 0),
    "button_pressed": (0, 4),
    "button_pressed_hover": (4, 4),
    # Text areas / slots (connected-texture)
    "text_unpressed": (8, 0),
    "text_hover": (12, 0),
}

# Bit order for CTM 4-neighbor connections.
# mask bit0=N, bit1=E, bit2=S, bit3=W
CTM_DIRS: Tuple[Tuple[int, int, int], ...] = (
    (0, -1, 1),
    (1, 0, 2),
    (0, 1, 4),
    (-1, 0, 8),
)


def ctm_tile_offset(mask: int) -> Tuple[int, int]:
    """Map a 4-neighbor mask (0..15) into a 4x4 (dx,dy) tile offset.

    Current mapping is row-major order:
      (mask % 4, mask // 4)

    If your atlas uses a different order (common in CTM sheets), change it here.
    """

    m = mask & 0xF
    return (m % 4, m // 4)
