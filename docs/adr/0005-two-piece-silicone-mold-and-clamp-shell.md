# 0005: Two-piece silicone mold generation - open-tray construction, keys vs. bolted flange, shared cavity size

Date: 2026-09-12

## Context

Beyond generating and modifying a printable model, a user may want to cast
that model in a different material - pour RTV silicone around the printed
part to make a flexible negative mold, then use that mold to cast plaster
of paris or cement. That workflow needs two separate rigid, 3D-printed
tools:

1. A **pour box**: a container you set the printed model inside and pour
   liquid silicone into, forming the negative mold once cured.
2. A **clamp shell**: once the silicone mold exists (as two halves), a
   second, more rigid jacket that clamps those halves together tightly
   enough to resist the hydraulic pressure of pouring plaster/cement into
   the silicone's own cavity - silicone alone will bulge and leak under
   that pressure without external support.

`mold.py` and `blender_scripts/make_mold.py` implement generating both,
in two halves each, from any mesh this service can load - reusing
`thickness.py`'s Blender-subprocess pattern (headless Blender, no LLM
call, deterministic).

## Decisions

**Each half is built as its own open tray directly, not by bisecting one
sealed box in half.** The first implementation attempt built a single
fully-enclosed hollow box (an outer box minus a cavity box, both
symmetric around the parting height) and then tried to bisect it in two
via `bmesh.ops.bisect_plane`. A hollow box's cross-section at the parting
plane is an *annulus* - an outer wall-perimeter loop plus a smaller,
disjoint cavity loop - and every fill technique tried to cap that cut
face (`holes_fill`, `triangle_fill`, `bridge_loops`) produced ambiguous
or outright wrong results (confirmed live against a real Blender 4.5
install, not just reasoned about - see the commit history for
`make_mold.py` if you want the blow-by-blow). The fix was to stop trying
to split a sealed shape after the fact: `build_half()` constructs each
half's own outer box and cavity-cutting box directly, with the
cavity-cutting box deliberately overshooting past the parting line by
`wall + 5mm` so the boolean difference exits cleanly through that one
face, leaving a genuine, ordinary "open box top" there (the exact shape
`watertight.py` already treats as an intentional opening) with zero
custom fill logic needed.

**A part with an open cavity can still report 0 boundary edges - that
doesn't mean it's sealed shut.** While debugging the above, `bm.edges`
with `len(link_faces) == 1` (bmesh's usual "this is a hole" signal)
reported **zero** boundary edges on parts that were, by construction,
supposed to have an open cavity. This is not a bug: Blender's EXACT
boolean solver represents a cut face with a hole in it (an annulus) as a
single n-gon whose loop traces the outer boundary, bridges via a
zero-width slit to the inner (hole) boundary, and back - every edge ends
up with 2 face users even though no face actually covers the hole's
interior. **Verified independently via ray-casting**: a ray cast straight
up from a point inside the cavity's open-air region hits nothing before
escaping the mesh, while the same ray from a point inside actual wall
material registers exactly 1 hit (odd = inside solid). Don't trust
"boundary edge count == 0" alone as a watertightness/opening check on a
boolean result with a hole in a cut face; corroborate with a containment
test (ray-cast, or in this case, the size/shape assertions in
`test_mold_integration.py`) when the geometry is this shape.

**The pour box uses interlocking hemispherical keys; the clamp shell uses
a bolted flange - different mechanisms for different jobs.** The pour
box's keys (a bump unioned into the bottom half's rim, a matching
oversized socket cut into the top half's rim, at the four corners of the
cavity footprint) only need to *register* the two halves during a
low-pressure pour - hand pressure or tape is enough to hold them while
silicone cures. The clamp shell's job is fighting the real hydraulic
pressure of a plaster/cement pour, which keys alone won't survive; it
gets a flange with four bolt holes (sized for M4 clearance by default)
so the user can actually torque the two halves together.

**The clamp shell's cavity is deliberately the *same size* as the pour
box's cavity, not derived from the clamp shell's own wall thickness.**
The silicone mold's outer shape, once cast, exactly matches the pour
box's cavity (model + `clearance_mm` on every side) - so the clamp shell
existing to hold *that* object snugly must share that same cavity size,
regardless of how thick the clamp shell's own walls are. Both `pour_box`
and `clamp_shell` in `build_mold()` are built from one shared
`cavity_size`/`cavity_center` computed once from the model's bounding
box; only `pour_box_wall_mm` vs. `clamp_wall_mm` (and the flange) differ
between them.

**Registration key radius is clamped to 40% of `pour_box_wall_mm`.** A
thin-walled pour box (say 2mm) combined with a large requested
`key_diameter_mm` would otherwise poke the key sphere out of the wall
ring entirely, into the cavity on one side or outside the outer wall on
the other. Clamping keeps the key embedded in whatever wall thickness was
actually requested, silently shrinking it rather than producing broken
geometry - `mold.py`'s docstring tells the caller to raise
`pour_box_wall_mm` instead of `key_diameter_mm` if that happens.

**One Blender subprocess builds all four parts.** Splitting pour-box and
clamp-shell generation into separate subprocess calls would mean
launching headless Blender twice per request for no benefit (they share
the same input mesh and cavity math) - `build_mold()` in
`blender_scripts/make_mold.py` builds and exports all four STL files
before exiting.

**`MAX_MOLD_DIMENSION_MM` (default 300mm) guards against a runaway
boolean pass, not against cost.** Unlike `MAX_WALL_THICKNESS_MM` (a
print-practicality limit) or the LLM-call rate limits (a cost control),
this exists purely so a parameter typo (e.g. `clearance_mm=50` meant as
`5.0`, or millimeters mistaken for centimeters) doesn't hang a headless
Blender process computing an exact boolean on a multi-meter box before
the caller notices anything is wrong. The check runs inside
`make_mold.py` itself (it needs the model's actual bounding box, which
only Blender has loaded) rather than as a pre-flight guess in `mold.py`.

## Testing note

`tests/test_mold.py` mocks the Blender subprocess entirely (matching
`test_thickness.py`'s approach) and only exercises `mold.py`'s own
orchestration - parameter validation, CLI argument passing, JSON report
handling. `tests/test_mold_integration.py` runs the real boolean/geometry
pipeline against an actual Blender install (skipped automatically if
`blender` isn't on `PATH`), checking watertightness and size
relationships between the four parts. It does **not** re-run the
ray-casting containment check described above on every test run - that
was a one-time verification during development (real bmesh + BVH ray
casting from a pytest fixture is a lot of machinery to keep exercising
for a regression the bbox/watertightness checks would also catch if a
future change broke the booleans outright, e.g. producing a missing or
degenerate part).
