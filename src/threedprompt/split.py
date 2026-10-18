"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/split.py
Description: Split a model that is too big for a printer into pieces that
    each fit the bed, with alignment connectors on every cut face
    (docs/print-tools-plan.md item 1). The model's bounding box is divided
    into the smallest grid of equal cells (nx x ny x nz) whose cells fit
    the bed in some axis-aligned orientation. On each internal cut face the
    model's cross-section is inset and connectors are placed inside it:
      - "dowel": a matching hole in both pieces plus a printed pin
        (dowel_pin.stl, one per hole pair);
      - "dpeg": a one-way D-shaped peg on the lower piece and a socket in
        the upper one (no loose pins);
      - "none": plain cuts.
    Pieces keep the model's own coordinates, so they reassemble with no
    transform (the viewer just offsets them for an exploded view).
Inputs: split_to_fit(input_path, output_dir, **params) - params mirror
    SplitRequest in models.py.
Outputs: STL files in output_dir (part_NN.stl, optional dowel_pin.stl) and
    a SplitResult listing each piece, whether it fits and how to orient it.
Troubleshooting:
    - "can't split into at most N pieces": the bed is small relative to the
      model; raise max_pieces or use a bigger printer.
    - A cut face gets no connector (noted per face): its cross-section is
      too thin or too small to hold one with a wall around it, or a hole
      there would break through the surface. Glue that joint flat, or use a
      smaller connector_diameter_mm.
    - Dowels too tight/loose: change clearance_mm (radial gap per side).
"""

from __future__ import annotations

import itertools
import math
from pathlib import Path

import manifold3d as mf
import numpy as np

from threedprompt import geometry
from threedprompt.logging_config import get_logger
from threedprompt.models import SplitPart, SplitResult

logger = get_logger(__name__)

_AXES = "XYZ"
# Rotations that map the cut axis onto Z (rows are the new x, y, z), so a
# cut is always a horizontal plane in this frame and manifold's slice() works.
_TO_FRAME = {
    0: np.array([[0, 1, 0, 0], [0, 0, 1, 0], [1, 0, 0, 0]], dtype=float),  # (x,y,z) -> (y,z,x)
    1: np.array([[0, 0, 1, 0], [1, 0, 0, 0], [0, 1, 0, 0]], dtype=float),  # (x,y,z) -> (z,x,y)
    2: np.array([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]], dtype=float),
}
# In-plane (u, v) axes of each frame, as model axes.
_FRAME_UV = {0: (1, 2), 1: (2, 0), 2: (0, 1)}
_WALL_MM = 1.6  # material kept around a connector hole
_MAX_CONNECTORS_PER_FACE = 4


class SplitError(RuntimeError):
    """Raised for an unusable mesh or a split that can't meet the requested limits."""


def _from_frame(axis: int) -> np.ndarray:
    """3x4 transform back from the cut-axis frame to model coordinates (inverse of a permutation)."""
    m = np.zeros((3, 4))
    m[:, :3] = _TO_FRAME[axis][:, :3].T
    return m


def _choose_grid(ext: np.ndarray, bed: list[float], margin: float, extra: float, max_pieces: int):
    """
    Smallest grid (nx, ny, nz) whose cells fit the bed.

    `extra` is added to a cell along every axis that is cut (room for a
    D-peg sticking out of a cut face). Ties prefer fewer cuts overall and
    then cuts along Z (shorter pieces print faster and more reliably).
    """
    best = None
    for n in itertools.product(range(1, max_pieces + 1), repeat=3):
        count = n[0] * n[1] * n[2]
        if count > max_pieces:
            continue
        cell = [ext[i] / n[i] + (extra if n[i] > 1 else 0.0) for i in range(3)]
        if geometry.fit_orientation(cell, bed, margin) is None:
            continue
        key = (count, sum(n), -n[2])
        if best is None or key < best[0]:
            best = (key, n)
    return None if best is None else best[1]


def _keep_clear(axis_edges: np.ndarray, i: int, keep: float) -> tuple[float, float]:
    """Range of cell i along one axis, shrunk by `keep` on each side that is an internal cut (outer sides pad out)."""
    lo = axis_edges[i] + (keep if i > 0 else -1.0)
    hi = axis_edges[i + 1] - (keep if i < len(axis_edges) - 2 else -1.0)
    return float(lo), float(hi)


