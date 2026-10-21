"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/gridfinity.py
Description: Gridfinity sizes, the stepped base profile and the bin
    template (make_bin: compartments, stacking lip, label tabs, magnet
    holes) and the miniatures bin (make_minis_bin: a socket per base),
    built with manifold3d. The base is shared with the tool insert
    (tool_insert.py); the bin is docs/print-tools-plan.md item 4.
    Gridfinity is Zack Freedman's open (MIT) storage system: a 42 mm grid,
    7 mm height units, bins 0.5 mm smaller than the grid so they drop in,
    and a 4.75 mm stepped foot under every grid cell (0.8 mm 45 deg
    chamfer, 1.8 mm vertical, 2.15 mm 45 deg chamfer). The sizes here
    follow the published spec; only the numbers are reused, no files.
Inputs: Grid counts (nx, ny) and height units from the caller;
    make_bin(output_dir, **params) - params mirror GridfinityBinRequest.
Outputs: manifold3d solids; plain sizes; gridfinity_bin.stl from make_bin.
Troubleshooting:
    - A bin rocks on the baseplate: print a single-cell test first; the
      foot is a polygon approximation of the spec's rounded corners
      (FOOT_SEGMENTS), and very loose or tight fits usually come from
      slicer XY compensation, not the profile.
    - Next bin doesn't seat in the stacking lip: the lip is an
      approximation of the spec's (0.7 / 1.8 / 2.15 mm steps with a 0.4 mm
      flat on top); print without the lip if your bins are a different
      Gridfinity variant.
"""

from __future__ import annotations

from pathlib import Path

import manifold3d as mf

from threedprompt import geometry
from threedprompt.models import TemplatePart, TemplateResult

GRID_MM = 42.0  # grid pitch
CLEARANCE_MM = 0.5  # a bin is this much smaller than its grid footprint
HEIGHT_UNIT_MM = 7.0
OUTER_RADIUS_MM = 3.75  # corner radius of a bin's outside walls
# Foot profile from the bottom up: (height of the step, inset gained over it).
FOOT_STEPS = ((0.8, 0.8), (1.8, 0.0), (2.15, 2.15))
FOOT_HEIGHT_MM = sum(h for h, _ in FOOT_STEPS)  # 4.75
FOOT_SEGMENTS = 32
LIP_TOP_FLAT_MM = 0.4  # the lip's thin flat top, so its edge isn't zero-width
MAGNET_DIAMETER_MM = 6.5  # hole for a 6 x 2 mm magnet
MAGNET_DEPTH_MM = 2.4
MAGNET_OFFSET_MM = 13.0  # from a cell's centre, along x and y
BIN_FILENAME = "gridfinity_bin.stl"


def footprint_mm(units: int) -> float:
    """Outside size of a bin `units` cells wide (42 * units - 0.5)."""
    return GRID_MM * units - CLEARANCE_MM


def rounded_rect(width: float, depth: float, radius: float) -> mf.CrossSection:
    """Rounded rectangle centred on the origin; radius is clamped to fit."""
    radius = max(0.01, min(radius, width / 2 - 0.01, depth / 2 - 0.01))
    core = mf.CrossSection.square((width - 2 * radius, depth - 2 * radius), center=True)
    return core.offset(radius, mf.JoinType.Round, circular_segments=FOOT_SEGMENTS)


def _slab(inset: float, z: float) -> mf.Manifold:
    """A paper-thin footprint slab, `inset` smaller than one bin cell on each side, at height z."""
    size = footprint_mm(1) - 2 * inset
    outline = rounded_rect(size, size, OUTER_RADIUS_MM - inset)
    return mf.Manifold.extrude(outline, 0.01).translate((0, 0, z))


def foot() -> mf.Manifold:
    """One cell's stepped foot, centred on the origin, from z=0 to FOOT_HEIGHT_MM."""
    total_inset = sum(i for _, i in FOOT_STEPS)
    z, inset = 0.0, total_inset
    levels = [(inset, z)]
    for h, gain in FOOT_STEPS:
        z += h
        inset -= gain
        levels.append((inset, z - (0.01 if z >= FOOT_HEIGHT_MM else 0)))
    pieces = [mf.Manifold.batch_hull([_slab(*a), _slab(*b)]) for a, b in zip(levels, levels[1:], strict=False)]
    return mf.Manifold.batch_boolean(pieces, mf.OpType.Add)


