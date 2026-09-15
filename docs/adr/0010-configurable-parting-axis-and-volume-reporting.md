# 0010: Configurable parting axis/offset via rotate-then-build, and per-mode casting-volume reporting

Date: 2026-09-13

## Context

`docs/mold-production-research-and-plan.md` (Phase 6) bundled two small,
independent additions: FR-6 (a configurable parting axis and an optional
offset from the model's own geometric center) and FR-8 (report the
cavity's actual casting/pour volume). Both were scoped as "parameter/
reporting extensions... neither needs new geometry techniques" - true in
spirit, but each still touched every one of the four build functions in
`make_mold.py`, so the actual decisions are recorded here rather than
skipped as "too routine for an ADR."

## Decisions

**Parting axis is implemented as a rotation applied once, up front - not
by rewriting every builder to take an arbitrary axis.** Every build
function (`build_half()`, `build_offset_cavity_half()`,
`_add_registration_keys()`, `_add_pour_holes()`, `_add_flange_half()`)
hardcodes Z as "the" parting axis. Rewriting all of them to accept an
arbitrary axis vector would have meant re-deriving and re-verifying the
box/cylinder/sphere placement math for every one of them. Instead,
`_rotate_axis_to_z()` bakes a rotation into the imported model's own mesh
data (`transform_apply`) at the very start of `build_mold()`, mapping the
requested `parting_axis` ('x' or 'y') onto local Z - `'z'` is a no-op.
Every downstream function then keeps operating on "Z" exactly as before,
oblivious to the fact a rotation happened. Verified live in
`.scratch_moldtest/debug_parting_axis.py` (a 1x2x3mm box, rotating x->z,
confirming the expected extent swap) and end-to-end in
`.scratch_moldtest/debug_parting_and_volume.py` (a real direct_cast mold
built with `parting_axis="x"`, confirming the outer mold's own bounding
box - including the flange - matches the rotated model's dimensions
exactly).

**The exported STL parts are NOT rotated back to the original upload's
orientation.** This is the one genuinely non-obvious behavioral
consequence: for a non-`z` `parting_axis`, every output file is in the
rotated working frame, not the frame the input file was uploaded in.
Rotating back would need tracking and re-applying the inverse transform
per exported object across all four builders - real, but non-trivial,
extra code for a purely cosmetic property (which way the file "sits" in
a slicer). Functionally nothing is lost: the cavity is congruent to the
(also rotated, in the same frame) physical model or mesh being cast, so
the mold works correctly - the physical model or a re-imported print
just needs to be inserted/oriented to match, exactly the same
accommodation a user already makes when assembling any mold around a
real object. Documented explicitly in `make_mold.py`'s and `mold.py`'s
troubleshooting sections rather than left as a surprise.

**Parting offset is validated once per builder via a shared
`_resolve_parting_z()` helper, against the *original* model's own
bounding box - not the mode-specific cavity's expanded bounding box.**
`parting_offset_mm` is a request-level concept ("shift the split relative
to the model's own geometry"), so its zero-point and valid range are
always the model's own bbox along the parting axis, even though
`silicone_block`'s actual cavity is larger (clearance added) and
`form_fitting`'s is larger still (grown outward by `shell_thickness_mm`).
Using the model's own bbox as the reference keeps the parameter's meaning
identical across all four modes, and keeps the validation error
(`"...outside the model's own range..."`) phrased in terms a caller
supplied a real model dimension for, not an internal derived cavity size
they'd have to reverse-engineer.

**Discovered and fixed along the way: `_add_registration_keys()` assumed
`cavity_center[2] == parting_z`, which an offset breaks.** Registration
keys need to sit exactly at the real parting seam for the two halves to
nest; before this phase the cavity's own geometric Z center and the
parting line were always the same value (nothing could move them apart),
so `_add_registration_keys()` read `cavity_center[2]` as a stand-in for
the parting line rather than taking it explicitly. Caught in code review
while wiring the offset through `_build_silicone_block_mold()` and
`_build_form_fitting_mold()` (both callers of that function) - fixed by
adding an explicit `parting_z` parameter rather than relying on an
identity that no longer holds. Verified live in
`.scratch_moldtest/debug_parting_offset_form_hollow.py` that
`form_fitting` still builds successfully with a non-zero offset.

**Every builder's two halves now get independently-sized `half_extent`
values** (`bottom_half_extent`/`top_half_extent`) instead of one shared
`half_extent` assumed symmetric - `build_half()`/`build_offset_cavity_half()`
were already fully self-contained per half (no shared-symmetry
assumption in their own logic), so this only required updating each of
the four `_build_*_mold()` functions' own call sites, not the shared
geometry primitives. Verified live that a 1mm `parting_offset_mm` on a
2x3x4mm box fixture produces a bottom half exactly 2mm taller than the
top half (a 1mm shift moves 1mm from one side to the other on each side
of the seam) - see `debug_parting_and_volume.py`'s case 3.

**Casting volume (FR-8) means something different per mode, always "the
material a real pour actually uses," not "the cavity-cutting object's own
solid volume."** Computed via a small `_object_volume_mm3()` bmesh helper
(`abs(bm.calc_volume(signed=True))`) plus, for `silicone_block`, a cheaper
analytic box-volume formula (no bmesh needed for a plain box):
- `silicone_block`: the pour box's own box-shaped cavity volume.
- `direct_cast`: the model's own mesh volume (that's the entire cast).
- `form_fitting`: the offset shell's volume *minus* the model's volume -
  only the thin gap between the two surfaces actually fills with
  silicone; reporting the offset solid's full volume would overstate
  material by roughly the model's own bulk.
- `hollow_cast`: the model's volume *minus* the core's volume - same
  reasoning, the core displaces the center so only the shell gap fills.
All four were spot-checked live in `.scratch_moldtest/debug_parting_and_volume.py`
against a 2x3x4mm box (`direct_cast`: exactly 24mm3, matching the box's
own volume; `silicone_block`: exactly matching the analytic
`(2+16)*(3+16)*(4+16)` cavity-box formula) and
`.scratch_moldtest/debug_parting_offset_form_hollow.py` (`form_fitting`/
`hollow_cast`: both produced sane, non-negative shell volumes on the
same fixture with a non-zero offset and non-`z` axis).

**`material_density_g_per_cm3` is a separate, optional request field
handled entirely in `main.py`, not passed into `mold.make_mold()`.** The
Blender subprocess has no reason to know about density - it only ever
reports a geometric volume. `main.py` pops the field out of
`MoldRequest.model_dump()` before forwarding the rest to `make_mold()`,
and multiplies it against `MoldResult.cavity_volume_cm3` itself
(`_estimated_mass_g()`) to populate `MoldResponse.estimated_cast_mass_g`
- `None` when no density was given, exactly mirroring how
`draft_check`/`repaired_hole_ids` are optional-but-always-present-shaped
fields elsewhere in the same response.

## Testing note

Same split as prior ADRs: `tests/test_mold.py` mocks the Blender
subprocess for CLI arg passing (`--parting-axis`, `--parting-offset-mm`)
and `MoldResult.cavity_volume_cm3` population (the fake subprocess now
always writes a `cavity_volume_mm3` field in its report).
`tests/test_mold_integration.py` runs the real pipeline, checking a
non-zero `parting_offset_mm` produces the expected asymmetric half sizes
and that `cavity_volume_cm3` is positive and roughly sized. The axis-
rotation extent-swap check, the exact asymmetric-height math, and the
per-mode volume-formula spot checks were one-time development-time
verifications (`.scratch_moldtest/debug_parting_axis.py`,
`debug_parting_and_volume.py`, `debug_parting_offset_form_hollow.py`),
not re-run on every test pass - same rationale as every prior ADR's
equivalent note.

## Correction (found while finishing this phase's own tests)

The asymmetric-half-sizing claim above was verified for the bottom half
and for `cavity_volume_mm3`, but not for the *top* half's true outer
ceiling height - no test asserted it. Writing
`test_make_mold_direct_cast_parting_offset_produces_asymmetric_halves`
(which does check it) surfaced a real, pre-existing bug in
`_add_pour_holes()`, unrelated to this phase's own offset/axis/volume
work: its sprue/vent cylinder was centered on the model's own original
geometric center with no relationship to the actual cavity footprint, so
a hole diameter comparable to (or larger than) that footprint's own XY
extent - which is exactly what the default `sprue_diameter_mm=10.0`
is, against the integration tests' 1mm cube fixture - made the cutting
cylinder's circular cross-section cover the *entire* ceiling plate. The
boolean DIFFERENCE then removed the whole ceiling instead of punching a
hole through it, silently producing a mold with no ceiling at all.
Reproduced with `parting_offset_mm=0.0` (the already-shipped default),
confirming this predates this phase entirely and simply had no test
checking for it.

Fixed by rejecting the case outright in `_add_pour_holes()` itself
(shared by all four modes) rather than patching only `direct_cast`:
`sprue_diameter_mm`/`vent_diameter_mm` must each be smaller than the
cavity's own XY footprint, or `make_mold.py` raises a clear `ValueError`
naming both diameters and the footprint instead of building a broken
mold. Verified live via `.scratch_moldtest/debug_offset_investigate.py`
that with a proportionate hole size the top half's ceiling comes out at
its correct full height (`(0.5, 4.0)` for the symmetric case, `(0.7,
4.0)` for a 0.2mm offset) in both cases. The integration tests'
1mm-cube fixture now passes a hole diameter scaled to each mode's own
cavity size (`direct_cast`/`hollow_cast`: 0.4/0.2mm against a ~1mm
cavity; `form_fitting`: 2.0/1.0mm against its ~4.46mm offset-shell
cavity) rather than the library defaults, which only make sense against
a realistically-sized part.
