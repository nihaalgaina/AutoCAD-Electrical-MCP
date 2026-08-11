# src/tools/mcc_general_data.py
"""
General Data sheet automation — reads and writes the GENRA001B block in
"General_Data Sheet.dwg".

Two mechanisms:
  Text attributes  — set via GetAttributes() / TextString (46 attdefs)
  Checkbox squares — SOLID hatch inserted at the box position to "check",
                     hatch deleted to "uncheck".  Each hatch is tagged
                     with XData ("MCC_GENDATA", checkbox_id) so it can be
                     found and removed reliably.

Coordinate system
-----------------
All checkbox positions below are in BLOCK-LOCAL space (from genra001b_dump.json).
Y values are negative (standard CAD top-to-bottom convention).
When the block is inserted at (ins_x, ins_y) with scale/rotation, we transform
them to model space before inserting hatches.
"""
from __future__ import annotations

import math
from typing import Any

BLOCK_NAME   = "GENRA001B"
DOC_FRAGMENT = "General_Data Sheet"

# ---------------------------------------------------------------------------
# Checkbox coordinate map
# (xmin, ymin, xmax, ymax) — all in BLOCK-LOCAL space
# Derived from genra001b_dump.json.  All squares are 3×3 drawing units.
# ---------------------------------------------------------------------------

