# 0011: Geometry-aware vent placement (FR-7)

Date: 2026-09-13

## Context

`docs/mold-production-research-and-plan.md` (Phase 7, FR-7) asked to
replace `_add_pour_holes()`'s fixed `0.35 * cavity_size` vent offset with
detection of real trapped-air pockets - "the locally highest points of
the model's own surface relative to the pour direction," the way
Moldboxer's "Automatic Air-Trap Support Pillars" feature does - rather
than always cutting the vent at the same relative spot regardless of the
model's actual shape. Explicitly the lowest-priority item in the plan
(a refinement to an already-acceptable default, not a capability gap).

## Decisions

**A "trapped-air pocket" is defined as a strict local Z-maximum of the
cavity's own roof surface - not a bare "highest vertex among its mesh
neighbors."** As material rises from below during a pour, air collects
under any point where the surface curves back down on every side above
it (a dome, an ear, a raised boss) - topologically, a vertex that's
higher than every neighbor it's connected to. The first version of this
(comparing a vertex to *every* mesh-edge neighbor, full stop) was wrong:
a box's top-face corners each have a *vertical* edge down to the bottom
face, and that neighbor is always lower - so every corner of a perfectly
flat-topped box looked like a "local max" even though the top is flat
and traps nothing. Caught immediately via
`.scratch_moldtest/debug_vent_placement.py`'s first run (a flat box
returned a mesh corner instead of the expected fixed-offset fallback).
Fixed by excluding any neighbor reached by a purely vertical edge (same
X and Y) from the comparison - only neighbors that actually move across
the roof surface count, which correctly leaves a flat top with zero
local maxima (`_find_vent_xy()` in `make_mold.py`).

**Falls back to the old fixed `0.35 * cavity_size` offset in two cases,
rather than requiring a real peak or failing:** when the cavity has no
real mesh to analyze at all (`silicone_block`'s cavity is a synthetic
box - `cavity_obj=None`), and when the mesh genuinely has no surviving
local maximum (a plain box's flat top, or a model whose only peaks sit
too close to the sprue - see below). This keeps every already-shipped,
already-verified shape (the unit-cube test fixture, any box-like model)
producing the exact same mold as before this phase - FR-7 is additive
for models with real surface features, not a behavior change for boxy
ones. Verified live in `debug_vent_placement.py`: a flat box always
falls back to `(0.35*20, 0.35*20) = (7, 7)`.

**Points within `exclude_radius_mm` (passed as `sprue_diameter_mm`) of
the cavity center are skipped**, even if they're a genuine local
maximum, since the sprue already vents that immediate area - venting
right next to (or overlapping) the sprue's own hole would be redundant
and risks the two cylindrical cuts intersecting awkwardly. Using the
sprue's own diameter as the exclusion radius avoids introducing a new,
separate magic-number parameter for what's fundamentally "stay outside
the sprue's own footprint." Verified live: a bump placed within that
radius of center correctly falls back to the fixed offset instead of
being selected.

**Picks the single highest surviving peak, not every peak found.** A
model could have several separate raised features (two ears, three
bumps), each trapping its own air pocket that ideally wants its own
vent - full parity with Moldboxer's approach would cut one hole per
detected pocket. That's real added complexity (multiple cylinder cuts,
a cap on how many holes are reasonable, more surface area for adjacent
holes to interact) for a phase explicitly scored as the lowest-priority
refinement in the plan. Scoped down to one vent - the single highest
peak - keeping `_add_pour_holes()`'s existing "exactly one sprue, one
vent" contract unchanged. A future revisit could extend `_find_vent_xy`
to return a list and have `_add_pour_holes` cut one hole per point if a
real multi-pocket model turns out to need it in practice.

**Vent location is resolved once, before the cavity-source object is
deleted, and passed into `_add_pour_holes()` as a plain `(x, y)` tuple**
rather than passing the object itself into `_add_pour_holes`. Three of
the four modes delete their cavity-source object (`model_obj` /
`offset_obj`) shortly after building the halves, and `form_fitting`
specifically deletes `offset_obj` *before* its two `_add_pour_holes`
calls (both top halves share one cavity, hence one vent location) - so
computing the location up front and threading a tuple through sidesteps
any object-lifetime reordering across the four builders. `direct_cast`/
`hollow_cast` share `_build_direct_cast_halves()` and had `model_obj`
already alive at the right point, needing no reordering.
`silicone_block` has no real cavity object at all (`cavity_obj=None`),
so its call just resolves straight to the fallback.

## Testing note

Live-verified in two throwaway scripts before trusting the technique,
per this project's usual discipline for new geometry code:
`.scratch_moldtest/debug_vent_placement.py` unit-tests `_find_vent_xy`
directly (imported straight from `make_mold.py` via `importlib`, no
subprocess) against three constructed meshes - a flat box (must fall
back), a box with an off-center bump (must find it), and a box with a
bump placed inside the sprue's exclude radius (must still fall back).
`.scratch_moldtest/debug_vent_placement_e2e.py` runs the *actual*
`make_mold.py` CLI end-to-end against a bumpy fixture and ray-casts into
the exported top half, confirming the sprue and the new vent both
produce real through-holes while the old fixed-offset location is now
solid (proving the change took effect through the whole pipeline, not
just in isolation).

`tests/conftest.py`'s new `bumpy_box_stl` fixture (the same lifted-corner
box used in the e2e debug script) backs a permanent real-Blender
integration test,
`test_make_mold_direct_cast_vents_the_real_trapped_air_pocket` in
`tests/test_mold_integration.py`, using a small `_hits_solid()` helper
(a one-off Blender subprocess doing a single ray-cast) since a hole's
XY position - unlike overall part size - can't be read off a bounding
box. No mocked-level test was added: this phase changes no request/
response field and no CLI argument, only an internal geometry heuristic,
so there's nothing for a mocked-subprocess test to exercise that the
real-Blender test doesn't already cover.
