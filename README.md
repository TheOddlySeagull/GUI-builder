# GUI-builder

A small Tkinter-based GUI layout editor + previewer.

You paint a background, place interactive elements (buttons, text slots, text entry, select list, item slots) on a 16×16 or 32×32 grid, and export/import the layout as JSON.

Preview mode can render elements using a connected-texture tilesheet (a skin pack `Modules.png`) instead of flat colors.

## Features

- Grid editor (16×16 or 32×32)
- Multi-page GUI (switch pages and preview page navigation)
- Tools: background, standard button, press button, toggle button, text entry, select list, text slot, item slot
- JSON save/load (versioned format)
- Preview mode:
  - Interactions (toggle/press/standard buttons, text entry popup, select list popup)
  - Hover tooltips (per-element)
  - Optional textured rendering via skin packs (CTM)

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

## Skin packs (Modules.png + Background.png)

The app supports multiple GUI “skins” via **skin packs**.

Create this folder next to `gui_builder.py`:

- `skin_packs/`

Inside it, create one folder per skin name:

- `skin_packs/<skin_name>/Modules.png`
- `skin_packs/<skin_name>/Background.png` (optional; tiled under painted background cells)

In the GUI, use the **Skin Pack** dropdown to select from detected packs.
If no pack is selected (or Modules.png fails to load), preview falls back to solid colors.

### Where to fix the atlas mapping

All tile mapping is centralized here:

- [gui_builder_app/texture_mapping.py](gui_builder_app/texture_mapping.py)

Things you will typically tweak:

1) **Block origins** (where each 4×4 CTM block starts in the atlas)

Edit `CTM_ORIGINS` in [gui_builder_app/texture_mapping.py](gui_builder_app/texture_mapping.py).
The values are **tile coordinates** (16×16 tiles), not pixels.

Notable keys:
- `text_entry_border` / `text_entry_border_hover`: used for `text_entry`
- `select_list_border` / `select_list_border_hover`: used for `select_list`
- `item_slot` / `item_slot_hover`: used for `item_slot`

Compatibility:
- `input_border` / `input_border_hover` are still present as legacy aliases, but the default tool mapping now uses the new per-tool keys above.

Tool → module mapping:
- `ENTRY_TOOL_MODULES` in [gui_builder_app/texture_mapping.py](gui_builder_app/texture_mapping.py) controls which module keys each tool uses (base/hover/pressed/etc).

2) **CTM variant ordering** (how the 4-neighbor mask maps to the 16 tiles)

Edit `ctm_tile_offset(mask)` in [gui_builder_app/texture_mapping.py](gui_builder_app/texture_mapping.py).

The current implementation matches this 4×4 per-module layout:

- (0,0): single tile
- Row 0 (y=0, x=1..3): horizontal strip (left/mid/right)
- Col 0 (x=0, y=1..3): vertical strip (top/mid/bottom)
- 3×3 block (x=1..3, y=1..3): nine-slice corners/edges/center

If your sheet differs, adjust `ctm_tile_offset(mask)`.

3) **Neighbor bit convention** (which bit means N/E/S/W)

Edit `CTM_DIRS` in [gui_builder_app/texture_mapping.py](gui_builder_app/texture_mapping.py).

## Background textures (preview)

Background tiling comes from the selected skin pack’s `Background.png`.
The background borders come from the `background_border` module in the atlas and are expected to be partially transparent so the tiled background can show through.

## JSON export/import

- Use **File → Save JSON…** and **File → Load JSON…**
- Current format is `version: 3` with:
  - `gui_name` (the GUI Name field)
  - `grid_n` (16 or 32)
  - `start_page_id`
  - `available_in_skin_packs` (names detected at export time)
  - `pages[]` with `background_rects[]` and `entries[]`

When using **File → Save JSON…**, the default filename is `<gui_name>.json`.

An example export lives in:

