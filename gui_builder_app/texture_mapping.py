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

from .models import Tool

TILE_PX = 16
TEXTURE_SHEET_FILENAME = "GUI_CTM.png"
BACKGROUND_TEXTURES_DIRNAME = "backgrounds"


def module_origin(mx: int, my: int) -> Tuple[int, int]:
    """Convert a *module* coordinate to a tile origin.

    A module is a 4x4 CTM block, so module (mx,my) starts at tile (mx*4,my*4).
    """

    return (mx * 4, my * 4)

# Connected texture layout assumptions (tile coordinates, NOT pixels).
# Each state is expected to be a 4x4 CTM block, addressed by a 4-neighbor bitmask.
CTM_ORIGINS: Dict[str, Tuple[int, int]] = {
    # Buttons (connected-texture)
    "button_unpressed": module_origin(0, 0),
    "button_hover": module_origin(1, 0),
    "button_pressed": module_origin(0, 1),
    "button_pressed_hover": module_origin(1, 1),
    "text_unpressed": module_origin(2, 0),
    "text_hover": module_origin(3, 0),
    "item_slot": module_origin(2, 1),
    "item_slot_hover": module_origin(3, 1),
    "text_entry_border": module_origin(0, 2),
    "text_entry_border_hover": module_origin(1, 2),
    "select_list_border": module_origin(2, 2),
    "select_list_border_hover": module_origin(3, 2),
    "background_border": module_origin(0, 3),
    # Filler used when baking flat backgrounds: rendered under where buttons sit.
    # Placed immediately to the right of background_border in the atlas.
    "button_background": module_origin(1, 3),
}


# For each entry tool, define which module keys to use.
# Multiple keys are used when a tool has hover/pressed variants.
ENTRY_TOOL_MODULES: Dict[Tool, Dict[str, str]] = {
    Tool.BUTTON_STANDARD: {
        "base": "button_unpressed",
        "hover": "button_hover",
        "pressed": "button_pressed",
        "pressed_hover": "button_pressed_hover",
    },
    Tool.BUTTON_PRESS: {
        "base": "button_unpressed",
        "hover": "button_hover",
        "pressed": "button_pressed",
        "pressed_hover": "button_pressed_hover",
    },
    Tool.BUTTON_TOGGLE: {
        "base": "button_unpressed",
        "hover": "button_hover",
        "pressed": "button_pressed",
        "pressed_hover": "button_pressed_hover",
    },
    Tool.TEXT_SLOT: {
        "base": "text_unpressed",
        "hover": "text_hover",
    },
    Tool.TEXT_ENTRY: {
        "base": "text_entry_border",
        "hover": "text_entry_border_hover",
    },
    Tool.SELECT_LIST: {
        "base": "select_list_border",
        "hover": "select_list_border_hover",
    },
    Tool.ITEM_SLOT: {
        "base": "item_slot",
        "hover": "item_slot_hover",
    },
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

    This project uses a *structured* 4x4 layout per module (button, hover, etc):

    - (0,0): single-tile element
    - Row 0 (y=0, x=1..3): horizontal-only elements
        - (1,0): left cap   (E only)
        - (2,0): middle     (E + W)
        - (3,0): right cap  (W only)
    - Col 0 (x=0, y=1..3): vertical-only elements
        - (0,1): top cap    (S only)
        - (0,2): middle     (N + S)
        - (0,3): bottom cap (N only)
    - 3x3 block (x=1..3, y=1..3): nine-slice for multi-wide multi-tall rectangles
        arranged as:
          (1,1) TL | (2,1) T  | (3,1) TR
          (1,2) L  | (2,2) C  | (3,2) R
          (1,3) BL | (2,3) B  | (3,3) BR

    The input mask uses bits: N=1, E=2, S=4, W=8.
    """

    m = mask & 0xF
    n = bool(m & 0x1)
    e = bool(m & 0x2)
    s = bool(m & 0x4)
    w = bool(m & 0x8)

    # No neighbors -> single
    if not (n or e or s or w):
        return (0, 0)

    # Pure horizontal strip (no vertical neighbors)
    if not (n or s) and (e or w):
        if e and w:
            return (2, 0)
        if e:
            return (1, 0)
        return (3, 0)  # w only

    # Pure vertical strip (no horizontal neighbors)
    if not (e or w) and (n or s):
        if n and s:
            return (0, 2)
        if s:
            return (0, 1)
        return (0, 3)  # n only

    # 2D nine-slice for rectangles
    # X: left / middle / right
    if w and e:
        dx = 2
    elif w:
        dx = 3
    else:
        dx = 1

    # Y: top / middle / bottom
    if n and s:
        dy = 2
    elif n:
        dy = 3
    else:
        dy = 1

    return (dx, dy)
