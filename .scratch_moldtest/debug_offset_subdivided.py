"""
Throwaway check (not part of the test suite): does _offset_model_along_normals
grow a flat face's INTERIOR vertices by the correct distance, with distortion
confined to edges/corners? Build a subdivided cube (interior face vertices
present), run the same normal-push used by make_mold.py's
_offset_model_along_normals, and compare an interior-face-vertex displacement
against a corner-vertex displacement.
"""
import bpy
import bmesh

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.5, 0.5, 0.5))
obj = bpy.context.active_object

bm = bmesh.new()
bm.from_mesh(obj.data)
bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=6, use_grid_fill=True)
bm.to_mesh(obj.data)
bm.free()
obj.data.update()

offset_mm = 0.3

bm = bmesh.new()
bm.from_mesh(obj.data)
bm.verts.ensure_lookup_table()
bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
bm.normal_update()

before = {v.index: v.co.copy() for v in bm.verts}
for v in bm.verts:
    v.co += v.normal * offset_mm

# Interior-face vertex: on a flat face, not on any cube edge/corner (normal
# should be exactly perpendicular -> full offset_mm displacement).
interior = None
for v in bm.verts:
    b = before[v.index]
    on_edge = sum(1 for c in b if abs(abs(c) - 0.5) < 1e-6)
    if on_edge == 1:  # exactly one coordinate at the face plane extreme -> face interior
        interior = v
        break

# Corner vertex: all three coordinates at extremes.
corner = None
for v in bm.verts:
    b = before[v.index]
    if sum(1 for c in b if abs(abs(c) - 0.5) < 1e-6) == 3:
        corner = v
        break

for label, v in (("interior_face_vertex", interior), ("corner_vertex", corner)):
    b = before[v.index]
    disp = (v.co - b).length
    print(f"{label}: before={tuple(b)} after={tuple(v.co)} displacement={disp:.4f} (expected {offset_mm})")

bm.free()
