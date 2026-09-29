# 3D Printing Model Prompt

Turns a natural-language description of a part into a 3D-printable STL file,
can increase the wall thickness of an existing model, can check a model for
watertightness - finding surface gaps a slicer would choke on and telling
deliberate openings (a cup's mouth, an open box top) apart from unintentional
defects (a couple of misaligned vertices leaving a hole) - and can generate a
two-part silicone pour box plus a matching clamp shell for casting a model in
RTV silicone and then plaster of paris or cement.
Simple, mechanical parts (brackets, mounts, spacers, enclosures) are built
with **[OpenSCAD](https://openscad.org/)**; complex, organic, or
characterful shapes (creatures, figurines, freeform sculptures) are built
with **[Blender](https://www.blender.org/)** running headless, which also
powers watertight analysis/repair and remains the default/fallback for
wall-thickness shelling. **[FreeCAD](https://www.freecad.org/)** running
headless is a third backend, tried first for `.stl` wall-thickness
shelling (a real solid B-rep result via Part Thickness, not just a
thicker mesh) and for two capabilities Blender can't do at all: STEP
import/export and solid healing (OCCT's ShapeFix) - see
[STEP conversion & solid healing](#step-conversion--solid-healing) below
and `docs/adr/0012-freecad-third-cad-backend.md`. An HTTP API in front
decides which backend to use per request.

**Browser UI**: once the service is running, open `http://localhost:8000/`
in a browser for a simple page to generate a model from a prompt, pick a
local file to increase its wall thickness, or open the
[watertight checker](#watertight-check--repair) - a 3D viewer for spotting
and closing holes - no curl or API client needed. The full HTTP API (below)
is also available for scripting/automation, with interactive docs at
`/docs`.

## How routing works

1. A cheap, deterministic keyword heuristic looks at the prompt first
   (`src/threedprompt/classifier.py`) - words like "bracket" or "mount" route
   to OpenSCAD, words like "creature" or "dragon" route to Blender. This
   costs nothing and handles most requests.
2. If the heuristic isn't confident (prompt matches both vocabularies, or
   neither), the configured LLM is asked to classify it instead, so unusual
   prompts still get routed sensibly.
3. **OpenSCAD path**: a handful of built-in parametric templates (bracket,
   box/enclosure, plate, spacer/standoff) cover common parts with no LLM call
   at all. Anything else is authored as OpenSCAD source by the LLM and
   compiled to STL.
4. **Blender path**: always LLM-authored - the LLM writes a small `bpy`
   Python script that builds the shape from primitives and modifiers, which
   headless Blender then runs and exports to STL.

**Known limitation:** there's no deterministic fallback for organic shapes -
"a dwarf sitting under a mushroom" is genuinely hard to model well from a
short text prompt via generated `bpy` code, and result quality tracks the
LLM's capability. A local Ollama model will produce cruder geometry than a
larger hosted model; this is a real case where a paid model's quality helps
(see [AI/LLM backend](#aillm-backend) below) - that's a deliberate tradeoff to
make, not a bug to chase.

## Wall thickness

`POST /models/{model_id}/thicken` increases the wall thickness of a model
this service generated, in millimeters:

- If the model was built via OpenSCAD and its source declares a
  `wall_thickness` variable, that variable is bumped and the part is
  **re-rendered from source** - the cleanest result, still fully parametric,
  no LLM call.
- Otherwise, for `.stl` input, headless **FreeCAD's Part Thickness**
  operation is tried first, giving a real solid B-rep result rather than
  a thicker mesh - falling back automatically to Blender's **Solidify
  modifier** if FreeCAD is unavailable or the input isn't solid/manifold
  enough for Part Thickness. Every other mesh format goes straight to
  Blender. See `docs/adr/0012-freecad-third-cad-backend.md`.

`POST /thicken` does the same mesh-shelling for an arbitrary uploaded
STL, OBJ, or Rhino `.3dm` file that this service didn't generate - useful for any file you
already have. Unlike the other endpoints, `POST /thicken` returns the
thickened **STL file itself** directly (not JSON) - it's meant to be a
single round trip: load a file, modify it, get the modified file back. The
new model's id and the method used are still available afterwards as
response headers (`X-Model-Id`, `X-Thicken-Method`), e.g. if you want to
re-download the same result later via `GET /models/{model_id}/download`.

Either way, thickening produces a *new* model rather than overwriting the
original.

Optional `quad_target_faces` (default `0` = off, max 1,000,000; JSON field
or form field) retopologizes the shelled mesh into roughly that many
quad-dominant faces via Blender's QuadriFlow - a topology/cosmetic pass,
ignored on the regenerate-from-source path. Pick a target above the mesh's
natural face count; too low a target can leave it non-watertight even
after the repair pass that runs afterwards.

**Rhino `.3dm` input:** `.3dm` files are read inside headless Blender by a
vendored copy of the MIT-licensed
[import_3dm](https://github.com/jesterKing/import_3dm) converter
(`src/threedprompt/blender_scripts/vendor/rhino3dm_reader/`, see its
`NOTICE.md`), which needs the `rhino3dm` Python package installed into
the Python that Blender runs (the Docker image does this). Rhino units are
converted to millimeters. The browser UI's file pickers don't offer `.3dm`
yet - use the API (`/thicken`, `/mold`, `/watertight/upload`).

## Mold generation

`POST /models/{model_id}/mold` builds mold tooling around a model this
service generated (or thickened/repaired), split at the model's own
vertical midpoint. Four modes, chosen via `"mode"` (default
`"silicone_block"`):

**`silicone_block`** - a two-part silicone **pour box** (`pour_box_bottom.stl`
/ `pour_box_top.stl`) and a matching two-part rigid **clamp shell**
(`clamp_shell_bottom.stl` / `clamp_shell_top.stl`), 4 parts total:

1. Print the pour box halves. Assemble them around the printed model (the
   hemispherical registration keys near the parting line align the halves)
   and pour RTV silicone in through the top half's sprue hole, using the
   vent hole to let trapped air escape. Once cured and demolded, you have a
   two-piece flexible silicone mold shaped like the model.
2. Print the clamp shell halves. Reassemble the silicone mold's two pieces
   and clamp the rigid shell around them using bolts (or zip ties) through
   the flange holes - this is what keeps the flexible silicone from
   bulging or leaking under the pressure of the next pour.
3. Pour plaster of paris or cement into the clamp shell's own sprue hole,
   which lines up with the silicone mold's cavity underneath it. Once
   cured, unbolt the clamp shell and peel the silicone mold away.

**`direct_cast`** - a single rigid two-part mold (`direct_mold_bottom.stl`
/ `direct_mold_top.stl`, 2 parts) whose cavity is shaped like the model
itself - no silicone step. Bolt the two printed halves together through
the flange holes and pour resin, urethane, or foam straight into the
sprue hole. Only suitable for a model with no undercuts along the Z axis
(a rigid mold can't flex to release one the way silicone can), and v1
ships with no draft angle or release tolerance - see
`docs/adr/0006-direct-cast-mold-mode.md` for why those were deferred
rather than guessed at.

**`form_fitting`** - a thin, contour-following silicone **skin-pour tool**
(`skin_pour_bottom.stl` / `skin_pour_top.stl`) and a matching rigid
**support jacket** (`support_jacket_bottom.stl` / `support_jacket_top.stl`),
4 parts total - for organic/detailed models a rigid `direct_cast` mold
couldn't release along any single axis:

1. Print the skin-pour tool halves and assemble them around the printed
   model (registration keys align them, same as `silicone_block`'s pour
   box). Pour RTV silicone through the sprue hole - since the tool's
   cavity is the model's own surface grown outward by `shell_thickness_mm`,
   this casts a thin silicone shell that exactly hugs the model's contours.
2. Print the support jacket halves and bolt them around the finished
   flexible shell through the flange holes - same role as
   `silicone_block`'s clamp shell, holding the thin shell rigid.
3. Pour plaster of paris or cement into the jacket's sprue hole.

The offset is a per-vertex normal push, not a true Minkowski offset: flat
or smoothly-curved regions of the model grow by exactly
`shell_thickness_mm`, but sharp corners/edges come out thinner than
requested - see `docs/adr/0008-form-fitting-thin-shell-mold.md`. Fine for
the organic/rounded models this mode targets; not recommended for boxy
models with sharp corners (use `direct_cast` or `silicone_block` there
instead).

**`hollow_cast`** - the same rigid outer mold as `direct_cast`
(`hollow_cast_bottom.stl` / `hollow_cast_top.stl`) plus a separate solid
**core** (`hollow_cast_core.stl`), 3 parts total - for casting a hollow
vessel (a vase, a cup) as a genuine shell instead of a solid block:

1. Print the outer mold halves and the core.
2. Place the core inside the bottom half's cavity, then close the top
   half - the core is the model's own surface shrunk inward by
   `cast_wall_thickness_mm`, so a gap of exactly that thickness surrounds
   it on every side.
3. Bolt the halves together and pour resin, urethane, or plaster into the
   sprue hole - it fills only that gap, forming a hollow shell rather
   than a solid cast.

Shares `direct_cast`'s no-draft/no-release-tolerance limitations, plus
`form_fitting`'s offset-technique limitations (corner-rounding, concave
self-intersection risk) applied inward instead of outward - see
`docs/adr/0009-hollow-vessel-inner-core-mode.md`.

`POST /mold` does the same for an arbitrary uploaded mesh, returning the
resulting STL parts as one zip directly (one round trip, like
`POST /thicken`). All dimensions are in millimeters and every parameter has
a sensible default (see `MoldRequest` in `/docs`); the ones worth knowing
about:

- `clearance_mm` (default 8, `silicone_block` only) - gap between the
  model surface and the pour box's cavity wall, i.e. how thick the cast
  silicone will be.
- `pour_box_wall_mm` / `clamp_wall_mm` (default 4 / 6, `silicone_block`
  only) - rigid wall thickness of each part; the clamp shell is thicker by
  default since it resists real hydraulic pressure, not just holding a
  shape during a pour.
- `direct_mold_wall_mm` (default 6, `direct_cast` and `hollow_cast`) -
  rigid wall thickness of the outer mold.
- `shell_thickness_mm` (default 3, `form_fitting` only) - how far the
  model's surface is grown outward to form the cavity shape; this becomes
  the finished silicone shell's own thickness.
- `skin_pour_wall_mm` / `support_jacket_wall_mm` (default 3 / 5,
  `form_fitting` only) - rigid wall thickness of each part, same
  thinner-tool/thicker-jacket relationship as `silicone_block`.
- `cast_wall_thickness_mm` (default 4, `hollow_cast` only) - how far the
  core is shrunk inward from the model's own surface; this becomes the
  finished cast's own wall thickness.
- `clamp_flange_width_mm` / `bolt_hole_diameter_mm` (default 12 / 4.5,
  `silicone_block`'s clamp shell, `direct_cast`, `form_fitting`'s support
  jacket, and `hollow_cast`'s outer mold) - the bolted flange; 4.5mm is M4
  clearance.
- `sprue_diameter_mm` / `vent_diameter_mm` (default 10 / 4, all modes) -
  the pour hole and its air-vent hole through each top half's ceiling.
  The vent is placed over a real detected trapped-air pocket (the
  cavity's own highest local surface peak) when the model has one,
  falling back to a fixed offset from the cavity center for boxy models
  with no such peak - see
  `docs/adr/0011-geometry-aware-vent-placement.md`. Each hole diameter
  must be smaller than the cavity's own footprint - a hole comparable to
  (or larger than) the cavity itself would punch away the entire ceiling
  instead of leaving a working pour hole, and is rejected with a clear
  error rather than silently producing a broken mold; scale both down
  for small models.
- `parting_axis` (default `"z"`, all modes) - which model axis the two
  halves split along (`"x"`, `"y"`, or `"z"`). A non-`z` axis is
  implemented as a rotation baked into the model before building, so the
  exported parts come out in that rotated frame, not the upload's
  original orientation - see
  `docs/adr/0010-configurable-parting-axis-and-volume-reporting.md`.
- `parting_offset_mm` (default 0, all modes) - shifts the parting plane
  this far from the model's own bounding-box midpoint along
  `parting_axis` (positive moves it toward the max end); rejected if it
  would push the split outside the model's own range.
- `material_density_g_per_cm3` (optional, all modes) - when given,
  `estimated_cast_mass_g` (JSON) / `X-Estimated-Cast-Mass-G` (upload
  header) reports `cavity_volume_cm3 * material_density_g_per_cm3`.

Before generating any mode, the input mesh is automatically checked for
watertightness and repaired if needed: holes the existing watertight-check
heuristic confidently calls likely defects (not likely-intentional
openings) are closed automatically; anything left open fails the request
with a clear error instead of handing Blender's boolean solver broken
geometry (a rigid boolean cavity cut needs a genuinely closed mesh, and a
broken one otherwise fails opaquely deep inside the Blender subprocess).
Which hole ids (if any) were auto-repaired comes back as
`repaired_hole_ids` in the JSON response for `POST /models/{model_id}/mold`,
or the `X-Repaired-Hole-Ids` header for `POST /mold` - inspect them via
`POST /models/{model_id}/analyze` if you want to know what was found.

Every generation also reports the actual cast/pour material volume -
`cavity_volume_cm3` in the JSON response, or `X-Cavity-Volume-Cm3` for
`POST /mold` - and, if you pass `material_density_g_per_cm3`, an estimated
mass (`estimated_cast_mass_g` / `X-Estimated-Cast-Mass-G`). What "cavity"
means differs by mode - `silicone_block`'s pour-box cavity, `direct_cast`/
`hollow_cast`'s own model volume (minus the core's, for `hollow_cast`),
`form_fitting`'s thin shell gap - see
`docs/adr/0010-configurable-parting-axis-and-volume-reporting.md`.

`direct_cast` also runs an automatic, non-blocking draft-angle/undercut
check after generating (a rigid mold can't flex around an undercut the
way silicone can): `draft_check` in the JSON response, or the
`X-Draft-Releasable` / `X-Draft-Problem-Island-Count` headers for
`POST /mold`, tell you if anything was flagged without failing the
request. For full per-face detail (or to check a `silicone_block` model,
or try a different pull axis/threshold before committing to a full
generation), call `POST /models/{model_id}/mold/draft-check` directly:

```bash
curl -X POST http://localhost:8000/models/<model_id>/mold/draft-check \
  -H "Content-Type: application/json" \
  -d '{"pull_axis": "z", "min_draft_angle_deg": 2.0}'
```

It reports `releasable` and a `problem_islands[]` list (each a connected
group of faces, classified `"undercut"` - won't release at all - or
`"insufficient_draft"` - releases, but with less clearance than
requested), without modifying or generating anything. See
`docs/adr/0007-draft-undercut-analysis.md` for how draft angle is
measured and why each half's own pull direction is used rather than one
global direction.

See `docs/adr/0005-two-piece-silicone-mold-and-clamp-shell.md` for why the
pour box uses registration keys while the clamp shell uses a bolted flange
instead, and how the geometry was verified against a real Blender install.
See `docs/adr/0006-direct-cast-mold-mode.md` for direct_cast's own design
(model-mesh-as-cavity-tool, and why draft/tolerance/keys were skipped in
v1), `docs/adr/0007-draft-undercut-analysis.md` for the draft-angle
heuristic, `docs/adr/0008-form-fitting-thin-shell-mold.md` for
form_fitting's offset-along-normals technique and its corner-rounding
limitation, `docs/adr/0009-hollow-vessel-inner-core-mode.md` for how
hollow_cast reuses that same offset technique inward with no new boolean
step, `docs/adr/0010-configurable-parting-axis-and-volume-reporting.md`
for the parting-axis rotation trick, `parting_offset_mm`'s validation, the
per-mode casting-volume formulas, and a real `_add_pour_holes()` bug found
and fixed while testing it (an oversized sprue/vent hole could silently
punch away a mold's entire ceiling - now a rejected, clearly-named error),
and `docs/adr/0011-geometry-aware-vent-placement.md` for how a trapped-air
pocket is detected (and why a flat-topped box's corners don't falsely
count as one). See `docs/mold-production-research-and-plan.md` for the
research behind this feature (competitor tools, manual-technique guides)
and what's deliberately out of scope (custom branding, a batch/grid tool,
and any browser-UI work).

## Watertight check & repair

Finds boundary-edge holes (surface gaps) and inverted-normal ("wrinkle")
defects in a model - either one this service generated, or an arbitrary
upload - and classifies each hole as a likely **intentional opening** (a
cup's mouth, an open box top, an open base underside) or a likely
**unintentional defect** (a couple of misaligned/duplicate vertices
leaving a gap), with a confidence and plain-language reason for each.

Open `http://localhost:8000/watertight.html` for the 3D viewer: upload a
model, see every flagged hole as a color-coded clickable marker on the
actual mesh (red = likely defect, green = likely intentional, amber =
ambiguous), click a marker or its row in the side panel to pick which to
close, then repair and download. The underlying API:

1. `POST /watertight/upload` - upload a mesh (any of
   `.stl .obj .ply .glb .gltf .fbx .3dm`), get back a `model_id`.
2. `POST /models/{model_id}/analyze` - runs the check, returns
   `is_watertight`, every `holes[]` entry's classification/confidence/
   reason, any `flipped_normal_islands[]`, `self_intersection_count`
   (face pairs that cut through each other), `mesh_volume_cm3` (enclosed
   volume, 1 unit = 1 mm; `null` when the mesh isn't watertight), and a
   `viewer_glb_url` for the 3D preview.
3. `POST /models/{model_id}/repair` with `{"hole_ids": [...]}` - closes
   just those holes (leaving the rest open), re-checks watertightness,
   and returns a fresh `download_url`. **Hole ids are positional** - they
   renumber whenever the file changes, including after closing some of
   them, so always use ids from the most recent `/analyze` response.
   Or send `{"auto_repair": true}` instead of `hole_ids`: the model is
   re-analyzed and only holes classified *likely defect* are closed (plus a
   normals recalculation that fixes flipped regions); intentional and
   ambiguous openings stay open and come back in `skipped_hole_ids`. If
   there's nothing to fix, the file is left untouched and `changed` is
   `false`. 3dPrinterWorkshopManager uses this for its intake check.

A model generated via `POST /generate`, or thickened via
`/models/{model_id}/thicken`, can be checked the same way - just call
`/models/{model_id}/analyze` directly on its existing `model_id`, no
separate upload needed.

See `docs/adr/0004-watertight-hole-detection-and-repair.md` for how the
classification heuristic works and why it's a heuristic rather than an
LLM/ML call.

## STEP conversion & solid healing

Two FreeCAD-backed capabilities Blender can't provide at all:

1. **STEP import/export** - `POST /step/upload` (multipart, `.step`/`.stp`)
   converts an uploaded STEP solid into this service's mesh pipeline
   (returns a `model_id` usable with every other endpoint);
   `POST /models/{model_id}/export-step` converts a model's mesh back into
   a STEP solid and returns the file directly. This is a best-effort B-rep
   wrap of mesh triangles, not true reverse-engineered parametric CAD - a
   flat face becomes one real STEP planar face, but there's no
   curve/fillet recovery.
2. **Solid healing** - `POST /models/{model_id}/repair-solid` runs OCCT's
   ShapeFix to repair malformed B-rep topology (small gaps, invalid
   edges/faces), overwriting the model's STL in place. This is a
   **different class of repair** than `/repair` above: watertight repair
   closes genuine missing patches in a mesh (an open boundary loop);
   solid healing fixes a shape that's already "closed-looking" but
   geometrically malformed (e.g. after a rough mesh-to-solid conversion or
   a messy STEP import). Use `/repair` for a mesh with an actual hole,
   `/repair-solid` for a solid that fails validity checks despite looking
   closed.

Both require FreeCAD (`freecad_available` in `GET /health`) - unlike
Blender, FreeCAD isn't required for the service overall, so these two
endpoints return `503` if it's unavailable while everything else keeps
working. See `docs/adr/0012-freecad-third-cad-backend.md` for the real
semantic limitations found integrating FreeCAD (STEP import only works
via one specific API call; `Part.export()` on a bare shape silently omits
all geometry; Part Thickness needs an "opening" face, unlike Solidify).

## Thumbnails

`POST /thumbnail` renders a square PNG thumbnail of an uploaded mesh or
STEP file via headless Blender - an auto-framed orthographic camera and
flat Workbench-engine shading, fast enough to run per file on demand
rather than needing a pre-baked render pipeline. STEP/STP input is
converted to a mesh via FreeCAD first (Blender has no STEP importer).
`.3mf` isn't handled here on purpose - extract its embedded slicer-preview
PNG instead, which is cheaper and more accurate than a fresh render of a
re-triangulated mesh; `.amf` has no importer anywhere in this pipeline
either. Returns the PNG directly, one round trip, the same shape as
`POST /thicken`. Built primarily for a caller with no CAD tooling of its
own - see the farm-manager sibling repo's Library feature.

## AI tag suggestions

`POST /tags/suggest` suggests short descriptive tags for a library file
from its filename, designer name, and any known slicer metadata (filament
colors), via the configured LLM backend (Ollama-first, see "AI/LLM
backend" below) - text-only reasoning, not a vision model inspecting the
actual geometry, so treat suggestions as a rough starting point for human
review rather than authoritative. Never fails outright: if the LLM is
unreachable or returns something unparseable, it falls back to
filename-derived heuristic tags and reports `"method": "heuristic_fallback"`
in the response so a caller can tell the two apart. Built for the same
farm-manager sibling repo's Library feature as `/thumbnail` above.

## Voice answers (speech-to-text)

`POST /transcribe` (multipart `file`: a short `.webm`/`.ogg`/`.wav`/`.mp3`/
`.m4a` recording) returns `{"text": "..."}`. It runs **locally** with
[faster-whisper](https://github.com/SYSTRAN/faster-whisper) on CPU - no
cloud speech API. Built for the farm-manager sibling repo's AI Model
Generator interview, so answers can be spoken instead of typed.

- The Whisper model (`WHISPER_MODEL`, default `base.en`, ~140 MB) downloads
  on the first request into `WHISPER_MODEL_DIR` (default
  `<OUTPUT_DIR>/whisper-models`, i.e. inside the `threedprompt_output`
  volume under Docker) and stays loaded. First answer: ~10 s plus the
  download; later ones: 1-2 s.
- `WHISPER_MODEL=small.en` is more accurate but slower (~460 MB); drop the
  `.en` for other languages. `WHISPER_DEVICE`/`WHISPER_COMPUTE_TYPE`
  default to `cpu`/`int8`.
- Uploads over `MAX_AUDIO_UPLOAD_BYTES` (25 MB) get a 413; a missing
  dependency or failed model download gets a 503 with the reason.

## API

| Method | Path                          | Description                                                          |
|--------|-------------------------------|------------------------------------------------------------------------|
| GET    | `/health`                     | Reports OpenSCAD/Blender/FreeCAD/LLM availability                       |
| POST   | `/generate`                   | `{"prompt": "...", "wall_thickness_mm": 3.0}` -> JSON w/ model_id       |
| GET    | `/models/{model_id}/download` | Download the STL                                                        |
| POST   | `/models/{model_id}/thicken`  | `{"amount_mm": 1.5}` -> JSON w/ new model_id (see above)                 |
| POST   | `/thicken`                    | multipart upload (`file` .stl/.obj/.3dm, `amount_mm`, optional `quad_target_faces`) -> **the thickened STL file** |
| POST   | `/models/{model_id}/mold`     | Mold params incl. `mode` (all optional) -> JSON w/ new model_id, `download_url` for the zip |
| GET    | `/models/{model_id}/mold.zip` | Download the generated mold STL parts as one zip                        |
| POST   | `/mold`                       | multipart upload (`file` .stl/.obj/.3dm, mold params incl. `mode`) -> **the STL parts as one zip** |
| POST   | `/models/{model_id}/mold/draft-check` | `{"pull_axis": "z", "min_draft_angle_deg": 2.0}` -> JSON draft/undercut report, no files |
| POST   | `/watertight/upload`          | multipart upload (`file`) -> JSON w/ model_id                           |
| POST   | `/models/{model_id}/analyze`  | Watertight check -> JSON report (holes, classifications, viewer URL)    |
| POST   | `/models/{model_id}/repair`   | `{"hole_ids": [0, 2]}` or `{"auto_repair": true}` -> closes holes, re-checks, new download |
| GET    | `/models/{model_id}/viewer.glb` | Web-viewable GLB preview (produced by a prior analyze/repair call)   |
| POST   | `/step/upload`                | multipart upload (`file`, `.step`/`.stp`) -> JSON w/ model_id (mesh)    |
| POST   | `/models/{model_id}/export-step` | Converts the model's mesh to STEP -> **the STEP file**               |
| POST   | `/models/{model_id}/repair-solid` | Heals B-rep topology via FreeCAD ShapeFix -> JSON, overwrites in place |
| POST   | `/thumbnail`                  | multipart upload (`file`, `size`) -> **a PNG thumbnail**                |
| POST   | `/tags/suggest`               | `{"file_name": "...", ...}` -> JSON w/ suggested tags + method          |

Interactive docs are auto-generated by FastAPI at `/docs` once the service
is running - `/thicken` shows up there with a file-picker and an
`amount_mm` field; clicking "Execute" gives you a "Download file" link with
the result, since the response is the file itself.

### Example

```bash
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "a mounting bracket for a raspberry pi, 60mm", "wall_thickness_mm": 3}'
# => {"model_id": "...", "backend": "openscad", ...}

curl -o bracket.stl http://localhost:8000/models/<model_id>/download

curl -X POST http://localhost:8000/models/<model_id>/thicken \
  -H "Content-Type: application/json" -d '{"amount_mm": 1.5}'

# load a file you already have, increase its wall thickness, and save the
# result directly - one call, no separate download step:
curl -X POST http://localhost:8000/thicken \
  -F "file=@my_model.stl" -F "amount_mm=1.5" \
  -o my_model_thickened.stl

# same one-round-trip shape for mold generation - all params optional:
curl -X POST http://localhost:8000/mold \
  -F "file=@my_model.stl" -F "clearance_mm=8" \
  -o my_model_mold.zip

# direct-cast mode (no silicone step, 2 parts instead of 4):
curl -X POST http://localhost:8000/mold \
  -F "file=@my_model.stl" -F "mode=direct_cast" -F "direct_mold_wall_mm=6" \
  -o my_model_direct_mold.zip

# form-fitting mode (thin contour-hugging skin + support jacket, for
# organic models a rigid direct_cast mold couldn't release):
curl -X POST http://localhost:8000/mold \
  -F "file=@my_model.stl" -F "mode=form_fitting" -F "shell_thickness_mm=3" \
  -o my_model_form_fitting_mold.zip

# hollow-cast mode (rigid outer mold + a core, for casting a hollow
# vessel as a shell instead of a solid block):
curl -X POST http://localhost:8000/mold \
  -F "file=@my_model.stl" -F "mode=hollow_cast" -F "cast_wall_thickness_mm=4" \
  -o my_model_hollow_cast_mold.zip

# split along the model's own X axis instead of Z, offset 2mm toward the
# max end, and estimate the cast's mass at 1.2 g/cm3 - X-Cavity-Volume-Cm3
# and X-Estimated-Cast-Mass-G come back as response headers:
curl -X POST http://localhost:8000/mold \
  -F "file=@my_model.stl" -F "mode=direct_cast" \
  -F "parting_axis=x" -F "parting_offset_mm=2" -F "material_density_g_per_cm3=1.2" \
  -o my_model_direct_mold.zip
```

## Running with Docker (recommended)

The project is Docker-first (CLAUDE.md rule 4) - OpenSCAD, Blender, and
FreeCAD are installed inside the image, so there's nothing to set up on
the host besides Docker itself.

```bash
cd /home/user/3d-printing-model-prompt
cp .env.example .env
# in .env, set: OLLAMA_HOST=http://ollama:11434
docker compose up --build
# first run only, to pull the default local model:
docker compose exec ollama ollama pull llama3.1
```

The API is then available at `http://localhost:8000`. `docker-compose.yml`
also starts a local Ollama container so the no-cost default LLM backend
works with no extra setup.

**Already have Ollama running on your machine** (e.g. installed natively,
not in Docker)? Skip the bundled container instead of fighting a port
conflict on `11434`:

```bash
cd /home/user/3d-printing-model-prompt
cp .env.example .env
# in .env, set: OLLAMA_HOST=http://host.docker.internal:11434
docker compose up --build --no-deps app
```

`--no-deps` is required - without it, Compose starts the bundled `ollama`
service anyway (as the `app` service's dependency) and you'll hit the same
port conflict. `OLLAMA_HOST` is read entirely from `.env` (rule 22); nothing
in `docker-compose.yml` overrides it.

To build/run the app image alone (bring your own LLM backend):

```bash
docker build -t threedprompt .
docker run --rm -p 8000:8000 --env-file .env -v threedprompt_output:/app/output threedprompt
```

## Running locally without Docker

Requires OpenSCAD and Blender installed and on `PATH` (or point
`OPENSCAD_BINARY` / `BLENDER_BINARY` at their full paths). Watertight and
mold features also need `numpy` available to Blender's Python, and `.3dm`
input needs `rhino3dm` there too (e.g. `python3 -m pip install rhino3dm`
for the system Python a distro-packaged Blender uses). FreeCAD
(`freecadcmd`) is optional - without it, `.stl` thickening falls back to
Blender automatically and `/step/upload`, `/models/{id}/export-step`,
`/models/{id}/repair-solid` return 503; point `FREECAD_BINARY` at its
full path if it isn't on `PATH`.

```bash
cd /home/user/3d-printing-model-prompt
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pip install -e .
cp .env.example .env
uvicorn threedprompt.main:app --reload
```

Run the tests: `pytest` (all CAD/LLM calls are mocked, so this needs neither
OpenSCAD/Blender nor a running LLM; the `*_integration` tests are skipped
unless a real Blender is on `PATH`). Lint/format: `ruff check src tests` and
`black src tests`.

CI (`.github/workflows/ci.yml`) runs ruff, black, pytest and a Docker build
on every push/PR. The vendored `rhino3dm_reader` is excluded from ruff and
black in `pyproject.toml` (third-party code, not held to this repo's style).

## AI/LLM backend

Per CLAUDE.md rule 6, the LLM used for classification fallback and for
authoring OpenSCAD/`bpy` code is behind a small abstraction
(`src/threedprompt/llm_client.py`) selected entirely by `LLM_PROVIDER`:

| `LLM_PROVIDER` | Cost         | Notes                                             |
|-----------------|--------------|----------------------------------------------------|
| `ollama` (default) | free, local | Requires an Ollama server reachable at `OLLAMA_HOST` running `OLLAMA_MODEL` (default `llama3.1`). |
| `claude`         | paid API     | Requires `ANTHROPIC_API_KEY`. Generally produces noticeably better organic-shape (Blender) results - see the known limitation above. |

Switching backends is a single env var change; no call sites change.

## Licensing posture

This project is intended to be open source (CLAUDE.md rule 7), MIT licensed
(see `LICENSE`). Runtime dependencies:

| Dependency | License | Notes |
|---|---|---|
| FastAPI, Uvicorn, Pydantic, Requests | MIT/BSD | permissive |
| `anthropic` (Python SDK) | MIT | only used if `LLM_PROVIDER=claude` |
| `faster-whisper`, `ctranslate2`, `onnxruntime`, `tokenizers`, `huggingface-hub` | MIT / Apache-2.0 | `/transcribe`; Whisper model weights are MIT |
| `av` (PyAV) | BSD-3-Clause | audio decoding for `/transcribe`; its wheels bundle FFmpeg shared libraries (LGPL) - dynamically linked, keep FFmpeg's notice if redistributing the image |
| three.js r0.160.0 (vendored, `src/threedprompt/static/vendor/three/`) | MIT | watertight viewer; vendored not CDN-loaded, per rule 4 |
| import_3dm v0.0.18 reader (vendored, `src/threedprompt/blender_scripts/vendor/rhino3dm_reader/`) | MIT | `.3dm` import inside Blender; license + `NOTICE.md` kept alongside |
| `rhino3dm` (pip, installed into the image's system Python for Blender) | MIT | only needed for `.3dm` input |
| OpenSCAD | GPL-2.0 | invoked as an external CLI process (subprocess), not linked into this codebase - GPL applies to OpenSCAD itself, not to this project |
| Blender | GPL-3.0 | same: invoked as an external headless process, not linked in |
| FreeCAD | LGPL-2.1 | same: invoked as an external headless process (`freecadcmd`), not linked in - LGPL is weak copyleft and, unlike OpenSCAD/Blender's GPL, wouldn't impose source-disclosure obligations even if linked directly |

**Flagging per rule 7:** OpenSCAD and Blender are themselves GPL-licensed
(FreeCAD is LGPL-2.1, more permissive). This project only *shells out* to
their CLI/headless executables (no linking, no bundling of their source
into this codebase), which does not impose GPL obligations on this
project's own code - but if you redistribute the Docker image itself
(which bundles all three binaries), you're redistributing GPL/LGPL
software and should keep their license notices intact. Confirm this
arrangement is acceptable before distributing built images.

## Data privacy & backups

This service collects no personal data - only prompts and uploaded mesh
files, used solely to generate/modify models (rule 17). Generated models
live under `OUTPUT_DIR` (`./output` by default, a Docker volume in Compose)
as plain files with no database; there is no automated backup - see
`TROUBLESHOOTING.md` for the backup/restore note (rule 18).

## Project status

Early stage - not yet announced for outside contributions, so there's no
`CONTRIBUTING.md` yet (rule 19). See `CHANGELOG.md` for what's shipped and
`docs/adr/` for the reasoning behind the OpenSCAD/Blender split, the hybrid
classifier, the wall-thickness strategy, the watertight hole-detection/
repair feature, and the mold-generation feature. `GettingStarted.md` is the
short quick start; `Requirements.md` tracks what's built vs. open.
