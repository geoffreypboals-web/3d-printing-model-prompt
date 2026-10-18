# 0015: Multi-colour 3MF export writes plain XML in one of two slicer layouts

Date: 2026-10-18

## Context

Plan item 5 (`docs/print-tools-plan.md`) asks for a 3MF where each part
prints with its own filament: the Bambu AMS, the Elegoo CANVAS on the
Centauri Carbon, and PrusaSlicer for completeness. The 3MF core spec has
no notion of "extruder"; each slicer family adds its own metadata file.
We read both loaders to see what each one actually does with a
multi-part object:

- OrcaSlicer's `src/libslic3r/Format/bbs_3mf.cpp` (Bambu Studio's loader,
  which Elegoo Slicer and Anycubic Slicer Next also use) reads
  `Metadata/model_settings.config` even from files other programs wrote.
  It builds one volume per `<component>` of an object and matches each
  `<part id>` to the component's object id; it ignores triangle ranges.
  Without that file it maps `m:colorgroup` colours to filaments.
- PrusaSlicer turns `<components>` into separate objects (checked by
  slicing with PrusaSlicer 2.7), so a raised part drops to the bed. Its own
  format is one mesh per object with `<volume firstid lastid>` triangle
  ranges in `Metadata/Slic3r_PE_model.config`.

No single file serves both for an assembled multi-part object.

## Decision

- `threemf.write_3mf(..., slicer="orca" | "prusa")` writes the layout
  each family reads; "orca" is the default because the owner's printers
  use Bambu Studio / Elegoo Slicer / Anycubic Slicer Next.
- Plain XML with the standard library; `lib3mf` (BSD-2-Clause) is a
  test-only dependency used to check the files are valid 3MF.
- Every part also gets an `m:colorgroup` colour for viewers and for
  Orca's colour-to-filament fallback.
- Separate (not assembled) objects carry the slot as object metadata in
  both config files, which both families read.

## Consequences

- The PrusaSlicer layout is verified by really slicing it in a test (run
  when `prusa-slicer` is installed; skipped in CI). The Orca layout is checked against
  the loader source only, until someone opens a file in Bambu Studio or
  Elegoo Slicer (owner to confirm).
- Per-face painted colours (Bambu `paint_color`) are not written; that is
  a later phase.
