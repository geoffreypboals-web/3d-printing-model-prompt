# 0006: Direct-cast rigid mold mode - model-mesh-as-cavity-tool, and what v1 deliberately skips

Date: 2026-09-12

## Context

`docs/mold-production-research-and-plan.md` (Phase 1) asked for a second mold
mode alongside the existing silicone pour-box + clamp-shell workflow
(`docs/adr/0005-two-piece-silicone-mold-and-clamp-shell.md`): a **direct-cast**
rigid mold whose cavity is shaped like the model itself, for casting
resin/urethane/foam straight into a 3D-printed mold with no silicone
intermediate step at all. `MoldRequest.mode` (see `models.py`) now accepts
`"silicone_block"` (existing default) or `"direct_cast"`.

The plan document's own Phase 1 description assumed this would "reuse
`build_half()` almost as-is" with a rectangular cavity plus an added draft
angle. That assumption didn't survive contact with the actual geometry
problem: a silicone_block cavity is a plain box (the model gets inserted
physically later), but a direct_cast cavity **has no physical model to
insert** - the mold itself has to already be shaped like the part. The
cavity-cutting tool has to be the model's own mesh, not a box.

## Decisions

**The cavity-cutting tool is a duplicate of the model's own mesh, not a
box.** `build_direct_cast_half()` in `blender_scripts/make_mold.py` builds
each half's outer box (model bounding box + `direct_mold_wall_mm` on every
side, only past the parting line - same box-range shape `build_half()`
uses) and boolean-subtracts a duplicate of the imported model object from
it (`_duplicate_object()`). Because `parting_z` is defined as the model's
own bounding-box Z midpoint, the model mesh always spans across the
parting plane by construction - subtracting it therefore always cuts a
genuine through-hole at that face, exactly the property `build_half()`
had to engineer manually (via the `wall + 5mm` cavity overshoot) for its
box-shaped cavity. Confirmed live against a real Blender install in
`.scratch_moldtest/debug_direct_cast.py`: ray-casting into a point at the
cube fixture's own center (inside its bottom half) came back **not**
solid, while a point in the surrounding wall material came back solid,
and the resulting half's own bounding box exactly matched the outer-box
math (model + wall + flange). This also meant `build_mold()` had to stop
deleting the imported model object immediately after bbox extraction (the
old silicone_block-only behavior) - it's now kept alive until both
direct-cast halves have duplicated it, then removed.

**No automatic draft angle in v1.** The plan doc's original Phase 1
description called for adding a real draft angle to the cavity walls.
Doing that correctly means deforming the model's own geometry (tapering
its side walls outward toward the parting line) - which, applied blindly
to an arbitrary uploaded model, would distort the model's true dimensions
without the caller asking for that. Real draft/undercut *analysis* (flag
which faces would need it, without silently changing the geometry) is
scoped to Phase 3's dedicated analysis endpoint in the plan doc, not this
mode. Shipping without draft for v1 means some models will stick and need
mold release or a flexible-material workaround - documented as a known
limitation rather than guessed at.

**No release-tolerance offset in v1.** An outward offset (e.g. growing the
cavity by 0.1-0.2mm on every side) is the standard fix for a friction-fit
cast part, but implementing it correctly needs either a mesh-level
Solidify/shrinkwrap pass or a per-vertex normal-offset step - both carry a
real self-intersection risk on concave or thin-featured meshes that would
need its own live-Blender verification pass before shipping, the same
discipline used to catch the box-scaling and cavity-sealing bugs in ADR
0005. Deferred rather than guessed at; the cast part fits the model's
exact dimensions for now (documented in mold.py's and make_mold.py's
troubleshooting sections).

**Bolted flange, not registration keys.** `docs/mold-production-research-and-plan.md`'s
research (the Creality guide in particular) describes direct rigid-to-rigid
mold halves as bolted together, not hand-registered with keys - unlike
silicone_block's pour box, which only needs to *register* two halves that
a human then holds/tapes shut for a low-pressure silicone pour. Reusing
`_add_flange_half()` (the same function the silicone_block clamp shell
uses) also sidesteps an edge case specific to keys on this mode: they're
placed at the four corners of the cavity's *bounding-box* footprint,
which for a boxy or rectangular model can coincide with the model's own
cavity void, producing broken or missing key geometry. Flanges don't have
that failure mode since they sit entirely outside the cavity, in the
wall+wall-margin ring.

**Sprue/vent placement reuses `_add_pour_holes()` unchanged, positioned at
the model's bounding-box center.** This is the same heuristic
silicone_block already uses for its (box-shaped) cavity, extended here to
an arbitrary model shape. For a convex, roughly-centered model this lands
the pour channel over the cavity as intended; for a strongly non-convex
model (an L-shape, a ring) the bbox center could fall outside the model's
own footprint at the parting plane, cutting a channel through solid wall
material next to empty cavity rather than into it. Accepted as a v1
limitation (documented in `mold.py`'s troubleshooting section) rather than
computing a true cross-sectional centroid, which the plan doc scopes to a
later phase's geometry-aware vent placement work.

**`clamp_flange_width_mm` and `bolt_hole_diameter_mm` are reused as-is,
not duplicated under direct_cast-specific names.** Both modes bolt two
rigid halves together via the same flange mechanism (`_add_flange_half()`),
so there's no reason for the request schema to carry two parallel sets of
flange parameters - `direct_mold_wall_mm` is the only new field
direct_cast actually needs.

## Testing note

Same split as ADR 0005: `tests/test_mold.py` mocks the Blender subprocess
for direct_cast's parameter validation, CLI arg passing (`--mode`,
`--direct-mold-wall-mm`), and MoldResult field population (silicone_block
fields None under direct_cast and vice versa). `tests/test_mold_integration.py`
runs the real boolean pipeline against the same unit-cube fixture ADR 0005
uses, checking both halves are watertight and correctly sized. The
ray-casting containment check that proves the cavity is genuinely carved
(not just correctly sized) was a one-time development-time verification
in `.scratch_moldtest/debug_direct_cast.py`, not re-run on every test pass
- same rationale as ADR 0005's equivalent note.
