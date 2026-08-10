# =============================================================================
# server.py PATCH
# =============================================================================
# 1. Add this import alongside the other tool imports near the top of server.py:
#
#       from src.tools import mcc_blocks
#
# 2. Paste the section below anywhere after the existing @mcp.tool() blocks.
# =============================================================================


# ===========================================================================
# MCC Block tools
# ===========================================================================

@mcp.tool()
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
    """Insert any named block from the company library at (x, y).

    Parameters
    ----------
    block_name : str
        Name in mcc_block_catalog.yaml (e.g. "MCC_500", "UNIT5080"),
        a full .dwg path, or a block already in the drawing's block table.
    x, y : float
        Insertion point in drawing units (mm).
    x_scale, y_scale : float
        Scale factors (1.0 = no scaling).
    rotation : float
        Rotation in degrees.
    attributes : dict or None
        Attribute tag -> value using exact tag names from the block.
        Section blocks:  {"SECTION": "F1"}
        Unit blocks:     {"UNIT-NO.": "F1A", "STARTER": "FVNR", "EQUIP-NO.": "5"}
    layer : str or None
        Move the block to this layer after insertion.
    """
    return mcc_blocks.insert_block(
        block_name, x, y, x_scale, y_scale, rotation, attributes, layer
    )


@mcp.tool()
def insert_mcc_section(
    section_width: int,
    x: float,
    y: float,
    section_id: str = "",
    variant: str | None = None,
    rotation: float = 0.0,
) -> dict[str, Any]:
    """Insert an MCC section frame block (the enclosure outline).

    Parameters
    ----------
    section_width : int
        Width in mm: 400, 500, 600, 800, or 1000.
    x, y : float
        Insertion point.
    section_id : str
        Section label for the SECTION attribute (e.g. "F1", "G2").
    variant : str or None
        Optional variant block name (e.g. "MCC_400_18M", "MCC_500RH1").
        None uses the standard block for that width.
    rotation : float
        Rotation in degrees.
    """
    return mcc_blocks.insert_mcc_section(
        section_width, x, y, section_id, variant, rotation
    )


@mcp.tool()
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
    """Insert a unit (bucket/starter) block into an MCC section.

    The correct block is resolved automatically from section_width and
    mod_height using the naming formula:
      UNIT[WIDTH_CODE][MOD_CODE]
      e.g. 500mm + 8 mod -> UNIT5080,  1000mm + 4 mod -> UNIT1040

    Parameters
    ----------
    section_width : int
        Width of the parent section in mm (400, 500, 600, 800, 1000).
    mod_height : float
        Height of this unit in mod units (e.g. 5, 8, 12, 24.5).
    x, y : float
        Insertion point — calculated from section Y origin plus cumulative
        height of units already inserted above this one.
    unit_no : str
        Value for UNIT-NO. attribute (e.g. "F1A", "F1B").
    starter_type : str
        Value for STARTER attribute (e.g. "FVNR", "FEEDER", "FVR", "2S1W").
    equip_no : str
        Value for EQUIP-NO. attribute. Defaults to str(mod_height) if blank.
    rotation : float
        Rotation in degrees.
    """
    return mcc_blocks.insert_unit(
        section_width, mod_height, x, y, unit_no, starter_type, equip_no, rotation
    )


@mcp.tool()
def list_mcc_blocks() -> dict[str, Any]:
    """Return all MCC section and unit blocks defined in mcc_block_catalog.yaml."""
    return mcc_blocks.list_mcc_blocks()


@mcp.tool()
def read_block_attributes(handle: str) -> dict[str, Any]:
    """Read all attributes from a block in the drawing by its AutoCAD handle.

    Parameters
    ----------
    handle : str
        AutoCAD entity handle (e.g. "1A2B3C"). Returned by insert_block,
        insert_mcc_section, or insert_unit — also visible in AutoCAD Properties.
    """
    return mcc_blocks.read_block_attributes(handle)


@mcp.tool()
def update_block_attributes(
    handle: str,
    attributes: dict[str, str],
) -> dict[str, Any]:
    """Update attribute values on an already-inserted block by its handle.

    Parameters
    ----------
    handle : str
        AutoCAD entity handle.
    attributes : dict[str, str]
        Attribute tag -> new value (e.g. {"UNIT-NO.": "F2A", "STARTER": "FVR"}).
    """
    return mcc_blocks.update_block_attributes(handle, attributes)
