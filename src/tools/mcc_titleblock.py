"""
mcc_titleblock.py — Read/write the TITLE3 titleblock block.

The TITLE3 block appears in the bottom-right corner of every MCC drawing.
Attributes are accessed by index (GetAttributes) because several tag names
are duplicated (CUSTOMER, PROJECT.NAME, REVISION, etc.).

Attribute index order
---------------------
Run ``scripts/dump_title3.py`` to verify these against your actual block.
The order listed here matches the typical TITLE3 definition sequence:

  Idx  Logical key      AutoCAD prompt
  ---  -----------      --------------
   0   DATE             ENTER DATE DRAWN
   1   CUSTOMER_1       ENTER CUSTOMER NAME 1
   2   CUSTOMER_2       ENTER CUSTOMER NAME 2
   3   ORDER_NO         ENTER ORDER NO. & SEQ. NO.
   4   BY               ENTER YOUR INITIALS
   5   DRAWING_NO       ENTER DRAWING NUMBER
   6   PROJECT_1        ENTER PROJECT NAME LINE 1
   7   PROJECT_2        ENTER PROJECT NAME LINE 2
   8   REV_NO           ENTER REVISION
   9   REV_A_DESC       ENTER REVISION DESCRIPTION  (row A)
  10   REV_A_BY         ENTER INITIALS FOR REVISION (row A)
  11   REV_A_DATE       ENTER REVISION DATE         (row A)
  12   REV_A_LETTER     ENTER REVISION LETTER       (row A)
  13   REV_B_LETTER     ENTER REVISION LETTER       (row B — letter defined before description)
  14   REV_B_DESC       ENTER REVISION DESCRIPTION  (row B)
  15   REV_B_BY         ENTER INITIALS FOR REVISION (row B)
  16   REV_B_DATE       ENTER REVISION DATE         (row B)
  17   REV_C_LETTER     ENTER REVISION LETTER       (row C)
  18   REV_C_DESC       ENTER REVISION DESCRIPTION  (row C)
  19   REV_C_BY         ENTER INITIALS FOR REVISION (row C)
  20   REV_C_DATE       ENTER REVISION DATE         (row C)

If your block has a different count or order, update ATTDEF_ORDER below and
re-run the dump script to confirm.
"""

from __future__ import annotations
from typing import Any

BLOCK_NAME = "TITLE3"

ATTDEF_ORDER: list[str] = [
    "DATE",          # 0
    "CUSTOMER_1",    # 1
    "CUSTOMER_2",    # 2
    "ORDER_NO",      # 3
    "BY",            # 4
    "DRAWING_NO",    # 5
    "PROJECT_1",     # 6
    "PROJECT_2",     # 7
    "REV_NO",        # 8
    "REV_A_DESC",    # 9
    "REV_A_BY",      # 10
    "REV_A_DATE",    # 11
    "REV_A_LETTER",  # 12
    "REV_B_LETTER",  # 13
    "REV_B_DESC",    # 14
    "REV_B_BY",      # 15
    "REV_B_DATE",    # 16
    "REV_C_LETTER",  # 17
    "REV_C_DESC",    # 18
    "REV_C_BY",      # 19
    "REV_C_DATE",    # 20
]

# ---------------------------------------------------------------------------
# COM helpers
# ---------------------------------------------------------------------------

_RPC_BUSY_CODES = frozenset({
    -2147418111, 0x80010001,
    -2147417846, 0x8001010A,
    -2147418108, 0x80010004,
})


def _is_rpc_busy(exc: Exception) -> bool:
    args = getattr(exc, "args", ())
    return bool(args) and isinstance(args[0], int) and args[0] in _RPC_BUSY_CODES


def _com_call(fn, *args, max_retries: int = 20, base_delay: float = 0.3):
    import time
    delay = base_delay
    last_exc = None
    for _ in range(max_retries):
        try:
            return fn(*args)
        except Exception as exc:
            if _is_rpc_busy(exc):
                last_exc = exc
                time.sleep(delay)
                delay = min(delay * 1.7, 8.0)
                continue
            raise
    raise last_exc


def _get_autocad():
    try:
        import win32com.client as win32
        return win32.GetActiveObject("AutoCAD.Application")
    except Exception:
        raise RuntimeError(
            "Cannot connect to AutoCAD. "
            "Please start AutoCAD Electrical and try again."
        )


def _get_doc_by_name(acad, filename: str):
    import os
    target = os.path.basename(filename).upper()
    docs = acad.Documents
    try:
        count = docs.Count
    except Exception:
        count = 0
    for i in range(count):
        try:
            d = docs.Item(i)
            if os.path.basename(d.Name).upper() == target:
                return d
        except Exception:
            continue
    raise RuntimeError(
        f"'{filename}' is not open in AutoCAD. "
        f"Please open it or use Reassign Drawings to update the mapping."
    )


def _get_doc_by_fragment(acad, fragment: str):
    frag = fragment.upper()
    docs = acad.Documents
    try:
        count = docs.Count
    except Exception:
        count = 0
    for i in range(count):
        try:
            d = docs.Item(i)
            if frag in d.Name.upper():
                return d
        except Exception:
            continue
    raise RuntimeError(f"No open drawing matching '{fragment}' found in AutoCAD.")


