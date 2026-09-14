"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/freecad_scripts/_shared.py
Description: Mesh-to-solid conversion and CLI-argv helpers shared by
    thicken.py, step_export.py, step_import.py, and heal.py. Only
    importable from inside FreeCADCmd (uses the Part/Mesh modules), and
    never imported by pytest - these scripts need a real FreeCAD process.
Inputs: N/A (a library of functions; see each function's docstring).
Outputs: N/A.
Troubleshooting:
    - "Null input shape" from a caller's later Part Thickness call
      usually means mesh_to_solid()'s removeSplitter() step was skipped
      somewhere, not a bug here - FreeCAD's Part Thickness has no "fully
      sealed shell" mode (unlike Blender's Solidify), it always needs at
      least one face nominated as the opening, and a raw mesh-derived
      solid has one face per triangle until removeSplitter() merges
      coplanar triangles into the real faces that operation expects.
    - If a script's args come back wrong, remember FreeCADCmd (unlike
      Blender) does NOT strip its own arguments before your script's
      sys.argv - parse_freecad_args() does that slicing manually; a
      script that calls sys.argv directly instead will see
      [freecadcmd_path, script_path, "--", ...] rather than just the
      script's own args.
"""

from __future__ import annotations

import sys

import Mesh
import Part


def mesh_to_solid(path: str, tolerance: float = 0.05):
    """
    Load a mesh file (.stl/.obj/.ply - whatever FreeCAD's Mesh module
    reads) and return a valid, face-merged Part solid ready for a
    Thickness or STEP-export operation. Raises Part.OCCError if the mesh
    isn't closed/manifold enough to form a solid - callers that want to
    heal a broken mesh first should not go through this helper (see
    heal.py, which works on the raw pre-solid shape instead).
    """
    mesh = Mesh.Mesh(path)
    shape = Part.Shape()
    shape.makeShapeFromMesh(mesh.Topology, tolerance)
    return Part.makeSolid(shape).removeSplitter()


def parse_freecad_args(parser):
    """
    Parse this script's own CLI args out of sys.argv and hand them to an
    argparse.ArgumentParser. FreeCADCmd invokes a script as
    `freecadcmd script.py -- --input X --output Y`, but (confirmed live
    against FreeCAD 1.0.0) does NOT strip everything through "--" the way
    Blender's `--python script.py --` convention does - sys.argv here is
    still [freecadcmd_path, script_path, "--", "--input", "X", ...], so
    this slices past the literal "--" itself before delegating to argparse.
    """
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else sys.argv[2:]
    return parser.parse_args(argv)
