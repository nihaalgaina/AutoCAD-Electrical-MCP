# src/tools/mcc_layout.py
"""
MCC_LAYOUT manager — inserts section frames and unit blocks into MCC_LAYOUT.dwg.

This module ONLY touches MCC_LAYOUT.dwg.  All MCC_UNITDATA.dwg operations are
delegated to mcc_unitdata.py, which has its own isolated COM helpers.

Why split?
  The two documents require opposite attribute-update strategies:
    MCC_LAYOUT blocks  — loaded from file paths; need Update() on every attr
                         so that CENTER/MIDDLE justification is applied.
    MCC_UNITDATA rows  — UDATALIN already lives in the block table; positions
                         are correct immediately after InsertBlock and ANY
                         Update() or Regen call corrupts column alignment.
  Keeping them in the same file and sharing _set_attrs caused whichever fix
  was applied last to break the other document.  Separate files, separate
  helpers, zero cross-contamination.
"""

from __future__ import annotations

import math
import uuid
from typing import Any

# ---------------------------------------------------------------------------
# In-memory project store
# ---------------------------------------------------------------------------

_projects: dict[str, dict] = {}

# ---------------------------------------------------------------------------
# Unit-numbering helpers
# ---------------------------------------------------------------------------

def _idx_to_letters(n: int) -> str:
    """Convert a 0-based index to Excel-style column letters.

    Examples: 0→'A', 25→'Z', 26→'AA', 27→'AB'.
    """
    result = ""
    n += 1          # switch to 1-based arithmetic
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result


def _display_unit_no(unit_no: str) -> str:
    """Return the unit number to write to AutoCAD block attributes.

    Internal tracking IDs (prefixed with ``_SPACE_``) are never written to
    block attributes — anonymous spacers should show an empty field in the CAD
    drawing.
    """
    return "" if unit_no.startswith("_SPACE_") else unit_no

# ---------------------------------------------------------------------------
# COM helpers  (layout document only)
# ---------------------------------------------------------------------------

def _get_autocad():
    try:
        import win32com.client as win32
        return win32.GetActiveObject("AutoCAD.Application")
    except Exception:
        raise RuntimeError(
            "Cannot connect to AutoCAD. "
            "Please start AutoCAD Electrical and try again."
        )


def _get_doc(acad, name_fragment: str):
    """Return an open document by partial name match (case-insensitive).

    AutoCAD's Documents COM collection does not support Python's iterator
    protocol in all binding modes (raises "This object does not support
    enumeration").  We iterate by integer index via Item() instead.
    """
    frag = name_fragment.upper()
    docs = acad.Documents
    try:
        count = docs.Count
    except Exception:
        count = 0

    for i in range(count):
        try:
            doc = docs.Item(i)
            if frag in doc.Name.upper():
                return doc
        except Exception:
            continue

    raise RuntimeError(
        f"{name_fragment}.dwg is not open in AutoCAD. "
        f"Please open {name_fragment}.dwg and try again."
    )


def _is_rpc_rejected(exc: Exception) -> bool:
    """Return True if exc is RPC_E_CALL_REJECTED (0x80010001 / -2147418111)."""
    # Check args[0] directly (most reliable — avoids str formatting ambiguity)
    try:
        if exc.args and exc.args[0] in (-2147418111, 0x80010001):
            return True
    except Exception:
        pass
    # Fallback: string search for hex or signed-decimal form
    s = str(exc)
    return "80010001" in s or "-2147418111" in s


def _is_rpc_disconnected(exc: Exception) -> bool:
    """Return True if exc is RPC_E_DISCONNECTED (0x80010108 / -2147417848).

    This happens when AutoCAD's COM proxy becomes stale — typically after a
    long-running operation (e.g. inserting a large block) causes the IPC
    connection to time out.  The fix is to re-acquire the Application and
    Document COM objects rather than retrying with the stale reference.
    """
    try:
        if exc.args and exc.args[0] in (-2147417848, 0x80010108):
            return True
    except Exception:
        pass
    s = str(exc)
    return "80010108" in s or "-2147417848" in s


def _wait_quiescent(doc, timeout_s: float = 25.0) -> None:
    """Poll IsQuiescent without switching the active document.

    Called after a layout InsertBlock completes to let AutoCAD finish any
    post-insert processing (index updates, display regens) before we switch
    tabs to MCC_UNITDATA.  NOT calling Activate() here is intentional: each
    tab-switch can itself trigger a regen on the newly backgrounded doc,
    making AutoCAD busy again in a feedback loop.

    Requires 3 consecutive True readings (~0.45 s of confirmed idle) before
    returning — a single True is insufficient as AutoCAD can flip back to
    busy briefly between background work bursts.
    """
    import time
    # Initial pause: InsertBlock returns before AutoCAD starts its post-insert
    # work, so IsQuiescent may still read True momentarily.  Wait for it to
    # go False first (or just sleep 0.5 s as a minimum buffer).
    time.sleep(0.5)
    try:
        acad   = doc.Application
        stable = 0
        end    = time.time() + timeout_s
        while time.time() < end:
            time.sleep(0.15)
            try:
                if acad.GetAcadState().IsQuiescent:
                    stable += 1
                    if stable >= 3:
                        return
                else:
                    stable = 0
            except Exception:
                break
    except Exception:
        pass
    time.sleep(1.0)


def _insert(doc, block_name_or_path: str, x: float, y: float,
            x_scale: float = 1.0, y_scale: float = 1.0,
            rotation_deg: float = 0.0):
    """Insert a block into doc.ModelSpace with retry on RPC errors.

    Handles two COM error types:
    - RPC_E_CALL_REJECTED (0x80010001): AutoCAD is busy — sleep and retry with
      the existing doc reference.
    - RPC_E_DISCONNECTED (0x80010108): COM proxy is stale (often caused by
      AutoCAD regenerating after a large block insert) — re-acquire the
      Application + Document COM objects from scratch, then retry.

    We do NOT call _activate() inside the retry loop: each tab-switch can
    trigger a regen on the backgrounded document, keeping AutoCAD perpetually
    busy.
    """
    import time
    import win32com.client as win32
    import pythoncom
    # Capture the document name before entering the retry loop — doc.Name may
    # become unreadable if the COM proxy goes stale mid-loop.
    try:
        _doc_name = doc.Name  # e.g. "MCC_LAYOUT.dwg"
        _doc_fragment = _doc_name.upper().replace(".DWG", "")  # e.g. "MCC_LAYOUT"
    except Exception:
        _doc_fragment = None
    _doc = doc
    ms   = _doc.ModelSpace
    pt   = win32.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [x, y, 0.0])
    for attempt in range(20):
        try:
            return ms.InsertBlock(pt, block_name_or_path,
                                  x_scale, y_scale, 1.0,
                                  math.radians(rotation_deg))
        except Exception as exc:
            if _is_rpc_disconnected(exc) and attempt < 19:
                # COM proxy went stale (e.g. AutoCAD regen after large block
                # insert).  Re-acquire the Application + Document objects from
                # scratch before retrying.
                time.sleep(2.0 + attempt * 1.0)
                if _doc_fragment:
                    try:
                        _acad = _get_autocad()
                        _doc  = _get_doc(_acad, _doc_fragment)
                        ms    = _doc.ModelSpace
                    except Exception:
                        pass  # Let the next attempt raise naturally.
            elif _is_rpc_rejected(exc) and attempt < 19:
                time.sleep(1.0 + attempt * 0.5)   # 1.0 s → 1.5 → 2.0 … → 10.5 s
            else:
                raise


def _set_attrs(ref, attrs: dict[str, str]) -> None:
    """Set attribute TextString values on a LAYOUT block reference.

    Only sets TextString — no Update(), no Regen().
    Update() mis-applies block-local attribute positions as world coordinates,
    moving text off-screen.  Alignment is handled separately by _attsync().
    """
    if not attrs:
        return
    attrs_upper = {k.upper(): v for k, v in attrs.items()}
    try:
        for attrib in ref.GetAttributes():
            tag = attrib.TagString.upper()
            if tag in attrs_upper:
                attrib.TextString = str(attrs_upper[tag])
    except Exception:
        pass


def _set_attrs_indexed(ref, values: list[str]) -> None:
    """Set block attributes by position.

    Used for blocks whose tags are not unique — e.g. the dual-feeder blocks
    (UNIT40XX1) where ``UNIT-NO.``, ``TYPE`` and ``EQUIP-NO.`` each appear
    twice (left + right sub-unit).  Setting by tag name would overwrite every
    occurrence with the same string; setting by index lets us write distinct
    values to each occurrence.
    """
    try:
        attrs = list(ref.GetAttributes())
        for i, val in enumerate(values):
            if i >= len(attrs):
                break
            attrs[i].TextString = str(val)
    except Exception:
        pass


def _activate(doc) -> None:
    """Activate doc and wait until AutoCAD is quiescent before returning.

    Why Activate() at all?
      InsertBlock via COM only correctly initialises CENTER/MIDDLE-justified
      attribute positions when the target document is the active tab.  Background
      documents leave TextAlignmentPoint uninitialised → misaligned text.

    Why poll IsQuiescent?
      A fixed sleep is unreliable: Activate() triggers internal AutoCAD
      processing whose duration varies with drawing complexity.  Calling
      InsertBlock before AutoCAD is ready produces RPC_E_CALL_REJECTED (0x80010001).
      Polling GetAcadState().IsQuiescent waits for the exact moment AutoCAD
      signals it is idle, eliminating the race condition.
    """
    import time
    try:
        doc.Activate()
    except Exception:
        return
    # Poll for stable quiescence — require 3 consecutive True readings (≈0.45 s of
    # confirmed idle) before returning.  A single True reading is not sufficient:
    # AutoCAD can flip back to busy milliseconds after reporting quiescent while
    # background index/regen work is still in progress.
    try:
        acad       = doc.Application
        stable     = 0
        for _ in range(200):          # up to 20 s total
            time.sleep(0.1)
            try:
                if acad.GetAcadState().IsQuiescent:
                    stable += 1
                    if stable >= 3:
                        return
                else:
                    stable = 0
            except Exception:
                break
    except Exception:
        pass
    time.sleep(1.0)


def _get_bounding_box(ref) -> tuple[float, float]:
    """Return (width, height) of a block reference bounding box."""
    try:
        mn, mx = ref.GetBoundingBox()
        return abs(mx[0] - mn[0]), abs(mx[1] - mn[1])
    except Exception:
        return 0.0, 0.0


# ---------------------------------------------------------------------------
# Public tools
# ---------------------------------------------------------------------------