# fmt: off
CHECKBOXES: dict[str, tuple[float, float, float, float]] = {

    # ── EEMAC WIRING ─────────────────────────────────────────────────────────
    "EEMAC_IA":           ( 60.0,  -8.75,  63.0,  -5.75),
    "EEMAC_IB":           ( 75.0,  -8.75,  78.0,  -5.75),
    "EEMAC_IC":           ( 90.0,  -8.75,  93.0,  -5.75),
    "EEMAC_IIB":          (105.0,  -8.75, 108.0,  -5.75),
    "EEMAC_IIC":          (120.0,  -8.75, 123.0,  -5.75),
    "EEMAC_MODIFIED":     (135.0,  -8.75, 138.0,  -5.75),   # WIRING attdef = "MODIFIED"

    # ── ENCLOSURE EEMAC ───────────────────────────────────────────────────────
    "ENCL_1":             ( 60.0, -13.75,  63.0, -10.75),
    "ENCL_1A":            ( 75.0, -13.75,  78.0, -10.75),
    "ENCL_12":            ( 90.0, -13.75,  93.0, -10.75),
    "ENCL_2":             (105.0, -13.75, 108.0, -10.75),
    "ENCL_SPRINKLER":     (120.0, -13.75, 123.0, -10.75),   # ENCLOSURE attdef = "SPRINKLERPROOF"

    # ── ENCLOSURE FINISH ──────────────────────────────────────────────────────
    "FINISH_ASA61GREY":   ( 60.0, -18.75,  63.0, -15.75),
    "FINISH_SAND_ENAMEL": ( 95.0, -18.75,  98.0, -15.75),   # FINISH attdef = "SAND ENAMEL HS544H75"

    # ── ARRANGEMENT ───────────────────────────────────────────────────────────
    "ARRANGE_FOB":        ( 60.0, -23.75,  63.0, -20.75),
    "ARRANGE_BTB":        ( 80.0, -23.75,  83.0, -20.75),
    "ARRANGE_CUSTOM":     (100.0, -23.75, 103.0, -20.75),   # ARRANGEMENT attdef

    # ── MASTER TERMINAL BRD ───────────────────────────────────────────────────
    "TERMBD_TOP":         ( 60.0, -28.75,  63.0, -25.75),
    "TERMBD_BOT":         ( 80.0, -28.75,  83.0, -25.75),
    "TERMBD_CUSTOM":      (105.0, -28.75, 108.0, -25.75),   # TERMINALBOARD attdef

    # ── MAIN LUG / MAIN BREAKER (new in GENRA001B) ───────────────────────────
    "MAIN_LUG":           ( 60.0, -33.75,  63.0, -30.75),
    "MAIN_BREAKER":       (105.0, -33.75, 108.0, -30.75),
    "MAIN_CUSTOM":        (145.0, -33.75, 148.0, -30.75),   # MAIN_KA attdef for KA rating

    # ── SYSTEM ISC (new in GENRA001B) ────────────────────────────────────────
    "ISC_18KA":           ( 60.0, -38.75,  63.0, -35.75),
    "ISC_22KA":           ( 78.0, -38.75,  81.0, -35.75),
    "ISC_25KA":           ( 95.0, -38.75,  98.0, -35.75),
    "ISC_35KA":           (113.0, -38.75, 116.0, -35.75),
    "ISC_42KA":           (130.0, -38.75, 133.0, -35.75),
    "ISC_50KA":           (147.0, -38.75, 150.0, -35.75),
    "ISC_65KA":           (165.0, -38.75, 168.0, -35.75),

    # ── CSA C22.2 LABEL (new in GENRA001B) ───────────────────────────────────
    "CSA_STRUCT_UNIT":    ( 60.0,  -43.75,  63.0,  -40.75),
    "CSA_WHERE_APPLIC":   (106.625,-43.75, 109.625,-40.75),
    "CSA_SPECIAL":        (152.625,-43.75, 155.625,-40.75),

    # ── CABLE 1 ───────────────────────────────────────────────────────────────
    "CABLE1_TOP":         ( 80.0, -48.75,  83.0, -45.75),
    "CABLE1_BOT":         ( 95.0, -48.75,  98.0, -45.75),

    # ── NEUTRAL CABLE ─────────────────────────────────────────────────────────
    "NEUTRAL_TOP":        ( 80.0, -58.75,  83.0, -55.75),
    "NEUTRAL_BOT":        ( 95.0, -58.75,  98.0, -55.75),

    # ── CABLE 2 ───────────────────────────────────────────────────────────────
    "CABLE2_TOP":         ( 80.0, -68.75,  83.0, -65.75),
    "CABLE2_BOT":         ( 95.0, -68.75,  98.0, -65.75),

    # ── BUSDUCT ───────────────────────────────────────────────────────────────
    "BUSDUCT_TOP":        ( 80.0, -78.75,  83.0, -75.75),
    "BUSDUCT_BOT":        ( 95.0, -78.75,  98.0, -75.75),
    "BUSDUCT_3W":         (145.0, -78.75, 148.0, -75.75),
    "BUSDUCT_4W":         (160.0, -78.75, 163.0, -75.75),

    # ── BUSBAR BRACING ────────────────────────────────────────────────────────
    "BRACE_22KA":         ( 60.0, -83.75,  63.0, -80.75),
    "BRACE_42KA":         ( 90.0, -83.75,  93.0, -80.75),
    "BRACE_65KA":         (120.0, -83.75, 123.0, -80.75),

    # ── HORIZONTAL TYPE ───────────────────────────────────────────────────────
    "HORIZ_AL":           ( 60.0, -88.75,  63.0, -85.75),
    "HORIZ_CU":           ( 80.0, -88.75,  83.0, -85.75),
    "PLATING_TIN":        (125.0, -88.75, 128.0, -85.75),
    "PLATING_SILVER":     (140.0, -88.75, 143.0, -85.75),

    # ── INSULATED BUS ─────────────────────────────────────────────────────────
    "INSBUS_HORIZ":       ( 60.0, -113.75,  63.0, -110.75),
    "INSBUS_VERT":        (110.0, -113.75, 113.0, -110.75),

    # ── GROUND SIZE ───────────────────────────────────────────────────────────
    "GROUND_TOP":         (100.0, -118.75, 103.0, -115.75),
    "GROUND_BOT":         (120.0, -118.75, 123.0, -115.75),
    "GROUND_VERT":        (150.0, -118.75, 153.0, -115.75),

    # ── DISCONNECT DEVICE ─────────────────────────────────────────────────────
    "DISC_FUSIBLE":       ( 60.0, -123.75,  63.0, -120.75),
    "DISC_BREAKER":       (110.0, -123.75, 113.0, -120.75),
    "DISC_KA":            (155.0, -123.75, 158.0, -120.75),   # KA attdef (idx 11)

    # ── MOTOR FUSE TYPE ───────────────────────────────────────────────────────
    "MFUSE_FR2_C":        ( 60.0, -128.75,  63.0, -125.75),
    "MFUSE_FRI_J":        ( 95.0, -128.75,  98.0, -125.75),
    "MFUSE_FRI_JT":       (130.0, -128.75, 133.0, -125.75),   # MOTOR_FUSE attdef = "J(T)"

    # ── FEEDER FUSE TYPE ──────────────────────────────────────────────────────
    "FFUSE_FR2_C":        ( 60.0, -133.75,  63.0, -130.75),
    "FFUSE_FRI_J":        ( 95.0, -133.75,  98.0, -130.75),
    "FFUSE_FRI_JT":       (130.0, -133.75, 133.0, -130.75),   # FEEDER_FUSE attdef = "J(T)"

    # ── SUPPLY FUSES ──────────────────────────────────────────────────────────
    "SUPPLY_ALL":         ( 60.0, -138.75,  63.0, -135.75),
    "SUPPLY_FITTINGS":    (125.0, -138.75, 128.0, -135.75),

    # ── CONTROL CIRCUIT VOLTAGE ───────────────────────────────────────────────
    "CTRL_120V":          ( 60.0, -143.75,  63.0, -140.75),
    "CTRL_240V":          ( 95.0, -143.75,  98.0, -140.75),
    "CTRL_CVOLT":         (130.0, -143.75, 133.0, -140.75),   # CVOLT attdef

    # ── CONTROL CIRCUIT SUPPLY ────────────────────────────────────────────────
    "CSUPPLY_INDIV":      ( 60.0, -148.75,  63.0, -145.75),
    "CSUPPLY_GROUP":      ( 95.0, -148.75,  98.0, -145.75),
    "CSUPPLY_LINE":       (130.0, -148.75, 133.0, -145.75),
    "CSUPPLY_SEPARATE":   (155.0, -148.75, 158.0, -145.75),

    # ── NAMEPLATES ────────────────────────────────────────────────────────────
    "NP_ENGLISH":         ( 60.0, -158.75,  63.0, -155.75),
    "NP_FRENCH":          ( 95.0, -158.75,  98.0, -155.75),
    "NP_CUSTOM":          (130.0, -158.75, 133.0, -155.75),   # NAMEPLATE attdef

    # ── WIREMARKER TYPE ───────────────────────────────────────────────────────
    "WMT_ZMARKERS":       ( 60.0, -163.75,  63.0, -160.75),
    "WMT_HEATSHRINK":     ( 95.0, -163.75,  98.0, -160.75),
    "WMT_CUSTOM":         (130.0, -163.75, 133.0, -160.75),   # WIREMARKERS attdef

    # ── WIREMARKERS row 1 ─────────────────────────────────────────────────────
    "WM_UNIT_CTRL":       ( 60.0, -168.75,  63.0, -165.75),
    "WM_UNIT_PWR":        (100.0, -168.75, 103.0, -165.75),
    "WM_MTB_CTRL":        (135.0, -168.75, 138.0, -165.75),

    # ── WIREMARKERS row 2 ─────────────────────────────────────────────────────
    "WM_MTB_PWR":         ( 60.0, -173.75,  63.0, -170.75),
    "WM_INTERWIRING":     (100.0, -173.75, 103.0, -170.75),

    # ── SELECTOR SWITCH 2-POS ─────────────────────────────────────────────────
    "SS2_MAINTAINED":     ( 60.0, -178.75,  63.0, -175.75),
    "SS2_SPRING":         (100.0, -178.75, 103.0, -175.75),   # SS2_SEL attdef = "L" (to centre)

    # ── SELECTOR SWITCH 3-POS ─────────────────────────────────────────────────
    "SS3_MAINTAINED":     ( 60.0, -183.75,  63.0, -180.75),
    "SS3_SPRING":         (100.0, -183.75, 103.0, -180.75),   # SS3_SEL attdef = "L" (to centre)

    # ── PILOT LIGHT TYPE — FV (120 VAC LED options) ───────────────────────────
    "PL_120VAC":          ( 60.0, -188.75,  63.0, -185.75),   # FV_PILOT attdef for voltage
    "PL_24VAC":           (105.0, -188.75, 108.0, -185.75),   # FV_PILOT_24V attdef

    # ── PILOT LIGHT TYPE — PTT ────────────────────────────────────────────────
    "PL_PTT_120VAC":      ( 60.0, -198.75,  63.0, -195.75),   # PTT_PILOT attdef for voltage
    "PL_PTT_24VAC":       (105.0, -198.75, 108.0, -195.75),

    # ── TERMINAL TYPE ─────────────────────────────────────────────────────────
    "TERM_8WH1":          ( 60.0, -213.75,  63.0, -210.75),
    "TERM_CF4_10":        ( 85.0, -213.75,  88.0, -210.75),
    "TERM_CUSTOM":        (120.0, -213.75, 123.0, -210.75),   # TERMINAL attdef
}
# fmt: on

