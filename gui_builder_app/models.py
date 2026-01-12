from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class Tool(str, Enum):
    BACKGROUND = "background"
    BUTTON_STANDARD = "button_standard"
    BUTTON_PRESS = "button_press"
    BUTTON_TOGGLE = "button_toggle"
    TEXT_ENTRY = "text_entry"
    SELECT_LIST = "select_list"
    TEXT_SLOT = "text_slot"
    ITEM_SLOT = "item_slot"


SQUARE_ONLY = {
    Tool.ITEM_SLOT,
}


@dataclass
class Rect:
    x0: int
    y0: int
    x1: int
    y1: int

    def normalized(self) -> "Rect":
        ax0, ax1 = sorted((self.x0, self.x1))
        ay0, ay1 = sorted((self.y0, self.y1))
        return Rect(ax0, ay0, ax1, ay1)

    def width(self) -> int:
        r = self.normalized()
        return r.x1 - r.x0 + 1

    def height(self) -> int:
        r = self.normalized()
        return r.y1 - r.y0 + 1

    def cells(self) -> List[Tuple[int, int]]:
        r = self.normalized()
        return [(x, y) for y in range(r.y0, r.y1 + 1) for x in range(r.x0, r.x1 + 1)]


@dataclass
class Entry:
    entry_id: int
    tool: Tool
    rect: Rect
    uid: int = 0
    active: bool = False
    label: str = ""  # used for text_entry content / selected list value / etc
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PageState:
    page_id: int
    background: List[List[bool]]
    entries: Dict[int, Entry]
    cell_to_entry: List[List[Optional[int]]]
    next_entry_id: int = 1
