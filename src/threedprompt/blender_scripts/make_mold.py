"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/blender_scripts/make_mold.py
Description: Runs *inside* Blender (`blender --background --python
    make_mold.py -- --input <mesh> --output-dir <dir> --report-output
    <json> --mode <mode> <mold params...>`). Builds one of two mold
    systems around an imported model, purely from primitive boxes/
    cylinders/spheres combined with boolean modifiers - no bisecting a
    sealed box in half (see build_half()'s docstring for why that first
    approach was abandoned):

      --mode silicone_block (default): a two-part silicone pour box (you
        insert the *physical printed model* and pour RTV silicone around
        it) plus a matching two-part rigid clamp shell (for pouring
        plaster/cement into the resulting silicone mold under pressure -
        see docs/adr/0005-two-piece-silicone-mold-and-clamp-shell.md).
        4 output files.
      --mode direct_cast: a single two-part rigid mold whose cavity is
        the model's *own mesh geometry* (not a rectangular void - there's
        no physical model to insert here, the mold cavity has to already
        be shaped like the part), for casting resin/urethane/foam
        directly with no silicone step at all. Bolted flange (reused from
        the clamp shell), no registration keys, no separate clamp shell -
        see docs/adr/0006-direct-cast-mold-mode.md. 2 output files. Only
        suitable for models with no undercuts along the Z axis (a rigid
        mold can't flex to release one the way silicone does) - this
        script does not check for that (see
        docs/mold-production-research-and-plan.md's FR-4/Phase 3).
      --mode form_fitting: a thin, contour-hugging silicone shell mode
        for large/organic shapes where a full block of silicone
        (silicone_block) would waste material - two tool *pairs*, both
        sharing the same cavity shape (the model's surface pushed
        outward by --shell-thickness-mm, not a box): a skin pour tool
        (registration keys, cast the thin flexible shell around the
        model through it) and a support jacket (bolted flange, holds
        that finished shell rigid for the final pour into its now-empty
        interior - functionally clamp_shell's role, just around a
        contour-following cavity instead of a box one). 4 output files.
        See docs/adr/0008-form-fitting-thin-shell-mold.md.
      --mode hollow_cast: the same rigid two-part outer mold as
        direct_cast (cavity = the model's own mesh) plus a separate core
        - an inward-offset duplicate of the model shrunk by
        --cast-wall-thickness-mm - that the caller seats inside the
        bottom half's cavity before closing the top half and pouring, so
        the cast forms a hollow shell (a vessel wall) rather than a solid
        block. No boolean cut against the core: it's just a smaller
        object nested inside the same model-shaped cavity, naturally
        centered by the resulting shell-thickness gap on every side. 3
        output files. See docs/adr/0009-hollow-vessel-inner-core-mode.md.
Inputs: CLI args after a literal `--`:
    --input <path>                mesh file: .stl/.obj/.ply/.glb/.gltf/.fbx/.3dm
    --output-dir <dir>            where the STL(s) are written
    --report-output <path>        where to write the JSON report
    --mode <silicone_block|direct_cast|form_fitting|hollow_cast>
    --clearance-mm <float>        (silicone_block) gap between model surface and pour-box cavity wall
    --pour-box-wall-mm <float>    (silicone_block) pour box wall thickness
    --key-diameter-mm <float>     (silicone_block, form_fitting) registration key (hemisphere) diameter
    --sprue-diameter-mm <float>   pour-hole diameter through each top half's ceiling
    --vent-diameter-mm <float>    vent-hole diameter through each top half's ceiling
    --clamp-wall-mm <float>       (silicone_block) clamp shell wall thickness
    --clamp-flange-width-mm <float>  width of the bolted flange (direct_cast, form_fitting, hollow_cast)
    --bolt-hole-diameter-mm <float>  flange bolt-hole diameter (direct_cast, form_fitting, hollow_cast)
    --direct-mold-wall-mm <float> (direct_cast, hollow_cast) rigid outer mold wall thickness
    --shell-thickness-mm <float>  (form_fitting) thin silicone shell thickness (the offset distance)
    --skin-pour-wall-mm <float>   (form_fitting) skin pour tool's own rigid wall thickness
    --support-jacket-wall-mm <float>  (form_fitting) support jacket's own rigid wall thickness
    --cast-wall-thickness-mm <float>  (hollow_cast) how far the core is shrunk inward from the model surface
    --parting-axis <x|y|z>        (FR-6, default z) which model axis the two halves split along
    --parting-offset-mm <float>   (FR-6) shift the parting plane this far from the model's own bbox
                                   midpoint along parting_axis; positive moves it toward the max end
    --max-dimension-mm <float>    reject if any resulting part's largest side exceeds this
Outputs: silicone_block: pour_box_bottom.stl, pour_box_top.stl,
    clamp_shell_bottom.stl, clamp_shell_top.stl. direct_cast:
    direct_mold_bottom.stl, direct_mold_top.stl. form_fitting:
    skin_pour_bottom.stl, skin_pour_top.stl, support_jacket_bottom.stl,
    support_jacket_top.stl. hollow_cast: hollow_cast_bottom.stl,
    hollow_cast_top.stl, hollow_cast_core.stl. Either way, under
    --output-dir, plus a JSON report at --report-output:
    {"paths": {...}, "cavity_volume_mm3": float} on success (FR-8 - the
    actual cast/pour material volume, meaning differs slightly per mode,
    see each _build_*_mold() docstring) or {"error": "..."} on failure
    (also exits non-zero on failure, matching close_holes.py).
Troubleshooting:
    - "resulting mold would be ...mm": lower clearance/wall/flange
      params or raise --max-dimension-mm - see mold.py's docstring for
      why this cap exists.
    - A key bump doesn't protrude/socket doesn't recess: key radius is
      clamped to 40% of pour_box_wall_mm/skin_pour_wall_mm so it never
      pokes through the cavity or outer wall - a very thin wall silently
      shrinks the key.
    - primitive_cube_add(size=1) spans -0.5..0.5 (edge length 1) -
      scaling it by size/2 (as if it spanned -1..1) silently builds every
      box at half the intended dimensions. Confirmed live in
      .scratch_moldtest/debug_mold.py by comparing a built shell's own
      bounding box against the requested one - _new_box's `obj.scale =
      (size[0], size[1], size[2])` line is deliberately the full size,
      not size/2.
    - direct_cast mode's cast part will fit *exactly* to the model's own
      dimensions (no release tolerance/offset is applied yet) - a tight
      FDM/resin print may need mold release or light sanding to separate
      cleanly. See docs/mold-production-research-and-plan.md for why this
      was deferred rather than guessed at.
    - form_fitting's offset surface (_offset_model_along_normals) can
      self-intersect on strongly concave regions (pushing every vertex
      outward independently along its own normal has no awareness of
      nearby geometry it might collide with) - a known v1 limitation, see
      docs/adr/0008-form-fitting-thin-shell-mold.md. Inspect the exported
      skin_pour_*.stl files for obviously broken/pinched geometry near
      any sharp concave fold before trusting them on a highly organic
      model.
    - hollow_cast's core (same _offset_model_along_normals helper, an
      inward offset this time) has the identical corner-rounding and
      concave-self-intersection risks as form_fitting's outward offset -
      see docs/adr/0009-hollow-vessel-inner-core-mode.md. Also has no
      registration/support geometry of its own in v1: it simply rests
      inside the bottom half's cavity by hand before the top half closes.
    - --parting-axis x/y rotates the model so that axis becomes local Z
      before building (see _rotate_axis_to_z()) and does NOT rotate the
      exported parts back afterward - every exported STL for a non-Z
      parting axis is in that rotated frame, not the original upload's
      orientation. This is deliberate, not a bug: see
      docs/adr/0010-configurable-parting-axis-and-volume-reporting.md.
    - --parting-offset-mm too large (parting plane would land at or past
      one of the model's own bbox extremes) raises a clear error rather
      than building a degenerate zero/negative-height half.
    - "sprue_diameter_mm/vent_diameter_mm ... must be smaller than the
      cavity's own footprint": a hole diameter comparable to (or larger
      than) the cavity's own XY size would punch away the entire ceiling
      plate instead of leaving a working hole once cut - scale both
      params down for a small model, or use a larger model.
    - The vent hole isn't where you expected: it's placed over the
      cavity's own highest local surface peak (a real trapped-air
      pocket) when one exists, not always at a fixed offset from the
      cavity center - see docs/adr/0011-geometry-aware-vent-placement.md.
      A plain box (or any model with no such peak) still gets the old
      fixed 0.35*cavity_size offset.
    - See docs/adr/0005-two-piece-silicone-mold-and-clamp-shell.md for
      why each half is built as its own open tray directly (not a
      bisected sealed box), and why the pour box uses keys while the
      clamp shell uses a bolted flange instead; docs/adr/0006-direct-cast-mold-mode.md
      for the direct_cast mode's own decisions; docs/adr/0008-form-fitting-thin-shell-mold.md
      for form_fitting's offset-surface technique and its own decisions;
      docs/adr/0009-hollow-vessel-inner-core-mode.md for hollow_cast's
      reuse of that same offset technique in the inward direction;
      docs/adr/0010-configurable-parting-axis-and-volume-reporting.md for
      the axis-rotation trick and the volume calculator's per-mode
      "cavity volume" definitions; docs/adr/0011-geometry-aware-vent-placement.md
      for the trapped-air-pocket detection and its fixed-offset fallback.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _shared  # noqa: E402


def _parse_args():
    """Parse this script's CLI args from the portion of sys.argv after Blender's own `--`."""
    argv = sys.argv
    argv = argv[argv.index("--") + 1 :] if "--" in argv else []
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--report-output", required=True)
    parser.add_argument(
        "--mode",
        choices=["silicone_block", "direct_cast", "form_fitting", "hollow_cast"],
        default="silicone_block",
    )
    parser.add_argument("--direct-mold-wall-mm", type=float, required=True)
    parser.add_argument("--shell-thickness-mm", type=float, required=True)
    parser.add_argument("--skin-pour-wall-mm", type=float, required=True)
    parser.add_argument("--support-jacket-wall-mm", type=float, required=True)
    parser.add_argument("--cast-wall-thickness-mm", type=float, required=True)
    parser.add_argument("--parting-axis", choices=["x", "y", "z"], default="z")
    parser.add_argument("--parting-offset-mm", type=float, required=True)
    parser.add_argument("--clearance-mm", type=float, required=True)
    parser.add_argument("--pour-box-wall-mm", type=float, required=True)
    parser.add_argument("--key-diameter-mm", type=float, required=True)
    parser.add_argument("--sprue-diameter-mm", type=float, required=True)
    parser.add_argument("--vent-diameter-mm", type=float, required=True)
    parser.add_argument("--clamp-wall-mm", type=float, required=True)
    parser.add_argument("--clamp-flange-width-mm", type=float, required=True)
    parser.add_argument("--bolt-hole-diameter-mm", type=float, required=True)
    parser.add_argument("--max-dimension-mm", type=float, required=True)
    return parser.parse_args(argv)


def _new_box_from_range(bpy, name, xy_size, x_center, y_center, z_min, z_max):
    """Create an axis-aligned box spanning [z_min, z_max] in Z, xy_size in X/Y, centered at (x_center, y_center)."""
    size = (xy_size[0], xy_size[1], z_max - z_min)
    center = (x_center, y_center, (z_min + z_max) / 2)
    bpy.ops.mesh.primitive_cube_add(size=1, location=center)
    obj = bpy.context.active_object
    obj.name = name
    # primitive_cube_add(size=1) spans -0.5..0.5 (edge length 1), so the
    # scale factor to reach a desired edge length IS that length, not half
    # of it - see the module docstring's troubleshooting note.
    obj.scale = size
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    return obj


def _new_vertical_cylinder(bpy, name, radius, depth, center):
    """Create a Z-axis cylinder of given radius/depth centered at center, transform-applied, and return it."""
    bpy.ops.mesh.primitive_cylinder_add(radius=radius, depth=depth, location=center)
    obj = bpy.context.active_object
    obj.name = name
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    return obj


def _new_sphere(bpy, name, radius, center):
    """Create a UV sphere of given radius centered at center, transform-applied, and return it."""
    bpy.ops.mesh.primitive_uv_sphere_add(radius=radius, location=center, segments=24, ring_count=12)
    obj = bpy.context.active_object
    obj.name = name
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    return obj


def _boolean(bpy, obj, other, operation):
    """Apply a boolean modifier (EXACT solver) to obj using other as the operand, delete other, return obj."""
    bpy.context.view_layer.objects.active = obj
    modifier = obj.modifiers.new(name="bool", type="BOOLEAN")
    modifier.operation = operation
    modifier.object = other
    modifier.solver = "EXACT"
    bpy.ops.object.modifier_apply(modifier=modifier.name)
    bpy.data.objects.remove(other, do_unlink=True)
    return obj


def _corner_offsets(half_x, half_y, inset):
    """The 4 (x, y) corner offsets of a half_x*2 by half_y*2 rectangle, inset from its edge by inset."""
    ix, iy = half_x - inset, half_y - inset
    return [(sx * ix, sy * iy) for sx in (-1, 1) for sy in (-1, 1)]


def build_half(bpy, name, outer_xy, cavity_xy, cx, cy, parting_z, half_extent, wall, sign):
    """
    Build one open-tray half of a hollow box directly (bottom half if
    sign < 0, top half if sign > 0): solid from the parting line out to
    half_extent further away, with the cavity cut all the way through
    both the tray's far (closed) end *and* out past the parting-line end
    - the cavity-cutting box deliberately overshoots past the parting
    line by `wall + 5mm` so the boolean difference exits clean through
    that face, leaving it genuinely open there (an ordinary "open box
    top", the same shape watertight.py already treats as an intentional
    opening) rather than needing any manual fill.

    This replaced an earlier approach that built one *sealed* hollow box
    spanning the full height and then bisected it in two: bisecting a
    hollow box's cross-section is an annulus (an outer wall loop plus a
    disjoint, smaller cavity loop), and every fill technique tried
    (holes_fill, triangle_fill, bridge_loops) either capped the cavity
    shut or left ambiguous results - confirmed live via
    .scratch_moldtest/debug_mold.py against a real Blender install, not
    just reasoned about. Building each half as its own tray from the
    start sidesteps the whole problem: there's never a cavity loop to
    accidentally cap, because the cavity is open by construction.
    """
    overshoot = wall + 5.0
    if sign < 0:
        outer = _new_box_from_range(bpy, f"{name}_outer", outer_xy, cx, cy, parting_z - half_extent - wall, parting_z)
        cavity = _new_box_from_range(
            bpy, f"{name}_cavity", cavity_xy, cx, cy, parting_z - half_extent, parting_z + overshoot
        )
    else:
        outer = _new_box_from_range(bpy, f"{name}_outer", outer_xy, cx, cy, parting_z, parting_z + half_extent + wall)
        cavity = _new_box_from_range(
            bpy, f"{name}_cavity", cavity_xy, cx, cy, parting_z - overshoot, parting_z + half_extent
        )
    return _boolean(bpy, outer, cavity, "DIFFERENCE")


def _duplicate_object(bpy, obj, name):
    """Duplicate obj's mesh data into a new, independent linked object (boolean tools consume/delete their operand)."""
    dup = obj.copy()
    dup.data = obj.data.copy()
    dup.name = name
    bpy.context.collection.objects.link(dup)
    return dup


def build_offset_cavity_half(bpy, cavity_source_obj, name, outer_xy, cx, cy, parting_z, half_extent, wall, sign):
    """
    Build one half of a rigid tool whose cavity is cavity_source_obj's
    own mesh shape - not a synthetic box - subtracted out directly: an
    outer box from the parting line out to half_extent + wall beyond it,
    minus a duplicate of cavity_source_obj. Unlike build_half()'s
    box-shaped cavity, cavity_source_obj needs no manual overshoot past
    the parting line to guarantee an open cut there: it already spans
    across parting_z by construction (parting_z is its own bounding-box
    midpoint), so subtracting it always cuts a genuine through-hole at
    that face.

    Shared by two modes with otherwise-unrelated cavity sources: direct_cast
    passes the imported model object directly (see
    docs/adr/0006-direct-cast-mold-mode.md), form_fitting passes a
    duplicate that's already been pushed outward along its own normals
    by the shell thickness (see
    docs/adr/0008-form-fitting-thin-shell-mold.md) - this function itself
    doesn't care which, it just needs a closed, non-hollow solid to
    subtract.
    """
    if sign < 0:
        outer = _new_box_from_range(bpy, f"{name}_outer", outer_xy, cx, cy, parting_z - half_extent - wall, parting_z)
    else:
        outer = _new_box_from_range(bpy, f"{name}_outer", outer_xy, cx, cy, parting_z, parting_z + half_extent + wall)
    cavity_dupe = _duplicate_object(bpy, cavity_source_obj, f"{name}_cavity")
    return _boolean(bpy, outer, cavity_dupe, "DIFFERENCE")


def _offset_model_along_normals(bpy, model_obj, name, offset_mm):
    """
    Duplicate model_obj and push every vertex along its own
    (recalculated) vertex normal by offset_mm - a direct "grow/shrink the
    surface" operation. A positive offset_mm grows the surface outward:
    form_fitting mode's cavity shape (the thin silicone shell's own outer
    surface, see docs/adr/0008-form-fitting-thin-shell-mold.md). A
    negative offset_mm shrinks it inward: hollow_cast mode's core (see
    docs/adr/0009-hollow-vessel-inner-core-mode.md). Deliberately not a
    Solidify modifier pass, which produces a hollow two-surface shell
    that would need a second, riskier boolean union (against near-
    coincident geometry) to fill before it's usable as a single solid -
    see ADR 0008 for the full reasoning and this technique's own known
    limitation (self-intersection risk on strongly concave/convex
    regions, the same category of deferred concern as direct_cast's
    release tolerance).
    """
    import bmesh

    dup = _duplicate_object(bpy, model_obj, name)
    bm = bmesh.new()
    bm.from_mesh(dup.data)
    bm.verts.ensure_lookup_table()
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.normal_update()
    for v in bm.verts:
        v.co += v.normal * offset_mm
    bm.to_mesh(dup.data)
    bm.free()
    dup.data.update()
    return dup


def _object_volume_mm3(obj) -> float:
    """
    Compute a mesh object's own enclosed volume (mm3) via bmesh, without
    modifying it - FR-8's casting-volume calculator reads this off
    whichever object is each mode's cavity-cutting geometry before it's
    consumed by a boolean or deleted.
    """
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    volume = abs(bm.calc_volume(signed=True))
    bm.free()
    return volume


def _add_registration_keys(bpy, bottom, top, cavity_size, cavity_center, parting_z, key_diameter_mm, wall_mm):
    """
    Add interlocking registration keys at the 4 corners of the cavity's
    footprint: a hemispherical bump unioned into bottom (protrudes above
    its flat parting-line rim) and a slightly larger matching socket cut
    into top (recessed into its flat parting-line rim) at the same XY
    position, so the finished silicone halves nest together the same
    way. Radius is clamped to 40% of wall_mm so it stays embedded in the
    wall material rather than poking into the cavity or outside the
    outer wall. Takes parting_z explicitly (not derived from
    cavity_center[2]) since FR-6's parting_offset_mm can move the actual
    parting line away from the cavity's own geometric Z center - keys
    must sit at the real seam for the two halves to nest.
    """
    radius = min(key_diameter_mm / 2, 0.4 * wall_mm)
    z = parting_z
    half_x = cavity_size[0] / 2 + wall_mm / 2
    half_y = cavity_size[1] / 2 + wall_mm / 2
    for kx, ky in _corner_offsets(half_x, half_y, 0.0):
        bump = _new_sphere(bpy, "key_bump", radius, (cavity_center[0] + kx, cavity_center[1] + ky, z))
        bottom = _boolean(bpy, bottom, bump, "UNION")
        socket = _new_sphere(bpy, "key_socket", radius + 0.3, (cavity_center[0] + kx, cavity_center[1] + ky, z))
        top = _boolean(bpy, top, socket, "DIFFERENCE")
    return bottom, top


def _find_vent_xy(bpy, cavity_obj, cavity_center, cavity_size, exclude_radius_mm):
    """
    FR-7: pick a vent location over a real trapped-air pocket instead of
    a fixed offset from the cavity center. A pocket is a strict local
    Z-maximum of the cavity's own *roof* surface - higher than every
    neighbor reached by an edge that actually moves across the surface
    (some horizontal displacement), and strictly higher than at least
    one such neighbor. Purely vertical edges (a box's side walls) are
    excluded from the neighbor set entirely - without that filter, every
    top corner of a plain box looks like a "local max" purely because its
    downward wall edge is lower, even though the top face itself is
    perfectly flat and traps nothing. Points within exclude_radius_mm of
    the cavity center are skipped, since the sprue already vents that
    area. Falls back to the old fixed 0.35*cavity_size offset when
    cavity_obj is None (silicone_block's cavity is a synthetic box - no
    surface to analyze) or no genuine peak survives the filter (e.g. a
    plain box's flat top), so already-verified simple-shape placement is
    unchanged. Picks the single highest surviving peak - see
    docs/adr/0011-geometry-aware-vent-placement.md for why multi-vent
    support was scoped out.
    """
    fallback = (cavity_center[0] + 0.35 * cavity_size[0], cavity_center[1] + 0.35 * cavity_size[1])
    if cavity_obj is None:
        return fallback

    import bmesh

    bm = bmesh.new()
    bm.from_mesh(cavity_obj.data)
    best = None
    for v in bm.verts:
        roof_neighbor_zs = []
        for e in v.link_edges:
            n = e.other_vert(v)
            if ((n.co.x - v.co.x) ** 2 + (n.co.y - v.co.y) ** 2) ** 0.5 > 1e-6:
                roof_neighbor_zs.append(n.co.z)
        if not roof_neighbor_zs:
            continue
        if not (all(v.co.z >= nz for nz in roof_neighbor_zs) and any(v.co.z > nz for nz in roof_neighbor_zs)):
            continue
        dx, dy = v.co.x - cavity_center[0], v.co.y - cavity_center[1]
        if (dx * dx + dy * dy) ** 0.5 < exclude_radius_mm:
            continue
        if best is None or v.co.z > best[2]:
            best = (v.co.x, v.co.y, v.co.z)
    bm.free()
    return (best[0], best[1]) if best is not None else fallback


def _add_pour_holes(bpy, top, cavity_center, cavity_size, vent_xy, ceiling_z, sprue_diameter_mm, vent_diameter_mm):
    """
    Cut a sprue (pour) hole centered over the cavity and a vent hole at
    vent_xy (see _find_vent_xy) through top's ceiling. A hole diameter
    close to (or larger than) the cavity's own XY footprint doesn't
    leave a "hole" at all - the cutting cylinder's circular cross-
    section then covers the whole ceiling plate, and the DIFFERENCE
    removes it entirely instead of punching through it, silently
    producing an unusable mold with no ceiling. Rejecting that up front
    (rather than a mode-specific fix) covers all four modes, since they
    all funnel through here.
    """
    max_hole_diameter = min(cavity_size[0], cavity_size[1])
    if sprue_diameter_mm >= max_hole_diameter or vent_diameter_mm >= max_hole_diameter:
        raise ValueError(
            f"sprue_diameter_mm/vent_diameter_mm ({sprue_diameter_mm}/{vent_diameter_mm}mm) must be smaller "
            f"than the cavity's own footprint ({cavity_size[0]:.2f}x{cavity_size[1]:.2f}mm) -- a hole this "
            "large relative to the model would punch away the mold's entire ceiling instead of leaving a "
            "working pour hole"
        )

    span = ceiling_z - cavity_center[2]
    cyl_z = cavity_center[2] + span / 2
    cyl_depth = span + 10.0

    sprue = _new_vertical_cylinder(
        bpy, "sprue", sprue_diameter_mm / 2, cyl_depth, (cavity_center[0], cavity_center[1], cyl_z)
    )
    top = _boolean(bpy, top, sprue, "DIFFERENCE")

    vx, vy = vent_xy
    vent = _new_vertical_cylinder(bpy, "vent", vent_diameter_mm / 2, cyl_depth, (vx, vy, cyl_z))
    top = _boolean(bpy, top, vent, "DIFFERENCE")
    return top


def _add_flange_half(
    bpy, half_obj, outer_xy, cx, cy, parting_z, flange_width_mm, flange_half_thickness_mm, sign, bolt_hole_diameter_mm
):
    """
    Union half the bolted flange's thickness onto one clamp-shell half
    (below the parting line if sign < 0, above it if sign > 0), with
    through-bolt-holes at its 4 corners. Called once per half with the
    same XY hole positions, so the two halves' holes line up when
    assembled and a bolt (or a zip tie) can clamp them together.
    """
    if sign < 0:
        z_min, z_max = parting_z - flange_half_thickness_mm, parting_z
    else:
        z_min, z_max = parting_z, parting_z + flange_half_thickness_mm

    flange_outer_xy = (outer_xy[0] + 2 * flange_width_mm, outer_xy[1] + 2 * flange_width_mm)
    ring_outer = _new_box_from_range(bpy, "flange_outer", flange_outer_xy, cx, cy, z_min, z_max)
    ring_inner = _new_box_from_range(bpy, "flange_inner", outer_xy, cx, cy, z_min - 1.0, z_max + 1.0)
    ring = _boolean(bpy, ring_outer, ring_inner, "DIFFERENCE")
    half_obj = _boolean(bpy, half_obj, ring, "UNION")

    half_x = flange_outer_xy[0] / 2
    half_y = flange_outer_xy[1] / 2
    hole_z_center = (z_min + z_max) / 2
    for bx, by in _corner_offsets(half_x, half_y, flange_width_mm / 2):
        hole = _new_vertical_cylinder(
            bpy,
            "bolt_hole",
            bolt_hole_diameter_mm / 2,
            flange_half_thickness_mm + 4.0,
            (cx + bx, cy + by, hole_z_center),
        )
        half_obj = _boolean(bpy, half_obj, hole, "DIFFERENCE")
    return half_obj


def _export_one(bpy, obj, path):
    """Export exactly one object to an STL path (the shared export_mesh() dumps the whole scene, unusable here)."""
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.wm.stl_export(filepath=path, export_selected_objects=True)


def _rotate_axis_to_z(bpy, obj, axis: str) -> None:
    """
    Bake a rotation into obj's own mesh data so the requested parting_axis
    ('x'/'y'/'z') becomes local Z, letting every other function in this
    module keep hardcoding Z as "the" parting axis instead of being
    rewritten to take an arbitrary axis. 'z' is a no-op. The exported
    parts stay in this rotated frame (not rotated back) - see
    docs/adr/0010-configurable-parting-axis-and-volume-reporting.md for
    why that's a deliberate, documented v1 choice rather than an
    oversight. Verified live in .scratch_moldtest/debug_parting_axis.py
    against a non-cubic box that this produces the expected axis swap.
    """
    if axis == "z":
        return
    if axis == "x":
        obj.rotation_euler = (0.0, math.radians(-90), 0.0)
    elif axis == "y":
        obj.rotation_euler = (math.radians(90), 0.0, 0.0)
    else:
        raise ValueError(f"parting_axis must be 'x', 'y', or 'z' (got {axis!r})")
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)


def _resolve_parting_z(z_min: float, z_max: float, offset_mm: float) -> float:
    """
    The parting plane's Z coordinate: the model's own bounding-box
    midpoint along the (possibly rotated-to-Z) parting axis, shifted by
    offset_mm (FR-6) - positive moves the split toward z_max. Rejects an
    offset that would leave one half with zero or negative height, since
    that's not a valid two-part mold.
    """
    parting_z = (z_min + z_max) / 2 + offset_mm
    if not (z_min < parting_z < z_max):
        raise ValueError(
            f"parting_offset_mm={offset_mm} would put the parting plane at {parting_z:.2f}, outside the "
            f"model's own range ({z_min:.2f} to {z_max:.2f}) along its parting axis -- reduce the offset"
        )
    return parting_z


def build_mold(bpy, input_path: str, output_dir: str, params: dict) -> dict:
    """
    Import input_path's model, dispatch to the requested mode's builder,
    and return {"paths": {part_name: path}, "cavity_volume_mm3": float}
    (FR-8's casting-volume calculator - the actual material volume the
    cast/pour needs, per mode: see each builder's own docstring for what
    "cavity" means there).
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _shared.import_mesh(bpy, input_path)
    obj = _shared.join_into_single_object(bpy)
    _rotate_axis_to_z(bpy, obj, params["parting_axis"])

    xs = [v.co.x for v in obj.data.vertices]
    ys = [v.co.y for v in obj.data.vertices]
    zs = [v.co.z for v in obj.data.vertices]
    if not xs:
        raise ValueError(f"model at {input_path} has no vertices")
    model_min = (min(xs), min(ys), min(zs))
    model_max = (max(xs), max(ys), max(zs))

    if params["mode"] == "direct_cast":
        paths, volume = _build_direct_cast_mold(bpy, obj, model_min, model_max, output_dir, params)
    elif params["mode"] == "form_fitting":
        paths, volume = _build_form_fitting_mold(bpy, obj, model_min, model_max, output_dir, params)
    elif params["mode"] == "hollow_cast":
        paths, volume = _build_hollow_cast_mold(bpy, obj, model_min, model_max, output_dir, params)
    else:
        bpy.data.objects.remove(obj, do_unlink=True)
        paths, volume = _build_silicone_block_mold(bpy, model_min, model_max, output_dir, params)
    return {"paths": paths, "cavity_volume_mm3": volume}


def _build_direct_cast_halves(bpy, model_obj, model_min, model_max, params):
    """
    Build the direct-cast-style outer mold's two halves (cavity = the
    model's own mesh, bolted flange, no registration keys) - shared by
    direct_cast (the mold is the whole output) and hollow_cast (the mold
    is paired with a separate inward-offset core). Does NOT delete
    model_obj or export anything - the caller owns both, since
    hollow_cast still needs model_obj alive to build the core afterward.
    """
    wall = params["direct_mold_wall_mm"]
    flange_width = params["clamp_flange_width_mm"]

    model_size = tuple(mx - mn for mn, mx in zip(model_min, model_max, strict=True))
    model_center = tuple((mn + mx) / 2 for mn, mx in zip(model_min, model_max, strict=True))
    outer_xy = (model_size[0] + 2 * wall, model_size[1] + 2 * wall)
    cx, cy = model_center[0], model_center[1]
    parting_z = _resolve_parting_z(model_min[2], model_max[2], params["parting_offset_mm"])
    bottom_half_extent = parting_z - model_min[2]
    top_half_extent = model_max[2] - parting_z
    flange_half_thickness = max(wall, 4.0) / 2

    max_dim = max(
        max(outer_xy),
        bottom_half_extent + top_half_extent + 2 * wall,
        outer_xy[0] + 2 * flange_width,
        outer_xy[1] + 2 * flange_width,
    )
    if max_dim > params["max_dimension_mm"]:
        raise ValueError(
            f"resulting mold would be {max_dim:.1f}mm on its largest side, exceeding "
            f"MAX_MOLD_DIMENSION_MM={params['max_dimension_mm']} -- reduce direct_mold_wall_mm/"
            "clamp_flange_width_mm, or raise the limit for a genuinely large part"
        )

    bottom = build_offset_cavity_half(
        bpy, model_obj, "direct_bottom", outer_xy, cx, cy, parting_z, bottom_half_extent, wall, -1
    )
    top = build_offset_cavity_half(bpy, model_obj, "direct_top", outer_xy, cx, cy, parting_z, top_half_extent, wall, 1)

    bottom = _add_flange_half(
        bpy,
        bottom,
        outer_xy,
        cx,
        cy,
        parting_z,
        flange_width,
        flange_half_thickness,
        -1,
        params["bolt_hole_diameter_mm"],
    )
    top = _add_flange_half(
        bpy,
        top,
        outer_xy,
        cx,
        cy,
        parting_z,
        flange_width,
        flange_half_thickness,
        1,
        params["bolt_hole_diameter_mm"],
    )
    vent_xy = _find_vent_xy(bpy, model_obj, model_center, model_size, params["sprue_diameter_mm"])
    top = _add_pour_holes(
        bpy,
        top,
        model_center,
        model_size,
        vent_xy,
        parting_z + top_half_extent + wall,
        params["sprue_diameter_mm"],
        params["vent_diameter_mm"],
    )
    return bottom, top


def _build_direct_cast_mold(bpy, model_obj, model_min, model_max, output_dir, params):
    """
    Build direct_cast mode: the outer mold is the whole output - export
    both halves and delete model_obj. Cavity volume (FR-8) is the
    model's own volume, computed before it's deleted, since that's
    exactly how much resin/urethane/foam a pour uses.
    """
    volume = _object_volume_mm3(model_obj)
    bottom, top = _build_direct_cast_halves(bpy, model_obj, model_min, model_max, params)
    bpy.data.objects.remove(model_obj, do_unlink=True)

    paths = {
        "direct_mold_bottom": os.path.join(output_dir, "direct_mold_bottom.stl"),
        "direct_mold_top": os.path.join(output_dir, "direct_mold_top.stl"),
    }
    for part_obj, key in ((bottom, "direct_mold_bottom"), (top, "direct_mold_top")):
        _export_one(bpy, part_obj, paths[key])
    return paths, volume


def _build_hollow_cast_mold(bpy, model_obj, model_min, model_max, output_dir, params):
    """
    Build hollow_cast mode: the same rigid outer mold as direct_cast,
    plus a separate core - an inward-offset duplicate of the model
    shrunk by cast_wall_thickness_mm (see _offset_model_along_normals) -
    that the caller seats inside the bottom half's cavity before closing
    the top half and pouring, so the cast forms a hollow shell rather
    than a solid block. The core is built from model_obj before it's
    deleted, since _build_direct_cast_halves doesn't own that lifecycle.
    Cavity volume (FR-8) is the model's own volume minus the core's -
    the actual material the pour uses once the core displaces the
    center, not the full solid-block volume direct_cast would report.
    """
    core = _offset_model_along_normals(bpy, model_obj, "hollow_core", -params["cast_wall_thickness_mm"])
    volume = _object_volume_mm3(model_obj) - _object_volume_mm3(core)
    bottom, top = _build_direct_cast_halves(bpy, model_obj, model_min, model_max, params)
    bpy.data.objects.remove(model_obj, do_unlink=True)

    paths = {
        "hollow_cast_bottom": os.path.join(output_dir, "hollow_cast_bottom.stl"),
        "hollow_cast_top": os.path.join(output_dir, "hollow_cast_top.stl"),
        "hollow_cast_core": os.path.join(output_dir, "hollow_cast_core.stl"),
    }
    for part_obj, key in (
        (bottom, "hollow_cast_bottom"),
        (top, "hollow_cast_top"),
        (core, "hollow_cast_core"),
    ):
        _export_one(bpy, part_obj, paths[key])
    return paths, volume


def _build_form_fitting_mold(bpy, model_obj, model_min, model_max, output_dir, params):
    """
    Build form_fitting mode's two tool pairs: a skin pour tool (cavity =
    the model's surface offset outward by shell_thickness_mm -
    registration keys, for casting a thin flexible silicone shell around
    the model) and a support jacket (the *same* offset-surface cavity,
    since that's exactly the finished shell's own outer surface - bolted
    flange, for holding that shell rigid during the final pour into its
    hollow interior). Both tool pairs share one offset duplicate of
    model_obj, deleted once all 4 halves have their own cavity-cutting
    duplicates made from it. See
    docs/adr/0008-form-fitting-thin-shell-mold.md. Cavity volume (FR-8)
    is the offset shell's own volume minus the model's - the actual
    silicone the skin pour uses is only the thin gap between the two
    surfaces, not the offset solid's full volume.
    """
    shell_thickness = params["shell_thickness_mm"]
    skin_wall = params["skin_pour_wall_mm"]
    jacket_wall = params["support_jacket_wall_mm"]
    flange_width = params["clamp_flange_width_mm"]

    offset_obj = _offset_model_along_normals(bpy, model_obj, "shell_offset", shell_thickness)
    volume = _object_volume_mm3(offset_obj) - _object_volume_mm3(model_obj)
    bpy.data.objects.remove(model_obj, do_unlink=True)

    xs = [v.co.x for v in offset_obj.data.vertices]
    ys = [v.co.y for v in offset_obj.data.vertices]
    zs = [v.co.z for v in offset_obj.data.vertices]
    if not xs:
        raise ValueError("shell offset produced no vertices")
    cavity_size = (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))
    cavity_center = ((max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2, (max(zs) + min(zs)) / 2)
    cx, cy = cavity_center[0], cavity_center[1]
    # parting_z is resolved against the *original* model's own bbox (before
    # the outward offset grew it), so parting_offset_mm means the same
    # thing here as it does for every other mode - a shift relative to the
    # model's own geometry, not the shell-grown cavity's.
    parting_z = _resolve_parting_z(model_min[2], model_max[2], params["parting_offset_mm"])
    bottom_half_extent = parting_z - min(zs)
    top_half_extent = max(zs) - parting_z

    skin_outer_xy = (cavity_size[0] + 2 * skin_wall, cavity_size[1] + 2 * skin_wall)
    jacket_outer_xy = (cavity_size[0] + 2 * jacket_wall, cavity_size[1] + 2 * jacket_wall)
    flange_half_thickness = max(jacket_wall, 4.0) / 2

    max_dim = max(
        max(skin_outer_xy),
        bottom_half_extent + top_half_extent + 2 * skin_wall,
        jacket_outer_xy[0] + 2 * flange_width,
        jacket_outer_xy[1] + 2 * flange_width,
        bottom_half_extent + top_half_extent + 2 * jacket_wall,
    )
    if max_dim > params["max_dimension_mm"]:
        raise ValueError(
            f"resulting mold would be {max_dim:.1f}mm on its largest side, exceeding "
            f"MAX_MOLD_DIMENSION_MM={params['max_dimension_mm']} -- reduce shell_thickness_mm/"
            "skin_pour_wall_mm/support_jacket_wall_mm/clamp_flange_width_mm, or raise the limit "
            "for a genuinely large part"
        )

    skin_bottom = build_offset_cavity_half(
        bpy, offset_obj, "skin_bottom", skin_outer_xy, cx, cy, parting_z, bottom_half_extent, skin_wall, -1
    )
    skin_top = build_offset_cavity_half(
        bpy, offset_obj, "skin_top", skin_outer_xy, cx, cy, parting_z, top_half_extent, skin_wall, 1
    )
    jacket_bottom = build_offset_cavity_half(
        bpy, offset_obj, "jacket_bottom", jacket_outer_xy, cx, cy, parting_z, bottom_half_extent, jacket_wall, -1
    )
    jacket_top = build_offset_cavity_half(
        bpy, offset_obj, "jacket_top", jacket_outer_xy, cx, cy, parting_z, top_half_extent, jacket_wall, 1
    )
    vent_xy = _find_vent_xy(bpy, offset_obj, cavity_center, cavity_size, params["sprue_diameter_mm"])
    bpy.data.objects.remove(offset_obj, do_unlink=True)

    skin_bottom, skin_top = _add_registration_keys(
        bpy, skin_bottom, skin_top, cavity_size, cavity_center, parting_z, params["key_diameter_mm"], skin_wall
    )
    skin_top = _add_pour_holes(
        bpy,
        skin_top,
        cavity_center,
        cavity_size,
        vent_xy,
        parting_z + top_half_extent + skin_wall,
        params["sprue_diameter_mm"],
        params["vent_diameter_mm"],
    )

    jacket_bottom = _add_flange_half(
        bpy,
        jacket_bottom,
        jacket_outer_xy,
        cx,
        cy,
        parting_z,
        flange_width,
        flange_half_thickness,
        -1,
        params["bolt_hole_diameter_mm"],
    )
    jacket_top = _add_flange_half(
        bpy,
        jacket_top,
        jacket_outer_xy,
        cx,
        cy,
        parting_z,
        flange_width,
        flange_half_thickness,
        1,
        params["bolt_hole_diameter_mm"],
    )
    jacket_top = _add_pour_holes(
        bpy,
        jacket_top,
        cavity_center,
        cavity_size,
        vent_xy,
        parting_z + top_half_extent + jacket_wall,
        params["sprue_diameter_mm"],
        params["vent_diameter_mm"],
    )

    paths = {
        "skin_pour_bottom": os.path.join(output_dir, "skin_pour_bottom.stl"),
        "skin_pour_top": os.path.join(output_dir, "skin_pour_top.stl"),
        "support_jacket_bottom": os.path.join(output_dir, "support_jacket_bottom.stl"),
        "support_jacket_top": os.path.join(output_dir, "support_jacket_top.stl"),
    }
    for part_obj, key in (
        (skin_bottom, "skin_pour_bottom"),
        (skin_top, "skin_pour_top"),
        (jacket_bottom, "support_jacket_bottom"),
        (jacket_top, "support_jacket_top"),
    ):
        _export_one(bpy, part_obj, paths[key])
    return paths, volume


def _build_silicone_block_mold(bpy, model_min, model_max, output_dir, params):
    """
    Build all 4 silicone_block mold parts (pour box + clamp shell, each
    in 2 halves) and export them. Cavity volume (FR-8) is the pour box's
    own box-shaped cavity volume - the actual silicone a pour uses -
    computed analytically (no bmesh needed for a box).
    """
    clearance = params["clearance_mm"]
    pour_wall = params["pour_box_wall_mm"]
    clamp_wall = params["clamp_wall_mm"]
    flange_width = params["clamp_flange_width_mm"]

    cavity_size = tuple((mx - mn) + 2 * clearance for mn, mx in zip(model_min, model_max, strict=True))
    cavity_center = tuple((mn + mx) / 2 for mn, mx in zip(model_min, model_max, strict=True))
    cavity_xy = (cavity_size[0], cavity_size[1])
    cx, cy = cavity_center[0], cavity_center[1]
    parting_z = _resolve_parting_z(model_min[2], model_max[2], params["parting_offset_mm"])
    bottom_half_extent = (parting_z - model_min[2]) + clearance
    top_half_extent = (model_max[2] - parting_z) + clearance

    pour_outer_xy = (cavity_size[0] + 2 * pour_wall, cavity_size[1] + 2 * pour_wall)
    clamp_outer_xy = (cavity_size[0] + 2 * clamp_wall, cavity_size[1] + 2 * clamp_wall)
    flange_half_thickness = max(clamp_wall, 4.0) / 2

    max_dim = max(
        max(pour_outer_xy),
        bottom_half_extent + top_half_extent + 2 * pour_wall,
        clamp_outer_xy[0] + 2 * flange_width,
        clamp_outer_xy[1] + 2 * flange_width,
        bottom_half_extent + top_half_extent + 2 * clamp_wall,
    )
    if max_dim > params["max_dimension_mm"]:
        raise ValueError(
            f"resulting mold would be {max_dim:.1f}mm on its largest side, exceeding "
            f"MAX_MOLD_DIMENSION_MM={params['max_dimension_mm']} -- reduce clearance_mm/"
            "pour_box_wall_mm/clamp_wall_mm/clamp_flange_width_mm, or raise the limit for a genuinely large part"
        )

    pour_bottom = build_half(
        bpy, "pour_bottom", pour_outer_xy, cavity_xy, cx, cy, parting_z, bottom_half_extent, pour_wall, -1
    )
    pour_top = build_half(bpy, "pour_top", pour_outer_xy, cavity_xy, cx, cy, parting_z, top_half_extent, pour_wall, 1)
    pour_bottom, pour_top = _add_registration_keys(
        bpy, pour_bottom, pour_top, cavity_size, cavity_center, parting_z, params["key_diameter_mm"], pour_wall
    )
    # cavity_obj=None: the pour box's cavity is a synthetic box (no model
    # mesh to analyze), so _find_vent_xy always falls back to the fixed
    # offset here - unchanged from pre-FR-7 behavior.
    vent_xy = _find_vent_xy(bpy, None, cavity_center, cavity_size, params["sprue_diameter_mm"])
    pour_top = _add_pour_holes(
        bpy,
        pour_top,
        cavity_center,
        cavity_size,
        vent_xy,
        parting_z + top_half_extent + pour_wall,
        params["sprue_diameter_mm"],
        params["vent_diameter_mm"],
    )

    clamp_bottom = build_half(
        bpy, "clamp_bottom", clamp_outer_xy, cavity_xy, cx, cy, parting_z, bottom_half_extent, clamp_wall, -1
    )
    clamp_top = build_half(
        bpy, "clamp_top", clamp_outer_xy, cavity_xy, cx, cy, parting_z, top_half_extent, clamp_wall, 1
    )
    clamp_bottom = _add_flange_half(
        bpy,
        clamp_bottom,
        clamp_outer_xy,
        cx,
        cy,
        parting_z,
        flange_width,
        flange_half_thickness,
        -1,
        params["bolt_hole_diameter_mm"],
    )
    clamp_top = _add_flange_half(
        bpy,
        clamp_top,
        clamp_outer_xy,
        cx,
        cy,
        parting_z,
        flange_width,
        flange_half_thickness,
        1,
        params["bolt_hole_diameter_mm"],
    )
    clamp_top = _add_pour_holes(
        bpy,
        clamp_top,
        cavity_center,
        cavity_size,
        vent_xy,
        parting_z + top_half_extent + clamp_wall,
        params["sprue_diameter_mm"],
        params["vent_diameter_mm"],
    )

    paths = {
        "pour_box_bottom": os.path.join(output_dir, "pour_box_bottom.stl"),
        "pour_box_top": os.path.join(output_dir, "pour_box_top.stl"),
        "clamp_shell_bottom": os.path.join(output_dir, "clamp_shell_bottom.stl"),
        "clamp_shell_top": os.path.join(output_dir, "clamp_shell_top.stl"),
    }
    for part_obj, key in (
        (pour_bottom, "pour_box_bottom"),
        (pour_top, "pour_box_top"),
        (clamp_bottom, "clamp_shell_bottom"),
        (clamp_top, "clamp_shell_top"),
    ):
        _export_one(bpy, part_obj, paths[key])
    volume = cavity_xy[0] * cavity_xy[1] * cavity_size[2]
    return paths, volume


def main():
    """CLI entry point: parse args, run build_mold(), write the JSON report (or a JSON error) to --report-output."""
    args = _parse_args()
    try:
        import bpy
    except ImportError as exc:  # pragma: no cover - only fails outside Blender
        raise RuntimeError(
            "make_mold.py must be run inside Blender (blender --background --python make_mold.py -- ...)"
        ) from exc

    os.makedirs(args.output_dir, exist_ok=True)
    params = {
        "mode": args.mode,
        "direct_mold_wall_mm": args.direct_mold_wall_mm,
        "shell_thickness_mm": args.shell_thickness_mm,
        "skin_pour_wall_mm": args.skin_pour_wall_mm,
        "support_jacket_wall_mm": args.support_jacket_wall_mm,
        "cast_wall_thickness_mm": args.cast_wall_thickness_mm,
        "parting_axis": args.parting_axis,
        "parting_offset_mm": args.parting_offset_mm,
        "clearance_mm": args.clearance_mm,
        "pour_box_wall_mm": args.pour_box_wall_mm,
        "key_diameter_mm": args.key_diameter_mm,
        "sprue_diameter_mm": args.sprue_diameter_mm,
        "vent_diameter_mm": args.vent_diameter_mm,
        "clamp_wall_mm": args.clamp_wall_mm,
        "clamp_flange_width_mm": args.clamp_flange_width_mm,
        "bolt_hole_diameter_mm": args.bolt_hole_diameter_mm,
        "max_dimension_mm": args.max_dimension_mm,
    }

    try:
        report = build_mold(bpy, args.input, args.output_dir, params)
        with open(args.report_output, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
    except Exception as exc:  # noqa: BLE001 - must still emit a JSON error for the caller
        with open(args.report_output, "w", encoding="utf-8") as f:
            json.dump({"error": str(exc)}, f, indent=2)
        print(f"make_mold.py failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
