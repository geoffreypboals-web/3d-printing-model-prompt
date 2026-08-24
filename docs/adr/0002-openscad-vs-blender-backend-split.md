# 0002. OpenSCAD for simple parts, headless Blender for complex shapes

Date: 2026-08-24

## Status

Accepted

## Context

The service needs to turn a text prompt into a printable mesh. OpenSCAD's
CSG (constructive solid geometry) model is excellent for simple parametric
parts (booleans of cubes/cylinders/etc.) but has no real sculpting
capability. Blender can model anything but has no built-in notion of
"generate this from a text description" - it needs to be driven by scripted
`bpy` calls, most naturally authored by an LLM.

## Decision

Route simple/mechanical prompts to OpenSCAD, generating `.scad` source
either from a small library of deterministic built-in templates (bracket,
box, plate, spacer - no LLM call) or, for anything else, LLM-authored
OpenSCAD source rendered via the `openscad` CLI. Route complex/organic
prompts to headless Blender: the LLM authors a `build_scene()` function
using `bpy` primitives/modifiers, wrapped in a fixed boilerplate script that
clears the scene and exports STL, run via `blender --background --python`.

There is no single "Blender SDK" separate from `bpy` - `bpy` (used inside
Blender itself, which is what "headless Blender + Python script" means
here) and the standalone `bpy` PyPI package are the same API; we use the
former since it doesn't require Python-version-locked binary wheels and
matches Docker-first development (rule 4) cleanly.

## Consequences

- Simple parts get a clean, still-parametric result (useful directly for
  wall-thickness regeneration - see ADR 0003) at low/no LLM cost.
- Complex parts are entirely LLM-quality-dependent - there's no
  deterministic fallback, which is a genuine, documented limitation (see
  README "Known limitation") rather than a gap to silently paper over.
- Both backends shell out to external GPL-licensed binaries (OpenSCAD,
  Blender) as subprocesses rather than linking them - see the Licensing
  posture section of README.md for why that doesn't impose GPL obligations
  on this project's own code, and what it means for redistributing the
  Docker image.
