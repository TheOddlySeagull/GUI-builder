# GUI-builder

A small Tkinter-based GUI layout editor + previewer.

You paint a background, place interactive elements (buttons, text slots, text entry, select list, item slots) on a 16×16 or 32×32 grid, and export/import the layout as JSON.

Preview mode can render elements using a connected-texture tilesheet (`GUI_CTM.png`) instead of flat colors.

## Features

- Grid editor (16×16 or 32×32)
- Multi-page GUI (switch pages and preview page navigation)
- Tools: background, standard button, press button, toggle button, text entry, select list, text slot, item slot
- JSON save/load (versioned format)
- Preview mode:
  - Interactions (toggle/press/standard buttons, text entry popup, select list popup)
  - Hover tooltips (per-element)
  - Optional textured rendering via `GUI_CTM.png` (CTM)

## Requirements

- Windows / macOS / Linux
- Python 3.10+ recommended
- Tkinter (usually included with standard Python installs on Windows)

No external dependencies.

## Run

From the repo root:

```bash
py gui_builder.py
```

## Texture sheet (GUI_CTM.png)

If `GUI_CTM.png` is present next to `gui_builder.py`, preview mode will try to use it.
If it’s missing or fails to load, the app falls back to solid-color rendering.

### Where to fix the atlas mapping

All tile mapping is centralized here:

- [gui_builder_app/texture_mapping.py](gui_builder_app/texture_mapping.py)

Things you will typically tweak:

1) **Block origins** (where each 4×4 CTM block starts in the atlas)

Edit `CTM_ORIGINS` in [gui_builder_app/texture_mapping.py](gui_builder_app/texture_mapping.py).
The values are **tile coordinates** (16×16 tiles), not pixels.

2) **CTM variant ordering** (how the 4-neighbor mask maps to the 16 tiles)

Edit `ctm_tile_offset(mask)` in [gui_builder_app/texture_mapping.py](gui_builder_app/texture_mapping.py).

By default it is row-major:

```py
return (mask % 4, mask // 4)
```

If your sheet uses a different layout (common for CTM sheets), replace this function with your ordering.

3) **Neighbor bit convention** (which bit means N/E/S/W)

Edit `CTM_DIRS` in [gui_builder_app/texture_mapping.py](gui_builder_app/texture_mapping.py).

## JSON export/import

- Use **File → Save JSON…** and **File → Load JSON…**
- Current format is `version: 3` with:
  - `grid_n` (16 or 32)
  - `start_page_id`
  - `pages[]` with `background_rects[]` and `entries[]`

An example export lives in:

- [gui_builder_app/exports/placeholder_0.json](gui_builder_app/exports/placeholder_0.json)

## Project layout

- [gui_builder.py](gui_builder.py): thin entrypoint
- [gui_builder_app/app.py](gui_builder_app/app.py): main Tkinter app (`GuiBuilderApp`)
- [gui_builder_app/models.py](gui_builder_app/models.py): enums + dataclasses
- [gui_builder_app/texture.py](gui_builder_app/texture.py): tilesheet loader/slicer/cache
- [gui_builder_app/texture_mapping.py](gui_builder_app/texture_mapping.py): atlas mapping (edit this to fix CTM)

## Notes

- Switching between 16×16 and 32×32 currently clears the layout (MVP behavior).
- The textured preview assumes a 16×16 tile atlas and scales tiles to the current cell size.
