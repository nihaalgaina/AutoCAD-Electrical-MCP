# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [0.5.0] — MCC Nameplate Fixes (August 10, 2026)

### Added
- `MCC_BLOCK_LIBRARY` environment variable — set your block folder in `.env`
  instead of editing source files. Supports semicolon-separated multiple paths.
- `.env.example` now includes `MCC_BLOCK_LIBRARY` with clear instructions.
- `mcc_block_catalog.yaml` `library_paths` entry now uses `${MCC_BLOCK_LIBRARY}`
  so the catalog is portable across machines out of the box.
- `_friendlyError()` helper in `mcc.js` — AutoCAD-not-running and
  block-file-not-found errors now display actionable guidance instead of raw
  Python exception text.
- New Project dialog now lists all three required drawings
  (`MCC_LAYOUT.dwg`, `MCC_UNITDATA.dwg`, `MCC_NAMEPLATE.dwg`).

### Fixed 
- **LAMACOID LINE.1–LINE.4 fields not writing** — `add_unit` stored
  `nameplate_fields` with keys `"LINE1"/"LINE2"/"LINE3"/"LINE4"` (no dots),
  so the lookup against `"LINE.1"` etc. always returned `None`.
  Corrected to `"LINE.1"/"LINE.2"/"LINE.3"/"LINE.4"` throughout.
- **LINE.2 not swapping during move/reorder** — `_sync_nameplate_reorder` and
  `edit_unit` both had a `"LINE2."` typo (dot after the digit). Fixed to
  `"LINE.2"`.
- **`_set_attrs` now uses two-pass strategy** — pass 1 matches attributes by
  normalised tag name (strips dots so `"LINE.1"` and `"LINE1"` both resolve);
  pass 2 falls back to positional index for any remaining fields. This fixes
  QTY/STYLE/SIZE not being written when `GetAttributes()` returns fewer than
  8 elements.
- **CUSTOM unit TYPE field** — `add_unit` wrote `"CUSTOM"` instead of the
  user-supplied tag value (e.g. `"PLC"`) for CUSTOM-type units.
- **Tag not showing in MCC configurator** — `get_project_state` omitted
  `tag`, `starter_type`, `custom_width`, dual-feeder fields, and
  `nameplate_fields` from unit output. All fields now included.
- **Wireway drawn through CUSTOM units** — both wireway code paths used
  `proj["first_unit_y"]` as the start Y, ignoring non-wireway units above.
  Fixed to use the current unit's actual Y position.
- **`edit_unit` `tag` variable shadowing** — the `for tag, val in named:`
  loop overwrote the `tag` function parameter, causing the CUSTOM TYPE fix
  to use an empty string. Resolved by capturing `_eff_tag` before the loop.

---

## [0.4.0] — MCC_NAMEPLATE support (August 10, 2026)

### Added
- **`src/tools/mcc_nameplate.py`** — new module managing the `MCC_NAMEPLATE.dwg`
  document. Inserts, updates, and reorders `LAMACOID` block rows with
  index-based attribute setting to work around AutoCAD's dot-encoding of
  `LINE.1`–`LINE.4` tag names.
- **Nameplate fields in MCC configurator** — Details tab now includes a
  *Nameplate* section: Line 1–4, NP Qty, NP Size, NP Style.
- **`new_mcc_project`** accepts `nameplate_row_x` / `nameplate_row_y`
  parameters and opens `MCC_NAMEPLATE.dwg` alongside the other two documents.
- **`_sync_nameplate_reorder`** — mirrors `_sync_unitdata_reorder`; rewrites
  LAMACOID attribute data in-place so physical rows always reflect the current
  logical unit ordering after any move or delete.
- **`save_project` / `load_project`** now persist all nameplate state:
  `nameplate_cursor_y`, `nameplate_handle_order`, etc.
- **`get_project_state`** returns `nameplate_handle`, `nameplate_fields`,
  `tag`, `starter_type`, `custom_width`, and dual-feeder sub-unit fields
  in every unit record.