def new_mcc_project(
    layout_origin_x: float = 95.0,
    layout_origin_y: float = 120.0,
    first_unit_y: float = 224.0,
    unitdata_row_x: float = 20.0,
    unitdata_row_y: float = 230.0,
    nameplate_row_x: float = 25.0,
    nameplate_row_y: float = 235.0,
    project_name: str = "",
) -> dict[str, Any]:
    """Start a new MCC project.  MCC_LAYOUT, MCC_UNITDATA and MCC_NAMEPLATE must be open.

    Parameters
    ----------
    layout_origin_x/y : float
        Insertion point for the first section frame in MCC_LAYOUT.
    first_unit_y : float
        Y coordinate for the first unit slot inside every section (default 224.0).
    unitdata_row_x/y : float
        World coordinates for the first UDATALIN row in MCC_UNITDATA.
    nameplate_row_x/y : float
        World coordinates for the first LAMACOID row in MCC_NAMEPLATE.
    project_name : str
        Human-readable name for the project (used as the default save filename).
    """
    try:
        acad           = _get_autocad()
        layout_doc     = _get_doc(acad, "MCC_LAYOUT")
        unitdata_doc   = _get_doc(acad, "MCC_UNITDATA")
        nameplate_doc  = _get_doc(acad, "MCC_NAMEPLATE")
    except Exception as exc:
        return {"success": False, "error": str(exc)}

    project_id = str(uuid.uuid4())[:8]
    _projects[project_id] = {
        "project_name":            project_name.strip(),
        "layout_doc":              layout_doc,
        "unitdata_doc":            unitdata_doc,
        "nameplate_doc":           nameplate_doc,
        "section_cursor_x":        layout_origin_x,
        "layout_origin_y":         layout_origin_y,
        "unitdata_row_x":          unitdata_row_x,
        "unitdata_cursor_y":       unitdata_row_y,
        "unitdata_row_y_initial":  unitdata_row_y,   # preserved for save/load
        "nameplate_row_x":         nameplate_row_x,
        "nameplate_cursor_y":      nameplate_row_y,
        "nameplate_row_y_initial": nameplate_row_y,  # preserved for save/load
        "first_unit_y":            first_unit_y,
        "sections":                {},
        "unit_index":              {},
        "row_height":              4,   # 1 mod = 4 drawing units
        "nameplate_row_h":         5,   # LAMACOID rows are 5 drawing units apart
        "unitdata_handle_order":   [],  # physical UDATALIN row handles in Y-position order
        "nameplate_handle_order":  [],  # physical LAMACOID row handles in Y-position order
    }
    return {
        "success":         True,
        "project_id":      project_id,
        "project_name":    project_name.strip(),
        "layout_doc":      layout_doc.Name,
        "unitdata_doc":    unitdata_doc.Name,
        "nameplate_doc":   nameplate_doc.Name,
    }


