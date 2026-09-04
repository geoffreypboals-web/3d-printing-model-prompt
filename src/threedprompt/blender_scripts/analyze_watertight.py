"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/blender_scripts/analyze_watertight.py
Description: Runs *inside* Blender (`blender --background --python
    analyze_watertight.py -- --input <mesh> --output <json>`), never
    imported directly — `bpy`/`bmesh` only exist inside the Blender
    executable's own Python. Loads a mesh, finds every boundary-edge loop
    ("hole" — a location where the surface isn't watertight) and every
    connected island of faces whose winding disagrees with its neighbors
    (the "inverted / wrinkle" defect the project owner described), and
    writes a JSON report matching the WatertightReport shape in
    ../models.py. Classification of each hole (intentional opening vs.
    likely defect) is deliberately left blank here — classifier.py does
    that afterwards in plain Python so it stays unit-testable without a
    Blender install. Shares its mesh-loading and hole-grouping logic with
    close_holes.py via _shared.py so hole ids stay consistent between an
    analysis run and a later repair run on the same file.
Inputs: CLI args after a literal `--`:
    --input <path>   mesh file: .stl/.obj/.ply/.glb/.gltf/.fbx
    --output <path>  where to write the JSON report
Outputs: JSON file at --output. On failure, writes {"error": "..."} to
    that same path *and* exits with a non-zero status, so a caller that
    only checks the exit code and one that reads the file both see the
    failure.
Troubleshooting:
    - "operator ... could not be found": Blender renamed an import/export
      operator between versions (e.g. wm.stl_import replaced
      import_mesh.stl in 4.x) — check `dir(bpy.ops.wm)` /
      `dir(bpy.ops.import_mesh)` in that Blender's own Python console.
    - Every hole reported with area 0 / planarity 0: the mesh likely has
      duplicate/zero-length boundary edges (degenerate geometry) —
      inspect with Blender's own Mesh Analysis overlay before trusting
      the numbers here.
    - Flipped-normal detection compares face normals before/after
      bmesh.ops.recalc_face_normals, so an isolated island that recalc
      "flips back" the *other* way (i.e. Blender picked the opposite
      global orientation to the one you expected) will still show as
      flipped relative to its neighbors — that's the intended, relative
      signal, not an absolute inside/outside judgement.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _shared  # noqa: E402


def _parse_args():
    """Parse this script's CLI args from the portion of sys.argv after Blender's own `--`."""
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1 :]
    else:
        argv = []
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--viewer-output",
        default=None,
        help="Optional .glb path to also export, for the web viewer — "
        "avoids a second Blender invocation just to convert formats.",
    )
    return parser.parse_args(argv)


def _bbox_diagonal(coords) -> float:
    """Diagonal length of the axis-aligned bounding box of coords (mathutils.Vector iterable)."""
    coords = list(coords)
    xs = [c.x for c in coords]
    ys = [c.y for c in coords]
    zs = [c.z for c in coords]
    return math.sqrt((max(xs) - min(xs)) ** 2 + (max(ys) - min(ys)) ** 2 + (max(zs) - min(zs)) ** 2)


def _try_order_loop(edges):
    """
    Best-effort: chain oriented edges v_from -> v_to into a single closed
    ordered loop for a nicer viewer outline. Returns None (falls back to
    an unordered, deduplicated vertex list) for branching/non-simple
    boundary components instead of guessing.
    """
    next_of = {}
    for e in edges:
        v_from, v_to = _shared.oriented_endpoints(e)
        if v_from.index in next_of:
            return None  # branching boundary — not a simple loop
        next_of[v_from.index] = v_to.index

    if not next_of:
        return None
    start = next(iter(next_of))
    ordered = [start]
    current = start
    for _ in range(len(next_of)):
        current = next_of.get(current)
        if current is None:
            return None
        if current == start:
            return ordered
        ordered.append(current)
    return None


def describe_hole(hole_id: int, edges) -> dict:
    """Compute id/vertex loop/centroid/area/perimeter/planarity for one boundary component."""
    from mathutils import Vector

    unique_verts = {}
    for e in edges:
        for v in e.verts:
            unique_verts[v.index] = v.co.copy()

    centroid = Vector((0.0, 0.0, 0.0))
    for co in unique_verts.values():
        centroid += co
    centroid /= len(unique_verts)

    perimeter = 0.0
    area_vector = None
    for e in edges:
        v_from, v_to = _shared.oriented_endpoints(e)
        perimeter += (v_to.co - v_from.co).length
        cross = (v_from.co - centroid).cross(v_to.co - centroid)
        area_vector = cross if area_vector is None else area_vector + cross

    area = 0.5 * area_vector.length if area_vector else 0.0
    normal = area_vector.normalized() if area_vector and area_vector.length > 1e-12 else None

    if normal is not None and unique_verts:
        deviations = [abs((co - centroid).dot(normal)) for co in unique_verts.values()]
        diag = _bbox_diagonal(unique_verts.values())
        avg_dev = sum(deviations) / len(deviations)
        planarity = max(0.0, 1.0 - (avg_dev / diag if diag > 1e-9 else 0.0))
    else:
        planarity = 0.0

    ordered_indices = _try_order_loop(edges) or sorted(unique_verts.keys())

    return {
        "id": hole_id,
        "vertex_indices": ordered_indices,
        "centroid": (centroid.x, centroid.y, centroid.z),
        "area": area,
        "perimeter": perimeter,
        "planarity": planarity,
        "classification": "ambiguous",
        "confidence": 0.0,
        "reason": "",
    }


