# src/tools/mcc_unitdata.py
"""
MCC_UNITDATA manager — inserts and updates UDATALIN rows in MCC_UNITDATA.dwg.

This module ONLY touches MCC_UNITDATA.dwg and is called by mcc_layout.py.
It has its own isolated COM helpers and its own _set_attrs that NEVER calls
Update() or Regen — doing so corrupts UDATALIN column alignment.

Why no Update()?
  UDATALIN is pre-defined in the MCC_UNITDATA block table.  When InsertBlock
  inserts a UDATALIN row by name, AutoCAD already knows the attribute positions
  from the block definition and places them correctly.  Calling Update() on a
  UDATALIN attribute resets it to the block definition origin, shifting every
  column.  Calling Regen(2) has the same effect (and also regens MCC_LAYOUT,
  breaking centered text there too).  The only safe operation is to set
  TextString values and do nothing else.
"""

from __future__ import annotations

import math
from typing import Any

# ---------------------------------------------------------------------------
# UDATALIN field list (48 fields — order matches GetAttributes() return order)
# ---------------------------------------------------------------------------

UDATALIN_FIELDS = [
    "UNIT", "QTY", "HGT", "D/F", "TYPE", "SIZE", "HP/KW", "FLA",
    "FRAME", "TRIP", "SWITCH", "F.TYPE", "FUSE",
    "CONT-QTY", "CONTACTOR", "COIL",
    "OL-QTY", "OVERLOAD",
    "CCT", "CCT-FSEC", "CCT-FPRI",
    "CFUSE-QTY", "CFUSE",
    "STOP", "START", "PTC", "POS", "SS",
    "PL-RED", "PL-GRN", "PL-YEL", "PL-WHT",
    "TMR-QTY", "ON", "OFF",
    "CR-QTY", "NO", "NC", "PTC-AUX", "HOUR",
    "VOLTMETER", "AMMETER", "CT'S", "XMER-PH", "KVA",
    "PNL-PH", "CCT'S", "DRAWING",
]

# ---------------------------------------------------------------------------
# COM helpers  (unitdata document only)
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
    """Insert a block into doc.ModelSpace with retry on RPC_E_CALL_REJECTED.

    On rejection: sleep and retry WITHOUT re-calling _activate().
    Re-activating inside the retry loop causes additional tab-switches that
    trigger regens on the previously-active document, keeping AutoCAD busy
    in a feedback loop.  A plain sleep lets it drain its work queue first.
    """
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
                time.sleep(1.0 + attempt * 0.5)   # 1.0 s → 1.5 → 2.0 … → 10.5 s
            else:
                raise


def _set_attrs(ref, attrs: dict[str, str]) -> None:
    """Set attribute TextString values on a UDATALIN row.

    NEVER calls Update() or Regen.

    UDATALIN's attribute positions are initialised correctly by InsertBlock
    because the block is already defined in the drawing's block table.
    Any Update() call resets the attribute's position to the raw block-
    definition origin (column 0), shifting all text to the left edge.
    Any Regen(2) call has the same effect, and also regens MCC_LAYOUT.

    This function ONLY sets TextString and does nothing else.
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


# ---------------------------------------------------------------------------
# Public API  (called by mcc_layout.py)
# ---------------------------------------------------------------------------

def _activate(doc) -> None:
    """Activate doc and wait until AutoCAD is quiescent before returning.

    See mcc_layout._activate for full rationale.  Short version: InsertBlock
    only correctly initialises attribute positions on the active document, and
    polling IsQuiescent eliminates the RPC_E_CALL_REJECTED race condition.
    """
    import time
    try:
        doc.Activate()
    except Exception:
        return
    # Require 3 consecutive True readings before proceeding — a single True is
    # not sufficient as AutoCAD can flip back to busy between background bursts.
    try:
        acad   = doc.Application
        stable = 0
        for _ in range(200):          # up to 20 s
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


def insert_row(
    unitdata_doc,
    x: float,
    y: float,
    attrs: dict[str, str],
) -> dict[str, Any]:
    """Insert one UDATALIN row at (x, y) and populate its attributes.

    Parameters
    ----------
    unitdata_doc : AutoCAD document COM object for MCC_UNITDATA.
    x, y         : Insertion point in drawing units.
    attrs        : Tag → value mapping (keys are uppercased automatically).

    Returns
    -------
    dict with keys "success", "handle" (on success) or "error" (on failure).
    """
    try:
        _activate(unitdata_doc)   # must be active for correct attr init
        ref = _insert(unitdata_doc, "UDATALIN", x, y)
        _set_attrs(ref, attrs)
        return {"success": True, "handle": ref.Handle}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def update_row(
    unitdata_doc,
    handle: str,
    attrs: dict[str, str],
) -> None:
    """Update attribute values on an existing UDATALIN row by handle.

    Parameters
    ----------
    unitdata_doc : AutoCAD document COM object for MCC_UNITDATA.
    handle       : AutoCAD entity handle string (from insert_row result).
    attrs        : Tag → new value mapping.
    """
    ref = unitdata_doc.HandleToObject(handle)
    _set_attrs(ref, attrs)


def read_row(
    unitdata_doc,
    handle: str,
) -> dict[str, str]:
    """Read all attribute values from a UDATALIN row by handle.

    Parameters
    ----------
    unitdata_doc : AutoCAD document COM object for MCC_UNITDATA.
    handle       : AutoCAD entity handle string.

    Returns
    -------
    dict mapping TagString → TextString for every attribute on the row.
    """
    ref = unitdata_doc.HandleToObject(handle)
    return {a.TagString: a.TextString for a in ref.GetAttributes()}


def sync_unitdata(unitdata_doc) -> dict[str, Any]:
    """Regenerate MCC_UNITDATA (manual refresh, not needed after insert_row).

    Parameters
    ----------
    unitdata_doc : AutoCAD document COM object for MCC_UNITDATA.
    """
    try:
        unitdata_doc.Regen(2)
        return {"success": True}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
