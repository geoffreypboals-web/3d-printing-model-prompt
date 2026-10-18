"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/lithophane.py
Description: Lithophane from a photo (docs/print-tools-plan.md item 4):
    the image becomes a height map - darker pixels are thicker, so less
    light gets through - on a flat panel, a curved panel or a full
    cylinder (a lamp shade), with an optional solid frame. The mesh is
    built directly as a closed grid (relief surface + smooth back + edge
    walls) with numpy and written with trimesh; no booleans are needed.
    Images are decoded with OpenCV (already used by tool_insert.py).
Inputs: make_lithophane(image_bytes, output_dir, **params) - params mirror
    LithophaneRequest in models.py.
Outputs: lithophane.stl in output_dir and a TemplateResult.
Troubleshooting:
    - Image looks washed out when lit: raise max_thickness_mm (3 mm is a
      common maximum) or lower min_thickness_mm (0.6-0.8 mm).
    - Image is mirrored: the relief faces the viewer (-Y for flat, outward
      for curved/cylinder); print upright with the relief toward the front
      of the bed, or flip the image before uploading.
    - Very large STL: lower resolution (mm per pixel) - 0.2 mm already
      matches a 0.4 mm nozzle's detail.
"""

from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np
import trimesh

from threedprompt.geometry import TemplateError
from threedprompt.models import TemplatePart, TemplateResult

OUTPUT_FILENAME = "lithophane.stl"
_MAX_GRID_POINTS = 600_000  # caps the mesh at ~2.4 M triangles


def _grid_faces(rows: int, cols: int, wrap: bool, offset: int = 0, flip: bool = False) -> np.ndarray:
    """Two triangles per grid cell for a rows x cols vertex grid (columns wrap around when `wrap`)."""
    c_count = cols if wrap else cols - 1
    r = np.arange(rows - 1)[:, None]
    c = np.arange(c_count)[None, :]
    a = r * cols + c
    b = r * cols + (c + 1) % cols
    d = (r + 1) * cols + c
    e = (r + 1) * cols + (c + 1) % cols
    tris = np.concatenate([np.stack([a, d, b], -1).reshape(-1, 3), np.stack([b, d, e], -1).reshape(-1, 3)])
    if flip:
        tris = tris[:, ::-1]
    return tris + offset


def _strip(front: np.ndarray, back: np.ndarray) -> np.ndarray:
    """Triangles joining two matching vertex index sequences (an edge wall between front and back)."""
    a, b = front[:-1], front[1:]
    c, d = back[:-1], back[1:]
    return np.concatenate([np.stack([a, b, c], -1), np.stack([b, d, c], -1)])


def make_lithophane(
    image_bytes: bytes,
    output_dir: Path,
    *,
    shape: str,
    width_mm: float,
    min_thickness_mm: float,
    max_thickness_mm: float,
    resolution_mm: float,
    frame_mm: float,
    curve_deg: float,
) -> TemplateResult:
    """
    Build a lithophane and write it to lithophane.stl.

    Args:
        image_bytes: the photo (JPEG/PNG).
        output_dir: where the STL goes.
        shape: "flat", "curved" or "cylinder".
        width_mm: panel width (for curved/cylinder: the arc length, i.e. the circumference for a cylinder).
        min_thickness_mm: thickness at white.
        max_thickness_mm: thickness at black.
        resolution_mm: size of one image pixel on the panel.
        frame_mm: solid border at max thickness (top and bottom only for a cylinder).
        curve_deg: arc of a curved panel.
    Returns:
        TemplateResult with the one part.
    Raises:
        TemplateError: unreadable image, or settings giving too many points.
    """
    gray = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise TemplateError("could not read the image; use a JPEG or PNG")
    if max_thickness_mm <= min_thickness_mm:
        raise TemplateError("max_thickness_mm must be more than min_thickness_mm")
    frame_px = int(round(frame_mm / resolution_mm))
    inner_w = int(round(width_mm / resolution_mm)) - (0 if shape == "cylinder" else 2 * frame_px)
    if inner_w < 10:
        raise TemplateError("the frame leaves no room for the picture; make it narrower or the panel wider")
    inner_h = max(2, int(round(inner_w * gray.shape[0] / gray.shape[1])))
    rows, cols = inner_h + 2 * frame_px, inner_w + (0 if shape == "cylinder" else 2 * frame_px)
    if rows * cols * 2 > _MAX_GRID_POINTS:
        raise TemplateError(
            f"{cols} x {rows} points is too fine; raise resolution_mm (e.g. {resolution_mm * 1.5:.2f})"
            " or reduce width_mm"
        )
    img = cv2.resize(gray, (inner_w, inner_h), interpolation=cv2.INTER_AREA).astype(float) / 255.0
    thick = min_thickness_mm + (1.0 - img) * (max_thickness_mm - min_thickness_mm)
    if frame_px:
        pad = ((frame_px, frame_px), (0, 0) if shape == "cylinder" else (frame_px, frame_px))
        thick = np.pad(thick, pad, constant_values=max_thickness_mm)

    wrap = shape == "cylinder"
    step = width_mm / (cols if wrap else cols - 1)  # ~resolution_mm, adjusted so the width comes out exact
    u = np.arange(cols) * step  # along the width
    v = (rows - 1 - np.arange(rows)) * step  # height; image row 0 is the top
    uu, vv = np.meshgrid(u, v)
    if shape == "flat":
        front = np.stack([uu, -thick, vv], -1)
        back = np.stack([uu, np.zeros_like(uu), vv], -1)
    else:
        arc = 2 * math.pi if wrap else math.radians(curve_deg)
        radius = width_mm / arc  # inner (back) radius
        ang = uu / radius - (0 if wrap else arc / 2)
        out_r = radius + thick
        front = np.stack([out_r * np.sin(ang), -out_r * np.cos(ang), vv], -1)
        back = np.stack([radius * np.sin(ang), -radius * np.cos(ang), vv], -1)

    n = rows * cols
    verts = np.concatenate([front.reshape(-1, 3), back.reshape(-1, 3)])
    idx = np.arange(n).reshape(rows, cols)
    faces = [_grid_faces(rows, cols, wrap), _grid_faces(rows, cols, wrap, offset=n, flip=True)]
    top, bottom = idx[0], idx[-1]
    if wrap:
        top, bottom = np.append(top, top[0]), np.append(bottom, bottom[0])
    # Edge walls; each is wound so its normal points away from the panel (top up, left to -u, ...).
    faces += [_strip(top, top + n), _strip(bottom, bottom + n)[:, ::-1]]
    if not wrap:
        left, right = idx[:, 0], idx[:, -1]
        faces += [_strip(left, left + n)[:, ::-1], _strip(right, right + n)]
    mesh = trimesh.Trimesh(vertices=verts, faces=np.concatenate(faces), process=False)
    if mesh.volume < 0:  # orientation depends on the shape's handedness; make normals point out
        mesh.invert()
    mesh.export(output_dir / OUTPUT_FILENAME)
    size = [round(float(s), 2) for s in mesh.extents]
    notes = []
    if shape == "cylinder":
        notes.append("Cylinder: no base; stand it over a tea light or LED, or glue it to a printed ring.")
    hint = "print upright (as modelled), relief facing out, 100 % infill, slow outer walls"
    return TemplateResult(
        parts=[TemplatePart(filename=OUTPUT_FILENAME, size_mm=size, print_hint=hint)],
        notes=notes,
        report={"pixels": [inner_w, inner_h], "grid": [cols, rows], "triangles": int(len(mesh.faces))},
    )