# ---------------------------------------------------------------------------
# Attribute definition order (matches GetAttributes() return order)
# Derived from genra001b_dump.json attdefs sorted by handle (B3B → B6C)
# ---------------------------------------------------------------------------
ATTDEF_ORDER: list[str] = [
    # B3B  KA            — main breaker KA rating (text next to MAIN BREAKER row)
    "MAIN_KA",
    # B3C  TERM          — special terminal blocks description
    "TERMINAL",
    # B3D  PILOT         — PTT FV pilot light voltage (default "120")
    "PTT_PILOT",
    # B3E  PILOT         — FV pilot light voltage (default "120")
    "FV_PILOT",
    # B3F  SEL           — 3-pos selector spring-return legend (default "L")
    "SS3_SEL",
    # B40  SEL           — 2-pos selector spring-return legend (default "L")
    "SS2_SEL",
    # B41  WIREMARKERS   — special wiremarker note
    "WIREMARKERS",
    # B42  NAMEPLATE     — special nameplate language
    "NAMEPLATE",
    # B43  CVOLT         — custom control voltage
    "CVOLT",
    # B44  FUSE          — feeder fuse class for J(T) option
    "FEEDER_FUSE",
    # B45  FUSE          — motor fuse class for J(T) option
    "MOTOR_FUSE",
    # B46  KA            — disconnect device KA rating
    "KA",
    # B47  SECOND        — ground bus second dimension
    "GROUND_SECOND",
    # B48  FIRST         — ground bus first dimension
    "GROUND_FIRST",
    # B49  SECOND        — O/S vertical bus second dimension
    "OS_SECOND",
    # B4A  FIRST         — O/S vertical bus first dimension
    "OS_FIRST",
    # B4B  AMPS          — O/S vertical bus amperage
    "OS_AMPS",
    # B4C  SECOND        — vertical bus second dimension
    "VERT_SECOND",
    # B4D  FIRST         — vertical bus first dimension
    "VERT_FIRST",
    # B4E  AMPS          — vertical bus amperage (default "440")
    "VERT_AMPS",
    # B4F  SECOND        — neutral bus second dimension
    "NEUT_SECOND",
    # B50  FIRST         — neutral bus first dimension
    "NEUT_FIRST",
    # B51  AMPS          — neutral bus amperage
    "NEUT_AMPS",
    # B52  SECOND        — horizontal bus second dimension (default '1 1/2"')
    "HORIZ_SECOND",
    # B53  FIRST         — horizontal bus first dimension (default '1/4"')
    "HORIZ_FIRST",
    # B54  BUSSES        — horizontal bus quantity/PH (default "1")
    "HORIZ_BUSSES",
    # B55  AMPS          — horizontal bus amperage (default "600")
    "HORIZ_AMPS",
    # B56  SIZE          — cable 2 size
    "CABLE2_SIZE",
    # B57  QTY           — cable 2 quantity
    "CABLE2_QTY",
    # B58  INC           — cable 2 included flag
    "CABLE2_INC",
    # B59  SIZE          — neutral cable size
    "NEUTRAL_SIZE",
    # B5A  QTY           — neutral cable quantity
    "NEUTRAL_QTY",
    # B5B  INC           — neutral cable included flag
    "NEUTRAL_INC",
    # B5C  SIZE          — cable 1 size
    "CABLE1_SIZE",
    # B5D  QTY           — cable 1 quantity
    "CABLE1_QTY",
    # B5E  INC           — cable 1 included flag
    "CABLE1_INC",
    # B5F  TERMINALBOARD — alternate master terminal board location
    "TERMINALBOARD",
    # B60  ARRANGEMENT   — special arrangement note
    "ARRANGEMENT",
    # B61  FINISH        — special paint / finish (default "SAND ENAMEL HS544H75")
    "FINISH",
    # B62  ENCLOSURE     — EEMAC enclosure special (default "SPRINKLERPROOF")
    "ENCLOSURE",
    # B63  WIRING        — EEMAC wiring special (default "MODIFIED")
    "WIRING",
    # B64  FREQ          — main supply frequency (default "60")
    "FREQ",
    # B65  WIRES         — main supply wires (default "3")
    "WIRES",
    # B66  PHASE         — main supply phases (default "3")
    "PHASE",
    # B67  VOLT          — main supply voltage
    "VOLT",
    # B6C  24V           — 24V FV pilot light voltage option (default "120")
    "FV_PILOT_24V",
]

