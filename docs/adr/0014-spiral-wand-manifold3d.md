# 0014: The spiral wand feature builds geometry in-process with manifold3d, not Blender/OpenSCAD

Date: 2026-10-11

## Context

The spiral wand feature makes a long twisted rod (a lobed cross-section
extruded with a constant twist) and cuts a matching twisted bore through
arbitrary user figurines. The fit between the two only works if both are
built from the same profile and twist phase, to well under a millimetre.
The figurines are whatever users download or generate: often several
overlapping closed shells, sometimes hundreds of thousands of triangles.

Every other mesh feature here shells out to Blender, OpenSCAD or FreeCAD.
OpenSCAD's `linear_extrude(twist=...)` makes the rod easily, but its CGAL
booleans on an imported high-poly figurine take minutes. Blender's boolean
modifier is fast but tends to fail or leave non-manifold results on
overlapping shells. Both also put a subprocess and a temp-file round trip
on each request.

## Decision

`spiral_wand.py` builds everything in-process with **manifold3d**
(Apache-2.0), whose booleans are guaranteed to produce manifold output and
run in about a second on typical figurines. It also has a native twisted
extrusion, so rod and bore share one function (`_twisted()`) with the
twist phase taken from absolute height. **trimesh** (MIT) does mesh I/O
and light repair; **networkx** (BSD) is the trimesh dependency for its
hole filling. Overlapping shells are split with manifold3d's
`decompose()` and unioned, which avoids pulling in scipy.

Meshes that remain open after light repair are rejected with a pointer to
the existing watertight checker, rather than adding a second repair path
(for example a voxel rebuild via scikit-image) to this feature.

## Consequences

- No new system dependencies and no Docker image changes beyond three
  pip packages. The feature works when Blender/OpenSCAD/FreeCAD are
  unavailable.
- Tests run the real geometry (no mocking) in a few seconds and check
  physical properties: the rider shows zero overlap with the rod at
  several heights and collides when turned out of phase.
- A second mesh-boolean engine now exists in the codebase. If another
  feature needs fast, robust booleans on user meshes, it should reuse
  manifold3d rather than adding a third.
