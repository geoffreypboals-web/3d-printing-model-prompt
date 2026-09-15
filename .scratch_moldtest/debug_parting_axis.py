"""
Throwaway verification (not part of the test suite) for Phase 6's
configurable parting axis: confirms that rotating a mesh object so the
requested axis becomes local Z (then baking the rotation into the mesh
via transform_apply) is a clean, reversible way to reuse all of
make_mold.py's existing Z-hardcoded geometry code for an X or Y parting
axis, without rewriting build_half()/build_offset_cavity_half()/etc.
Run: blender --background --python debug_parting_axis.py
"""
import math

import bpy

bpy.ops.wm.read_factory_settings(use_empty=True)
# A non-cubic box so X/Y/Z are distinguishable after rotation.
bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, 0.0))
obj = bpy.context.active_object
obj.scale = (1.0, 2.0, 3.0)  # X extent 1, Y extent 2, Z extent 3 (edge length, size=1 spans -0.5..0.5)
bpy.context.view_layer.objects.active = obj
obj.select_set(True)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)


def bbox_of(o):
    xs = [v.co.x for v in o.data.vertices]
    ys = [v.co.y for v in o.data.vertices]
    zs = [v.co.z for v in o.data.vertices]
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


print("BEFORE bbox (x,y,z extents should be 1,2,3):", bbox_of(obj))


def rotate_axis_to_z(o, axis):
    if axis == "z":
        return
    if axis == "x":
        o.rotation_euler = (0.0, math.radians(-90), 0.0)  # X -> Z
    elif axis == "y":
        o.rotation_euler = (math.radians(90), 0.0, 0.0)  # Y -> Z
    bpy.context.view_layer.objects.active = o
    o.select_set(True)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)


rotate_axis_to_z(obj, "x")
mn, mx = bbox_of(obj)
print("AFTER rotate x->z bbox (expect x-extent 3, y-extent 2, z-extent 1):", mn, mx)
