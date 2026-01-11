from __future__ import annotations

import json
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import Any, Dict, List, Optional, Tuple

from .models import Entry, PageState, Rect, SQUARE_ONLY, Tool
from .texture import TextureSheet
from .texture_mapping import (
    BACKGROUND_TEXTURES_DIRNAME,
    CTM_DIRS,
    CTM_ORIGINS,
    TEXTURE_SHEET_FILENAME,
    TILE_PX,
    ctm_tile_offset,
)


class GuiBuilderApp:
    JSON_VERSION = 3

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("CustomNPCs GUI Builder (MVP)")

        # Optional texture sheet for preview rendering.
        self._texture_sheet: Optional[TextureSheet] = None

        self.grid_n = 16
        self.canvas_px = 640
        self.cell_px = self.canvas_px // self.grid_n

        # Multi-page data model
        self.pages: Dict[int, PageState] = {}
        self.current_page_id: int = 1
        self.start_page_id: int = 1

        # These are aliases to the currently selected page state.
        self.background: List[List[bool]]
        self.entries: Dict[int, Entry]
        self.cell_to_entry: List[List[Optional[int]]]
        self.next_entry_id: int

        self.pages[self.current_page_id] = self._new_page_state(self.current_page_id)
        self._set_current_page(self.current_page_id)

        self.current_tool: Tool = Tool.BACKGROUND
        self.preview_mode = False

        # Hover defaults for newly placed entries (stored into Entry.meta["hover"])
        self.hover_tool_defaults: Dict[Tool, Dict[str, Any]] = {
            t: {"enabled": False, "text": ""} for t in Tool if t != Tool.BACKGROUND
        }

        # Editor selection state (right-click)
        self.selected_entry_id: Optional[int] = None

        # Preview hover state
        self._preview_hover_entry_id: Optional[int] = None

        # Preview background texture selection (optional)
        self._background_texture_paths: Dict[str, str] = {}
        self._background_texture_name: str = "(none)"
        self._background_texture_src: Optional[tk.PhotoImage] = None
        self._background_texture_scaled: Dict[int, tk.PhotoImage] = {}
        self._preview_background_image: Optional[tk.PhotoImage] = None
        self._preview_background_cache_key: Optional[Tuple[int, int, int, str, str]] = None

        # Tool-level metadata (applies to newly placed entries while tool is selected)
        self.standard_button_tool_meta: Dict[str, Any] = {
            "page_change": {
                "mode": "none",  # none|goto|next|prev
                "target_page_id": 1,
                "modulo": True,
            }
        }

        # Editor drag state
        self._dragging = False
        self._drag_start: Optional[Tuple[int, int]] = None
        self._drag_end: Optional[Tuple[int, int]] = None
        self._drag_mode: Optional[str] = None  # editor: "place"/"erase"/"paint_on"/"paint_off"

        # Preview interaction state
        self._preview_pressed_entry_id: Optional[int] = None  # for press buttons held down

        self._build_menu()
        self._build_ui()
        self._bind_events()

        # Load texture sheet after Tk is initialized.
        self._load_texture_sheet()
        self._scan_background_textures()
        self.redraw()

    # ----------------------------
    # Pages
    # ----------------------------

    def _new_page_state(self, page_id: int) -> PageState:
        return PageState(
            page_id=page_id,
            background=[[False for _ in range(self.grid_n)] for _ in range(self.grid_n)],
            entries={},
            cell_to_entry=[[None for _ in range(self.grid_n)] for _ in range(self.grid_n)],
            next_entry_id=1,
        )

    def _sorted_page_ids(self) -> List[int]:
        return sorted(self.pages.keys())

    def _sync_current_page_back(self) -> None:
        st = self.pages[self.current_page_id]
        st.next_entry_id = self.next_entry_id

    def _set_current_page(self, page_id: int) -> None:
        if page_id not in self.pages:
            raise ValueError(f"Unknown page_id: {page_id}")
        self.current_page_id = page_id
        st = self.pages[page_id]
        self.background = st.background
        self.entries = st.entries
        self.cell_to_entry = st.cell_to_entry
        self.next_entry_id = st.next_entry_id

        # Selection is per-page; clear on page switch.
        self.selected_entry_id = None
        self._refresh_selection_ui()

        self._refresh_page_ui()

    def _refresh_page_ui(self) -> None:
        if hasattr(self, "page_var"):
            self.page_var.set(str(self.current_page_id))
        if hasattr(self, "page_count_var"):
            self.page_count_var.set(f"Pages: {len(self.pages)}")

    def goto_page(self, page_id: int) -> None:
        if page_id not in self.pages:
            self.set_status(f"Page {page_id} does not exist")
            self._refresh_page_ui()
            return
        self._sync_current_page_back()
        self._set_current_page(page_id)

        # When loading a page, deactivate any non-toggle buttons.
        self._deactivate_non_toggle_buttons(self.current_page_id)

        # Clear any held press interaction when switching pages.
        self._preview_pressed_entry_id = None

        # Clear hover state when switching pages.
        self._preview_hover_entry_id = None

        self.set_status(f"Switched to page {self.current_page_id}")
        self.redraw()

    def _deactivate_non_toggle_buttons(self, page_id: int) -> None:
        page = self.pages.get(page_id)
        if not page:
            return
        for ent in page.entries.values():
            if ent.tool in (Tool.BUTTON_STANDARD, Tool.BUTTON_PRESS):
                ent.active = False

    def goto_prev_page(self) -> None:
        ids = self._sorted_page_ids()
        if not ids:
            return
        idx = ids.index(self.current_page_id)
        if idx <= 0:
            self.set_status("Already at first page")
            return
        self.goto_page(ids[idx - 1])

    def goto_next_page(self) -> None:
        ids = self._sorted_page_ids()
        if not ids:
            return
        idx = ids.index(self.current_page_id)
        if idx >= len(ids) - 1:
            self.set_status("Already at last page")
            return
        self.goto_page(ids[idx + 1])

    def add_page(self) -> None:
        new_id = (max(self.pages.keys()) + 1) if self.pages else 1
        self.pages[new_id] = self._new_page_state(new_id)
        self.goto_page(new_id)

    def delete_current_page(self) -> None:
        if len(self.pages) <= 1:
            messagebox.showinfo("Delete page", "Cannot delete the last remaining page.")
            return

        to_delete = self.current_page_id
        ids = self._sorted_page_ids()
        idx = ids.index(to_delete)
        fallback = ids[idx - 1] if idx > 0 else ids[idx + 1]

        del self.pages[to_delete]
        if self.start_page_id == to_delete:
            self.start_page_id = fallback
        self.goto_page(fallback)

    # ----------------------------
    # Menu
    # ----------------------------

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)
        filemenu = tk.Menu(menubar, tearoff=0)
        filemenu.add_command(label="Save JSON...", command=self.save_json)
        filemenu.add_command(label="Load JSON...", command=self.load_json)
        filemenu.add_separator()
        filemenu.add_command(label="Quit", command=self.root.quit)
        menubar.add_cascade(label="File", menu=filemenu)
        self.root.config(menu=menubar)

    # ----------------------------
    # UI
    # ----------------------------

    def _build_ui(self) -> None:
        outer = tk.Frame(self.root)
        outer.pack(fill="both", expand=True)

        # Fixed-width left pane so tool labels can wrap cleanly.
        left = tk.Frame(outer, padx=8, pady=8, width=230)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)

        right = tk.Frame(outer, padx=8, pady=8)
        right.pack(side="right", fill="both", expand=True)

        tk.Label(left, text="Tools", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        self.tool_var = tk.StringVar(value=self.current_tool.value)

        for t in Tool:
            rb = tk.Radiobutton(
                left,
                text=t.value,
                value=t.value,
                variable=self.tool_var,
                command=self._on_tool_changed,
                anchor="w",
                justify="left",
                wraplength=210,
            )
            rb.pack(fill="x", anchor="w")

        tk.Label(left, text="").pack()

        tk.Label(left, text="Page", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        self.page_count_var = tk.StringVar(value=f"Pages: {len(self.pages)}")
        tk.Label(left, textvariable=self.page_count_var, anchor="w", justify="left").pack(fill="x")

        page_ctrl = tk.Frame(left)
        page_ctrl.pack(fill="x", pady=(2, 0))

        tk.Button(page_ctrl, text="<", width=3, command=self.goto_prev_page).pack(side="left")
        tk.Button(page_ctrl, text=">", width=3, command=self.goto_next_page).pack(side="left", padx=(4, 0))

        self.page_var = tk.StringVar(value=str(self.current_page_id))
        self.page_entry = tk.Entry(page_ctrl, textvariable=self.page_var, width=6)
        self.page_entry.pack(side="left", padx=(6, 0))

        def on_page_enter(_e: tk.Event) -> None:
            try:
                pid = int(self.page_var.get())
            except ValueError:
                self._refresh_page_ui()
                return
            self.goto_page(pid)

        self.page_entry.bind("<Return>", on_page_enter)
        self.page_entry.bind("<FocusOut>", on_page_enter)

        tk.Button(left, text="New Page", command=self.add_page).pack(fill="x", pady=(4, 0))
        tk.Button(left, text="Delete Page", command=self.delete_current_page).pack(fill="x")

        tk.Label(left, text="").pack()

        # Tool-specific metadata panel (only visible for standard buttons)
        self.std_btn_meta_frame = tk.Frame(left)
        self.std_btn_meta_frame.pack(fill="x")

        tk.Label(self.std_btn_meta_frame, text="Standard Button", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        tk.Label(self.std_btn_meta_frame, text="Action: change page", anchor="w").pack(anchor="w")

        self.std_btn_action_var = tk.StringVar(value=self.standard_button_tool_meta["page_change"]["mode"])
        self.std_btn_target_var = tk.StringVar(value=str(self.standard_button_tool_meta["page_change"]["target_page_id"]))
        self.std_btn_modulo_var = tk.BooleanVar(value=bool(self.standard_button_tool_meta["page_change"]["modulo"]))

        def apply_std_meta() -> None:
            mode = self.std_btn_action_var.get()
            try:
                target_id = int(self.std_btn_target_var.get())
            except ValueError:
                target_id = 1
                self.std_btn_target_var.set("1")
            self.standard_button_tool_meta["page_change"] = {
                "mode": mode,
                "target_page_id": target_id,
                "modulo": bool(self.std_btn_modulo_var.get()),
            }

        def on_action_changed() -> None:
            apply_std_meta()
            self._refresh_std_btn_meta_visibility()

        tk.Radiobutton(
            self.std_btn_meta_frame,
            text="None",
            value="none",
            variable=self.std_btn_action_var,
            command=on_action_changed,
            anchor="w",
            justify="left",
        ).pack(fill="x", anchor="w")

        tk.Radiobutton(
            self.std_btn_meta_frame,
            text="Go to page ID",
            value="goto",
            variable=self.std_btn_action_var,
            command=on_action_changed,
            anchor="w",
            justify="left",
        ).pack(fill="x", anchor="w")

        self.std_btn_target_row = tk.Frame(self.std_btn_meta_frame)
        self.std_btn_target_row.pack(fill="x", padx=(18, 0), pady=(0, 2))
        tk.Label(self.std_btn_target_row, text="Target ID:").pack(side="left")
        target_entry = tk.Entry(self.std_btn_target_row, textvariable=self.std_btn_target_var, width=8)
        target_entry.pack(side="left", padx=(6, 0))
        target_entry.bind("<KeyRelease>", lambda _e: apply_std_meta())
        target_entry.bind("<FocusOut>", lambda _e: apply_std_meta())

        tk.Radiobutton(
            self.std_btn_meta_frame,
            text="Next page (ID+1)",
            value="next",
            variable=self.std_btn_action_var,
            command=on_action_changed,
            anchor="w",
            justify="left",
        ).pack(fill="x", anchor="w")

        tk.Radiobutton(
            self.std_btn_meta_frame,
            text="Previous page (ID-1)",
            value="prev",
            variable=self.std_btn_action_var,
            command=on_action_changed,
            anchor="w",
            justify="left",
        ).pack(fill="x", anchor="w")

        tk.Checkbutton(
            self.std_btn_meta_frame,
            text="Modulo wrap (last -> first)",
            variable=self.std_btn_modulo_var,
            command=apply_std_meta,
            anchor="w",
            justify="left",
            wraplength=210,
        ).pack(fill="x", anchor="w", pady=(2, 0))

        tk.Label(left, text="").pack()

        # Hover text defaults for newly placed elements (all tools except background)
        self.hover_defaults_frame = tk.Frame(left)
        self.hover_defaults_frame.pack(fill="x")

        tk.Label(self.hover_defaults_frame, text="Hover Text (new elements)", font=("TkDefaultFont", 10, "bold")).pack(
            anchor="w"
        )
        self.hover_default_enabled_var = tk.BooleanVar(value=False)
        self.hover_default_text_var = tk.StringVar(value="")

        def apply_hover_defaults() -> None:
            if self.current_tool == Tool.BACKGROUND:
                return
            self.hover_tool_defaults[self.current_tool] = {
                "enabled": bool(self.hover_default_enabled_var.get()),
                "text": str(self.hover_default_text_var.get()),
            }

        tk.Checkbutton(
            self.hover_defaults_frame,
            text="Show hover text",
            variable=self.hover_default_enabled_var,
            command=apply_hover_defaults,
            anchor="w",
            justify="left",
            wraplength=210,
        ).pack(fill="x", anchor="w")

        tk.Label(self.hover_defaults_frame, text="Text (optional):", anchor="w").pack(anchor="w")
        hover_text_entry = tk.Entry(self.hover_defaults_frame, textvariable=self.hover_default_text_var)
        hover_text_entry.pack(fill="x")
        hover_text_entry.bind("<KeyRelease>", lambda _e: apply_hover_defaults())
        hover_text_entry.bind("<FocusOut>", lambda _e: apply_hover_defaults())

        tk.Label(left, text="").pack()

        # Selected element meta panel (right-click an element in EDIT mode)
        self.selection_frame = tk.Frame(left)
        self.selection_frame.pack(fill="x")

        tk.Label(self.selection_frame, text="Selected Element", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        self.selected_info_var = tk.StringVar(value="(none)")
        tk.Label(self.selection_frame, textvariable=self.selected_info_var, anchor="w", justify="left").pack(fill="x")

        self.sel_hover_enabled_var = tk.BooleanVar(value=False)
        self.sel_hover_text_var = tk.StringVar(value="")

        def apply_selected_hover_meta() -> None:
            ent = self.entries.get(self.selected_entry_id) if self.selected_entry_id is not None else None
            if not ent:
                return
            meta = ent.meta if isinstance(ent.meta, dict) else {}
            meta["hover"] = {
                "enabled": bool(self.sel_hover_enabled_var.get()),
                "text": str(self.sel_hover_text_var.get()),
            }
            ent.meta = meta

        tk.Checkbutton(
            self.selection_frame,
            text="Show hover text",
            variable=self.sel_hover_enabled_var,
            command=apply_selected_hover_meta,
            anchor="w",
            justify="left",
            wraplength=210,
        ).pack(fill="x", anchor="w")

        tk.Label(self.selection_frame, text="Text (optional):", anchor="w").pack(anchor="w")
        sel_text_entry = tk.Entry(self.selection_frame, textvariable=self.sel_hover_text_var)
        sel_text_entry.pack(fill="x")
        sel_text_entry.bind("<KeyRelease>", lambda _e: apply_selected_hover_meta())
        sel_text_entry.bind("<FocusOut>", lambda _e: apply_selected_hover_meta())

        tk.Button(self.selection_frame, text="Clear selection", command=self._clear_selection).pack(fill="x", pady=(4, 0))

        tk.Label(left, text="Grid", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        self.grid_btn = tk.Button(left, text="Toggle 16×16 / 32×32", command=self.toggle_grid)
        self.grid_btn.pack(fill="x")

        tk.Label(left, text="Background (preview)", font=("TkDefaultFont", 10, "bold")).pack(anchor="w", pady=(8, 0))
        self.bg_texture_var = tk.StringVar(value="(none)")
        self.bg_texture_menu = tk.OptionMenu(left, self.bg_texture_var, "(none)", command=self._on_background_texture_changed)
        self.bg_texture_menu.pack(fill="x")

        tk.Button(left, text="Rescan backgrounds", command=self._scan_background_textures).pack(fill="x", pady=(4, 0))

        self.preview_btn = tk.Button(left, text="Preview: OFF", command=self.toggle_preview)
        self.preview_btn.pack(fill="x", pady=(6, 0))

        tk.Label(left, text="").pack()

        # Fixed height status area
        status_frame = tk.Frame(left, height=44)
        status_frame.pack(fill="x", pady=(0, 6))
        status_frame.pack_propagate(False)

        self.status_var = tk.StringVar(value="Ready")
        self.status = tk.Label(status_frame, textvariable=self.status_var, anchor="w", justify="left")
        self.status.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(
            right,
            width=self.canvas_px,
            height=self.canvas_px,
            bg="#1e1e1e",
            highlightthickness=0,
        )
        self.canvas.pack()

        self.help = tk.Label(
            right,
            text=(
                "EDIT MODE:\n"
                "  Left click/drag to place/remove\n"
                "PREVIEW MODE:\n"
                "  Interact with buttons, open text/select popups\n"
                "Square tools: item_slot\\n"
                "File -> Save/Load JSON"
            ),
            anchor="w",
            justify="left",
        )
        self.help.pack(fill="x", pady=(6, 0))

        self._refresh_std_btn_meta_visibility()
        self._refresh_hover_defaults_ui()
        self._refresh_selection_ui()

    def _bind_events(self) -> None:
        self.canvas.bind("<ButtonPress-1>", self.on_left_press)
        self.canvas.bind("<B1-Motion>", self.on_left_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_left_release)
        self.canvas.bind("<Motion>", self.on_motion)
        self.canvas.bind("<ButtonPress-3>", self.on_right_press)

    # ----------------------------
    # Helpers
    # ----------------------------

    def _background_to_rects(self, background: List[List[bool]]) -> List[Rect]:
        """Compress a boolean background grid into non-overlapping rectangles."""
        n = self.grid_n
        visited = [[False for _ in range(n)] for _ in range(n)]
        rects: List[Rect] = []

        for y in range(n):
            for x in range(n):
                if not background[y][x] or visited[y][x]:
                    continue

                # Grow width
                w = 1
                while x + w < n and background[y][x + w] and not visited[y][x + w]:
                    w += 1

                # Grow height as long as full next row segment is available
                h = 1
                can_grow = True
                while y + h < n and can_grow:
                    for xx in range(x, x + w):
                        if not background[y + h][xx] or visited[y + h][xx]:
                            can_grow = False
                            break
                    if can_grow:
                        h += 1

                # Mark visited
                for yy in range(y, y + h):
                    for xx in range(x, x + w):
                        visited[yy][xx] = True

                rects.append(Rect(x, y, x + w - 1, y + h - 1))

        return rects

    def _background_from_rects(self, rects: List[Rect]) -> List[List[bool]]:
        n = self.grid_n
        bg = [[False for _ in range(n)] for _ in range(n)]
        for r in rects:
            rr = r.normalized()
            for (x, y) in rr.cells():
                if 0 <= x < n and 0 <= y < n:
                    bg[y][x] = True
        return bg

    def _on_tool_changed(self) -> None:
        self.current_tool = Tool(self.tool_var.get())
        self.set_status(f"Tool: {self.current_tool.value}")
        self._refresh_std_btn_meta_visibility()
        self._refresh_hover_defaults_ui()

    def _refresh_std_btn_meta_visibility(self) -> None:
        if not hasattr(self, "std_btn_meta_frame"):
            return

        if self.current_tool == Tool.BUTTON_STANDARD:
            self.std_btn_meta_frame.pack(fill="x")
        else:
            self.std_btn_meta_frame.pack_forget()

        if hasattr(self, "std_btn_target_row") and hasattr(self, "std_btn_action_var"):
            if self.std_btn_action_var.get() == "goto":
                self.std_btn_target_row.pack(fill="x", padx=(18, 0), pady=(0, 2))
            else:
                self.std_btn_target_row.pack_forget()

    def _refresh_hover_defaults_ui(self) -> None:
        if not hasattr(self, "hover_defaults_frame"):
            return

        if self.current_tool == Tool.BACKGROUND:
            self.hover_defaults_frame.pack_forget()
            return

        self.hover_defaults_frame.pack(fill="x")
        defaults = self.hover_tool_defaults.get(self.current_tool, {"enabled": False, "text": ""})
        self.hover_default_enabled_var.set(bool(defaults.get("enabled", False)))
        self.hover_default_text_var.set(str(defaults.get("text", "")))

    def _refresh_selection_ui(self) -> None:
        if not hasattr(self, "selection_frame"):
            return

        ent = self.entries.get(self.selected_entry_id) if self.selected_entry_id is not None else None
        if not ent:
            self.selected_info_var.set("(none)")
            self.sel_hover_enabled_var.set(False)
            self.sel_hover_text_var.set("")
            return

        self.selected_info_var.set(f"ID {ent.entry_id} | {ent.tool.value}")
        hover = ent.meta.get("hover") if isinstance(ent.meta, dict) else None
        if not isinstance(hover, dict):
            hover = {"enabled": False, "text": ""}
        self.sel_hover_enabled_var.set(bool(hover.get("enabled", False)))
        self.sel_hover_text_var.set(str(hover.get("text", "")))

    def _clear_selection(self) -> None:
        self.selected_entry_id = None
        self._refresh_selection_ui()

    def set_status(self, msg: str) -> None:
        self.status_var.set(msg)

    def _hover_text_enabled_for(self, ent: Entry) -> bool:
        meta = ent.meta if isinstance(ent.meta, dict) else {}
        hover = meta.get("hover")
        if not isinstance(hover, dict):
            return False
        return bool(hover.get("enabled", False))

    def _hover_text_for(self, ent: Entry) -> str:
        meta = ent.meta if isinstance(ent.meta, dict) else {}
        hover = meta.get("hover")
        if not isinstance(hover, dict):
            return ""
        txt = str(hover.get("text", "")).strip()
        return txt

    def _format_hover_tooltip_text(self, ent: Entry) -> str:
        custom = self._hover_text_for(ent)
        if custom:
            return custom

        if ent.tool in (Tool.BUTTON_STANDARD, Tool.BUTTON_PRESS, Tool.BUTTON_TOGGLE):
            return self._format_button_hover_details(ent)

        if ent.tool == Tool.TEXT_ENTRY:
            return f"Text entry: {ent.label}" if ent.label else "Text entry"
        if ent.tool == Tool.SELECT_LIST:
            return f"Select: {ent.label}" if ent.label else "Select list"
        if ent.tool == Tool.TEXT_SLOT:
            return "Text slot"
        if ent.tool == Tool.ITEM_SLOT:
            return "Item slot"

        return ent.tool.value

    def _format_button_hover_details(self, ent: Entry) -> str:
        if ent.tool == Tool.BUTTON_TOGGLE:
            return f"Toggle: {'ON' if ent.active else 'OFF'}"

        if ent.tool == Tool.BUTTON_PRESS:
            return "Press: hold while mouse down"

        if ent.tool == Tool.BUTTON_STANDARD:
            meta = ent.meta if isinstance(ent.meta, dict) else {}
            page_change = meta.get("page_change")
            if not isinstance(page_change, dict):
                return "Standard: action=none"

            mode = str(page_change.get("mode", "none"))
            modulo = bool(page_change.get("modulo", False))
            wrap_txt = "wrap" if modulo else "no-wrap"

            if mode == "goto":
                try:
                    target_id = int(page_change.get("target_page_id", 1))
                except (TypeError, ValueError):
                    target_id = 1
                return f"Standard: goto page {target_id} ({wrap_txt})"

            if mode in ("next", "prev"):
                return f"Standard: {mode} ({wrap_txt})"

            return "Standard: action=none"

        return ""

    def _update_preview_hover_tooltip(self, event: tk.Event, ent: Optional[Entry]) -> None:
        # Tooltip is preview-only and only for buttons.
        self.canvas.delete("hover_tip")

        if not self.preview_mode:
            return
        if not ent:
            return
        if not self._hover_text_enabled_for(ent):
            return

        tooltip_text = self._format_hover_tooltip_text(ent)
        if not tooltip_text:
            return

        tx = int(event.x) + 14
        ty = int(event.y) + 14

        text_id = self.canvas.create_text(
            tx,
            ty,
            text=tooltip_text,
            anchor="nw",
            fill="#ffffff",
            font=("TkDefaultFont", 9),
            tags=("hover_tip",),
        )

        bbox = self.canvas.bbox(text_id)
        if not bbox:
            return

        pad = 4
        rect_id = self.canvas.create_rectangle(
            bbox[0] - pad,
            bbox[1] - pad,
            bbox[2] + pad,
            bbox[3] + pad,
            fill="#000000",
            outline="#ffffff",
            width=1,
            tags=("hover_tip",),
        )
        self.canvas.tag_raise(text_id, rect_id)

    def _xy_to_cell(self, x: int, y: int) -> Optional[Tuple[int, int]]:
        if x < 0 or y < 0 or x >= self.canvas_px or y >= self.canvas_px:
            return None
        cx = x // self.cell_px
        cy = y // self.cell_px
        if 0 <= cx < self.grid_n and 0 <= cy < self.grid_n:
            return cx, cy
        return None

    def _clamp_cell(self, cx: int, cy: int) -> Tuple[int, int]:
        cx = max(0, min(self.grid_n - 1, cx))
        cy = max(0, min(self.grid_n - 1, cy))
        return cx, cy

    def _make_square_rect(self, start: Tuple[int, int], end: Tuple[int, int]) -> Rect:
        x0, y0 = start
        x1, y1 = end
        dx = x1 - x0
        dy = y1 - y0
        size = max(abs(dx), abs(dy))
        sx = 1 if dx >= 0 else -1
        sy = 1 if dy >= 0 else -1
        return Rect(x0, y0, x0 + sx * size, y0 + sy * size).normalized()

    def _rect_from_drag(self, start: Tuple[int, int], end: Tuple[int, int]) -> Rect:
        if self.current_tool in SQUARE_ONLY:
            return self._make_square_rect(start, end)
        return Rect(start[0], start[1], end[0], end[1]).normalized()

    def _remove_entry_id(self, entry_id: int) -> None:
        ent = self.entries.get(entry_id)
        if not ent:
            return
        for (x, y) in ent.rect.cells():
            if 0 <= x < self.grid_n and 0 <= y < self.grid_n:
                if self.cell_to_entry[y][x] == entry_id:
                    self.cell_to_entry[y][x] = None
        del self.entries[entry_id]

        if self.selected_entry_id == entry_id:
            self.selected_entry_id = None
            self._refresh_selection_ui()

    def _remove_in_rect(self, rect: Rect) -> None:
        touched = set()
        for (x, y) in rect.cells():
            if 0 <= x < self.grid_n and 0 <= y < self.grid_n:
                eid = self.cell_to_entry[y][x]
                if eid is not None:
                    touched.add(eid)
        for eid in touched:
            self._remove_entry_id(eid)

    def _place_entry(self, rect: Rect) -> None:
        self._remove_in_rect(rect)

        eid = self.next_entry_id
        self.next_entry_id += 1
        ent = Entry(entry_id=eid, tool=self.current_tool, rect=rect)
        if self.current_tool == Tool.BUTTON_STANDARD:
            # Snapshot tool metadata into the entry so changes only affect newly placed buttons.
            ent.meta = json.loads(json.dumps(self.standard_button_tool_meta))

        # Snapshot hover defaults into the entry meta for ALL placed elements (except background tool).
        if self.current_tool != Tool.BACKGROUND:
            hover_defaults = self.hover_tool_defaults.get(self.current_tool, {"enabled": False, "text": ""})
            meta = ent.meta if isinstance(ent.meta, dict) else {}
            meta["hover"] = json.loads(json.dumps(hover_defaults))
            ent.meta = meta
        self.entries[eid] = ent

        for (x, y) in rect.cells():
            if 0 <= x < self.grid_n and 0 <= y < self.grid_n:
                self.cell_to_entry[y][x] = eid

    def _entry_at_cell(self, cx: int, cy: int) -> Optional[Entry]:
        eid = self.cell_to_entry[cy][cx]
        if eid is None:
            return None
        return self.entries.get(eid)

    # ----------------------------
    # Preview interactions
    # ----------------------------

    def _preview_handle_press(self, cx: int, cy: int) -> None:
        ent = self._entry_at_cell(cx, cy)
        if not ent:
            self.set_status("Preview: empty")
            return

        if ent.tool == Tool.BUTTON_STANDARD:
            source_page_id = self.current_page_id
            ent.active = True
            self.set_status(f"Preview: standard button {ent.entry_id} clicked")
            self._preview_handle_standard_button_action(ent)
            # auto-off shortly after
            self.root.after(
                140,
                lambda pid=source_page_id, eid=ent.entry_id: self._preview_deactivate_if_exists(pid, eid),
            )

        elif ent.tool == Tool.BUTTON_PRESS:
            ent.active = True
            self._preview_pressed_entry_id = ent.entry_id
            self.set_status(f"Preview: press button {ent.entry_id} DOWN")

        elif ent.tool == Tool.BUTTON_TOGGLE:
            ent.active = not ent.active
            self.set_status(f"Preview: toggle button {ent.entry_id} => {ent.active}")

        elif ent.tool == Tool.TEXT_ENTRY:
            self._popup_text_entry(ent)

        elif ent.tool == Tool.SELECT_LIST:
            self._popup_select_list(ent)

        elif ent.tool == Tool.TEXT_SLOT:
            self.set_status(f"Preview: text slot {ent.entry_id} (no interaction)")

        elif ent.tool == Tool.ITEM_SLOT:
            self.set_status(f"Preview: item slot {ent.entry_id} (WIP)")

        self.redraw()

    def _preview_handle_standard_button_action(self, ent: Entry) -> None:
        meta = ent.meta if isinstance(ent.meta, dict) else {}
        page_change = meta.get("page_change")
        if not isinstance(page_change, dict):
            return

        mode = str(page_change.get("mode", "none"))
        modulo = bool(page_change.get("modulo", False))
        ids = self._sorted_page_ids()
        if not ids:
            return

        if mode == "none":
            return

        if mode == "goto":
            try:
                target_id = int(page_change.get("target_page_id", self.current_page_id))
            except (TypeError, ValueError):
                return
            if target_id in self.pages:
                self.goto_page(target_id)
            else:
                self.set_status(f"Preview: page {target_id} does not exist")
            return

        cur_idx = ids.index(self.current_page_id)
        if mode == "next":
            new_idx = cur_idx + 1
        elif mode == "prev":
            new_idx = cur_idx - 1
        else:
            return

        if modulo:
            new_idx %= len(ids)
        else:
            if new_idx < 0 or new_idx >= len(ids):
                return

        self.goto_page(ids[new_idx])

    def _preview_handle_release(self, cx: int, cy: int) -> None:
        # release press button if it was held
        if self._preview_pressed_entry_id is None:
            return
        ent = self.entries.get(self._preview_pressed_entry_id)
        if ent and ent.tool == Tool.BUTTON_PRESS:
            ent.active = False
            self.set_status(f"Preview: press button {ent.entry_id} UP")
        self._preview_pressed_entry_id = None
        self.redraw()

    def _preview_deactivate_if_exists(self, page_id: int, entry_id: int) -> None:
        page = self.pages.get(page_id)
        if not page:
            return
        ent = page.entries.get(entry_id)
        if ent and ent.tool == Tool.BUTTON_STANDARD:
            ent.active = False
            if self.current_page_id == page_id:
                self.redraw()

    def _popup_text_entry(self, ent: Entry) -> None:
        top = tk.Toplevel(self.root)
        top.title(f"Text Entry #{ent.entry_id}")
        top.resizable(False, False)

        tk.Label(top, text="Enter text:").pack(anchor="w", padx=10, pady=(10, 4))
        var = tk.StringVar(value=ent.label)

        entry = tk.Entry(top, textvariable=var, width=40)
        entry.pack(padx=10)
        entry.focus_set()

        def save_close() -> None:
            ent.label = var.get()
            self.set_status(f"Preview: text_entry {ent.entry_id} updated")
            top.destroy()
            self.redraw()

        btns = tk.Frame(top)
        btns.pack(fill="x", padx=10, pady=10)

        tk.Button(btns, text="OK", command=save_close).pack(side="left")
        tk.Button(btns, text="Cancel", command=top.destroy).pack(side="left", padx=(6, 0))

        top.bind("<Return>", lambda _e: save_close())
        top.bind("<Escape>", lambda _e: top.destroy())

    def _popup_select_list(self, ent: Entry) -> None:
        # Minimal demo list. Later you’ll replace this with per-entry data.
        choices = ["Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta", "Theta"]

        top = tk.Toplevel(self.root)
        top.title(f"Select List #{ent.entry_id}")
        top.geometry("260x260")

        tk.Label(top, text="Select an entry:").pack(anchor="w", padx=10, pady=(10, 4))

        frame = tk.Frame(top)
        frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        sb = tk.Scrollbar(frame)
        sb.pack(side="right", fill="y")

        lb = tk.Listbox(frame, yscrollcommand=sb.set)
        for c in choices:
            lb.insert("end", c)
        lb.pack(side="left", fill="both", expand=True)

        sb.config(command=lb.yview)

        # preselect if matches
        if ent.label in choices:
            idx = choices.index(ent.label)
            lb.selection_set(idx)
            lb.see(idx)

        def choose() -> None:
            sel = lb.curselection()
            if not sel:
                return
            ent.label = choices[sel[0]]
            self.set_status(f"Preview: select_list {ent.entry_id} => {ent.label}")
            top.destroy()
            self.redraw()

        tk.Button(top, text="Choose", command=choose).pack(padx=10, pady=(0, 10))
        lb.bind("<Double-Button-1>", lambda _e: choose())
        top.bind("<Escape>", lambda _e: top.destroy())

    # ----------------------------
    # Mouse events (router)
    # ----------------------------

    def on_left_press(self, event: tk.Event) -> None:
        cell = self._xy_to_cell(event.x, event.y)
        if cell is None:
            return
        cx, cy = cell

        if self.preview_mode:
            # Runtime interaction, no editing.
            self._preview_handle_press(cx, cy)
            return

        # --- EDIT MODE ---
        self._dragging = True
        self._drag_start = (cx, cy)
        self._drag_end = (cx, cy)

        if self.current_tool == Tool.BACKGROUND:
            self._drag_mode = "paint_on" if not self.background[cy][cx] else "paint_off"
        else:
            self._drag_mode = "erase" if self.cell_to_entry[cy][cx] is not None else "place"

        self.redraw()

    def on_right_press(self, event: tk.Event) -> None:
        # Right click selects an element in EDIT mode for per-entry meta editing.
        if self.preview_mode:
            return
        cell = self._xy_to_cell(event.x, event.y)
        if cell is None:
            self._clear_selection()
            return
        cx, cy = cell
        ent = self._entry_at_cell(cx, cy)
        if ent:
            self.selected_entry_id = ent.entry_id
        else:
            self.selected_entry_id = None
        self._refresh_selection_ui()

    def on_left_drag(self, event: tk.Event) -> None:
        if self.preview_mode:
            # No editor drag in preview
            return

        if not self._dragging or self._drag_start is None:
            return

        cell = self._xy_to_cell(event.x, event.y)
        if cell is None:
            cx = max(0, min(self.grid_n - 1, event.x // self.cell_px))
            cy = max(0, min(self.grid_n - 1, event.y // self.cell_px))
            cell = self._clamp_cell(cx, cy)

        self._drag_end = cell
        self.redraw()

    def on_left_release(self, event: tk.Event) -> None:
        cell = self._xy_to_cell(event.x, event.y)
        if cell is None:
            return
        cx, cy = cell

        if self.preview_mode:
            self._preview_handle_release(cx, cy)
            return

        if not self._dragging or self._drag_start is None or self._drag_end is None:
            return

        rect = self._rect_from_drag(self._drag_start, self._drag_end)

        if self.current_tool == Tool.BACKGROUND:
            paint_on = self._drag_mode == "paint_on"
            for (x, y) in rect.cells():
                if 0 <= x < self.grid_n and 0 <= y < self.grid_n:
                    self.background[y][x] = paint_on
            self.set_status(f"Background {'ON' if paint_on else 'OFF'}: {rect.width()}×{rect.height()}")
        else:
            if self._drag_mode == "erase":
                self._remove_in_rect(rect)
                self.set_status(f"Removed entries in: {rect.width()}×{rect.height()}")
            else:
                self._place_entry(rect)
                self.set_status(f"Placed {self.current_tool.value}: {rect.width()}×{rect.height()}")

        self._dragging = False
        self._drag_start = None
        self._drag_end = None
        self._drag_mode = None
        self.redraw()

    def on_motion(self, event: tk.Event) -> None:
        cell = self._xy_to_cell(event.x, event.y)
        if cell is None:
            # Ensure tooltip disappears when leaving canvas
            self.canvas.delete("hover_tip")
            if self._preview_hover_entry_id is not None:
                self._preview_hover_entry_id = None
                self.redraw()
            return
        cx, cy = cell
        ent = self._entry_at_cell(cx, cy)
        bg = self.background[cy][cx]

        # Preview hover highlight (color effect) without forcing redraw every pixel.
        new_hover_id = ent.entry_id if (self.preview_mode and ent is not None) else None
        if new_hover_id != self._preview_hover_entry_id:
            self._preview_hover_entry_id = new_hover_id
            self.redraw()

        self._update_preview_hover_tooltip(event, ent)

        mode = "PREVIEW" if self.preview_mode else "EDIT"
        if ent:
            extra = ""
            if self.preview_mode and self._hover_text_enabled_for(ent):
                htxt = self._format_hover_tooltip_text(ent)
                if htxt:
                    extra = f" | Hover={htxt}"
            self.status_var.set(
                f"{mode} | Page={self.current_page_id} | Cell=({cx},{cy}) | BG={int(bg)} | Entry={ent.entry_id} {ent.tool.value} | Active={int(ent.active)}{extra}"
            )
        else:
            self.status_var.set(f"{mode} | Page={self.current_page_id} | Cell=({cx},{cy}) | BG={int(bg)} | Entry=-")

    # ----------------------------
    # Toggles
    # ----------------------------

    def toggle_preview(self) -> None:
        self.preview_mode = not self.preview_mode
        self.preview_btn.configure(text=f"Preview: {'ON' if self.preview_mode else 'OFF'}")

        # If the user just dropped GUI_CTM.png next to the script, pick it up.
        if self.preview_mode and self._texture_sheet is None:
            self._load_texture_sheet()

        # Clear any held press interaction when switching modes
        self._preview_pressed_entry_id = None

        # Clear hover state and tooltip when switching modes
        self._preview_hover_entry_id = None
        self.canvas.delete("hover_tip")

        if self.preview_mode and self._texture_sheet is None:
            self.set_status(f"Mode: PREVIEW (interactive) | Texture missing: {TEXTURE_SHEET_FILENAME} (using colors)")
        else:
            self.set_status(f"Mode: {'PREVIEW (interactive)' if self.preview_mode else 'EDIT'}")
        self.redraw()

    def toggle_grid(self) -> None:
        new_n = 32 if self.grid_n == 16 else 16

        # MVP: clear on toggle
        self.grid_n = new_n
        self.cell_px = self.canvas_px // self.grid_n

        self.pages.clear()
        self.current_page_id = 1
        self.start_page_id = 1
        self.pages[self.current_page_id] = self._new_page_state(self.current_page_id)
        self._set_current_page(self.current_page_id)

        self.selected_entry_id = None
        self._refresh_selection_ui()

        self.set_status(f"Grid changed to {self.grid_n}×{self.grid_n} (cleared)")
        self.redraw()

    # ----------------------------
    # Texture sheet (preview)
    # ----------------------------

    def _texture_sheet_path(self) -> str:
        # Prefer "next to the entrypoint" (gui_builder.py) since that's where users will drop assets.
        base_dir = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()
        return os.path.join(base_dir, TEXTURE_SHEET_FILENAME)

    def _background_textures_dir(self) -> str:
        return os.path.join(os.path.dirname(self._texture_sheet_path()), BACKGROUND_TEXTURES_DIRNAME)

    def _scan_background_textures(self) -> None:
        """Detect background textures from the backgrounds folder next to GUI_CTM.png."""
        has_ui = hasattr(self, "bg_texture_var") and hasattr(self, "bg_texture_menu")

        textures_dir = self._background_textures_dir()
        paths: Dict[str, str] = {}
        if os.path.isdir(textures_dir):
            for name in sorted(os.listdir(textures_dir)):
                if not name.lower().endswith(".png"):
                    continue
                paths[name] = os.path.join(textures_dir, name)

        self._background_texture_paths = paths

        # Preserve selection if still available.
        if self._background_texture_name != "(none)" and self._background_texture_name not in paths:
            self._background_texture_name = "(none)"
            self._background_texture_src = None
            self._background_texture_scaled.clear()
            self._preview_background_cache_key = None
            self._preview_background_image = None

        if has_ui:
            options = ["(none)"] + list(paths.keys())
            menu = self.bg_texture_menu["menu"]
            menu.delete(0, "end")
            for opt in options:
                menu.add_command(
                    label=opt,
                    command=lambda v=opt: self.bg_texture_var.set(v) or self._on_background_texture_changed(v),
                )
            self.bg_texture_var.set(self._background_texture_name)

        # If there's exactly one background and nothing selected, auto-select it.
        if self._background_texture_name == "(none)" and len(paths) == 1:
            only = next(iter(paths.keys()))
            if has_ui:
                self.bg_texture_var.set(only)
            self._on_background_texture_changed(only)

    def _on_background_texture_changed(self, selection: str) -> None:
        name = str(selection)
        if name == self._background_texture_name:
            return

        self._background_texture_name = name
        self._background_texture_src = None
        self._background_texture_scaled.clear()
        self._preview_background_cache_key = None
        self._preview_background_image = None

        if name == "(none)":
            self.set_status("Preview background: none")
            self.redraw()
            return

        path = self._background_texture_paths.get(name)
        if not path or not os.path.isfile(path):
            self._background_texture_name = "(none)"
            self.set_status("Preview background: missing file")
            self.redraw()
            return

        try:
            self._background_texture_src = tk.PhotoImage(file=path)
            self.set_status(f"Preview background: {name}")
        except Exception:
            self._background_texture_name = "(none)"
            self._background_texture_src = None
            self.set_status("Preview background: failed to load")

        self.redraw()

    def _scale_factors(self) -> Tuple[int, int]:
        """Return (zoom, subsample) factors so: TILE_PX * zoom / subsample == cell_px."""
        if self.cell_px == 40:
            return 5, 2  # 16*5/2=40
        if self.cell_px == 20:
            return 5, 4  # 16*5/4=20
        return 1, 1

    def _get_scaled_background_tile(self) -> Optional[tk.PhotoImage]:
        src = self._background_texture_src
        if src is None:
            return None

        cached = self._background_texture_scaled.get(self.cell_px)
        if cached is not None:
            return cached

        zoom, subsample = self._scale_factors()
        tile = src
        if zoom != 1 or subsample != 1:
            tile = src.zoom(zoom, zoom).subsample(subsample, subsample)
        self._background_texture_scaled[self.cell_px] = tile
        return tile

    def _background_signature(self) -> str:
        return "".join("1" if v else "0" for row in self.background for v in row)

    def _copy_wrapped(self, dest: tk.PhotoImage, src: tk.PhotoImage, sx: int, sy: int, w: int, h: int, dx: int, dy: int) -> None:
        """Copy a w×h region from src starting at (sx,sy), wrapping around src edges."""
        src_w = src.width()
        src_h = src.height()

        sx %= src_w
        sy %= src_h

        x_parts = [(sx, min(src_w, sx + w))]
        if sx + w > src_w:
            x_parts.append((0, (sx + w) - src_w))

        y_parts = [(sy, min(src_h, sy + h))]
        if sy + h > src_h:
            y_parts.append((0, (sy + h) - src_h))

        to_y = dy
        for (y0, y1) in y_parts:
            to_x = dx
            for (x0, x1) in x_parts:
                cw = x1 - x0
                ch = y1 - y0
                dest.tk.call(
                    dest,
                    "copy",
                    src,
                    "-from",
                    x0,
                    y0,
                    x1,
                    y1,
                    "-to",
                    to_x,
                    to_y,
                )
                to_x += cw
            to_y += ch

    def _build_preview_background_image(self) -> Optional[tk.PhotoImage]:
        tile = self._get_scaled_background_tile()
        if tile is None:
            return None

        key = (self.current_page_id, self.grid_n, self.cell_px, self._background_texture_name, self._background_signature())
        if self._preview_background_cache_key == key and self._preview_background_image is not None:
            return self._preview_background_image

        img = tk.PhotoImage(width=self.canvas_px, height=self.canvas_px)
        src_w = tile.width()
        src_h = tile.height()

        for y in range(self.grid_n):
            for x in range(self.grid_n):
                if not self.background[y][x]:
                    continue
                x0 = x * self.cell_px
                y0 = y * self.cell_px
                sx = x0 % src_w
                sy = y0 % src_h
                self._copy_wrapped(img, tile, sx, sy, self.cell_px, self.cell_px, x0, y0)

        self._preview_background_cache_key = key
        self._preview_background_image = img
        return img

    def _load_texture_sheet(self) -> None:
        """Load GUI_CTM.png if present; otherwise keep color-based preview."""
        path = self._texture_sheet_path()
        if not os.path.isfile(path):
            self._texture_sheet = None
            return

        try:
            self._texture_sheet = TextureSheet(self.root, path, tile_px=TILE_PX)
        except Exception:
            # Keep app usable even if the PNG cannot be loaded.
            self._texture_sheet = None

    def _ctm_mask(self, cell_set: "set[tuple[int, int]]", x: int, y: int) -> int:
        mask = 0
        for dx, dy, bit in CTM_DIRS:
            if (x + dx, y + dy) in cell_set:
                mask |= bit
        return mask

    def _entry_visual_state(self, ent: Entry) -> str:
        """Return a small state label for picking tiles."""
        hovered = self.preview_mode and (self._preview_hover_entry_id == ent.entry_id)

        if ent.tool in (Tool.BUTTON_STANDARD, Tool.BUTTON_TOGGLE, Tool.BUTTON_PRESS):
            pressed = False
            if ent.tool == Tool.BUTTON_PRESS:
                pressed = (self._preview_pressed_entry_id == ent.entry_id) or bool(ent.active)
            elif ent.tool == Tool.BUTTON_TOGGLE:
                pressed = bool(ent.active)
            elif ent.tool == Tool.BUTTON_STANDARD:
                pressed = bool(ent.active)

            if pressed and hovered:
                return "button_pressed_hover"
            if pressed:
                return "button_pressed"
            if hovered:
                return "button_hover"
            return "button_unpressed"

        if ent.tool == Tool.TEXT_SLOT:
            return "text_hover" if hovered else "text_unpressed"

        if ent.tool in (Tool.TEXT_ENTRY, Tool.SELECT_LIST):
            return "input_border_hover" if hovered else "input_border"

        if ent.tool == Tool.ITEM_SLOT:
            return "item_slot_hover" if hovered else "item_slot"

        # Everything else falls back to color rendering for now.
        return ""

    def _draw_entry_textured(self, ent: Entry) -> bool:
        """Draw entry with the texture sheet; returns True if drawn."""
        sheet = self._texture_sheet
        if sheet is None:
            return False

        state_key = self._entry_visual_state(ent)
        if not state_key:
            return False

        origin = CTM_ORIGINS.get(state_key)
        if not origin:
            return False

        ox, oy = origin
        r = ent.rect.normalized()
        cells = r.cells()
        cell_set = set(cells)

        for (cx, cy) in cells:
            mask = self._ctm_mask(cell_set, cx, cy)
            dx, dy = ctm_tile_offset(mask)
            tile = sheet.get_tile(ox + dx, oy + dy, self.cell_px)
            if tile is None:
                return False
            x0 = cx * self.cell_px
            y0 = cy * self.cell_px
            self.canvas.create_image(x0, y0, anchor="nw", image=tile)

        return True

    def _draw_cellset_textured(self, cell_set: "set[tuple[int,int]]", state_key: str) -> bool:
        """Draw a connected-texture block for an arbitrary cell set."""
        sheet = self._texture_sheet
        if sheet is None:
            return False

        origin = CTM_ORIGINS.get(state_key)
        if not origin:
            return False

        ox, oy = origin
        for (cx, cy) in cell_set:
            mask = self._ctm_mask(cell_set, cx, cy)
            dx, dy = ctm_tile_offset(mask)
            tile = sheet.get_tile(ox + dx, oy + dy, self.cell_px)
            if tile is None:
                return False
            x0 = cx * self.cell_px
            y0 = cy * self.cell_px
            self.canvas.create_image(x0, y0, anchor="nw", image=tile)

        return True

    # ----------------------------
    # JSON save/load
    # ----------------------------

    def to_json_dict(self) -> dict:
        pages_payload = []
        for pid in self._sorted_page_ids():
            p = self.pages[pid]
            background_rects = self._background_to_rects(p.background)
            pages_payload.append(
                {
                    "page_id": pid,
                    "background_rects": [
                        {"x0": rr.x0, "y0": rr.y0, "x1": rr.x1, "y1": rr.y1} for rr in background_rects
                    ],
                    "entries": [
                        {
                            "id": e.entry_id,
                            "tool": e.tool.value,
                            "rect": {"x0": e.rect.x0, "y0": e.rect.y0, "x1": e.rect.x1, "y1": e.rect.y1},
                            "active": e.active,
                            "label": e.label,
                            "meta": e.meta,
                        }
                        for e in sorted(p.entries.values(), key=lambda x: x.entry_id)
                    ],
                }
            )

        return {
            "version": self.JSON_VERSION,
            "grid_n": self.grid_n,
            "start_page_id": self.start_page_id,
            "pages": pages_payload,
        }

    def load_from_json_dict(self, data: dict) -> None:
        if not isinstance(data, dict):
            raise ValueError("Invalid JSON root (expected object).")

        version = data.get("version")
        if version not in (1, 2, 3):
            raise ValueError(f"Unsupported JSON version: {version} (expected 1, 2, or {self.JSON_VERSION}).")

        grid_n = data.get("grid_n")
        if grid_n not in (16, 32):
            raise ValueError(f"grid_n must be 16 or 32, got {grid_n}.")

        pages = data.get("pages")
        legacy_background = data.get("background")
        legacy_entries = data.get("entries")

        if pages is None:
            # Backward compatibility: old format = single page
            pages = [
                {
                    "page_id": 1,
                    "background": legacy_background,
                    "entries": legacy_entries if legacy_entries is not None else [],
                }
            ]

        if not isinstance(pages, list) or len(pages) == 0:
            raise ValueError("pages must be a non-empty list.")

        self.grid_n = grid_n
        self.cell_px = self.canvas_px // self.grid_n

        self.pages.clear()

        for pobj in pages:
            if not isinstance(pobj, dict):
                raise ValueError("pages contains an invalid page object.")

            page_id = int(pobj.get("page_id", 1))

            # Background can be stored either as legacy boolean grid (v1/v2) or as rectangles (v3)
            background: Optional[List[List[bool]]] = None
            rect_payload = pobj.get("background_rects")
            if isinstance(rect_payload, list):
                rects: List[Rect] = []
                for robj in rect_payload:
                    if not isinstance(robj, dict):
                        continue
                    try:
                        rr = Rect(int(robj["x0"]), int(robj["y0"]), int(robj["x1"]), int(robj["y1"]))
                    except Exception:
                        continue
                    rects.append(rr)
                background = self._background_from_rects(rects)
            else:
                legacy_bg = pobj.get("background")
                if not (isinstance(legacy_bg, list) and len(legacy_bg) == grid_n):
                    raise ValueError(f"background has invalid dimensions for page {page_id}.")
                for row in legacy_bg:
                    if not (isinstance(row, list) and len(row) == grid_n):
                        raise ValueError(f"background has invalid dimensions for page {page_id}.")
                background = [[bool(x) for x in row] for row in legacy_bg]

            entries = pobj.get("entries", [])
            if not isinstance(entries, list):
                raise ValueError(f"entries must be a list for page {page_id}.")

            st = PageState(
                page_id=page_id,
                background=background,
                entries={},
                cell_to_entry=[[None for _ in range(self.grid_n)] for _ in range(self.grid_n)],
                next_entry_id=1,
            )

            max_id = 0
            for obj in entries:
                tool = Tool(obj["tool"])
                r = obj["rect"]
                rect = Rect(int(r["x0"]), int(r["y0"]), int(r["x1"]), int(r["y1"]))
                rect = rect.normalized()
                eid = int(obj["id"])
                max_id = max(max_id, eid)

                ent = Entry(
                    entry_id=eid,
                    tool=tool,
                    rect=rect,
                    active=bool(obj.get("active", False)),
                    label=str(obj.get("label", "")),
                    meta=dict(obj.get("meta") or {}),
                )

                st.entries[eid] = ent
                for (x, y) in ent.rect.cells():
                    if 0 <= x < self.grid_n and 0 <= y < self.grid_n:
                        st.cell_to_entry[y][x] = eid

            st.next_entry_id = max_id + 1
            self.pages[page_id] = st

        start_pid = data.get("start_page_id", 1)
        try:
            self.start_page_id = int(start_pid)
        except (TypeError, ValueError):
            self.start_page_id = 1

        if self.start_page_id not in self.pages:
            self.start_page_id = self._sorted_page_ids()[0]

        # Keep editor on start page after load
        self._set_current_page(self.start_page_id)

    def save_json(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
            title="Save GUI JSON",
        )
        if not path:
            return

        try:
            self._sync_current_page_back()
            payload = self.to_json_dict()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            self.set_status(f"Saved JSON: {path}")
        except Exception as e:
            messagebox.showerror("Save failed", str(e))

    def load_json(self) -> None:
        path = filedialog.askopenfilename(
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
            title="Load GUI JSON",
        )
        if not path:
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.load_from_json_dict(data)
            self.set_status(f"Loaded JSON: {path}")
            self.redraw()
        except Exception as e:
            messagebox.showerror("Load failed", str(e))

    # ----------------------------
    # Rendering
    # ----------------------------

    def redraw(self) -> None:
        self.canvas.delete("all")

        bg_cells = {(x, y) for y in range(self.grid_n) for x in range(self.grid_n) if self.background[y][x]}

        # Background layer
        if self.preview_mode:
            bg_img = self._build_preview_background_image()
            if bg_img is not None:
                # Keep reference via self._preview_background_image (set by builder)
                self.canvas.create_image(0, 0, anchor="nw", image=bg_img)
            else:
                for (x, y) in bg_cells:
                    self._draw_cell_fill(x, y, "#2b2b2b")

            # Background border module (transparent overlay)
            if bg_cells:
                self._draw_cellset_textured(bg_cells, "background_border")
        else:
            for (x, y) in bg_cells:
                self._draw_cell_fill(x, y, "#2b2b2b")

        # Entries layer
        for ent in self.entries.values():
            self._draw_entry(ent)

        # Grid overlay (editor only). Preview should show only the textured tiles.
        if not self.preview_mode:
            self._draw_grid_lines()

        # Editor drag preview
        if (not self.preview_mode) and self._dragging and self._drag_start and self._drag_end:
            r = self._rect_from_drag(self._drag_start, self._drag_end)
            self._draw_rect_outline(r, "#ffffff")

        self._draw_legend()

    def _draw_hover_outline(self, ent: Entry) -> None:
        r = ent.rect.normalized()
        x0 = r.x0 * self.cell_px
        y0 = r.y0 * self.cell_px
        x1 = (r.x1 + 1) * self.cell_px
        y1 = (r.y1 + 1) * self.cell_px
        self.canvas.create_rectangle(
            x0,
            y0,
            x1,
            y1,
            fill="",
            outline="#ffd84d",
            width=3,
        )

    def _draw_cell_fill(self, cx: int, cy: int, color: str) -> None:
        x0 = cx * self.cell_px
        y0 = cy * self.cell_px
        x1 = x0 + self.cell_px
        y1 = y0 + self.cell_px
        self.canvas.create_rectangle(x0, y0, x1, y1, fill=color, outline="")

    def _draw_rect_outline(self, rect: Rect, color: str) -> None:
        r = rect.normalized()
        x0 = r.x0 * self.cell_px
        y0 = r.y0 * self.cell_px
        x1 = (r.x1 + 1) * self.cell_px
        y1 = (r.y1 + 1) * self.cell_px
        self.canvas.create_rectangle(x0, y0, x1, y1, outline=color, width=2)

    def _draw_entry(self, ent: Entry) -> None:
        # Preview: textured rendering using GUI_CTM.png (if available)
        if self.preview_mode and self._texture_sheet is not None:
            if self._draw_entry_textured(ent):
                # Keep debug labels in preview (can be removed later if you want a clean look)
                r = ent.rect.normalized()
                x0 = r.x0 * self.cell_px
                y0 = r.y0 * self.cell_px
                x1 = (r.x1 + 1) * self.cell_px
                y1 = (r.y1 + 1) * self.cell_px

                label_lines: List[str] = []
                if ent.tool in (Tool.TEXT_ENTRY, Tool.SELECT_LIST) and ent.label:
                    label_lines.append(ent.label[:24])

                if ent.tool == Tool.BUTTON_TOGGLE:
                    label_lines.append("ON" if ent.active else "OFF")

                if label_lines:
                    self.canvas.create_text(
                        (x0 + x1) / 2,
                        (y0 + y1) / 2,
                        text="\n".join(label_lines),
                        fill="#ffffff",
                        font=("TkDefaultFont", max(6, self.cell_px // 4)),
                        justify="center",
                    )
                return

        colors = {
            Tool.BUTTON_STANDARD: "#3a7bd5",
            Tool.BUTTON_PRESS: "#2bb673",
            Tool.BUTTON_TOGGLE: "#b05cff",
            Tool.TEXT_ENTRY: "#d57b3a",
            Tool.SELECT_LIST: "#d5c63a",
            Tool.TEXT_SLOT: "#aaaaaa",
            Tool.ITEM_SLOT: "#d53a3a",
        }
        fill = colors.get(ent.tool, "#666666")

        r = ent.rect.normalized()
        x0 = r.x0 * self.cell_px
        y0 = r.y0 * self.cell_px
        x1 = (r.x1 + 1) * self.cell_px
        y1 = (r.y1 + 1) * self.cell_px

        outline = "#111111"
        width = 1
        if ent.active:
            outline = "#ffffff"
            width = 3

        self.canvas.create_rectangle(x0, y0, x1, y1, fill=fill, outline=outline, width=width)

        # Show tool name; also show label for text/select if any
        label_lines = [ent.tool.value.replace("_", "\n")]
        if ent.tool in (Tool.TEXT_ENTRY, Tool.SELECT_LIST) and ent.label:
            label_lines.append("---")
            label_lines.append(ent.label[:24])

        if ent.active:
            label_lines.append("ACTIVE")

        text = "\n".join(label_lines)
        self.canvas.create_text(
            (x0 + x1) / 2,
            (y0 + y1) / 2,
            text=text,
            fill="#000000",
            font=("TkDefaultFont", max(6, self.cell_px // 4)),
            justify="center",
        )

    def _draw_grid_lines(self) -> None:
        for i in range(self.grid_n + 1):
            p = i * self.cell_px
            self.canvas.create_line(p, 0, p, self.canvas_px, fill="#2a2a2a")
            self.canvas.create_line(0, p, self.canvas_px, p, fill="#2a2a2a")

    def _draw_legend(self) -> None:
        mode = "PREVIEW" if self.preview_mode else "EDIT"
        txt = f"{mode} | Page {self.current_page_id} | {self.grid_n}×{self.grid_n} | Tool: {self.current_tool.value}"
        self.canvas.create_rectangle(6, 6, 6 + 520, 6 + 24, fill="#000000", outline="")
        self.canvas.create_text(12, 18, text=txt, fill="#ffffff", anchor="w", font=("TkDefaultFont", 10))

    # ----------------------------
    # Run
    # ----------------------------

    def run(self) -> None:
        self.root.mainloop()
