"""Block catalog generator for MCC automation.

Scans a directory of .dwg files, reads attribute tag definitions from each
block using ezdxf (no AutoCAD required), and writes a populated
mcc_block_catalog.yaml ready to use with the MCC tools.

Usage
-----
  pip install ezdxf pyyaml
  python scripts/generate_catalog.py --dir "C:\\path\\to\\8PX3 Blocks"

Options
-------
  --dir   PATH     Directory containing your .dwg block files (required)
  --out   PATH     Output YAML path (default: mcc_block_catalog.yaml in repo root)
  --merge          Merge into existing catalog instead of overwriting
  --verbose        Print attribute tags found in each block

What it does
------------
  1. Finds all .dwg files in --dir (non-recursive by default; add --recurse for subfolders)
  2. Opens each with ezdxf and reads ATTDEF entities (attribute definitions)
  3. Detects likely MCC section blocks by name pattern (MCC_SECT_*, 8PX3_*, etc.)
  4. Writes a catalog YAML with:
       - library_paths pointing to your directory
       - blocks[] entry for every .dwg with its discovered attribute tags
       - mcc_sections[] entries for blocks that look like section blocks,
         with a best-guess attribute_map you edit to confirm
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Dependency check
# ---------------------------------------------------------------------------
def _check_deps():
    missing = []
    try:
        import ezdxf
    except ImportError:
        missing.append("ezdxf")
    try:
        import yaml
    except ImportError:
        missing.append("pyyaml")
    if missing:
        print(f"Missing packages: {', '.join(missing)}")
        print(f"Run: pip install {' '.join(missing)}")
        sys.exit(1)

_check_deps()

import ezdxf
import yaml


# ---------------------------------------------------------------------------
# Attribute extraction
# ---------------------------------------------------------------------------
def read_attdefs(dwg_path: Path) -> list[dict[str, str]]:
    """Return a list of attribute definition dicts from a .dwg file.

    Each dict has keys: tag, prompt, default, layer, invisible.
    Returns [] if the file cannot be read or has no ATTDEFs.
    """
    try:
        doc = ezdxf.readfile(str(dwg_path))
    except Exception as exc:
        print(f"  WARN  Cannot read {dwg_path.name}: {exc}")
        return []

    attdefs = []

    # Check the model space for loose ATTDEFs (some blocks store them there)
    msp = doc.modelspace()
    for entity in msp:
        if entity.dxftype() == "ATTDEF":
            attdefs.append(_attdef_to_dict(entity))

    # Check every block definition in the block table
    for block_def in doc.blocks:
        if block_def.name.startswith("*"):
            continue  # skip *Model_Space, *Paper_Space etc.
        for entity in block_def:
            if entity.dxftype() == "ATTDEF":
                attdefs.append(_attdef_to_dict(entity))

    # De-duplicate by tag (same tag may appear in multiple block defs)
    seen: set[str] = set()
    unique = []
    for a in attdefs:
        if a["tag"] not in seen:
            seen.add(a["tag"])
            unique.append(a)

    return unique


def _attdef_to_dict(entity) -> dict[str, str]:
    try:
        tag = entity.dxf.tag
    except Exception:
        tag = ""
    try:
        prompt = entity.dxf.prompt
    except Exception:
        prompt = ""
    try:
        default = entity.dxf.text
    except Exception:
        default = ""
    try:
        layer = entity.dxf.layer
    except Exception:
        layer = "0"
    try:
        flags = entity.dxf.flags
        invisible = bool(flags & 1)
    except Exception:
        invisible = False

    return {
        "tag": tag,
        "prompt": prompt,
        "default": default,
        "layer": layer,
        "invisible": invisible,
    }


# ---------------------------------------------------------------------------
# Section block detection heuristics
# ---------------------------------------------------------------------------

# Patterns that suggest a block is an MCC section outline / frame
_SECTION_PATTERNS = [
    re.compile(r"(?i)(mcc[_\-\s]?sect)", ),
    re.compile(r"(?i)(sect[_\-\s]?\d{3,4}w?)", ),
    re.compile(r"(?i)(\d{3,4}[_\-\s]?w[_\-\s]?sect)", ),
    re.compile(r"(?i)(8px3[_\-\s]?\d{3,4})", ),
    re.compile(r"(?i)(section[_\-\s]?\d{3,4})", ),
]

_WIDTH_EXTRACT = re.compile(r"(\d{3,4})")  # pulls e.g. 500 from MCC_SECT_500

def _looks_like_section(name: str) -> bool:
    return any(p.search(name) for p in _SECTION_PATTERNS)

def _extract_width(name: str) -> int | None:
    m = _WIDTH_EXTRACT.search(name)
    if m:
        val = int(m.group(1))
        if 300 <= val <= 1200:   # plausible MCC section width in mm
            return val
    return None


# ---------------------------------------------------------------------------
# Attribute map guesser
# ---------------------------------------------------------------------------

# Maps common attribute tag patterns to starter_data keys.
# Edit _ATTR_GUESSES to match your company's naming conventions.
_ATTR_GUESSES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?i)^(sect|section|unit.?no|unit.?num)$"),  "section_id"),
    (re.compile(r"(?i)^(desc|description|load.?desc)"),        "description"),
    (re.compile(r"(?i)^(hp|horse.?power|kw)$"),               "hp"),
    (re.compile(r"(?i)^(volt|voltage|kv)$"),                  "voltage"),
    (re.compile(r"(?i)^(fla|full.?load|current)$"),           "fla"),
    (re.compile(r"(?i)^(type|starter.?type|unit.?type)$"),    "starter_type"),
    (re.compile(r"(?i)^(cat|catalog|cat.?no|part.?no)$"),     "catalog_no"),
    (re.compile(r"(?i)^(ord|order|job.?no)$"),                "order_no"),
    (re.compile(r"(?i)^(frame|breaker.?frame)$"),              "breaker_frame"),
    (re.compile(r"(?i)^(trip|trip.?a)$"),                     "breaker_trip"),
    (re.compile(r"(?i)^(fuse|fuse.?a|fuse.?amp)$"),           "fuse_amp"),
    (re.compile(r"(?i)^(coil|coil.?v|coil.?volt)$"),          "coil_voltage"),
    (re.compile(r"(?i)^(overload|ol.?range|range)$"),         "ol_range"),
    (re.compile(r"(?i)^(ctrl.?va|ctl.?va|xfmr.?va)$"),       "ctrl_xfmr_va"),
]

def _guess_attr_map(attdefs: list[dict]) -> dict[str, str]:
    """Return a starter_data_key -> attribute_tag mapping based on tag names."""
    result: dict[str, str] = {}
    used_keys: set[str] = set()
    for a in attdefs:
        tag = a["tag"]
        for pattern, data_key in _ATTR_GUESSES:
            if data_key not in used_keys and pattern.match(tag):
                result[data_key] = tag
                used_keys.add(data_key)
                break
    return result


# ---------------------------------------------------------------------------
# Catalog builder
# ---------------------------------------------------------------------------

def build_catalog(
    block_dir: Path,
    recurse: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """Scan block_dir and return a catalog dict."""

    dwg_files = sorted(
        block_dir.rglob("*.dwg") if recurse else block_dir.glob("*.dwg")
    )
    dwg_files += sorted(
        block_dir.rglob("*.DWG") if recurse else block_dir.glob("*.DWG")
    )
    # de-dup (case-insensitive file systems may return both)
    seen_lower: set[str] = set()
    unique_files = []
    for f in dwg_files:
        key = str(f).lower()
        if key not in seen_lower:
            seen_lower.add(key)
            unique_files.append(f)
    dwg_files = unique_files

    print(f"\nFound {len(dwg_files)} .dwg files in {block_dir}\n")

    blocks: dict[str, Any] = {}
    mcc_sections: dict[int, Any] = {}

    for dwg in dwg_files:
        name = dwg.stem   # filename without extension
        attdefs = read_attdefs(dwg)
        tags = [a["tag"] for a in attdefs]

        if verbose:
            print(f"  {name}")
            if tags:
                print(f"    Attributes: {tags}")
            else:
                print(f"    (no attributes found)")

        # Build the blocks[] entry
        blocks[name] = {
            "description": f"Block: {name}",   # edit this manually
            "file_path": dwg.name,              # relative to library_paths
            "attributes": tags,
        }

        # If it looks like a section block, add to mcc_sections too
        if _looks_like_section(name):
            width = _extract_width(name)
            attr_map = _guess_attr_map(attdefs)

            entry = {
                "block_name": name,
                "description": f"{width}mm MCC section" if width else name,
                "file_path": dwg.name,
                "attribute_map": attr_map if attr_map else {
                    # Placeholders — replace right side with real tag names
                    "section_id":   "SECT",
                    "description":  "DESC",
                    "hp":           "HP",
                    "voltage":      "VOLT",
                    "fla":          "FLA",
                    "starter_type": "TYPE",
                    "catalog_no":   "CAT",
                    "order_no":     "ORD",
                },
                "_detected_attributes": tags,   # keep for reference; remove later
            }

            if width and width not in mcc_sections:
                mcc_sections[width] = entry
            elif not width:
                # No width detected — add under the block name as key
                mcc_sections[name] = entry

        sys.stdout.flush()

    catalog: dict[str, Any] = {
        "library_paths": [str(block_dir)],
        "layout": {
            "start_x": 0,
            "start_y": 0,
            "direction": "horizontal",
        },
        "mcc_sections": dict(sorted(mcc_sections.items())),
        "blocks": blocks,
    }

    return catalog


# ---------------------------------------------------------------------------
# Merge helper
# ---------------------------------------------------------------------------

def merge_catalogs(existing: dict, new: dict) -> dict:
    """Merge new catalog into existing, preserving manual edits in existing."""
    merged = dict(existing)

    # Merge library_paths (union)
    existing_paths = set(existing.get("library_paths", []))
    new_paths = set(new.get("library_paths", []))
    merged["library_paths"] = sorted(existing_paths | new_paths)

    # Merge blocks (new entries added, existing entries kept as-is)
    existing_blocks = existing.get("blocks", {})
    new_blocks = new.get("blocks", {})
    merged_blocks = dict(new_blocks)
    for k, v in existing_blocks.items():
        merged_blocks[k] = v   # existing manual edits win
    merged["blocks"] = merged_blocks

    # Merge mcc_sections (same logic)
    existing_sects = existing.get("mcc_sections", {})
    new_sects = new.get("mcc_sections", {})
    merged_sects = dict(new_sects)
    for k, v in existing_sects.items():
        merged_sects[k] = v
    merged["mcc_sections"] = merged_sects

    return merged


# ---------------------------------------------------------------------------
# YAML writer (preserves int keys, readable output)
# ---------------------------------------------------------------------------

class _IntKeyDumper(yaml.Dumper):
    pass

def _int_representer(dumper, data):
    return dumper.represent_int(data)

_IntKeyDumper.add_representer(int, _int_representer)


def write_catalog(catalog: dict, out_path: Path):
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("# =============================================================================\n")
        fh.write("# MCC Block Catalog  (auto-generated by scripts/generate_catalog.py)\n")
        fh.write("# Review attribute_map entries in mcc_sections — confirm tags match your blocks.\n")
        fh.write("# Remove _detected_attributes keys once you've confirmed the attribute_map.\n")
        fh.write("# =============================================================================\n\n")
        yaml.dump(
            catalog,
            fh,
            Dumper=_IntKeyDumper,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
            width=120,
        )
    print(f"\nCatalog written to: {out_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Scan a .dwg block library and generate mcc_block_catalog.yaml"
    )
    parser.add_argument(
        "--dir", required=True,
        help='Path to folder containing .dwg block files, e.g. "C:\\Work\\8PX3 Blocks"'
    )
    parser.add_argument(
        "--out", default=None,
        help="Output YAML path (default: mcc_block_catalog.yaml in repo root)"
    )
    parser.add_argument(
        "--merge", action="store_true",
        help="Merge into existing catalog instead of overwriting"
    )
    parser.add_argument(
        "--recurse", action="store_true",
        help="Recurse into subdirectories"
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Print attribute tags found in each block"
    )
    args = parser.parse_args()

    block_dir = Path(args.dir)
    if not block_dir.exists():
        print(f"ERROR: Directory not found: {block_dir}")
        sys.exit(1)

    # Default output: repo root / mcc_block_catalog.yaml
    out_path = Path(args.out) if args.out else Path(__file__).parent.parent / "mcc_block_catalog.yaml"

    catalog = build_catalog(block_dir, recurse=args.recurse, verbose=args.verbose)

    if args.merge and out_path.exists():
        with open(out_path, "r", encoding="utf-8") as fh:
            existing = yaml.safe_load(fh)
        catalog = merge_catalogs(existing, catalog)
        print("Merged with existing catalog.")

    write_catalog(catalog, out_path)

    # Summary
    n_blocks = len(catalog.get("blocks", {}))
    n_sections = len(catalog.get("mcc_sections", {}))
    print(f"\nSummary:")
    print(f"  {n_blocks} blocks cataloged")
    print(f"  {n_sections} MCC section blocks detected")
    if n_sections:
        print(f"  Section widths found: {sorted(k for k in catalog['mcc_sections'] if isinstance(k, int))}")
    print(f"\nNext step: open mcc_block_catalog.yaml and verify the attribute_map")
    print(f"for each mcc_sections entry. The _detected_attributes list shows")
    print(f"what tags were found — match them to the right starter_data keys.")


if __name__ == "__main__":
    main()
