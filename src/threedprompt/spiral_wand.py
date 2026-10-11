"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/spiral_wand.py
Description: Spiral wand generator - no LLM call, no CAD subprocess. Builds
    two kinds of printable parts with manifold3d (in-process mesh
    booleans) and trimesh (mesh I/O):
      - The wand: a handle with grip rings, a stop flange, a plain
        "rest" neck (handle_gap), a twisted lobed rod (the spiral), and a
        separate end cap (dome / disc / cone / none / any uploaded mesh)
        that plugs onto a D-shaped peg. Wands taller than
        max_print_height are cut inside the spiral into D-pegged
        sections; the twist runs continuously across each joint.
      - A rider: any figurine mesh with a matching twisted bore cut
        straight through it, so it threads onto the wand and, held tip
        up, spins all the way down under gravity.
    Both use the same rod settings (rod_diameter_mm, lobes, lobe_depth_mm,
    pitch_mm, handed, clearance_mm) - a rider only fits a wand built with
    identical values. See docs/adr/0014-spiral-wand-manifold3d.md for why
    this uses manifold3d rather than Blender/OpenSCAD.
Inputs: make_wand(output_dir, **params) and make_rider(input_path,
    output_dir, **params) - params mirror SpiralWandRequest /
    SpiralRiderRequest in models.py. A cap_mesh_path (optional) for a
    custom end cap.
Outputs: STL files written into output_dir, and a SpiralWandResult
    (models.py) listing each part, where it sits in the assembled wand
    (for the browser viewer), and any non-fatal notes for the user.
Troubleshooting:
    - SpiralWandError "not a closed solid": the input mesh has holes or
      non-manifold edges that trimesh's light repair couldn't fix - run
      it through the watertight checker (/watertight.html) first and use
      the repaired model_id.
    - A rider that binds on the printed wand: raise clearance_mm (0.45 mm
      default is a starting guess, not a print-tested value) on BOTH
      parts, or rebuild just the rider with more clearance.
    - "sleeve shows" notes: the figurine is narrower than the bore wall
      over part of its height - scale it up (height_mm) or use a slimmer
      rod for both wand and rider.
    - Very slow rider builds: decimate huge (>1M triangle) scans first;
      manifold3d is fast but the bore boolean is still O(triangles).
