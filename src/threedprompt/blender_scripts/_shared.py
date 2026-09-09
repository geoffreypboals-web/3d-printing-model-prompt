"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/blender_scripts/_shared.py
Description: Mesh import/join and boundary-loop-grouping helpers shared by
    analyze_watertight.py and close_holes.py. Only importable from inside
    Blender (uses bpy/bmesh), and only meaningful when both scripts use
    the *identical* import -> join -> transform_apply -> boundary-edge-
    grouping sequence: that determinism is what lets a hole id the user
    saw in an analysis report still mean the same boundary loop when
    close_holes.py re-opens the same source file later.
Inputs: N/A (a library of functions; see each function's docstring).
Outputs: N/A.
Troubleshooting:
    - If a hole id closed by close_holes.py doesn't match the region the
      user pointed at in the viewer, check that nothing between the two
      Blender invocations changed this module (or the input file) — the
      id is positional (index into _group_boundary_edges' output), not a
      stable content hash, so any change to import/join order, face
      winding, or the boundary-edge scan order will silently renumber
      holes.
"""

from __future__ import annotations

import os
from collections import defaultdict


def import_mesh(bpy, path: str) -> None:
    """Import a mesh file (.stl/.obj/.ply/.glb/.gltf/.fbx/.3dm) into the current scene."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".stl":
        bpy.ops.wm.stl_import(filepath=path)
    elif ext == ".obj":
        bpy.ops.wm.obj_import(filepath=path)
    elif ext == ".ply":
        bpy.ops.wm.ply_import(filepath=path)
    elif ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=path)
    elif ext == ".3dm":
        import sys

        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
        import rhino3dm_reader.read3dm as read3dm

        result = read3dm.read_3dm(
            bpy.context,
            path,
            {"import_brep": True, "import_extrusions": True, "import_meshes": True, "import_subd": True},
        )
        if "FINISHED" not in result:
            raise ValueError(f"Failed to import .3dm file: {path}")

        # read_3dm() scales the file's own units into Blender's scene units
        # (meters, by this script's factory-default scene) -- everything
        # else in this pipeline treats 1 Blender unit as 1mm, so a real
        # ~40mm part lands as a ~0.04-unit mesh here. Left uncorrected,
        # mm-scaled operations applied afterward (Solidify thickness,
        # remove_doubles thresholds, etc.) are relatively enormous against
        # geometry 1000x smaller than expected -- confirmed live this
        # collapses a real bracket .3dm to 0 triangles. 1 meter = 1000mm.
        bpy.ops.object.select_all(action="SELECT")
        if bpy.context.selected_objects:
            for imported_obj in bpy.context.selected_objects:
                imported_obj.scale = tuple(component * 1000 for component in imported_obj.scale)
            bpy.context.view_layer.objects.active = bpy.context.selected_objects[0]
            bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    else:
        raise ValueError(f"Unsupported input format: {ext}")


def export_mesh(bpy, path: str) -> None:
    """Export the active object to path (.stl/.obj/.ply/.glb/.gltf), format from extension."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".stl":
        bpy.ops.wm.stl_export(filepath=path, export_selected_objects=False)
    elif ext == ".obj":
        bpy.ops.wm.obj_export(filepath=path, export_selected_objects=False)
    elif ext == ".ply":
        bpy.ops.wm.ply_export(filepath=path, export_selected_objects=False)
    elif ext in (".glb", ".gltf"):
        bpy.ops.export_scene.gltf(filepath=path)
    else:
        raise ValueError(f"Unsupported output format: {ext}")


def quad_remesh(bpy, obj, target_faces: int) -> None:
    """
    Retopologize obj into a clean, mostly-quad grid via QuadriFlow, targeting
    roughly target_faces faces. Intended as a topology/cosmetic-only step,
    but QuadriFlow does NOT reliably preserve watertightness on its own --
    confirmed live: closing a cube's one missing triangle then quad-
    remeshing it to a low target_faces reintroduced a boundary gap the
    hole-fill had just closed. A light fill_holes + normals pass afterward
    is what actually keeps the "doesn't affect watertightness" claim true;
    without it, this function could silently undo a caller's own repair.
    Every downstream STL export re-triangulates regardless of the quad
    topology. Run this *after* repair/thicken steps, since QuadriFlow wants
    clean, closed input to begin with.
    """
    import bmesh

    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.quadriflow_remesh(
        target_faces=target_faces,
        use_preserve_sharp=True,
        use_preserve_boundary=True,
        use_mesh_symmetry=False,
    )

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.holes_fill(bm, edges=[e for e in bm.edges if len(e.link_faces) == 1], sides=0)
    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    bm.to_mesh(obj.data)
    obj.data.update()
    bm.free()


def join_into_single_object(bpy):
    """
    Join every imported mesh object into one, apply its transform (so
    reported/edited coordinates are in the same space the file will be
    exported in), and return that single object.
    """
    mesh_objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not mesh_objs:
        raise RuntimeError("No mesh objects found in the imported file")

    bpy.ops.object.select_all(action="DESELECT")
    for obj in mesh_objs:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = mesh_objs[0]
    if len(mesh_objs) > 1:
        bpy.ops.object.join()

    obj = bpy.context.view_layer.objects.active
    if obj.data.users > 1:
        # Freshly imported mesh data can come back multi-user (still
        # referenced by Blender's undo/orphan bookkeeping) even though
        # only one object uses it in the scene; transform_apply refuses
        # to run on multi-user data, so force a single-user copy first.
        obj.data = obj.data.copy()
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    return obj


def group_boundary_edges(boundary_edges):
    """
    Split boundary edges (edges with exactly one linked face) into
    connected components — each component is one "hole". Two boundary
    edges are connected if they share a vertex. Components are returned
    in the order their first-encountered edge appears in boundary_edges,
    which is itself bmesh's own edge index order — deterministic for a
    given mesh, which is what keeps hole ids stable between an analyze
    run and a later close run on the same file.
    """
    vert_to_edges = defaultdict(list)
    for e in boundary_edges:
        vert_to_edges[e.verts[0].index].append(e)
        vert_to_edges[e.verts[1].index].append(e)

    visited = set()
    components = []
    for start in boundary_edges:
        if start.index in visited:
            continue
        stack = [start]
        component = []
        local_seen = set()
        while stack:
            e = stack.pop()
            if e.index in local_seen:
                continue
            local_seen.add(e.index)
            component.append(e)
            for v in e.verts:
                for e2 in vert_to_edges[v.index]:
                    if e2.index not in local_seen:
                        stack.append(e2)
        visited |= local_seen
        components.append(component)
    return components


def oriented_endpoints(edge):
    """
    Return (v_from, v_to) for a boundary edge using the winding order of
    its single adjacent face, so consecutive boundary edges around a hole
    have a consistent rotational direction.
    """
    loop = edge.link_loops[0]
    return loop.vert, loop.link_loop_next.vert
