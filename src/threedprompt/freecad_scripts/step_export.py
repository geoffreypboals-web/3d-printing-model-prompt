"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/freecad_scripts/step_export.py
Description: Headless FreeCADCmd script - converts a mesh file into a
    STEP solid (a best-effort B-rep wrap of the mesh's triangles, not
    true reverse-engineered parametric CAD - a flat face becomes one
    real STEP planar face, but there's no curve/fillet recovery). See
    docs/adr/0005-freecad-third-cad-backend.md. Invoked by
    freecad_cad.py's mesh_to_step(); never imported directly.
Inputs: --input <mesh path>, --output <step path>, --report <json path>.
Outputs: Writes the STEP file to --output and a JSON report
    ({"ok": true, "volume": ...} or {"ok": false, "error": ...}) to
    --report.
Troubleshooting:
    - Uses shape.exportStep(path) - never Part.export([shape], path).
      The latter silently writes a STEP file with no solid geometry at
      all when given a bare Part.Shape/Solid (as opposed to a document
      object): confirmed live, Part.export produced a 20-entity file
      with no MANIFOLD_SOLID_BREP, importable nowhere, while
      .exportStep() wrote the full 170-entity B-rep correctly on the
      exact same shape.
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
    """Load --input as a solid and write it to --output as STEP + --report."""
    args = _parse_args()
    try:
        solid = _shared.mesh_to_solid(args.input)
        solid.exportStep(args.output)
        result = {"ok": True, "volume": solid.Volume}
    except Exception as exc:  # noqa: BLE001 - reported via JSON, not re-raised
        result = {"ok": False, "error": str(exc), "traceback": traceback.format_exc()}

    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)


main()