def add_section(
    project_id: str,
    section_width: int,
    section_id: str,
    variant: str | None = None,
) -> dict[str, Any]:
    """Insert the next MCC section frame into MCC_LAYOUT.

    Sections are placed left-to-right automatically.

    Parameters
    ----------
    project_id   : str        From new_mcc_project().
    section_width : int       400 / 500 / 600 / 800 / 1000 mm.
    section_id   : str        Label written into the SECTION attribute (e.g. "F1").
    variant      : str | None Optional alternate block name within the section entry.
    """
    proj = _projects.get(project_id)
    if proj is None:
        return {"success": False, "error": f"Unknown project_id: {project_id}"}
    if section_id in proj["sections"]:
        return {"success": False, "error": f"Section '{section_id}' already exists"}

    from src.tools import mcc_blocks
    catalog = mcc_blocks._load_catalog()
    entry   = catalog.get("mcc_sections", {}).get(section_width)
    if entry is None:
        return {"success": False, "error": f"No section defined for {section_width}mm"}

    if variant:
        var_entry = entry.get("variants", {}).get(variant)
        if var_entry is None:
            return {"success": False, "error": f"Variant '{variant}' not found"}
        file_path  = var_entry["file_path"]
        block_name = variant
    else:
        file_path  = entry["file_path"]
        block_name = entry["block_name"]

    path = mcc_blocks._resolve_block_path(file_path)
    if path is None:
        libs = mcc_blocks._library_paths()
        hint = " Check MCC_BLOCK_LIBRARY in your .env file." if not libs else f" Searched in: {libs}"
        return {"success": False, "error": f"Section block file not found: {file_path}.{hint}"}

    x = proj["section_cursor_x"]
    y = proj["layout_origin_y"]

    try:
        _activate(proj["layout_doc"])   # must be active for correct attr init
        ref = _insert(proj["layout_doc"], path, x, y)
        _set_attrs(ref, {"SECTION": section_id})
        width_units, height_units = _get_bounding_box(ref)

        proj["sections"][section_id] = {
            "handle":        ref.Handle,
            "block_name":    block_name,
            "section_width": section_width,
            "origin_x":      x,
            "origin_y":      y,
            "width_units":   width_units,
            "height_units":  height_units,
            "unit_cursor_y": proj["first_unit_y"],
            "used_mods":     0.0,
            "capacity_mods": entry.get("mod_height", 24.5),
            "units":         [],
            # Wireway: 100 mm strip on the right side of the section (5 drawing units
            # at 1:20 scale).  VFD/SS units don't get a wireway; all others do.
            # wireway_x  = left edge of wireway = right edge of unit block
            # section_rx = right edge of section frame
            "wireway_x":            x + width_units - 5,
            "section_rx":           x + width_units,
            "wireway_handle":       None,   # primary vertical wireway line handle
            "wireway_cap_handle":   None,   # horizontal cap at bottom of primary segment
            "wireway_extra_handles": [],    # extra segment handles [vert, cap, vert, cap, ...]
        }
        proj["section_cursor_x"] += width_units

        # Wait for AutoCAD to settle after the section insert before returning.
        # The caller will immediately call add_unit() → _activate(layout_doc) →
        # InsertBlock; if AutoCAD is still processing the section insert it will
        # reject that call.
        _wait_quiescent(proj["layout_doc"])

        return {
            "success":       True,
            "section_id":    section_id,
            "handle":        ref.Handle,
            "x": x, "y": y,
            "width_units":   round(width_units, 2),
            "height_units":  round(height_units, 2),
            "capacity_mods": entry.get("mod_height", 24.5),
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def add_unit(
    project_id: str,
    section_id: str,
    mod_height: float,
    unit_no: str,
    qty: int = 1,
    drawout_fixed: str = "D",
    starter_type: str = "",
    tag: str = "",
    custom_width: int | None = None,
    eemac_size: str = "",
    hp_kw: str = "",
    fla: str = "",
    left_amp: str = "",
    right_amp: str = "",
    frame: str = "",
    trip: str = "",
    switch: str = "",
    fuse_type: str = "",
    fuse: str = "",
    cont_qty: str = "",
    contactor: str = "",
    coil: str = "",
    ol_qty: str = "",
    overload: str = "",
    drawing: str = "",
    # Nameplate (LAMACOID) fields
    np_line1: str = "",
    np_line2: str = "",
    np_line3: str = "",
    np_line4: str = "",
    np_qty: str = "",
    np_size: str = "",
    np_style: str = "",
    **extra_fields,
) -> dict[str, Any]:
    """Insert a unit block into MCC_LAYOUT, a UDATALIN row into MCC_UNITDATA,
    and a LAMACOID row into MCC_NAMEPLATE.

    Layout insertion uses Update() on every attribute (centered text).
    UDATALIN insertion is fully delegated to mcc_unitdata.py which uses its
    own isolated _set_attrs that never calls Update().

    Parameters
    ----------
    project_id    : str   From new_mcc_project().
    section_id    : str   Parent section e.g. "F1".
    mod_height    : float Unit height in mod units (e.g. 8, 5, 12).
    unit_no       : str   Unit identifier e.g. "F1A".
    qty           : int   Quantity for UDATALIN QTY field.
    drawout_fixed : str   "D" drawout / "F" fixed.
    starter_type  : str   e.g. "FVNR", "FEEDER", "FVR", "2S1W", "VFD".
    eemac_size    : str   EEMAC motor size 1–6.
    hp_kw         : str   Motor rating e.g. "15HP".
    fla           : str   Full load amps e.g. "20".
    frame         : str   Breaker frame e.g. "CD63A".
    trip          : str   Breaker trip amps.
    switch        : str   Switch size.
    fuse_type     : str   Fuse type.
    fuse          : str   Fuse size.
    cont_qty      : str   Quantity of contactors.
    contactor     : str   Contactor catalog #.
    coil          : str   Coil voltage.
    ol_qty        : str   Quantity of overloads.
    overload      : str   Overload range e.g. "28-40A".
    drawing       : str   Schematic drawing number.
    extra_fields  : dict  Any additional UDATALIN tag→value pairs.
    """
    proj = _projects.get(project_id)
    if proj is None:
        return {"success": False, "error": f"Unknown project_id: {project_id}"}

    sec = proj["sections"].get(section_id)
    if sec is None:
        return {"success": False, "error": f"Section '{section_id}' not found. Call add_section() first."}

    # SPACE units may be anonymous — auto-assign a unique internal ID
    if not unit_no and starter_type.upper() == "SPACE":
        n = sum(
            1 for u in proj["unit_index"]
            if u.startswith(f"_SPACE_{section_id}")
        )
        unit_no = f"_SPACE_{section_id}_{n + 1}"

    if unit_no in proj["unit_index"]:
        return {"success": False, "error": f"Unit '{unit_no}' already exists in this project"}

    remaining = sec["capacity_mods"] - sec["used_mods"]
    if mod_height > remaining + 0.01:
        return {
            "success": False,
            "error":   f"Section '{section_id}' has {remaining:.1f} mod remaining, "
                       f"unit requires {mod_height} mod",
        }

    # Resolve block path.
    # Units with a wireway use the 400 mm block regardless of section width
    # (the wireway occupies the remaining 100 mm).  VFD/SS units span the full
    # section width and therefore use the section-width block.
    # Dual feeders always use the 400 mm wireway block variant (UNIT40XX1).
    from src.tools import mcc_blocks
    _NO_WIREWAY_TYPES = {"VFD", "SS", "VVVF", "SOFTSTART"}
    is_dual_feeder = starter_type.upper() == "DUAL_FEEDER"
    is_custom      = starter_type.upper() == "CUSTOM"

    if is_custom:
        # CUSTOM units let the user choose the block width explicitly.
        # 400 mm → uses wireway channel; any other width → full-section-width block.
        effective_width = custom_width if custom_width is not None else sec["section_width"]
        has_wireway     = (effective_width == 400)
    else:
        has_wireway     = is_dual_feeder or (starter_type.upper() not in _NO_WIREWAY_TYPES)
        effective_width = 400 if has_wireway else sec["section_width"]

    # The STARTER attribute shown in MCC_LAYOUT uses the user-supplied tag when
    # provided (e.g. "FVNR-1"), otherwise falls back to the starter_type string.
    layout_starter_label = tag if tag else starter_type

    if is_dual_feeder:
        # Dual feeder block naming: UNIT40{mod_code}1
        # Whole-number mods use the bare integer (4 → "4"), giving UNIT4041.
        # Fractional mods use mod*10 (3.5 → "35"), giving UNIT40351.
        # This differs from the standard blocks which always use mod*10 with a
        # trailing zero (UNIT4040, UNIT4050, …).
        if mod_height == int(mod_height):
            mod_code = str(int(mod_height))
        else:
            mod_code = str(int(round(mod_height * 10)))
        block_name = f"UNIT40{mod_code}1"
        catalog    = mcc_blocks._load_catalog()
        unit_entry = catalog.get("blocks", {}).get(block_name)
        # Try catalog first, then both file extensions
        if unit_entry:
            file_path = unit_entry["file_path"]
            path      = mcc_blocks._resolve_block_path(file_path)
        else:
            path = (mcc_blocks._resolve_block_path(f"{block_name}.dwg") or
                    mcc_blocks._resolve_block_path(f"{block_name}.DWG"))
        if path is None:
            libs = mcc_blocks._library_paths()
            hint = f" Check MCC_BLOCK_LIBRARY in your .env file. Searched: {libs}" if not libs else f" Searched in: {libs}"
            return {"success": False, "error": f"Block file not found: {block_name}.dwg.{hint}"}
    else:
        try:
            block_name = mcc_blocks.resolve_unit_block_name(effective_width, mod_height)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}

        catalog    = mcc_blocks._load_catalog()
        unit_entry = catalog.get("blocks", {}).get(block_name)
        file_path  = unit_entry["file_path"] if unit_entry else f"{block_name}.DWG"
        path       = mcc_blocks._resolve_block_path(file_path)
        if path is None:
            libs = mcc_blocks._library_paths()
            hint = " Check MCC_BLOCK_LIBRARY in your .env file." if not libs else f" Searched in: {libs}"
            return {"success": False, "error": f"Block file not found: {file_path}.{hint}"}

    unit_x = sec["origin_x"]
    unit_y = sec["unit_cursor_y"]

    # Anonymous SPACE units have an internal tracking ID (starts with _SPACE_)
    # but must show an empty unit-number field in the drawing.
    disp_no = _display_unit_no(unit_no)

    # ---- DUAL FEEDER fast path -----------------------------------------------
    # Dual-feeder blocks have 8 attributes with DUPLICATE tag names so we must
    # set them by position (index), not by tag.  The layout is:
    #   [0] UNIT-NO.  → "{disp_no}L"       (left sub-unit label)
    #   [1] TYPE      → "FEEDER"
    #   [2] EQUIP-NO. → left_amp           (left feeder amps)
    #   [3] EQUIP-NO. → "{mod_height} MOD"
    #   [4] UNIT-NO.  → "{disp_no}R"       (right sub-unit label)
    #   [5] TYPE      → "FEEDER"
    #   [6] EQUIP-NO. → right_amp          (right feeder amps)
    #   [7] EQUIP-NO. → "{mod_height} MOD"
    # Two UDATALIN rows are inserted — one for the L sub-unit and one for R.
    if is_dual_feeder:
        left_unit_no  = f"{disp_no}L"
        right_unit_no = f"{disp_no}R"

        # ---- MCC_LAYOUT insert (indexed attrs) --------------------------------
        try:
            _activate(proj["layout_doc"])
            layout_ref = _insert(proj["layout_doc"], path, unit_x, unit_y)
            _set_attrs_indexed(layout_ref, [
                left_unit_no,  "FEEDER", left_amp,  f"{mod_height} MOD",
                right_unit_no, "FEEDER", right_amp, f"{mod_height} MOD",
            ])
        except Exception as exc:
            return {"success": False, "error": f"MCC_LAYOUT (dual feeder) insert failed: {exc}"}

        sec["unit_cursor_y"] -= mod_height * 4

        # Wireway: dual feeder always has wireway; update the same way as
        # standard wireway units.
        mcc_bottom    = proj["first_unit_y"] - sec["capacity_mods"] * 4
        wireway_end_y = mcc_bottom
        import win32com.client as win32
        import pythoncom
        ms    = proj["layout_doc"].ModelSpace
        wx    = sec["wireway_x"]
        rx    = sec["section_rx"]
        try:
            if sec["wireway_handle"] is None:
                sp = win32.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [wx, unit_y, 0.0])
                ep = win32.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [wx, wireway_end_y, 0.0])
                sec["wireway_handle"] = ms.AddLine(sp, ep).Handle
            else:
                wline = proj["layout_doc"].HandleToObject(sec["wireway_handle"])
                ep = win32.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [wx, wireway_end_y, 0.0])
                wline.EndPoint = ep
            cap_sp = win32.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [wx, wireway_end_y, 0.0])
            cap_ep = win32.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [rx, wireway_end_y, 0.0])
            if sec["wireway_cap_handle"] is None:
                sec["wireway_cap_handle"] = ms.AddLine(cap_sp, cap_ep).Handle
            else:
                cap = proj["layout_doc"].HandleToObject(sec["wireway_cap_handle"])
                cap.StartPoint = cap_sp
                cap.EndPoint   = cap_ep
        except Exception:
            pass

        _wait_quiescent(proj["layout_doc"])

        # ---- MCC_UNITDATA: two rows (L then R) --------------------------------
        from src.tools import mcc_unitdata
        row_h = proj["row_height"]

        ud_attrs_L = {
            "UNIT": left_unit_no, "QTY": str(qty), "HGT": str(mod_height),
            "D/F": drawout_fixed, "TYPE": "FEEDER", "FLA": left_amp,
        }
        ud_attrs_R = {
            "UNIT": right_unit_no, "QTY": str(qty), "HGT": str(mod_height),
            "D/F": drawout_fixed, "TYPE": "FEEDER", "FLA": right_amp,
        }

        ud_result_L = mcc_unitdata.insert_row(
            proj["unitdata_doc"], proj["unitdata_row_x"],
            proj["unitdata_cursor_y"], ud_attrs_L,
        )
        if not ud_result_L["success"]:
            try:
                layout_ref.Delete()
            except Exception:
                pass
            return {"success": False, "error": f"MCC_UNITDATA (dual L) insert failed: {ud_result_L['error']}"}

        ud_handle_L = ud_result_L["handle"]
        proj["unitdata_cursor_y"] -= row_h
        proj.setdefault("unitdata_handle_order", []).append(ud_handle_L)
        _wait_quiescent(proj["unitdata_doc"])

        ud_result_R = mcc_unitdata.insert_row(
            proj["unitdata_doc"], proj["unitdata_row_x"],
            proj["unitdata_cursor_y"], ud_attrs_R,
        )
        if not ud_result_R["success"]:
            # Roll back L row too
            try:
                proj["unitdata_doc"].HandleToObject(ud_handle_L).Delete()
                proj["unitdata_handle_order"].remove(ud_handle_L)
                proj["unitdata_cursor_y"] += row_h
            except Exception:
                pass
            try:
                layout_ref.Delete()
            except Exception:
                pass
            return {"success": False, "error": f"MCC_UNITDATA (dual R) insert failed: {ud_result_R['error']}"}

        ud_handle_R = ud_result_R["handle"]
        proj["unitdata_cursor_y"] -= row_h
        proj.setdefault("unitdata_handle_order", []).append(ud_handle_R)
        _wait_quiescent(proj["unitdata_doc"])

        # ---- MCC_NAMEPLATE: one LAMACOID row for the dual feeder unit --------
        np_handle_df: str | None = None
        nameplate_fields_df = {
            "UNIT":  disp_no,
            "LINE.1": np_line1,
            "LINE.2": np_line2,
            "LINE.3": np_line3,
            "LINE.4": np_line4,
            "QTY":   np_qty,
            "SIZE":  np_size,
            "STYLE": np_style,
        }
        if proj.get("nameplate_doc") is not None:
            from src.tools import mcc_nameplate
            np_res_df = mcc_nameplate.insert_row(
                proj["nameplate_doc"],
                proj["nameplate_row_x"],
                proj["nameplate_cursor_y"],
                nameplate_fields_df,
            )
            if np_res_df["success"]:
                np_handle_df = np_res_df["handle"]
                proj["nameplate_cursor_y"] -= proj.get("nameplate_row_h", 5)
                proj.setdefault("nameplate_handle_order", []).append(np_handle_df)
            _wait_quiescent(proj["nameplate_doc"])

        sec["used_mods"] += mod_height
        unit_record = {
            "unit_no":               unit_no,
            "section_id":            section_id,
            "mod_height":            mod_height,
            "layout_handle":         layout_ref.Handle,
            "unitdata_handle":       ud_handle_L,   # primary (L)
            "unitdata_handle_right": ud_handle_R,   # secondary (R)
            "nameplate_handle":      np_handle_df,
            "fields":                ud_attrs_L,
            "nameplate_fields":      nameplate_fields_df,
            "layout_x":              unit_x,
            "layout_y":              unit_y,
            "starter_type":          "DUAL_FEEDER",
            "left_unit_no":          left_unit_no,
            "right_unit_no":         right_unit_no,
            "left_amp":              left_amp,
            "right_amp":             right_amp,
        }
        sec["units"].append(unit_record)
        proj["unit_index"][unit_no] = unit_record

        return {
            "success":                True,
            "unit_no":                unit_no,
            "section_id":             section_id,
            "block_name":             block_name,
            "layout_handle":          layout_ref.Handle,
            "unitdata_handle_left":   ud_handle_L,
            "unitdata_handle_right":  ud_handle_R,
            "nameplate_handle":       np_handle_df,
            "mods_used":              sec["used_mods"],
            "mods_remaining":         round(sec["capacity_mods"] - sec["used_mods"], 2),
        }
    # ---- END DUAL FEEDER fast path -------------------------------------------

    # ---- MCC_LAYOUT insert (this module) ------------------------------------
    try:
        _activate(proj["layout_doc"])   # must be active for correct attr init
        layout_ref = _insert(proj["layout_doc"], path, unit_x, unit_y)
        layout_attrs: dict[str, str] = {
            "UNIT-NO.":  disp_no,
            "STARTER":   layout_starter_label,
            "EQUIP-NO.": f"{mod_height} MOD",
        }
        if starter_type.upper() == "SPACE":
            layout_attrs["TYPE"] = "SPACE"   # SPACE units use TYPE tag in MCC_LAYOUT
        _set_attrs(layout_ref, layout_attrs)
    except Exception as exc:
        return {"success": False, "error": f"MCC_LAYOUT insert failed: {exc}"}

    # Advance cursor by exact slot height — 1 mod = 4 drawing units.
    # _get_bounding_box() is NOT used here: the bounding box includes border/line
    # thickness and returns ~16.25 for a 4-mod block instead of the exact 16.0,
    # causing accumulated Y-offset error across multiple units.
    sec["unit_cursor_y"] -= mod_height * 4

    # ---- Wireway line ----------------------------------------------------------
    # 400 mm wide units have a 100 mm wireway channel on the right side.
    # Full-width units (VFD, SS, CUSTOM ≥500 mm) span the whole section width
    # so the wireway stops at their top edge rather than running past them.
    mcc_bottom = proj["first_unit_y"] - sec["capacity_mods"] * 4
    if has_wireway:
        wireway_end_y = mcc_bottom   # extend to full MCC bottom (incl. filler)
    else:
        wireway_end_y = unit_y       # full-width unit: stop wireway at its top

    # Update the wireway whenever (a) the new unit is a wireway unit, or (b) a
    # VFD/SS unit has been inserted below existing wireway units (retract it).
    if has_wireway or sec["wireway_handle"] is not None:
        import win32com.client as win32
        import pythoncom
        ms    = proj["layout_doc"].ModelSpace
        wx    = sec["wireway_x"]
        rx    = sec["section_rx"]      # right edge of section frame
        try:
            # --- Vertical wireway line ---
            if sec["wireway_handle"] is None:
                # Start at the top of THIS unit (not the section top) so that
                # any full-width units above (CUSTOM ≥500 mm, VFD, SS) are not
                # incorrectly spanned by the wireway.
                sp = win32.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [wx, unit_y, 0.0])
                ep = win32.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [wx, wireway_end_y, 0.0])
                sec["wireway_handle"] = ms.AddLine(sp, ep).Handle
            else:
                wline = proj["layout_doc"].HandleToObject(sec["wireway_handle"])
                ep = win32.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [wx, wireway_end_y, 0.0])
                wline.EndPoint = ep

            # --- Horizontal cap line closing the bottom of the wireway ---
            # Runs from wireway_x to the section right edge at wireway_end_y.
            cap_sp = win32.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [wx, wireway_end_y, 0.0])
            cap_ep = win32.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [rx, wireway_end_y, 0.0])
            if sec["wireway_cap_handle"] is None:
                sec["wireway_cap_handle"] = ms.AddLine(cap_sp, cap_ep).Handle
            else:
                cap = proj["layout_doc"].HandleToObject(sec["wireway_cap_handle"])
                cap.StartPoint = cap_sp
                cap.EndPoint   = cap_ep
        except Exception:
            pass   # wireway lines are cosmetic; don't fail the whole unit insert

    # Wait for AutoCAD to finish post-insert processing before switching tabs.
    # Switching immediately triggers a regen on the newly-backgrounded layout doc,
    # keeping AutoCAD busy and causing RPC_E_CALL_REJECTED on the unitdata insert.
    _wait_quiescent(proj["layout_doc"])

    # ---- MCC_UNITDATA insert (delegated to mcc_unitdata.py) -----------------
    udatalin_attrs = {
        "UNIT":      disp_no,   # empty string for anonymous spacers
        "QTY":       str(qty),
        "HGT":       str(mod_height),
        "D/F":       drawout_fixed,
        "TYPE":      (tag if (is_custom and tag) else starter_type),
        "SIZE":      eemac_size,
        "HP/KW":     hp_kw,
        "FLA":       fla,
        "FRAME":     frame,
        "TRIP":      trip,
        "SWITCH":    switch,
        "F.TYPE":    fuse_type,
        "FUSE":      fuse,
        "CONT-QTY":  cont_qty,
        "CONTACTOR": contactor,
        "COIL":      coil,
        "OL-QTY":    ol_qty,
        "OVERLOAD":  overload,
        "DRAWING":   drawing,
    }
    udatalin_attrs.update({k.upper(): v for k, v in extra_fields.items()})

    from src.tools import mcc_unitdata
    ud_result = mcc_unitdata.insert_row(
        proj["unitdata_doc"],
        proj["unitdata_row_x"],
        proj["unitdata_cursor_y"],
        udatalin_attrs,
    )
    if not ud_result["success"]:
        try:
            layout_ref.Delete()
        except Exception:
            pass
        return {"success": False, "error": f"MCC_UNITDATA insert failed: {ud_result['error']}"}

    ud_handle = ud_result["handle"]
    proj["unitdata_cursor_y"] -= proj["row_height"]
    proj.setdefault("unitdata_handle_order", []).append(ud_handle)

    # Wait for AutoCAD to settle after the unitdata insert before inserting nameplate.
    _wait_quiescent(proj["unitdata_doc"])

    # ---- MCC_NAMEPLATE insert (one LAMACOID row per unit) --------------------
    np_handle: str | None = None
    nameplate_fields = {
        "UNIT":   disp_no,
        "LINE.1": np_line1,
        "LINE.2": np_line2,
        "LINE.3": np_line3,
        "LINE.4": np_line4,
        "QTY":    np_qty,
        "SIZE":   np_size,
        "STYLE":  np_style,
    }
    if proj.get("nameplate_doc") is not None:
        from src.tools import mcc_nameplate
        np_result = mcc_nameplate.insert_row(
            proj["nameplate_doc"],
            proj["nameplate_row_x"],
            proj["nameplate_cursor_y"],
            nameplate_fields,
        )
        if np_result["success"]:
            np_handle = np_result["handle"]
            proj["nameplate_cursor_y"] -= proj.get("nameplate_row_h", 5)
            proj.setdefault("nameplate_handle_order", []).append(np_handle)
        _wait_quiescent(proj["nameplate_doc"])

    # Update state
    sec["used_mods"] += mod_height
    unit_record = {
        "unit_no":          unit_no,
        "section_id":       section_id,
        "mod_height":       mod_height,
        "layout_handle":    layout_ref.Handle,
        "unitdata_handle":  ud_handle,
        "nameplate_handle": np_handle,
        "fields":           udatalin_attrs,
        "nameplate_fields": nameplate_fields,
        "layout_x":         unit_x,
        "layout_y":         unit_y,
        "starter_type":     starter_type,
        "tag":              tag,
        "custom_width":     custom_width,
    }
    sec["units"].append(unit_record)
    proj["unit_index"][unit_no] = unit_record

    return {
        "success":          True,
        "unit_no":          unit_no,
        "section_id":       section_id,
        "block_name":       block_name,
        "layout_handle":    layout_ref.Handle,
        "unitdata_handle":  ud_handle,
        "nameplate_handle": np_handle,
        "mods_used":        sec["used_mods"],
        "mods_remaining":   round(sec["capacity_mods"] - sec["used_mods"], 2),
    }