def _pick_points(region: mf.CrossSection, min_gap: float, per_face: int) -> list[tuple[float, float]]:
    """
    Choose connector centres inside an inset cross-section.

    Every vertex of the inset region is already far enough from the real
    section's edge, so points are taken from its vertices: per component,
    the two vertices farthest apart (or one, if they'd be too close).
    """
    points: list[tuple[float, float]] = []
    for comp in sorted(region.decompose(), key=lambda c: -c.area()):
        if len(points) >= _MAX_CONNECTORS_PER_FACE or comp.area() <= 0:
            continue
        verts = np.array(comp.to_polygons()[0], dtype=float)
        if len(verts) > 200:
            verts = verts[:: math.ceil(len(verts) / 200)]
        if per_face == 1 or len(verts) == 1:
            points.append(tuple(verts[0]))
            continue
        d = np.linalg.norm(verts[:, None, :] - verts[None, :, :], axis=-1)
        i, j = np.unravel_index(np.argmax(d), d.shape)
        points.append(tuple(verts[i]))
        if d[i, j] >= min_gap:
            points.append(tuple(verts[j]))
    return points[:_MAX_CONNECTORS_PER_FACE]


def _connector(axis: int, c: float, u: float, v: float, r: float, z0: float, z1: float, d_shape: bool) -> mf.Manifold:
    """A connector cylinder (round or D) along `axis`, spanning c+z0..c+z1, centred at in-plane (u, v)."""
    body = geometry.d_peg(r, z1 - z0, c + z0) if d_shape else geometry.cylinder(r, z1 - z0, c + z0, 48)
    return body.translate((u, v, 0)).transform(_from_frame(axis))


