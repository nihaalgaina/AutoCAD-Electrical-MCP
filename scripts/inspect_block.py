"""Diagnostic script — dumps everything found in a single .dwg file.

Run this on one of your block files to understand its structure before
trying to scan the whole library.

Usage
-----
  python scripts/inspect_block.py "C:\\path\\to\\UNIT5080.DWG"
"""

from __future__ import annotations
import sys
from pathlib import Path

def _check():
    try:
        import win32com.client
    except ImportError:
        print("pywin32 not installed. Run: pip install pywin32"); sys.exit(1)

_check()
import win32com.client


def get_autocad():
    for prog_id in ("AutoCAD.Application.26", "AutoCAD.Application"):
        try:
            raw = win32com.client.GetActiveObject(prog_id)
            return win32com.client.Dispatch(raw)
        except Exception:
            continue
    print("ERROR: AutoCAD is not running."); sys.exit(1)


def inspect(dwg_path: str):
    app = get_autocad()
    path = Path(dwg_path)
    if not path.exists():
        print(f"File not found: {dwg_path}"); sys.exit(1)

    print(f"\nOpening: {path.name}")
    print("=" * 60)

    # Open the file
    docs_before = set()
    try:
        for i in range(app.Documents.Count):
            docs_before.add(app.Documents.Item(i).FullName.lower())
    except Exception:
        pass

    try:
        active = win32com.client.Dispatch(app.ActiveDocument)
        active.SendCommand(f'-OPEN "{dwg_path}" \n')
    except Exception as exc:
        print(f"SendCommand failed ({exc})")
        print("Try opening the file manually in AutoCAD first, then rerun this script.")
        sys.exit(1)

    import time
    doc = None
    for _ in range(30):
        time.sleep(0.5)
        try:
            for i in range(app.Documents.Count):
                d = app.Documents.Item(i)
                if d.FullName.lower() not in docs_before:
                    doc = win32com.client.Dispatch(d)
                    break
        except Exception:
            pass
        if doc:
            break

    if doc is None:
        print("File did not open within 15 seconds.")
        print("Try opening the file manually in AutoCAD first, then rerun this script.")
        sys.exit(1)

    print(f"Opened: {doc.FullName}\n")

    # ---------------------------------------------------------------
    # 1. Model space contents
    # ---------------------------------------------------------------
    print("── MODEL SPACE ─────────────────────────────────────────")
    try:
        ms = doc.ModelSpace
        print(f"Entity count: {ms.Count}")
        type_counts: dict[str, int] = {}
        for i in range(ms.Count):
            try:
                ent = ms.Item(i)
                name = ent.ObjectName
                type_counts[name] = type_counts.get(name, 0) + 1

                if name == "AcDbAttributeDefinition":
                    print(f"  ATTDEF  tag={ent.TagString!r:25s} prompt={ent.PromptString!r}")
                elif name == "AcDbAttribute":
                    print(f"  ATTRIB  tag={ent.TagString!r:25s} value={ent.TextString!r}")
                elif name == "AcDbBlockReference":
                    print(f"  INSERT  name={ent.Name!r}")
            except Exception as e:
                print(f"  [item {i} error: {e}]")

        print("\nEntity type summary:")
        for t, n in sorted(type_counts.items(), key=lambda x: -x[1]):
            print(f"  {n:4d}  {t}")
    except Exception as exc:
        print(f"  ERROR reading model space: {exc}")

    # ---------------------------------------------------------------
    # 2. Block table
    # ---------------------------------------------------------------
    print("\n── BLOCK TABLE ─────────────────────────────────────────")
    try:
        blocks = doc.Blocks
        print(f"Block definitions: {blocks.Count}")
        for i in range(blocks.Count):
            try:
                blk = blocks.Item(i)
                bname = blk.Name
                count = blk.Count

                # Collect entity types inside this block
                inner_types: dict[str, int] = {}
                attdefs_found = []
                for j in range(count):
                    try:
                        ent = blk.Item(j)
                        oname = ent.ObjectName
                        inner_types[oname] = inner_types.get(oname, 0) + 1
                        if oname == "AcDbAttributeDefinition":
                            attdefs_found.append(ent.TagString)
                        elif oname == "AcDbAttribute":
                            attdefs_found.append(f"ATTRIB:{ent.TagString}")
                    except Exception:
                        pass

                # Print a summary line for this block
                has_attrs = bool(attdefs_found)
                marker = " *** HAS ATTDEFS ***" if has_attrs else ""
                print(f"\n  Block: {bname!r:40s} ({count} entities){marker}")
                if has_attrs:
                    for tag in attdefs_found:
                        print(f"         → {tag}")
                elif inner_types:
                    summary = ", ".join(f"{n}×{t.replace('AcDb','')}" for t, n in inner_types.items())
                    print(f"         ({summary})")

            except Exception as exc:
                print(f"  [block {i} error: {exc}]")
    except Exception as exc:
        print(f"  ERROR reading block table: {exc}")

    # ---------------------------------------------------------------
    # 3. Close
    # ---------------------------------------------------------------
    print("\n" + "=" * 60)
    try:
        doc.Close(False)
        print("File closed.")
    except Exception:
        try:
            doc.SendCommand("CLOSE \nN\n")
        except Exception:
            pass


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/inspect_block.py \"C:\\path\\to\\BLOCK.DWG\"")
        sys.exit(1)
    inspect(sys.argv[1])
