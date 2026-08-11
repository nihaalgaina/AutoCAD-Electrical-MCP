"""
scripts/scan_general_data_block.py
===================================
One-time diagnostic: reads the Genra001 block definition from
"General_Data Sheet.dwg" and outputs a JSON coordinate map of:
  - All attribute definitions (tag, prompt, insertion point)
  - All checkbox squares (closed polylines sized < 5 x 5 drawing units)
    with their centroid, bounding box, and nearest text label

Usage (with General_Data Sheet.dwg open in AutoCAD):
    python scripts/scan_general_data_block.py
    python scripts/scan_general_data_block.py --out genra001_map.json
    python scripts/scan_general_data_block.py --doc "General_Data Sheet" --block Genra001

The output JSON is the input for mcc_general_data.py's coordinate map.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dist(ax, ay, bx, by) -> float:
    return math.sqrt((ax - bx) ** 2 + (ay - by) ** 2)


def _centroid(coords_flat: list[float]) -> tuple[float, float]:
    """Return (cx, cy) from a flat [x0,y0, x1,y1, ...] list."""
    n = len(coords_flat) // 2
    xs = [coords_flat[i * 2]     for i in range(n)]
    ys = [coords_flat[i * 2 + 1] for i in range(n)]
    return sum(xs) / n, sum(ys) / n


def _bbox(coords_flat: list[float]) -> tuple[float, float, float, float]:
    """Return (x_min, y_min, x_max, y_max)."""
    n = len(coords_flat) // 2
    xs = [coords_flat[i * 2]     for i in range(n)]
    ys = [coords_flat[i * 2 + 1] for i in range(n)]
    return min(xs), min(ys), max(xs), max(ys)


# ---------------------------------------------------------------------------
# Main scanner
# ---------------------------------------------------------------------------

def scan(doc_fragment: str, block_name: str) -> dict:
    try:
        import win32com.client as win32
    except ImportError:
        print("[ERROR] pywin32 not installed. Run: pip install pywin32")
        sys.exit(1)

    # ── Connect to AutoCAD ────────────────────────────────────────────────
    try:
        acad = win32.GetActiveObject("AutoCAD.Application")
    except Exception:
        print("[ERROR] AutoCAD is not running. Open AutoCAD with General_Data Sheet.dwg loaded.")
        sys.exit(1)

    # ── Find the document ─────────────────────────────────────────────────
    doc = None
    frag_lower = doc_fragment.lower().replace(" ", "")
    for i in range(acad.Documents.Count):
        d = acad.Documents.Item(i)
        name_lower = d.Name.lower().replace(" ", "").replace(".dwg", "")
        if frag_lower in name_lower or name_lower in frag_lower:
            doc = d
            break

    if doc is None:
        available = [acad.Documents.Item(i).Name for i in range(acad.Documents.Count)]
        print(f"[ERROR] Could not find document matching '{doc_fragment}'.")
        print(f"  Open documents: {available}")
        sys.exit(1)

    print(f"  Using document: {doc.Name}")

    # ── Find the block definition ─────────────────────────────────────────
    block_def = None
    try:
        block_def = doc.Blocks.Item(block_name)
    except Exception:
        pass

    if block_def is None:
        # Try case-insensitive search
        for i in range(doc.Blocks.Count):
            b = doc.Blocks.Item(i)
            if b.Name.upper() == block_name.upper():
                block_def = b
                break

    if block_def is None:
        print(f"[ERROR] Block '{block_name}' not found in {doc.Name}.")
        sys.exit(1)

    print(f"  Found block: {block_def.Name}  ({block_def.Count} entities)")

    # ── Iterate entities ──────────────────────────────────────────────────
    checkboxes   = []   # small closed polylines
    attdefs      = []   # attribute definitions
    text_labels  = []   # Text / MText entities (for labelling checkboxes)

    for i in range(block_def.Count):
        try:
            ent = block_def.Item(i)
        except Exception:
            continue

        try:
            etype = ent.EntityName
        except Exception:
            continue

        # ── Attribute definitions ─────────────────────────────────────────
        if etype == "AcDbAttributeDefinition":
            try:
                ip = ent.InsertionPoint
                attdefs.append({
                    "tag":     ent.TagString,
                    "prompt":  ent.PromptString,
                    "default": ent.TextString,
                    "x":       round(ip[0], 4),
                    "y":       round(ip[1], 4),
                })
            except Exception:
                pass
            continue

        # ── Text / MText (for nearby-label search) ────────────────────────
        if etype in ("AcDbText", "AcDbMText"):
            try:
                if etype == "AcDbText":
                    ip  = ent.InsertionPoint
                    txt = ent.TextString
                else:
                    ip  = ent.InsertionPoint
                    txt = ent.TextString  # MText may include formatting codes
                text_labels.append({
                    "text": txt.strip(),
                    "x":    round(ip[0], 4),
                    "y":    round(ip[1], 4),
                })
            except Exception:
                pass
            continue

        # ── Polylines (checkbox candidates) ──────────────────────────────
        if etype in ("AcDbPolyline", "AcDb2dPolyline", "AcDbLwPolyLine"):
            try:
                closed = bool(ent.Closed)
            except Exception:
                closed = False

            try:
                coords = list(ent.Coordinates)
            except Exception:
                continue

            if len(coords) < 6:   # need at least 3 vertices
                continue

            x_min, y_min, x_max, y_max = _bbox(coords)
            width  = x_max - x_min
            height = y_max - y_min

            # Checkbox squares: closed, roughly square, small (< 5 units)
            if closed and width < 5.0 and height < 5.0 and width > 0.1 and height > 0.1:
                cx, cy = _centroid(coords)
                checkboxes.append({
                    "cx":     round(cx, 4),
                    "cy":     round(cy, 4),
                    "x_min":  round(x_min, 4),
                    "y_min":  round(y_min, 4),
                    "x_max":  round(x_max, 4),
                    "y_max":  round(y_max, 4),
                    "width":  round(width, 4),
                    "height": round(height, 4),
                })

    # ── Label each checkbox with the nearest text entity ──────────────────
    all_labels = text_labels + [
        {"text": a["tag"], "x": a["x"], "y": a["y"]} for a in attdefs
    ]

    for cb in checkboxes:
        nearest_text = ""
        nearest_dist = float("inf")
        for lbl in all_labels:
            d = _dist(cb["cx"], cb["cy"], lbl["x"], lbl["y"])
            if d < nearest_dist:
                nearest_dist = d
                nearest_text = lbl["text"]
        cb["nearest_label"] = nearest_text
        cb["label_dist"]    = round(nearest_dist, 4)

    # Sort top-to-bottom, left-to-right (matches visual layout)
    checkboxes.sort(key=lambda c: (-round(c["cy"], 1), c["cx"]))
    attdefs.sort(   key=lambda a: (-a["y"], a["x"]))

    result = {
        "document":   doc.Name,
        "block":      block_def.Name,
        "checkboxes": checkboxes,
        "attdefs":    attdefs,
        "text_labels": sorted(text_labels, key=lambda t: (-t["y"], t["x"])),
    }

    print(f"\n  Checkboxes found : {len(checkboxes)}")
    print(f"  Attdefs found    : {len(attdefs)}")
    print(f"  Text labels found: {len(text_labels)}")

    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Scan Genra001 block for checkbox coordinates.")
    parser.add_argument("--doc",   default="General_Data Sheet", help="Document name fragment")
    parser.add_argument("--block", default="Genra001",           help="Block name")
    parser.add_argument("--out",   default="genra001_map.json",  help="Output JSON file")
    args = parser.parse_args()

    print(f"\nScanning block '{args.block}' in '{args.doc}'...")
    data = scan(args.doc, args.block)

    out_path = Path(args.out)
    out_path.write_text(json.dumps(data, indent=2))
    print(f"\n  Map written to: {out_path.resolve()}")

    # Pretty-print summary
    print("\n── Attribute Definitions ─────────────────────────────────────────")
    for a in data["attdefs"]:
        print(f"  {a['tag']:20s}  prompt={a['prompt']:30s}  ({a['x']:.2f}, {a['y']:.2f})")

    print("\n── Checkbox Squares (top → bottom) ───────────────────────────────")
    for cb in data["checkboxes"]:
        print(
            f"  cx={cb['cx']:8.3f}  cy={cb['cy']:8.3f}  "
            f"size={cb['width']:.3f}x{cb['height']:.3f}  "
            f"nearest='{cb['nearest_label']}'"
        )

    print("\nDone. Review the JSON, assign checkbox IDs, then run the backend build.")


if __name__ == "__main__":
    main()
