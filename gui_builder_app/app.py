from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import tkinter as tk
import zipfile
from tkinter import filedialog, messagebox
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    # Package import (normal): run via gui_builder.py
    from .models import Entry, PageState, Rect, SQUARE_ONLY, Tool
    from .texture import TextureSheet
    from .texture_mapping import (
        CTM_DIRS,
        CTM_ORIGINS,
        ENTRY_TOOL_MODULES,
        BACKGROUND_FILENAME,
        MODULES_FILENAME,
        SKIN_PACKS_DIRNAME,
        TILE_PX,
        ctm_tile_offset,
    )
except ImportError:
    # Allow running this file directly for quick debugging:
    #   python gui_builder_app/app.py
    from gui_builder_app.models import Entry, PageState, Rect, SQUARE_ONLY, Tool
    from gui_builder_app.texture import TextureSheet
    from gui_builder_app.texture_mapping import (
        CTM_DIRS,
        CTM_ORIGINS,
        ENTRY_TOOL_MODULES,
        BACKGROUND_FILENAME,
        MODULES_FILENAME,
        SKIN_PACKS_DIRNAME,
        TILE_PX,
        ctm_tile_offset,
    )


class GuiBuilderApp:
    JSON_VERSION = 3

    # Exported texture sheet size (pixels). Used to pack assembled button textures.
    EXPORT_SHEET_PX = 256

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("CustomNPCs GUI Builder (MVP)")

        # Name used as the top-level export folder (exports/<gui_name>/...).
        self.gui_name_var = tk.StringVar(value="unnamed_gui")

        # Persistent user settings.
        self._settings: Dict[str, Any] = {}
        self._load_settings()

        # Export / injection settings (persisted).
        self.export_base_dir_var = tk.StringVar(value=str(self._settings.get("export_base_dir") or ""))
        self.manifest_output_dir_var = tk.StringVar(value=str(self._settings.get("manifest_output_dir") or ""))
        self.inject_pack_kind_var = tk.StringVar(value=str(self._settings.get("inject_pack_kind") or "folder"))
        self.inject_pack_path_var = tk.StringVar(value=str(self._settings.get("inject_pack_path") or ""))
        self.group_buttons_by_size_var = tk.BooleanVar(value=bool(self._settings.get("group_buttons_by_size", True)))

        if not isinstance(self._settings.get("extra_skin_packs"), list):
            self._settings["extra_skin_packs"] = []

        # UI handles for the extra skin packs list (created in _build_ui).
        self._extra_skin_pack_vars: Dict[str, tk.BooleanVar] = {}
        self._extra_skin_packs_frame: Optional[tk.Frame] = None

        def _persist(_: Any = None, __: Any = None, ___: Any = None) -> None:
            self._persist_export_inject_settings()

        self.export_base_dir_var.trace_add("write", _persist)
        self.manifest_output_dir_var.trace_add("write", _persist)
        self.inject_pack_kind_var.trace_add("write", _persist)
        self.inject_pack_path_var.trace_add("write", _persist)
        self.group_buttons_by_size_var.trace_add("write", _persist)

        # Optional texture sheet for preview rendering.
        self._texture_sheet: Optional[TextureSheet] = None

        self.grid_n = 16
        self.canvas_px = 640
        self.cell_px = self.canvas_px // self.grid_n

        # Canvas sizing/centering state (grid is centered inside the canvas widget).
        self._canvas_widget_w = self.canvas_px
        self._canvas_widget_h = self.canvas_px
        self._canvas_offset_x = 0
        self._canvas_offset_y = 0

        # Multi-page data model
        self.pages: Dict[int, PageState] = {}
        self.current_page_id: int = 1
        self.start_page_id: int = 1

        # Global unique ID for entries across all pages (stable for exports).
        self.next_uid: int = 1

        # These are aliases to the currently selected page state.
        self.background: List[List[bool]]
        self.entries: Dict[int, Entry]
        self.cell_to_entry: List[List[Optional[int]]]
        self.next_entry_id: int

        self.pages[self.current_page_id] = self._new_page_state(self.current_page_id)
        self._set_current_page(self.current_page_id)

        self.current_tool: Tool = Tool.BACKGROUND
        self.preview_mode = False
        # Used by the menubar Preview checkbutton (and kept in sync with preview_mode).
        self.preview_mode_var = tk.BooleanVar(value=self.preview_mode)
        self._syncing_preview_mode = False

        # Editor selection state (right-click)
        self.selected_entry_id: Optional[int] = None

        # Preview hover state
        self._preview_hover_entry_id: Optional[int] = None

        # Skin pack selection (Modules.png + Background.png)
        self._skin_pack_paths: Dict[str, Dict[str, str]] = {}
        self._skin_pack_name: str = "(none)"
        self._skin_background_src: Optional[tk.PhotoImage] = None
        self._skin_background_scaled: Dict[int, tk.PhotoImage] = {}
        self._preview_background_image: Optional[tk.PhotoImage] = None
        self._preview_background_cache_key: Optional[Tuple[int, int, int, str, str]] = None

        # Tool-level metadata (applies to newly placed entries while tool is selected)
        self.standard_button_tool_meta: Dict[str, Any] = {
            "page_change": {
                "mode": "none",  # none|goto|next|prev|close
                "target_page_id": 1,
                "modulo": True,
            }
        }

        # Editor drag state
        self._dragging = False
        self._drag_start: Optional[Tuple[int, int]] = None
        self._drag_end: Optional[Tuple[int, int]] = None
        self._drag_mode: Optional[str] = None  # editor: "place"/"erase"/"paint_on"/"paint_off"

        self._build_menu()
        self._build_ui()
        self._bind_events()

        # Load skin packs after Tk is initialized.
        self._scan_skin_packs()
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

        # Clear hover state when switching pages.
        self._preview_hover_entry_id = None

        self.set_status(f"Switched to page {self.current_page_id}")
        self.redraw()

    def _deactivate_non_toggle_buttons(self, page_id: int) -> None:
        page = self.pages.get(page_id)
        if not page:
            return
        for ent in page.entries.values():
            if ent.tool == Tool.BUTTON_STANDARD:
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
        filemenu.add_command(label="Export Textures...", command=self.export_textures)
        filemenu.add_command(label="Export All Skin Packs...", command=self.export_all_skin_packs)
        filemenu.add_command(label="Inject into Texture Pack...", command=self.inject_into_texture_pack)
        filemenu.add_separator()
        filemenu.add_command(label="Quit", command=self.root.quit)
        menubar.add_cascade(label="File", menu=filemenu)

        previewmenu = tk.Menu(menubar, tearoff=0)

        def _on_preview_menu_toggle() -> None:
            self._set_preview_mode(bool(self.preview_mode_var.get()))

        previewmenu.add_checkbutton(
            label="Preview Mode",
            variable=self.preview_mode_var,
            command=_on_preview_menu_toggle,
        )
        previewmenu.add_separator()
        previewmenu.add_command(label="Rescan Skin Packs", command=self._scan_skin_packs)
        menubar.add_cascade(label="Preview", menu=previewmenu)

        self.root.config(menu=menubar)

    def _set_preview_mode(self, enabled: bool) -> None:
        if enabled == self.preview_mode:
            return

        self.preview_mode = bool(enabled)

        if hasattr(self, "preview_btn"):
            self.preview_btn.configure(text=f"Preview: {'ON' if self.preview_mode else 'OFF'}")

        # Clear any held press interaction when switching modes
        self._preview_pressed_entry_id = None

        # Clear hover state and tooltip when switching modes
        self._preview_hover_entry_id = None
        if hasattr(self, "canvas"):
            self.canvas.delete("hover_tip")

        if self.preview_mode and self._texture_sheet is None:
            self.set_status("Mode: PREVIEW (interactive) | No skin pack selected (using colors)")
        else:
            self.set_status(f"Mode: {'PREVIEW (interactive)' if self.preview_mode else 'EDIT'}")

        # Keep the menu checkbutton state in sync.
        try:
            if not self._syncing_preview_mode:
                self._syncing_preview_mode = True
                if bool(self.preview_mode_var.get()) != bool(self.preview_mode):
                    self.preview_mode_var.set(bool(self.preview_mode))
        finally:
            self._syncing_preview_mode = False

        self.redraw()

    def _settings_path(self) -> str:
        return os.path.join(self._assets_base_dir(), ".gui_builder_settings.json")

    def _load_settings(self) -> None:
        try:
            path = self._settings_path()
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._settings = data
        except Exception:
            self._settings = {}

    def _save_settings(self) -> None:
        try:
            path = self._settings_path()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._settings, f, indent=2)
        except Exception:
            # Non-fatal.
            pass

    def _persist_export_inject_settings(self) -> None:
        self._settings["export_base_dir"] = str(self.export_base_dir_var.get() or "")
        self._settings["manifest_output_dir"] = str(self.manifest_output_dir_var.get() or "")
        kind = str(self.inject_pack_kind_var.get() or "folder").strip().lower()
        self._settings["inject_pack_kind"] = "zip" if kind == "zip" else "folder"
        self._settings["inject_pack_path"] = str(self.inject_pack_path_var.get() or "")
        self._settings["group_buttons_by_size"] = bool(self.group_buttons_by_size_var.get())
        if not isinstance(self._settings.get("extra_skin_packs"), list):
            self._settings["extra_skin_packs"] = []
        self._save_settings()

    def _selected_extra_skin_packs(self) -> List[str]:
        raw = self._settings.get("extra_skin_packs")
        if not isinstance(raw, list):
            return []
        out: List[str] = []
        for x in raw:
            try:
                s = str(x)
            except Exception:
                continue
            if s:
                out.append(s)
        return out

    def _set_selected_extra_skin_packs(self, packs: List[str]) -> None:
        self._settings["extra_skin_packs"] = list(packs)
        self._save_settings()

    def _resolved_extra_skin_packs(self) -> List[str]:
        """Return selected extra packs as real detected pack names.

        Accepts either stored raw names (e.g. "TanWood") or folder names (e.g. "tanwood")
        in .gui_builder_settings.json.
        """

        selected = set(self._selected_extra_skin_packs())
        default_pack = str(self._skin_pack_name or "")
        resolved: List[str] = []
        for name in sorted(self._skin_pack_paths.keys()):
            if name == default_pack:
                continue
            folder = self._skin_pack_folder_name(name)
            if name in selected or folder in selected:
                resolved.append(name)
        return resolved

    def _rebuild_extra_skin_packs_ui(self) -> None:
        if self._extra_skin_packs_frame is None:
            return

        for child in list(self._extra_skin_packs_frame.winfo_children()):
            child.destroy()

        self._extra_skin_pack_vars.clear()

        default_pack = str(self._skin_pack_name or "")
        selected = set(self._selected_extra_skin_packs())
        packs = sorted(self._skin_pack_paths.keys())
        choices = [p for p in packs if p != default_pack]

        if not choices:
            tk.Label(self._extra_skin_packs_frame, text="(no additional skin packs detected)", anchor="w").pack(fill="x")
            # Keep settings clean.
            self._set_selected_extra_skin_packs([])
            return

        info = tk.Label(
            self._extra_skin_packs_frame,
            text=f"Default included: {default_pack}",
            anchor="w",
            justify="left",
        )
        info.pack(fill="x", pady=(0, 4))

        for name in choices:
            folder = self._skin_pack_folder_name(name)
            v = tk.BooleanVar(value=(name in selected) or (folder in selected))

            def _on_toggle(_a: Any = None, _b: Any = None, _c: Any = None) -> None:
                picked = [n for n, vv in self._extra_skin_pack_vars.items() if bool(vv.get())]
                self._set_selected_extra_skin_packs(picked)

            v.trace_add("write", _on_toggle)
            self._extra_skin_pack_vars[name] = v
            tk.Checkbutton(self._extra_skin_packs_frame, text=name, variable=v, anchor="w").pack(fill="x", anchor="w")

        # Drop selections that no longer exist.
        valid = [n for n in self._selected_extra_skin_packs() if (n in choices) or (n in [self._skin_pack_folder_name(c) for c in choices])]
        if set(valid) != set(self._selected_extra_skin_packs()):
            # Normalize to raw names only.
            self._set_selected_extra_skin_packs([n for n in self._resolved_extra_skin_packs()])

    def _browse_export_base_dir(self) -> None:
        initial = str(self.export_base_dir_var.get() or "")
        initial = initial if os.path.isdir(initial) else ""
        path = filedialog.askdirectory(title="Select export base folder", initialdir=initial)
        if path:
            self.export_base_dir_var.set(path)

    def _browse_manifest_output_dir(self) -> None:
        initial = str(self.manifest_output_dir_var.get() or "")
        initial = initial if os.path.isdir(initial) else ""
        path = filedialog.askdirectory(title="Select folder to save gui_manifest.json", initialdir=initial)
        if path:
            self.manifest_output_dir_var.set(path)

    def _browse_inject_pack_path(self) -> None:
        kind = str(self.inject_pack_kind_var.get() or "folder").strip().lower()
        last = str(self._settings.get("last_resource_pack_path") or "")
        initial_dir = last if os.path.isdir(last) else (os.path.dirname(last) if last else "")

        path = ""
        if kind == "zip":
            path = filedialog.askopenfilename(
                title="Select resource pack zip",
                filetypes=[("Zip", "*.zip"), ("All files", "*.*")],
                initialdir=initial_dir,
            )
        else:
            path = filedialog.askdirectory(title="Select resource pack folder", initialdir=initial_dir)

        if path:
            self.inject_pack_path_var.set(path)
            self._settings["last_resource_pack_path"] = path
            self._save_settings()

    def _safe_gui_folder_for_pack(self) -> str:
        """Folder name for injection into a resource pack.

        Requirement: lowercase, spaces -> underscore, and safe for Windows paths.
        """

        raw = str(self.gui_name_var.get() or "")
        raw = raw.strip().lower().replace(" ", "_")
        return self._safe_dir_name(raw)

    def _skin_pack_folder_name(self, pack_name: str) -> str:
        """Normalize a skin pack name to the folder name used for outputs.

        Requirement: lowercase, no spaces (spaces -> underscore), and safe for Windows paths.
        """

        raw = str(pack_name or "skin").strip().lower().replace(" ", "_")
        return self._safe_dir_name(raw)

    def _zip_remove_prefix(self, zip_path: str, prefix: str) -> bool:
        """Remove all entries whose name starts with prefix from a zip.

        Zipfiles can't delete in-place; we rewrite the archive.
        """

        try:
            prefix = prefix.replace("\\", "/")
            if prefix and not prefix.endswith("/"):
                prefix += "/"

            tmp_path = f"{zip_path}.tmp"
            with zipfile.ZipFile(zip_path, "r") as zin, zipfile.ZipFile(
                tmp_path, "w", compression=zipfile.ZIP_DEFLATED
            ) as zout:
                for item in zin.infolist():
                    name = item.filename
                    if name.startswith(prefix):
                        continue
                    data = zin.read(name)
                    zout.writestr(item, data)

            os.replace(tmp_path, zip_path)
            return True
        except Exception:
            try:
                if os.path.exists(f"{zip_path}.tmp"):
                    os.remove(f"{zip_path}.tmp")
            except Exception:
                pass
            return False

    def _export_component_sheets_with_writer(
        self,
        plan: Dict[str, Any],
        *,
        theme_rel_root: str,
        write_png: Callable[[str, tk.PhotoImage], None],
        quiet: bool = False,
    ) -> bool:
        """Export component sheets + backgrounds using a custom writer.

        `theme_rel_root` is a forward-slash relative path prefix (e.g. ".../<skin_pack>").
        `write_png(rel_path, image)` must write a PNG.
        """

        atlas_path = self._skin_pack_modules_path()
        if not atlas_path or not os.path.exists(atlas_path):
            if not quiet:
                messagebox.showerror(
                    "Export textures",
                    "Missing skin pack modules.\n\n"
                    "Create: skin_packs/<skin_name>/Modules.png (+ optional Background.png), then select it in the Skin Pack dropdown.",
                )
            return False

        try:
            atlas = tk.PhotoImage(file=atlas_path)
        except Exception as e:
            if not quiet:
                messagebox.showerror("Export textures", f"Failed to load texture sheet:\n{e}")
            return False

        # Resolve representative entries for each block.
        rep_map: Dict[Tuple[Any, ...], Entry] = {}
        for bk, rep in (plan.get("block_key_to_rep") or {}).items():
            try:
                pid, entry_id = rep
            except Exception:
                continue
            page = self.pages.get(int(pid))
            if not page:
                continue
            ent = page.entries.get(int(entry_id))
            if not ent:
                continue
            rep_map[bk] = ent

        # Render sheets.
        sheets = plan.get("sheets") or []
        for sheet_idx, sh in enumerate(sheets):
            sw = int(sh.get("w") or self.EXPORT_SHEET_PX)
            shh = int(sh.get("h") or self.EXPORT_SHEET_PX)
            sheet_img = tk.PhotoImage(width=sw, height=shh)

            for pl in sh.get("placements", []):
                bk = pl.get("block_key")
                ent = rep_map.get(bk)
                if ent is None:
                    continue
                block_img = self._compose_component_block(atlas, ent)
                if block_img is None:
                    continue
                px = int(pl.get("x") or 0)
                py = int(pl.get("y") or 0)
                w = int(block_img.width())
                h = int(block_img.height())
                sheet_img.tk.call(sheet_img, "copy", block_img, "-from", 0, 0, w, h, "-to", px, py)

            filename = f"sheet_{int(sheet_idx) + 1}.png"
            rel = f"{theme_rel_root}/{filename}".replace("\\", "/")
            try:
                write_png(rel, sheet_img)
            except Exception as e:
                if not quiet:
                    messagebox.showerror("Export textures", f"Failed writing {filename}:\n{e}")
                return False

        # Render backgrounds.
        for pid in self._sorted_page_ids():
            bg_img = self._render_flat_background_page(atlas, pid)
            if bg_img is None:
                continue
            filename = f"background_page_{int(pid)}.png"
            rel = f"{theme_rel_root}/{filename}".replace("\\", "/")
            try:
                write_png(rel, bg_img)
            except Exception as e:
                if not quiet:
                    messagebox.showerror("Export textures", f"Failed writing {filename}:\n{e}")
                return False

        return True

    # ----------------------------
    # UI
    # ----------------------------

    def _build_ui(self) -> None:
        outer = tk.Frame(self.root)
        outer.pack(fill="both", expand=True)

        # Fixed-width left pane so tool labels can wrap cleanly.
        left_pane = tk.Frame(outer, padx=8, pady=8, width=230)
        left_pane.pack(side="left", fill="y")
        left_pane.pack_propagate(False)

        # Scrollable container inside the fixed-width left pane.
        left_scroll = tk.Scrollbar(left_pane, orient="vertical")
        left_scroll.pack(side="right", fill="y")

        left_canvas = tk.Canvas(left_pane, highlightthickness=0, borderwidth=0, yscrollcommand=left_scroll.set)
        left_canvas.pack(side="left", fill="both", expand=True)
        left_scroll.configure(command=left_canvas.yview)

        left = tk.Frame(left_canvas)
        left_window = left_canvas.create_window((0, 0), window=left, anchor="nw")

        def _on_left_inner_configure(_e: tk.Event) -> None:
            # Update scroll region and keep inner frame width in sync.
            bbox = left_canvas.bbox("all")
            if bbox:
                left_canvas.configure(scrollregion=bbox)
            left_canvas.itemconfigure(left_window, width=left_canvas.winfo_width())

        def _on_left_canvas_configure(e: tk.Event) -> None:
            left_canvas.itemconfigure(left_window, width=e.width)

        def _on_left_mousewheel(e: tk.Event) -> None:
            # Windows: event.delta is typically multiples of 120.
            delta = int(-1 * (e.delta / 120)) if e.delta else 0
            if delta:
                left_canvas.yview_scroll(delta, "units")

        left.bind("<Configure>", _on_left_inner_configure)
        left_canvas.bind("<Configure>", _on_left_canvas_configure)
        left_canvas.bind("<MouseWheel>", _on_left_mousewheel)

        right = tk.Frame(outer, padx=8, pady=8)
        right.pack(side="right", fill="both", expand=True)

        # ----------------------------
        # Sidebar styling helpers
        # ----------------------------

        def _rgb16_to_hex(r: int, g: int, b: int) -> str:
            # winfo_rgb returns 0..65535
            return f"#{r // 256:02x}{g // 256:02x}{b // 256:02x}"

        def _blend(color_a: str, color_b: str, t: float) -> str:
            """Blend two Tk colors (0..1). Falls back to color_a on failures."""

            try:
                ar, ag, ab = self.root.winfo_rgb(color_a)
                br, bg, bb = self.root.winfo_rgb(color_b)
                t2 = max(0.0, min(1.0, float(t)))
                rr = int(ar + (br - ar) * t2)
                rg = int(ag + (bg - ag) * t2)
                rb = int(ab + (bb - ab) * t2)
                return _rgb16_to_hex(rr, rg, rb)
            except Exception:
                return color_a

        base_bg = str(self.root.cget("bg") or "SystemButtonFace")
        section_bg = _blend(base_bg, "#ffffff", 0.06)
        title_bg = _blend(base_bg, "#000000", 0.04)

        def _section(parent: tk.Misc, title: str) -> Tuple[tk.Frame, tk.Frame, tk.Label]:
            outer_sec = tk.Frame(parent, bd=1, relief="groove", bg=section_bg)
            title_lbl = tk.Label(
                outer_sec,
                text=title,
                font=("TkDefaultFont", 10, "bold"),
                bg=title_bg,
                anchor="w",
            )
            title_lbl.pack(fill="x", padx=6, pady=(6, 4))
            body = tk.Frame(outer_sec, bg=section_bg)
            body.pack(fill="x", padx=6, pady=(0, 6))
            return outer_sec, body, title_lbl

        # Sidebar ordering:
        # GUI Name -> Grid -> Tools -> Pages -> Selected Element -> Preview -> Export/Inject

        gui_sec, gui_body, _ = _section(left, "GUI Name")
        gui_sec.pack(fill="x", pady=(0, 8))
        tk.Entry(gui_body, textvariable=self.gui_name_var).pack(fill="x")

        grid_sec, grid_body, _ = _section(left, "Grid")
        grid_sec.pack(fill="x", pady=(0, 8))
        self.grid_btn = tk.Button(grid_body, text="Toggle 16×16 / 32×32", command=self.toggle_grid)
        self.grid_btn.pack(fill="x")

        tools_sec, tools_body, _ = _section(left, "Tools")
        tools_sec.pack(fill="x", pady=(0, 8))
        self.tool_var = tk.StringVar(value=self.current_tool.value)

        for t in Tool:
            rb = tk.Radiobutton(
                tools_body,
                text=t.value,
                value=t.value,
                variable=self.tool_var,
                command=self._on_tool_changed,
                anchor="w",
                justify="left",
                wraplength=210,
            )
            rb.pack(fill="x", anchor="w")

        pages_sec, pages_body, _ = _section(left, "Pages")
        pages_sec.pack(fill="x", pady=(0, 8))

        self.page_count_var = tk.StringVar(value=f"Pages: {len(self.pages)}")
        tk.Label(pages_body, textvariable=self.page_count_var, anchor="w", justify="left", bg=section_bg).pack(fill="x")

        page_ctrl = tk.Frame(pages_body, bg=section_bg)
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

        tk.Button(pages_body, text="New Page", command=self.add_page).pack(fill="x", pady=(4, 0))
        tk.Button(pages_body, text="Delete Page", command=self.delete_current_page).pack(fill="x")

        # Tool-specific metadata panel (only visible for standard buttons)
        self.std_btn_section, self.std_btn_meta_frame, _ = _section(left, "Standard Button")
        self.std_btn_section.pack(fill="x", pady=(0, 8))
        tk.Label(self.std_btn_meta_frame, text="Action: change page", anchor="w", bg=section_bg).pack(anchor="w")

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
            text="Close GUI",
            value="close",
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

        # Selected element meta panel (right-click an element in EDIT mode)
        self.selection_section, self.selection_frame, _ = _section(left, "Selected Element")
        self.selection_section.pack(fill="x", pady=(0, 8))

        self.selected_info_var = tk.StringVar(value="(none)")
        tk.Label(self.selection_frame, textvariable=self.selected_info_var, anchor="w", justify="left", bg=section_bg).pack(fill="x")

        self.sel_hover_enabled_var = tk.BooleanVar(value=False)
        self.sel_hover_text_var = tk.StringVar(value="")
        self.sel_locked_var = tk.BooleanVar(value=False)
        self.sel_label_var = tk.StringVar(value="")

        def apply_selected_meta() -> None:
            ent = self.entries.get(self.selected_entry_id) if self.selected_entry_id is not None else None
            if not ent:
                return
            meta = ent.meta if isinstance(ent.meta, dict) else {}
            meta["locked"] = bool(self.sel_locked_var.get())
            meta["hover"] = {
                "enabled": bool(self.sel_hover_enabled_var.get()),
                "text": str(self.sel_hover_text_var.get()),
            }
            ent.meta = meta

            # Displayed text/value is stored in ent.label for these tools.
            if ent.tool in (
                Tool.TEXT_SLOT,
                Tool.TEXT_ENTRY,
                Tool.SELECT_LIST,
                Tool.BUTTON_STANDARD,
                Tool.BUTTON_TOGGLE,
            ):
                ent.label = str(self.sel_label_var.get())

        tk.Checkbutton(
            self.selection_frame,
            text="Locked",
            variable=self.sel_locked_var,
            command=apply_selected_meta,
            anchor="w",
            justify="left",
            wraplength=210,
        ).pack(fill="x", anchor="w")

        tk.Checkbutton(
            self.selection_frame,
            text="Show hover text",
            variable=self.sel_hover_enabled_var,
            command=apply_selected_meta,
            anchor="w",
            justify="left",
            wraplength=210,
        ).pack(fill="x", anchor="w")

        tk.Label(self.selection_frame, text="Text (optional):", anchor="w", bg=section_bg).pack(anchor="w")
        sel_text_entry = tk.Entry(self.selection_frame, textvariable=self.sel_hover_text_var)
        sel_text_entry.pack(fill="x")
        sel_text_entry.bind("<KeyRelease>", lambda _e: apply_selected_meta())
        sel_text_entry.bind("<FocusOut>", lambda _e: apply_selected_meta())

        # Displayed text/value editor (independent from hover text)
        self.sel_label_frame = tk.Frame(self.selection_frame)
        self.sel_label_frame.pack(fill="x", pady=(6, 0))

        self.sel_label_title = tk.Label(self.sel_label_frame, text="Value/Text", font=("TkDefaultFont", 9, "bold"))
        self.sel_label_title.pack(anchor="w")
        self.sel_label_hint = tk.Label(self.sel_label_frame, text="", anchor="w", justify="left", wraplength=210)
        self.sel_label_hint.pack(anchor="w")

        sel_label_entry = tk.Entry(self.sel_label_frame, textvariable=self.sel_label_var)
        sel_label_entry.pack(fill="x")
        sel_label_entry.bind("<KeyRelease>", lambda _e: apply_selected_meta())
        sel_label_entry.bind("<FocusOut>", lambda _e: apply_selected_meta())

        # Hidden by default; shown only for tools that use ent.label.
        self.sel_label_frame.pack_forget()

        # Button-specific metadata (for standard buttons)
        self.sel_button_meta_frame = tk.Frame(self.selection_frame)
        self.sel_button_meta_frame.pack(fill="x", pady=(6, 0))

        tk.Label(self.sel_button_meta_frame, text="Button Action", font=("TkDefaultFont", 9, "bold")).pack(anchor="w")
        self.sel_btn_action_var = tk.StringVar(value="none")
        self.sel_btn_target_var = tk.StringVar(value="1")
        self.sel_btn_modulo_var = tk.BooleanVar(value=True)

        def apply_selected_button_meta() -> None:
            ent = self.entries.get(self.selected_entry_id) if self.selected_entry_id is not None else None
            if not ent:
                return
            if ent.tool != Tool.BUTTON_STANDARD:
                return

            mode = str(self.sel_btn_action_var.get())
            try:
                target_id = int(self.sel_btn_target_var.get())
            except ValueError:
                target_id = 1
                self.sel_btn_target_var.set("1")

            meta = ent.meta if isinstance(ent.meta, dict) else {}
            meta["page_change"] = {
                "mode": mode,
                "target_page_id": target_id,
                "modulo": bool(self.sel_btn_modulo_var.get()),
            }
            ent.meta = meta

        def on_sel_btn_action_changed() -> None:
            apply_selected_button_meta()
            if self.sel_btn_action_var.get() == "goto":
                self.sel_btn_target_row.pack(fill="x", padx=(18, 0), pady=(0, 2))
            else:
                self.sel_btn_target_row.pack_forget()

        tk.Radiobutton(
            self.sel_button_meta_frame,
            text="None",
            value="none",
            variable=self.sel_btn_action_var,
            command=on_sel_btn_action_changed,
            anchor="w",
            justify="left",
        ).pack(fill="x", anchor="w")

        tk.Radiobutton(
            self.sel_button_meta_frame,
            text="Close GUI",
            value="close",
            variable=self.sel_btn_action_var,
            command=on_sel_btn_action_changed,
            anchor="w",
            justify="left",
        ).pack(fill="x", anchor="w")

        tk.Radiobutton(
            self.sel_button_meta_frame,
            text="Go to page ID",
            value="goto",
            variable=self.sel_btn_action_var,
            command=on_sel_btn_action_changed,
            anchor="w",
            justify="left",
        ).pack(fill="x", anchor="w")

        self.sel_btn_target_row = tk.Frame(self.sel_button_meta_frame)
        self.sel_btn_target_row.pack(fill="x", padx=(18, 0), pady=(0, 2))
        tk.Label(self.sel_btn_target_row, text="Target ID:").pack(side="left")
        sel_target_entry = tk.Entry(self.sel_btn_target_row, textvariable=self.sel_btn_target_var, width=8)
        sel_target_entry.pack(side="left", padx=(6, 0))
        sel_target_entry.bind("<KeyRelease>", lambda _e: apply_selected_button_meta())
        sel_target_entry.bind("<FocusOut>", lambda _e: apply_selected_button_meta())

        tk.Radiobutton(
            self.sel_button_meta_frame,
            text="Next page (ID+1)",
            value="next",
            variable=self.sel_btn_action_var,
            command=on_sel_btn_action_changed,
            anchor="w",
            justify="left",
        ).pack(fill="x", anchor="w")

        tk.Radiobutton(
            self.sel_button_meta_frame,
            text="Previous page (ID-1)",
            value="prev",
            variable=self.sel_btn_action_var,
            command=on_sel_btn_action_changed,
            anchor="w",
            justify="left",
        ).pack(fill="x", anchor="w")

        tk.Checkbutton(
            self.sel_button_meta_frame,
            text="Modulo wrap (last -> first)",
            variable=self.sel_btn_modulo_var,
            command=apply_selected_button_meta,
            anchor="w",
            justify="left",
            wraplength=210,
        ).pack(fill="x", anchor="w", pady=(2, 0))

        # Hidden by default; shown only for standard buttons.
        self.sel_button_meta_frame.pack_forget()

        self.clear_selection_btn = tk.Button(self.selection_frame, text="Clear selection", command=self._clear_selection)
        self.clear_selection_btn.pack(fill="x", pady=(4, 0))

        # Start hidden until something is selected.
        self.selection_section.pack_forget()

        # Preview (overall)
        self.preview_section_frame, preview_body, _ = _section(left, "Preview")
        self.preview_section_frame.pack(fill="x", pady=(0, 8))
        self.preview_btn = tk.Button(preview_body, text="Preview: OFF", command=self.toggle_preview)
        self.preview_btn.pack(fill="x")

        tk.Label(preview_body, text="Skin Pack", bg=section_bg).pack(anchor="w", pady=(6, 0))
        self.skin_pack_var = tk.StringVar(value="(none)")
        self.skin_pack_menu = tk.OptionMenu(preview_body, self.skin_pack_var, "(none)", command=self._on_skin_pack_changed)
        self.skin_pack_menu.pack(fill="x")

        # Export / inject settings panel (persisted to .gui_builder_settings.json)
        export_sec, export_body, _ = _section(left, "Export / Inject")
        export_sec.pack(fill="x", pady=(0, 8))

        tk.Label(export_body, text="(Use File menu to export/inject)", anchor="w", justify="left", wraplength=210, bg=section_bg).pack(
            anchor="w", pady=(0, 2)
        )

        tk.Checkbutton(
            export_body,
            text="Group buttons by size (reuse)",
            variable=self.group_buttons_by_size_var,
            anchor="w",
            justify="left",
            wraplength=210,
        ).pack(fill="x", anchor="w")

        tk.Label(export_body, text="Export base folder:", bg=section_bg).pack(anchor="w", pady=(6, 0))
        row = tk.Frame(export_body, bg=section_bg)
        row.pack(fill="x")
        tk.Entry(row, textvariable=self.export_base_dir_var).pack(side="left", fill="x", expand=True)
        tk.Button(row, text="Browse", command=self._browse_export_base_dir).pack(side="left", padx=(6, 0))

        tk.Label(export_body, text="Inject pack:", bg=section_bg).pack(anchor="w", pady=(8, 0))
        kind_row = tk.Frame(export_body, bg=section_bg)
        kind_row.pack(fill="x")
        tk.Radiobutton(kind_row, text="Folder", value="folder", variable=self.inject_pack_kind_var, anchor="w").pack(
            side="left"
        )
        tk.Radiobutton(kind_row, text="Zip", value="zip", variable=self.inject_pack_kind_var, anchor="w").pack(
            side="left", padx=(10, 0)
        )

        row = tk.Frame(export_body, bg=section_bg)
        row.pack(fill="x")
        tk.Entry(row, textvariable=self.inject_pack_path_var).pack(side="left", fill="x", expand=True)
        tk.Button(row, text="Browse", command=self._browse_inject_pack_path).pack(side="left", padx=(6, 0))

        tk.Label(export_body, text="Manifest folder (inject):", bg=section_bg).pack(anchor="w", pady=(8, 0))
        row = tk.Frame(export_body, bg=section_bg)
        row.pack(fill="x")
        tk.Entry(row, textvariable=self.manifest_output_dir_var).pack(side="left", fill="x", expand=True)
        tk.Button(row, text="Browse", command=self._browse_manifest_output_dir).pack(side="left", padx=(6, 0))

        tk.Label(export_body, text="Additional skin packs:", bg=section_bg).pack(anchor="w", pady=(8, 0))
        self._extra_skin_packs_frame = tk.Frame(export_body, bg=section_bg)
        self._extra_skin_packs_frame.pack(fill="x")
        self._rebuild_extra_skin_packs_ui()

        # NOTE: Export/Inject actions are intentionally only in the File menu.

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
        self.canvas.pack(fill="both", expand=True)

        self.help = tk.Label(
            right,
            text=(
                "EDIT MODE:\n"
                "  Left click/drag to place/remove\n"
                "PREVIEW MODE:\n"
                "  Interact with buttons, open text/select popups\n"
                "Square tools: item_slot\\n"
                "File -> Save/Load JSON\\n"
                "File -> Export Textures"
            ),
            anchor="w",
            justify="left",
        )
        self.help.pack(fill="x", pady=(6, 0))

        self._refresh_std_btn_meta_visibility()
        self._refresh_selection_ui()

    def _bind_events(self) -> None:
        self.canvas.bind("<ButtonPress-1>", self.on_left_press)
        self.canvas.bind("<B1-Motion>", self.on_left_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_left_release)
        self.canvas.bind("<Motion>", self.on_motion)
        self.canvas.bind("<ButtonPress-3>", self.on_right_press)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

    def _on_canvas_configure(self, event: tk.Event) -> None:
        """Keep the grid scaled and centered when the window is resized."""
        try:
            w = int(event.width)
            h = int(event.height)
        except Exception:
            return

        if w <= 1 or h <= 1:
            return

        self._canvas_widget_w = w
        self._canvas_widget_h = h

        # Fit a square grid into the available canvas area.
        side = min(w, h)
        cell_px = max(1, side // self.grid_n)
        grid_px = cell_px * self.grid_n

        self._canvas_offset_x = max(0, (w - grid_px) // 2)
        self._canvas_offset_y = max(0, (h - grid_px) // 2)

        # Only trigger a redraw if sizing changed.
        if grid_px != self.canvas_px or cell_px != self.cell_px:
            self.canvas_px = grid_px
            self.cell_px = cell_px

            # Background caches depend on cell_px/canvas_px.
            self._preview_background_cache_key = None
            self._preview_background_image = None

        # Redraw on any resize (offsets may have changed even if cell_px didn't).
        self.redraw()

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

    def _refresh_std_btn_meta_visibility(self) -> None:
        if not hasattr(self, "std_btn_meta_frame"):
            return

        if self.current_tool == Tool.BUTTON_STANDARD:
            if hasattr(self, "std_btn_section") and not self.std_btn_section.winfo_ismapped():
                self.std_btn_section.pack(fill="x", pady=(0, 8))
        else:
            if hasattr(self, "std_btn_section") and self.std_btn_section.winfo_ismapped():
                self.std_btn_section.pack_forget()

        if hasattr(self, "std_btn_target_row") and hasattr(self, "std_btn_action_var"):
            if self.std_btn_action_var.get() == "goto":
                self.std_btn_target_row.pack(fill="x", padx=(18, 0), pady=(0, 2))
            else:
                self.std_btn_target_row.pack_forget()

    def _refresh_selection_ui(self) -> None:
        if not hasattr(self, "selection_frame"):
            return

        ent = self.entries.get(self.selected_entry_id) if self.selected_entry_id is not None else None
        if not ent:
            if hasattr(self, "selection_section") and self.selection_section.winfo_ismapped():
                self.selection_section.pack_forget()
            self.selected_info_var.set("(none)")
            self.sel_hover_enabled_var.set(False)
            self.sel_hover_text_var.set("")
            self.sel_locked_var.set(False)
            if hasattr(self, "sel_label_var"):
                self.sel_label_var.set("")
            if hasattr(self, "sel_label_frame") and self.sel_label_frame.winfo_ismapped():
                self.sel_label_frame.pack_forget()
            if hasattr(self, "sel_button_meta_frame") and self.sel_button_meta_frame.winfo_ismapped():
                self.sel_button_meta_frame.pack_forget()
            return

        # Ensure the selection panel is visible in its intended position.
        if hasattr(self, "selection_section") and not self.selection_section.winfo_ismapped():
            if hasattr(self, "preview_section_frame") and self.preview_section_frame.winfo_exists():
                self.selection_section.pack(fill="x", pady=(0, 8), before=self.preview_section_frame)
            else:
                self.selection_section.pack(fill="x", pady=(0, 8))

        if ent.uid:
            self.selected_info_var.set(f"UID {ent.uid} | ID {ent.entry_id} | {ent.tool.value}")
        else:
            self.selected_info_var.set(f"ID {ent.entry_id} | {ent.tool.value}")
        meta = ent.meta if isinstance(ent.meta, dict) else {}
        self.sel_locked_var.set(bool(meta.get("locked", False)))
        hover = meta.get("hover")
        if not isinstance(hover, dict):
            hover = {"enabled": False, "text": ""}
        self.sel_hover_enabled_var.set(bool(hover.get("enabled", False)))
        self.sel_hover_text_var.set(str(hover.get("text", "")))

        # Show/hide displayed text/value editor depending on tool.
        if hasattr(self, "sel_label_frame") and hasattr(self, "sel_label_title") and hasattr(self, "sel_label_hint"):
            if ent.tool in (
                Tool.TEXT_SLOT,
                Tool.TEXT_ENTRY,
                Tool.SELECT_LIST,
                Tool.BUTTON_STANDARD,
                Tool.BUTTON_TOGGLE,
            ):
                if not self.sel_label_frame.winfo_ismapped():
                    if hasattr(self, "clear_selection_btn"):
                        self.sel_label_frame.pack(fill="x", pady=(6, 0), before=self.clear_selection_btn)
                    else:
                        self.sel_label_frame.pack(fill="x", pady=(6, 0))

                if ent.tool == Tool.TEXT_SLOT:
                    self.sel_label_title.configure(text="Text Slot Text")
                    self.sel_label_hint.configure(text="Shown inside the text slot. Independent from hover text.")
                elif ent.tool == Tool.TEXT_ENTRY:
                    self.sel_label_title.configure(text="Text Entry Value")
                    self.sel_label_hint.configure(text="Current input value (preview/demo). Independent from hover text.")
                elif ent.tool in (Tool.BUTTON_STANDARD, Tool.BUTTON_TOGGLE):
                    self.sel_label_title.configure(text="Button Text")
                    self.sel_label_hint.configure(text="Shown on the button. Independent from hover text.")
                else:
                    self.sel_label_title.configure(text="Select List Value")
                    self.sel_label_hint.configure(text="Currently selected item (preview/demo). Independent from hover text.")

                self.sel_label_var.set(str(ent.label or ""))
            else:
                if self.sel_label_frame.winfo_ismapped():
                    self.sel_label_frame.pack_forget()

        # Button options only apply to standard buttons.
        if hasattr(self, "sel_button_meta_frame"):
            if ent.tool == Tool.BUTTON_STANDARD:
                if not self.sel_button_meta_frame.winfo_ismapped():
                    if hasattr(self, "clear_selection_btn"):
                        self.sel_button_meta_frame.pack(fill="x", pady=(6, 0), before=self.clear_selection_btn)
                    else:
                        self.sel_button_meta_frame.pack(fill="x", pady=(6, 0))

                page_change = meta.get("page_change")
                if not isinstance(page_change, dict):
                    page_change = {"mode": "none", "target_page_id": 1, "modulo": True}

                mode = str(page_change.get("mode", "none"))
                self.sel_btn_action_var.set(mode)
                self.sel_btn_modulo_var.set(bool(page_change.get("modulo", True)))
                self.sel_btn_target_var.set(str(page_change.get("target_page_id", 1)))

                if mode == "goto":
                    self.sel_btn_target_row.pack(fill="x", padx=(18, 0), pady=(0, 2))
                else:
                    self.sel_btn_target_row.pack_forget()
            else:
                if self.sel_button_meta_frame.winfo_ismapped():
                    self.sel_button_meta_frame.pack_forget()

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

        if ent.tool in (Tool.BUTTON_STANDARD, Tool.BUTTON_TOGGLE):
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

        if ent.tool == Tool.BUTTON_STANDARD:
            meta = ent.meta if isinstance(ent.meta, dict) else {}
            page_change = meta.get("page_change")
            if not isinstance(page_change, dict):
                return "Standard: action=none"

            mode = str(page_change.get("mode", "none"))
            if mode == "close":
                return "Standard: action=close_gui"
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
        x -= self._canvas_offset_x
        y -= self._canvas_offset_y
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
        uid = self.next_uid
        self.next_uid += 1
        ent = Entry(entry_id=eid, uid=uid, tool=self.current_tool, rect=rect)
        if self.current_tool == Tool.BUTTON_STANDARD:
            # Snapshot tool metadata into the entry so changes only affect newly placed buttons.
            ent.meta = json.loads(json.dumps(self.standard_button_tool_meta))

        # New entries default to: allow hover textures, but no hover tooltip text.
        if self.current_tool != Tool.BACKGROUND:
            meta = ent.meta if isinstance(ent.meta, dict) else {}
            meta.setdefault("locked", False)
            meta.setdefault("hover", {"enabled": False, "text": ""})
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

        meta = ent.meta if isinstance(ent.meta, dict) else {}
        if bool(meta.get("locked", False)):
            self.set_status(f"Preview: entry {ent.entry_id} is locked")
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
        elif ent.tool == Tool.BUTTON_TOGGLE:
            ent.active = not ent.active
            self.set_status(f"Preview: toggle button {ent.entry_id} => {ent.active}")

        elif ent.tool == Tool.TEXT_ENTRY:
            self._popup_text_entry(ent)

        elif ent.tool == Tool.SELECT_LIST:
            self._popup_select_list(ent)

        elif ent.tool == Tool.TEXT_SLOT:
            self.set_status(f"Preview: text slot {ent.entry_id} (display only)")

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

        if mode == "close":
            self.set_status("Preview: close GUI")
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
        return

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
        self._set_preview_mode(not self.preview_mode)

    def toggle_grid(self) -> None:
        new_n = 32 if self.grid_n == 16 else 16

        # MVP: clear on toggle
        self.grid_n = new_n

        # Recompute sizing based on current canvas widget size.
        w = int(self.canvas.winfo_width()) if hasattr(self, "canvas") else self.canvas_px
        h = int(self.canvas.winfo_height()) if hasattr(self, "canvas") else self.canvas_px
        side = min(max(1, w), max(1, h))
        self.cell_px = max(1, side // self.grid_n)
        self.canvas_px = self.cell_px * self.grid_n

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
    # Skin packs (preview)
    # ----------------------------

    def _assets_base_dir(self) -> str:
        # Prefer "next to the entrypoint" (gui_builder.py) since that's where users will drop assets.
        return os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else os.getcwd()

    def _skin_packs_dir(self) -> str:
        return os.path.join(self._assets_base_dir(), SKIN_PACKS_DIRNAME)

    def _skin_pack_modules_path(self) -> Optional[str]:
        if self._skin_pack_name == "(none)":
            return None
        info = self._skin_pack_paths.get(self._skin_pack_name)
        if not info:
            return None
        return info.get("modules")

    def _skin_pack_background_path(self) -> Optional[str]:
        if self._skin_pack_name == "(none)":
            return None
        info = self._skin_pack_paths.get(self._skin_pack_name)
        if not info:
            return None
        return info.get("background")

    def _scan_skin_packs(self) -> None:
        """Detect skin packs from skin_packs/<name>/Modules.png (+ optional Background.png)."""
        has_ui = hasattr(self, "skin_pack_var") and hasattr(self, "skin_pack_menu")

        packs_dir = self._skin_packs_dir()
        packs: Dict[str, Dict[str, str]] = {}
        if os.path.isdir(packs_dir):
            for name in sorted(os.listdir(packs_dir)):
                full = os.path.join(packs_dir, name)
                if not os.path.isdir(full):
                    continue
                modules = os.path.join(full, MODULES_FILENAME)
                if not os.path.isfile(modules):
                    continue
                bg = os.path.join(full, BACKGROUND_FILENAME)
                packs[name] = {
                    "dir": full,
                    "modules": modules,
                    "background": bg if os.path.isfile(bg) else "",
                }

        self._skin_pack_paths = packs

        if self._skin_pack_name != "(none)" and self._skin_pack_name not in packs:
            self._on_skin_pack_changed("(none)")

        if has_ui:
            options = ["(none)"] + list(packs.keys())
            menu = self.skin_pack_menu["menu"]
            menu.delete(0, "end")
            for opt in options:
                menu.add_command(
                    label=opt,
                    command=lambda v=opt: self.skin_pack_var.set(v) or self._on_skin_pack_changed(v),
                )
            self.skin_pack_var.set(self._skin_pack_name)

        # Auto-select if there's exactly one skin pack.
        if self._skin_pack_name == "(none)" and len(packs) == 1:
            only = next(iter(packs.keys()))
            if has_ui:
                self.skin_pack_var.set(only)
            self._on_skin_pack_changed(only)

        self._rebuild_extra_skin_packs_ui()

    def _on_skin_pack_changed(self, selection: str) -> None:
        name = str(selection)
        if name == self._skin_pack_name:
            return

        self._skin_pack_name = name
        self._skin_background_src = None
        self._skin_background_scaled.clear()
        self._preview_background_cache_key = None
        self._preview_background_image = None

        # Reset texture sheet (preview modules)
        self._texture_sheet = None

        if name == "(none)":
            self.set_status("Skin pack: none (using colors)")
            self.redraw()
            self._rebuild_extra_skin_packs_ui()
            return

        modules_path = self._skin_pack_modules_path()
        if not modules_path or not os.path.isfile(modules_path):
            self._skin_pack_name = "(none)"
            self.set_status("Skin pack: missing Modules.png")
            self.redraw()
            return

        try:
            self._texture_sheet = TextureSheet(self.root, modules_path, tile_px=TILE_PX)
        except Exception:
            self._texture_sheet = None

        bg_path = self._skin_pack_background_path()
        if bg_path and os.path.isfile(bg_path):
            try:
                self._skin_background_src = tk.PhotoImage(file=bg_path)
            except Exception:
                self._skin_background_src = None

        if self._texture_sheet is None:
            self.set_status(f"Skin pack: {name} (modules failed to load)")
        else:
            self.set_status(f"Skin pack: {name}")
        self.redraw()

        self._rebuild_extra_skin_packs_ui()

    def _scale_factors(self) -> Tuple[int, int]:
        """Return (zoom, subsample) factors so: TILE_PX * zoom / subsample ~= cell_px."""
        cell_px = int(self.cell_px)
        if cell_px <= 0:
            return 1, 1

        tile_px = int(TILE_PX)
        if tile_px <= 0:
            return 1, 1

        best_zoom, best_sub = 1, 1
        best_err = abs(tile_px - cell_px)
        best_complexity = best_zoom * best_sub

        for sub in range(1, 65):
            zoom = int(round((cell_px * sub) / tile_px))
            if zoom < 1 or zoom > 64:
                continue
            scaled = (tile_px * zoom) / sub
            err = abs(scaled - cell_px)
            complexity = zoom * sub
            if err < best_err - 1e-9 or (abs(err - best_err) <= 1e-9 and complexity < best_complexity):
                best_zoom, best_sub = zoom, sub
                best_err = err
                best_complexity = complexity

        return best_zoom, best_sub

    def _get_scaled_background_tile(self) -> Optional[tk.PhotoImage]:
        src = self._skin_background_src
        if src is None:
            return None

        cached = self._skin_background_scaled.get(self.cell_px)
        if cached is not None:
            return cached

        zoom, subsample = self._scale_factors()
        tile = src
        if zoom != 1 or subsample != 1:
            tile = src.zoom(zoom, zoom).subsample(subsample, subsample)
        self._skin_background_scaled[self.cell_px] = tile
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

        key = (self.current_page_id, self.grid_n, self.cell_px, self._skin_pack_name, self._background_signature())
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
        """Legacy no-op retained for compatibility.

        Preview textures are loaded via skin pack selection.
        """
        return

    def _ctm_mask(self, cell_set: "set[tuple[int, int]]", x: int, y: int) -> int:
        mask = 0
        for dx, dy, bit in CTM_DIRS:
            if (x + dx, y + dy) in cell_set:
                mask |= bit
        return mask

    def _entry_visual_state(self, ent: Entry) -> str:
        """Return a small state label for picking tiles."""
        hovered_raw = self.preview_mode and (self._preview_hover_entry_id == ent.entry_id)
        meta = ent.meta if isinstance(ent.meta, dict) else {}
        locked = bool(meta.get("locked", False))
        disabled = bool(meta.get("disabled", False))
        hovered = hovered_raw and (not locked) and (not disabled)

        # Buttons are the only widgets that support hover textures.
        if ent.tool == Tool.BUTTON_STANDARD:
            mapping = ENTRY_TOOL_MODULES.get(ent.tool, {})
            if locked:
                return str(mapping.get("locked") or mapping.get("base") or "")
            return str(mapping.get("hover" if hovered else "base") or "")

        if ent.tool == Tool.BUTTON_TOGGLE:
            mapping = ENTRY_TOOL_MODULES.get(ent.tool, {})

            # Keys: base (pressed/on), unpressed (off), hover_base, hover_unpressed, pressed_locked, unpressed_locked
            if locked:
                return str((mapping.get("pressed_locked") if bool(ent.active) else mapping.get("unpressed_locked")) or mapping.get("base") or "")

            if disabled:
                return str(mapping.get("unpressed_locked") or mapping.get("pressed_locked") or mapping.get("unpressed") or "")

            if bool(ent.active):
                # Active/pressed state
                if hovered and mapping.get("hover_base") is not None:
                    return str(mapping.get("hover_base") or "")
                return str(mapping.get("base") or mapping.get("unpressed") or "")

            # Not active (unpressed/off)
            if hovered and mapping.get("hover_unpressed") is not None:
                return str(mapping.get("hover_unpressed") or "")
            return str(mapping.get("unpressed") or mapping.get("base") or "")

        # Non-buttons: no hover variants. (Optional locked variant via meta.)
        mapping = ENTRY_TOOL_MODULES.get(ent.tool) or {}
        if locked:
            return str(mapping.get("locked") or mapping.get("base") or "")
        return str(mapping.get("base") or "")

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
            x0 = self._canvas_offset_x + (cx * self.cell_px)
            y0 = self._canvas_offset_y + (cy * self.cell_px)
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
            x0 = self._canvas_offset_x + (cx * self.cell_px)
            y0 = self._canvas_offset_y + (cy * self.cell_px)
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
                            "uid": e.uid,
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
            "gui_name": str(self.gui_name_var.get() or ""),
            "grid_n": self.grid_n,
            "start_page_id": self.start_page_id,
            "next_uid": self.next_uid,
            "available_in_skin_packs": sorted(self._skin_pack_paths.keys()),
            "pages": pages_payload,
        }

    def _safe_dir_name(self, name: str) -> str:
        # Keep Windows path characters safe.
        bad = '<>:"/\\|?*'
        out = "".join(("_" if c in bad else c) for c in name).strip()
        return out or "skin"

    def _safe_gui_name(self, name: str) -> str:
        safe = self._safe_dir_name(str(name))
        return safe if safe != "skin" else "gui"

    def _reset_export_dir(self, path: str, *, quiet: bool = False) -> bool:
        """Ensure `path` exists and is empty.

        This is used for exports so repeated exports replace prior content.
        """

        try:
            if os.path.exists(path):
                if not os.path.isdir(path):
                    if not quiet:
                        messagebox.showerror("Export textures", f"Export path exists but is not a folder:\n{path}")
                    return False

                for name in os.listdir(path):
                    child = os.path.join(path, name)
                    if os.path.isdir(child):
                        shutil.rmtree(child)
                    else:
                        os.remove(child)
            else:
                os.makedirs(path, exist_ok=True)
        except Exception as e:
            if not quiet:
                messagebox.showerror("Export textures", f"Failed clearing export folder:\n{path}\n\n{e}")
            return False

        return True

    def _write_gui_manifest(self, gui_root: str, manifest: Dict[str, Any], *, quiet: bool = False) -> bool:
        path = os.path.join(gui_root, "gui_manifest.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2)
        except Exception as e:
            if not quiet:
                messagebox.showerror("Export textures", f"Failed writing gui_manifest.json:\n{e}")
            return False
        return True

    def _write_gui_buttons_script(self, gui_root: str, manifest: Dict[str, Any], gui_name: str, *, quiet: bool = False) -> bool:
        """Generate a gui_<gui_name>.js file with button handler skeleton.
        
        Creates a nested switch statement with a case for each page,
        and for each page, a case for each button component.
        """
        pages = manifest.get("pages", [])
        
        # Generate inner cases for each page's buttons
        page_cases = []
        for page_info in pages:
            page_id = page_info.get("page", 0)
            components = page_info.get("components", [])
            
            # Filter for button-like components (buttons and toggle buttons)
            button_components = [
                c for c in components 
                if c.get("type") in ("button", "toggle_button")
            ]
            
            if button_components or True:  # Always include page case even if no buttons
                # Generate button cases
                button_cases = []
                for comp in button_components:
                    button_id = comp.get("id", 0)
                    button_cases.append(f"                // case {button_id}:")
                    button_cases.append(f"                //     // Do something")
                    button_cases.append(f"                //     break;")
                
                # Build the page case
                page_case = f"        case {page_id}:\n"
                page_case += "            switch (buttonId) {\n"
                if button_cases:
                    page_case += "\n".join(button_cases) + "\n"
                else:
                    page_case += "                // Add button cases here\n"
                page_case += "            }\n"
                page_case += "            break;"
                page_cases.append(page_case)
        
        # Build the full script
        script = "function guiButtons(event, buttonId, pageId) {\n"
        script += "    switch (pageId) {\n"
        script += "\n".join(page_cases) + "\n"
        script += "    }\n"
        script += "}\n\n"
        script += "function guiBuilder_updateManifest(event, manifest) {\n"
        script += "    // Modify the manifest as needed before building the GUI\n"
        script += "    // For example, if you want to make a locked button unlocked due to some conditions.\n"
        script += "    return manifest;\n"
        script += "}\n"
        
        # Write to file
        filename = f"gui_{gui_name}.js"
        path = os.path.join(gui_root, filename)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(script)
        except Exception as e:
            if not quiet:
                messagebox.showerror("Export textures", f"Failed writing {filename}:\n{e}")
            return False
        return True

    def _manifest_pages_payload(self, components: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Group flat component list into per-page entries for the manifest."""

        # Build a lookup by page id.
        comps_by_page: Dict[int, List[Dict[str, Any]]] = {}
        for c in components:
            page = int((c.get("offset") or {}).get("page") or 0)
            comps_by_page.setdefault(page, []).append(c)

        pages_payload: List[Dict[str, Any]] = []
        for pid in self._sorted_page_ids():
            pid_i = int(pid)
            page_components: List[Dict[str, Any]] = []
            for c in comps_by_page.get(pid_i, []):
                cc = dict(c)
                off = cc.get("offset")
                if isinstance(off, dict) and "page" in off:
                    off2 = dict(off)
                    del off2["page"]
                    cc["offset"] = off2
                page_components.append(cc)

            # Stable ordering within a page.
            page_components.sort(key=lambda x: int(x.get("id", 0)))
            pages_payload.append({"page": pid_i, "components": page_components})

        return pages_payload

    def _ask_additional_skin_packs(self, *, default_pack: str, title: str) -> Optional[List[str]]:
        """Ask the user which *additional* skin packs to export/inject.

        The currently selected skin pack (default_pack) is always included automatically.

        Returns:
            List[str] -> selected additional pack names (can be empty)
            None      -> user cancelled
        """

        # Refresh from disk so the list is up-to-date.
        self._scan_skin_packs()

        packs = sorted(self._skin_pack_paths.keys())
        choices = [p for p in packs if p != default_pack]

        if not choices:
            return []

        win = tk.Toplevel(self.root)
        win.title(title)
        win.transient(self.root)
        win.grab_set()

        result: Dict[str, Any] = {"ok": False}
        vars_by_name: Dict[str, tk.BooleanVar] = {name: tk.BooleanVar(value=False) for name in choices}

        tk.Label(
            win,
            text=(
                "Select extra skin packs (optional).\n"
                f"The selected skin pack is always included: {default_pack}"
            ),
            justify="left",
        ).pack(anchor="w", padx=10, pady=(10, 6))

        # Scrollable list, so it still works if there are many packs.
        outer = tk.Frame(win)
        outer.pack(fill="both", expand=True, padx=10)

        canvas = tk.Canvas(outer, borderwidth=0, highlightthickness=0)
        scrollbar = tk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        inner = tk.Frame(canvas)
        inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_inner_configure(_: tk.Event) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_configure(_: tk.Event) -> None:
            # Keep inner frame width in sync with the canvas width.
            canvas.itemconfigure(inner_id, width=canvas.winfo_width())

        inner.bind("<Configure>", _on_inner_configure)
        canvas.bind("<Configure>", _on_canvas_configure)

        for name in choices:
            tk.Checkbutton(inner, text=name, variable=vars_by_name[name]).pack(anchor="w", fill="x")

        buttons = tk.Frame(win)
        buttons.pack(fill="x", padx=10, pady=10)

        def _select_all() -> None:
            for v in vars_by_name.values():
                v.set(True)

        def _select_none() -> None:
            for v in vars_by_name.values():
                v.set(False)

        def _ok() -> None:
            result["ok"] = True
            win.destroy()

        def _cancel() -> None:
            result["ok"] = False
            win.destroy()

        tk.Button(buttons, text="All", command=_select_all).pack(side="left")
        tk.Button(buttons, text="None", command=_select_none).pack(side="left", padx=(6, 0))
        tk.Button(buttons, text="Cancel", command=_cancel).pack(side="right")
        tk.Button(buttons, text="OK", command=_ok).pack(side="right", padx=(0, 6))

        win.protocol("WM_DELETE_WINDOW", _cancel)
        win.minsize(360, 240)
        self.root.wait_window(win)

        if not result.get("ok"):
            return None

        selected = [name for name, v in vars_by_name.items() if bool(v.get())]
        return selected

    def load_from_json_dict(self, data: dict) -> None:
        if not isinstance(data, dict):
            raise ValueError("Invalid JSON root (expected object).")

        if "gui_name" in data:
            try:
                self.gui_name_var.set(str(data.get("gui_name") or ""))
            except Exception:
                # Keep existing GUI name if the value is invalid.
                pass

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

        # Global unique IDs (UID) are optional in older exports.
        used_uids: set[int] = set()
        uid_counter = 1
        try:
            uid_counter = max(1, int(data.get("next_uid", 1)))
        except (TypeError, ValueError):
            uid_counter = 1
        max_uid = 0

        def _alloc_uid(requested: Any) -> int:
            nonlocal uid_counter, max_uid

            try:
                rid = int(requested)
            except (TypeError, ValueError):
                rid = 0

            if rid > 0 and rid not in used_uids:
                used_uids.add(rid)
                max_uid = max(max_uid, rid)
                return rid

            while uid_counter in used_uids:
                uid_counter += 1

            used_uids.add(uid_counter)
            max_uid = max(max_uid, uid_counter)
            uid_counter += 1
            return max_uid

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
                raw_tool = str(obj.get("tool") or "")
                if raw_tool == "button_press":
                    # Deprecated tool (removed): treat as a standard button.
                    tool = Tool.BUTTON_STANDARD
                else:
                    try:
                        tool = Tool(raw_tool)
                    except Exception:
                        # Skip unknown tools.
                        continue
                r = obj["rect"]
                rect = Rect(int(r["x0"]), int(r["y0"]), int(r["x1"]), int(r["y1"]))
                rect = rect.normalized()
                eid = int(obj["id"])
                max_id = max(max_id, eid)

                uid = _alloc_uid(obj.get("uid"))

                ent = Entry(
                    entry_id=eid,
                    uid=uid,
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

        # Ensure the next UID is always above the max observed.
        self.next_uid = max(uid_counter, max_uid + 1)

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
        default_filename = f"{self._safe_gui_name(self.gui_name_var.get())}.json"
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
            title="Save GUI JSON",
            initialfile=default_filename,
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
    # Texture export
    # ----------------------------

    def _compose_entry_variant_image(self, atlas: tk.PhotoImage, ent: Entry, state_key: str) -> Optional[tk.PhotoImage]:
        """Create a full-size assembled image for a single entry variant.

        This is primarily used for buttons since CustomNPCs expects single-image textures
        (not multi-tile CTM rendering) for button elements.
        """

        origin = CTM_ORIGINS.get(state_key)
        if not origin:
            return None

        r = ent.rect.normalized()
        w_tiles = r.width()
        h_tiles = r.height()

        # Build an unscaled pixel image (16px tiles).
        out_w = w_tiles * TILE_PX
        out_h = h_tiles * TILE_PX
        if out_w <= 0 or out_h <= 0:
            return None

        out = tk.PhotoImage(width=out_w, height=out_h)

        ox, oy = origin
        cell_set = set(r.cells())

        for (cx, cy) in r.cells():
            mask = self._ctm_mask(cell_set, cx, cy)
            dx, dy = ctm_tile_offset(mask)
            src_col = ox + dx
            src_row = oy + dy

            sx0 = src_col * TILE_PX
            sy0 = src_row * TILE_PX
            sx1 = sx0 + TILE_PX
            sy1 = sy0 + TILE_PX

            dx0 = (cx - r.x0) * TILE_PX
            dy0 = (cy - r.y0) * TILE_PX

            out.tk.call(out, "copy", atlas, "-from", sx0, sy0, sx1, sy1, "-to", dx0, dy0)

        return out

    def _scale_factors_for(self, src_px: int, target_px: int) -> Tuple[int, int]:
        """Return (zoom, subsample) so: src_px * zoom / subsample ~= target_px."""

        if src_px <= 0 or target_px <= 0:
            return 1, 1

        best_zoom, best_sub = 1, 1
        best_err = abs(src_px - target_px)
        best_complexity = best_zoom * best_sub

        for sub in range(1, 65):
            zoom = int(round((target_px * sub) / src_px))
            if zoom < 1 or zoom > 64:
                continue
            scaled = (src_px * zoom) / sub
            err = abs(scaled - target_px)
            complexity = zoom * sub
            if err < best_err - 1e-9 or (abs(err - best_err) <= 1e-9 and complexity < best_complexity):
                best_zoom, best_sub = zoom, sub
                best_err = err
                best_complexity = complexity

        return best_zoom, best_sub

    def _get_background_tile_for_export(self) -> Optional[tk.PhotoImage]:
        """Return the current skin pack Background.png scaled to TILE_PX (or None)."""

        src = self._skin_background_src
        if src is None:
            return None

        # Cache the export-scale tile under key TILE_PX.
        cached = self._skin_background_scaled.get(TILE_PX)
        if cached is not None:
            return cached

        # Background tiles are expected to be 16x16, but scale if they aren't.
        src_w = int(src.width())
        if src_w <= 0:
            return None

        zoom, subsample = self._scale_factors_for(src_w, int(TILE_PX))
        tile = src
        if zoom != 1 or subsample != 1:
            tile = src.zoom(zoom, zoom).subsample(subsample, subsample)
        self._skin_background_scaled[TILE_PX] = tile
        return tile

    def _blit_ctm_cellset_to_image(self, dest: tk.PhotoImage, atlas: tk.PhotoImage, cell_set: "set[tuple[int,int]]", state_key: str) -> bool:
        """Blit a CTM-rendered cell set into a PhotoImage at TILE_PX scale."""

        origin = CTM_ORIGINS.get(state_key)
        if not origin:
            return False

        ox, oy = origin
        for (cx, cy) in cell_set:
            mask = self._ctm_mask(cell_set, cx, cy)
            dx, dy = ctm_tile_offset(mask)
            src_col = ox + dx
            src_row = oy + dy

            sx0 = src_col * TILE_PX
            sy0 = src_row * TILE_PX
            sx1 = sx0 + TILE_PX
            sy1 = sy0 + TILE_PX

            dx0 = cx * TILE_PX
            dy0 = cy * TILE_PX

            dest.tk.call(dest, "copy", atlas, "-from", sx0, sy0, sx1, sy1, "-to", dx0, dy0)

        return True

    def _render_flat_background_page(self, atlas: tk.PhotoImage, page_id: int) -> Optional[tk.PhotoImage]:
        """Render a flat background image for a page.

        Includes:
        - painted background (tiled using the skin pack Background.png if present)
        - background border overlay

        Also includes any *static* components (no hover/pressed variants).

        Excludes components that have variant states; those are exported as separate textures.
        Output is TILE_PX-per-cell (16x16 -> 256x256, 32x32 -> 512x512).
        """

        page = self.pages.get(page_id)
        if page is None:
            return None

        out_px = int(self.grid_n) * int(TILE_PX)
        if out_px <= 0:
            return None

        img = tk.PhotoImage(width=out_px, height=out_px)

        bg_cells = {(x, y) for y in range(self.grid_n) for x in range(self.grid_n) if page.background[y][x]}

        # Base background (tiled PNG) or a flat fill for painted cells.
        tile = self._get_background_tile_for_export()
        if tile is not None:
            src_w = tile.width()
            src_h = tile.height()
            if src_w > 0 and src_h > 0:
                for (x, y) in bg_cells:
                    x0 = x * TILE_PX
                    y0 = y * TILE_PX
                    sx = x0 % src_w
                    sy = y0 % src_h
                    self._copy_wrapped(img, tile, sx, sy, TILE_PX, TILE_PX, x0, y0)
        else:
            solid = tk.PhotoImage(width=TILE_PX, height=TILE_PX)
            solid.put("#2b2b2b", to=(0, 0, TILE_PX, TILE_PX))
            for (x, y) in bg_cells:
                dx0 = x * TILE_PX
                dy0 = y * TILE_PX
                img.tk.call(img, "copy", solid, "-from", 0, 0, TILE_PX, TILE_PX, "-to", dx0, dy0)

        # Background border overlay
        if bg_cells:
            self._blit_ctm_cellset_to_image(img, atlas, bg_cells, "background_border")

        # Fill where buttons should sit (so the baked background doesn't look empty).
        # IMPORTANT: render each button separately to avoid CTM connections between distinct buttons.
        button_tools = {Tool.BUTTON_STANDARD, Tool.BUTTON_TOGGLE}
        for ent in page.entries.values():
            if ent.tool not in button_tools:
                continue
            r = ent.rect.normalized()
            cell_set = set(r.cells())
            self._blit_ctm_cellset_to_image(img, atlas, cell_set, "button_background")

        # Bake any *static* components into the background.
        # IMPORTANT: render each entry separately to avoid CTM connections between distinct entries.
        for ent in page.entries.values():
            if ent.tool == Tool.BACKGROUND:
                continue
            if self._entry_requires_component_export(ent):
                continue

            modules = ENTRY_TOOL_MODULES.get(ent.tool) or {}
            base_module = modules.get("base")
            if not base_module:
                continue

            r = ent.rect.normalized()
            cell_set = set(r.cells())
            self._blit_ctm_cellset_to_image(img, atlas, cell_set, str(base_module))

        return img

    def _component_type_for_tool(self, tool: Tool) -> str:
        # Names intended for the CustomNPCs JS consumer.
        if tool == Tool.BUTTON_STANDARD:
            return "button"
        if tool == Tool.BUTTON_TOGGLE:
            return "toggle_button"
        if tool == Tool.TEXT_SLOT:
            return "label"
        if tool == Tool.TEXT_ENTRY:
            return "text_field"
        if tool == Tool.SELECT_LIST:
            return "scroll_list"
        if tool == Tool.ITEM_SLOT:
            return "item_slot"
        return str(tool.value)

    def _component_label_for_entry(self, ent: Entry) -> str:
        # Only some component types use this, but it's harmless to include.
        return str(getattr(ent, "label", "") or "")

    def _entry_requires_component_export(self, ent: Entry) -> bool:
        """Return True if this entry needs to be exported as a separate component texture.

        CustomNPCs only supports hover/pressed textures for buttons.
        All other entry types are baked into the page background export.
        """

        if ent.tool == Tool.BACKGROUND:
            return False

        # Only buttons are exported as separate textures.
        if ent.tool not in (Tool.BUTTON_STANDARD, Tool.BUTTON_TOGGLE):
            return False

        modules = ENTRY_TOOL_MODULES.get(ent.tool) or {}
        # Toggle buttons use 'unpressed' or 'base' as the base visual.
        base_module = modules.get("base") if ent.tool != Tool.BUTTON_TOGGLE else (modules.get("unpressed") or modules.get("base"))
        if not base_module:
            return False

        # Toggle buttons need a dedicated texture block even if the atlas variants
        # are identical, because the consumer expects OFF/ON/(optional) DISABLED textures.
        if ent.tool == Tool.BUTTON_TOGGLE:
            return True

        # Standard buttons: export if any non-base module is mapped.
        base = str(base_module)
        return any(
            (m is not None and str(m) != base)
            for m in (modules.get("hover"), modules.get("locked"))
        )

    def _compose_component_block(self, atlas: tk.PhotoImage, ent: Entry) -> Optional[tk.PhotoImage]:
        """Compose a 2x2 texture block for a component.

        Layout (each cell is the component's own w*h in pixels):
        - Standard button: base | locked
                         hover | locked_hover (usually same as locked)
                - Toggle button:   off        | on
                                                 hover_*     | disabled (locked)
                    Where hover_* resolves to hover_on or hover_off depending on current ent.active

        Missing variants fall back to base so the JS can use a consistent layout.
        """

        modules = ENTRY_TOOL_MODULES.get(ent.tool) or {}
        base_module = modules.get("base") if ent.tool != Tool.BUTTON_TOGGLE else (modules.get("unpressed") or modules.get("base"))
        if not base_module:
            return None

        if ent.tool == Tool.BUTTON_STANDARD:
            hover_module = modules.get("hover") or base_module
            locked_module = modules.get("locked") or base_module
            locked_hover_module = modules.get("locked_hover") or locked_module

            base_img = self._compose_entry_variant_image(atlas, ent, str(base_module))
            if base_img is None:
                return None
            hover_img = self._compose_entry_variant_image(atlas, ent, str(hover_module)) or base_img
            locked_img = self._compose_entry_variant_image(atlas, ent, str(locked_module)) or base_img
            locked_hover_img = self._compose_entry_variant_image(atlas, ent, str(locked_hover_module)) or locked_img

            w = int(base_img.width())
            h = int(base_img.height())
            out = tk.PhotoImage(width=w * 2, height=h * 2)
            out.tk.call(out, "copy", base_img, "-from", 0, 0, w, h, "-to", 0, 0)
            out.tk.call(out, "copy", hover_img, "-from", 0, 0, w, h, "-to", 0, h)
            out.tk.call(out, "copy", locked_img, "-from", 0, 0, w, h, "-to", w, 0)
            out.tk.call(out, "copy", locked_hover_img, "-from", 0, 0, w, h, "-to", w, h)
            return out

        if ent.tool == Tool.BUTTON_TOGGLE:
            # Keys: base (pressed/on), unpressed (off), hover_base, hover_unpressed, pressed_locked, unpressed_locked
            pressed_module = modules.get("base") or base_module
            unpressed_module = base_module  # Already set to unpressed or base above
            
            # Hover depends on active state
            hover_module = (modules.get("hover_base") if bool(ent.active) else modules.get("hover_unpressed")) or base_module

            meta = ent.meta if isinstance(ent.meta, dict) else {}
            if bool(meta.get("locked", False)):
                disabled_module = modules.get("pressed_locked") if bool(ent.active) else modules.get("unpressed_locked")
            else:
                # Fallback to any locked variant or pressed_module
                disabled_module = modules.get("pressed_locked") or modules.get("unpressed_locked") or pressed_module

            # Block layout: unpressed (TL), hover (BL), pressed (TR), disabled (BR)
            unpressed_img = self._compose_entry_variant_image(atlas, ent, str(unpressed_module))
            if unpressed_img is None:
                return None
            hover_img = self._compose_entry_variant_image(atlas, ent, str(hover_module)) or unpressed_img
            pressed_img = self._compose_entry_variant_image(atlas, ent, str(pressed_module)) or unpressed_img
            disabled_img = self._compose_entry_variant_image(atlas, ent, str(disabled_module)) or pressed_img

            w = int(unpressed_img.width())
            h = int(unpressed_img.height())
            out = tk.PhotoImage(width=w * 2, height=h * 2)
            out.tk.call(out, "copy", unpressed_img, "-from", 0, 0, w, h, "-to", 0, 0)
            out.tk.call(out, "copy", hover_img, "-from", 0, 0, w, h, "-to", 0, h)
            out.tk.call(out, "copy", pressed_img, "-from", 0, 0, w, h, "-to", w, 0)
            out.tk.call(out, "copy", disabled_img, "-from", 0, 0, w, h, "-to", w, h)
            return out

        # Shouldn't be reached: non-buttons are baked into backgrounds.
        base_img = self._compose_entry_variant_image(atlas, ent, str(base_module))
        if base_img is None:
            return None

        w = int(base_img.width())
        h = int(base_img.height())
        out = tk.PhotoImage(width=w * 2, height=h * 2)
        out.tk.call(out, "copy", base_img, "-from", 0, 0, w, h, "-to", 0, 0)
        out.tk.call(out, "copy", base_img, "-from", 0, 0, w, h, "-to", 0, h)
        out.tk.call(out, "copy", base_img, "-from", 0, 0, w, h, "-to", w, 0)
        out.tk.call(out, "copy", base_img, "-from", 0, 0, w, h, "-to", w, h)
        return out

    def _plan_component_sheet_layout(self, *, group_buttons_by_size: bool) -> Optional[Dict[str, Any]]:
        """Create a theme-independent packing plan for component texture blocks."""

        components: List[Dict[str, Any]] = []
        block_specs: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
        block_key_to_rep: Dict[Tuple[Any, ...], Tuple[int, int]] = {}

        page_ids = self._sorted_page_ids()

        def _resolved_open_page_for_button(*, pid: int, ent: Entry) -> Optional[int]:
            if ent.tool != Tool.BUTTON_STANDARD:
                return None

            meta = ent.meta if isinstance(ent.meta, dict) else {}
            page_change = meta.get("page_change")
            if not isinstance(page_change, dict):
                return None

            mode = str(page_change.get("mode", "none"))
            modulo = bool(page_change.get("modulo", False))
            if not page_ids or mode == "none":
                return None

            if mode == "close":
                return None

            if mode == "goto":
                try:
                    target_id = int(page_change.get("target_page_id", pid))
                except (TypeError, ValueError):
                    return None
                return int(target_id) if target_id in self.pages else None

            if pid not in page_ids:
                return None

            cur_idx = page_ids.index(pid)
            if mode == "next":
                new_idx = cur_idx + 1
            elif mode == "prev":
                new_idx = cur_idx - 1
            else:
                return None

            if modulo:
                new_idx %= len(page_ids)
            else:
                if new_idx < 0 or new_idx >= len(page_ids):
                    return None

            return int(page_ids[new_idx])

        def _close_gui_for_button(*, ent: Entry) -> bool:
            if ent.tool != Tool.BUTTON_STANDARD:
                return False
            meta = ent.meta if isinstance(ent.meta, dict) else {}
            page_change = meta.get("page_change")
            if not isinstance(page_change, dict):
                return False
            return str(page_change.get("mode", "none")) == "close"

        # Collect components from all pages.
        # NOTE: Only buttons are exported as separate component textures.
        # Other component types are baked into the page background PNG, but we still include
        # them in the manifest so consumers can lay out and reference them.
        for pid in page_ids:
            page = self.pages[pid]
            for ent in page.entries.values():
                if ent.tool == Tool.BACKGROUND:
                    continue

                include_in_manifest = ent.tool in (
                    Tool.BUTTON_STANDARD,
                    Tool.BUTTON_TOGGLE,
                    Tool.TEXT_SLOT,
                    Tool.TEXT_ENTRY,
                    Tool.SELECT_LIST,
                    Tool.ITEM_SLOT,
                )
                if not include_in_manifest:
                    continue

                needs_texture = self._entry_requires_component_export(ent)

                uid = int(getattr(ent, "uid", 0) or 0)
                if uid <= 0:
                    # Fallback if ever needed.
                    uid = int(ent.entry_id)

                r = ent.rect.normalized()
                w_tiles = int(r.width())
                h_tiles = int(r.height())
                w_px = w_tiles * int(TILE_PX)
                h_px = h_tiles * int(TILE_PX)
                if w_px <= 0 or h_px <= 0:
                    continue

                comp_type = self._component_type_for_tool(ent.tool)

                comp: Dict[str, Any] = {
                    "id": int(uid),
                    "type": str(comp_type),
                    "offset": {"page": int(pid), "x": int(r.x0), "y": int(r.y0)},
                    "size_tiles": {"w": int(w_tiles), "h": int(h_tiles)},
                    "label": self._component_label_for_entry(ent),
                }

                meta = ent.meta if isinstance(ent.meta, dict) else {}
                if bool(meta.get("locked", False)):
                    comp["locked"] = True

                if str(comp_type) == "scroll_list":
                    raw_items = str(ent.label or "")
                    items = [s.strip() for s in raw_items.split(",")]
                    items = [s for s in items if s]
                    comp["items"] = items
                    comp.pop("label", None)

                if self._hover_text_enabled_for(ent):
                    comp["hover_text"] = self._format_hover_tooltip_text(ent)

                open_page = _resolved_open_page_for_button(pid=int(pid), ent=ent)
                if open_page is not None:
                    comp["open_page"] = int(open_page)

                if _close_gui_for_button(ent=ent):
                    comp["close_gui"] = True

                if ent.tool == Tool.BUTTON_TOGGLE:
                    comp["toggled"] = bool(getattr(ent, "active", False))

                if needs_texture:
                    # Default: one unique texture block per component.
                    # For buttons, optionally reuse one texture per size.
                    if ent.tool in (Tool.BUTTON_STANDARD, Tool.BUTTON_TOGGLE) and group_buttons_by_size:
                        if ent.tool == Tool.BUTTON_TOGGLE:
                            # Toggle blocks may depend on locked/active (we may bake a locked-state variant).
                            block_key = (
                                "toggle",
                                w_tiles,
                                h_tiles,
                                bool(meta.get("locked", False)),
                                bool(getattr(ent, "active", False)),
                            )
                        else:
                            block_key = ("button", w_tiles, h_tiles)
                    else:
                        block_key = ("component", uid)

                    # Our exported block is always 2x2 variants (semantics depend on tool).
                    block_w_px = w_px * 2
                    block_h_px = h_px * 2

                    if block_key not in block_specs:
                        block_specs[block_key] = {
                            "block_key": block_key,
                            "w": int(block_w_px),
                            "h": int(block_h_px),
                            "w_tiles": int(w_tiles),
                            "h_tiles": int(h_tiles),
                        }
                        block_key_to_rep[block_key] = (int(pid), int(ent.entry_id))

                    comp["_block_key"] = block_key

                components.append(comp)

        if not components:
            return {
                "components": [],
                "sheets": [],
                "placement_index": {},
                "block_key_to_rep": {},
            }

        # Sort blocks deterministically (big-to-small, then by key).
        items = list(block_specs.values())
        items.sort(key=lambda it: (int(it["h"]) * int(it["w"]), int(it["h"]), int(it["w"]), repr(it["block_key"])), reverse=True)

        max_px = int(self.EXPORT_SHEET_PX)
        if max_px < int(TILE_PX):
            max_px = 512

        sheet_tiles = max(1, int(max_px) // int(TILE_PX))

        def _new_sheet() -> Dict[str, Any]:
            return {
                "w": int(max_px),
                "h": int(max_px),
                "placements": [],
                "occ": [[False for _ in range(sheet_tiles)] for _ in range(sheet_tiles)],
            }

        def _tiles_needed(px: int) -> int:
            return max(1, (int(px) + int(TILE_PX) - 1) // int(TILE_PX))

        def _can_place(occ: List[List[bool]], x0: int, y0: int, w_t: int, h_t: int) -> bool:
            if x0 < 0 or y0 < 0:
                return False
            if x0 + w_t > sheet_tiles or y0 + h_t > sheet_tiles:
                return False
            for yy in range(y0, y0 + h_t):
                row = occ[yy]
                for xx in range(x0, x0 + w_t):
                    if row[xx]:
                        return False
            return True

        def _mark(occ: List[List[bool]], x0: int, y0: int, w_t: int, h_t: int) -> None:
            for yy in range(y0, y0 + h_t):
                row = occ[yy]
                for xx in range(x0, x0 + w_t):
                    row[xx] = True

        def _place_in_sheet(sheet: Dict[str, Any], it: Dict[str, Any]) -> bool:
            w = int(it["w"])
            h = int(it["h"])
            w_t = _tiles_needed(w)
            h_t = _tiles_needed(h)
            occ = sheet["occ"]

            for y0 in range(0, sheet_tiles - h_t + 1):
                for x0 in range(0, sheet_tiles - w_t + 1):
                    if _can_place(occ, x0, y0, w_t, h_t):
                        _mark(occ, x0, y0, w_t, h_t)
                        sheet["placements"].append(
                            {"x": int(x0) * int(TILE_PX), "y": int(y0) * int(TILE_PX), "block_key": it["block_key"]}
                        )
                        return True
            return False

        sheets: List[Dict[str, Any]] = []
        for it in items:
            w = int(it["w"])
            h = int(it["h"])

            # Oversized blocks get their own dedicated sheet.
            if w > max_px or h > max_px:
                sheets.append({"w": w, "h": h, "placements": [{"x": 0, "y": 0, "block_key": it["block_key"]}]})
                continue

            placed = False
            for sh in sheets:
                if sh.get("occ") is None:
                    continue
                if _place_in_sheet(sh, it):
                    placed = True
                    break
            if not placed:
                sh = _new_sheet()
                _place_in_sheet(sh, it)
                sheets.append(sh)

        # Build placement index and strip occ.
        placement_index: Dict[Tuple[Any, ...], Dict[str, int]] = {}
        for sheet_idx, sh in enumerate(sheets):
            for pl in sh["placements"]:
                bk = pl["block_key"]
                spec = block_specs.get(bk)
                if not spec:
                    continue
                placement_index[bk] = {
                    "sheet": int(sheet_idx) + 1,  # 1-based for the JS consumer
                    "x": int(pl["x"]),
                    "y": int(pl["y"]),
                }

        for sh in sheets:
            if "occ" in sh:
                del sh["occ"]

        # Attach sheet/x/y to each component.
        for c in components:
            bk = c.get("_block_key")
            pos = placement_index.get(bk) if bk is not None else None
            if pos:
                c["sheet"] = int(pos["sheet"])
                c["tex"] = {"x": int(pos["x"]), "y": int(pos["y"])}

                # Toggle buttons additionally expose the ON texture origin and a disabled origin.
                # Our packed block is 2x2: unpressed (TL), hover (BL), pressed (TR), disabled (BR).
                if str(c.get("type")) == "toggle_button":
                    size = c.get("size_tiles") or {}
                    w_tiles = int(size.get("w") or 0)
                    h_tiles = int(size.get("h") or 0)
                    w_px = int(w_tiles) * int(TILE_PX)
                    h_px = int(h_tiles) * int(TILE_PX)
                    # Always emit these keys, fall back to base tex if sizes are missing.
                    if w_px > 0 and h_px > 0:
                        c["toggle_tex"] = {"x": int(pos["x"]) + w_px, "y": int(pos["y"])}
                        c["disabled_tex"] = {"x": int(pos["x"]) + w_px, "y": int(pos["y"]) + h_px}
                        c["toggle_disabled_tex"] = dict(c["disabled_tex"])  # alias for consumers expecting this name
                    else:
                        c["toggle_tex"] = dict(c["tex"])
                        c["disabled_tex"] = dict(c["tex"])
                        c["toggle_disabled_tex"] = dict(c["tex"])

                # Standard buttons expose a disabled_tex pointing to the "locked" quadrant.
                # (Our block is 2x2: base TL, hover BL, locked TR, locked_hover BR)
                if str(c.get("type")) == "button":
                    size = c.get("size_tiles") or {}
                    w_tiles = int(size.get("w") or 0)
                    h_tiles = int(size.get("h") or 0)
                    w_px = int(w_tiles) * int(TILE_PX)
                    if w_px > 0:
                        c["disabled_tex"] = {"x": int(pos["x"]) + w_px, "y": int(pos["y"])}
                    else:
                        # Fallback: use base tex if we cannot compute offset.
                        c["disabled_tex"] = dict(c["tex"])
            if "_block_key" in c:
                del c["_block_key"]

        # Stable component ordering.
        components.sort(key=lambda c: (int(c.get("offset", {}).get("page", 0)), int(c.get("id", 0))))

        return {
            "components": components,
            "sheets": sheets,
            "placement_index": placement_index,
            "block_key_to_rep": block_key_to_rep,
        }

    def _export_component_sheets_for_theme(self, theme_out_dir: str, plan: Dict[str, Any], *, quiet: bool = False) -> bool:
        os.makedirs(theme_out_dir, exist_ok=True)

        def _write(rel: str, img: tk.PhotoImage) -> None:
            out_path = os.path.join(theme_out_dir, os.path.basename(rel))
            img.write(out_path, format="png")

        return self._export_component_sheets_with_writer(
            plan,
            theme_rel_root=".",
            write_png=_write,
            quiet=quiet,
        )

    def inject_into_texture_pack(self) -> None:
        """Export PNGs into a Minecraft resource pack (folder or zip).

        PNGs go under: assets/minecraft/textures/gui/gui_creator/<gui_name>/<skin_pack>/...
        The GUI manifest JSON is saved separately to a user-chosen folder.
        """

        if self._skin_pack_name == "(none)":
            messagebox.showerror(
                "Inject into Texture Pack",
                "No skin pack selected.\n\nSelect a Skin Pack in the left panel first.",
            )
            return

        group_by_size = bool(self.group_buttons_by_size_var.get())

        extra_packs = self._resolved_extra_skin_packs()
        packs_to_inject = [str(self._skin_pack_name)] + [p for p in extra_packs if p != str(self._skin_pack_name)]
        pack_folders_to_inject = [self._skin_pack_folder_name(p) for p in packs_to_inject]

        kind = str(self.inject_pack_kind_var.get() or "folder").strip().lower()
        pack_path = str(self.inject_pack_path_var.get() or "").strip()
        if not pack_path:
            messagebox.showerror("Inject into Texture Pack", "No resource pack path configured.\n\nSet it in the left panel (Export / Inject).")
            return

        if kind == "zip":
            if not os.path.isfile(pack_path):
                messagebox.showerror("Inject into Texture Pack", f"Zip does not exist:\n{pack_path}")
                return
        else:
            if not os.path.isdir(pack_path):
                messagebox.showerror("Inject into Texture Pack", f"Folder does not exist:\n{pack_path}")
                return

        plan = self._plan_component_sheet_layout(group_buttons_by_size=bool(group_by_size))
        if plan is None:
            return

        gui_folder = self._safe_gui_folder_for_pack()
        base_prefix = f"assets/minecraft/textures/gui/gui_creator/{gui_folder}".replace("\\", "/")
        previous_pack = self._skin_pack_name

        # Writer that targets a folder pack.
        if kind != "zip":
            gui_dir = os.path.join(pack_path, *base_prefix.split("/"))
            if not self._reset_export_dir(gui_dir, quiet=True):
                messagebox.showerror("Inject into Texture Pack", f"Failed preparing pack folder:\n{gui_dir}")
                return

            # Ensure base gui dir exists.
            os.makedirs(gui_dir, exist_ok=True)

            def _write_png(rel: str, img: tk.PhotoImage) -> None:
                out_path = os.path.join(pack_path, *rel.split("/"))
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                img.write(out_path, format="png")

            try:
                for pack_name in packs_to_inject:
                    self._on_skin_pack_changed(pack_name)
                    theme_prefix = f"{base_prefix}/{self._skin_pack_folder_name(pack_name)}".replace("\\", "/")
                    if not self._export_component_sheets_with_writer(
                        plan,
                        theme_rel_root=theme_prefix,
                        write_png=_write_png,
                        quiet=False,
                    ):
                        return
            finally:
                self._on_skin_pack_changed(previous_pack)
        else:
            # Zip pack: remove existing gui folder prefix then append new files.
            if not self._zip_remove_prefix(pack_path, base_prefix):
                messagebox.showerror("Inject into Texture Pack", f"Failed cleaning existing paths in zip:\n{base_prefix}")
                return

            with tempfile.TemporaryDirectory(prefix="gui_builder_zip_") as tmpdir:
                def _write_png(rel: str, img: tk.PhotoImage) -> None:
                    rel = rel.replace("\\", "/")
                    tmp_path = os.path.join(tmpdir, *rel.split("/"))
                    os.makedirs(os.path.dirname(tmp_path), exist_ok=True)
                    img.write(tmp_path, format="png")

                try:
                    for pack_name in packs_to_inject:
                        self._on_skin_pack_changed(pack_name)
                        theme_prefix = f"{base_prefix}/{self._skin_pack_folder_name(pack_name)}".replace("\\", "/")
                        if not self._export_component_sheets_with_writer(
                            plan,
                            theme_rel_root=theme_prefix,
                            write_png=_write_png,
                            quiet=False,
                        ):
                            return
                finally:
                    self._on_skin_pack_changed(previous_pack)

                try:
                    with zipfile.ZipFile(pack_path, "a", compression=zipfile.ZIP_DEFLATED) as zf:
                        for root, _, files in os.walk(tmpdir):
                            for fn in files:
                                abs_path = os.path.join(root, fn)
                                rel_path = os.path.relpath(abs_path, tmpdir).replace("\\", "/")
                                zf.write(abs_path, arcname=rel_path)
                except Exception as e:
                    messagebox.showerror("Inject into Texture Pack", f"Failed writing to zip:\n{e}")
                    return

        # Manifest JSON saved separately.
        base_out_dir = str(self.manifest_output_dir_var.get() or "").strip()
        if not base_out_dir:
            messagebox.showerror(
                "Inject into Texture Pack",
                "No manifest output folder configured.\n\nSet it in the left panel (Export / Inject).",
            )
            return

        try:
            os.makedirs(base_out_dir, exist_ok=True)
        except Exception as e:
            messagebox.showerror("Inject into Texture Pack", f"Failed creating manifest folder:\n{base_out_dir}\n\n{e}")
            return

        gui_root = os.path.join(base_out_dir, self._safe_gui_name(self.gui_name_var.get()))
        if not self._reset_export_dir(gui_root, quiet=False):
            return

        gui_manifest: Dict[str, Any] = {
            "version": 3,
            "gui_name": gui_folder,
            "size": int(self.grid_n),
            "skin_packs": pack_folders_to_inject,
            "pages": self._manifest_pages_payload(plan.get("components") or []),
        }
        if not self._write_gui_manifest(gui_root, gui_manifest):
            return
        
        if not self._write_gui_buttons_script(gui_root, gui_manifest, gui_folder):
            return

        messagebox.showinfo(
            "Inject into Texture Pack",
            "Injection complete.\n\n"
            + f"Pack: {pack_path}\n"
            + f"Injected under: {base_prefix}/...\n"
            + f"Skin packs: {', '.join(pack_folders_to_inject)}\n\n"
            + f"Manifest saved to: {os.path.join(gui_root, 'gui_manifest.json')}",
        )

    def export_textures(self) -> None:
        if self._skin_pack_name == "(none)":
            messagebox.showerror(
                "Export textures",
                "No skin pack selected.\n\nSelect a Skin Pack in the left panel first.",
            )
            return

        group_by_size = bool(self.group_buttons_by_size_var.get())

        extra_packs = self._resolved_extra_skin_packs()
        packs_to_export = [str(self._skin_pack_name)] + [p for p in extra_packs if p != str(self._skin_pack_name)]
        pack_folders_to_export = [self._skin_pack_folder_name(p) for p in packs_to_export]

        base_out_dir = str(self.export_base_dir_var.get() or "").strip()
        if not base_out_dir:
            messagebox.showerror("Export textures", "No export base folder configured.\n\nSet it in the left panel (Export / Inject).")
            return

        try:
            os.makedirs(base_out_dir, exist_ok=True)
        except Exception as e:
            messagebox.showerror("Export textures", f"Failed creating export base folder:\n{base_out_dir}\n\n{e}")
            return

        gui_safe_name = self._safe_gui_name(self.gui_name_var.get())
        gui_root = os.path.join(base_out_dir, gui_safe_name)
        if not self._reset_export_dir(gui_root, quiet=False):
            return


        plan = self._plan_component_sheet_layout(group_buttons_by_size=bool(group_by_size))
        if plan is None:
            return

        previous_pack = self._skin_pack_name
        failed: List[str] = []

        try:
            for pack_name in packs_to_export:
                self._on_skin_pack_changed(pack_name)
                # Keep exports grouped by skin pack name.
                skin_dir = os.path.join(gui_root, self._skin_pack_folder_name(pack_name))
                os.makedirs(skin_dir, exist_ok=True)
                if not self._export_component_sheets_for_theme(skin_dir, plan, quiet=False):
                    failed.append(pack_name)
        finally:
            self._on_skin_pack_changed(previous_pack)

        if failed:
            messagebox.showwarning(
                "Export textures",
                "Export completed with errors.\n\nFailed skin packs:\n" + "\n".join(failed),
            )
            return

        # Minimal, theme-independent manifest.
        gui_manifest: Dict[str, Any] = {
            "version": 3,
            "gui_name": gui_safe_name,
            "size": int(self.grid_n),
            "skin_packs": pack_folders_to_export,
            "pages": self._manifest_pages_payload(plan.get("components") or []),
        }

        if not self._write_gui_manifest(gui_root, gui_manifest):
            return
        
        if not self._write_gui_buttons_script(gui_root, gui_manifest, gui_safe_name):
            return

        self.set_status("Exported textures + gui_manifest.json + gui_buttons.js")
        messagebox.showinfo(
            "Export textures",
            "Export complete.\n\nOutputs:\n"
            + "- <skin_pack>/sheet_*.png\n"
            + "- <skin_pack>/background_page_*.png\n"
            + "- gui_manifest.json",
        )

    def _ask_export_button_grouping(self) -> Optional[bool]:
        """Ask how button textures should be exported.

        Returns:
            True  -> group by size (current behavior: reuse one texture per size)
            False -> export each button independently
            None  -> user cancelled
        """

        return messagebox.askyesnocancel(
            "Export textures",
            "Button export mode:\n\n"
            "Yes: Reuse one texture per button size (current)\n"
            "No: Export every button independently\n\n"
            "(This only affects exported button textures; backgrounds are unchanged.)",
            default="yes",
        )

    def export_all_skin_packs(self) -> None:
        # Refresh list first so we export what's actually on disk.
        self._scan_skin_packs()

        group_by_size = bool(self.group_buttons_by_size_var.get())

        packs = sorted(self._skin_pack_paths.keys())
        pack_folders = [self._skin_pack_folder_name(p) for p in packs]
        if not packs:
            messagebox.showinfo(
                "Export all skin packs",
                "No skin packs found. Create skin_packs/<name>/Modules.png first.",
            )
            return

        base_out_dir = str(self.export_base_dir_var.get() or "").strip()
        if not base_out_dir:
            messagebox.showerror(
                "Export all skin packs",
                "No export base folder configured.\n\nSet it in the left panel (Export / Inject).",
            )
            return

        try:
            os.makedirs(base_out_dir, exist_ok=True)
        except Exception as e:
            messagebox.showerror("Export all skin packs", f"Failed creating export base folder:\n{base_out_dir}\n\n{e}")
            return

        gui_safe_name = self._safe_gui_name(self.gui_name_var.get())
        gui_root = os.path.join(base_out_dir, gui_safe_name)
        if not self._reset_export_dir(gui_root, quiet=True):
            messagebox.showerror(
                "Export all skin packs",
                f"Failed clearing export folder:\n{gui_root}",
            )
            return

        plan = self._plan_component_sheet_layout(group_buttons_by_size=bool(group_by_size))
        if plan is None:
            return

        # Minimal, theme-independent manifest.
        gui_manifest: Dict[str, Any] = {
            "version": 3,
            "gui_name": gui_safe_name,
            "size": int(self.grid_n),
            "skin_packs": pack_folders,
            "pages": self._manifest_pages_payload(plan.get("components") or []),
        }

        if not self._write_gui_manifest(gui_root, gui_manifest, quiet=True):
            messagebox.showerror(
                "Export all skin packs",
                "Failed writing gui_manifest.json.",
            )
            return
        
        if not self._write_gui_buttons_script(gui_root, gui_manifest, gui_safe_name, quiet=True):
            messagebox.showerror(
                "Export all skin packs",
                f"Failed writing gui_{gui_safe_name}.js.",
            )
            return

        previous = self._skin_pack_name
        ok = 0
        failed: List[str] = []

        try:
            for name in packs:
                self._on_skin_pack_changed(name)
                out_dir = os.path.join(gui_root, self._skin_pack_folder_name(name))
                os.makedirs(out_dir, exist_ok=True)

                if not self._export_component_sheets_for_theme(out_dir, plan, quiet=True):
                    failed.append(name)
                    continue
                ok += 1
        finally:
            self._on_skin_pack_changed(previous)

        if failed:
            messagebox.showwarning(
                "Export all skin packs",
                f"Exported {ok}/{len(packs)} skin packs. Failed: {', '.join(failed)}\n\nWrote: {os.path.join(gui_root, 'gui_manifest.json')}",
            )
        else:
            messagebox.showinfo(
                "Export all skin packs",
                f"Exported {ok} skin packs into:\n{gui_root}\n\nWrote: {os.path.join(gui_root, 'gui_manifest.json')}",
            )

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
                self.canvas.create_image(self._canvas_offset_x, self._canvas_offset_y, anchor="nw", image=bg_img)
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
        x0 = self._canvas_offset_x + (r.x0 * self.cell_px)
        y0 = self._canvas_offset_y + (r.y0 * self.cell_px)
        x1 = self._canvas_offset_x + ((r.x1 + 1) * self.cell_px)
        y1 = self._canvas_offset_y + ((r.y1 + 1) * self.cell_px)
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
        x0 = self._canvas_offset_x + (cx * self.cell_px)
        y0 = self._canvas_offset_y + (cy * self.cell_px)
        x1 = x0 + self.cell_px
        y1 = y0 + self.cell_px
        self.canvas.create_rectangle(x0, y0, x1, y1, fill=color, outline="")

    def _draw_rect_outline(self, rect: Rect, color: str) -> None:
        r = rect.normalized()
        x0 = self._canvas_offset_x + (r.x0 * self.cell_px)
        y0 = self._canvas_offset_y + (r.y0 * self.cell_px)
        x1 = self._canvas_offset_x + ((r.x1 + 1) * self.cell_px)
        y1 = self._canvas_offset_y + ((r.y1 + 1) * self.cell_px)
        self.canvas.create_rectangle(x0, y0, x1, y1, outline=color, width=2)

    def _draw_entry(self, ent: Entry) -> None:
        # Preview: textured rendering using the selected skin pack modules (if available)
        if self.preview_mode and self._texture_sheet is not None:
            if self._draw_entry_textured(ent):
                # Keep debug labels in preview (can be removed later if you want a clean look)
                r = ent.rect.normalized()
                x0 = self._canvas_offset_x + (r.x0 * self.cell_px)
                y0 = self._canvas_offset_y + (r.y0 * self.cell_px)
                x1 = self._canvas_offset_x + ((r.x1 + 1) * self.cell_px)
                y1 = self._canvas_offset_y + ((r.y1 + 1) * self.cell_px)

                # Text slots: wrap within the available rectangle instead of truncating.
                if ent.tool == Tool.TEXT_SLOT and ent.label:
                    wrap_w = max(1, int((x1 - x0) - 8))
                    self.canvas.create_text(
                        (x0 + x1) / 2,
                        (y0 + y1) / 2,
                        text=str(ent.label),
                        fill="#ffffff",
                        font=("TkDefaultFont", max(6, self.cell_px // 4)),
                        justify="center",
                        width=wrap_w,
                    )
                    return

                label_lines: List[str] = []
                if ent.label and ent.tool in (
                    Tool.TEXT_ENTRY,
                    Tool.SELECT_LIST,
                    Tool.BUTTON_STANDARD,
                    Tool.BUTTON_TOGGLE,
                ):
                    label_lines.append(ent.label[:24])

                if ent.tool == Tool.BUTTON_TOGGLE:
                    # Keep state feedback for toggles.
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
            Tool.BUTTON_TOGGLE: "#b05cff",
            Tool.TEXT_ENTRY: "#d57b3a",
            Tool.SELECT_LIST: "#d5c63a",
            Tool.TEXT_SLOT: "#aaaaaa",
            Tool.ITEM_SLOT: "#d53a3a",
        }
        fill = colors.get(ent.tool, "#666666")

        r = ent.rect.normalized()
        x0 = self._canvas_offset_x + (r.x0 * self.cell_px)
        y0 = self._canvas_offset_y + (r.y0 * self.cell_px)
        x1 = self._canvas_offset_x + ((r.x1 + 1) * self.cell_px)
        y1 = self._canvas_offset_y + ((r.y1 + 1) * self.cell_px)

        outline = "#111111"
        width = 1
        if ent.active:
            outline = "#ffffff"
            width = 3

        self.canvas.create_rectangle(x0, y0, x1, y1, fill=fill, outline=outline, width=width)

        # Show tool name; also show label for text/select if any
        if ent.tool == Tool.TEXT_SLOT and ent.label:
            # Wrap label text inside the slot.
            text = str(ent.label)
            wrap_w = max(1, int((x1 - x0) - 8))
            self.canvas.create_text(
                (x0 + x1) / 2,
                (y0 + y1) / 2,
                text=text,
                fill="#000000",
                font=("TkDefaultFont", max(6, self.cell_px // 4)),
                justify="center",
                width=wrap_w,
            )
            return

        if ent.tool in (Tool.BUTTON_STANDARD, Tool.BUTTON_TOGGLE) and ent.label:
            label_lines = [ent.label[:24]]
        else:
            label_lines = [ent.tool.value.replace("_", "\n")]

        if ent.tool in (Tool.TEXT_ENTRY, Tool.SELECT_LIST, Tool.TEXT_SLOT) and ent.label:
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
            x = self._canvas_offset_x + p
            y = self._canvas_offset_y + p
            self.canvas.create_line(x, self._canvas_offset_y, x, self._canvas_offset_y + self.canvas_px, fill="#2a2a2a")
            self.canvas.create_line(self._canvas_offset_x, y, self._canvas_offset_x + self.canvas_px, y, fill="#2a2a2a")

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