"""

from __future__ import annotations

import math
from pathlib import Path

import manifold3d as mf
import numpy as np
import trimesh

from threedprompt.logging_config import get_logger
from threedprompt.models import SpiralWandPart, SpiralWandResult

logger = get_logger(__name__)

# Mesh formats trimesh can read without extra optional dependencies.
SUPPORTED_EXTENSIONS = {".stl", ".obj", ".ply", ".glb", ".gltf", ".off"}

# Below this helix angle a gravity-driven rider tends to stick (inference
# from friction-angle reasoning, not a print-tested threshold).
_MIN_HELIX_ANGLE_DEG = 55.0

_UP_ROTATIONS = {
    "z": np.eye(4),
    "-z": trimesh.transformations.rotation_matrix(math.pi, (1, 0, 0)),
    "y": trimesh.transformations.rotation_matrix(math.pi / 2, (1, 0, 0)),
    "-y": trimesh.transformations.rotation_matrix(-math.pi / 2, (1, 0, 0)),
    "x": trimesh.transformations.rotation_matrix(-math.pi / 2, (0, 1, 0)),
    "-x": trimesh.transformations.rotation_matrix(math.pi / 2, (0, 1, 0)),
}


class SpiralWandError(RuntimeError):
    """Raised for parameters that can't produce a working part, or an input mesh that can't be used."""


# --------------------------------------------------------------------------
# Mesh helpers
# --------------------------------------------------------------------------
def _to_trimesh(m: mf.Manifold) -> trimesh.Trimesh:
    """Convert a manifold3d solid into a trimesh mesh (for STL export)."""
    mesh = m.to_mesh()
    return trimesh.Trimesh(
        vertices=np.asarray(mesh.vert_properties)[:, :3], faces=np.asarray(mesh.tri_verts), process=False
    )


def _to_manifold(tm: trimesh.Trimesh) -> mf.Manifold:
    """Convert a trimesh mesh into a manifold3d solid; check .status() for validity."""
    mesh = mf.Mesh(
        vert_properties=np.ascontiguousarray(tm.vertices, dtype=np.float32),
        tri_verts=np.ascontiguousarray(tm.faces, dtype=np.uint32),
    )
    return mf.Manifold(mesh)


def _bbox(m: mf.Manifold) -> tuple[np.ndarray, np.ndarray]:
    """Return (min_xyz, max_xyz) of a solid as numpy arrays."""
    box = m.bounding_box()
    return np.array(box[:3]), np.array(box[3:])


def _save(m: mf.Manifold, path: Path) -> list[float]:
    """Write a solid to STL and return its [x, y, z] size in mm."""
    _to_trimesh(m).export(path)
    lo, hi = _bbox(m)
    return [round(float(v), 2) for v in hi - lo]


def _cylinder(r: float, h: float, z0: float = 0.0, segs: int = 96) -> mf.Manifold:
    """Z-axis cylinder of radius r and height h, base at z0."""
    return mf.Manifold.cylinder(h, r, r, segs).translate((0, 0, z0))


def load_solid(path: Path) -> mf.Manifold:
    """
    Load a mesh file and return it as one valid solid.

    Merges duplicate vertices, drops degenerate faces, fills small holes
    and fixes normals, then unions overlapping shells (common in
    downloaded figurines: head, arms and body as separate closed meshes)
    so later booleans see one clean volume.

    Args:
        path: mesh file with a suffix in SUPPORTED_EXTENSIONS.
    Returns:
        The mesh as a manifold3d solid.
    Raises:
        SpiralWandError: unreadable file or not a closed solid after repair.
    """
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise SpiralWandError(f"unsupported mesh format {path.suffix!r}; supported: {sorted(SUPPORTED_EXTENSIONS)}")
    try:
        tm = trimesh.load(path, force="mesh")
    except Exception as exc:  # trimesh raises many exception types for bad files
        logger.error("could not read mesh %s: %s", path, exc)
        raise SpiralWandError(f"could not read a mesh from {path.name}") from exc
    if not isinstance(tm, trimesh.Trimesh) or len(tm.faces) == 0:
        raise SpiralWandError(f"could not read a mesh from {path.name}")

    tm.merge_vertices()
    tm.update_faces(tm.nondegenerate_faces())
    tm.update_faces(tm.unique_faces())
    if not tm.is_watertight:
        trimesh.repair.fill_holes(tm)
    with np.errstate(all="ignore"):  # degenerate shells warn while computing volume; harmless
        trimesh.repair.fix_normals(tm)

    solid = _to_manifold(tm)
    if solid.status() != mf.Error.NoError or solid.is_empty():
        raise SpiralWandError(
            f"{path.name} is not a closed solid even after repair ({solid.status().name}). "
            "Run it through the watertight checker (/watertight.html) and use the repaired model."
        )
    shells = solid.decompose()
    if len(shells) > 1:
        solid = mf.Manifold.batch_boolean(shells, mf.OpType.Add)
    return solid


# --------------------------------------------------------------------------
# The twisted rod and its matching bore
# --------------------------------------------------------------------------
def _rod_profile(rod: dict, grow: float = 0.0) -> mf.CrossSection:
    """Lobed cross-section r(t) = R - d*(1 - cos(n*t))/2, optionally grown outward by `grow` mm."""
    big_r, depth, lobes = rod["rod_diameter_mm"] / 2, rod["lobe_depth_mm"], rod["lobes"]
    segs = 96
    pts = []
    for i in range(segs):
        t = 2 * math.pi * i / segs
        r = big_r - depth * (1 - math.cos(lobes * t)) / 2
        pts.append((r * math.cos(t), r * math.sin(t)))
    cs = mf.CrossSection([pts])
    return cs.offset(grow, mf.JoinType.Round) if grow else cs


def twist_rate_deg_per_mm(rod: dict) -> float:
    """Signed twist rate: positive = right-handed (counter-clockwise going up, seen from above)."""
    sign = 1.0 if rod["handed"] == "right" else -1.0
    return sign * 360.0 / rod["pitch_mm"]


def _twisted(rod: dict, length: float, z0: float = 0.0, grow: float = 0.0) -> mf.Manifold:
    """
    Twisted extrusion of the rod profile from z0 to z0 + length.

    The twist phase comes from absolute z, so separately built pieces line
    up exactly; the rider bore uses this same function so it always fits.
    """
    rate = twist_rate_deg_per_mm(rod)
    twist = rate * length
    divisions = max(4, math.ceil(max(length / 0.5, abs(twist) / 1.5)))
    solid = mf.Manifold.extrude(_rod_profile(rod, grow), length, divisions, twist)
    return solid.rotate((0, 0, rate * z0)).translate((0, 0, z0))


def _inner_radius(rod: dict) -> float:
    """Radius of the rod's valleys (its solid core)."""
    return rod["rod_diameter_mm"] / 2 - rod["lobe_depth_mm"]


