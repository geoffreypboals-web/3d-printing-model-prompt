# 0009: Hollow/vessel inner-core mold mode - reusing the offset helper inward, no boolean cut against the core

Date: 2026-09-12

## Context

`docs/mold-production-research-and-plan.md` (Phase 5, FR-5) asked for a
mode that lets a hollow vessel (a vase, a cup) be cast as a genuine shell
rather than a solid block: for a model whose input mesh is a solid blob
shaped like the vessel's *outer* surface (no interior modeled), casting it
via `direct_cast` (`docs/adr/0006-direct-cast-mold-mode.md`) would produce
a solid resin/plaster lump shaped like a vase - heavy, wasteful of
material, and not actually hollow. `MoldRequest.mode` now also accepts
`"hollow_cast"`.

## Decisions

**The core is a separate, un-cut solid nested inside the existing
direct_cast-style cavity - no new boolean operation needed.** The core is
built by reusing `_offset_model_along_normals()`
(`docs/adr/0008-form-fitting-thin-shell-mold.md`) with a *negative*
offset (`-cast_wall_thickness_mm`), producing a duplicate of the model
shrunk uniformly inward. Because the offset shrinks the model in every
direction (including vertically), there's naturally a
`cast_wall_thickness_mm` gap between the core's own surface and the
model's original surface on every side - so the caller can simply place
the printed core inside the bottom mold half's cavity before closing the
top half, and poured material fills that gap on its own to form a hollow
shell. No stem, flange, or support geometry was added to hold the core in
place: verified live in `.scratch_moldtest/debug_hollow_core.py` and
`.scratch_moldtest/debug_hollow_cast_full.py` that the shrunk core is a
valid, non-degenerate, positive-volume solid, and that the outer mold's
cavity is genuinely open exactly like `direct_cast`'s (ray-cast
containment check, same methodology as ADR 0005/0006/0008).

**The outer mold is `direct_cast`'s own halves, unmodified.** Rather than
inventing new outer-mold geometry, `_build_direct_cast_halves()` was
extracted from what was `_build_direct_cast_mold()` (now a thin wrapper:
build halves, delete the model, export) so `hollow_cast` can call the same
halves-building logic before deleting the model object, then duplicate
the model into a core first. This is the same "extend by composing
existing builders" pattern `form_fitting` used with
`build_offset_cavity_half()` - no new box/boolean logic, just a new
combination of the pieces `direct_cast` and `form_fitting` already
proved.

**Reuses `direct_mold_wall_mm` for the outer mold, not a `hollow_cast`-
specific wall parameter.** The outer mold *is* a direct_cast mold - giving
it a separate wall-thickness field would just be two names for the same
concept split across two request fields, the same reasoning ADR 0006 gave
for not duplicating `clamp_flange_width_mm`/`bolt_hole_diameter_mm` under
mode-specific names. Only `cast_wall_thickness_mm` (how far the core
shrinks in, i.e. the finished cast's wall thickness) is genuinely new.

**Known limitation: shares `form_fitting`'s offset-technique limitations,
applied inward.** The same per-vertex-normal-offset under-grows sharp
corners/edges (here: under-shrinks them, leaving a slightly thicker-than-
requested wall right at a model's corners) and has no self-intersection
guard for models whose features are narrow relative to
`cast_wall_thickness_mm` (a thin-necked vase with a large
`cast_wall_thickness_mm` could shrink the core's neck section past itself
into an inverted, unusable solid). Accepted as a documented v1 limitation
for the same reasons ADR 0008 gave: this mode targets rounded vessel
shapes, and fixing it requires a genuinely different offset algorithm
that's out of scope here. Also shares `direct_cast`'s existing
limitations unmodified (no draft angle, no release tolerance, no
automatic undercut check for this mode - FR-4's automatic draft warning
in `mold.py` still only runs for `mode="direct_cast"`, not `hollow_cast`,
since that check was scoped to the mode it was built for and extending it
here wasn't asked for).

## Testing note

Same split as ADR 0005/0006/0008: `tests/test_mold.py` mocks the Blender
subprocess for `hollow_cast`'s parameter validation, CLI arg passing
(`--cast-wall-thickness-mm`), and `MoldResult` field population (only the
`hollow_cast_*` fields populated, everything else `None`, `draft_check`
stays `None` since the automatic check doesn't run for this mode).
`tests/test_mold_integration.py` runs the real boolean/offset pipeline
against the unit-cube fixture, checking the outer mold halves are
watertight and correctly sized (identical math to `direct_cast`'s own
integration test) and the core is watertight, smaller than the cavity,
and centered. The ray-casting containment check and the core's own
bbox/volume sanity check were one-time development-time verifications in
`.scratch_moldtest/debug_hollow_core.py` and
`.scratch_moldtest/debug_hollow_cast_full.py`, not re-run on every test
pass - same rationale as the prior ADRs' equivalent notes.