def base(nx: int, ny: int) -> mf.Manifold:
    """Feet for an nx x ny bin, centred on the origin in XY, z=0..FOOT_HEIGHT_MM."""
    one = foot()
    feet = [
        one.translate(((i - (nx - 1) / 2) * GRID_MM, (j - (ny - 1) / 2) * GRID_MM, 0))
        for i in range(nx)
        for j in range(ny)
    ]
    return mf.Manifold.batch_boolean(feet, mf.OpType.Add)


def body(nx: int, ny: int, top_z: float) -> mf.Manifold:
    """Solid block of an nx x ny bin from the top of the feet up to top_z, centred in XY."""
    outline = rounded_rect(footprint_mm(nx), footprint_mm(ny), OUTER_RADIUS_MM)
    # Starts 0.01 mm inside the feet so the union has no coplanar faces.
    return mf.Manifold.extrude(outline, top_z - FOOT_HEIGHT_MM + 0.01).translate((0, 0, FOOT_HEIGHT_MM - 0.01))


def _cavity_slab(nx: int, ny: int, inset: float, z: float) -> mf.Manifold:
    """Paper-thin slab of a whole bin's outline, `inset` smaller on each side, at height z."""
    outline = rounded_rect(footprint_mm(nx) - 2 * inset, footprint_mm(ny) - 2 * inset, OUTER_RADIUS_MM - inset)
    return mf.Manifold.extrude(outline, 0.01).translate((0, 0, z))


def lip_parts(nx: int, ny: int, top_z: float) -> tuple[mf.Manifold, mf.Manifold]:
    """
    The stacking lip on top of a bin: (solid ring block, cavity to cut from it).

    The cavity mirrors the foot profile, so the next bin's feet sit in it:
    from the top, a 0.7 mm 45 deg chamfer, 1.8 mm vertical, a 2.15 mm 45
    deg chamfer, leaving a ledge above the compartments.
    """
    height = FOOT_HEIGHT_MM
    outline = rounded_rect(footprint_mm(nx), footprint_mm(ny), OUTER_RADIUS_MM)
    block = mf.Manifold.extrude(outline, height + 0.01).translate((0, 0, top_z - 0.01))
    z, inset = top_z + height, LIP_TOP_FLAT_MM
    levels = [(inset, z + 0.5), (inset, z - 0.01)]  # start above the top so the cut leaves no skin
    for h, gain in reversed(FOOT_STEPS):
        z -= h
        inset += gain
        levels.append((inset, z))
    levels[-1] = (levels[-1][0], top_z - 0.01)
    pieces = [
        mf.Manifold.batch_hull([_cavity_slab(nx, ny, *a), _cavity_slab(nx, ny, *b)])
        for a, b in zip(levels, levels[1:], strict=False)
    ]
    return block, mf.Manifold.batch_boolean(pieces, mf.OpType.Add)


def _label_tab(x0: float, x1: float, y_back: float, top_z: float, depth: float) -> mf.Manifold:
    """A shelf along a compartment's back wall: flat on top, 45 deg underneath (prints without support)."""
    tri = mf.CrossSection([[(0.0, 0.0), (-depth, 0.0), (0.0, -depth)]])  # in (y, z) about the back-top edge
    prism = mf.Manifold.extrude(tri, x1 - x0)  # along the third axis
    # (a, b, c) -> (x = c, y = a, z = b): a cyclic permutation, so no mirroring.
    prism = prism.transform([[0, 0, 1, 0], [1, 0, 0, 0], [0, 1, 0, 0]])
    return prism.translate((x0, y_back, top_z))


