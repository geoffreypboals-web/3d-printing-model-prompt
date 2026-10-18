"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/geometry.py
Description: Shared in-process mesh helpers for the manifold3d-based
    features (spiral_wand.py, split.py and the print tools that follow,
    see docs/print-tools-plan.md): loading and lightly repairing a mesh
    file into one valid solid, converting between trimesh and manifold3d,
    STL export, primitive builders (cylinders, one-way D-pegs), the
    "does this fit a print bed in any orientation" test and the overhang
    (needs-support) measurement.
Inputs: Mesh file paths (SUPPORTED_EXTENSIONS) and manifold3d solids.
Outputs: manifold3d solids, STL files, plain Python values.
Troubleshooting:
    - GeometryError "not a closed solid": the mesh still has holes or
      non-manifold edges after trimesh's light repair - run it through the
      watertight checker (/watertight.html) and use the repaired model_id.
    - fits_bed() says no for a part you can print: it only tries the 6
      axis-aligned orientations (no tilted placements) and keeps a margin
      (default 1 mm) on every side.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import manifold3d as mf
import numpy as np
import trimesh

from threedprompt.logging_config import get_logger

logger = get_logger(__name__)

# Mesh formats trimesh can read without extra optional dependencies.
SUPPORTED_EXTENSIONS = {".stl", ".obj", ".ply", ".glb", ".gltf", ".off"}


class GeometryError(RuntimeError):
    """Raised for an input mesh that can't be read or isn't a closed solid."""


class TemplateError(RuntimeError):
    """Raised by the rule-based templates for settings that can't make a printable part."""


def to_trimesh(m: mf.Manifold) -> trimesh.Trimesh:
    """Convert a manifold3d solid into a trimesh mesh (for STL export)."""
    mesh = m.to_mesh()
    return trimesh.Trimesh(
        vertices=np.asarray(mesh.vert_properties)[:, :3], faces=np.asarray(mesh.tri_verts), process=False
    )


def to_manifold(tm: trimesh.Trimesh) -> mf.Manifold:
    """Convert a trimesh mesh into a manifold3d solid; check .status() for validity."""
    mesh = mf.Mesh(
        vert_properties=np.ascontiguousarray(tm.vertices, dtype=np.float32),
        tri_verts=np.ascontiguousarray(tm.faces, dtype=np.uint32),
    )
    return mf.Manifold(mesh)


def bbox(m: mf.Manifold) -> tuple[np.ndarray, np.ndarray]:
    """Return (min_xyz, max_xyz) of a solid as numpy arrays."""
    box = m.bounding_box()
    return np.array(box[:3]), np.array(box[3:])


def size(m: mf.Manifold) -> list[float]:
    """Return the [x, y, z] size of a solid's bounding box in mm, rounded to 0.01."""
    lo, hi = bbox(m)
    return [round(float(v), 2) for v in hi - lo]


def save(m: mf.Manifold, path: Path) -> list[float]:
    """Write a solid to STL and return its [x, y, z] size in mm."""
    to_trimesh(m).export(path)
    return size(m)


def cylinder(r: float, h: float, z0: float = 0.0, segs: int = 96) -> mf.Manifold:
    """Z-axis cylinder of radius r and height h, base at z0."""
    return mf.Manifold.cylinder(h, r, r, segs).translate((0, 0, z0))


def d_peg(r: float, h: float, z0: float) -> mf.Manifold:
    """Z-axis cylinder with one flat side, base at z0, so a joint only goes together one way."""
    flat = mf.Manifold.cube((2 * r, 2 * r, h + 2)).translate((r * 0.55, -r, z0 - 1))
    return cylinder(r, h, z0, 64) - flat


def fit_orientation(dims: list[float] | np.ndarray, bed: list[float], margin: float = 1.0) -> tuple[int, ...] | None:
    """
    Find an axis-aligned orientation in which a box of `dims` fits a bed.

    Args:
        dims: [x, y, z] size of the part in mm.
        bed: [x, y, z] printable volume in mm.
        margin: clearance kept on each axis, in mm.
    Returns:
        A permutation p such that dims[p[i]] + margin <= bed[i] for every i
        (part axis p[i] lies along bed axis i), preferring the original
        orientation; None if no orientation fits.
    """
    for perm in itertools.permutations(range(3)):
        if all(dims[perm[i]] + margin <= bed[i] for i in range(3)):
            return perm
    return None


def overhang_report(mesh: trimesh.Trimesh, angle_deg: float = 45.0, bed_tolerance: float = 0.3) -> dict:
    """
    Measure the surface that would need support when printed as oriented (Z up).

    A face needs support when it points down more steeply than `angle_deg`
    from vertical (the usual 45 deg rule); faces lying on the bed (within
    `bed_tolerance` mm of the lowest point) don't count.

    Args:
        mesh: the model, in print orientation.
        angle_deg: overhang angle from vertical beyond which support is needed.
        bed_tolerance: height above the lowest point still counted as "on the bed", in mm.
    Returns:
        {"overhang_area_mm2", "total_area_mm2", "overhang_fraction", "overhang_face_count", "angle_deg"}.
    """
    normals = mesh.face_normals
    areas = mesh.area_faces
    face_z = mesh.vertices[mesh.faces][:, :, 2]
    on_bed = face_z.max(axis=1) - mesh.bounds[0][2] <= bed_tolerance
    steep = -normals[:, 2] > np.sin(np.radians(angle_deg))
    flagged = steep & ~on_bed
    total = float(areas.sum())
    over = float(areas[flagged].sum())
    return {
        "overhang_area_mm2": round(over, 2),
        "total_area_mm2": round(total, 2),
        "overhang_fraction": round(over / total, 4) if total else 0.0,
        "overhang_face_count": int(flagged.sum()),
        "angle_deg": angle_deg,
    }


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
        GeometryError: unreadable file or not a closed solid after repair.
    """
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise GeometryError(f"unsupported mesh format {path.suffix!r}; supported: {sorted(SUPPORTED_EXTENSIONS)}")
    try:
        tm = trimesh.load(path, force="mesh")
    except Exception as exc:  # trimesh raises many exception types for bad files
        logger.error("could not read mesh %s: %s", path, exc)
        raise GeometryError(f"could not read a mesh from {path.name}") from exc
    if not isinstance(tm, trimesh.Trimesh) or len(tm.faces) == 0:
        raise GeometryError(f"could not read a mesh from {path.name}")

    tm.merge_vertices()
    tm.update_faces(tm.nondegenerate_faces())
    tm.update_faces(tm.unique_faces())
    if not tm.is_watertight:
        trimesh.repair.fill_holes(tm)
    with np.errstate(all="ignore"):  # degenerate shells warn while computing volume; harmless
        trimesh.repair.fix_normals(tm)

    solid = to_manifold(tm)
    if solid.status() != mf.Error.NoError or solid.is_empty():
        raise GeometryError(
            f"{path.name} is not a closed solid even after repair ({solid.status().name}). "
            "Run it through the watertight checker (/watertight.html) and use the repaired model."
        )
    shells = solid.decompose()
    if len(shells) > 1:
        solid = mf.Manifold.batch_boolean(shells, mf.OpType.Add)
    return solid
