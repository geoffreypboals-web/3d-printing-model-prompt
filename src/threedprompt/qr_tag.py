"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/qr_tag.py
Description: QR tag template (docs/print-tools-plan.md item 4): text, a URL
    or Wi-Fi details -> a QR code (segno, BSD-3-Clause) raised on a
    rounded plate, with a quiet-zone border, an optional key-ring hole and
    an optional NFC sticker pocket in the back. The plate and the raised
    code are also written as separate STLs so they can be printed in two
    colours (or with a filament change at the code's first layer).
Inputs: make_qr_tag(output_dir, **params) - params mirror QrTagRequest in
    models.py.
Outputs: qr_tag.stl (plate + code), qr_plate.stl, qr_code.stl in
    output_dir and a TemplateResult.
Troubleshooting:
    - Phone won't scan it: the code needs strong contrast - print the code
      in black on a white plate (two colours or a filament change), keep
      module_mm at 1.5 mm or more, and keep the quiet zone (4 is the
      standard; 2 usually still scans).
    - Wi-Fi details: the password is part of the code itself, so anyone
      who scans the tag gets it. It is never logged or saved with the
      model's spec; only the STLs hold it.
"""

from __future__ import annotations

from pathlib import Path

import manifold3d as mf
import numpy as np
import segno
from segno import helpers

from threedprompt import geometry, gridfinity
from threedprompt.geometry import TemplateError
from threedprompt.models import TemplatePart, TemplateResult

TAG_FILENAME = "qr_tag.stl"
PLATE_FILENAME = "qr_plate.stl"
CODE_FILENAME = "qr_code.stl"


def qr_matrix(kind: str, text: str, ssid: str, password: str, security: str, error: str) -> np.ndarray:
    """
    Encode the content as a QR code.

    Returns:
        Boolean array, True = dark module, without a quiet zone.
    Raises:
        TemplateError: empty or too-long content.
    """
    try:
        if kind == "wifi":
            if not ssid:
                raise TemplateError("enter the Wi-Fi network name (SSID)")
            sec = None if security == "nopass" else security
            data = helpers.make_wifi_data(ssid=ssid, password=password or None, security=sec)
            code = segno.make(data, error=error, micro=False)
        else:
            if not text:
                raise TemplateError("enter the text or URL to encode")
            code = segno.make(text, error=error, micro=False)
    except (ValueError, segno.DataOverflowError) as exc:
        raise TemplateError(f"can't encode that as a QR code: {exc}") from exc
    return np.array([[bool(v) for v in row] for row in code.matrix_iter(scale=1, border=0)], dtype=bool)


def _module_runs(matrix: np.ndarray, module: float, origin: tuple[float, float]) -> list[mf.CrossSection]:
    """Dark modules as rectangles, merging horizontal runs (row 0 is the top) to keep the boolean small."""
    rects = []
    n = matrix.shape[0]
    for r in range(n):
        c = 0
        while c < n:
            if not matrix[r, c]:
                c += 1
                continue
            start = c
            while c < n and matrix[r, c]:
                c += 1
            x = origin[0] + start * module
            y = origin[1] + (n - 1 - r) * module
            rects.append(mf.CrossSection.square((module * (c - start), module)).translate((x, y)))
    return rects


def make_qr_tag(
    output_dir: Path,
    *,
    kind: str,
    text: str,
    ssid: str,
    password: str,
    security: str,
    error_correction: str,
    module_mm: float,
    quiet_zone: int,
    plate_mm: float,
    code_height_mm: float,
    corner_radius_mm: float,
    keyring_hole: bool,
    nfc_pocket: bool,
    nfc_diameter_mm: float,
) -> TemplateResult:
    """
    Build a QR tag: plate with the code raised on top.

    Args:
        output_dir: where the STLs go.
        kind: "text" (text or URL) or "wifi".
        text: content for kind "text".
        ssid, password, security: Wi-Fi details for kind "wifi" (security WPA, WEP or nopass).
        error_correction: segno error level l, m, q or h.
        module_mm: size of one QR square.
        quiet_zone: empty border in modules (4 is the standard).
        plate_mm: plate thickness.
        code_height_mm: how far the code stands up.
        corner_radius_mm: plate corner radius.
        keyring_hole: add a tab with a 5 mm hole at the top.
        nfc_pocket: add a round pocket in the back for an NFC sticker.
        nfc_diameter_mm: pocket diameter (stickers are usually 25 mm).
    Returns:
        TemplateResult with the combined tag and the two colour parts.
    """
    matrix = qr_matrix(kind, text, ssid, password, security, error_correction)
    n = matrix.shape[0]
    side = (n + 2 * quiet_zone) * module_mm
    outline = (
        gridfinity.rounded_rect(side, side, corner_radius_mm)
        if corner_radius_mm > 0
        else (mf.CrossSection.square((side, side), center=True))
    )
    notes: list[str] = []
    if keyring_hole:
        tab = mf.CrossSection.circle(6, 48).translate((0, side / 2)) + mf.CrossSection.square((12, 6)).translate(
            (-6, side / 2 - 6)
        )
        outline = outline + tab
    plate = mf.Manifold.extrude(outline, plate_mm)
    cuts = []
    if keyring_hole:
        cuts.append(geometry.cylinder(2.5, plate_mm + 1, -0.5, 32).translate((0, side / 2 + 1, 0)))
    if nfc_pocket:
        if nfc_diameter_mm > side - 4:
            notes.append("NFC pocket is wider than the plate allows; it was left out. Use a larger module_mm.")
        elif plate_mm < 2:
            notes.append("Plate is under 2 mm thick; the NFC pocket was left out.")
        else:
            cuts.append(geometry.cylinder(nfc_diameter_mm / 2, 0.9 + 0.5, -0.5, 64))
            notes.append("NFC pocket: 0.9 mm deep in the back; stick the tag in and cover it with tape or glue.")
    if cuts:
        plate = plate - mf.Manifold.batch_boolean(cuts, mf.OpType.Add)

    origin = (-n * module_mm / 2, -n * module_mm / 2)
    code_2d = mf.CrossSection.batch_boolean(_module_runs(matrix, module_mm, origin), mf.OpType.Add)
    # Modules that touch only at a corner would share a single edge in 3D (non-manifold);
    # shrinking the code by 0.02 mm separates them without changing how it scans.
    code_2d = code_2d.offset(-0.02, mf.JoinType.Miter)
    # The code sinks 0.01 mm into the plate so the combined tag is one solid without coplanar faces.
    code = mf.Manifold.extrude(code_2d, code_height_mm + 0.01).translate((0, 0, plate_mm - 0.01))

    tag = plate + code
    parts = [
        TemplatePart(
            filename=TAG_FILENAME,
            size_mm=geometry.save(tag, output_dir / TAG_FILENAME),
            print_hint="print flat; filament change at the code's first layer for contrast",
        ),
        TemplatePart(
            filename=PLATE_FILENAME,
            size_mm=geometry.save(plate, output_dir / PLATE_FILENAME),
            print_hint="colour 1 (light)",
        ),
        TemplatePart(
            filename=CODE_FILENAME,
            size_mm=geometry.save(code, output_dir / CODE_FILENAME),
            print_hint="colour 2 (dark); same position as the plate",
        ),
    ]
    if module_mm < 1.5:
        notes.append("Modules under 1.5 mm can be hard to print cleanly and to scan.")
    if quiet_zone < 4:
        notes.append(f"Quiet zone of {quiet_zone} modules is below the standard 4; most phones still scan it.")
    return TemplateResult(
        parts=parts,
        notes=notes,
        report={
            "modules": n,
            "version": (n - 17) // 4,
            "plate_mm": [round(side, 2), round(side, 2), plate_mm],
            "code_layer_z_mm": plate_mm,
        },
    )
