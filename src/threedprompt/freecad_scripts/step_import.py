"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/freecad_scripts/step_import.py
Description: Headless FreeCADCmd script - converts a STEP file back
    into a tessellated mesh file, so a STEP solid uploaded via
    /step/upload can be previewed/downloaded/thickened the same way as
    any other model. Invoked by freecad_cad.py's step_to_mesh(); never
    imported directly.
Inputs: --input <step path>, --output <mesh path>, --report <json path>.
Outputs: Writes the tessellated mesh to --output and a JSON report
    ({"ok": true, "volume": ..., "solids": ...} or
    {"ok": false, "error": ...}) to --report.
Troubleshooting:
    - Uses Part.read(path) - never Part.insert()/Import.insert()/
      Import.open() (all three silently create zero document objects in
      FreeCAD 1.0.0's Debian package, confirmed live across three
      separate attempts) or Import.StepShape(...).read() (raises
      NotImplementedError - "Not yet implemented" - in this build's
      Python bindings). Part.read() is the one STEP-import path that
      actually works headless.
    - A "Null input shape" result usually means the STEP file itself has
      no MANIFOLD_SOLID_BREP entity (e.g. it was written by
      Part.export([shape], path) instead of shape.exportStep(path) - see
      step_export.py's Troubleshooting section), not a reader bug.
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
    """Parse this script's --input/--output/--report args."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    return _shared.parse_freecad_args(parser)


def main() -> None:
    """Load --input STEP file, tessellate it, write --output mesh + --report."""
    import Mesh
    import Part

    args = _parse_args()
    try:
        shape = Part.read(args.input)
        if not shape.isValid():
            raise ValueError("imported STEP shape is not valid")

        out_mesh = Mesh.Mesh()
        out_mesh.addFacets(shape.tessellate(0.1))
        out_mesh.write(args.output)

        result = {"ok": True, "volume": shape.Volume, "solids": len(shape.Solids)}
    except Exception as exc:  # noqa: BLE001 - reported via JSON, not re-raised
        result = {"ok": False, "error": str(exc), "traceback": traceback.format_exc()}

    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)


main()
