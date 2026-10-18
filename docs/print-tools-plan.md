# Print tools plan (ideas from the 3DPrintCAD review)

Date: 2026-10-17. Status: items 1-2 built 2026-10-18 (`split.py`, overhang check); items 3-5 planned.

## Why

The owner asked for a review of [3DPrintCAD](https://3dprintcad.com/), a free
browser CAD tool for 3D printing. It has no AI, no API and no stated source
licence, so nothing can be integrated or copied from it. Several of its
features are still worth rebuilding here from scratch, because they fill gaps
in this service and in 3dPrinterWorkshopManager. This doc is the plan; the
matching entries are in `Backlog.md` under "Ready to implement".

What this service already does better (no action): prompt-to-model, hole
classification and repair, wall thickening, molds, STEP, the spiral wand.

## Ground rules for every item

- Same shape as the spiral wand feature: a module in `src/threedprompt/`,
  Pydantic request/response models in `models.py`, routes in `main.py`, a
  static page or a section of an existing page, tests that check real
  geometry, README + CHANGELOG + TROUBLESHOOTING updates.
- No LLM calls. These are rule-based and run in-process.
- Geometry with manifold3d + trimesh (ADR 0014); reuse `spiral_wand.py`
  helpers instead of copying them.
- Any new dependency must allow commercial use without caps (owner rule,
  2026-09-29) and goes in the README licensing table. Licences marked
  "verify" below must be checked before the dependency is added.
- Next free ADR number when an item needs one (0014 is taken by the spiral
  wand; the mold `cast_material` work in Backlog also needs a number).

## Order

| # | Item | Size | Why this order |
|---|---|---|---|
| 1 | Split a model to fit the bed, with connectors | M | Real day-to-day problem; reuses the wand's cutting and D-peg code |
| 2 | Overhang highlighting in the viewers | S | Small, client-side, helps every other feature |
| 3 | Tool insert from a photo | L | The standout feature; new image-processing step |
| 4 | Rule-based templates: Gridfinity bin, gears, lithophane, QR tag | M each | Cheap, no LLM; feed the products plan |
| 5 | Multi-colour 3MF export | M | Needs format research for Bambu/Orca colour metadata |

## 1. Split a model to fit the bed, with connectors

**Goal:** any model too big for a printer is cut into pieces that each fit,
with alignment connectors, and the Workshop Manager's fit check offers it.

- Module `split.py`: `split_to_fit(input_path, output_dir, bed_x, bed_y,
  bed_z, connector="dowel"|"dpeg"|"none", clearance_mm, max_pieces)`.
- Cut planes: try axis-aligned planes first (fewest pieces whose bounding
  boxes fit the bed in any of the 6 orientations, the same test the Workshop
  Manager's fit check uses). Prefer cuts through the thickest cross-section
  so connectors have material around them. Optional user-given plane.
- Connectors: for each cut face, place connectors inside the cut section
  (manifold `slice()` gives the polygon; pick points by inset distance).
  `dowel`: matching holes on both faces for a separate printed or bought pin
  (diameter options). `dpeg`: the wand's one-way D-peg on one side, socket
  on the other. Reuse `spiral_wand._d_peg` (move it to a shared
  `geometry.py`).
- API: `POST /models/{id}/split` (JSON) → parts with `z_offset`/transform for
  reassembly, zip, notes; viewer shows exploded and assembled views.
- Workshop Manager: when `/library/files/:id/fit` says "doesn't fit", show
  "Split to fit this printer" calling this endpoint with that printer's bed
  size (see that repo's Backlog).
- Tests: a 300 mm bar splits into 2 parts under a 250 mm bed; parts
  reassemble with zero overlap; connectors don't break out of thin walls
  (skip a connector and add a note instead); a model that fits is returned
  unchanged.

## 2. Overhang highlighting in the viewers

**Goal:** see unsupported areas before exporting.

- Client-side in the three.js viewers (`spiral-wand.html`,
  `watertight.html`, and the split viewer): colour faces whose normal points
  down by more than a threshold (default 45°, slider), ignoring faces on the
  bed. Toggle button.
- Optional API metric: `overhang_area_mm2` added to `/analyze`, computed with
  trimesh face normals.
- Tests: a JS-free unit test for the Python metric (a T shape has a known
  overhang area); a Playwright smoke test like the spiral wand's.

## 3. Tool insert from a photo

**Goal:** photo of tools on white paper with a calibration square → a tray
(Gridfinity or custom) with a pocket shaped like each tool.

- Module `tool_insert.py`, page `tool-insert.html`.
- Image pipeline (OpenCV, Apache-2.0): find the paper (largest bright
  quadrilateral) and correct perspective; find the calibration square
  (10/15/20/25 mm, user picks) for mm-per-pixel; threshold + contour the tool;
  simplify the outline. Fall back to "click two points a known distance
  apart" when the square isn't found.
- Outline editor in the page: drag points, add finger notches (circles
  unioned onto the outline). The edited outline is posted back as a polygon,
  so the server never has to trust its own detection.
- Geometry: outline offset by clearance (snug 0.5 / normal 1 / loose 2 mm),
  extruded as a pocket of chosen depth into a tray; tray sized to fit the
  outline plus margin, or to Gridfinity units (42 mm grid, 7 mm height units,
  standard base profile -- see item 4). Multiple tools per tray. Optional
  chamfered top edge.
- Tests: a synthetic photo (rendered rectangle + square on white) gives the
  right size within 1 %; pocket is clearance larger than the outline; tray is
  watertight and pockets don't break through the floor.

## 4. Rule-based templates

Each is a small module plus a section on a shared `templates.html` page.

- **Gridfinity bin:** grid units W×D×H, compartments, label tab, stacking lip,
  magnet holes. Follow the published Gridfinity spec (verify its licence
  before shipping; the spec is widely reused). Shared base with item 3.
- **Gears:** involute spur and helical gears plus a rack, written here (no
  dependency): module, tooth count, pressure angle, backlash, bore (round, D,
  hex). A "check mesh" report for a gear pair (centre distance, contact
  ratio). Bevel, worm and planetary later.
- **Lithophane:** image → height map (Pillow; thicker = darker) as a flat,
  curved or cylinder panel, with frame and light-hole options.
- **QR tag:** text/URL/Wi-Fi → raised QR on a plate (segno, BSD-3), with a
  border, optional logo cut-out and NFC sticker pocket; checked by decoding
  the rendered top view.

Tests per template: watertight output, key dimensions match the request,
gear pairs have the right centre distance, the QR decodes.

## 5. Multi-colour 3MF export

**Goal:** export a model as a 3MF where each part or painted region uses its
own filament, for the AMS (Bambu) and the CANVAS (Centauri Carbon).

- Start with per-part colours (each STL part = one object with an
  extruder/colour), which covers riders, lithophane frames, QR tags (raised
  code in a second colour) and Gridfinity labels.
- Research first: what Bambu Studio, OrcaSlicer and Elegoo's slicer read for
  per-object filament (3MF core materials vs. their `Metadata/*.config`
  extensions). Write with lib3mf (BSD-2, verify) or plain XML.
- Painted regions (per-face colour) are a later phase.
- Tests: the 3MF opens in lib3mf, has one object per part with the right
  colour, and round-trips the geometry.

## Out of scope

- A general sketch/constraint CAD editor like 3DPrintCAD's: large, and the
  prompt-to-CAD path covers it differently.
- Mesh sculpting.
- Copying anything from 3DPrintCAD: its code is unlicensed for reuse; only
  the feature ideas are used.