def _resolve_doc(acad, project_id: str | None, role: str):
    """Get the document for a given role from the project's dwg_map."""
    if project_id:
        try:
            from src.tools.mcc_layout import _projects
            proj = _projects.get(project_id)
            if proj:
                name = (proj.get("dwg_map") or {}).get(role, "")
                if name:
                    return _get_doc_by_name(acad, name)
        except Exception:
            pass
    raise RuntimeError(
        f"No drawing assigned to role '{role}' in this project. "
        f"Check your project's dwg_map via Reassign Drawings."
    )


def _find_title3(doc):
    """Find the first TITLE3 block reference in model space."""
    ms = _com_call(lambda: doc.ModelSpace)
    count = _com_call(lambda: ms.Count)
    for i in range(count):
        try:
            raw = ms.Item(i)
            h   = raw.Handle
            ent = _com_call(lambda: doc.HandleToObject(h))
            if (
                _com_call(lambda: ent.EntityName) == "AcDbBlockReference"
                and _com_call(lambda: ent.Name) == BLOCK_NAME
            ):
                return ent
        except Exception:
            continue
    raise RuntimeError(
        f"Block '{BLOCK_NAME}' not found in model space of {doc.Name}. "
        f"Please insert it and try again."
    )


def _wait_for_autocad(doc, timeout: float = 30.0) -> None:
    import time
    try:
        acad   = doc.Application
        stable = 0
        end    = time.time() + timeout
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
    import time as _t
    _t.sleep(0.5)


# ---------------------------------------------------------------------------
# Public tools
# ---------------------------------------------------------------------------

def get_titleblock(
    project_id: str,
    role: str = "layout",
) -> dict[str, Any]:
    """Read the TITLE3 block attributes from one drawing.

    Parameters
    ----------
    project_id : str
        Active project ID (used to resolve which drawing to read from).
    role : str
        Which drawing to read — one of ``layout``, ``unitdata``,
        ``nameplate``, ``general_data``.

    Returns
    -------
    dict with ``success`` and ``fields`` ({logical_key: value}).
    """
    try:
        acad = _get_autocad()
        doc  = _resolve_doc(acad, project_id, role)
        ref  = _find_title3(doc)

        attribs = list(_com_call(ref.GetAttributes))
        fields: dict[str, str] = {}
        for idx, key in enumerate(ATTDEF_ORDER):
            if idx < len(attribs):
                fields[key] = _com_call(lambda a=attribs[idx]: a.TextString)
            else:
                fields[key] = ""

        return {"success": True, "fields": fields, "doc": doc.Name}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def set_titleblock(
    fields:     dict[str, str],
    project_id: str,
    targets:    list[str] | None = None,
) -> dict[str, Any]:
    """Write titleblock fields to one or more drawings.

    Parameters
    ----------
    fields : dict
        ``{logical_key: value}`` — only keys present are updated.
        Keys must be from ``ATTDEF_ORDER``.
    project_id : str
        Active project ID.
    targets : list[str] | None
        Which drawing roles to update, e.g. ``["layout", "unitdata",
        "nameplate"]``.  Defaults to all four roles.

    Returns
    -------
    dict with ``success``, ``updated`` (list of doc names written),
    ``skipped`` (roles not found / no TITLE3 block), and any ``errors``.
    """
    import time

    if targets is None:
        targets = ["layout", "unitdata", "nameplate", "general_data"]

    # Build index→value map (only fields that are provided)
    key_to_idx = {k: i for i, k in enumerate(ATTDEF_ORDER)}
    idx_vals: dict[int, str] = {}
    for key, val in fields.items():
        if key in key_to_idx:
            idx_vals[key_to_idx[key]] = val

    if not idx_vals:
        return {"success": False, "error": "No valid field keys supplied."}

    try:
        acad = _get_autocad()
    except Exception as exc:
        return {"success": False, "error": str(exc)}

    updated: list[str] = []
    skipped: list[str] = []
    errors:  list[str] = []

    for role in targets:
        try:
            doc = _resolve_doc(acad, project_id, role)
        except Exception as exc:
            skipped.append(f"{role}: {exc}")
            continue

        try:
            ref = _find_title3(doc)
        except Exception as exc:
            skipped.append(f"{role} ({doc.Name}): {exc}")
            continue

        try:
            # Must activate the document before writing — background docs
            # return stale GetAttributes() snapshots and silently drop writes.
            _com_call(lambda: doc.Activate())
            _wait_for_autocad(doc)

            # Re-find the block reference after activation (handle is stable,
            # but re-fetching ensures we have a live COM proxy for this doc).
            ref = _find_title3(doc)
            attribs = list(_com_call(ref.GetAttributes))
            for idx, val in idx_vals.items():
                if idx < len(attribs):
                    attr = attribs[idx]
                    _com_call(lambda a=attr, v=val: setattr(a, "TextString", v))
                    _com_call(lambda a=attr: a.Update())
                    time.sleep(0.08)
            updated.append(doc.Name)
        except Exception as exc:
            errors.append(f"{role} ({doc.Name}): {exc}")

    return {
        "success": len(updated) > 0,
        "updated": updated,
        "skipped": skipped,
        "errors":  errors,
    }
