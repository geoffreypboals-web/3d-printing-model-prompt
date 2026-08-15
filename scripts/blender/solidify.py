"""
Applies a Solidify modifier to every mesh object in a GLB, exports the
result, and renders an auto-framed preview PNG of the outcome.

Usage (invoked headless):
  blender --background --factory-startup --python solidify.py -- \
      <input.glb> <output.glb> <output_preview.png> <thicknessMm> <inside|outside>

Unit assumption: 1 GLB unit is treated as 1mm. Text-to-3D generators do not
guarantee real-world scale, so this is an approximation, not a physical
measurement -- documented for the caller/UI, not hidden here.

"inside" (preserve inside dimensions) -> Solidify offset = +1.0: new material
is added outward, away from center; the original surface becomes an inner
wall and the outer silhouette grows.

"outside" (preserve outside dimensions) -> Solidify offset = -1.0: new
material is added inward; the outer silhouette stays fixed and the center
void shrinks.

Both directions were empirically verified against a known test sphere before
this script was written (see PR description / plan for the numbers).
"""

import math
import sys

import bpy
import mathutils

argv = sys.argv[sys.argv.index("--") + 1:]
if len(argv) != 5:
    raise SystemExit(
        "usage: solidify.py -- <input.glb> <output.glb> <output_preview.png> <thicknessMm> <inside|outside>"
    )

input_path, output_path, preview_path, thickness_mm_raw, direction = argv
thickness_mm = float(thickness_mm_raw)
if direction not in ("inside", "outside"):
    raise SystemExit(f"direction must be 'inside' or 'outside', got: {direction}")
offset = 1.0 if direction == "inside" else -1.0

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=input_path)

mesh_objects = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
if not mesh_objects:
    raise SystemExit("no mesh objects found in imported file")

for obj in mesh_objects:
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    modifier = obj.modifiers.new(name="Solidify", type="SOLIDIFY")
    modifier.thickness = thickness_mm
    modifier.offset = offset
    bpy.ops.object.modifier_apply(modifier=modifier.name)
    dims = obj.dimensions
    print(f"RESULT_DIMS {obj.name} x={dims.x:.4f} y={dims.y:.4f} z={dims.z:.4f}")

bpy.ops.export_scene.gltf(filepath=output_path, export_format="GLB")

# --- Auto-framed preview render ---
min_co = mathutils.Vector((1e9, 1e9, 1e9))
max_co = mathutils.Vector((-1e9, -1e9, -1e9))
for obj in mesh_objects:
    for corner in obj.bound_box:
        world = obj.matrix_world @ mathutils.Vector(corner)
        min_co = mathutils.Vector(map(min, min_co, world))
        max_co = mathutils.Vector(map(max, max_co, world))
center = (min_co + max_co) / 2
radius = (max_co - min_co).length / 2 or 1.0

camera_data = bpy.data.cameras.new("PreviewCamera")
camera_obj = bpy.data.objects.new("PreviewCamera", camera_data)
bpy.context.scene.collection.objects.link(camera_obj)
camera_obj.location = center + mathutils.Vector((radius * 2.2, -radius * 2.2, radius * 1.6))
camera_obj.rotation_euler = (center - camera_obj.location).to_track_quat("-Z", "Y").to_euler()
bpy.context.scene.camera = camera_obj

sun_data = bpy.data.lights.new("PreviewSun", type="SUN")
sun_data.energy = 3.0
sun_obj = bpy.data.objects.new("PreviewSun", sun_data)
sun_obj.rotation_euler = (math.radians(50), 0, math.radians(30))
bpy.context.scene.collection.objects.link(sun_obj)

scene = bpy.context.scene
scene.render.engine = "BLENDER_WORKBENCH"
scene.render.resolution_x = 512
scene.render.resolution_y = 512
scene.render.filepath = preview_path
scene.display.shading.light = "STUDIO"
scene.display.shading.color_type = "MATERIAL"

bpy.ops.render.render(write_still=True)

print("RESULT_OK")