def split_to_fit(
    input_path: Path,
    output_dir: Path,
    *,
    bed_x_mm: float,
    bed_y_mm: float,
    bed_z_mm: float,
    connector: str,
    connector_diameter_mm: float,
    connector_length_mm: float,
    clearance_mm: float,
    max_pieces: int,
    margin_mm: float,
) -> SplitResult:
    """
    Cut a model into pieces that each fit the bed, with connectors on the cut faces.

    Returns:
        SplitResult with one part per piece (part_NN.stl) and, for dowels,
        a dowel_pin.stl part whose `count` says how many to print.
    Raises:
        SplitError: unusable mesh, or no grid within max_pieces fits the bed.
    """
    try:
        solid = geometry.load_solid(input_path)
    except geometry.GeometryError as exc:
        raise SplitError(str(exc)) from exc

    bed = [bed_x_mm, bed_y_mm, bed_z_mm]
    lo, hi = geometry.bbox(solid)
    ext = hi - lo
    output_dir.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []
    r = connector_diameter_mm / 2
    half = connector_length_mm / 2

    if geometry.fit_orientation(ext, bed, margin_mm) is not None:
        perm = geometry.fit_orientation(ext, bed, margin_mm)
        dims = geometry.save(solid, output_dir / "part_01.stl")
        notes.append("The model already fits this printer; nothing was cut.")
        part = SplitPart(
            filename="part_01.stl", role="piece", size_mm=dims, fits=True, print_hint=_hint(perm), cell=[0, 0, 0]
        )
        return SplitResult(parts=[part], notes=notes, grid=[1, 1, 1], connector_count=0, model_size_mm=dims)

    extra = half + 0.5 if connector == "dpeg" else 0.0
    grid = _choose_grid(ext, bed, margin_mm, extra, max_pieces)
    if grid is None:
        raise SplitError(
            f"can't split a {ext[0]:.0f} x {ext[1]:.0f} x {ext[2]:.0f} mm model into at most {max_pieces} pieces "
            f"that fit {bed[0]:.0f} x {bed[1]:.0f} x {bed[2]:.0f} mm; raise max_pieces or use a bigger printer"
        )
    edges = [np.linspace(lo[i], hi[i], grid[i] + 1) for i in range(3)]

    # Place connectors on every internal cut face.
    holes: list[mf.Manifold] = []
    pegs: dict[tuple[int, int, int], list[mf.Manifold]] = {}
    faces_without = 0
    inset = r + clearance_mm + _WALL_MM
    edge_keep = half + r + 1.0  # keeps connectors on different cut planes from crossing
    per_face = 2 if connector == "dowel" else 1
    if connector != "none":
        for axis in range(3):
            if grid[axis] == 1:
                continue
            frame = solid.transform(_TO_FRAME[axis])
            ua, va = _FRAME_UV[axis]
            for k in range(1, grid[axis]):
                c = float(edges[axis][k])
                section = frame.slice(c)
                others = [i for i in range(3) if i != axis]
                for cells in itertools.product(*(range(grid[o]) for o in others)):
                    idx = dict(zip(others, cells, strict=True))
                    # Keep clear of the face's edges only where another cut meets them;
                    # outer edges are the model's own bounding box, already handled by the inset.
                    u0, u1 = _keep_clear(edges[ua], idx[ua], edge_keep)
                    v0, v1 = _keep_clear(edges[va], idx[va], edge_keep)
                    if u1 <= u0 or v1 <= v0:
                        faces_without += 1
                        continue
                    rect = mf.CrossSection.square((u1 - u0, v1 - v0)).translate((u0, v0))
                    face = section ^ rect
                    if face.area() <= 0:
                        continue  # the model doesn't cross this cell face
                    region = face.offset(-inset, mf.JoinType.Round)
                    placed = 0
                    for u, v in _pick_points(region, 2 * inset + 1, per_face):
                        # Holes start 0.5 mm past the cut plane: a hole face coplanar with the cut
                        # would leave a zero-thickness skin sealing it shut.
                        hole_lo, hole_hi = (-half - 0.5, half + 0.5) if connector == "dowel" else (-0.5, half + 0.5)
                        hole = _connector(axis, c, u, v, r + clearance_mm, hole_lo, hole_hi, connector == "dpeg")
                        # Skip a connector whose hole would break out of the surface.
                        if (hole - solid).volume() > 1e-3 * hole.volume():
                            continue
                        holes.append(hole)
                        if connector == "dpeg":
                            lower = [0, 0, 0]
                            lower[axis] = k - 1
                            for o in others:
                                lower[o] = idx[o]
                            peg = _connector(axis, c, u, v, r, -0.5, half, True)
                            pegs.setdefault(tuple(lower), []).append(peg)
                        placed += 1
                    if placed == 0:
                        faces_without += 1
    if faces_without:
        notes.append(
            f"{faces_without} cut face(s) were too thin or small for a connector; glue those joints flat "
            "or use a smaller connector_diameter_mm."
        )

    cut_solid = solid - mf.Manifold.batch_boolean(holes, mf.OpType.Add) if holes else solid

    # Cut the model into grid cells.
    parts: list[SplitPart] = []
    pad = 1.0  # outer cells reach past the bounding box so no face is coplanar with the model's own
    n = 0
    for cell in itertools.product(range(grid[0]), range(grid[1]), range(grid[2])):
        c_lo = [edges[i][cell[i]] - (pad if cell[i] == 0 else 0.0) for i in range(3)]
        c_hi = [edges[i][cell[i] + 1] + (pad if cell[i] == grid[i] - 1 else 0.0) for i in range(3)]
        box = mf.Manifold.cube(tuple(c_hi[i] - c_lo[i] for i in range(3))).translate(tuple(c_lo))
        piece = cut_solid ^ box
        if pegs.get(cell):
            piece = mf.Manifold.batch_boolean([piece, *pegs[cell]], mf.OpType.Add)
        if piece.is_empty() or piece.volume() < 1e-3:
            continue
        n += 1
        name = f"part_{n:02d}.stl"
        dims = geometry.save(piece, output_dir / name)
        perm = geometry.fit_orientation(dims, bed, margin_mm)
        parts.append(
            SplitPart(
                filename=name,
                role="piece",
                size_mm=dims,
                fits=perm is not None,
                print_hint=_hint(perm),
                cell=list(cell),
            )
        )

    connector_count = len(holes)
    if connector == "dowel" and holes:
        pin = geometry.cylinder(r, 2 * half, 0, 48)
        pin -= _chamfer_ring(r, 2 * half)
        dims = geometry.save(pin, output_dir / "dowel_pin.stl")
        parts.append(
            SplitPart(filename="dowel_pin.stl", role="pin", size_mm=dims, fits=True, count=connector_count, cell=None)
        )
        notes.append(f"Print {connector_count} dowel pin(s) (dowel_pin.stl) and glue them into the holes.")
    if any(not p.fits for p in parts):
        notes.append("Some pieces still don't fit (an odd shape filled a cell); raise max_pieces.")

    logger.info("split %s into %d piece(s), grid %s, %d connector(s)", input_path.name, n, grid, connector_count)
    return SplitResult(
        parts=parts,
        notes=notes,
        grid=list(grid),
        connector_count=connector_count,
        model_size_mm=[round(float(v), 2) for v in ext],
    )


def _chamfer_ring(r: float, length: float, c: float = 0.4) -> mf.Manifold:
    """Cutter that chamfers both ends of a Z pin (radius r, from z=0 to length) so pins start easily."""
    bottom = geometry.cylinder(r + 1, c, 0, 48) - mf.Manifold.cylinder(c, r - c, r + 0.01, 48)
    top = bottom.mirror((0, 0, 1)).translate((0, 0, length))
    return bottom + top


def _hint(perm: tuple[int, ...] | None) -> str | None:
    """Plain-language print orientation for a fit permutation (which model axis stands vertical)."""
    if perm is None:
        return None
    if perm[2] == 2:
        return "print as modelled (Z up)"
    return f"lay it so its {_AXES[perm[2]]} axis points up"