REGAPP = "MCC_GENDATA"   # XData application name for hatch tagging

# ---------------------------------------------------------------------------
# COM retry helpers
# ---------------------------------------------------------------------------

# All HRESULT codes that mean "AutoCAD is busy, try again later"
_RPC_BUSY_CODES = frozenset({
    -2147418111,   # RPC_E_CALL_REJECTED        0x80010001
    0x80010001,
    -2147417846,   # RPC_E_SERVERCALL_RETRYLATER 0x8001010A
    0x8001010A,
    -2147418108,   # RPC_E_CALL_CANCELED         0x80010004 (rare)
    0x80010004,
})
_RPC_BUSY_STRS = ("80010001", "8001010a", "80010004",
                  "-2147418111", "-2147417846", "-2147418108")


def _is_rpc_busy(exc: Exception) -> bool:
    """True when AutoCAD rejected the call because it is busy."""
    try:
        if exc.args and exc.args[0] in _RPC_BUSY_CODES:
            return True
    except Exception:
        pass
    s = str(exc).lower()
    return any(code in s for code in _RPC_BUSY_STRS)


def _wait_for_autocad(doc, timeout: float = 45.0) -> None:
    """Block until AutoCAD reports IsQuiescent (3 consecutive True reads).

    Called before each batch of COM operations so we don't fire into a busy
    server.  A single True reading is not sufficient — AutoCAD can flicker
    briefly between background work bursts.
    """
    import time
    try:
        acad   = doc.Application
        stable = 0
        end    = time.time() + timeout
        while time.time() < end:
            time.sleep(0.2)
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
    # Fallback: unconditional pause if polling failed
    time.sleep(1.0)


