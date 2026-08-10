# src/tools/mcc_nameplate.py
"""
MCC_NAMEPLATE manager — inserts and updates LAMACOID rows in MCC_NAMEPLATE.dwg.

This module ONLY touches MCC_NAMEPLATE.dwg and is called by mcc_layout.py.
It mirrors the design of mcc_unitdata.py: isolated COM helpers, no Update()
calls on attributes (same risk of position corruption as UDATALIN).

Row layout
----------
- First row inserted at (25, 235, 0) in MCC_NAMEPLATE.dwg.
- Each subsequent row is 5 drawing units lower (Y decrements by 5).
- One LAMACOID row per unit regardless of type (including DUAL_FEEDER).

LAMACOID attributes
-------------------
  UNIT    — unit number (same value as UDATALIN UNIT column)
  LINE1   — nameplate line 1
  LINE2   — nameplate line 2
  LINE3   — nameplate line 3
  LINE4   — nameplate line 4
  QTY     — quantity
  SIZE    — nameplate size / legend
  STYLE   — nameplate style code
"""

from __future__ import annotations

import math
from typing import Any

# ---------------------------------------------------------------------------
# Field list
# ---------------------------------------------------------------------------

LAMACOID_FIELDS = ["UNIT", "LINE.1", "LINE.2", "LINE.3", "LINE.4", "QTY", "SIZE", "STYLE"]

# ---------------------------------------------------------------------------
# COM helpers  (nameplate document only)
# ---------------------------------------------------------------------------

def _is_rpc_rejected(exc: Exception) -> bool:
    """Return True if exc is RPC_E_CALL_REJECTED (0x80010001 / -2147418111)."""
    try:
        if exc.args and exc.args[0] in (-2147418111, 0x80010001):
            return True
    except Exception:
        pass
    s = str(exc)
    return "80010001" in s or "-2147418111" in s


def _insert(doc, block_name_or_path: str, x: float, y: float,
            x_scale: float = 1.0, y_scale: float = 1.0,
            rotation_deg: float = 0.0):
    """Insert a block into doc.ModelSpace with retry on RPC_E_CALL_REJECTED."""
    import time
    import win32com.client as win32
    import pythoncom
    ms  = doc.ModelSpace
    pt  = win32.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [x, y, 0.0])
    for attempt in range(20):
        try:
            return ms.InsertBlock(pt, block_name_or_path,
                                  x_scale, y_scale, 1.0,
                                  math.radians(rotation_deg))
        except Exception as exc:
            if _is_rpc_rejected(exc) and attempt < 19:
                time.sleep(1.0 + attempt * 0.5)
            else:
                raise


def _norm_tag(s: str) -> str:
    """Normalise a tag string for comparison — upper-case and strip non-alphanumeric chars.

    AutoCAD may encode the dot in "LINE.1" differently (or omit it in the COM
    TagString), so stripping non-alphanumeric chars lets "LINE.1", "LINE1",
    "line.1" all resolve to "LINE1" and match each other.
    """
    return "".join(c for c in s.upper() if c.isalnum())


def _set_attrs(ref, attrs: dict[str, str]) -> None:
    """Set attribute TextString values on a LAMACOID row.

    Strategy (two passes):
      1. Tag-name pass  — iterate every returned attribute and match it against
         *attrs* by normalised tag name (dots and special chars stripped).
         This handles LINE.1-4 regardless of how AutoCAD encodes the dot.
      2. Positional pass — for any field not matched in pass 1, fall back to the
         canonical block-definition index order.  This catches QTY/STYLE/SIZE if
         GetAttributes() returns fewer items than the full 8 (e.g. only returns
         the tag-prompted attributes).

    Does NOT call Update() or Regen.
    """
    if not attrs:
        return

    # Canonical positional order (block definition attribute order).
    _ORDER = ["UNIT", "LINE.1", "LINE.2", "LINE.3", "LINE.4", "QTY", "STYLE", "SIZE"]

    # Build normalised lookup keyed by stripped name.
    attrs_norm      = {k.strip().upper(): v  for k, v in attrs.items()}  # exact key
    attrs_norm_strip = {_norm_tag(k): v       for k, v in attrs.items()}  # stripped key

    try:
        attrib_list = list(ref.GetAttributes())
    except Exception:
        return

    written: set[str] = set()   # track which _ORDER fields were written in pass 1

    # ── Pass 1: tag-name matching ──────────────────────────────────────────────
    for attrib in attrib_list:
        try:
            tag = attrib.TagString.strip().upper()
        except Exception:
            continue
        # Try exact key first, then normalised (dot-stripped) key.
        val = attrs_norm.get(tag)
        if val is None:
            val = attrs_norm_strip.get(_norm_tag(tag))
        if val is not None:
            try:
                attrib.TextString = str(val)
            except Exception:
                pass
            # Mark all _ORDER fields whose normalised name matches this tag.
            for field in _ORDER:
                if _norm_tag(field) == _norm_tag(tag):
                    written.add(field)

    # ── Pass 2: positional fallback for any unmatched fields ──────────────────
    for i, field in enumerate(_ORDER):
        if field in written:
            continue          # already handled by pass 1
        if i >= len(attrib_list):
            break             # no more attributes available
        val = attrs_norm.get(field.upper())
        if val is None:
            val = attrs_norm_strip.get(_norm_tag(field))
        if val is not None:
            try:
                attrib_list[i].TextString = str(val)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Activate helper
# ---------------------------------------------------------------------------

def _activate(doc) -> None:
    """Activate doc and wait until AutoCAD is quiescent."""
    import time
    try:
        doc.Activate()
    except Exception:
        return
    try:
        acad   = doc.Application
        stable = 0
        for _ in range(200):
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


# ---------------------------------------------------------------------------
# Public API  (called by mcc_layout.py)
# ---------------------------------------------------------------------------

def insert_row(
    nameplate_doc,
    x: float,
    y: float,
    attrs: dict[str, str],
) -> dict[str, Any]:
    """Insert one LAMACOID row at (x, y) and populate its attributes.

    Parameters
    ----------
    nameplate_doc : AutoCAD document COM object for MCC_NAMEPLATE.
    x, y          : Insertion point in drawing units.
    attrs         : Tag → value mapping (keys are uppercased automatically).

    Returns
    -------
    dict with keys "success", "handle" (on success) or "error" (on failure).
    """
    try:
        _activate(nameplate_doc)
        ref = _insert(nameplate_doc, "LAMACOID", x, y)
        _set_attrs(ref, attrs)
        return {"success": True, "handle": ref.Handle}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def update_row(
    nameplate_doc,
    handle: str,
    attrs: dict[str, str],
) -> None:
    """Update attribute values on an existing LAMACOID row by handle."""
    ref = nameplate_doc.HandleToObject(handle)
    _set_attrs(ref, attrs)


def read_row(
    nameplate_doc,
    handle: str,
) -> dict[str, str]:
    """Read all attribute values from a LAMACOID row by handle."""
    ref = nameplate_doc.HandleToObject(handle)
    return {a.TagString: a.TextString for a in ref.GetAttributes()}
