"""Diagnostic script for AutoCAD COM connection and MCC block insertion.

Run this BEFORE wiring up the AI to confirm:
  1. pywin32 is installed and AutoCAD 2026 is reachable via COM
  2. The active drawing is accessible
  3. ModelSpace can be iterated
  4. A test block can be inserted and its attributes read back

Usage
-----
  python scripts/test_mcc_connection.py

Optional flags
--------------
  --block  NAME   Name or path of a block to test-insert (dry-run by default)
  --x      FLOAT  Insertion X coordinate (default 0)
  --y      FLOAT  Insertion Y coordinate (default 0)
  --insert        Actually insert the block (without this flag, step 4 is skipped)
  --undo          Send an UNDO command after insertion (keeps drawing clean)
"""

from __future__ import annotations

import argparse
import sys
import traceback

# ---------------------------------------------------------------------------
# 1. pywin32 check
# ---------------------------------------------------------------------------
def check_pywin32() -> bool:
    print("\n[1] Checking pywin32 ...")
    try:
        import win32com.client
        import pythoncom
        print("    OK — pywin32 is installed.")
        return True
    except ImportError:
        print("    FAIL — pywin32 not found.  Run: pip install pywin32")
        return False


# ---------------------------------------------------------------------------
# 2. AutoCAD COM connection
# ---------------------------------------------------------------------------
def check_autocad_connection():
    print("\n[2] Connecting to AutoCAD via COM ...")
    try:
        import win32com.client
        # Try the versioned ProgID for 2026 first, then fall back to generic
        app = None
        for prog_id in ("AutoCAD.Application.26", "AutoCAD.Application"):
            try:
                app = win32com.client.GetActiveObject(prog_id)
                print(f"    OK — connected using ProgID '{prog_id}'")
                print(f"         Name   : {app.Name}")
                print(f"         Version: {app.Version}")
                return app
            except Exception:
                continue
        print("    FAIL — AutoCAD is not running or COM registration not found.")
        print("           Make sure AutoCAD Electrical 2026 is open with a drawing.")
        return None
    except Exception as exc:
        print(f"    FAIL — unexpected error: {exc}")
        traceback.print_exc()
        return None


# ---------------------------------------------------------------------------
# 3. Active document / ModelSpace
# ---------------------------------------------------------------------------
def check_active_document(app) -> tuple:
    print("\n[3] Checking active document and ModelSpace ...")
    try:
        doc = app.ActiveDocument
        if doc is None:
            print("    FAIL — no document is open.  Open a drawing and retry.")
            return None, None
        print(f"    OK — Active document: {doc.Name}")
        print(f"         Full path     : {doc.FullName}")

        ms = doc.ModelSpace
        print(f"    OK — ModelSpace entity count: {ms.Count}")

        # List the first 5 entities as a sanity check
        limit = min(5, ms.Count)
        if limit:
            print(f"    First {limit} entities:")
            for i in range(limit):
                obj = ms.Item(i)
                try:
                    name = obj.ObjectName
                    handle = obj.Handle
                    layer = obj.Layer
                    print(f"      [{i}] {name}  handle={handle}  layer={layer}")
                except Exception:
                    print(f"      [{i}] (could not read properties)")

        return doc, ms
    except Exception as exc:
        print(f"    FAIL — {exc}")
        traceback.print_exc()
        return None, None


# ---------------------------------------------------------------------------
# 4. Block insertion test
# ---------------------------------------------------------------------------
def test_block_insert(app, doc, ms, block_name: str, x: float, y: float, undo: bool):
    print(f"\n[4] Inserting test block '{block_name}' at ({x}, {y}) ...")
    try:
        import win32com.client
        import pythoncom
        import math

        insertion_pt = win32com.client.VARIANT(
            pythoncom.VT_ARRAY | pythoncom.VT_R8,
            [float(x), float(y), 0.0],
        )

        ref = ms.InsertBlock(
            insertion_pt,
            block_name,
            1.0,   # x_scale
            1.0,   # y_scale
            1.0,   # z_scale
            0.0,   # rotation (radians)
        )

        print(f"    OK — Inserted.  Handle: {ref.Handle}  Name: {ref.Name}")

        # Read back attributes
        try:
            attrs = ref.GetAttributes()
            if len(attrs) == 0:
                print("    INFO — Block has no attributes.")
            else:
                print(f"    Attributes ({len(attrs)}):")
                for a in attrs:
                    print(f"      {a.TagString:20s} = {a.TextString!r}")
        except Exception as exc:
            print(f"    WARN — Could not read attributes: {exc}")

        doc.Regen(1)

        if undo:
            doc.SendCommand("UNDO 1 ")
            print("    INFO — UNDO sent (drawing restored).")

        return True

    except Exception as exc:
        print(f"    FAIL — InsertBlock raised: {exc}")
        print()
        print("    Common causes:")
        print("      - Block name not found in drawing's block table")
        print("      - File path does not exist or AutoCAD cannot find the .dwg")
        print("      - Scale or rotation type mismatch (should be float)")
        traceback.print_exc()
        return False


# ---------------------------------------------------------------------------
# 5. Catalog check
# ---------------------------------------------------------------------------
def check_catalog():
    print("\n[5] Checking mcc_block_catalog.yaml ...")
    from pathlib import Path
    catalog_path = Path(__file__).parent.parent / "mcc_block_catalog.yaml"
    if not catalog_path.exists():
        print(f"    WARN — Catalog not found at {catalog_path}")
        print("           Copy mcc_block_catalog.yaml to the project root.")
        return

    try:
        import yaml
    except ImportError:
        print("    WARN — PyYAML not installed.  Run: pip install pyyaml")
        return

    with open(catalog_path, "r", encoding="utf-8") as fh:
        catalog = yaml.safe_load(fh)

    lib_paths = catalog.get("library_paths", [])
    sections = catalog.get("mcc_sections", {})
    blocks = catalog.get("blocks", {})

    print(f"    Library paths  : {lib_paths}")
    print(f"    MCC sections   : {list(sections.keys())}")
    print(f"    Standalone blks: {list(blocks.keys())}")

    for path in lib_paths:
        from pathlib import Path as P
        if P(path).exists():
            print(f"    [EXISTS] {path}")
        else:
            print(f"    [MISSING] {path}  <-- update mcc_block_catalog.yaml")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="AutoCAD MCC connection diagnostic")
    parser.add_argument("--block",  default="",    help="Block name or .dwg path to test")
    parser.add_argument("--x",      type=float, default=0.0, help="Insertion X")
    parser.add_argument("--y",      type=float, default=0.0, help="Insertion Y")
    parser.add_argument("--insert", action="store_true",     help="Actually insert the block")
    parser.add_argument("--undo",   action="store_true",     help="UNDO after insertion")
    args = parser.parse_args()

    print("=" * 60)
    print("  AutoCAD MCC Connection Diagnostic")
    print("=" * 60)

    if not check_pywin32():
        sys.exit(1)

    app = check_autocad_connection()
    if app is None:
        sys.exit(1)

    doc, ms = check_active_document(app)
    if doc is None:
        sys.exit(1)

    check_catalog()

    if args.insert and args.block:
        test_block_insert(app, doc, ms, args.block, args.x, args.y, args.undo)
    elif args.block and not args.insert:
        print(f"\n[4] Block insert skipped (pass --insert to actually insert '{args.block}')")
    else:
        print("\n[4] Block insert skipped (pass --block NAME --insert to test insertion)")

    print("\n" + "=" * 60)
    print("  Diagnostic complete.")
    print("=" * 60)


if __name__ == "__main__":
    main()
