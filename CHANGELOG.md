# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/), versioning follows
[SemVer](https://semver.org/) once there's a tagged release.

## [Unreleased]

### Added (geometry-aware vent placement - Phase 7 of the mold research plan)

- Vent-hole placement (all four modes) now detects a real trapped-air
  pocket - the cavity's own highest local surface peak, excluding
  purely-vertical wall edges so a flat-topped box's corners don't falsely
  register as peaks - instead of always cutting the vent at a fixed
  `0.35 * cavity_size` offset from the cavity center. Falls back to that
  same fixed offset when there's no real cavity mesh to analyze
  (`silicone_block`'s synthetic box cavity) or no genuine peak survives
  (a plain box's flat top, or a peak too close to the sprue to be worth a
  separate vent) - every already-shipped, boxy model produces the exact
  same mold as before this phase.
- `docs/adr/0011-geometry-aware-vent-placement.md` records the local-
  maximum definition (and the flat-box false positive it took to get
  there right), the fixed-offset fallback design, why only the single
  highest peak is vented rather than one vent per pocket (scoped down
  from full Moldboxer-style multi-pillar parity as this phase's own
  lowest-priority framing calls for), and the ray-cast-based live
  verification proving the vent actually moves for a real bump.

### Added (configurable parting axis/offset + casting-volume reporting - Phase 6 of the mold research plan)

- `MoldRequest.parting_axis` (`"x"`/`"y"`/`"z"`, default `"z"`) picks which
  model axis the two halves split along, for all four modes. Implemented
  as a one-time rotation baked into the imported model's own mesh before
  any other geometry code runs (`_rotate_axis_to_z()`), so every builder
  keeps hardcoding "Z" internally - a non-`z` axis means the exported
  parts stay in that rotated working frame rather than the upload's
  original orientation (documented in `make_mold.py`'s and `mold.py`'s
  troubleshooting notes).
- `MoldRequest.parting_offset_mm` (default `0.0`) shifts the parting
  plane away from the model's own bounding-box midpoint along
  `parting_axis`, for all four modes - rejected with a clear error if it
  would push the split outside the model's own range.
- `MoldResponse.cavity_volume_cm3` (always populated) and
  `MoldResponse.estimated_cast_mass_g` (populated only when the new,
  optional `MoldRequest.material_density_g_per_cm3` request field is
  given) report the actual pour/cast material volume and its estimated
  mass - meaning differs per mode (see
  `docs/adr/0010-configurable-parting-axis-and-volume-reporting.md`):
  `silicone_block`'s pour-box cavity, `direct_cast`/`hollow_cast`'s own
  model volume (minus the core's, for `hollow_cast`), `form_fitting`'s
  thin shell gap. Surfaced as `X-Cavity-Volume-Cm3` /
  `X-Estimated-Cast-Mass-G` headers on `POST /mold`.
- `docs/adr/0010-configurable-parting-axis-and-volume-reporting.md`
  records the rotate-then-build design, why `parting_offset_mm` is
  validated against the model's own bbox rather than each mode's larger
  cavity, a real `_add_registration_keys()` bug found and fixed along the
  way (it assumed the cavity's own Z center was always the parting line,
  which an offset breaks), and per-mode volume-formula definitions - plus
  a follow-up correction documenting a second, pre-existing bug this
  phase's own tests caught: `_add_pour_holes()` could silently punch away
  a mold's entire ceiling instead of a hole when the sprue/vent diameter
  wasn't meaningfully smaller than the cavity's own footprint (now a
  rejected, clearly-named error instead of silently broken geometry).

### Added (hollow/vessel inner-core mold mode - Phase 5 of the mold research plan)

- `MoldRequest.mode` now also accepts `"hollow_cast"` - the same rigid
  two-part outer mold as `direct_cast` (`hollow_cast_bottom.stl` /
  `hollow_cast_top.stl`) plus a separate solid core
  (`hollow_cast_core.stl`) - the model's own surface shrunk inward by
  the new `cast_wall_thickness_mm` field, using the same
  `_offset_model_along_normals()` helper `form_fitting` introduced (run
  with a negative offset here). Seat the core inside the bottom half's
  cavity before closing the top half and pouring, so the cast forms a
  hollow shell instead of a solid block - for casting vessels (vases,
  cups) without wasting material on a solid center.
- `docs/adr/0009-hollow-vessel-inner-core-mode.md` records the design
  (no new boolean operation needed - the core is just a smaller solid
  nested in the same model-shaped cavity `direct_cast` already builds;
  `_build_direct_cast_halves()` extracted from `direct_cast`'s builder so
  both modes share it) and the corner-rounding/self-intersection
  limitations inherited from reusing form_fitting's offset technique in
  the inward direction.

### Added (form-fitting thin-shell mold mode - Phase 4 of the mold research plan)

- `MoldRequest.mode` now also accepts `"form_fitting"` - a thin,
  contour-following silicone skin-pour tool (`skin_pour_bottom.stl` /
  `skin_pour_top.stl`) plus a matching rigid support jacket
  (`support_jacket_bottom.stl` / `support_jacket_top.stl`), 4 parts
  total, for organic/detailed models a rigid `direct_cast` mold couldn't
  release along any single axis. Both tools share one cavity shape - the
  model's surface grown outward by the new `shell_thickness_mm` field -
  since that offset surface is the finished silicone shell's own outer
  face. New `skin_pour_wall_mm` / `support_jacket_wall_mm` request
  fields.
- `docs/adr/0008-form-fitting-thin-shell-mold.md` records the design
  (per-vertex-normal-offset technique chosen over a Solidify-modifier
  approach, the shared-cavity-shape insight reused from
  `silicone_block`'s pour-box/clamp-shell split, and the corner-rounding
  limitation found and verified live: flat/curved regions grow by exactly
  `shell_thickness_mm`, but sharp corners/edges are under-grown since
  their vertex normal averages several face normals rather than being
  perpendicular to any one face).

### Added (draft-angle/undercut analysis - Phase 3 of the mold research plan)

- `POST /models/{model_id}/mold/draft-check` reports which faces of a
  model would prevent a *rigid* two-part mold from releasing cleanly
  along a given pull axis - a deterministic dot-product heuristic (no
  LLM/ML), read-only, generates no files.
  `src/threedprompt/draft_analysis.py` / `src/threedprompt/blender_scripts/analyze_draft.py`.
- `POST /models/{model_id}/mold` and `POST /mold` now run this
  automatically (non-blocking) after generating a `direct_cast` mold,
  surfaced as `draft_check` in the JSON response or the
  `X-Draft-Releasable` / `X-Draft-Problem-Island-Count` headers - a rigid
  mold can't flex around an undercut the way `silicone_block`'s silicone
  step can.
- `docs/adr/0007-draft-undercut-analysis.md` records the design (why
  draft angle is measured per-half against that half's own pull
  direction rather than one global direction, the island-grouping reuse
  from `analyze_watertight.py`, and why it's non-blocking for
  `direct_cast` and not run automatically at all for `silicone_block`).

### Added (auto mesh repair before mold generation - Phase 2 of the mold research plan)

- `POST /models/{model_id}/mold` and `POST /mold` now run
  `watertight.analyze_mesh()` on the input before generating anything;
  holes the existing classifier heuristic confidently calls likely
  defects are auto-repaired via `watertight.repair_mesh()`, and anything
  left open (ambiguous/intentional-looking holes, non-manifold junction
  edges) fails the request with a clear `MoldError` instead of handing
  Blender's boolean solver broken geometry. Which holes were repaired (if
  any) is surfaced as `repaired_hole_ids` in the JSON response /
  `X-Repaired-Hole-Ids` header, not silently.

### Planning (no code changes)

- `docs/mold-production-research-and-plan.md` - research into 3D-printed
  mold production techniques (direct-cast rigid molds vs. silicone-
  intermediate casting vs. thin form-fitting shells) and a feature survey
  of Moldboxer, Mold Studio, and SolidWorks 3D Mold Creator, translated
  into numbered requirements and a phased development plan for what this
  project's mold feature doesn't cover yet.

### Added (direct-cast mold mode - Phase 1 of the mold research plan)

- `MoldRequest.mode` (`"silicone_block"` default, or `"direct_cast"`) on
  `POST /models/{model_id}/mold` and `POST /mold` - `direct_cast` produces
  a single rigid two-part mold (`direct_mold_bottom.stl` /
  `direct_mold_top.stl`) whose cavity is the model's own mesh geometry,
  for casting resin/urethane/foam directly with no silicone step. Bolted
  flange (reused from the clamp shell), no registration keys, no separate
  clamp shell. New `direct_mold_wall_mm` request field.
- `docs/adr/0006-direct-cast-mold-mode.md` records the design (model-mesh-
  as-cavity-tool instead of a box, and why draft angle, release tolerance,
  and registration keys were all deliberately skipped in v1 rather than
  guessed at).

### Added (mold generation)

- `POST /models/{model_id}/mold`, `POST /mold`,
  `GET /models/{model_id}/mold.zip` - generates a two-part silicone pour
  box and a matching two-part rigid clamp shell for a model, via headless
  Blender booleans (no LLM call). The pour box is for casting a model in
  RTV silicone; the clamp shell holds the resulting flexible silicone mold
  rigid via a bolted flange while pouring plaster of paris or cement into
  it. `src/threedprompt/mold.py` / `src/threedprompt/blender_scripts/make_mold.py`.
- `docs/adr/0005-two-piece-silicone-mold-and-clamp-shell.md` records the
  design (why each half is built as its own open tray rather than
  bisecting a sealed box, keys vs. bolted flange, shared cavity size, and
  a subtle boolean-geometry gotcha found and fixed during development).

### Added (watertight analysis/repair)

- `POST /watertight/upload`, `POST /models/{model_id}/analyze`,
  `POST /models/{model_id}/repair`, `GET /models/{model_id}/viewer.glb` -
  finds boundary-edge holes and inverted-normal ("wrinkle") defects on a
  mesh via headless Blender, classifies each hole as a likely intentional
  opening (a cup's mouth, an open box top, an open base underside) vs. a
  likely unintentional defect via a pure-Python geometric heuristic
  (`src/threedprompt/hole_classifier.py`), and lets the caller close a
  chosen subset and re-check watertightness.
- Browser 3D viewer at `/watertight.html` (three.js, vendored locally -
  see `src/threedprompt/static/vendor/three/`) - upload a model, see every
  flagged hole as a color-coded clickable marker on the actual mesh, pick
  which to close, download the repaired file.
- `docs/adr/0004-watertight-hole-detection-and-repair.md` records the
  design (heuristic vs. ML classification, why hole ids are positional,
  the Blender/glTF axis-conversion and OrbitControls-singularity bugs
  found and fixed during development).

### Changed

- Default `MAX_UPLOAD_BYTES` raised from 50MB to 300MB - dense/scanned
  STL meshes routinely exceeded the old default. Still configurable via
  `.env` for files larger than that.
- `POST /thicken` (arbitrary file upload) now returns the thickened STL
  file directly instead of JSON, so uploading and getting the modified
  file back is one round trip - usable directly from Swagger UI's
  "Execute" -> "Download file". The new model_id and method are now
  carried as `X-Model-Id` / `X-Thicken-Method` response headers instead of
  JSON fields. `POST /models/{model_id}/thicken` is unchanged (still JSON).

### Added

- Minimal browser UI served at `/` (`src/threedprompt/static/index.html`) -
  generate a model from a prompt, or pick a local STL/OBJ file, increase
  its wall thickness, and download the result, all from a real file picker
  without needing curl or the Swagger docs.
- Initial service: `POST /generate` classifies a prompt (hybrid
  heuristic/LLM router) and generates a model via OpenSCAD (simple/
  parametric parts) or headless Blender (complex/organic shapes).
- `POST /models/{model_id}/thicken` and `POST /thicken` to increase wall
  thickness, preferring OpenSCAD-source regeneration and falling back to a
  Blender Solidify mesh shell.
- `GET /health` dependency status check; `GET /models/{model_id}/download`.
- LLM backend abstraction supporting local Ollama (default, no-cost) and the
  Claude API (opt-in, paid).
- Docker/Compose setup bundling OpenSCAD, Blender, and a local Ollama
  service.
- Test suite covering the classifier, both generation backends, both
  thickening strategies, storage, and the HTTP API (all CAD/LLM calls
  mocked).
