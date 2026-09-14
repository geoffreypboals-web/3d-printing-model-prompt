"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/blender_scripts/render_thumbnail.py
Description: Runs *inside* Blender (`blender --background --python
    render_thumbnail.py -- --input <mesh> --output <png> --size <px>`),
    never imported directly -- `bpy` only exists inside the Blender
    executable's own Python. Imports a mesh, auto-frames an orthographic
    camera around its bounding box, and renders a square PNG via the
    Workbench engine's flat studio lighting -- deliberately not
    Eevee/Cycles, since Workbench needs no light/material setup at all and
    is fast enough to run per-file on demand (see thumbnail.py's
    docstring for why that matters at library scale). Shares its mesh
    import/join logic with thickness.py's Solidify script and
    watertight.py's analyze/close scripts via _shared.py.
Inputs: CLI args after a literal `--`:
    --input <path>   mesh file: .stl/.obj/.ply/.glb/.gltf/.fbx/.3dm
    --output <path>  where to write the PNG
    --size <int>     square render resolution in pixels
Outputs: A PNG file at --output (transparent background, RGBA). On
    failure, prints the error to stderr and exits non-zero -- no PNG is
    left behind, which is what thumbnail.py's caller checks for (Blender's
    own exit code is reliable, unlike FreeCADCmd's -- see freecad_cad.py's
    docstring for that contrast).
Troubleshooting:
    - A flat gray silhouette with no shading definition: expected for a
      model with no material under Workbench's 'MATERIAL' color mode --
      it falls back to Blender's default gray rather than failing.
    - "operator ... could not be found": Blender renamed an import
      operator between versions -- see analyze_watertight.py's
      Troubleshooting section for the same issue and how to check.
"""

from __future__ import annotations

import argparse
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
    parser.add_argument("--size", type=int, default=512)
    return parser.parse_args(argv)


def _frame_camera(bpy, obj, margin: float = 1.15):
    """
    Add an orthographic camera angled isometrically, sized to fit obj's
    world-space bounding box with a small margin, and point it at the
    object's center. Orthographic (rather than perspective) keeps every
    thumbnail's apparent scale consistent regardless of camera distance,
    and sidesteps perspective-distortion framing math entirely.
    """
    from mathutils import Vector

    corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    xs, ys, zs = (c.x for c in corners), (c.y for c in corners), (c.z for c in corners)
    center = Vector(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, (min(zs) + max(zs)) / 2))
    radius = max((c - center).length for c in corners) or 1.0

    direction = Vector((1.0, -1.0, 0.7)).normalized()
    camera_data = bpy.data.cameras.new("ThumbnailCamera")
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = radius * 2.0 * margin
    camera = bpy.data.objects.new("ThumbnailCamera", camera_data)
    bpy.context.collection.objects.link(camera)
    camera.location = center + direction * (radius * 4.0 + 1.0)
    camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = camera


def _configure_workbench_render(bpy, size: int) -> None:
    """Set up the Workbench engine's flat studio lighting and a transparent square PNG output."""
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "MATERIAL"
    scene.render.film_transparent = True
    scene.render.resolution_x = size
    scene.render.resolution_y = size
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"


def main() -> None:
    """CLI entry point: import the mesh, frame a camera around it, render a PNG."""
    args = _parse_args()
    try:
        import bpy

        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)

        _shared.import_mesh(bpy, args.input)
        obj = _shared.join_into_single_object(bpy)
        obj.select_set(True)
        bpy.ops.object.shade_smooth()

        _frame_camera(bpy, obj)
        _configure_workbench_render(bpy, max(16, args.size))

        bpy.context.scene.render.filepath = args.output
        bpy.ops.render.render(write_still=True)
    except Exception as exc:  # noqa: BLE001 - reported to stderr + nonzero exit, no JSON report needed here
        print(f"render_thumbnail.py failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