# ---------------------------------------------------------------------------
# Drag-and-drop helpers
# ---------------------------------------------------------------------------

def _renumber_section(proj: dict, sec_id: str) -> list[dict]:
    """Re-assign A/B/C… unit numbers to every named unit in *sec_id*.

    Rules
    -----
    - Numbering is ``{sec_id}{letter}`` from A upward (AA after Z, etc.).
    - SPACE units whose mod_height < 3.5 are **anonymous** — they keep an
      internal ``_SPACE_`` tracking ID but the display number written to the
      AutoCAD block is ``""``.  They do *not* consume a letter.
    - All other units (including SPACE ≥ 3.5 mod) get the next letter.
    - ``unit_index`` is updated to reflect the new keys.
    - ``unit_rec["fields"]["UNIT"]`` is updated so ``_sync_unitdata_reorder``
      writes the correct value on the next sync.
    - The ``UNIT-NO.`` attribute on each layout block is updated immediately.

    Returns a list of ``{"old": ..., "new": ...}`` for each unit that changed.
    """
    sec    = proj["sections"][sec_id]
    doc    = proj.get("layout_doc")
    letter_idx = 0
    changes: list[dict] = []

    # Remove all this section's keys from unit_index before re-inserting
    for unit_rec in sec["units"]:
        proj["unit_index"].pop(unit_rec.get("unit_no", ""), None)

    for i, unit_rec in enumerate(sec["units"]):
        stype = unit_rec.get("starter_type", "").upper()
        mod_h = unit_rec.get("mod_height", 0)
        old_no = unit_rec.get("unit_no", "")
        old_display = _display_unit_no(old_no)

        if stype == "SPACE" and mod_h < 3.5:
            # Anonymous spacer — refresh the internal key, write "" to CAD
            new_internal = f"_SPACE_{sec_id}_{i + 1}"
            new_display  = ""
        else:
            letter       = _idx_to_letters(letter_idx)
            letter_idx  += 1
            new_internal = sec_id + letter
            new_display  = new_internal

        unit_rec["unit_no"] = new_internal
        unit_rec.setdefault("fields", {})["UNIT"] = new_display
        proj["unit_index"][new_internal] = unit_rec

        # Update MCC_LAYOUT block attribute in-place
        lh = unit_rec.get("layout_handle")
        if lh and doc is not None:
            try:
                ref = doc.HandleToObject(lh)
                if stype == "DUAL_FEEDER":
                    # Dual-feeder blocks use duplicate tag names — must set by index.
                    # Attributes [0..3] are Left, [4..7] are Right.
                    left_label  = f"{new_display}L"
                    right_label = f"{new_display}R"
                    unit_rec["left_unit_no"]  = left_label
                    unit_rec["right_unit_no"] = right_label
                    _set_attrs_indexed(ref, [
                        left_label,  "FEEDER", unit_rec.get("left_amp",  ""), f"{unit_rec['mod_height']} MOD",
                        right_label, "FEEDER", unit_rec.get("right_amp", ""), f"{unit_rec['mod_height']} MOD",
                    ])
                else:
                    _set_attrs(ref, {"UNIT-NO.": new_display})
            except Exception:
                pass

        if old_display != new_display:
            changes.append({"old": old_display, "new": new_display})

    return changes


def _sync_section_layout_y(proj: dict, sec_id: str) -> None:
    """Move all unit block references in *sec_id* to their correct Y positions.

    When units are reordered the in-memory section list is already updated.
    This function reads the new list order and uses AutoCAD COM ``Move()`` to
    translate each block reference from its current (stale) position to the
    correct new Y coordinate.  X is also updated so that cross-section moves
    place blocks in the correct column.
    """
    import win32com.client as win32
    import pythoncom

    sec = proj["sections"][sec_id]
    doc = proj["layout_doc"]
    target_x = sec["origin_x"]
    y = proj["first_unit_y"]

    for unit_rec in sec["units"]:
        layout_handle = unit_rec.get("layout_handle")
        if not layout_handle:
            # SPACE unit with no block — just advance cursor
            y -= unit_rec["mod_height"] * 4
            unit_rec["layout_y"] = y + unit_rec["mod_height"] * 4
            continue

        old_x = unit_rec.get("layout_x", target_x)
        old_y = unit_rec.get("layout_y", y)
        new_x, new_y = target_x, y

        if abs(old_x - new_x) > 0.001 or abs(old_y - new_y) > 0.001:
            try:
                ref = doc.HandleToObject(layout_handle)
                from_pt = win32.VARIANT(
                    pythoncom.VT_ARRAY | pythoncom.VT_R8, [old_x, old_y, 0.0]
                )
                to_pt = win32.VARIANT(
                    pythoncom.VT_ARRAY | pythoncom.VT_R8, [new_x, new_y, 0.0]
                )
                ref.Move(from_pt, to_pt)
                unit_rec["layout_x"] = new_x
                unit_rec["layout_y"] = new_y
            except Exception:
                # Non-fatal: positions may be slightly off but data is correct
                pass

        y -= unit_rec["mod_height"] * 4

    sec["unit_cursor_y"] = y


def _sync_section_wireway(proj: dict, sec_id: str) -> None:
    """Redraw wireway lines for *sec_id* to match the current unit order.

    After a drag-and-drop reorder the physical wireway lines are stale because
    they were drawn during ``add_unit`` in the original insertion order.  This
    function deletes every existing wireway line for the section and draws a
    fresh set of segments based on the current ``sec["units"]`` list.

    A "wireway segment" is a continuous vertical strip on the right side of the
    section that spans one or more consecutive *standard* (non-VFD/SS) units.
    Each segment gets its own vertical line + horizontal cap.  VFD/SS units
    interrupt the wireway because they span the full section width.

    Segment storage
    ---------------
    - First segment  → ``sec["wireway_handle"]`` + ``sec["wireway_cap_handle"]``
    - Extra segments → ``sec["wireway_extra_handles"]`` (flat list: vert, cap, vert, cap …)
    """
    import win32com.client as win32
    import pythoncom

    _NO_WIREWAY = {"VFD", "SS", "VVVF", "SOFTSTART"}

    sec = proj["sections"][sec_id]
    doc = proj["layout_doc"]
    ms  = doc.ModelSpace

    # ---- 1. Delete all existing wireway geometry for this section ----
    for hkey in ("wireway_handle", "wireway_cap_handle"):
        h = sec.get(hkey)
        if h:
            try:
                doc.HandleToObject(h).Delete()
            except Exception:
                pass
            sec[hkey] = None

    for h in sec.get("wireway_extra_handles", []):
        try:
            doc.HandleToObject(h).Delete()
        except Exception:
            pass
    sec["wireway_extra_handles"] = []

    # ---- 2. Walk unit list and build (y_start, y_end) segments ----
    #  y_start = Y at top of first wireway unit in a consecutive run
    #  y_end   = Y at bottom of last wireway unit in that run
    #              (i.e. Y at top of the following VFD/SS, or mcc_bottom)
    y          = proj["first_unit_y"]
    mcc_bottom = y - sec["capacity_mods"] * 4
    segments: list[tuple[float, float]] = []
    seg_start: float | None = None

    def _unit_has_wireway(unit_rec: dict) -> bool:
        """Return True if this unit occupies the wireway channel (400 mm wide block)."""
        stype = unit_rec.get("starter_type", "").upper()
        if stype in _NO_WIREWAY:
            return False
        if stype == "DUAL_FEEDER":
            return True   # dual feeder always uses 400 mm block
        if stype == "CUSTOM":
            # CUSTOM units: wireway only if the user picked the 400 mm block
            cw = unit_rec.get("custom_width")
            return cw is None or cw == 400
        return True   # all standard starters use 400 mm block (with wireway)

    for unit_rec in sec["units"]:
        is_wireway_unit = _unit_has_wireway(unit_rec)

        if is_wireway_unit:
            if seg_start is None:
                seg_start = y   # begin a new segment at top of this unit
        else:
            if seg_start is not None:
                segments.append((seg_start, y))  # close segment: VFD/SS starts here
                seg_start = None

        y -= unit_rec["mod_height"] * 4

    # Close a trailing segment when the bottom-most unit(s) have a wireway
    if seg_start is not None:
        segments.append((seg_start, mcc_bottom))

    if not segments:
        return  # section is all VFD/SS — no wireway needed

    # ---- 3. Draw each segment ----
    wx = sec["wireway_x"]
    rx = sec["section_rx"]
    extra_handles: list[str] = []

    for i, (y_start, y_end) in enumerate(segments):
        sp     = win32.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [wx, y_start, 0.0])
        ep     = win32.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [wx, y_end,   0.0])
        cap_sp = win32.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [wx, y_end,   0.0])
        cap_ep = win32.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [rx, y_end,   0.0])

        try:
            vert_line = ms.AddLine(sp, ep)
            cap_line  = ms.AddLine(cap_sp, cap_ep)
        except Exception:
            continue  # wireway is cosmetic; don't abort

        if i == 0:
            sec["wireway_handle"]     = vert_line.Handle
            sec["wireway_cap_handle"] = cap_line.Handle
        else:
            extra_handles.extend([vert_line.Handle, cap_line.Handle])

    sec["wireway_extra_handles"] = extra_handles


