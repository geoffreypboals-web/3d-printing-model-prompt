"""
Verifies _find_vent_xy() (FR-7) picks a real off-center trapped-air
pocket instead of the fixed 0.35*cavity_size offset, and that it still
falls back to that fixed offset for a flat-topped box (no genuine peak).

Run: blender --background --python debug_vent_placement.py
"""
import importlib.util
import sys

import bpy
import bmesh

MAKE_MOLD_PATH = r"D:\3d-printing-model-prompt\src\threedprompt\blender_scripts\make_mold.py"
spec = importlib.util.spec_from_file_location("make_mold", MAKE_MOLD_PATH)
make_mold = importlib.util.module_from_spec(spec)
sys.modules["make_mold"] = make_mold
spec.loader.exec_module(make_mold)


def _fresh():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def build_flat_box(size_xy=20.0, height=6.0):
    _fresh()
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, 0, height / 2))
    obj = bpy.context.active_object
    obj.scale = (size_xy, size_xy, height)
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.transform_apply(location=True, rotation=False, scale=True)
    return obj


def build_box_with_bump(size_xy=20.0, height=6.0, bump_xy=(5.0, -5.0), bump_height=3.0):
    obj = build_flat_box(size_xy, height)
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    # find the 4 top-face vertices nearest bump_xy and lift the single
    # nearest one to create an unambiguous off-center apex.
    top_verts = [v for v in bm.verts if abs(v.co.z - height) < 1e-6]
    nearest = min(top_verts, key=lambda v: (v.co.x - bump_xy[0]) ** 2 + (v.co.y - bump_xy[1]) ** 2)
    nearest.co.x, nearest.co.y = bump_xy
    nearest.co.z = height + bump_height
    bm.to_mesh(obj.data)
    obj.data.update()
    bm.free()
    print("post-bump vertex coords:", [tuple(v.co) for v in obj.data.vertices])
    return obj


print("=== flat box (no real peak) -> must fall back to fixed 0.35 offset ===")
obj = build_flat_box()
cavity_center = (0.0, 0.0, 3.0)
cavity_size = (20.0, 20.0, 6.0)
vent_xy = make_mold._find_vent_xy(bpy, obj, cavity_center, cavity_size, exclude_radius_mm=10.0)
expected_fallback = (0.35 * 20.0, 0.35 * 20.0)
print("vent_xy:", vent_xy, "expected fallback:", expected_fallback)
assert abs(vent_xy[0] - expected_fallback[0]) < 1e-6
assert abs(vent_xy[1] - expected_fallback[1]) < 1e-6
print("PASS: flat box falls back correctly")

print("=== box with an off-center bump -> must pick the bump's apex ===")
obj2 = build_box_with_bump(bump_xy=(8.0, -8.0), bump_height=3.0)
vent_xy2 = make_mold._find_vent_xy(bpy, obj2, cavity_center, cavity_size, exclude_radius_mm=10.0)
print("vent_xy2:", vent_xy2, "expected bump apex: (8.0, -8.0)")
assert abs(vent_xy2[0] - 8.0) < 1e-6
assert abs(vent_xy2[1] - (-8.0)) < 1e-6
print("PASS: bump apex detected correctly, not the fixed-offset fallback")

print("=== bump too close to the sprue (within exclude radius) -> must fall back ===")
obj3 = build_box_with_bump(bump_xy=(2.0, 2.0), bump_height=3.0)
vent_xy3 = make_mold._find_vent_xy(bpy, obj3, cavity_center, cavity_size, exclude_radius_mm=10.0)
print("vent_xy3:", vent_xy3, "expected fallback:", expected_fallback)
assert abs(vent_xy3[0] - expected_fallback[0]) < 1e-6
assert abs(vent_xy3[1] - expected_fallback[1]) < 1e-6
print("PASS: bump within exclude radius correctly ignored, falls back")

print("ALL CHECKS PASSED")
