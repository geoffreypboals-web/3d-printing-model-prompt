"""
Estimates the minimum wall thickness of a mesh via binary search over
Blender's bundled 3D Print Toolbox addon (object_print3d_utils.mesh_helpers.
bmesh_check_thick_object), which flags faces thinner than a given threshold
using backward ray-casting. There is no direct "give me the minimum value"
API, so this narrows a threshold until no faces are flagged.

Usage (invoked headless):
  blender --background --factory-startup --python analyze_thickness.py -- <input.glb>

Unit assumption: 1 GLB unit is treated as 1mm (see solidify.py for the same
caveat -- text-to-3D generators do not guarantee real-world scale).

Prints: MIN_THICKNESS_MM=<value>
"""

import sys

import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
if len(argv) != 1:
    raise SystemExit("usage: analyze_thickness.py -- <input.glb>")

input_path = argv[0]

# The addon lives under one of Blender's script/addons search paths --
# location varies by install method (apt package vs. official tarball), so
# search rather than hardcode.
mesh_helpers = None
for base in bpy.utils.script_paths(subdir="addons"):
    if base not in sys.path:
        sys.path.insert(0, base)
try:
    from object_print3d_utils import mesh_helpers  # noqa: E402
except ImportError as error:
    raise SystemExit(f"could not import object_print3d_utils addon: {error}")

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=input_path)

mesh_objects = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
if not mesh_objects:
    raise SystemExit("no mesh objects found in imported file")

# Search range: 0.01mm floor, up to half the smallest bounding-box dimension
# across all mesh objects (capped at 20mm -- a sane upper bound for "thin
# wall" checks on printable-scale objects).
smallest_dim = min(
    min(obj.dimensions.x, obj.dimensions.y, obj.dimensions.z) for obj in mesh_objects
)
low = 0.01
high = min(smallest_dim / 2, 20.0)
if high <= low:
    high = low * 2

for obj in mesh_objects:
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

ITERATIONS = 12
for _ in range(ITERATIONS):
    mid = (low + high) / 2
    any_thin = False
    for obj in mesh_objects:
        thin_faces = mesh_helpers.bmesh_check_thick_object(obj, mid)
        if len(thin_faces) > 0:
            any_thin = True
            break
    # any_thin at `mid` means the true minimum thickness is < mid (something
    # thinner exists), so narrow the search downward; otherwise nothing is
    # thinner than `mid`, so the true minimum is >= mid -- narrow upward.
    if any_thin:
        high = mid
    else:
        low = mid

print(f"MIN_THICKNESS_MM={high:.4f}")
print("RESULT_OK")