def _sync_unitdata_reorder(proj: dict) -> None:
    """Rewrite UDATALIN row attribute data to match current unit ordering.

    Physical rows in MCC_UNITDATA.dwg are at fixed Y positions.  Rather than
    moving them, we rewrite the attribute text on each row so that row[i]
    contains the data for unit[i] in the current logical ordering.
    """
    from src.tools import mcc_unitdata

    handle_order = proj.get("unitdata_handle_order", [])
    if not handle_order:
        return

    # Flat list of write-entries: each unit expands to 1 row normally,
    # or 2 rows for DUAL_FEEDER (Left then Right).
    # Each entry: (unit_rec, attrs_dict, handle_key)
    write_entries: list[tuple[dict, dict, str]] = []
    for sec in proj["sections"].values():
        for unit in sec["units"]:
            if unit.get("starter_type", "").upper() == "DUAL_FEEDER":
                left_label  = unit.get("left_unit_no",  unit["unit_no"] + "L")
                right_label = unit.get("right_unit_no", unit["unit_no"] + "R")
                attrs_L = {
                    "UNIT": left_label,
                    "HGT":  str(unit["mod_height"]),
                    "D/F":  unit.get("fields", {}).get("D/F", "D"),
                    "TYPE": "FEEDER",
                    "FLA":  unit.get("left_amp", ""),
                }
                attrs_R = {
                    "UNIT": right_label,
                    "HGT":  str(unit["mod_height"]),
                    "D/F":  unit.get("fields", {}).get("D/F", "D"),
                    "TYPE": "FEEDER",
                    "FLA":  unit.get("right_amp", ""),
                }
                write_entries.append((unit, attrs_L, "unitdata_handle"))
                write_entries.append((unit, attrs_R, "unitdata_handle_right"))
            else:
                attrs = dict(unit.get("fields", {}))
                attrs.setdefault("UNIT", unit["unit_no"])
                attrs.setdefault("HGT",  str(unit["mod_height"]))
                write_entries.append((unit, attrs, "unitdata_handle"))

    doc = proj["unitdata_doc"]
    mcc_unitdata._activate(doc)

    for i, handle in enumerate(handle_order):
        if i >= len(write_entries):
            break
        unit, attrs, handle_key = write_entries[i]
        try:
            ref = doc.HandleToObject(handle)
            mcc_unitdata._set_attrs(ref, attrs)
            # Keep the handle pointer on the unit record up to date
            unit[handle_key] = handle
        except Exception:
            pass


def _sync_nameplate_reorder(proj: dict) -> None:
    """Rewrite LAMACOID row attribute data to match current unit ordering.

    Physical rows in MCC_NAMEPLATE.dwg are at fixed Y positions.  We rewrite
    the attribute text on each row so row[i] contains the data for unit[i] in
    the current logical ordering across all sections.  One row per unit
    (dual feeders count as one unit).
    """
    from src.tools import mcc_nameplate

    handle_order = proj.get("nameplate_handle_order", [])
    if not handle_order:
        return

    doc = proj.get("nameplate_doc")
    if doc is None:
        return

    # Build write list: one entry per unit in section order
    write_entries: list[tuple[dict, dict]] = []
    for sec in proj["sections"].values():
        for unit in sec["units"]:
            disp = _display_unit_no(unit["unit_no"])
            np_f = unit.get("nameplate_fields", {})
            attrs = {
                "UNIT":   disp,
                "LINE.1": np_f.get("LINE.1", ""),
                "LINE.2": np_f.get("LINE.2", ""),
                "LINE.3": np_f.get("LINE.3", ""),
                "LINE.4": np_f.get("LINE.4", ""),
                "QTY":    np_f.get("QTY",   ""),
                "SIZE":   np_f.get("SIZE",  ""),
                "STYLE":  np_f.get("STYLE", ""),
            }
            write_entries.append((unit, attrs))

    mcc_nameplate._activate(doc)

    for i, handle in enumerate(handle_order):
        if i >= len(write_entries):
            break
        unit, attrs = write_entries[i]
        try:
            ref = doc.HandleToObject(handle)
            mcc_nameplate._set_attrs(ref, attrs)
            unit["nameplate_handle"] = handle
        except Exception:
            pass


def move_unit(
    project_id: str,
    unit_no: str,
    target_section_id: str,
    target_index: int,
) -> dict[str, Any]:
    """Move a unit to a new position within or between sections.

    Parameters
    ----------
    project_id        : str  Project returned by new_mcc_project().
    unit_no           : str  Unit identifier e.g. "F1A".
    target_section_id : str  Destination section e.g. "F2".
    target_index      : int  0-based insertion position within the target
                             section (0 = top of section).

    Overflow handling
    -----------------
    If inserting the unit would push the target section past its capacity
    (24.5 mod by default), the bottom-most unit in that section is
    automatically cascaded to the top of the next section.  If there is no
    next section the move is rejected and an overflow_warning is returned.

    Returns
    -------
    dict with keys:
      success          : bool
      moved            : str | False   (False if no change was needed)
      from_section     : str
      to_section       : str
      to_index         : int
      cascaded         : list[dict]    (units that were pushed to next section)
      overflow_warning : bool          (present only when move was rejected)
      sync_errors      : list[str]     (non-fatal AutoCAD errors)
    """
    proj = _projects.get(project_id)
    if proj is None:
        return {"success": False, "error": f"Unknown project_id: {project_id}"}

    sections = proj["sections"]

    # 1. Locate the unit
    src_sec_id: str | None = None
    src_idx: int | None = None
    for sid, sec in sections.items():
        for i, u in enumerate(sec["units"]):
            if u["unit_no"] == unit_no:
                src_sec_id, src_idx = sid, i
                break
        if src_sec_id:
            break

    if src_sec_id is None:
        return {"success": False, "error": f"Unit '{unit_no}' not found."}
    if target_section_id not in sections:
        return {"success": False, "error": f"Section '{target_section_id}' not found."}

    # 2. Clamp and no-op check
    tgt_max = len(sections[target_section_id]["units"])
    if src_sec_id == target_section_id:
        tgt_max -= 1  # one element is being removed first
    target_index = max(0, min(target_index, tgt_max))

    if src_sec_id == target_section_id and src_idx == target_index:
        return {"success": True, "moved": False, "message": "No change."}

    # 3. Remove from source
    unit_rec = sections[src_sec_id]["units"].pop(src_idx)
    sections[src_sec_id]["used_mods"] = round(
        sections[src_sec_id]["used_mods"] - unit_rec["mod_height"], 4
    )

    # Adjust index when moving down in same section
    if src_sec_id == target_section_id and src_idx < target_index:
        target_index -= 1

    # 4. Insert into target
    tgt_sec = sections[target_section_id]
    target_index = max(0, min(target_index, len(tgt_sec["units"])))
    tgt_sec["units"].insert(target_index, unit_rec)
    tgt_sec["used_mods"] = round(tgt_sec["used_mods"] + unit_rec["mod_height"], 4)
    unit_rec["section_id"] = target_section_id

    # 5. Overflow check → cascade
    cascaded: list[dict] = []
    MAX_MODS = tgt_sec["capacity_mods"]

    if tgt_sec["used_mods"] > MAX_MODS + 0.01:
        sec_ids = list(sections.keys())
        tgt_pos = sec_ids.index(target_section_id)

        if tgt_pos >= len(sec_ids) - 1:
            # No next section — revert and warn
            tgt_sec["units"].pop(target_index)
            tgt_sec["used_mods"] = round(tgt_sec["used_mods"] - unit_rec["mod_height"], 4)
            sections[src_sec_id]["units"].insert(src_idx, unit_rec)
            sections[src_sec_id]["used_mods"] = round(
                sections[src_sec_id]["used_mods"] + unit_rec["mod_height"], 4
            )
            unit_rec["section_id"] = src_sec_id
            return {
                "success": False,
                "overflow_warning": True,
                "error": (
                    f"Moving '{unit_no}' to '{target_section_id}' would exceed "
                    f"{MAX_MODS} mod capacity. Create another section first."
                ),
            }

        # Push bottom-most unit in target → top of next section
        overflow_rec = tgt_sec["units"].pop()
        tgt_sec["used_mods"] = round(tgt_sec["used_mods"] - overflow_rec["mod_height"], 4)

        next_sec_id = sec_ids[tgt_pos + 1]
        next_sec    = sections[next_sec_id]
        next_sec["units"].insert(0, overflow_rec)
        next_sec["used_mods"] = round(next_sec["used_mods"] + overflow_rec["mod_height"], 4)
        overflow_rec["section_id"] = next_sec_id
        if overflow_rec["unit_no"] in proj["unit_index"]:
            proj["unit_index"][overflow_rec["unit_no"]]["section_id"] = next_sec_id

        cascaded.append({
            "unit_no":      overflow_rec["unit_no"],
            "from_section": target_section_id,
            "to_section":   next_sec_id,
        })

    # 6. Sync AutoCAD — layout block positions
    affected = {src_sec_id, target_section_id}
    for c in cascaded:
        affected.add(c["to_section"])

    sync_errors: list[str] = []
    if proj.get("layout_doc") is not None:
        try:
            _activate(proj["layout_doc"])
            for sid in affected:
                _sync_section_layout_y(proj, sid)
                _sync_section_wireway(proj, sid)
                _renumber_section(proj, sid)
            _wait_quiescent(proj["layout_doc"])
        except Exception as exc:
            sync_errors.append(f"layout: {exc}")

    # 7. Sync AutoCAD — UDATALIN row data (unitdata after renumber so UNIT fields are current)
    if proj.get("unitdata_doc") is not None:
        try:
            _sync_unitdata_reorder(proj)
            _wait_quiescent(proj["unitdata_doc"])
        except Exception as exc:
            sync_errors.append(f"unitdata: {exc}")

    # 8. Sync LAMACOID nameplate rows
    if proj.get("nameplate_doc") is not None:
        try:
            _sync_nameplate_reorder(proj)
            _wait_quiescent(proj["nameplate_doc"])
        except Exception as exc:
            sync_errors.append(f"nameplate: {exc}")

    result: dict[str, Any] = {
        "success":      True,
        "moved":        unit_no,
        "from_section": src_sec_id,
        "to_section":   target_section_id,
        "to_index":     target_index,
        "cascaded":     cascaded,
    }
    if sync_errors:
        result["sync_errors"] = sync_errors
    return result


