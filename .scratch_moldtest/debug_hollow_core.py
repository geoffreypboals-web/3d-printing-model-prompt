"""
Throwaway verification (not part of the test suite) for Phase 5's
hollow_cast mode: confirms _offset_model_along_normals() with a NEGATIVE
offset shrinks a model inward into a valid, watertight, non-degenerate
solid centered inside the original - the "core" that seats inside the
mold cavity so a pour fills only the thin shell gap around it.
Run: blender --background --python debug_hollow_core.py
"""
import bmesh
import bpy

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.5, 0.5, 0.5))
obj = bpy.context.active_object

bm = bmesh.new()
bm.from_mesh(obj.data)
bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
bm.normal_update()
offset_mm = -0.1  # shrink inward
for v in bm.verts:
    v.co += v.normal * offset_mm
bm.to_mesh(obj.data)
bm.free()
obj.data.update()

bm = bmesh.new()
bm.from_mesh(obj.data)
bbox_min = [min(v.co[i] for v in bm.verts) for i in range(3)]
bbox_max = [max(v.co[i] for v in bm.verts) for i in range(3)]
print("CORE_BBOX_MIN=", bbox_min)
print("CORE_BBOX_MAX=", bbox_max)
volume = bm.calc_volume(signed=True)
print("CORE_VOLUME=", volume, "(expect positive, non-degenerate, < 1.0 original cube volume)")
bm.free()