def find_flipped_normal_islands(bm):
    """
    Flip-detect via bmesh.ops.recalc_face_normals: faces whose normal
    direction changes after Blender re-derives a locally-consistent
    orientation were disagreeing with their neighbors beforehand — the
    "unexpected wrinkle" pattern. Grouped into connected islands so the
    viewer can highlight each defect region once instead of per-face.
    """
    import bmesh
    from mathutils import Vector

    bm.normal_update()
    before = {f.index: f.normal.copy() for f in bm.faces}

    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    bm.normal_update()

    flipped = {f.index for f in bm.faces if before[f.index].dot(f.normal) < -1e-6}
    if not flipped:
        return []

    face_by_index = {f.index: f for f in bm.faces}
    visited = set()
    islands = []
    for start_index in flipped:
        if start_index in visited:
            continue
        stack = [start_index]
        island = []
        while stack:
            idx = stack.pop()
            if idx in visited:
                continue
            visited.add(idx)
            island.append(idx)
            face = face_by_index[idx]
            for e in face.edges:
                for nf in e.link_faces:
                    if nf.index in flipped and nf.index not in visited:
                        stack.append(nf.index)
        islands.append(island)

    result = []
    for i, island in enumerate(islands):
        coords = []
        for idx in island:
            coords.extend(v.co for v in face_by_index[idx].verts)
        centroid = Vector((0.0, 0.0, 0.0))
        for co in coords:
            centroid += co
        centroid /= len(coords)
        result.append(
            {
                "id": i,
                "face_indices": island,
                "centroid": (centroid.x, centroid.y, centroid.z),
                "face_count": len(island),
            }
        )
    return result


def analyze(bpy, input_path: str, viewer_output: str | None = None) -> dict:
    """
    Build the full JSON-able report dict for one imported mesh file. If
    viewer_output is given (a .glb path), also export a web-viewable copy
    of the (joined, transform-applied) mesh there, so the caller doesn't
    need a second Blender invocation just to convert formats.
    """
    import bmesh

    bpy.ops.wm.read_factory_settings(use_empty=True)
    _shared.import_mesh(bpy, input_path)
    obj = _shared.join_into_single_object(bpy)

    if viewer_output:
        _shared.export_mesh(bpy, viewer_output)

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    boundary_edges = [e for e in bm.edges if len(e.link_faces) == 1]
    nonmanifold_junction_edges = [e for e in bm.edges if len(e.link_faces) not in (1, 2)]

    holes = [describe_hole(i, component) for i, component in enumerate(_shared.group_boundary_edges(boundary_edges))]

    total_surface_area = sum(f.calc_area() for f in bm.faces)
    flipped_islands = find_flipped_normal_islands(bm)

    xs = [v.co.x for v in bm.verts]
    ys = [v.co.y for v in bm.verts]
    zs = [v.co.z for v in bm.verts]
    bbox = {
        "min": (min(xs), min(ys), min(zs)) if xs else (0.0, 0.0, 0.0),
        "max": (max(xs), max(ys), max(zs)) if xs else (0.0, 0.0, 0.0),
    }

    report = {
        "source_path": input_path,
        "is_watertight": not holes and not nonmanifold_junction_edges,
        "vertex_count": len(bm.verts),
        "face_count": len(bm.faces),
        "total_surface_area": total_surface_area,
        "bounding_box": bbox,
        "holes": holes,
        "flipped_normal_islands": flipped_islands,
        "nonmanifold_junction_edge_count": len(nonmanifold_junction_edges),
        "blender_version": ".".join(str(v) for v in bpy.app.version),
    }
    bm.free()
    return report


def main():
    """CLI entry point: parse args, run analyze(), write the JSON report (or a JSON error) to --output."""
    args = _parse_args()
    try:
        import bpy
    except ImportError as exc:  # pragma: no cover - only fails outside Blender
        raise RuntimeError(
            "analyze_watertight.py must be run inside Blender "
            "(blender --background --python analyze_watertight.py -- ...)"
        ) from exc

    try:
        report = analyze(bpy, args.input, viewer_output=args.viewer_output)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
    except Exception as exc:  # noqa: BLE001 - must still emit a JSON error for the caller
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump({"error": str(exc)}, f, indent=2)
        print(f"analyze_watertight.py failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