def remove_unit(
    project_id: str,
    unit_no: str,
) -> dict[str, Any]:
    """Delete a unit from the project, removing its geometry from both drawings.

    The layout block reference is deleted from MCC_LAYOUT.  The corresponding
    UDATALIN row in MCC_UNITDATA is blanked out (all attributes cleared) and
    de-tracked — it will no longer appear in unitdata exports.  Remaining units
    are re-ordered in the unitdata drawing and the section wireway is redrawn.

    Parameters
    ----------
    project_id : str   Project ID from new_mcc_project().
    unit_no    : str   Unit identifier to remove, e.g. "F1A".

    Returns
    -------
    dict with keys ``success``, ``removed``, ``section_id``, optionally
    ``sync_errors`` (non-fatal AutoCAD failures).
    """
    proj = _projects.get(project_id)
    if proj is None:
        return {"success": False, "error": f"Unknown project_id: {project_id}"}

    # 1. Locate the unit
    src_sec_id: str | None = None
    src_idx: int | None    = None
    for sid, sec in proj["sections"].items():
        for i, u in enumerate(sec["units"]):
            if u["unit_no"] == unit_no:
                src_sec_id, src_idx = sid, i
                break
        if src_sec_id:
            break

    if src_sec_id is None:
        return {"success": False, "error": f"Unit '{unit_no}' not found."}

    sec      = proj["sections"][src_sec_id]
    unit_rec = sec["units"].pop(src_idx)
    sec["used_mods"] = round(sec["used_mods"] - unit_rec["mod_height"], 4)
    proj["unit_index"].pop(unit_no, None)

    sync_errors: list[str] = []

    # 2. Delete the layout block from MCC_LAYOUT
    layout_handle = unit_rec.get("layout_handle")
    if layout_handle and proj.get("layout_doc") is not None:
        try:
            _activate(proj["layout_doc"])
            proj["layout_doc"].HandleToObject(layout_handle).Delete()
        except Exception as exc:
            sync_errors.append(f"layout delete: {exc}")

    # 3. Delete the UDATALIN block(s) and shift all subsequent rows up to close the gap.
    #    Dual feeders have TWO rows (L handle + R handle); shift by 2*row_h after removing both.
    is_dual = unit_rec.get("starter_type", "").upper() == "DUAL_FEEDER"
    ud_handle   = unit_rec.get("unitdata_handle")          # always present (L for dual)
    ud_handle_r = unit_rec.get("unitdata_handle_right")    # only for dual feeder

    # Collect the handles we need to delete in logical order (L before R)
    handles_to_delete = [h for h in [ud_handle, ud_handle_r] if h]

    if handles_to_delete and proj.get("unitdata_doc") is not None:
        try:
            import win32com.client as win32
            import pythoncom
            from src.tools import mcc_unitdata

            ho     = proj.setdefault("unitdata_handle_order", [])
            row_h  = proj.get("row_height", 4)
            doc_ud = proj["unitdata_doc"]
            mcc_unitdata._activate(doc_ud)

            # Find the earliest row index among the handles being deleted
            first_row_idx: int | None = None
            for h in handles_to_delete:
                try:
                    idx = ho.index(h)
                    if first_row_idx is None or idx < first_row_idx:
                        first_row_idx = idx
                except ValueError:
                    pass

            # Delete all physical blocks and remove from handle list
            rows_deleted = 0
            for h in handles_to_delete:
                try:
                    doc_ud.HandleToObject(h).Delete()
                except Exception:
                    pass
                if h in ho:
                    ho.remove(h)
                    rows_deleted += 1

            # Shift every remaining row that came AFTER the deleted slot(s) up
            if first_row_idx is not None and rows_deleted > 0:
                shift_y = float(row_h * rows_deleted)
                from_pt = win32.VARIANT(
                    pythoncom.VT_ARRAY | pythoncom.VT_R8, [0.0, 0.0, 0.0]
                )
                to_pt = win32.VARIANT(
                    pythoncom.VT_ARRAY | pythoncom.VT_R8, [0.0, shift_y, 0.0]
                )
                for h in ho[first_row_idx:]:
                    try:
                        doc_ud.HandleToObject(h).Move(from_pt, to_pt)
                    except Exception:
                        pass

                proj["unitdata_cursor_y"] = proj.get("unitdata_cursor_y", 0) + shift_y

        except Exception as exc:
            sync_errors.append(f"unitdata delete: {exc}")

    # 3b. Delete the LAMACOID nameplate row and shift remaining rows up.
    np_handle = unit_rec.get("nameplate_handle")
    if np_handle and proj.get("nameplate_doc") is not None:
        try:
            import win32com.client as win32
            import pythoncom
            from src.tools import mcc_nameplate

            np_ho    = proj.setdefault("nameplate_handle_order", [])
            np_row_h = proj.get("nameplate_row_h", 5)
            doc_np   = proj["nameplate_doc"]
            mcc_nameplate._activate(doc_np)

            np_row_idx: int | None = None
            try:
                np_row_idx = np_ho.index(np_handle)
            except ValueError:
                pass

            # Delete the physical LAMACOID block
            try:
                doc_np.HandleToObject(np_handle).Delete()
            except Exception:
                pass
            if np_handle in np_ho:
                np_ho.remove(np_handle)

            # Shift all subsequent rows up
            if np_row_idx is not None:
                from_pt = win32.VARIANT(
                    pythoncom.VT_ARRAY | pythoncom.VT_R8, [0.0, 0.0, 0.0]
                )
                to_pt = win32.VARIANT(
                    pythoncom.VT_ARRAY | pythoncom.VT_R8, [0.0, float(np_row_h), 0.0]
                )
                for h in np_ho[np_row_idx:]:
                    try:
                        doc_np.HandleToObject(h).Move(from_pt, to_pt)
                    except Exception:
                        pass
                proj["nameplate_cursor_y"] = proj.get("nameplate_cursor_y", 0) + np_row_h

        except Exception as exc:
            sync_errors.append(f"nameplate delete: {exc}")

    # 4. Re-sync layout Y positions, wireway, and unit numbering
    if proj.get("layout_doc") is not None:
        try:
            _activate(proj["layout_doc"])
            _sync_section_layout_y(proj, src_sec_id)
            _sync_section_wireway(proj, src_sec_id)
            _renumber_section(proj, src_sec_id)
            _wait_quiescent(proj["layout_doc"])
        except Exception as exc:
            sync_errors.append(f"layout sync: {exc}")

    # 5. Re-sync unitdata row data after renumbering then wait
    if proj.get("unitdata_doc") is not None:
        try:
            _sync_unitdata_reorder(proj)
            _wait_quiescent(proj["unitdata_doc"])
        except Exception as exc:
            sync_errors.append(f"unitdata sync: {exc}")

    # 6. Re-sync nameplate row data after renumbering
    if proj.get("nameplate_doc") is not None:
        try:
            _sync_nameplate_reorder(proj)
            _wait_quiescent(proj["nameplate_doc"])
        except Exception as exc:
            sync_errors.append(f"nameplate sync: {exc}")

    result: dict[str, Any] = {
        "success":    True,
        "removed":    unit_no,
        "section_id": src_sec_id,
    }
    if sync_errors:
        result["sync_errors"] = sync_errors
    return result


