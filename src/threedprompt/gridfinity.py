"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/gridfinity.py
Description: Gridfinity sizes and the stepped base profile, built with
    manifold3d. Shared by the tool insert (tool_insert.py) and the
    Gridfinity bin template that follows (docs/print-tools-plan.md item 4).
    Gridfinity is Zack Freedman's open (MIT) storage system: a 42 mm grid,
    7 mm height units, bins 0.5 mm smaller than the grid so they drop in,
    and a 4.75 mm stepped foot under every grid cell (0.8 mm 45 deg
    chamfer, 1.8 mm vertical, 2.15 mm 45 deg chamfer). The sizes here
    follow the published spec; only the numbers are reused, no files.
Inputs: Grid counts (nx, ny) and height units from the caller.
Outputs: manifold3d solids; plain sizes.
Troubleshooting:
    - A bin rocks on the baseplate: print a single-cell test first; the
      foot is a polygon approximation of the spec's rounded corners
      (FOOT_SEGMENTS), and very loose or tight fits usually come from
      slicer XY compensation, not the profile.
"""

from __future__ import annotations

import manifold3d as mf

GRID_MM = 42.0  # grid pitch
CLEARANCE_MM = 0.5  # a bin is this much smaller than its grid footprint
HEIGHT_UNIT_MM = 7.0
OUTER_RADIUS_MM = 3.75  # corner radius of a bin's outside walls
# Foot profile from the bottom up: (height of the step, inset gained over it).
FOOT_STEPS = ((0.8, 0.8), (1.8, 0.0), (2.15, 2.15))
FOOT_HEIGHT_MM = sum(h for h, _ in FOOT_STEPS)  # 4.75
FOOT_SEGMENTS = 32


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
