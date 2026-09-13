# 0007: Draft-angle/undercut analysis - dot-product heuristic, per-half pull direction, non-blocking for direct_cast

Date: 2026-09-12

## Context

Phase 3 of `docs/mold-production-research-and-plan.md` asked for FR-4:
report which faces of a model would prevent a *rigid* mold from releasing
cleanly, since a rigid material (unlike Phase 1's target use case for
`direct_cast`) can't flex around an undercut the way silicone can. The
plan called for a dedicated Blender script (`blender_scripts/analyze_draft.py`,
mirroring `analyze_watertight.py`'s structure) and a standalone endpoint,
plus running it automatically as a non-blocking warning inside `direct_cast`
mold generation.

## Decisions

**Draft angle is measured as 90 degrees minus the angle between a face's
normal and its own half's pull direction**, not the raw dot product
itself. This gives an intuitive, single signed number: +90 for a face
whose normal points straight along the pull direction (ideal - a flat
cap perpendicular to the parting axis), 0 for a vertical wall parallel to
the pull axis (the plain undrafted case every mold-making guide warns
about), and negative for a genuine undercut (the surface would have to
pass back through solid material to release). A single `--min-draft-angle-deg`
threshold (default 2.0, per the Creality guide's 2-4 degree
recommendation cited in the research doc) then catches both "genuinely
can't release" (negative) and "releases, but with more friction than
requested" (0 up to the threshold) in one pass, without needing two
separate parameters or classification passes.

**Each face's pull direction is derived from which side of the parting
plane its own centroid falls on** (+axis for a face above the plane,
-axis for a face below it) - not a single global pull direction for the
whole mesh. This matches how `make_mold.py` itself actually splits a
mold: the "top" half is pulled away in +Z, the "bottom" half in -Z, and a
face's own half determines which of those two directions it needs to
release from a plain vertical wall gets flagged as "insufficient_draft"
for both halves it borders (0 degrees is 0 degrees regardless of which
half's pull direction you measure it against), and an overhang's
underside only reads as a genuine "undercut" for the half whose pull
direction it actually opposes.

**The parting plane is always the model's own bounding-box midpoint along
the requested axis** - the same convention `make_mold.py`'s
`parting_z = cavity_center[2]` already uses for Z, generalized to
whichever axis the caller picks. FR-6 (configurable parting axis *and
offset*, for an asymmetric model whose true widest cross-section isn't at
the geometric center) is explicitly a later, separate phase; this script
accepts `--pull-axis` for forward compatibility with that but does not
yet accept an explicit offset.

**Flagged faces are grouped into connected islands via the same BFS
pattern `analyze_watertight.py`'s `find_flipped_normal_islands()` already
uses** (per-triangle results would be unusable at any real mesh
resolution, and it's a proven, already-tested grouping algorithm rather
than a new one). An island's reported `min_draft_angle_deg` is the worst
(most negative/lowest) angle among its faces, and its classification is
`"undercut"` if that worst angle is negative, else `"insufficient_draft"`.

**Non-blocking for `direct_cast`, not run automatically for
`silicone_block`.** `mold.make_mold()` calls `draft_analysis.analyze_draft()`
once, automatically, after a successful `direct_cast` generation (surfaced
as `MoldResult.draft_check` / `MoldResponse.draft_check` /
`X-Draft-Releasable` + `X-Draft-Problem-Island-Count` headers on the
upload endpoint) - but only as a warning, never failing the request,
because flagging undercuts is exactly the kind of judgment call
(the user may genuinely be fine hand-flexing a mostly-rigid PETG print,
or only care about a subset of faces) that shouldn't block a request that
already produced valid geometry. `silicone_block` doesn't get this
automatic check at all, since flexible silicone forgives most of what it
would flag - a caller who wants to check it anyway (or wants a different
pull_axis/threshold than the direct_cast default) can call
`POST /models/{model_id}/mold/draft-check` directly. If the automatic
check itself fails for any reason (e.g. a transient Blender hiccup),
`_direct_cast_draft_warning()` logs and returns `None` rather than
undoing a mold that already generated successfully.

**Geometry verified live against a real Blender install, not just
reasoned about** - `.scratch_moldtest/debug_draft.py` confirmed a plain
unit cube's 4 vertical side walls (8 triangles) get grouped into one
connected island at exactly 0 degrees / `insufficient_draft`, correctly
excluding the top/bottom caps (which read +90 degrees, matching the flat-
cap-is-ideal intuition). `.scratch_moldtest/debug_draft_undercut.py`
separately confirmed a downward-facing quad placed in the mesh's own top
half reads exactly -90 degrees / `undercut`, proving the per-half pull-
direction logic (not just the general dot-product math) actually
branches correctly - the same "don't trust reasoning alone on a boolean/
geometry pass" discipline ADR 0005 established.

## Testing note

Same split as ADR 0005/0006: `tests/test_draft_analysis.py` mocks the
Blender subprocess for parameter validation and JSON-report parsing.
`tests/test_draft_analysis_integration.py` runs the real geometry pass
against the same unit-cube fixture used elsewhere (skipped automatically
if `blender` isn't on `PATH`), checking the exact island/classification
shape the two debug scripts above verified during development.