def bulk_add_units(
    project_id: str,
    count: int,
    starter_type: str,
    mod_height: float,
    section_prefix: str,
    section_width: int = 500,
    starting_section_number: int | None = None,
    drawout_fixed: str = "D",
    qty: int = 1,
    eemac_size: str = "",
    hp_kw: str = "",
    fla: str = "",
    frame: str = "",
    trip: str = "",
    switch: str = "",
    fuse_type: str = "",
    fuse: str = "",
    cont_qty: str = "",
    contactor: str = "",
    coil: str = "",
    ol_qty: str = "",
    overload: str = "",
    drawing: str = "",
    **extra_fields,
) -> dict[str, Any]:
    """Insert *count* identical starters, creating sections automatically.

    Sections are named ``{section_prefix}{n}`` (e.g. ``F1``, ``F2`` …).
    Units within each section are lettered ``A``, ``B``, ``C`` … in order.
    SPACE units < 3.5 mod are not used in bulk add (only real starters).

    Parameters
    ----------
    project_id            : str   From new_mcc_project().
    count                 : int   Total number of starters to insert.
    starter_type          : str   e.g. "FVNR", "VFD", "2S1W".
    mod_height            : float Module height of each starter.
    section_prefix        : str   Letter prefix for section IDs, e.g. "F".
    section_width         : int   Width in mm (400/500/600/800/1000).
    starting_section_number : int | None   First section number; auto-detected if None.
    drawout_fixed … drawing : str  Passed verbatim to every add_unit call.

    Returns
    -------
    dict with ``success``, ``sections_created``, ``units_added``, ``errors``,
    ``total_added``.
    """
    proj = _projects.get(project_id)
    if proj is None:
        return {"success": False, "error": f"Unknown project_id: {project_id}"}

    # How many starters fit in one 24.5 mod section?
    capacity = 24.5
    per_section = int(capacity // mod_height)
    if per_section == 0:
        return {
            "success": False,
            "error": f"{mod_height} mod exceeds section capacity ({capacity} mod).",
        }

    sections_needed = math.ceil(count / per_section)

    # Auto-detect starting section number: next unused number after existing ones
    if starting_section_number is None:
        nums = []
        for sid in proj["sections"]:
            if sid.upper().startswith(section_prefix.upper()):
                try:
                    nums.append(int(sid[len(section_prefix):]))
                except ValueError:
                    pass
        starting_section_number = (max(nums) + 1) if nums else 1

    results: dict[str, Any] = {
        "success":          True,
        "sections_created": [],
        "units_added":      [],
        "errors":           [],
    }

    # Shared kwargs for every add_unit call — named params plus any extra UDATALIN fields
    unit_kwargs = dict(
        drawout_fixed=drawout_fixed, qty=qty, eemac_size=eemac_size,
        hp_kw=hp_kw, fla=fla, frame=frame, trip=trip, switch=switch,
        fuse_type=fuse_type, fuse=fuse, cont_qty=cont_qty, contactor=contactor,
        coil=coil, ol_qty=ol_qty, overload=overload, drawing=drawing,
        **extra_fields,
    )

    remaining = count
    for sec_num in range(
        starting_section_number,
        starting_section_number + sections_needed,
    ):
        if remaining <= 0:
            break
        sec_id = f"{section_prefix}{sec_num}"

        # Create section if it doesn't exist yet
        if sec_id not in proj["sections"]:
            r = add_section(project_id, section_width, sec_id)
            if not r["success"]:
                results["errors"].append(f"Section {sec_id}: {r['error']}")
                continue
            results["sections_created"].append(sec_id)

        # Fill this section
        in_this_sec = min(remaining, per_section)
        for i in range(in_this_sec):
            letter  = _idx_to_letters(len(results["units_added"]) % per_section)
            unit_no = f"{sec_id}{letter}"
            r = add_unit(
                project_id=project_id,
                section_id=sec_id,
                mod_height=mod_height,
                unit_no=unit_no,
                starter_type=starter_type,
                **unit_kwargs,
            )
            if r["success"]:
                results["units_added"].append(unit_no)
            else:
                results["errors"].append(f"Unit {unit_no}: {r['error']}")

        remaining -= in_this_sec

    results["total_added"] = len(results["units_added"])
    if results["errors"] and not results["units_added"]:
        results["success"] = False
    return results


def update_unit(
    project_id: str,
    unit_no: str,
    fields: dict[str, str],
) -> dict[str, Any]:
    """Update UDATALIN fields on an existing unit (both drawings).

    Fields that map to MCC_LAYOUT attributes (UNIT→UNIT-NO., TYPE→STARTER)
    are also updated in the layout drawing.

    Parameters
    ----------
    project_id : str        Project ID.
    unit_no    : str        Unit to update e.g. "F1A".
    fields     : dict       UDATALIN tag → new value.
    """
    proj = _projects.get(project_id)
    if proj is None:
        return {"success": False, "error": f"Unknown project_id: {project_id}"}

    unit = proj["unit_index"].get(unit_no)
    if unit is None:
        return {"success": False, "error": f"Unit '{unit_no}' not found"}

    try:
        # MCC_UNITDATA via mcc_unitdata.py
        from src.tools import mcc_unitdata
        mcc_unitdata.update_row(
            proj["unitdata_doc"],
            unit["unitdata_handle"],
            fields,
        )

        # MCC_LAYOUT — only UNIT-NO. and STARTER map across
        layout_map = {"UNIT": "UNIT-NO.", "TYPE": "STARTER"}
        layout_updates = {
            layout_map[k.upper()]: v
            for k, v in fields.items()
            if k.upper() in layout_map
        }
        if layout_updates:
            layout_ref = proj["layout_doc"].HandleToObject(unit["layout_handle"])
            _set_attrs(layout_ref, layout_updates)

        unit["fields"].update({k.upper(): v for k, v in fields.items()})
        return {"success": True, "unit_no": unit_no, "updated": fields}

    except Exception as exc:
        return {"success": False, "error": str(exc)}


def edit_unit(
    project_id: str,
    unit_no: str,
    mod_height: float | None = None,
    starter_type: str | None = None,
    tag: str = "",
    custom_width: int | None = None,
    drawout_fixed: str = "",
    qty: int | None = None,
    eemac_size: str = "",
    hp_kw: str = "",
    fla: str = "",
    frame: str = "",
    trip: str = "",
    switch: str = "",
    fuse_type: str = "",
    fuse: str = "",
    cont_qty: str = "",
    contactor: str = "",
    coil: str = "",
    ol_qty: str = "",
    overload: str = "",
    drawing: str = "",
    left_amp: str = "",
    right_amp: str = "",
    # Nameplate (LAMACOID) fields — empty string = no change
    np_line1: str = "",
    np_line2: str = "",
    np_line3: str = "",
    np_line4: str = "",
    np_qty: str = "",
    np_size: str = "",
    np_style: str = "",
    **extra_fields,
) -> dict[str, Any]:
    """Edit an existing unit in-place — field values and/or structural changes.

    Field-only changes (UDATALIN text) are applied without touching AutoCAD
    geometry.  Changing ``mod_height`` or ``starter_type`` replaces the layout
    block and re-syncs all Y positions and the wireway for the section.

    Parameters
    ----------
    project_id   : Project ID.
    unit_no      : Unit identifier e.g. "F1A".
    mod_height   : New module height, or None to keep current.
    starter_type : New starter type, or None to keep current.
    All other keyword args map to UDATALIN fields (empty string = no change).
    """
    proj = _projects.get(project_id)
    if proj is None:
        return {"success": False, "error": f"Unknown project_id: {project_id}"}

    unit_rec = proj["unit_index"].get(unit_no)
    if unit_rec is None:
        return {"success": False, "error": f"Unit '{unit_no}' not found"}

    sec_id  = unit_rec["section_id"]
    sec     = proj["sections"][sec_id]
    sync_errors: list[str] = []

    old_mod    = unit_rec["mod_height"]
    old_type   = unit_rec.get("starter_type", "")
    new_mod    = mod_height    if mod_height    is not None else old_mod
    new_type   = starter_type  if starter_type  is not None else old_type

    # ── Structural replacement if mod_height or starter_type changed ──────────
    replace_block = (new_mod != old_mod) or (new_type.upper() != old_type.upper())

    if replace_block:
        # Capacity check for the new mod height
        mod_delta = new_mod - old_mod
        new_used  = round(sec["used_mods"] + mod_delta, 4)
        if new_used > sec["capacity_mods"] + 0.01:
            return {
                "success": False,
                "error":   f"Section '{sec_id}' only has "
                           f"{round(sec['capacity_mods'] - sec['used_mods'], 1)} mod free; "
                           f"need {mod_delta:+.1f} mod for this change.",
            }

        from src.tools import mcc_blocks
        _NO_WIREWAY_TYPES = {"VFD", "SS", "VVVF", "SOFTSTART"}
        is_dual      = new_type.upper() == "DUAL_FEEDER"
        is_custom_t  = new_type.upper() == "CUSTOM"
        if is_custom_t:
            eff_w       = custom_width if custom_width is not None else unit_rec.get("custom_width") or sec["section_width"]
            has_wireway = (eff_w == 400)
        else:
            has_wireway = is_dual or (new_type.upper() not in _NO_WIREWAY_TYPES)
            eff_w       = 400 if has_wireway else sec["section_width"]

        layout_starter_label = tag if tag else (unit_rec.get("tag") or new_type)

        # Resolve new block
        if is_dual:
            if new_mod == int(new_mod):
                mod_code = str(int(new_mod))
            else:
                mod_code = str(int(round(new_mod * 10)))
            block_name = f"UNIT40{mod_code}1"
            catalog    = mcc_blocks._load_catalog()
            unit_entry = catalog.get("blocks", {}).get(block_name)
            file_path  = unit_entry["file_path"] if unit_entry else f"{block_name}.dwg"
            path       = (mcc_blocks._resolve_block_path(file_path) or
                          mcc_blocks._resolve_block_path(f"{block_name}.DWG"))
        else:
            try:
                block_name = mcc_blocks.resolve_unit_block_name(eff_w, new_mod)
            except ValueError as exc:
                return {"success": False, "error": str(exc)}
            catalog    = mcc_blocks._load_catalog()
            unit_entry = catalog.get("blocks", {}).get(block_name)
            file_path  = unit_entry["file_path"] if unit_entry else f"{block_name}.DWG"
            path       = mcc_blocks._resolve_block_path(file_path)

        if path is None:
            libs = mcc_blocks._library_paths()
            hint = " Check MCC_BLOCK_LIBRARY in your .env file." if not libs else f" Searched in: {libs}"
            return {"success": False, "error": f"Block file not found: {file_path}.{hint}"}

        insert_y = unit_rec["layout_y"]
        insert_x = unit_rec["layout_x"]
        disp_no  = _display_unit_no(unit_no)

        try:
            _activate(proj["layout_doc"])
            # Delete old layout block
            proj["layout_doc"].HandleToObject(unit_rec["layout_handle"]).Delete()

            # Insert new block at same position
            new_ref = _insert(proj["layout_doc"], path, insert_x, insert_y)

            if is_dual:
                left_label  = disp_no + "L"
                right_label = disp_no + "R"
                _set_attrs_indexed(new_ref, [
                    left_label,  "FEEDER", left_amp  or unit_rec.get("left_amp",  ""), f"{new_mod} MOD",
                    right_label, "FEEDER", right_amp or unit_rec.get("right_amp", ""), f"{new_mod} MOD",
                ])
                unit_rec["left_unit_no"]  = left_label
                unit_rec["right_unit_no"] = right_label
                if left_amp:  unit_rec["left_amp"]  = left_amp
                if right_amp: unit_rec["right_amp"] = right_amp
            else:
                layout_attrs: dict[str, str] = {
                    "UNIT-NO.":  disp_no,
                    "STARTER":   layout_starter_label,
                    "EQUIP-NO.": f"{new_mod} MOD",
                }
                if new_type.upper() == "SPACE":
                    layout_attrs["TYPE"] = "SPACE"
                _set_attrs(new_ref, layout_attrs)

            unit_rec["layout_handle"] = new_ref.Handle
        except Exception as exc:
            return {"success": False, "error": f"Layout block replacement failed: {exc}"}

        # Update accounting
        sec["used_mods"] = new_used
        unit_rec["mod_height"]   = new_mod
        unit_rec["starter_type"] = new_type
        unit_rec["tag"]          = tag or unit_rec.get("tag", "")
        if custom_width is not None:
            unit_rec["custom_width"] = custom_width

        # Re-sync Y positions and wireway for the section
        _wait_quiescent(proj["layout_doc"])
        try:
            _sync_section_layout_y(proj, sec_id)
            _sync_section_wireway(proj, sec_id)
        except Exception as exc:
            sync_errors.append(f"layout sync: {exc}")

    # ── Tag-only update (no block replacement) ────────────────────────────────
    # If the tag changed but the block didn't need replacement, still update the
    # STARTER attribute on the existing layout block.
    if not replace_block and tag and tag != unit_rec.get("tag", ""):
        try:
            _activate(proj["layout_doc"])
            ref = proj["layout_doc"].HandleToObject(unit_rec["layout_handle"])
            _set_attrs(ref, {"STARTER": tag})
            unit_rec["tag"] = tag
        except Exception as exc:
            sync_errors.append(f"tag update: {exc}")

    # ── UDATALIN field updates ─────────────────────────────────────────────────
    # Collect all non-empty provided field values
    field_updates: dict[str, str] = {}
    named = [
        ("D/F",       drawout_fixed),
        ("HP/KW",     hp_kw),
        ("FLA",       fla),
        ("FRAME",     frame),
        ("TRIP",      trip),
        ("SWITCH",    switch),
        ("F.TYPE",    fuse_type),
        ("FUSE",      fuse),
        ("CONT-QTY",  cont_qty),
        ("CONTACTOR", contactor),
        ("COIL",      coil),
        ("OL-QTY",    ol_qty),
        ("OVERLOAD",  overload),
        ("DRAWING",   drawing),
    ]
    for tag, val in named:
        if val:
            field_updates[tag] = val
    if qty is not None:
        field_updates["QTY"] = str(qty)
    # Structural fields always written
    field_updates["HGT"]  = str(new_mod)
    _eff_tag = unit_rec.get("tag", "")
    field_updates["TYPE"] = (_eff_tag if new_type.upper() == "CUSTOM" and _eff_tag else new_type)
    # Extra UDATALIN tags passed as kwargs
    field_updates.update({k.upper(): v for k, v in extra_fields.items() if v})

    # For dual feeders update L and R rows separately
    is_dual_now = new_type.upper() == "DUAL_FEEDER"
    try:
        from src.tools import mcc_unitdata
        if is_dual_now:
            # Update L row
            l_attrs = dict(field_updates)
            l_attrs["UNIT"] = unit_rec.get("left_unit_no", unit_no + "L")
            if left_amp:  l_attrs["FLA"] = left_amp
            mcc_unitdata.update_row(proj["unitdata_doc"], unit_rec["unitdata_handle"], l_attrs)
            # Update R row
            r_attrs = dict(field_updates)
            r_attrs["UNIT"] = unit_rec.get("right_unit_no", unit_no + "R")
            if right_amp: r_attrs["FLA"] = right_amp
            r_handle = unit_rec.get("unitdata_handle_right")
            if r_handle:
                mcc_unitdata.update_row(proj["unitdata_doc"], r_handle, r_attrs)
        else:
            field_updates["UNIT"] = _display_unit_no(unit_no)
            mcc_unitdata.update_row(proj["unitdata_doc"], unit_rec["unitdata_handle"], field_updates)
    except Exception as exc:
        sync_errors.append(f"unitdata update: {exc}")

    unit_rec.setdefault("fields", {}).update(field_updates)

    # ── Nameplate (LAMACOID) update ───────────────────────────────────────────
    # Only update fields that were provided (non-empty string).
    np_updates = {
        k: v for k, v in {
            "LINE.1": np_line1, "LINE.2": np_line2,
            "LINE.3": np_line3, "LINE.4": np_line4,
            "QTY": np_qty, "SIZE": np_size, "STYLE": np_style,
        }.items() if v
    }
    if np_updates and proj.get("nameplate_doc") is not None:
        try:
            from src.tools import mcc_nameplate
            np_handle = unit_rec.get("nameplate_handle")
            if np_handle:
                np_updates["UNIT"] = _display_unit_no(unit_no)
                mcc_nameplate.update_row(proj["nameplate_doc"], np_handle, np_updates)
                unit_rec.setdefault("nameplate_fields", {}).update(np_updates)
        except Exception as exc:
            sync_errors.append(f"nameplate update: {exc}")

    result: dict[str, Any] = {"success": True, "unit_no": unit_no}
    if sync_errors:
        result["sync_errors"] = sync_errors
    return result


def swap_units(
    project_id: str,
    unit_no_a: str,
    unit_no_b: str,
) -> dict[str, Any]:
    """Swap two units — attribute data only, physical positions unchanged.

    Parameters
    ----------
    project_id : str   Project ID.
    unit_no_a  : str   First unit e.g. "F1A".
    unit_no_b  : str   Second unit e.g. "F2A".
    """
    proj = _projects.get(project_id)
    if proj is None:
        return {"success": False, "error": f"Unknown project_id: {project_id}"}

    unit_a = proj["unit_index"].get(unit_no_a)
    unit_b = proj["unit_index"].get(unit_no_b)
    if unit_a is None:
        return {"success": False, "error": f"Unit '{unit_no_a}' not found"}
    if unit_b is None:
        return {"success": False, "error": f"Unit '{unit_no_b}' not found"}

    try:
        from src.tools import mcc_unitdata

        attrs_a = mcc_unitdata.read_row(proj["unitdata_doc"], unit_a["unitdata_handle"])
        attrs_b = mcc_unitdata.read_row(proj["unitdata_doc"], unit_b["unitdata_handle"])

        mcc_unitdata.update_row(proj["unitdata_doc"], unit_a["unitdata_handle"], attrs_b)
        mcc_unitdata.update_row(proj["unitdata_doc"], unit_b["unitdata_handle"], attrs_a)

        la = proj["layout_doc"].HandleToObject(unit_a["layout_handle"])
        lb = proj["layout_doc"].HandleToObject(unit_b["layout_handle"])
        _set_attrs(la, {"UNIT-NO.": unit_a["fields"].get("UNIT", unit_no_a),
                        "STARTER":  unit_a["fields"].get("TYPE", "")})
        _set_attrs(lb, {"UNIT-NO.": unit_b["fields"].get("UNIT", unit_no_b),
                        "STARTER":  unit_b["fields"].get("TYPE", "")})

        unit_a["fields"], unit_b["fields"] = attrs_b, attrs_a
        proj["unit_index"][unit_no_a] = unit_b
        proj["unit_index"][unit_no_b] = unit_a
        unit_b["unit_no"] = unit_no_a
        unit_a["unit_no"] = unit_no_b

        return {"success": True, "swapped": [unit_no_a, unit_no_b]}

    except Exception as exc:
        return {"success": False, "error": str(exc)}


def sync_layout(project_id: str) -> dict[str, Any]:
    """Trigger a full regeneration of both drawings (manual refresh tool).

    Useful after a session to ensure all display geometry is up to date.
    Not needed after add_section() / add_unit() — Update() handles those.
    """
    proj = _projects.get(project_id)
    if proj is None:
        return {"success": False, "error": f"Unknown project_id: {project_id}"}
    try:
        proj["layout_doc"].Regen(2)
        proj["unitdata_doc"].Regen(2)
        return {"success": True, "project_id": project_id}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def get_project_state(project_id: str) -> dict[str, Any]:
    """Return the full current state of a project."""
    proj = _projects.get(project_id)
    if proj is None:
        return {"success": False, "error": f"Unknown project_id: {project_id}"}

    sections_out = {}
    for sid, sec in proj["sections"].items():
        sections_out[sid] = {
            "section_width":  sec["section_width"],
            "origin_x":       sec["origin_x"],
            "origin_y":       sec["origin_y"],
            "used_mods":      sec["used_mods"],
            "capacity_mods":  sec["capacity_mods"],
            "remaining_mods": round(sec["capacity_mods"] - sec["used_mods"], 2),
            "units": [
                {
                    "unit_no":               u["unit_no"],
                    "mod_height":            u["mod_height"],
                    "layout_handle":         u["layout_handle"],
                    "unitdata_handle":       u["unitdata_handle"],
                    "fields":                u["fields"],
                    "starter_type":          u.get("starter_type", ""),
                    "tag":                   u.get("tag", ""),
                    "custom_width":          u.get("custom_width"),
                    # dual feeder extras
                    "left_unit_no":          u.get("left_unit_no"),
                    "right_unit_no":         u.get("right_unit_no"),
                    "left_amp":              u.get("left_amp"),
                    "right_amp":             u.get("right_amp"),
                    "unitdata_handle_right": u.get("unitdata_handle_right"),
                    # nameplate
                    "nameplate_handle":      u.get("nameplate_handle"),
                    "nameplate_fields":      u.get("nameplate_fields", {}),
                }
                for u in sec["units"]
            ],
        }

    return {
        "success":        True,
        "project_id":     project_id,
        "sections":       sections_out,
        "total_sections": len(proj["sections"]),
        "total_units":    len(proj["unit_index"]),
    }


def list_projects() -> dict[str, Any]:
    """List all active in-memory projects."""
    return {
        "success":  True,
        "projects": [
            {
                "project_id":     pid,
                "project_name":   p.get("project_name", ""),
                "total_sections": len(p["sections"]),
                "total_units":    len(p["unit_index"]),
            }
            for pid, p in _projects.items()
        ],
    }


def list_saved_projects(
    directory: str | None = None,
) -> dict[str, Any]:
    """Scan the projects folder and return metadata for every saved .json file.

    Parameters
    ----------
    directory : str | None
        Folder to scan.  Defaults to ``projects/`` relative to cwd.

    Returns
    -------
    dict with ``success`` and ``projects`` list, each entry having:
    ``filepath``, ``project_id``, ``project_name``, ``saved_at``,
    ``total_sections``, ``total_units``.
    """
    import json
    import os

    folder = directory or "projects"
    if not os.path.isdir(folder):
        return {"success": True, "projects": []}

    results = []
    for fname in sorted(os.listdir(folder)):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(folder, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            results.append({
                "filepath":       fpath,
                "project_id":     data.get("project_id", ""),
                "project_name":   data.get("project_name", ""),
                "saved_at":       data.get("saved_at", ""),
                "total_sections": len(data.get("sections", {})),
                "total_units":    len(data.get("unit_index", {})),
            })
        except Exception:
            # Skip unreadable files silently
            continue

    return {"success": True, "projects": results}


# ---------------------------------------------------------------------------
# Phase 2 — Project persistence  (save / load)
# ---------------------------------------------------------------------------

def save_project(
    project_id: str,
    filepath: str | None = None,
) -> dict[str, Any]:
    """Serialise a project to a JSON file.

    COM object references (AutoCAD documents) are excluded — only the pure
    data model is written.  All AutoCAD entity handles are kept as strings so
    the drawing can be reconnected later with load_project().

    Parameters
    ----------
    project_id : str
        The project to save (from new_mcc_project).
    filepath : str | None
        Destination path.  Defaults to ``projects/<project_id>.json`` relative
        to the current working directory.

    Returns
    -------
    dict with keys ``success``, ``filepath`` (on success) or ``error``.
    """
    import json
    import os
    from datetime import datetime, timezone

    proj = _projects.get(project_id)
    if proj is None:
        return {"success": False, "error": f"Unknown project_id: {project_id}"}

    if filepath is None:
        os.makedirs("projects", exist_ok=True)
        # Prefer a human-readable name; fall back to the UUID-fragment ID
        safe_name = proj.get("project_name", "").strip()
        safe_name = "".join(c for c in safe_name if c.isalnum() or c in " _-").strip()
        filename = f"{safe_name}.json" if safe_name else f"{project_id}.json"
        filepath = os.path.join("projects", filename)

    # Build serialisable section list (exclude COM objects, keep all data fields)
    _COM_KEYS = {"layout_doc", "unitdata_doc"}
    sections_out: dict[str, Any] = {}
    for sid, sec in proj["sections"].items():
        sections_out[sid] = {k: v for k, v in sec.items() if k not in _COM_KEYS}

    data: dict[str, Any] = {
        "project_id":       project_id,
        "project_name":     proj.get("project_name", ""),
        "saved_at":         datetime.now(timezone.utc).isoformat(),
        "layout_origin_x":  proj["section_cursor_x"] - sum(
            s["width_units"] for s in proj["sections"].values()
        ),   # recompute: cursor_x minus all section widths = original origin
        "layout_origin_y":  proj["layout_origin_y"],
        "first_unit_y":     proj["first_unit_y"],
        "unitdata_row_x":   proj["unitdata_row_x"],
        "unitdata_cursor_y_initial": proj.get("unitdata_row_y_initial",
                                               proj["unitdata_cursor_y"]),
        "section_cursor_x": proj["section_cursor_x"],
        "unitdata_cursor_y": proj["unitdata_cursor_y"],
        "row_height":              proj["row_height"],
        "unitdata_handle_order":   proj.get("unitdata_handle_order", []),
        "nameplate_row_x":         proj.get("nameplate_row_x", 25.0),
        "nameplate_cursor_y":      proj.get("nameplate_cursor_y", 235.0),
        "nameplate_row_y_initial": proj.get("nameplate_row_y_initial", 235.0),
        "nameplate_row_h":         proj.get("nameplate_row_h", 5),
        "nameplate_handle_order":  proj.get("nameplate_handle_order", []),
        "sections":                sections_out,
        # unit_index is derivable from sections; include for fast lookup
        "unit_index":       {
            uid: {k: v for k, v in unit.items()}
            for uid, unit in proj["unit_index"].items()
        },
    }

    try:
        with open(filepath, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
    except OSError as exc:
        return {"success": False, "error": str(exc)}

    return {"success": True, "filepath": filepath, "project_id": project_id}


def load_project(
    filepath: str,
    reconnect: bool = True,
) -> dict[str, Any]:
    """Load a project from a JSON file back into _projects.

    If ``reconnect=True`` (default) and AutoCAD is running with both
    MCC_LAYOUT and MCC_UNITDATA open, the project is immediately usable for
    further add_unit / update_unit calls.  If AutoCAD is not running (or
    reconnect=False), the project state is loaded into memory but
    ``layout_doc`` / ``unitdata_doc`` are set to ``None`` — useful for
    offline data inspection.

    Parameters
    ----------
    filepath : str
        Path to the ``.json`` file written by save_project.
    reconnect : bool
        Whether to attempt to bind to the running AutoCAD instance.

    Returns
    -------
    dict with keys ``success``, ``project_id`` (on success) or ``error``.
    """
    import json

    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            data: dict[str, Any] = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        return {"success": False, "error": f"Cannot read {filepath}: {exc}"}

    project_id = data.get("project_id")
    if not project_id:
        return {"success": False, "error": "File missing 'project_id'"}

    layout_doc    = None
    unitdata_doc  = None
    nameplate_doc = None

    if reconnect:
        try:
            acad          = _get_autocad()
            layout_doc    = _get_doc(acad, "MCC_LAYOUT")
            unitdata_doc  = _get_doc(acad, "MCC_UNITDATA")
            nameplate_doc = _get_doc(acad, "MCC_NAMEPLATE")
        except Exception as exc:
            # Non-fatal — project loads in offline mode
            reconnect = False

    # Rebuild the in-memory project dict
    proj: dict[str, Any] = {
        "project_name":            data.get("project_name", ""),
        "layout_doc":              layout_doc,
        "unitdata_doc":            unitdata_doc,
        "nameplate_doc":           nameplate_doc,
        "layout_origin_y":         data.get("layout_origin_y", 0.0),
        "first_unit_y":            data.get("first_unit_y", 224.0),
        "unitdata_row_x":          data.get("unitdata_row_x", 0.0),
        "unitdata_row_y_initial":  data.get("unitdata_cursor_y_initial", 0.0),
        "section_cursor_x":        data.get("section_cursor_x", 0.0),
        "unitdata_cursor_y":       data.get("unitdata_cursor_y", 0.0),
        "row_height":              data.get("row_height", 4),
        "unitdata_handle_order":   data.get("unitdata_handle_order", []),
        "nameplate_row_x":         data.get("nameplate_row_x", 25.0),
        "nameplate_cursor_y":      data.get("nameplate_cursor_y", 235.0),
        "nameplate_row_y_initial": data.get("nameplate_row_y_initial", 235.0),
        "nameplate_row_h":         data.get("nameplate_row_h", 5),
        "nameplate_handle_order":  data.get("nameplate_handle_order", []),
        "sections":                {},
        "unit_index":              {},
    }

    # Restore sections (wireway handles remain as strings — usable if reconnected)
    for sid, sec in data.get("sections", {}).items():
        proj["sections"][sid] = dict(sec)   # plain dict, no COM refs needed

    # Restore unit index
    for uid, unit in data.get("unit_index", {}).items():
        proj["unit_index"][uid] = dict(unit)

    _projects[project_id] = proj

    return {
        "success":      True,
        "project_id":   project_id,
        "project_name": proj.get("project_name", ""),
        "reconnected":  reconnect,
        "sections":     list(proj["sections"].keys()),
        "units":        list(proj["unit_index"].keys()),
    }
