# 0008: Form-fitting thin-shell mold mode - offset-along-normals, shared cavity shape, and the corner-rounding limitation

Date: 2026-09-12

## Context

`docs/mold-production-research-and-plan.md` (Phase 4, FR-3) asked for a
third mold mode: a thin, contour-following silicone "skin" that hugs a
detailed/organic model's surface, for models a rigid `direct_cast` mold
(`docs/adr/0006-direct-cast-mold-mode.md`) can't release along any single
pull axis. `MoldRequest.mode` now accepts `"form_fitting"` alongside
`"silicone_block"` and `"direct_cast"`.

The core geometric problem this mode adds that neither existing mode has:
the cavity shape isn't a box (`silicone_block`) or the model's own mesh
(`direct_cast`) - it's the model's surface **grown outward** by
`shell_thickness_mm`, since that grown surface *is* the finished silicone
shell's own outer face. Two tools need that exact same cavity shape: a
"skin-pour tool" that casts the thin shell around the model (with
registration keys, mirroring `silicone_block`'s pour box), and a "support
jacket" that bolts around the finished flexible shell afterward to hold it
rigid for the final plaster/cement pour (mirroring `silicone_block`'s
clamp shell). They differ only in wall thickness and which accessories
they get - the same `pour_box`/`clamp_shell` relationship ADR 0005
established, just built around a non-box cavity.

## Decisions

**Cavity source is a duplicated, per-vertex-normal-offset copy of the
model, reusing the same open-tray boolean subtraction `direct_cast`
already proved.** `build_direct_cast_half()` was generalized and renamed
`build_offset_cavity_half()` (same body: outer box minus a duplicated
"cavity source" object) so both modes share it - `direct_cast` passes the
raw model as the cavity source, `form_fitting` passes an offset-grown
duplicate. `_offset_model_along_normals()` duplicates the model, then
pushes every vertex along its own vertex normal by `shell_thickness_mm`
(`bmesh.ops.recalc_face_normals` + `bm.normal_update()` first, so normals
are correct even on a mesh with reversed winding). Confirmed live against
a real Blender install in `.scratch_moldtest/debug_form_fitting.py`: the
cavity point (inside the model) reads as not-solid, a point in the tool's
own wall material reads as solid, and all 4 exported parts are watertight
- the boolean mechanism itself works exactly like `direct_cast`'s.

**Solidify modifier was rejected in favor of the direct offset.** The
alternative considered first was Blender's Solidify modifier on a
duplicate, which produces a hollow two-surface shell (inner = original,
outer = offset) that would then need a boolean *union* of that shell
against a near-coincident duplicate to get a single filled solid before
subtracting it as a cavity tool - a boolean between two surfaces that
nearly touch is exactly the kind of near-coincident-geometry case ADR
0005 already found produces unreliable Blender boolean results. The
per-vertex offset sidesteps that risk entirely: it produces one closed
solid directly, subtracted the same well-proven way `direct_cast`'s cavity
is.

**Known limitation: the offset is not a true Minkowski offset - flat
interior regions grow correctly, but sharp corners/edges are
under-grown.** Verified live in `.scratch_moldtest/debug_offset_subdivided.py`
against a subdivided cube: an interior face vertex (whose vertex normal,
after `recalc_face_normals`, is exactly perpendicular to its flat face)
displaced by exactly the requested `offset_mm`. A corner vertex also
displaced by exactly `offset_mm` in *length*, but along the diagonal
average of its three adjacent face normals - so its growth *per axis* is
only `offset_mm / sqrt(3)` (~58%), not the full `offset_mm` a true offset
surface would give a rounded corner. This was first surfaced as an
unexplained bounding-box discrepancy on the unit-cube integration fixture
(observed span ~10.46mm instead of the naively expected 13mm for
`shell_thickness_mm=3, skin_pour_wall_mm=3`) before being isolated to this
specific cause. Accepted as a documented v1 limitation rather than fixed,
because:
- It only affects sharp corners/edges, not flat or smoothly-curved
  regions - and this mode's whole purpose is organic/detailed models,
  which are dominated by curved surfaces where vertex normals already
  closely approximate the true surface normal.
- A properly rounded true-offset corner would need to *insert new
  geometry* there (an arc of faces), which per-vertex displacement of the
  existing mesh topology can't produce without a genuinely different
  algorithm (e.g. Blender's own Offset Edges / Bevel-based approaches) -
  out of scope for a v1 that already reuses `direct_cast`'s proven boolean
  pattern.
- The practical effect (a boxy model's corners come out with a
  thinner-than-requested shell) is the same category of "documented,
  don't guess" limitation ADR 0006 already accepted for `direct_cast`'s
  missing draft angle and release tolerance.
Documented in `mold.py`'s troubleshooting section and `make_mold.py`'s
module docstring.

**No self-intersection guard for concave models in v1.** A large
`shell_thickness_mm` relative to a model's concave features (e.g. a deep,
narrow groove) can push opposite walls' offset surfaces into and past each
other, folding the resulting mesh back on itself - the same category of
risk ADR 0006 flagged (and deferred) for a hypothetical `direct_cast`
release-tolerance offset. Not solved here either; documented as a known
limitation rather than adding non-manifold-detection machinery whose
recovery behavior would need its own separate design and verification
pass.

**Shared cavity size/center, split accessories exactly like
`silicone_block`.** `_build_form_fitting_mold()` computes the offset
object's own bounding box once, builds all 4 halves from it
(`skin_pour_bottom/top` with `skin_pour_wall_mm`, `support_jacket_bottom/top`
with `support_jacket_wall_mm`), adds registration keys to the skin-pour
pair only (`_add_registration_keys()`, unchanged), pour/vent holes to both
top halves (`_add_pour_holes()`, unchanged), and a bolted flange to the
support-jacket pair only (`_add_flange_half()`, unchanged) - directly
reusing every accessory helper `silicone_block` and `direct_cast` already
established, no new geometry helpers beyond the offset itself.

## Testing note

Same split as ADR 0005/0006: `tests/test_mold.py` mocks the Blender
subprocess for `form_fitting`'s parameter validation, CLI arg passing
(`--shell-thickness-mm`, `--skin-pour-wall-mm`, `--support-jacket-wall-mm`),
and `MoldResult` field population (only the `skin_pour_*`/
`support_jacket_*` fields populated, everything else `None`).
`tests/test_mold_integration.py` runs the real boolean pipeline against
the unit-cube fixture, checking all 4 parts are watertight and the
skin-pour cavity is smaller than the support-jacket cavity is not
required (they're the same cavity) but the jacket's outer wall is thicker.
The ray-casting containment check and the corner-offset measurement were
one-time development-time verifications in `.scratch_moldtest/
debug_form_fitting.py` and `.scratch_moldtest/debug_offset_subdivided.py`,
not re-run on every test pass - same rationale as ADR 0005/0006's
equivalent notes.
