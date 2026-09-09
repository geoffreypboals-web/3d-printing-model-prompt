# Vendored from import_3dm

`read3dm.py` and `converters/` are taken from
[jesterKing/import_3dm](https://github.com/jesterKing/import_3dm) v0.0.18
(MIT license, Nathan Letwory and contributors), stripped down to just the
`.3dm` -> Blender mesh conversion logic.

**What's removed:** the addon's own `__init__.py` (Blender operator/UI
registration, `bl_info`, drag-and-drop handlers). That file imports
`bpy_extras.io_utils.poll_file_object_drop`, which doesn't exist before
Blender 4.2 -- confirmed live, this whole package is built as a Blender
4.2+ "Extension" (see its `blender_manifest.toml`, `blender_version_min =
"4.2.0"`), not a classic addon. This repo doesn't need the addon
registration at all -- it only calls `read3dm.read_3dm(context, filepath,
options)` directly from a headless `--python` script, so the incompatible
file was simply never copied. This directory's own `__init__.py` is a
blank placeholder, only present so `from . import converters` inside
`read3dm.py` resolves.

**Known Blender-4.1+-only call, unpatched**: `converters/render_mesh.py`'s
`Mesh.from_pydata(..., shade_flat=False)` doesn't exist before Blender 4.1.
This repo's Dockerfile (`FROM python:3.11-slim`, Debian trixie) installs
Blender 4.3.2 via apt, so this is fine today -- but if that base image
ever resolves to a pre-4.1 Blender, this call site breaks. Confirmed live
against Blender 3.4.1 (a sibling project's Debian-bookworm-based image)
while building this same vendoring for that repo.

**Also confirmed live in this repo's own container**: the imported
geometry lands in Blender's scene units (meters, per read_3dm()'s own
Rhino-units -> Blender-scene-units conversion), while this pipeline
otherwise treats 1 Blender unit as 1mm throughout -- callers must scale
imported objects by 1000x after read_3dm() returns, or a real ~40mm part
silently becomes a ~0.04-unit mesh and gets destroyed by any mm-scaled
operation (remove_doubles, Solidify thickness, etc.) applied afterward.

**Dependency:** `rhino3dm` (BSD/MIT, McNeel) -- installed via pip in this
repo's Dockerfile, not vendored here (it ships prebuilt wheels per-
platform, unlike this pure-Python conversion logic).