def make_bin(
    output_dir: Path,
    *,
    units_x: int,
    units_y: int,
    height_units: int,
    compartments_x: int,
    compartments_y: int,
    wall_mm: float,
    divider_mm: float,
    floor_mm: float,
    stacking_lip: bool,
    label_tab: bool,
    magnet_holes: bool,
) -> TemplateResult:
    """
    Build a Gridfinity bin and write it to gridfinity_bin.stl.

    Args:
        output_dir: where the STL goes.
        units_x, units_y: footprint in 42 mm grid units.
        height_units: body height in 7 mm units (the lip, if any, is on top).
        compartments_x, compartments_y: grid of compartments inside.
        wall_mm: outer wall thickness.
        divider_mm: thickness of the walls between compartments.
        floor_mm: floor above the feet.
        stacking_lip: add the lip that the next bin's feet sit in.
        label_tab: a 45 deg shelf along the back of each compartment.
        magnet_holes: 6.5 x 2.4 mm holes in the four corners of each foot.
    Returns:
        TemplateResult with the one part.
    Raises:
        geometry.TemplateError: settings that leave no room for compartments.
    """
    notes: list[str] = []
    top = height_units * HEIGHT_UNIT_MM
    floor_z = FOOT_HEIGHT_MM + floor_mm
    if floor_z >= top - 2:
        raise geometry.TemplateError("the bin is too short for its floor; add height units")
    solid = base(units_x, units_y) + body(units_x, units_y, top)
    cuts = []
    inner_x = footprint_mm(units_x) - 2 * wall_mm
    inner_y = footprint_mm(units_y) - 2 * wall_mm
    cell_x = (inner_x - (compartments_x - 1) * divider_mm) / compartments_x
    cell_y = (inner_y - (compartments_y - 1) * divider_mm) / compartments_y
    if min(cell_x, cell_y) < 5:
        raise geometry.TemplateError(f"compartments would be {min(cell_x, cell_y):.1f} mm wide; use fewer")
    radius = max(0.5, min(OUTER_RADIUS_MM - wall_mm, cell_x / 2, cell_y / 2))
    tabs = []
    for i in range(compartments_x):
        for j in range(compartments_y):
            cx = -inner_x / 2 + cell_x / 2 + i * (cell_x + divider_mm)
            cy = -inner_y / 2 + cell_y / 2 + j * (cell_y + divider_mm)
            pocket = rounded_rect(cell_x, cell_y, radius).translate((cx, cy))
            cuts.append(mf.Manifold.extrude(pocket, top - floor_z + 0.5).translate((0, 0, floor_z)))
            if label_tab:
                depth = min(12.0, cell_y / 2, top - floor_z - 1)
                # Overlap 0.3 mm into the walls so no tab face is coplanar with a wall face.
                tabs.append(
                    _label_tab(cx - cell_x / 2 - 0.3, cx + cell_x / 2 + 0.3, cy + cell_y / 2 + 0.3, top, depth + 0.3)
                )
    if stacking_lip:
        block, cavity = lip_parts(units_x, units_y, top)
        solid = solid + block
        cuts.append(cavity)
    if magnet_holes:
        for i in range(units_x):
            for j in range(units_y):
                cx, cy = (i - (units_x - 1) / 2) * GRID_MM, (j - (units_y - 1) / 2) * GRID_MM
                for sx in (-1, 1):
                    for sy in (-1, 1):
                        cuts.append(
                            geometry.cylinder(MAGNET_DIAMETER_MM / 2, MAGNET_DEPTH_MM + 0.5, -0.5, 48).translate(
                                (cx + sx * MAGNET_OFFSET_MM, cy + sy * MAGNET_OFFSET_MM, 0)
                            )
                        )
    result = solid - mf.Manifold.batch_boolean(cuts, mf.OpType.Add)
    if tabs:
        result = result + mf.Manifold.batch_boolean(tabs, mf.OpType.Add)
    size_mm = geometry.save(result, output_dir / BIN_FILENAME)
    if not stacking_lip:
        notes.append("No stacking lip: other bins won't stack on this one, but it's easier to reach into.")
    return TemplateResult(
        parts=[TemplatePart(filename=BIN_FILENAME, size_mm=size_mm, print_hint="print as modelled (feet down)")],
        notes=notes,
        report={
            "units": [units_x, units_y, height_units],
            "compartment_mm": [round(cell_x, 2), round(cell_y, 2), round(top - floor_z, 2)],
            "volume_cm3": round(result.volume() / 1000, 1),
        },
    )


MINIS_BIN_FILENAME = "minis_bin.stl"


