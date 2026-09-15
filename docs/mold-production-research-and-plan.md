# 3D-printed mold production: research, requirements, and development plan

Date: 2026-09-12

This document is the research behind, and the roadmap for, this project's
mold-generation feature (`src/threedprompt/mold.py`,
`src/threedprompt/blender_scripts/make_mold.py`, see
`docs/adr/0005-two-piece-silicone-mold-and-clamp-shell.md` for what's
already built). It is a planning document, not an ADR - nothing here is a
committed decision until it's actually implemented and gets its own ADR.

## 1. Research summary

### 1.1 Manual technique - confirms the pattern this project already automates

[Instructables: "Making a 3-D Printed Mold"](https://www.instructables.com/Making-a-3-D-Printed-Mold/)
(2012) and the
[Creality forum step-by-step guide](https://forum.creality.com/t/step-by-step-guide-to-creating-3d-printed-molds/23431)
both describe the same manual CAD workflow our `make_mold.py` already
automates: box around the model → subtract the model → cut at a parting
plane at the model's widest point → add registration keys at the parting
line → add a pour hole and vents → print → bolt/clamp together → pour →
demold. Concrete numbers worth adopting:

- **Draft angle 2-4°** on vertical walls for reliable release (Creality) -
  we don't apply any draft today; our cavity walls are perfectly vertical,
  which works for flexible silicone (per Instructables: "if you are
  planning cast with a flexible material such as silicone... you can get
  away with fudging some of these guidelines") but will bind a **rigid**
  direct-cast mold (see 1.2).
- **Vent channels ~0.2mm deep** (Creality) rather than full-diameter holes
  for fine air escape without leaking liquid material - our current vent
  is a full-diameter through-hole, adequate for silicone (viscous, slow
  pour) but not ideal for a thin urethane/resin direct-cast.
- **Parting line placement is "a bit of an art"** (Instructables) - both
  guides confirm the widest-cross-section heuristic our `parting_z =
  cavity_center[2]` (the model's own vertical midpoint) already uses is
  the standard default, not a shortcut.
- **FDM molds top out at "hundreds" of parts** (Creality) vs. industrial
  tooling - correctly sets expectations for this feature's target user
  (a hobbyist/maker, not a production line).

### 1.2 Two competing techniques - our feature currently only builds one

[JLC3DP's guide](https://jlc3dp.com/blog/3d-printed-mold-making) and the
Creality guide describe **two different, non-overlapping** workflows that
get conflated under "3D printed mold":

1. **Silicone-intermediate ("indirect") casting** - print a rigid *master*
   or *pour box*, cast flexible RTV silicone against it, then use the
   *silicone* as the actual mold for the final part (plaster, cement, wax,
   resin). This is what our existing pour-box + clamp-shell feature
   builds. Reusable dozens-hundreds of times; needed whenever the final
   part has any undercut a rigid mold couldn't release from.
2. **Direct casting** - print the mold *cavity itself* in rigid plastic
   (PLA/PETG/resin) and pour the final casting material (urethane rubber,
   resin, even foam) straight into the printed cavity - no silicone step
   at all. This is what both the Instructables and Creality guides
   actually demonstrate end-to-end. Cheaper and faster per mold (skips an
   entire casting+cure cycle and a consumable), but only works when the
   part has **no undercuts along the parting axis** (a rigid mold can't
   flex to release one) and the draft-angle guidance above actually
   matters, since the material won't give the way silicone does.

**Our feature only builds mode 1.** Mode 2 is a materially simpler,
faster, and cheaper-per-use workflow that a meaningful share of users
(anyone casting a simple, undercut-free shape) would prefer by default -
see Requirement FR-1.

### 1.3 Thin-shell / form-fitting molds - the third technique, material-efficiency-driven

Both competitor products described in 1.4 support a *third* mode, distinct
from a solid rectangular silicone block: a **thin, contour-hugging
flexible shell** (Moldboxer calls it "Printed Mold · Skin," aimed at
"latex and silicone masks"; Mold Studio calls it "form-fitting" mode)
rather than a thick block of silicone cast in a rectangular box. The
shell is thin (a few mm, following the model's own surface via an offset,
not a bounding box), which uses drastically less silicone for large or
organic shapes, and - because a thin shell is far more flexible than a
solid block of the same silicone - still releases from moderate undercuts
a thick block might lock onto. Because a thin flexible shell alone can't
hold its shape or resist casting pressure, both tools pair it with a
rigid **support jacket** (a two-part case sized to the shell's *outer*
surface) that holds the shell in the correct shape during the pour -
functionally the same role our existing clamp shell already plays for the
block-mold case, just with a differently-shaped inner cavity (the model's
offset contour, not a rectangular box).

This is a genuinely different geometry problem from what `make_mold.py`
does today (box-minus-box booleans): it needs an **offset/shrink-wrapped
surface** following the model's own shape, not axis-aligned primitives.
See FR-3 / Phase 4 below.

### 1.4 Feature survey - two competing automated tools

[Moldboxer](https://moldboxer.com/) and
[Mold Studio](https://moldstudio.app) (a free browser tool one of this
project's users found via
[r/resinprinting](https://www.reddit.com/r/resinprinting/comments/1u2zmhe/i_built_a_free_tool_that_generates_3d_printable/))
are both purpose-built "upload an STL, get a mold" web tools - closer
competitors to this feature than the general-purpose CAD tooling below.
Consolidated feature list, cross-referenced against what we have:

| Feature | Moldboxer | Mold Studio | This project |
|---|---|---|---|
| Rectangular box + cavity mold | Yes ("Adapted Box") | Yes ("box" mode) | **Yes (built)** |
| Direct-print rigid mold (no silicone step) | Yes ("Direct-Mold" variants) | implied (FAQ: "pour silicone **or resin**" into the printed parts) | **Yes (built, FR-1 - `mode=direct_cast`, see ADR 0006)** |
| Thin/form-fitting shell mold | Yes ("Printed Mold · Skin") | Yes ("form-fitting" mode) | **Yes (built, FR-3 - `mode=form_fitting`, see ADR 0008)** |
| Hollow/vessel inner core | Yes ("Inner Cavity") | Yes ("hollow cast" with inner core + flange) | **Yes (built, FR-5 - `mode=hollow_cast`, see ADR 0009)** |
| Registration keys at parting line | Yes ("Smart Base Key Placement") | Yes | **Yes (built)** |
| Bolted/clamped rigid outer shell | Yes ("Efficient Casting Boxes") | implied ("clamp them together") | **Yes (built, clamp shell)** |
| Automatic mesh repair before generation | Yes ("Safe Mesh Processing") | Yes (auto-repairs non-manifold on upload) | **Yes (built, FR-2 - wired into `/mold` and `/models/{model_id}/mold`)** |
| Draft/undercut detection or removal | Yes ("Clear Extraction") | not advertised | **Yes (built, FR-4 - detection only, no auto-removal, see ADR 0007)** |
| Configurable/multiple parting planes | Yes ("Unlimited Customizable Splits") | not advertised | **Yes (built, FR-6 - `parting_axis`/`parting_offset_mm`, see ADR 0010)** |
| Air-trap detection + support pillars | Yes | not advertised | **Yes (built, FR-7 - single-highest-peak detection, see ADR 0011; no support pillars, out of scope)** |
| Silicone/material volume calculator | Yes | Yes ("exact silicone volume... in cm³ and grams") | **Yes (built, FR-8 - `cavity_volume_cm3`/`estimated_cast_mass_g`, see ADR 0010)** |
| Adjustable wall thickness / tolerances | Yes | Yes | **Yes (built: `clearance_mm`, wall params)** |
| Pouring sprue / funnel | Yes | Yes | **Yes (built: `sprue_diameter_mm`)** |
| Custom branding/engraving on mold | Yes | not advertised | No (out of scope - low value for this project) |
| Batch/grid tool for multiple molds at once | Yes ("Grid Tool") | not advertised | No (out of scope for now) |

[SolidWorks 3D Mold Creator](https://www.solidworks.com/product/3d-mold-creator)
is professional injection-mold tooling (core/cavity split, shutoff
surfaces, parting-surface generation) aimed at industrial production, not
DIY/silicone casting - not a direct feature competitor, but its **"mold
design workflow intelligence"** (the tool tracks which step you're on,
warns if you skipped one, and suggests the next action) is a UX pattern
worth borrowing for whatever front-end this feature eventually gets - see
FR-9. [3DWASP's WASP App](https://www.3dwasp.com/en/wasp-app-parametric-clay-3d-modelling-software/)
turned out to be unrelated (parametric clay-printer slicing software, no
mold-making feature at all) - included for completeness, not further
referenced below.

## 2. Software requirements

Numbered so the development plan (§3) can reference them. "Built" means
already shipped in `mold.py`/`make_mold.py` as of this document's date.

- **FR-1 (new): Direct-cast rigid mold mode.** Generate a two-part rigid
  mold whose cavity *is* the model (offset by a small tolerance for
  release, not a full silicone-thickness clearance), for casting resin,
  urethane, or foam directly - no silicone intermediate. Must apply a
  real draft angle (FR-4) since a rigid material won't forgive vertical
  walls the way silicone does.
- **FR-2 (new): Auto mesh repair before generation.** Every competitor
  surveyed repairs the input mesh automatically before building a mold;
  we already have this capability (`watertight.analyze_mesh` /
  `repair_mesh`) but `/mold` and `/models/{model_id}/mold` don't call it.
  Should run automatically (repair non-manifold defects it's confident
  about) with the option to surface what it found, not silently.
- **FR-3 (new): Form-fitting thin-shell mold mode.** An offset/
  shrink-wrapped thin shell following the model's own surface (not a
  rectangular box), paired with a rigid two-part support jacket sized to
  the shell's outer surface - see §1.3. This is a different geometry
  technique from the box-boolean approach `make_mold.py` uses today
  (needs a true offset surface, e.g. Blender's Shrinkwrap or a Solidify
  push outward from the model's own surface, not axis-aligned primitives)
  and is the biggest single lift in this plan.
- **FR-4 (new): Draft-angle and undercut analysis.** Before generating a
  *rigid* mold (both the existing block pour-box, on its rigid parts, and
  new FR-1), report any face whose normal, projected onto the parting
  axis, would prevent clean release (the deterministic geometric-
  heuristic approach already established in `hole_classifier.py` per ADR
  0004 - dot product of face normal vs. pull direction, no LLM/ML). Purely
  informational for the existing silicone modes (flexible material
  forgives most of this); load-bearing for FR-1.
- **FR-5 (new): Hollow/vessel inner-core mode.** For a hollow model (a
  vase, a cup), generate a second, inward-offset core solid (offset
  inward by the desired cast wall thickness) that seats into the cavity
  from one open face, so the cast forms a shell rather than a solid block.
- **FR-6 (new): Configurable parting axis and offset.** `make_mold.py`
  currently hardcodes the parting plane at the model's own Z-midpoint.
  Extend `MoldRequest`/`build_mold()` to accept a parting axis (X/Y/Z) and
  an optional explicit offset, for models whose natural widest
  cross-section isn't at the geometric center (e.g. an asymmetric
  figurine) - `build_half()`'s sign/axis parametrization already
  generalizes to this without a redesign.
- **FR-7 (new): Geometry-aware vent/air-trap placement.** Replace the
  current fixed `0.35 * cavity_size` vent offset with detection of actual
  trapped-air pockets (locally highest points of the model's own surface
  relative to the pour direction) the way Moldboxer's "Automatic Air-Trap
  Support Pillars" does - lower priority than FR-1/FR-3/FR-4, since the
  current fixed-offset vent is a reasonable default for the box/clamp-
  shell modes already shipped.
- **FR-8 (new): Silicone/casting material volume calculator.** Report
  the cavity's actual volume (cheap to compute in the same Blender pass
  that already has the mesh loaded - `bm.calc_volume()` on the cavity-
  cutting geometry) in cm³ and estimated grams (silicone/resin density is
  a simple user-supplied or looked-up constant), matching a feature both
  competitor tools treat as standard. Surfaced in the API response, not a
  new endpoint.
- **FR-9 (new, UI-only, low priority): Guided step sequencing in the
  browser UI.** If/when `static/index.html` grows a mold-generation form,
  borrow SolidWorks 3D Mold Creator's "tell the user what to do next, warn
  if a step was skipped" pattern rather than a flat form - out of scope
  until the UI work itself is prioritized (see the prior conversation:
  browser UI wiring was explicitly deferred).
- **FR-10 (built): Two-part silicone block mold** with registration keys
  (`pour_box_bottom.stl` / `pour_box_top.stl`).
- **FR-11 (built): Bolted rigid clamp shell** sized to the silicone mold's
  own outer surface, for casting plaster/cement under pressure
  (`clamp_shell_bottom.stl` / `clamp_shell_top.stl`).
- **FR-12 (built): Adjustable wall thickness, clearance, sprue/vent
  diameter, flange width, and bolt-hole diameter** via `MoldRequest`.
- **FR-13 (built): One-request, one-Blender-subprocess, zip-bundled
  four-file output**, both for a model this service generated
  (`POST /models/{model_id}/mold`) and an arbitrary upload (`POST /mold`).

### Non-functional requirements (carried over from CLAUDE.md, restated for this feature specifically)

- **No new paid dependency or LLM/API call for any mode above** - every
  technique here (draft analysis, offset shells, volume calculation) is
  deterministic geometry, computable in the same headless-Blender
  subprocess pattern already established (rule 6/10).
- **One Blender subprocess per request**, regardless of how many modes or
  parts that request produces - do not add a second `subprocess.run` call
  per mode (rule 9, and ADR 0005's existing rationale for combining pour
  box + clamp shell into one invocation extends to any new mode added
  alongside them).
- **Every new mode ships with the same test split already established**:
  a mocked-subprocess unit test (`test_mold.py`-style, tests the Python
  orchestration and validation) plus a real-Blender integration test
  (`test_mold_integration.py`-style, skipped when `blender` isn't on
  `PATH`) that checks the actual geometry, not just that files exist.
- **A genuinely non-obvious geometry decision gets an ADR** once
  implemented (rule 24) - FR-1, FR-3, and FR-4 are all likely candidates
  given how much iteration ADR 0005 already needed for a simpler case.

## 3. Development plan

Phased so each phase ships independently and keeps the "one Blender
subprocess, mocked + real-Blender tests, README/CHANGELOG/ADR" discipline
established for the existing feature. Ordered by a mix of user value and
implementation cost - not a fixed commitment, re-evaluate after each
phase actually ships.

**Phase 1 - Direct-cast rigid mold mode (FR-1, partial FR-4). SHIPPED -
see `docs/adr/0006-direct-cast-mold-mode.md`.** The cavity turned out to
need the model's own mesh as the boolean-subtraction tool rather than a
box (there's no physical model to insert in this mode, so the cavity has
to already be shaped like the part) - `build_half()` couldn't be reused
directly for that, so a parallel `build_direct_cast_half()` was added
instead. Draft angle and release tolerance were deliberately deferred
rather than built as originally described here (draft would otherwise
silently distort an arbitrary model's true geometry; tolerance needs its
own self-intersection-risk verification pass first - see the ADR). The
clamp shell is skipped entirely as planned; the direct-cast mold's two
halves bolt together via the same flange mechanism the clamp shell uses.
New `MoldRequest.mode` field: `"silicone_block"` (default) vs.
`"direct_cast"`.

**Phase 2 - Wire in auto mesh repair (FR-2). SHIPPED.** Implemented
exactly as scoped: `mold._ensure_watertight_input()` calls
`watertight.analyze_mesh()` before generation, auto-repairs any
LIKELY_DEFECT hole via `watertight.repair_mesh()`, and fails fast with a
clear `MoldError` if the mesh is still not watertight afterward (or has
no confidently-repairable holes at all). No new ADR needed, per this
plan's own note that FR-2 wasn't a likely candidate for one - small,
self-contained, no new geometry code, confirmed live: the real classifier
called a symmetric single-missing-triangle test fixture an
INTENTIONAL_OPENING rather than a defect (see the real-Blender test in
`test_mold_integration.py`), correctly refusing to auto-repair it rather
than guessing.

**Phase 3 - Draft/undercut analysis endpoint (FR-4, full). SHIPPED - see
`docs/adr/0007-draft-undercut-analysis.md`.** Implemented as scoped:
`blender_scripts/analyze_draft.py` mirrors `analyze_watertight.py`'s
structure (same island-grouping BFS, reused directly), exposed as
`POST /models/{model_id}/mold/draft-check`, and `mold.make_mold()` runs
it automatically as a non-blocking warning after a `direct_cast`
generation. One refinement made during implementation: draft angle is
measured per-face against *that face's own half's* pull direction
(whichever side of the parting plane its centroid falls on), not one
global pull direction for the whole mesh - verified live against a real
Blender install that this correctly distinguishes a plain vertical wall
(0 degrees, "insufficient_draft") from a genuine overhang (negative
degrees, "undercut") rather than conflating them.

**Phase 4 - Form-fitting thin-shell mode (FR-3). SHIPPED - see
`docs/adr/0008-form-fitting-thin-shell-mold.md`.** Built as a per-vertex
normal-offset (`_offset_model_along_normals()`) rather than the Shrinkwrap/
Solidify approach originally sketched here - a Solidify pass produces a
hollow two-surface shell needing a risky boolean *union* against
near-coincident geometry to fill, exactly the kind of case ADR 0005 found
unreliable; the direct offset instead produces one closed solid straight
away, subtracted from an outer box via the same `build_direct_cast_half()`
pattern Phase 1 proved (generalized and renamed
`build_offset_cavity_half()` to be shared by both modes). The support
jacket reuses `_add_flange_half()` unchanged, exactly the
`clamp_shell`-around-a-cavity idea this plan anticipated. One thing *not*
anticipated here: verified live in `.scratch_moldtest/debug_offset_subdivided.py`
that the offset is only exact on flat/curved regions - sharp corners/
edges under-grow (a vertex normal there averages several face normals
instead of being perpendicular to one), which is fine for the
organic/detailed models this mode targets but not for boxy ones.
Documented as a v1 limitation rather than fixed (see the ADR for why).
New `MoldRequest.mode="form_fitting"`, `shell_thickness_mm`,
`skin_pour_wall_mm`, `support_jacket_wall_mm` fields.

**Phase 5 - Hollow/vessel inner-core mode (FR-5). SHIPPED - see
`docs/adr/0009-hollow-vessel-inner-core-mode.md`.** Turned out to need no
new boolean operation at all: the core is just `_offset_model_along_normals()`
run with a negative offset (shrinking the model inward by
`cast_wall_thickness_mm`), exported as its own solid, and the outer mold
is `direct_cast`'s own halves unmodified - `_build_direct_cast_halves()`
was extracted from `direct_cast`'s builder so `hollow_cast` could reuse
it before deleting the model object it also needs for the core.
Confirmed simpler than anticipated: no stem/flange/registration geometry
was needed for the core either, since the offset naturally leaves a
`cast_wall_thickness_mm` gap on every side (including vertically) once
it's placed inside the cavity by hand. Inherits form_fitting's
corner-rounding/self-intersection limitations, applied inward. New
`MoldRequest.mode="hollow_cast"`, `cast_wall_thickness_mm` field.

**Phase 6 - Configurable parting axis/offset + volume calculator (FR-6,
FR-8). SHIPPED - see
`docs/adr/0010-configurable-parting-axis-and-volume-reporting.md`.** Two
small, independent additions bundled together since neither needed new
geometry techniques - both are parameter/reporting extensions to code
that already existed. `parting_axis` is a rotation baked into the
model's own mesh before any builder runs, rather than rewriting every
builder to take an arbitrary axis; `parting_offset_mm` is validated
against the model's own bbox (not each mode's larger cavity) so its
meaning stays identical across all four modes. Found and fixed a real
bug along the way in code this phase touched but didn't introduce:
`_add_registration_keys()` assumed the cavity's own Z center was always
the parting line, which an offset breaks. Testing this phase's own
asymmetric-offset behavior surfaced a second, independent pre-existing
bug in `_add_pour_holes()` (present since Phase 1, never caught because
no prior test checked the top half's true outer ceiling height): a
sprue/vent diameter comparable to the cavity's own footprint could
silently punch away the entire ceiling instead of a hole. Fixed with a
clear rejection in the shared `_add_pour_holes()` rather than a
mode-specific patch, since all four modes funnel through it. New
`MoldRequest.parting_axis`, `parting_offset_mm`,
`material_density_g_per_cm3` fields; new
`MoldResponse.cavity_volume_cm3`, `estimated_cast_mass_g` fields.

**Phase 7 - Geometry-aware vent placement (FR-7). SHIPPED - see
`docs/adr/0011-geometry-aware-vent-placement.md`.** `_find_vent_xy()`
detects a real trapped-air pocket - a strict local Z-maximum of the
cavity's own roof surface (excluding purely vertical wall edges, which
otherwise make every corner of a flat-topped box look like a "peak") -
and vents there instead of at the fixed `0.35 * cavity_size` offset,
falling back to that same fixed offset for `silicone_block`'s synthetic
box cavity or any model with no genuine peak (a plain box), so every
already-shipped shape is unaffected. Scoped to the single highest
surviving peak rather than one vent per detected pocket, since this was
explicitly the lowest-priority refinement in this plan - true parity
with Moldboxer's multi-pillar detection was judged not worth the added
cylinder-cut/parameter complexity for a v1. Live-verified via
`.scratch_moldtest/debug_vent_placement.py` (the algorithm in isolation)
and `debug_vent_placement_e2e.py` (ray-cast proof through the real
pipeline that the vent moved off the old fixed location).

**Deferred / out of scope for now:** custom branding/engraving, batch
grid tool for multiple simultaneous molds, and any browser-UI work (FR-9)
- all explicitly lower value for this project's current single-user API-
first shape than the geometry capabilities above, and the UI question was
already deferred once this session.
