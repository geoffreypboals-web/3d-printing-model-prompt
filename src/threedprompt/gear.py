"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/gear.py
Description: Involute spur and helical gears, a matching rack, and a mesh
    check for a gear pair (docs/print-tools-plan.md item 4). Written here
    from the standard formulas - no gear library:
      pitch radius r = m z / 2, base radius rb = r cos(alpha),
      tip radius ra = r + m, root radius rf = r - 1.25 m,
      flank angle from the tooth centre at radius R:
        psi + inv(alpha) - inv(alpha_R), inv(a) = tan(a) - a,
        cos(alpha_R) = rb / R, psi = half the tooth thickness angle.
    Backlash is split evenly between the two gears of a pair (each tooth
    is thinned by backlash/2 along the pitch circle). Helical gears are
    the spur profile extruded with a twist; a helical pair needs opposite
    helix angles.
Inputs: make_gears(output_dir, **params) - params mirror GearRequest in
    models.py.
Outputs: gear.stl, optional gear_mate.stl (placed at the centre
    distance) and rack.stl in output_dir; a TemplateResult whose report
    holds the pitch/tip/root diameters, centre distance and contact ratio.
Troubleshooting:
    - Teeth look pinched at the root for fewer than 17 teeth (20 deg):
      that's undercut, which real gear cutting has too; it is not
      modelled, so use 17+ teeth or a larger pressure angle for strength.
    - Gears bind: raise backlash_mm (0.1-0.2 is usual for FDM) and check
      your slicer's XY compensation / elephant's foot setting.
    - "contact ratio below 1.2": the pair will run rough; use more teeth.