def make_minis_bin(
    output_dir: Path,
    *,
    units_x: int,
    units_y: int,
    height_units: int,
    base_diameter_mm: float,
    base_clearance_mm: float,
    socket_depth_mm: float,
    socket_gap_mm: float,
    wall_mm: float,
    magnet_diameter_mm: float,
    magnet_depth_mm: float,
    stacking_lip: bool,
) -> TemplateResult:
    """
    Build a Gridfinity bin for miniatures and write it to minis_bin.stl.

    The floor is a grid of round sockets, one per miniature base, so each
    figure stands in its own place and can't slide into its neighbour; above
    them the bin is open up to its height, for the figures themselves.

    Args:
        output_dir: where the STL goes.
        units_x, units_y: footprint in 42 mm grid units.
        height_units: body height in 7 mm units; pick it from the tallest figure.
        base_diameter_mm: the miniatures' round base (25, 32, 40, 50 ...).
        base_clearance_mm: added to the base diameter so bases drop in.
        socket_depth_mm: how deep each base sits (a base is about 3-4 mm thick).
        socket_gap_mm: solid floor left between two sockets.
        wall_mm: outer wall thickness.
        magnet_diameter_mm: hole under each socket for a magnet (0 = none), so
            magnetised bases (or a steel washer in the hole) stay put.
        magnet_depth_mm: depth of that hole.
        stacking_lip: add the lip that the next bin's feet sit in.
    Returns:
        TemplateResult with the one part; report has the socket count.
    Raises:
        geometry.TemplateError: no socket fits, or the bin is too short.
    """
    notes: list[str] = []
    top = height_units * HEIGHT_UNIT_MM
    socket_d = base_diameter_mm + base_clearance_mm
    magnet = magnet_diameter_mm > 0
    under = 0.8 + (magnet_depth_mm if magnet else 0)  # solid floor under a socket (and its magnet)
    floor_z = FOOT_HEIGHT_MM + under + socket_depth_mm
    if floor_z >= top - 2:
        raise geometry.TemplateError("the bin is too short for its sockets; add height units")
    if magnet and magnet_diameter_mm > socket_d - 2:
        raise geometry.TemplateError("the magnet hole is wider than the socket")
    inner_x = footprint_mm(units_x) - 2 * wall_mm
    inner_y = footprint_mm(units_y) - 2 * wall_mm
    pitch = socket_d + socket_gap_mm
    nx = int((inner_x - socket_d) // pitch) + 1 if inner_x >= socket_d else 0
    ny = int((inner_y - socket_d) // pitch) + 1 if inner_y >= socket_d else 0
    if nx < 1 or ny < 1:
        raise geometry.TemplateError(
            f"a {base_diameter_mm:g} mm base doesn't fit in {units_x} x {units_y} units; make the bin bigger"
        )
    solid = base(units_x, units_y) + body(units_x, units_y, top)
    pocket = rounded_rect(inner_x, inner_y, max(0.5, OUTER_RADIUS_MM - wall_mm))
    cuts = [mf.Manifold.extrude(pocket, top - floor_z + 0.5).translate((0, 0, floor_z))]
    z_socket = floor_z - socket_depth_mm
    for i in range(nx):
        for j in range(ny):
            cx = (i - (nx - 1) / 2) * pitch
            cy = (j - (ny - 1) / 2) * pitch
            # 0.01 mm into the open cavity above, so the faces aren't coplanar.
            cuts.append(geometry.cylinder(socket_d / 2, socket_depth_mm + 0.01, z_socket, 96).translate((cx, cy, 0)))
            if magnet:
                cuts.append(
                    geometry.cylinder(
                        magnet_diameter_mm / 2, magnet_depth_mm + 0.01, z_socket - magnet_depth_mm, 48
                    ).translate((cx, cy, 0))
                )
    if stacking_lip:
        block, cavity = lip_parts(units_x, units_y, top)
        solid = solid + block
        cuts.append(cavity)
    result = solid - mf.Manifold.batch_boolean(cuts, mf.OpType.Add)
    size_mm = geometry.save(result, output_dir / MINIS_BIN_FILENAME)
    headroom = top - floor_z
    notes.append(f"Room for figures {headroom:.0f} mm tall above the base (more without a bin stacked on top).")
    if magnet:
        size = f"{magnet_diameter_mm:g} x {magnet_depth_mm:g} mm"
        notes.append(f"Magnet holes {size}: glue the magnets in, all the same pole up.")
    return TemplateResult(
        parts=[TemplatePart(filename=MINIS_BIN_FILENAME, size_mm=size_mm, print_hint="print as modelled (feet down)")],
        notes=notes,
        report={
            "units": [units_x, units_y, height_units],
            "sockets": [nx, ny],
            "socket_count": nx * ny,
            "socket_diameter_mm": round(socket_d, 2),
            "headroom_mm": round(headroom, 2),
            "volume_cm3": round(result.volume() / 1000, 1),
        },
    )