def _bore_radius(rod: dict) -> float:
    """Outer radius of the rider's bore (lobe tips plus clearance)."""
    return rod["rod_diameter_mm"] / 2 + rod["clearance_mm"]


def _peg_radius(rod: dict) -> float:
    """Radius of the D-pegs joining sections and the cap."""
    return max(2.0, _inner_radius(rod) * 0.6)


def _d_peg(r: float, h: float, z0: float) -> mf.Manifold:
    """Cylinder with one flat side, so a joint only goes together one way."""
    flat = mf.Manifold.cube((2 * r, 2 * r, h + 2)).translate((r * 0.55, -r, z0 - 1))
    return _cylinder(r, h, z0, 64) - flat


def _chamfer_top(r: float, z_top: float, c: float) -> mf.Manifold:
    """Ring-shaped cutter that bevels the top outer edge of a radius-r cylinder ending at z_top."""
    outer = _cylinder(r + 1, c + 0.01, z_top - c)
    cone = mf.Manifold.cylinder(c + 0.01, r + 0.01, r - c, 96).translate((0, 0, z_top - c))
    return outer - cone


def _rod_notes(rod: dict) -> list[str]:
    """Validate shared rod settings; return non-fatal notes, raise SpiralWandError on unusable ones."""
    if _inner_radius(rod) < 2.5:
        raise SpiralWandError("lobe_depth_mm must leave at least 2.5 mm of core radius (rod_diameter_mm/2 - 2.5)")
    notes = []
    helix_deg = math.degrees(math.atan(rod["pitch_mm"] / (math.pi * rod["rod_diameter_mm"])))
    if helix_deg < _MIN_HELIX_ANGLE_DEG:
        suggest = math.pi * rod["rod_diameter_mm"] * math.tan(math.radians(60))
        notes.append(f"Helix angle is only {helix_deg:.0f} deg, so the rider may stick; try pitch_mm >= {suggest:.0f}.")
    return notes


def _rod_settings(params: dict) -> dict:
    """Pick the shared rod settings out of a request's params."""
    keys = ("rod_diameter_mm", "lobes", "lobe_depth_mm", "pitch_mm", "handed", "clearance_mm")
    return {k: params[k] for k in keys}