"""

from __future__ import annotations

import math
from pathlib import Path

import manifold3d as mf
import numpy as np

from threedprompt import geometry
from threedprompt.geometry import TemplateError
from threedprompt.models import TemplatePart, TemplateResult

_FLANK_POINTS = 12  # points along each involute flank
_TIP_POINTS = 4


def _inv(a: float) -> float:
    """Involute function tan(a) - a."""
    return math.tan(a) - a


def gear_profile(module: float, teeth: int, pressure_deg: float, backlash: float) -> mf.CrossSection:
    """
    2D outline of an involute spur gear centred on the origin.

    Args:
        module: tooth size m in mm (pitch diameter / teeth).
        teeth: tooth count.
        pressure_deg: pressure angle (20 is standard).
        backlash: total backlash of a pair in mm; this gear takes half.
    Returns:
        The gear outline (no bore).
    """
    alpha = math.radians(pressure_deg)
    r = module * teeth / 2
    rb = r * math.cos(alpha)
    ra = r + module
    rf = max(r - 1.25 * module, 0.5)
    thickness = math.pi * module / 2 - backlash / 2  # along the pitch circle
    psi = thickness / (2 * r)  # half the tooth's angle at the pitch circle
    start = max(rb, rf)
    radii = np.linspace(start, ra, _FLANK_POINTS)
    half = []  # (radius, angle from tooth centre) along one flank, root to tip
    for rad in radii:
        alpha_r = math.acos(min(1.0, rb / rad))
        half.append((rad, psi + _inv(alpha) - _inv(alpha_r)))
    if half[-1][1] <= 0:
        raise TemplateError("teeth come to a point before the tip; use more backlash-free settings or a larger module")
    pitch_angle = 2 * math.pi / teeth
    pts = []
    for k in range(teeth):
        c = k * pitch_angle
        # Root point under the flank (radial line down from the base circle when rf < rb).
        root_a = half[0][1]
        pts.append((rf, c - root_a))
        for rad, a in half:
            pts.append((rad, c - a))
        tip_a = half[-1][1]
        for t in np.linspace(-tip_a, tip_a, _TIP_POINTS + 2)[1:-1]:
            pts.append((ra, c + t))
        for rad, a in reversed(half):
            pts.append((rad, c + a))
        pts.append((rf, c + root_a))
        # Root arc to the next tooth.
        for t in np.linspace(c + root_a, c + pitch_angle - root_a, 5)[1:-1]:
            pts.append((rf, t))
    xy = [(rad * math.cos(a), rad * math.sin(a)) for rad, a in pts]
    return mf.CrossSection([xy], mf.FillRule.Positive).simplify(1e-4)


def _bore(kind: str, diameter: float) -> mf.CrossSection | None:
    """Bore cut-out: round, D (one flat at 0.8 of the diameter) or hex (diameter across flats)."""
    if kind == "none" or diameter <= 0:
        return None
    if kind == "hex":
        return mf.CrossSection.circle(diameter / (2 * math.cos(math.pi / 6)), 6)
    circle = mf.CrossSection.circle(diameter / 2, 64)
    if kind == "d":
        cut = mf.CrossSection.square((diameter, diameter * 2)).translate((diameter * 0.3, -diameter))
        return circle - cut
    return circle


def _extrude(profile: mf.CrossSection, width: float, helix_deg: float, pitch_r: float) -> mf.Manifold:
    """Extrude a gear profile; a helix angle twists it so the teeth follow a helix at the pitch radius."""
    if helix_deg == 0:
        return mf.Manifold.extrude(profile, width)
    twist = math.degrees(width * math.tan(math.radians(helix_deg)) / pitch_r)
    divisions = max(4, int(abs(twist) / 2))
    return mf.Manifold.extrude(profile, width, divisions, twist)


def make_gears(
    output_dir: Path,
    *,
    module: float,
    teeth: int,
    pressure_angle_deg: float,
    backlash_mm: float,
    face_width_mm: float,
    helix_angle_deg: float,
    bore: str,
    bore_diameter_mm: float,
    mate_teeth: int | None,
    rack_teeth: int | None,
    rack_height_mm: float,
) -> TemplateResult:
    """
    Build a gear, optionally its mating gear (placed in mesh) and a rack.

    Args:
        output_dir: where the STLs go.
        module: tooth size in mm; gears only mesh with the same module and pressure angle.
        teeth: teeth on the main gear.
        pressure_angle_deg: 14.5, 20 or 25 usually.
        backlash_mm: total play of the pair along the pitch circle.
        face_width_mm: gear thickness.
        helix_angle_deg: 0 for spur; the mate gets the opposite hand.
        bore: "none", "round", "d" or "hex".
        bore_diameter_mm: bore size (across flats for hex).
        mate_teeth: teeth on a second gear to pair with, or None.
        rack_teeth: teeth on a rack, or None.
        rack_height_mm: rack body height below the tooth roots.
    Returns:
        TemplateResult with the parts and a mesh report.
    Raises:
        TemplateError: a bore that leaves no material, or a profile that can't be built.
    """
    notes: list[str] = []
    alpha = math.radians(pressure_angle_deg)
    parts: list[TemplatePart] = []
    report: dict = {}

    def one(z: int, helix: float, name: str, offset: tuple[float, float, float], spin_deg: float) -> dict:
        r = module * z / 2
        profile = gear_profile(module, z, pressure_angle_deg, backlash_mm)
        hole = _bore(bore, bore_diameter_mm)
        if hole is not None:
            if bore_diameter_mm / 2 > r - 1.25 * module - 1.5:
                raise TemplateError(
                    f"a {bore_diameter_mm} mm bore leaves under 1.5 mm under the teeth of the {z}-tooth gear"
                )
            profile = profile - hole
        solid = _extrude(profile.rotate(spin_deg), face_width_mm, helix, r).translate(offset)
        size = geometry.save(solid, output_dir / name)
        parts.append(TemplatePart(filename=name, size_mm=size, print_hint="print flat"))
        return {
            "teeth": z,
            "pitch_diameter_mm": round(2 * r, 3),
            "tip_diameter_mm": round(2 * r + 2 * module, 3),
            "root_diameter_mm": round(2 * r - 2.5 * module, 3),
            "base_diameter_mm": round(2 * r * math.cos(alpha), 3),
        }

    report["gear"] = one(teeth, helix_angle_deg, "gear.stl", (0, 0, 0), 0)
    if teeth < 17 and pressure_angle_deg <= 20:
        notes.append(
            f"{teeth} teeth at {pressure_angle_deg} deg would be undercut on a real gear; 17+ teeth is stronger."
        )

    if mate_teeth:
        a = module * (teeth + mate_teeth) / 2
        # Turn the mate half a tooth when its count is even so a tooth meets a gap on the line of centres.
        spin = 180 / mate_teeth if mate_teeth % 2 == 0 else 0.0
        report["mate"] = one(mate_teeth, -helix_angle_deg, "gear_mate.stl", (a, 0, 0), spin)
        ra1, ra2 = module * teeth / 2 + module, module * mate_teeth / 2 + module
        rb1, rb2 = module * teeth / 2 * math.cos(alpha), module * mate_teeth / 2 * math.cos(alpha)
        contact = (math.sqrt(ra1**2 - rb1**2) + math.sqrt(ra2**2 - rb2**2) - a * math.sin(alpha)) / (
            math.pi * module * math.cos(alpha)
        )
        report["pair"] = {
            "centre_distance_mm": round(a, 3),
            "ratio": round(mate_teeth / teeth, 4),
            "contact_ratio": round(contact, 3),
            "backlash_mm": backlash_mm,
        }
        if contact < 1.2:
            notes.append(f"Contact ratio {contact:.2f} is below 1.2; the pair will run rough. Use more teeth.")
        if helix_angle_deg:
            notes.append("Helical pair: the mate has the opposite helix hand, as it must to mesh on parallel axes.")

    if rack_teeth:
        if helix_angle_deg:
            notes.append("The rack is straight-cut; it only meshes with a spur gear (helix angle 0).")
        pitch = math.pi * module
        thickness = pitch / 2 - backlash_mm / 2
        tan_a = math.tan(alpha)
        root_y = -1.25 * module
        pts = [(0.0, root_y - rack_height_mm)]
        for k in range(rack_teeth):
            c = (k + 0.5) * pitch  # tooth centre
            pts += [
                (c - thickness / 2 - 1.25 * module * tan_a, root_y),
                (c - thickness / 2 + module * tan_a, module),
                (c + thickness / 2 - module * tan_a, module),
                (c + thickness / 2 + 1.25 * module * tan_a, root_y),
            ]
        length = rack_teeth * pitch
        pts += [(length, root_y), (length, root_y - rack_height_mm)]
        profile = mf.CrossSection([pts], mf.FillRule.NonZero).simplify(1e-4)  # clockwise as listed
        # Lay the rack under the main gear, pitch line tangent to its pitch circle, in mesh: the
        # gear's tooth nearest the bottom sits phi0 past -90 deg, i.e. at x = r * phi0 along the
        # pitch line, so a rack gap (gaps are at multiples of the pitch) is moved there.
        r = module * teeth / 2
        phi0 = math.radians(90 % (360 / teeth))
        shift = r * phi0 - (rack_teeth // 2) * pitch
        solid = mf.Manifold.extrude(profile, face_width_mm).translate((shift, -r, 0))
        size = geometry.save(solid, output_dir / "rack.stl")
        parts.append(TemplatePart(filename="rack.stl", size_mm=size, print_hint="print flat"))
        report["rack"] = {"teeth": rack_teeth, "length_mm": round(length, 3), "pitch_mm": round(pitch, 4)}

    return TemplateResult(parts=parts, notes=notes, report=report)