- [gui_builder_app/exports/placeholder_0.json](gui_builder_app/exports/placeholder_0.json)

## Export textures (for CustomNPCs)

CustomNPCs expects button textures as single images (not multi-tile CTM rendering).

Use:

- **File → Export Textures…**

Export options are configured in the left panel under **Export / Inject** (no export-time popups):

- Export base folder
- Group buttons by size
- Additional skin packs

Set **GUI Name** in the left panel before exporting.
Exports will be written under:

- `/<export base folder>/<gui_name>/<skin_pack_name>/...`

Note: exported skin pack folder names are normalized (lowercase, spaces replaced with `_`).

If the `/<export base folder>/<gui_name>/` folder already exists, its contents are cleared and replaced by the new export.

Manifests are grouped into one file:

- `/<export base folder>/<gui_name>/gui_manifest.json`

The manifest includes a `skin_packs` list that tells you which skin pack folders were exported (lowercase, no spaces).

It also includes:

- `gui_name`: the normalized GUI folder name used for export
- `size`: `16` or `32` (grid size)

And the component data is grouped per page:

- `pages`: list of `{ "page": <page_id>, "components": [...] }`

When exporting, you can choose:

- **Reuse one texture per button size** (default/current behavior)
- **Export every button independently** (one unique texture per button entry)

To export the same GUI textures for every detected skin pack:

- **File → Export All Skin Packs…**

### Inject into a Minecraft resource pack

Use:

- **File → Inject into Texture Pack…**

Injection options are configured in the left panel under **Export / Inject** (no inject-time popups):

- Pack type (folder/zip) + path
- Manifest output folder
- Group buttons by size
- Additional skin packs

This lets you select either:

- a resource pack **folder**, or
- a resource pack **.zip**

The exporter writes PNGs into the pack under:

- `assets/minecraft/textures/gui/gui_creator/<gui_name>/<skin_pack_name>/...`

Note: injected skin pack folder names are normalized (lowercase, spaces replaced with `_`).

Where `<gui_name>` is lowercased and spaces are replaced with `_`.

Then it prompts you for a separate folder where `gui_manifest.json` will be saved.

This export produces two outputs:

1) **Buttons** (assembled): exported as fully assembled images (base/hover/pressed/pressed_hover when available), packed into one or more PNG sheets and referenced by `gui_manifest.json`.

- `<skin_pack_name>/buttons_sheet_0.png`, `<skin_pack_name>/buttons_sheet_1.png`, ...
- (referenced from `gui_manifest.json`)

Hover layout rule:

- Hover is **directly beneath** the base texture in the packed output.
- Pressed hover is **directly beneath** the pressed texture.

2) **Flat backgrounds** (per page): exports a pixel-identical background image per page at the editor tile scale, merging:

- painted background area (tiled using the selected skin pack `Background.png` if present)
- background border overlay
- button placeholders filled using the `button_background` CTM module
- all non-button components (text entry/select list/text slot/item slot)

Buttons are excluded from the background export (they remain separate assembled textures).

Outputs:

- `<skin_pack_name>/background_page_<page_id>.png` (one PNG per page)
- (referenced from `gui_manifest.json`)

By default, export sheets are packed as 512×512.

## Project layout

- [gui_builder.py](gui_builder.py): thin entrypoint
- [gui_builder_app/app.py](gui_builder_app/app.py): main Tkinter app (`GuiBuilderApp`)
- [gui_builder_app/models.py](gui_builder_app/models.py): enums + dataclasses
- [gui_builder_app/texture.py](gui_builder_app/texture.py): tilesheet loader/slicer/cache
- [gui_builder_app/texture_mapping.py](gui_builder_app/texture_mapping.py): atlas mapping (edit this to fix CTM)

## Notes

- Switching between 16×16 and 32×32 currently clears the layout (MVP behavior).
- The textured preview assumes a 16×16 tile atlas (skin pack `Modules.png`) and scales tiles to the current cell size.