def _com_call(fn, *args, max_retries: int = 20, base_delay: float = 0.5):
    """Call fn(*args), retrying on any AutoCAD "busy" COM error.

    Uses exponential back-off capped at 10 s per attempt.
    Raises the last exception once all retries are exhausted.
    """
    import time

    delay    = base_delay
    last_exc: Exception | None = None
    for _ in range(max_retries):
        try:
            return fn(*args)
        except Exception as exc:
            if _is_rpc_busy(exc):
                last_exc = exc
                time.sleep(delay)
                delay = min(delay * 1.7, 10.0)
                continue
            raise   # non-busy error — propagate immediately
    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# COM helpers
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
    frag = name_fragment.lower().replace(" ", "").replace(".dwg", "")
    for i in range(acad.Documents.Count):
        d = acad.Documents.Item(i)
        if frag in d.Name.lower().replace(" ", "").replace(".dwg", ""):
            return d
    raise RuntimeError(
        f"'{name_fragment}.dwg' is not open in AutoCAD. "
        f"Please open it and try again."
    )


def _get_block_ref(doc, block_name: str):
    """Find the first insertion of block_name in model space via HandleToObject."""
    ms = doc.ModelSpace
    for i in range(ms.Count):
        try:
            raw = ms.Item(i)
            h   = raw.Handle
            ent = doc.HandleToObject(h)
            if ent.EntityName == "AcDbBlockReference" and ent.Name == block_name:
                return ent
        except Exception:
            continue
    raise RuntimeError(
        f"Block '{block_name}' is not inserted in the model space of {doc.Name}. "
        f"Please insert it and try again."
    )


def _to_model(ins_x, ins_y, xscale, yscale, rot_rad, lx, ly):
    """Transform block-local (lx, ly) to model space."""
    cos_r, sin_r = math.cos(rot_rad), math.sin(rot_rad)
    mx = ins_x + lx * xscale * cos_r - ly * yscale * sin_r
    my = ins_y + lx * xscale * sin_r + ly * yscale * cos_r
    return mx, my


# ---------------------------------------------------------------------------
# XData helpers
# ---------------------------------------------------------------------------

def _register_app(doc):
    try:
        doc.RegisteredApplications.Add(REGAPP)
    except Exception:
        pass


def _set_xdata(ent, doc, value: str) -> None:
    try:
        import win32com.client as win32
        import pythoncom
        _register_app(doc)
        xtype = win32.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_I2,
                              [1001, 1000])
        xval  = win32.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_VARIANT,
                              [REGAPP, value])
        ent.SetXData(xtype, xval)
    except Exception:
        pass


def _get_xdata_value(ent) -> str | None:
    try:
        _, xval = ent.GetXData(REGAPP)
        vals = list(xval)
        return str(vals[1]) if len(vals) >= 2 else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Hatch insert / delete
