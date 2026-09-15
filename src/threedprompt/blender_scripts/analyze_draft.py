"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/blender_scripts/analyze_draft.py
Description: Runs *inside* Blender (`blender --background --python
    analyze_draft.py -- --input <mesh> --output <json> --pull-axis
    {x,y,z} --min-draft-angle-deg <float>`), never imported directly.
    Implements FR-4 (draft-angle/undercut analysis): for a mold split at
    the model's own bounding-box midpoint along --pull-axis (the same
    parting-plane convention make_mold.py uses), computes each face's
    "draft angle" against the pull direction its half would actually be
    pulled in (+axis for the half above the parting plane, -axis for the
    half below), and groups any face drafted less than the threshold into
    connected islands - a deterministic dot-product heuristic (no LLM/ML,
    matching hole_classifier.py's precedent per ADR 0004), not a mesh
    modification: nothing here changes the input geometry.

    Draft angle convention: 90 degrees minus the angle between the face
    normal and the pull direction. A face whose normal points straight
    along the pull direction (a flat top/bottom, perpendicular to the
    parting plane) gets +90 degrees (ideal, releases immediately). A
    vertical wall parallel to the pull axis (normal perpendicular to the
    pull direction) gets 0 degrees (the plain, undrafted case most
    guides warn about). A face angled back toward the part (an actual
    undercut - the pull direction would have to pass back through solid
    material to release it) gets a *negative* draft angle. Flagging
    threshold (--min-draft-angle-deg) therefore catches both undercuts
    (negative) and merely-insufficient near-vertical walls (0 up to the
    threshold) in one pass.
Inputs: CLI args after a literal `--`:
    --input <path>                  mesh file: .stl/.obj/.ply/.glb/.gltf/.fbx/.3dm
    --output <path>                 where to write the JSON report
    --pull-axis <x|y|z>              default z
    --min-draft-angle-deg <float>   default 2.0
Outputs: JSON file at --output matching DraftReport's shape in
    ../models.py. On failure, writes {"error": "..."} to that same path
    *and* exits with a non-zero status, matching analyze_watertight.py.
Troubleshooting:
    - "model has no vertices": empty/degenerate input mesh.
    - Every face flagged "insufficient_draft" on an otherwise normal
      model: the parting plane is always the model's own bounding-box
      midpoint along pull_axis (not necessarily its true widest
      cross-section) - a strongly asymmetric model may need a different
      offset, which is FR-6/Phase 6 (configurable parting axis/offset),
      not this script.
    - Island grouping mirrors analyze_watertight.py's
      find_flipped_normal_islands() (a BFS over face-adjacency) so a
      viewer built for one report shape can reuse the same pattern for
      the other.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _shared  # noqa: E402

_AXIS_INDEX = {"x": 0, "y": 1, "z": 2}


def _parse_args():
    """Parse this script's CLI args from the portion of sys.argv after Blender's own `--`."""
    argv = sys.argv
    argv = argv[argv.index("--") + 1 :] if "--" in argv else []
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--pull-axis", choices=["x", "y", "z"], default="z")
    parser.add_argument("--min-draft-angle-deg", type=float, default=2.0)
    return parser.parse_args(argv)


def _face_draft_angle_deg(normal, axis_index: int, half_sign: float) -> float:
    """90 minus the angle (deg) between a unit face normal and its half's pull direction - see module docstring."""
    dot = normal[axis_index] * half_sign
    dot = max(-1.0, min(1.0, dot))
    return 90.0 - math.degrees(math.acos(dot))


def find_problem_face_islands(bm, axis_index: int, parting_coord: float, min_draft_angle_deg: float) -> list[dict]:
    """
    Flag every face drafted less than min_draft_angle_deg for its half's
    pull direction, then group adjacent flagged faces into connected
    islands (BFS over face-edge adjacency, mirroring
    analyze_watertight.py's find_flipped_normal_islands()).
    """
    from mathutils import Vector

    problem_angle = {}
    for f in bm.faces:
        centroid = f.calc_center_median()
        half_sign = 1.0 if centroid[axis_index] >= parting_coord else -1.0
        angle = _face_draft_angle_deg(f.normal, axis_index, half_sign)
        if angle < min_draft_angle_deg:
            problem_angle[f.index] = angle

    if not problem_angle:
        return []

    face_by_index = {f.index: f for f in bm.faces}
    visited = set()
    islands = []
    for start_index in problem_angle:
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
                    if nf.index in problem_angle and nf.index not in visited:
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
        worst_angle = min(problem_angle[idx] for idx in island)
        result.append(
            {
                "id": i,
                "face_indices": island,
                "centroid": (centroid.x, centroid.y, centroid.z),
                "face_count": len(island),
                "min_draft_angle_deg": worst_angle,
                "classification": "undercut" if worst_angle < 0 else "insufficient_draft",
            }
        )
    return result


def analyze_draft(bpy, input_path: str, pull_axis: str, min_draft_angle_deg: float) -> dict:
    """Build the full JSON-able draft-analysis report dict for one imported mesh file."""
    import bmesh

    bpy.ops.wm.read_factory_settings(use_empty=True)
    _shared.import_mesh(bpy, input_path)
    obj = _shared.join_into_single_object(bpy)

    axis_index = _AXIS_INDEX[pull_axis]

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    bm.normal_update()

    coords_on_axis = [v.co[axis_index] for v in bm.verts]
    if not coords_on_axis:
        raise ValueError(f"model at {input_path} has no vertices")
    parting_coord = (min(coords_on_axis) + max(coords_on_axis)) / 2

    islands = find_problem_face_islands(bm, axis_index, parting_coord, min_draft_angle_deg)

    report = {
        "source_path": input_path,
        "pull_axis": pull_axis,
        "parting_coordinate": parting_coord,
        "min_draft_angle_deg": min_draft_angle_deg,
        "releasable": not islands,
        "problem_islands": islands,
        "vertex_count": len(bm.verts),
        "face_count": len(bm.faces),
        "blender_version": ".".join(str(v) for v in bpy.app.version),
    }
    bm.free()
    return report


def main():
    """CLI entry point: parse args, run analyze_draft(), write the JSON report (or a JSON error) to --output."""
    args = _parse_args()
    try:
        import bpy
    except ImportError as exc:  # pragma: no cover - only fails outside Blender
        raise RuntimeError(
            "analyze_draft.py must be run inside Blender (blender --background --python analyze_draft.py -- ...)"
        ) from exc

    try:
        report = analyze_draft(bpy, args.input, args.pull_axis, args.min_draft_angle_deg)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
    except Exception as exc:  # noqa: BLE001 - must still emit a JSON error for the caller
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump({"error": str(exc)}, f, indent=2)
        print(f"analyze_draft.py failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