---

## [0.3.0] — MCC Builder GUI (August 6, 2027)

### Added
- **Web-based MCC configurator** (`web/frontend/js/mcc.js`) — visual
  drag-and-drop panel for building MCC layouts without AI.
- Sections rendered as columns; units as coloured cards sized by mod height.
- Drag-and-drop reordering via `move_unit` backend call.
- **Add Unit form** — full UDATALIN detail fields (General, Motor, Protection,
  Contactor/OL, Control Circuit, Pushbuttons, Pilot Lights, Timers, Control
  Relays, Metering, Transformer, Panel Board, Drawing).
- **Bulk Add** — insert multiple identical units at once.
- **DUAL_FEEDER** type with separate L/R amperage inputs.
- **CUSTOM** type with configurable width (mm) and tag field.
- **SPACE** type enforced for units under 3 mod.
- Drop zones between units with visual feedback.
- `move_unit` backend function with `_sync_unitdata_reorder` for in-place
  UDATALIN rewrite and `_sync_nameplate_reorder` for LAMACOID.
- `remove_unit` with LAMACOID row deletion and Y-shift of subsequent rows.
- `get_project_state` API used to refresh diagram after every operation.
- MCC toolbar: New Project · Save · Load · Add Section · Bulk Add.

---

## [0.2.0] — MCC Layout engine (August 7, 2026)

### Added
- **`src/tools/mcc_layout.py`** — core MCC automation module.
  - `new_mcc_project` — opens and links `MCC_LAYOUT.dwg` +
    `MCC_UNITDATA.dwg`, initialises in-memory project state.
  - `add_section` — inserts a section frame block at the next cursor position.
  - `add_unit` — inserts a unit block into MCC_LAYOUT, a UDATALIN row into
    MCC_UNITDATA, with wireway, width-detection, and mod-height enforcement.
  - `edit_unit` — in-place field update for layout and unitdata attributes.
  - `move_unit` — repositions a unit within or across sections; resyncs all
    row data.
  - `remove_unit` — deletes layout block, blanks unitdata row, shifts
    subsequent rows.
  - `save_project` / `load_project` — JSON persistence of full project state
    including all COM handles.
- **`src/tools/mcc_unitdata.py`** — UDATALIN row management with
  `_set_attrs_indexed` helper for position-based attribute setting (avoids
  tag-name lookup issues with special-character attribute names).
- **`src/tools/mcc_blocks.py`** — block path resolution, unit block name
  formula (`UNIT5080`, `UNIT4040`, etc.), catalog loading.
- **`mcc_block_catalog.yaml`** — maps section widths and mod heights to DWG
  file names; configures `library_paths`.
- `_display_unit_no` / `_idx_to_letters` helpers for section-prefixed unit
  numbering (F1A, F1B … F1Z, F1AA …).
- `_renumber_section` to update UNIT-NO. display attributes after reorders.
- COM STA thread serialisation in `web/backend/app.py` — all AutoCAD calls
  run through a single `ThreadPoolExecutor` worker to prevent
  `RPC_E_WRONG_THREAD` errors.

---

## [0.1.0] — Initial release (August 6, 2026)

### Added
- **46 MCP tools** across Drawing, Drawing3D, Electrical, Wires, Components,
  Reports, and Project categories.
- **Mode A** — MCP server (`src/server.py`) for use with Claude Code CLI.
- **Mode B** — FastAPI web dashboard (`web/`) with local AI via Ollama,
  OpenAI, Groq, LM Studio, or Claude.
- Multi-provider AI with live model switching and smart fallback.
- Keyword pre-router bypasses LLM for common commands.
- 34 tool-name aliases to correct AI hallucinations.
- Bilingual UI (English / Español) with instant switch.
- PWA support — installable as standalone desktop app.
- Drawing Files sidebar panel showing all open DWGs.
- Service worker with cache-versioned static assets.
- `config.yaml` + `.env` configuration with `${VAR}` interpolation.
- `pyproject.toml` with all dependencies declared.