# --------------------------------------------------------------------------
# Wand
# --------------------------------------------------------------------------
def _build_cap(
    rod: dict,
    style: str,
    diameter: float,
    height: float,
    peg_length: float,
    joint_clearance: float,
    cap_mesh_path: Path | None,
) -> mf.Manifold:
    """End cap modelled flat side down (its print orientation) with a D-socket in the underside."""
    r = diameter / 2
    if cap_mesh_path is not None:
        topper = load_solid(cap_mesh_path)
        lo, hi = _bbox(topper)
        span = max(hi[0] - lo[0], hi[1] - lo[1])
        k = diameter / span if span > 0 else 1.0
        topper = topper.translate((-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2])).scale((k, k, k))
        seat = _cylinder(min(r, _inner_radius(rod) + 4), peg_length + 2)
        cap = topper.translate((0, 0, peg_length + 1)) + seat
    elif style == "dome":
        cap = mf.Manifold.sphere(r, 96).scale((1, 1, height / r)).trim_by_plane((0, 0, 1), 0.0)
    elif style == "disc":
        cap = _cylinder(r, height) - _chamfer_top(r, height, min(1.5, height / 3))
    else:  # cone
        cap = mf.Manifold.cylinder(height, r, 1.0, 96)
    socket = _d_peg(_peg_radius(rod) + joint_clearance, peg_length + 0.6, -0.01)
    return cap - socket


