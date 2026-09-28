"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/freecad_scripts/heal.py
Description: Headless FreeCADCmd script - repairs malformed B-rep
    topology (small gaps, invalid edges/faces) via OCCT's ShapeFix
    (Part.Shape.fix()), on either a mesh or STEP input. This is a
    different class of repair than watertight.py's Blender-based
    open-boundary hole filling (which closes genuine missing patches in
    a mesh) - use this for a shape that's already "closed-looking" but
    geometrically malformed, e.g. after a rough mesh-to-solid conversion
    or a messy STEP import. Invoked by freecad_cad.py's heal_solid();
    never imported directly.
Inputs: --input <mesh or step path>, --output <mesh path>,
    --report <json path>, --tolerance <float, default 0.01>.
Outputs: Writes the healed mesh to --output (always mesh, regardless of
    input format) and a JSON report ({"ok": true, "fixed": bool,
    "valid_before": bool, "valid_after": bool, "shells_before": int,
    "shells_after": int} or {"ok": false, "error": ...}) to --report.
Troubleshooting:
    - fix() returning True does not guarantee the output is now
      print-ready - it only means ShapeFix made *some* correction, not
      that every defect is gone; check valid_after, not fixed.
    - Deliberately does NOT call _shared.mesh_to_solid() for mesh input
      (that helper calls Part.makeSolid(), which can itself raise on a
      genuinely broken/non-manifold mesh) - this script builds the raw
      pre-solid shape instead, since fixing exactly that kind of
      brokenness is the point of this script.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _shared  # noqa: E402

_STEP_SUFFIXES = (".step", ".stp")


def _parse_args():
    """Parse this script's --input/--output/--report/--tolerance args."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--tolerance", type=float, default=0.01)
    return _shared.parse_freecad_args(parser)


def _load_shape_for_healing(path: str):
    """Load --input as a raw (possibly broken) shape, without solidifying first - see module docstring."""
    import Mesh
    import Part

    if path.lower().endswith(_STEP_SUFFIXES):
        return Part.read(path)
    mesh = Mesh.Mesh(path)
    shape = Part.Shape()
    shape.makeShapeFromMesh(mesh.Topology, 0.05)
    return shape


def main() -> None:
    """Load --input, run ShapeFix healing, write --output mesh + --report."""
    import Mesh

    args = _parse_args()
    try:
        shape = _load_shape_for_healing(args.input)
        valid_before = shape.isValid()
        healed = shape.copy()
        fixed = healed.fix(args.tolerance, args.tolerance, args.tolerance)
        valid_after = healed.isValid()

        out_mesh = Mesh.Mesh()
        out_mesh.addFacets(healed.tessellate(0.1))
        out_mesh.write(args.output)

        result = {
            "ok": True,
            "fixed": bool(fixed),
            "valid_before": valid_before,
            "valid_after": valid_after,
            "shells_before": len(shape.Shells),
            "shells_after": len(healed.Shells),
        }
    except Exception as exc:  # noqa: BLE001 - reported via JSON, not re-raised
        result = {"ok": False, "error": str(exc), "traceback": traceback.format_exc()}

    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)


main()
