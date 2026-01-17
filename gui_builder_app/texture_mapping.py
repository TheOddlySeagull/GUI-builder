"""Texture atlas mapping for GUI modules.

Atlas layout is configured via `texture_mapping.json` (sibling to this file).
All coordinates are in TILE UNITS (e.g. 16x16), not pixels.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional, Tuple

from .models import Tool


def _default_config_path() -> str:
    return os.path.join(os.path.dirname(__file__), "texture_mapping.json")


def _load_mapping_config(path: Optional[str] = None) -> Dict[str, Any]:
    cfg_path = path or _default_config_path()
    with open(cfg_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("texture_mapping.json root must be an object")
    return data


def _origin_from_obj(obj: Any, *, ctm_size: int) -> Tuple[int, int]:
    if not isinstance(obj, dict):
        raise ValueError("ctm_origins entries must be objects")
    if "tile" in obj:
        tx, ty = obj.get("tile")
        return (int(tx), int(ty))
    if "module" in obj:
        mx, my = obj.get("module")
        return (int(mx) * int(ctm_size), int(my) * int(ctm_size))
    raise ValueError("ctm_origins entries must have 'tile' or 'module'")


_CFG = _load_mapping_config()

TILE_PX = int(_CFG.get("tile_px", 16))
_CTM_SIZE_TILES = int(((isinstance(_CFG.get("module"), dict) and _CFG.get("module") or {}).get("ctm_size_tiles", 4)))

_SK = _CFG.get("skin_pack") if isinstance(_CFG.get("skin_pack"), dict) else {}

# Skin pack layout (next to gui_builder.py):
#   skin_packs/<skin_name>/Modules.png
#   skin_packs/<skin_name>/Background.png
SKIN_PACKS_DIRNAME = str(_SK.get("dir") or "skin_packs")
MODULES_FILENAME = str(_SK.get("modules") or "Modules.png")
BACKGROUND_FILENAME = str(_SK.get("background") or "Background.png")


# Connected texture layout assumptions (tile coordinates, NOT pixels).
# Each state is expected to be a NxN CTM block, addressed by a 4-neighbor bitmask.
CTM_ORIGINS: Dict[str, Tuple[int, int]] = {}
_origins = _CFG.get("ctm_origins")
if not isinstance(_origins, dict):
    raise ValueError("texture_mapping.json must contain object 'ctm_origins'")
for key, obj in _origins.items():
    if not isinstance(key, str):
        continue
    CTM_ORIGINS[str(key)] = _origin_from_obj(obj, ctm_size=_CTM_SIZE_TILES)


# For each entry tool, define which module keys to use.
ENTRY_TOOL_MODULES: Dict[Tool, Dict[str, str]] = {}
_mods = _CFG.get("entry_tool_modules")
if not isinstance(_mods, dict):
    raise ValueError("texture_mapping.json must contain object 'entry_tool_modules'")

for tool_key, mapping in _mods.items():
    if not isinstance(tool_key, str) or not isinstance(mapping, dict):
        continue
    try:
        tool = Tool(tool_key)
    except Exception:
        # Ignore unknown tools.
        continue
    ENTRY_TOOL_MODULES[tool] = {str(k): str(v) for k, v in mapping.items() if isinstance(k, str) and v is not None}


_CTM_MASK_TO_OFFSET: Optional[Dict[int, Tuple[int, int]]] = None
_mask_cfg = _CFG.get("ctm_mask_to_offset")
if isinstance(_mask_cfg, dict):
    tmp: Dict[int, Tuple[int, int]] = {}
    for k, v in _mask_cfg.items():
        try:
            kk = int(k)
        except Exception:
            continue
        if isinstance(v, (list, tuple)) and len(v) == 2:
            tmp[kk & 0xF] = (int(v[0]), int(v[1]))
    _CTM_MASK_TO_OFFSET = tmp if tmp else None

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
    if _CTM_MASK_TO_OFFSET is not None:
        out = _CTM_MASK_TO_OFFSET.get(m)
        if out is not None:
            return out
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