# ---------------------------------------------------------------------------

def _insert_hatch(doc, ms, ins_x, ins_y, xscale, yscale, rot_rad,
                  checkbox_id: str) -> None:
    """Insert a SOLID hatch filling the checkbox square for checkbox_id.

    Every COM call is wrapped in _com_call() so that RPC_E_CALL_REJECTED
    from a busy AutoCAD instance is retried automatically.
    """
    import win32com.client as win32
    import pythoncom

    xmin, ymin, xmax, ymax = CHECKBOXES[checkbox_id]

    # 4 corners of the box in model space, closed (5 points)
    corners = [
        _to_model(ins_x, ins_y, xscale, yscale, rot_rad, lx, ly)
        for lx, ly in [
            (xmin, ymin), (xmax, ymin), (xmax, ymax),
            (xmin, ymax), (xmin, ymin),
        ]
    ]
    pts2d = win32.VARIANT(
        pythoncom.VT_ARRAY | pythoncom.VT_R8,
        [v for c in corners for v in c],
    )

    # Boundary polyline
    pline = _com_call(ms.AddLightWeightPolyline, pts2d)
    _com_call(setattr, pline, "Closed", True)

    # SOLID hatch — non-associative so it keeps its shape after pline is deleted
    hatch = _com_call(ms.AddHatch, 0, "SOLID", False)
    boundary = win32.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, [pline])
    _com_call(hatch.AppendOuterLoop, boundary)
    _com_call(hatch.Evaluate)

    # Tag with XData for reliable retrieval / deletion later
    _set_xdata(hatch, doc, checkbox_id)

    # Remove the temporary boundary polyline
    try:
        _com_call(pline.Delete)
    except Exception:
        pass


def _delete_hatch(doc, checkbox_id: str) -> bool:
    """Delete the hatch tagged with checkbox_id.  Returns True if found.

    Each COM call is retried on RPC_E_CALL_REJECTED.
    """
    ms = doc.ModelSpace
    count = _com_call(lambda: ms.Count)
    for i in range(count - 1, -1, -1):   # reverse so deletions don't shift indices
        try:
            raw   = _com_call(ms.Item, i)
            h     = _com_call(lambda r=raw: r.Handle)
            ent   = _com_call(doc.HandleToObject, h)
            ename = _com_call(lambda e=ent: e.EntityName)
            if ename != "AcDbHatch":
                continue
            if _get_xdata_value(ent) == checkbox_id:
                _com_call(ent.Delete)
                return True
        except Exception:
            continue
    return False


