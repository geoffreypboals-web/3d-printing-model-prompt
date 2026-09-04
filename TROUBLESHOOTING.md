# Troubleshooting

A living log of non-obvious issues hit while building/running this
project, so they don't get re-debugged from scratch. See `README.md` for
setup and usage.

## Blender / mesh analysis & repair

### `ModuleNotFoundError: No module named 'numpy'` when analyzing/repairing a mesh

**Symptom:** `analyze_mesh()`/`repair_mesh()` raises a `MeshRepairError`
whose message is a Python traceback from
`io_scene_gltf2/blender/exp/gltf2_blender_gather_tree.py` ending in
`ModuleNotFoundError: No module named 'numpy'`.

**Cause:** the Ubuntu/Debian `apt` package for Blender links against the
*system* Python (you'll see `/usr/lib/python3.12` in `sys.path` from
inside it) rather than bundling its own Python + numpy the way the
official blender.org downloads do. Its built-in glTF export addon
(used for the viewer preview, see `--viewer-output` in
`blender_scripts/analyze_watertight.py`) imports numpy at export time.

**Fix:** install numpy for that same system Python:

```bash
sudo apt-get install python3-numpy
```

(Already included in the `Dockerfile`.) If you installed Blender a
different way (a blender.org tarball, snap, flatpak), it likely bundles
its own Python and this won't apply — check `bpy.app.version` and
`sys.path` from inside `blender --background --python-console` if a
similar error shows up with a different traceback.

### `Error: Cannot apply to a multi user: Object "X", Mesh "X", aborting`

**Symptom:** `analyze_watertight.py`/`close_holes.py` fail immediately
after import, before any real analysis.

**Cause:** a freshly-imported mesh object's data-block can come back with
`users > 1` (still referenced by Blender's own undo/orphan bookkeeping)
even though only one object in the scene actually uses it —
`bpy.ops.object.transform_apply()` refuses to run on multi-user mesh
data.

**Fix:** already handled in `_shared.join_into_single_object()` — it
force-copies the mesh data (`obj.data = obj.data.copy()`) before applying
the transform whenever `obj.data.users > 1`. If you see this error
somewhere else, apply the same pattern before any `transform_apply`.

### A hole id from an old report doesn't match after re-analyzing

**Cause:** hole ids are **positional**, not stable content hashes — they
come from the order `_shared.group_boundary_edges()` encounters boundary
edges in bmesh's own edge index order for *that specific run*. Re-saving
the file through another tool, or repairing some holes (which renumbers
whatever's left), changes that order.

**Fix:** always call `analyze_mesh()` again immediately before
`repair_mesh()` on the *exact* file you're about to repair, and use the
ids from that fresh report. `api.py` does this automatically after every
`/repair` call.

### A real intentional opening (cup mouth, open box top) gets flagged as a defect, or vice versa

The classifier in `src/mesh_repair/classifier.py` is a heuristic, not a
certainty — it weighs size (relative to total surface area), position
(near a bounding-box cap vs. buried in the middle), planarity, and
boundary "raggedness" (perimeter²/area vs. a circle). It was tuned and
unit-tested (`tests/test_classifier.py`) against clear-cut synthetic
cases; a genuinely ambiguous or unusual model shape can still land on the
wrong side of a threshold. That's exactly why the viewer shows every hole
with its reason and confidence rather than auto-deciding — treat the
classification as a strong hint, not a verdict, and use the viewer to
confirm before closing anything on a model you care about.

One specific trap found during development: on a **low-poly** model, a
couple of missing/stray triangles can still cover a surprisingly large
*fraction* of the total surface area (there just isn't much total area to
begin with), which can otherwise push the "size" signal toward
"intentional". The classifier dampens this with the boundary-raggedness
signal (real designed openings are almost always clean, low-perimeter
shapes; a couple of stray triangles usually aren't) — see the comment
above `classify_hole()`'s `intentional_score` calculation if this needs
re-tuning for a particular class of model.

## Viewer

### Markers appear in the wrong place, or nowhere near the model

The analysis JSON reports coordinates in the mesh's own space (whatever
Blender read from the file), but the viewer's GLB preview is exported
with Blender's default glTF "+Y up" axis conversion — empirically
confirmed as `(x, y, z) → (x, z, -y)`. `viewer/index.html`'s
`pointToGltf()`/`bboxToGltf()` apply this before placing anything in the
three.js scene. If a future change adds a new place that consumes
`hole.centroid`, `island.centroid`, or `bounding_box` from the API, run
it through those same helpers first — this was caught during development
precisely because a hole's marker rendered in a location that didn't
correspond to any point on the visible mesh at all.

### A flagged hole never shows up in the default camera view

Two separate things had to be fixed for this:

1. The initial camera framing points toward the average direction of the
   flagged holes (`frameCamera()` in `viewer/index.html`), not a fixed
   generic corner — otherwise a hole can easily sit on the far side of a
   mostly-closed shape and never be in frame until the user thinks to
   orbit around it manually.
2. That averaged direction is singular (undefined camera orientation)
   when it's nearly parallel to the world "up" vector — e.g. a hole
   dead-center on a container's lid, which is an entirely ordinary case.
   `frameCamera()` blends in a small horizontal nudge whenever the view
   direction gets within ~10° of straight up/down to avoid this.

If markers still seem to vanish, first orbit the model (left-drag) before
assuming it's a placement bug — a marker can still legitimately end up
occluded by the mesh body itself from a particular angle after both
fixes, same as any other opaque 3D object.

### External CDN scripts (e.g. `cdn.jsdelivr.net`) don't load for the viewer

By design, `viewer/index.html` doesn't use a CDN — three.js is vendored
locally under `viewer/vendor/three/` (see `viewer/vendor/README.md`) so
the viewer keeps working in an offline/air-gapped Docker deployment. If
you see a CDN URL failing in the browser console, check whether a local
change accidentally reintroduced one.

## Docker

### Docker build wasn't verified end-to-end during development

The sandbox this project was developed in didn't have a working Docker
daemon (`dockerd` failed to start — no permission to adjust `ulimit`s in
that container). Every individual command in the `Dockerfile` was
verified directly on the same Debian-family base image (`apt-get install
blender python3-numpy`, `pip install -r requirements.txt`, the app
importing and serving correctly), but the image itself was never built
and run as a whole. Build and smoke-test (`docker run` + hit `/health`)
before relying on it.
