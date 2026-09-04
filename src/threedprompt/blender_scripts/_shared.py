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
    """Import a mesh file (.stl/.obj/.ply/.glb/.gltf/.fbx) into the current scene."""
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
    else:
        raise ValueError(f"Unsupported input format: {ext}")


def export_mesh(bpy, path: str) -> None:
    """Export the active object to path (.stl/.obj/.ply/.glb/.gltf), format from extension."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".stl":
        bpy.ops.export_mesh.stl(filepath=path)
    elif ext == ".obj":
        bpy.ops.wm.obj_export(filepath=path, export_selected_objects=False)
    elif ext == ".ply":
        bpy.ops.wm.ply_export(filepath=path, export_selected_objects=False)
    elif ext in (".glb", ".gltf"):
        bpy.ops.export_scene.gltf(filepath=path)
    else:
        raise ValueError(f"Unsupported output format: {ext}")


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