def _find_checked_boxes(doc) -> set[str]:
    """Return set of checkbox_ids that currently have hatches in model space.

    Every COM access is wrapped with _com_call so a busy AutoCAD doesn't
    cause the scan to return an incomplete set.
    """
    checked: set[str] = set()
    ms    = doc.ModelSpace
    count = _com_call(lambda: ms.Count)
    for i in range(count):
        try:
            raw   = _com_call(ms.Item, i)
            h     = _com_call(lambda r=raw: r.Handle)
            ent   = _com_call(doc.HandleToObject, h)
            ename = _com_call(lambda e=ent: e.EntityName)
            if ename != "AcDbHatch":
                continue
            val = _get_xdata_value(ent)
            if val and val in CHECKBOXES:
                checked.add(val)
        except Exception:
            continue
    return checked


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_general_data() -> dict[str, Any]:
    """
    Read the current state of the GENRA001B block in General_Data Sheet.dwg.

    Returns a dict with:
      "fields"       : {attdef_name: current_value, ...}  (all 46 attributes)
      "checked_boxes": [checkbox_id, ...]  (checked boxes only)
    """
    try:
        acad = _get_autocad()
        doc  = _get_doc(acad, DOC_FRAGMENT)
        ref  = _get_block_ref(doc, BLOCK_NAME)

        # ── Read attributes ───────────────────────────────────────────────
        fields: dict[str, str] = {}
        try:
            attribs = list(ref.GetAttributes())
            for idx, name in enumerate(ATTDEF_ORDER):
                if idx < len(attribs):
                    fields[name] = attribs[idx].TextString
                else:
                    fields[name] = ""
        except Exception as e:
            return {"success": False, "error": f"GetAttributes failed: {e}"}

        # ── Read checked boxes ────────────────────────────────────────────
        checked = list(_find_checked_boxes(doc))

        return {
            "success":     True,
            "fields":      fields,
            "checked_boxes": checked,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def set_general_data(
    fields:     dict[str, str] | None = None,
    checkboxes: list[str]      | None = None,
) -> dict[str, Any]:
    """
    Write fields and/or checkboxes to the GENRA001B block.

    Parameters
    ----------
    fields : {attdef_name: value}
        Only the keys present are updated; omitted keys are left unchanged.
    checkboxes : [checkbox_id, ...]
        The COMPLETE desired checked state.  Any box in this list that is
        currently unchecked will be checked; any box NOT in this list that is
        currently checked will be unchecked.
    """
    import time

    # Minimum gap between consecutive hatch insert/delete calls (seconds).
    # Longer than the previous 50 ms — hatch.Evaluate() triggers a drawing
    # regen that keeps AutoCAD busy for ~100–300 ms.
    _HATCH_GAP = 0.20

    try:
        acad = _get_autocad()
        doc  = _get_doc(acad, DOC_FRAGMENT)
        ref  = _get_block_ref(doc, BLOCK_NAME)

        # ── Phase 0: wait for AutoCAD to be idle before touching anything ──
        _wait_for_autocad(doc)

        ins    = list(_com_call(lambda: ref.InsertionPoint))
        ins_x, ins_y = ins[0], ins[1]
        xscale = _com_call(lambda: ref.XScaleFactor)
        yscale = _com_call(lambda: ref.YScaleFactor)
        rot    = _com_call(lambda: ref.Rotation)   # radians

        checked_added:   list[str] = []
        checked_removed: list[str] = []
        updated_fields:  list[str] = []
        errors:          list[str] = []

        # ── Phase 1: update text attributes ──────────────────────────────
        if fields:
            try:
                attribs = list(_com_call(ref.GetAttributes))
                for idx, name in enumerate(ATTDEF_ORDER):
                    if name in fields and idx < len(attribs):
                        try:
                            attr = attribs[idx]
                            _com_call(setattr, attr, "TextString", str(fields[name]))
                            updated_fields.append(name)
                        except Exception as e:
                            errors.append(f"Field {name}: {e}")
            except Exception as e:
                errors.append(f"GetAttributes: {e}")

        # ── Phase 2: wait for AutoCAD to settle after attribute writes ────
        if checkboxes is not None:
            _wait_for_autocad(doc)

        # ── Phase 3: diff current checkbox state ─────────────────────────
        if checkboxes is not None:
            desired  = set(checkboxes)
            current  = _find_checked_boxes(doc)
            ms       = doc.ModelSpace

            to_check   = desired - current
            to_uncheck = current - desired

            # ── Phase 4: remove unchecked hatches ─────────────────────────
            for cb_id in to_uncheck:
                # Wait until AutoCAD is idle before each delete
                _wait_for_autocad(doc)
                try:
                    if _delete_hatch(doc, cb_id):
                        checked_removed.append(cb_id)
                    else:
                        errors.append(f"Could not find hatch for {cb_id}")
                except Exception as e:
                    errors.append(f"Delete hatch {cb_id}: {e}")
                time.sleep(_HATCH_GAP)

            # Wait before the insert batch so deletes are fully committed
            if to_uncheck:
                _wait_for_autocad(doc)

            # ── Phase 5: insert new hatches ───────────────────────────────
            for cb_id in to_check:
                if cb_id not in CHECKBOXES:
                    errors.append(f"Unknown checkbox id: {cb_id}")
                    continue
                # Wait until AutoCAD is idle before each insert
                _wait_for_autocad(doc)
                try:
                    _insert_hatch(doc, ms, ins_x, ins_y, xscale, yscale, rot, cb_id)
                    checked_added.append(cb_id)
                except Exception as e:
                    errors.append(f"Hatch {cb_id}: {e}")
                time.sleep(_HATCH_GAP)

        # Final regen
        _wait_for_autocad(doc)
        _com_call(doc.Regen, 1)

        return {
            "success":         len(errors) == 0,
            "updated_fields":  updated_fields,
            "checked_added":   checked_added,
            "checked_removed": checked_removed,
            "errors":          errors,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def list_checkboxes() -> dict[str, Any]:
    """Return the complete checkbox map (for GUI enumeration)."""
    return {
        "success":    True,
        "checkboxes": list(CHECKBOXES.keys()),
        "attdefs":    ATTDEF_ORDER,
    }
