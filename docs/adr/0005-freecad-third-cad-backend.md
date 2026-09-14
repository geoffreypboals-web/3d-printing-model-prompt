# 0005: FreeCAD as a third CAD backend - solid-native operations, Blender kept as the default/fallback

Date: 2026-09-14

## Context

OpenSCAD and Blender cover this service's two existing needs well:
OpenSCAD for parametric/mechanical generation, Blender for organic/mesh
generation and mesh-level operations (Solidify shelling, hole filling).
Neither is a solid-modeling kernel, though - Blender's mesh model has no
concept of a B-rep face/solid, so it can't natively read or write STEP
(the standard exchange format for real CAD solids), and its Solidify
modifier can shell a mesh but with no notion of "this is now watertight
CAD geometry," just a thicker mesh. FreeCAD wraps OpenCASCADE (OCCT), a
real B-rep kernel, giving three capabilities Blender genuinely can't
provide: STEP import/export, a Part Thickness operation that reasons
about actual solid topology, and OCCT's ShapeFix healing for malformed
B-rep shapes. This ADR covers adding it as a third backend for exactly
those three capabilities.

## Decisions

**Blender stays the default/fallback, not replaced.** FreeCAD is only
tried where it has a real advantage: wall-thickness shelling for `.stl`
input (`thickness.mesh_shell()` tries FreeCAD first, falls back to
Blender automatically on any `FreeCADCADError` - missing binary,
non-manifold input, timeout), plus the two genuinely new capabilities
(STEP conversion, solid healing) that have no Blender equivalent at all.
Every other mesh format, and any format Blender-Solidify already
handles well, keeps going straight to Blender. This directly follows
this project's own framing that Blender should stay the path for
organic/mesh-only models - FreeCAD's B-rep operations only make sense on
input that's solid-like to begin with.

**Installed via Debian's `freecad-python3` apt package** (confirmed
`1.0.0+dfsg-8+deb13u3` on the Dockerfile's `python:3.11-slim` base,
Debian trixie), not a from-source build - matches the existing
OpenSCAD/Blender install pattern (apt, not compiled). This installs a
headless CLI at `/usr/lib/freecad/bin/freecadcmd-python3`, symlinked to
`/usr/bin/freecadcmd`, without the much larger `freecad` GUI package
(still substantial - Qt/PySide2/OpenCASCADE/VTK - but the same known
tradeoff as Blender's own image-size cost).

**Same wrapper shape as `watertight.py`**: `freecad_cad.py` invokes
FreeCADCmd headless as a subprocess running a script under
`freecad_scripts/`, which writes a JSON report file read back by a
`_read_json_report()`-equivalent. Success is judged purely by whether
that JSON exists and reports `"ok": true` - **not** by FreeCAD's own
exit code, which is always 0 regardless of what the script actually did
(confirmed live). FreeCADCmd also prints an unrelated, harmless Python
traceback to stderr about failing to auto-open one of the script's own
CLI argument values as a document; this is cosmetic noise, not a
failure signal.

**Real semantic limitations found during development** (verified
hands-on against a real STL cube run through actual Docker containers,
not assumed from documentation):

- **Part Thickness requires an opening face** - unlike Blender's
  Solidify, which can shell a fully-sealed mesh with zero faces removed,
  `Part.Shape.makeThickness()` raises `Null input shape` on an empty
  face list. It always needs at least one face nominated as the
  "opening" being removed. `freecad_scripts/thicken.py` picks the
  solid's largest planar face automatically - a deliberate default
  heuristic, not equivalent to Solidify's semantics, and worth knowing
  if a caller expects a literally sealed shell.
- **A raw mesh-derived solid has one B-rep face per source triangle** -
  picking "the largest face" naively on that raw solid picks a tiny
  triangle, not a useful full side of the part. `removeSplitter()` must
  run first to merge coplanar triangles into real faces (12 -> 6 faces
  on a test cube, volume preserved).
- **`Part.export([shape], path)` silently omits all solid geometry**
  when given a bare `Part.Shape`/`Part.Solid` (as opposed to a document
  object) - it writes a STEP file with a valid header but no
  `MANIFOLD_SOLID_BREP` entity at all (confirmed: a 20-entity file with
  zero faces/edges/vertices, importable nowhere). The fix is
  `shape.exportStep(path)` (the shape's own method), which writes the
  full B-rep correctly (170 entities on the same test cube, including 6
  `ADVANCED_FACE`s). This was the root cause of what first looked like a
  STEP *import* bug - every import attempt was failing on files that
  never had geometry in them to begin with.
- **STEP import only works via `Part.read(path)`** - `Part.insert(path,
  doc.Name)`, `Import.insert(path, doc.Name)`, and `Import.open(path)`
  all silently create zero document objects in this FreeCAD 1.0.0
  Debian build (confirmed across three separate attempts, each producing
  an empty `doc.Objects` list with no error raised), and
  `Import.StepShape(path).read()` raises `NotImplementedError: Not yet
  implemented` in this build's Python bindings. `Part.read()` is the one
  STEP-import path that actually works headless and was the one used in
  `freecad_scripts/step_import.py`.
- **STEP export/import is a best-effort B-rep wrap, not reverse
  engineering** - a flat mesh face becomes one real STEP planar face,
  but there's no curve/fillet/feature recovery from triangulated
  geometry. This is documented in the relevant endpoints' docstrings so
  callers don't expect a parametric STEP file back.

**Solid healing (ShapeFix) is a distinct feature from `watertight.py`'s
hole filling**, not a replacement or an extension of it -
`freecad_scripts/heal.py` runs `Part.Shape.fix()` on the *raw* pre-solid
shape (deliberately not solidified first via the shared `mesh_to_solid()`
helper, since `Part.makeSolid()` can itself raise on the kind of
brokenness this feature exists to fix) to repair malformed B-rep topology
- small gaps, invalid edges/faces. `watertight.py` closes genuine open
boundary loops in a mesh (a cup's mouth left unfilled, a missing patch).
These are different classes of defect on different representations
(mesh vs. B-rep); a caller with a mesh that isn't watertight should still
reach for `/repair`, not `/repair-solid`.

## Consequences

- `GET /health`'s `freecad_available` field does not gate `status`
  ok/degraded - Blender remains the required fallback for thickening, so
  FreeCAD's absence only disables `/step/upload`,
  `/models/{id}/export-step`, and `/models/{id}/repair-solid` (each
  returns its own 503 if called without it), not the whole service.
- The FreeCAD-first thickening path only applies to `.stl` input with
  `quad_target_faces=0` - QuadriFlow retopology is Blender-specific, so
  requesting it routes straight to Blender rather than silently dropping
  the request.
- Image size grows again (Blender already added ~1.5GB+; FreeCAD's
  Qt/OpenCASCADE/VTK dependencies add a comparable amount) - accepted as
  the same known tradeoff already made for Blender, not re-litigated
  here.
