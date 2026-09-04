"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/blender_scripts/close_holes.py
Description: Runs *inside* Blender (`blender --background --python
    close_holes.py -- --input <mesh> --hole-ids 0,2 --output <mesh> --report-output <json>`).
    Re-derives the same hole ids analyze_watertight.py would report for
    this file (via the shared import/join/group logic in _shared.py),
    caps only the boundary loops the caller asked to close, recalculates
    face normals across the whole mesh so the new caps (and any
    pre-existing inverted-normal "wrinkles") point the right way, exports
    the repaired mesh, then re-imports and re-analyzes *that* output file
    from scratch to produce an independent, trustworthy post-repair
    report — the same WatertightReport JSON shape analyze_watertight.py
    produces, so callers can reuse one JSON schema either way.
Inputs: CLI args after a literal `--`:
    --input <path>          source mesh: .stl/.obj/.ply/.glb/.gltf/.fbx
    --hole-ids <csv>         e.g. "0,2,5" — hole ids (from a prior
                             analyze_watertight.py report on this same
                             file) to cap. Holes not listed are left open.
    --output <path>          where to write the repaired mesh
    --report-output <path>   where to write the post-repair JSON report
Outputs: The repaired mesh at --output and a JSON report at
    --report-output. On failure, writes {"error": "..."} to
    --report-output and exits non-zero.
Troubleshooting:
    - "Hole id N not found": the input file changed (or was re-exported
      by another tool) since the analysis report the caller is working
      from was generated — hole ids are positional and only stable for a
      given file's own boundary-edge scan order, not portable across
      re-saves. Re-run analyze_watertight.py on the exact file being
      repaired and use its fresh ids.
    - A cap looks flipped inside-out after repair in the slicer: this
      script always runs a whole-mesh bmesh.ops.recalc_face_normals pass
      after filling, which should catch it — if it's still wrong, the
      surrounding shell was probably already inconsistent in a way the
      relative flip-detection in analyze_watertight.py couldn't resolve
      (e.g. more than half the shell was inverted, so "the majority
      wins" picked the wrong overall side).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _shared  # noqa: E402
import analyze_watertight  # noqa: E402


def _parse_args():
    """Parse this script's CLI args from the portion of sys.argv after Blender's own `--`."""
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1 :]
    else:
        argv = []
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--hole-ids", required=True, help="Comma-separated hole ids to close")
    parser.add_argument("--output", required=True)
    parser.add_argument("--report-output", required=True)
    parser.add_argument(
        "--viewer-output",
        default=None,
        help="Optional .glb path for an updated web-viewer copy of the repaired mesh.",
    )
    return parser.parse_args(argv)


def close_holes(bpy, input_path: str, hole_ids: set[int], output_path: str) -> None:
    """Cap the requested boundary loops in input_path and write the result to output_path."""
    import bmesh

    bpy.ops.wm.read_factory_settings(use_empty=True)
    _shared.import_mesh(bpy, input_path)
    obj = _shared.join_into_single_object(bpy)

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    boundary_edges = [e for e in bm.edges if len(e.link_faces) == 1]
    components = _shared.group_boundary_edges(boundary_edges)

    available_ids = set(range(len(components)))
    missing = hole_ids - available_ids
    if missing:
        raise ValueError(
            f"Hole id(s) {sorted(missing)} not found in {input_path} "
            f"(this file currently has {len(components)} hole(s), ids 0..{len(components) - 1}); "
            "re-run analysis on this exact file and use its current hole ids."
        )

    for hole_id in hole_ids:
        bmesh.ops.holes_fill(bm, edges=components[hole_id], sides=0)

    # Re-derive consistent outward winding across the whole shell, both for
    # the newly-created caps and for any pre-existing inverted-normal
    # islands the analysis step flagged — this is the "smooth out the
    # wrinkle" part of the repair, not just hole-filling.
    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    bm.normal_update()

    bm.to_mesh(obj.data)
    obj.data.update()
    bm.free()

    _shared.export_mesh(bpy, output_path)


def main():
    """CLI entry point: parse args, run close_holes() + a fresh analyze(), write the JSON report (or error)."""
    args = _parse_args()
    try:
        hole_ids = {int(x) for x in args.hole_ids.split(",") if x.strip() != ""}
    except ValueError as exc:
        print(
            f"close_holes.py: invalid --hole-ids {args.hole_ids!r}: {exc}",
            file=sys.stderr,
        )
        sys.exit(2)

    try:
        import bpy
    except ImportError as exc:  # pragma: no cover - only fails outside Blender
        raise RuntimeError(
            "close_holes.py must be run inside Blender " "(blender --background --python close_holes.py -- ...)"
        ) from exc

    try:
        close_holes(bpy, args.input, hole_ids, args.output)
        report = analyze_watertight.analyze(bpy, args.output, viewer_output=args.viewer_output)
        report["closed_hole_ids"] = sorted(hole_ids)
        with open(args.report_output, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
    except Exception as exc:  # noqa: BLE001 - must still emit a JSON error for the caller
        with open(args.report_output, "w", encoding="utf-8") as f:
            json.dump({"error": str(exc)}, f, indent=2)
        print(f"close_holes.py failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
