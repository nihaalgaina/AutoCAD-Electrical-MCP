# src/tools/mcc_blocks.py
"""
MCC block insertion tools — COM-based AutoCAD automation.

All public functions are registered as MCP tools in server.py via server_patch.py.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Catalog loading
# ---------------------------------------------------------------------------

_CATALOG_PATH = Path(__file__).parent.parent.parent / "mcc_block_catalog.yaml"
_catalog_cache: dict | None = None


def _load_catalog() -> dict:
    global _catalog_cache
    if _catalog_cache is None:
        with open(_CATALOG_PATH, "r") as f:
            _catalog_cache = yaml.safe_load(f)
    return _catalog_cache


def _library_paths() -> list[str]:
    """Return the ordered list of block-library directories to search.

    Sources (merged in this order, duplicates removed):
      1. MCC_BLOCK_LIBRARY environment variable — semicolon-separated paths.
         This is the primary setting new users configure in their .env file.
      2. ``library_paths`` entries in mcc_block_catalog.yaml — fallback /
         additional paths.  Entries containing unresolved ``${VAR}``
         placeholders (i.e. the env var wasn't set) are silently skipped.
    """
    seen: set[str] = set()
    paths: list[str] = []

    def _add(p: str) -> None:
        p = p.strip()
        if p and "${" not in p and p not in seen:
            seen.add(p)
            paths.append(p)

    # 1. Environment variable (highest priority)
    env_val = os.environ.get("MCC_BLOCK_LIBRARY", "")
    for part in env_val.split(";"):
        _add(part)

    # 2. Catalog file entries (fallback)
    for entry in _load_catalog().get("library_paths", []):
        if entry:
            _add(str(entry))

    return paths


def _resolve_block_path(file_name: str) -> str | None:
    """Search library_paths for a .DWG file (case-insensitive).

    Returns the full path if found, or None if no library folder contains
    the file.  Skips library paths that don't exist on disk.
    """
    name_lower = file_name.lower()
    libs = _library_paths()
    if not libs:
        return None
    for lib in libs:
        try:
            for entry in os.scandir(lib):
                if entry.name.lower() == name_lower:
                    return entry.path
        except (FileNotFoundError, PermissionError):
            continue
    return None


# ---------------------------------------------------------------------------
# Unit block naming formula
# ---------------------------------------------------------------------------

_WIDTH_CODES: dict[int, str] = {
    400:  "40",  # 400mm  blocks: UNIT40XX  — 2-digit mod (e.g. UNIT4040, UNIT4035)
    500:  "5",   # 500mm  blocks: UNIT5XXX  — 3-digit mod (e.g. UNIT5040, UNIT5035)
    600:  "6",   # 600mm  blocks: UNIT6XXX  — 3-digit mod (e.g. UNIT6040, UNIT6035)
    800:  "8",   # 800mm  blocks: UNIT8XXX  — 3-digit mod (e.g. UNIT8040, UNIT8035)
    1000: "1",   # 1000mm blocks: UNIT1XXX  — 3-digit mod (e.g. UNIT1040, UNIT1035)
}


def resolve_unit_block_name(section_width: int, mod_height: float) -> str:
    """Return the block name for a unit given section width (mm) and mod height.

    Examples
    --------
    >>> resolve_unit_block_name(500, 8)
    'UNIT580'
    >>> resolve_unit_block_name(500, 12)
    'UNIT5120'
    >>> resolve_unit_block_name(1000, 4)
    'UNIT1040'
    >>> resolve_unit_block_name(400, 4)
    'UNIT4040'
    """
    width_code = _WIDTH_CODES.get(section_width)
    if width_code is None:
        raise ValueError(
            f"section_width {section_width} not in WIDTH_CODES. "
            f"Valid values: {list(_WIDTH_CODES)}"
        )
    mod_code_int = int(round(mod_height * 10))
    # 400 mm uses a 2-digit mod code (UNIT4040); all other widths use 3-digit
    # zero-padded (UNIT5040, UNIT6040, UNIT8040, UNIT1040).
    if section_width == 400:
        return f"UNIT{width_code}{mod_code_int}"
    return f"UNIT{width_code}{mod_code_int:03d}"


def decode_unit_block_name(block_name: str) -> dict[str, Any]:
    """Parse a UNIT block name back into section_width and mod_height."""
    _CODE_TO_WIDTH = {v: k for k, v in _WIDTH_CODES.items()}
    name = block_name.upper()
    if not name.startswith("UNIT"):
        raise ValueError(f"Not a unit block name: {block_name}")
    rest = name[4:]
    for code, width in sorted(_CODE_TO_WIDTH.items(), key=lambda x: -len(x[0])):
        if rest.startswith(code):
            mod_str = rest[len(code):]
            mod_height = int(mod_str) / 10.0
            return {"section_width": width, "mod_height": mod_height, "block_name": block_name}
    raise ValueError(f"Cannot decode block name: {block_name}")


# ---------------------------------------------------------------------------
# COM helpers
# ---------------------------------------------------------------------------

def _get_autocad():
    """Return the running AutoCAD Application COM object."""
    try:
        import win32com.client as win32
        return win32.GetActiveObject("AutoCAD.Application")
    except Exception as exc:
        raise RuntimeError(
            "Could not connect to AutoCAD. Make sure AutoCAD is running "
            f"with a drawing open. Detail: {exc}"
        ) from exc


def _insert_block_com(ms, path: str, x: float, y: float,
                      x_scale: float = 1.0, y_scale: float = 1.0,
                      rotation_deg: float = 0.0) -> Any:
    """Low-level COM InsertBlock call."""
    import pythoncom
    import win32com.client as win32
    point3d = win32.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [x, y, 0.0])
    return ms.InsertBlock(point3d, path, x_scale, y_scale, 1.0, math.radians(rotation_deg))


def _set_attributes(block_ref, attributes: dict[str, str]) -> None:
    """Set attribute values on an inserted block reference."""
    if not attributes:
        return
    try:
        for attrib in block_ref.GetAttributes():
            tag = attrib.TagString.upper()
            for k, v in attributes.items():
                if k.upper() == tag:
                    attrib.TextString = str(v)
                    break
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Public tool functions
# ---------------------------------------------------------------------------

def insert_block(
    block_name: str,
    x: float,
    y: float,
    x_scale: float = 1.0,
    y_scale: float = 1.0,
    rotation: float = 0.0,
    attributes: dict[str, str] | None = None,
    layer: str | None = None,
) -> dict[str, Any]:
    """Insert any named block from the company library at (x, y)."""
    try:
        acad = _get_autocad()
        ms = acad.ActiveDocument.ModelSpace

        catalog = _load_catalog()
        path = block_name
        if not os.path.isabs(block_name):
            block_entry = catalog.get("blocks", {}).get(block_name)
            if block_entry:
                resolved = _resolve_block_path(block_entry["file_path"])
                if resolved:
                    path = resolved
            else:
                resolved = _resolve_block_path(block_name + ".DWG")
                if resolved:
                    path = resolved

        ref = _insert_block_com(ms, path, x, y, x_scale, y_scale, rotation)
        if attributes:
            _set_attributes(ref, attributes)
        if layer:
            ref.Layer = layer

        return {"success": True, "handle": ref.Handle, "block_name": block_name, "x": x, "y": y}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def insert_mcc_section(
    section_width: int,
    x: float,
    y: float,
    section_id: str = "",
    variant: str | None = None,
    rotation: float = 0.0,
) -> dict[str, Any]:
    """Insert an MCC section frame block."""
    catalog = _load_catalog()
    entry = catalog.get("mcc_sections", {}).get(section_width)
    if entry is None:
        return {"success": False, "error": f"No section defined for width {section_width}mm"}

    if variant:
        var_entry = entry.get("variants", {}).get(variant)
        if var_entry is None:
            return {"success": False, "error": f"Variant '{variant}' not found for {section_width}mm"}
        file_path = var_entry["file_path"]
        block_name = variant
    else:
        file_path = entry["file_path"]
        block_name = entry["block_name"]

    path = _resolve_block_path(file_path)
    if path is None:
        return {"success": False, "error": f"Block file not found: {file_path}"}

    attrs = {"SECTION": section_id} if section_id else {}

    try:
        acad = _get_autocad()
        ms = acad.ActiveDocument.ModelSpace
        ref = _insert_block_com(ms, path, x, y, 1.0, 1.0, rotation)
        if attrs:
            _set_attributes(ref, attrs)
        return {
            "success": True, "handle": ref.Handle, "block_name": block_name,
            "section_width": section_width, "section_id": section_id, "x": x, "y": y,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def insert_unit(
    section_width: int,
    mod_height: float,
    x: float,
    y: float,
    unit_no: str = "",
    starter_type: str = "",
    equip_no: str = "",
    rotation: float = 0.0,
) -> dict[str, Any]:
    """Insert a unit block. Block name resolved from section_width + mod_height."""
    try:
        block_name = resolve_unit_block_name(section_width, mod_height)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    catalog = _load_catalog()
    unit_entry = catalog.get("blocks", {}).get(block_name)
    file_path = unit_entry["file_path"] if unit_entry else f"{block_name}.DWG"

    path = _resolve_block_path(file_path)
    if path is None:
        return {"success": False, "error": f"Block file not found: {file_path}"}

    attrs: dict[str, str] = {}
    if unit_no:
        attrs["UNIT-NO."] = unit_no
    if starter_type:
        attrs["STARTER"] = starter_type
    attrs["EQUIP-NO."] = equip_no if equip_no else str(mod_height)

    try:
        acad = _get_autocad()
        ms = acad.ActiveDocument.ModelSpace
        ref = _insert_block_com(ms, path, x, y, 1.0, 1.0, rotation)
        if attrs:
            _set_attributes(ref, attrs)
        return {
            "success": True, "handle": ref.Handle, "block_name": block_name,
            "section_width": section_width, "mod_height": mod_height,
            "unit_no": unit_no, "starter_type": starter_type, "x": x, "y": y,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def list_mcc_blocks() -> dict[str, Any]:
    """Return all MCC section and unit blocks from the catalog."""
    catalog = _load_catalog()
    sections = catalog.get("mcc_sections", {})
    unit_cfg = catalog.get("unit_blocks", {})

    section_summary = {
        w: {
            "block_name": e["block_name"],
            "file_path": e["file_path"],
            "mod_height": e.get("mod_height"),
            "variants": list(e.get("variants", {}).keys()),
        }
        for w, e in sections.items()
    }
    unit_summary = {
        w: {
            "width_code": unit_cfg.get(w, {}).get("width_code"),
            "filler_mods": unit_cfg.get(w, {}).get("filler_mods", []),
            "unit_mods": unit_cfg.get(w, {}).get("unit_mods", []),
        }
        for w in [400, 500, 600, 800, 1000]
    }
    return {"sections": section_summary, "unit_config": unit_summary, "library_paths": _library_paths()}


def _get_entity(doc, handle: str):
    """Look up an entity by its handle using HandleToObject (fast, direct)."""
    try:
        return doc.HandleToObject(handle)
    except Exception:
        return None


def read_block_attributes(handle: str) -> dict[str, Any]:
    """Read all attributes from a block in the drawing by its AutoCAD handle."""
    try:
        acad = _get_autocad()
        doc = acad.ActiveDocument
        ref = _get_entity(doc, handle)
        if ref is None:
            return {"success": False, "error": f"No entity with handle {handle}"}
        attrs = {}
        try:
            for attrib in ref.GetAttributes():
                attrs[attrib.TagString] = attrib.TextString
        except Exception:
            pass
        return {"success": True, "handle": handle, "attributes": attrs}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def update_block_attributes(handle: str, attributes: dict[str, str]) -> dict[str, Any]:
    """Update attribute values on an already-inserted block by its handle."""
    try:
        acad = _get_autocad()
        doc = acad.ActiveDocument
        ref = _get_entity(doc, handle)
        if ref is None:
            return {"success": False, "error": f"No entity with handle {handle}"}
        updated = {}
        try:
            for attrib in ref.GetAttributes():
                tag = attrib.TagString.upper()
                for k, v in attributes.items():
                    if k.upper() == tag:
                        attrib.TextString = str(v)
                        updated[attrib.TagString] = v
                        break
        except Exception as exc:
            return {"success": False, "error": f"Attribute update failed: {exc}"}
        doc.Regen(1)
        return {"success": True, "handle": handle, "updated": updated}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
