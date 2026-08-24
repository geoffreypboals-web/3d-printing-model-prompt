# 0003. Two-strategy wall thickness increase: regenerate-from-source, then mesh-shell

Date: 2026-08-24

## Status

Accepted

## Context

"Increase the wall thickness of a loaded model" needs to work both for
models this service generated itself and for arbitrary STL/OBJ files a user
uploads that it knows nothing about. Those are different problems: a
parametric OpenSCAD part can just have its thickness variable bumped and be
re-rendered; an arbitrary mesh has no such variable and can only be
modified geometrically.

## Decision

Prefer regenerating from source when possible: if a model's `spec.json`
sidecar (`src/threedprompt/storage.py`) records `source_kind:
"openscad_scad"` and a `wall_thickness_param`, and that `.scad` file is
still present, bump the declared `wall_thickness` variable
(`src/threedprompt/thickness.py: regenerate_from_source`) and re-render via
`openscad`. Every OpenSCAD template and the LLM system prompt for
LLM-authored `.scad` both standardize on that exact variable name so this
path is reliably detectable.

For everything else - Blender-sourced models (no single "wall thickness"
concept in an LLM-authored `bpy` scene), OpenSCAD models with no recognized
thickness variable, or arbitrary user uploads - load the mesh into headless
Blender and apply a Solidify modifier by the requested amount
(`mesh_shell`). This works on any manifold-ish mesh regardless of origin
and needs no LLM call (the Blender script is a fixed, deterministic
template), keeping it free per rule 10.

Thickening always produces a new model rather than mutating the original,
so a bad result doesn't destroy the source.

## Consequences

- The clean, parametric path is used whenever it's available, giving better
  results (still fully manufacturable, no shelling artifacts) for the
  common case of "I generated this bracket, now make it sturdier."
- The mesh-shell path is a true fallback that works on anything, including
  files this service has never seen before - this is what makes
  `POST /thicken` (direct upload) possible at all.
- Neither path requires an LLM call, so thickening an existing model never
  incurs LLM cost - only initial complex-shape generation does.
