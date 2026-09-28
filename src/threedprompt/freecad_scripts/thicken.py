"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/freecad_scripts/thicken.py
Description: Headless FreeCADCmd script - shells a mesh by amount_mm
    using FreeCAD's Part Thickness operation, picking the solid's
    largest planar face as the required "opening" (Part Thickness has no
    fully-sealed-shell mode, unlike Blender's Solidify - see _shared.py's
    docstring). Invoked by freecad_cad.py's thicken_mesh(); never
    imported directly.
Inputs: --input <mesh path>, --output <mesh path>, --amount-mm <float>,
    --report <json path> (see _shared.parse_freecad_args()'s docstring
    for FreeCADCmd's argv quirk).
Outputs: Writes the thickened mesh to --output and a JSON report
    ({"ok": true, "original_volume": ..., "thickened_volume": ...} or
    {"ok": false, "error": ...}) to --report.
Troubleshooting:
    - "Null input shape" / result "ok": false: input mesh isn't a
      closed/manifold solid once loaded - repair it first via the
      repair-solid endpoint (heal.py) before thickening.
    - FreeCADCmd prints an unrelated Python traceback to stderr about
      failing to auto-open a file that looks like one of these argv
      values - confirmed harmless (exit code stays 0 regardless, this
      script still runs to completion); freecad_cad.py judges success
      purely by whether --report's JSON exists and parses.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _shared  # noqa: E402


def _parse_args():
    """Parse this script's --input/--output/--amount-mm/--report args."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--amount-mm", required=True, type=float)
    parser.add_argument("--report", required=True)
    return _shared.parse_freecad_args(parser)


def main() -> None:
    """Load --input, thicken it by --amount-mm, write --output + --report."""
    import Mesh

    args = _parse_args()
    try:
        solid = _shared.mesh_to_solid(args.input)
        largest_face = max(solid.Faces, key=lambda f: f.Area)
        thickened = solid.makeThickness([largest_face], -args.amount_mm, 1e-3)
        if not thickened.isValid() or not thickened.Solids:
            raise ValueError("thickening produced an invalid or empty solid")

        out_mesh = Mesh.Mesh()
        out_mesh.addFacets(thickened.tessellate(0.1))
        out_mesh.write(args.output)

        result = {"ok": True, "original_volume": solid.Volume, "thickened_volume": thickened.Volume}
    except Exception as exc:  # noqa: BLE001 - reported via JSON, not re-raised (no one reads this process's exit code)
        result = {"ok": False, "error": str(exc), "traceback": traceback.format_exc()}

    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)


main()