def make_wand(
    output_dir: Path,
    *,
    rod_diameter_mm: float,
    lobes: int,
    lobe_depth_mm: float,
    pitch_mm: float,
    handed: str,
    clearance_mm: float,
    total_length_mm: float,
    spiral_length_mm: float | None,
    handle_length_mm: float,
    handle_diameter_mm: float,
    handle_gap_mm: float,
    stop_diameter_mm: float,
    stop_thickness_mm: float,
    cap_style: str,
    cap_diameter_mm: float,
    cap_height_mm: float,
    peg_length_mm: float,
    joint_clearance_mm: float,
    max_print_height_mm: float,
    cap_mesh_path: Path | None = None,
) -> SpiralWandResult:
    """
    Build the wand body (split into sections if needed) and its end cap.

    Lengths: give total_length_mm (handle bottom to cap top) and the spiral
    takes what's left, or give spiral_length_mm to fix it directly (the
    total then follows). Parameters mirror SpiralWandRequest in models.py.

    Returns:
        SpiralWandResult with one part per STL written into output_dir.
    Raises:
        SpiralWandError: the requested dimensions can't make a working wand.
    """
    rod = _rod_settings(locals())
    notes = _rod_notes(rod)
    bore_d = 2 * _bore_radius(rod)
    has_cap = cap_style != "none" or cap_mesh_path is not None
    if has_cap and cap_diameter_mm <= bore_d + 1:
        raise SpiralWandError(f"cap_diameter_mm must exceed the rider bore ({bore_d:.1f} mm) by 1 mm")
    if cap_style in ("dome", "cone") and cap_mesh_path is None and cap_height_mm < peg_length_mm + 2:
        raise SpiralWandError("cap_height_mm must be at least peg_length_mm + 2 so the socket doesn't break through")
    if stop_diameter_mm <= bore_d + 1:
        raise SpiralWandError(f"stop_diameter_mm must exceed the rider bore ({bore_d:.1f} mm) by 1 mm")

    hl, hg, st, peg = handle_length_mm, handle_gap_mm, stop_thickness_mm, peg_length_mm
    cap_h = cap_height_mm if has_cap else 0.0
    if spiral_length_mm:
        spiral = float(spiral_length_mm)
        total = hl + st + hg + spiral + cap_h
    else:
        total = float(total_length_mm)
        spiral = total - (hl + st + hg + cap_h)
    if spiral < pitch_mm / 4:
        raise SpiralWandError(
            f"only {spiral:.1f} mm left for the spiral; raise total_length_mm or shrink the handle, gap or cap"
        )

    hr = handle_diameter_mm / 2
    z_neck = hl + st
    z_spiral = z_neck + hg
    z_tip = z_spiral + spiral

    handle = _cylinder(hr, hl)
    for i in range(1, int(hl // 12)):  # grip rings every 12 mm
        groove = mf.CrossSection.circle(1.0, 24).translate((hr + 0.35, 0))
        handle -= mf.Manifold.revolve(groove, 96).translate((0, 0, i * 12))
    handle -= _chamfer_top(hr, 1.5, 1.5).mirror((0, 0, 1)).translate((0, 0, 1.5))  # bevel the bottom edge
    stop = _cylinder(stop_diameter_mm / 2, st, hl) - _chamfer_top(stop_diameter_mm / 2, z_neck, 1.0)
    # The rest neck is slimmer than the spiral's valleys so a parked rider spins freely on it.
    neck = _cylinder(_inner_radius(rod) - 0.2, hg + 0.02, z_neck - 0.01)
    body = mf.Manifold.batch_boolean([handle, stop, neck, _twisted(rod, spiral, z_spiral)], mf.OpType.Add)
    if has_cap:
        body += _d_peg(_peg_radius(rod), peg, z_tip - 0.01)

    # Split into printable sections, cutting only inside the spiral. Each
    # section's height includes the peg sticking out of its top.
    body_h = z_tip + (peg if has_cap else 0)
    cuts: list[float] = []
    if body_h > max_print_height_mm:
        first = min(max_print_height_mm - peg - 1, body_h - 3 * peg)  # leave a usable top section
        if first < z_spiral + peg:
            raise SpiralWandError(
                f"the handle, stop and rest gap ({z_spiral:.0f} mm) leave no room for a joint under "
                "max_print_height_mm; shorten them or raise max_print_height_mm"
            )
        rest_h = body_h - first
        k = math.ceil((rest_h + peg) / (max_print_height_mm - 1))
        while k > 1 and rest_h / k + peg > max_print_height_mm - 1:
            k += 1
        cuts = [first + rest_h / k * i for i in range(k)]

    output_dir.mkdir(parents=True, exist_ok=True)
    parts: list[SpiralWandPart] = []
    rest, z_prev = body, 0.0
    pieces: list[tuple[mf.Manifold, float]] = []
    for zc in cuts:
        upper = rest.trim_by_plane((0, 0, 1), zc)
        lower = rest.trim_by_plane((0, 0, -1), -zc) + _d_peg(_peg_radius(rod), peg, zc - 0.01)
        upper -= _d_peg(_peg_radius(rod) + joint_clearance_mm, peg + 0.6, zc - 0.01)
        pieces.append((lower, z_prev))
        rest, z_prev = upper, zc
    pieces.append((rest, z_prev))

    for i, (piece, z0) in enumerate(pieces, 1):
        name = "wand_body.stl" if len(pieces) == 1 else f"wand_section{i}.stl"
        size = _save(piece.translate((0, 0, -z0)), output_dir / name)
        parts.append(SpiralWandPart(filename=name, role="wand", z_offset_mm=round(z0, 3), size_mm=size))
    if has_cap:
        cap = _build_cap(rod, cap_style, cap_diameter_mm, cap_h, peg, joint_clearance_mm, cap_mesh_path)
        size = _save(cap, output_dir / "wand_cap.stl")
        parts.append(SpiralWandPart(filename="wand_cap.stl", role="cap", z_offset_mm=round(z_tip, 3), size_mm=size))
    if len(pieces) > 1:
        notes.append(f"Split into {len(pieces)} sections to fit {max_print_height_mm:.0f} mm; glue the D-pegs.")

    logger.info("built wand: total %.1f mm, spiral %.1f mm, %d part(s)", total, spiral, len(parts))
    return SpiralWandResult(
        parts=parts,
        notes=notes,
        total_length_mm=round(total, 2),
        spiral_length_mm=round(spiral, 2),
        spiral_turns=round(spiral / pitch_mm, 2),
        rest_z_mm=round(z_neck, 3),
        spiral_start_z_mm=round(z_spiral, 3),
        spiral_end_z_mm=round(z_tip, 3),
        twist_deg_per_mm=twist_rate_deg_per_mm(rod),
    )


# --------------------------------------------------------------------------
# Rider
# --------------------------------------------------------------------------
def make_rider(
    input_path: Path,
    output_dir: Path,
    *,
    rod_diameter_mm: float,
    lobes: int,
    lobe_depth_mm: float,
    pitch_mm: float,
    handed: str,
    clearance_mm: float,
    height_mm: float | None,
    scale: float,
    up_axis: str,
    offset_x_mm: float,
    offset_y_mm: float,
    rotate_deg: float,
    collar: str,
    collar_wall_mm: float,
    collar_height_mm: float,
    base_diameter_mm: float,
    base_thickness_mm: float,
) -> SpiralWandResult:
    """
    Turn a figurine mesh into a rider with a matching twisted bore.

    The figurine is oriented (up_axis), scaled (height_mm, else scale),
    centred on the bore (shifted by offset_x/y_mm, spun by rotate_deg),
    optionally stood on a round base, sleeved around the bore (collar) and
    then bored through its full height. Parameters mirror
    SpiralRiderRequest in models.py.

    Returns:
        SpiralWandResult with a single "rider.stl" part (z_offset_mm 0).
    Raises:
        SpiralWandError: unusable mesh or settings.
    """
    rod = _rod_settings(locals())
    notes = _rod_notes(rod)
    fig = load_solid(input_path).transform(_UP_ROTATIONS[up_axis][:3, :])
    lo, hi = _bbox(fig)
    if hi[2] - lo[2] <= 0:
        raise SpiralWandError("the figurine has no height along the chosen up_axis")
    k = float(height_mm) / (hi[2] - lo[2]) if height_mm else float(scale)
    centre = ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2])
    fig = fig.translate(tuple(-c for c in centre)).scale((k, k, k)).rotate((0, 0, rotate_deg))
    fig = fig.translate((-offset_x_mm, -offset_y_mm, 0))
    if base_diameter_mm > 0:
        fig = fig.translate((0, 0, base_thickness_mm)) + _cylinder(base_diameter_mm / 2, base_thickness_mm)
    height = float(_bbox(fig)[1][2])

    sleeve_r = _bore_radius(rod) + collar_wall_mm
    # How much of the height is too slim to hide the sleeve (or, with no sleeve, to hold the bore)?
    ring = mf.CrossSection.circle(sleeve_r, 64)
    zs = np.linspace(0.5, height - 0.5, 60)
    exposed = sum((ring - fig.slice(float(z))).area() > 0.05 * ring.area() for z in zs) / len(zs)
    if exposed > 0.05:
        what = "the bore sleeve shows" if collar != "none" else "the bore breaks out of the figurine"
        notes.append(
            f"{what.capitalize()} over about {exposed:.0%} of the height (the figurine is slimmer than "
            f"{2 * sleeve_r:.1f} mm there). Scale it up with height_mm or use a slimmer rod."
        )
    if collar == "full":
        fig += _cylinder(sleeve_r, height)
    elif collar == "base":
        fig += _cylinder(sleeve_r, min(collar_height_mm, height))

    rider = fig - _twisted(rod, height + 2, -1.0, grow=clearance_mm)
    lead_in = mf.Manifold.cylinder(1.2, _bore_radius(rod) + 1.0, _bore_radius(rod) - 0.2, 96)
    rider -= lead_in.translate((0, 0, -0.01))
    rider -= lead_in.mirror((0, 0, 1)).translate((0, 0, height + 0.01))

    # Drop slivers the bore cut loose; keep (but flag) real parts that don't touch the sleeve.
    pieces = rider.decompose()
    if len(pieces) > 1:
        biggest = max(p.volume() for p in pieces)
        kept = [p for p in pieces if p.volume() > 0.01 * biggest]
        if len(kept) > 1:
            notes.append(f"The rider is {len(kept)} separate pieces; parts not touching the bore sleeve will fall off.")
        rider = mf.Manifold.batch_boolean(kept, mf.OpType.Add)
    if rider.is_empty():
        raise SpiralWandError("nothing is left after cutting the bore; the figurine is smaller than the rod")

    output_dir.mkdir(parents=True, exist_ok=True)
    size = _save(rider, output_dir / "rider.stl")
    logger.info("built rider from %s: height %.1f mm", input_path.name, height)
    return SpiralWandResult(
        parts=[SpiralWandPart(filename="rider.stl", role="rider", z_offset_mm=0.0, size_mm=size)],
        notes=notes,
        twist_deg_per_mm=twist_rate_deg_per_mm(rod),
        rider_height_mm=round(height, 2),
    )
