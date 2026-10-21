"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/tool_insert.py
Description: Tool insert from a photo (docs/print-tools-plan.md item 3).
    Two steps, so the server never has to trust its own detection:
      1. trace(): a photo of tools lying on white paper next to a dark
         calibration square -> the paper is found and its perspective
         corrected, the square gives mm per pixel, and every other dark
         shape becomes a simplified outline in mm. The page shows these
         outlines for the user to fix (drag points, add finger notches).
      2. make_insert(): the edited outlines -> a tray with a pocket shaped
         like each tool (outline + clearance, plus round finger notches),
         either just big enough ("fit") or sized to Gridfinity units
         (gridfinity.py). The pocket's top edge can get a stepped chamfer.
    Image processing uses OpenCV (Apache-2.0); geometry uses manifold3d.
Inputs: trace(image_bytes, square_mm, mm_per_px=None); make_insert(output_dir,
    tools, **params) - params mirror ToolInsertRequest in models.py.
Outputs: trace() -> TraceResult (outlines in pixels and mm, a JPEG preview
    of the corrected photo); make_insert() -> tool_insert.stl in
    output_dir and a ToolInsertResult.
Troubleshooting:
    - "calibration square not found": print or cut a solid black square
      of the chosen size, put it flat on the paper away from the tools,
      and keep the whole sheet in frame; or calibrate by hand in the page
      (click two points a known distance apart).
    - Shadows traced as part of a tool: light from straight above or use
      diffuse light; then trim the outline in the page.
    - Tools merged into one outline: leave a finger's width between them.
    - Pocket too tight: raise clearance_mm (0.5 snug, 1 normal, 2 loose).
"""

from __future__ import annotations

import base64
import math
from pathlib import Path

import cv2
import manifold3d as mf
import numpy as np

from threedprompt import geometry, gridfinity
from threedprompt.logging_config import get_logger
from threedprompt.models import SplitPart, ToolInsertResult, TracedTool, TraceResult

logger = get_logger(__name__)

OUTPUT_FILENAME = "tool_insert.stl"
_MAX_SIDE_PX = 2000  # photos are scaled down to this before tracing
_MAX_PIXELS = 60_000_000  # refuse anything bigger than ~60 MP before decoding fully
_SIMPLIFY_MM = 0.4  # outline points are kept within this distance of the traced edge
_MIN_TOOL_FRACTION = 0.002  # shapes smaller than this share of the paper are noise
_MAX_TOOLS = 12
_CHAMFER_STEPS = 4
# Long side / short side of common sheets; the flattened photo is given this shape.
PAPER_ASPECT = {"a4": 297 / 210, "letter": 11 / 8.5, "a3": 420 / 297, "legal": 14 / 8.5}


class ToolInsertError(RuntimeError):
    """Raised for an unreadable photo or an outline that can't become a pocket."""


# --- 1. Photo -> outlines ---


def _decode(image_bytes: bytes) -> np.ndarray:
    """Decode a photo into a BGR array no bigger than _MAX_SIDE_PX on its long side."""
    buf = np.frombuffer(image_bytes, dtype=np.uint8)
    header = cv2.imdecode(buf, cv2.IMREAD_REDUCED_GRAYSCALE_8)  # cheap size check
    if header is None:
        raise ToolInsertError("could not read the photo; use a JPEG or PNG")
    if header.shape[0] * header.shape[1] * 64 > _MAX_PIXELS:
        raise ToolInsertError("photo is too large; use one under about 60 megapixels")
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if img is None:
        raise ToolInsertError("could not read the photo; use a JPEG or PNG")
    scale = _MAX_SIDE_PX / max(img.shape[:2])
    if scale < 1:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return img


def _order_corners(pts: np.ndarray) -> np.ndarray:
    """Order 4 points as top-left, top-right, bottom-right, bottom-left."""
    pts = pts.reshape(4, 2).astype(np.float32)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]], dtype=np.float32)


def _find_paper(img: np.ndarray, aspect: float | None) -> tuple[np.ndarray, bool]:
    """
    Find the sheet of paper (largest bright quadrilateral) and warp it flat.

    Args:
        img: the photo.
        aspect: the sheet's long/short side ratio. Under perspective the
            sides' lengths in the photo don't give the true shape, so
            without it the scale can differ along x and y.
    Returns:
        (image of the paper only, True) or (the whole photo, False) when no
        sheet covering at least a fifth of the photo is found.
    """
    gray = cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    _, bright = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bright = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    contours, _ = cv2.findContours(bright, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return img, False
    sheet = max(contours, key=cv2.contourArea)
    if cv2.contourArea(sheet) < 0.2 * img.shape[0] * img.shape[1]:
        return img, False
    quad = cv2.approxPolyDP(sheet, 0.02 * cv2.arcLength(sheet, True), True)
    if len(quad) != 4:
        quad = cv2.boxPoints(cv2.minAreaRect(sheet))
    src = _order_corners(np.asarray(quad))
    width = int(max(np.linalg.norm(src[1] - src[0]), np.linalg.norm(src[2] - src[3])))
    height = int(max(np.linalg.norm(src[3] - src[0]), np.linalg.norm(src[2] - src[1])))
    if width < 50 or height < 50:
        return img, False
    if aspect:
        long_px = max(width, height)
        short_px = int(round(long_px / aspect))
        width, height = (long_px, short_px) if width >= height else (short_px, long_px)
    dst = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype=np.float32)
    warped = cv2.warpPerspective(img, cv2.getPerspectiveTransform(src, dst), (width, height))
    # Trim a thin border so the sheet's own edge isn't traced as a shape.
    trim = max(2, int(0.01 * min(width, height)))
    return warped[trim:-trim, trim:-trim], True


def _dark_shapes(img: np.ndarray) -> list[np.ndarray]:
    """Outer contours of everything darker than the paper, largest first."""
    gray = cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    _, dark = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    dark = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(dark, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    min_area = _MIN_TOOL_FRACTION * img.shape[0] * img.shape[1]
    return sorted((c for c in contours if cv2.contourArea(c) >= min_area), key=cv2.contourArea, reverse=True)


def _square_score(contour: np.ndarray) -> float | None:
    """How square and solid a contour is (lower is better); None when it isn't a filled square."""
    (_, _), (w, h), _ = cv2.minAreaRect(contour)
    if min(w, h) < 8:
        return None
    aspect = w / h
    solidity = cv2.contourArea(contour) / (w * h)
    if not 0.85 <= aspect <= 1.15 or solidity < 0.9:
        return None
    return abs(1 - aspect) + (1 - solidity)


def trace(image_bytes: bytes, square_mm: float, mm_per_px: float | None = None, paper: str = "a4") -> TraceResult:
    """
    Trace tool outlines from a photo of tools on white paper.

    Args:
        image_bytes: the photo (JPEG/PNG).
        square_mm: side of the dark calibration square in the photo.
        mm_per_px: known scale of the corrected photo; skips the square.
        paper: sheet size (a key of PAPER_ASPECT, or "other" for any sheet;
            "other" is less accurate when the photo is taken at an angle).
    Returns:
        TraceResult with each tool's outline in preview pixels and, when the
        scale is known, in mm (y up, origin at the paper's bottom-left).
    Raises:
        ToolInsertError: the photo can't be read or has no shapes on it.
    """
    img = _decode(image_bytes)
    paper_img, paper_found = _find_paper(img, PAPER_ASPECT.get(paper))
    notes = [] if paper_found else ["Sheet of paper not found; tracing the whole photo without perspective correction."]
    if paper_found and paper not in PAPER_ASPECT:
        notes.append("Paper size 'other': shoot from straight above, or sizes may be off along one axis.")
    paper = paper_img
    shapes = _dark_shapes(paper)
    if not shapes:
        raise ToolInsertError("no tools found on the paper; use plain white paper and a darker tool")

    square_px = None
    if mm_per_px is None:
        scored = [(s, i) for i, c in enumerate(shapes) if (s := _square_score(c)) is not None]
        if scored:
            _, idx = min(scored)
            square = shapes.pop(idx)
            (_, _), (w, h), _ = cv2.minAreaRect(square)
            mm_per_px = square_mm / ((w + h) / 2)
            square_px = cv2.boxPoints(cv2.minAreaRect(square)).round(1).tolist()
        else:
            notes.append("Calibration square not found; click two points a known distance apart to set the scale.")

    height_px = paper.shape[0]
    epsilon_px = _SIMPLIFY_MM / mm_per_px if mm_per_px else 0.002 * max(paper.shape[:2])
    tools = []
    for contour in shapes[:_MAX_TOOLS]:
        poly = cv2.approxPolyDP(contour, epsilon_px, True).reshape(-1, 2).astype(float)
        if len(poly) < 3:
            continue
        outline_mm = None
        if mm_per_px:
            outline_mm = [[round(x * mm_per_px, 2), round((height_px - y) * mm_per_px, 2)] for x, y in poly]
        tools.append(TracedTool(outline_px=poly.round(1).tolist(), outline_mm=outline_mm))
    if len(shapes) > _MAX_TOOLS:
        notes.append(f"Only the {_MAX_TOOLS} largest shapes were kept.")

    ok, jpeg = cv2.imencode(".jpg", paper, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:  # should not happen for a decoded image; surface it rather than return no preview
        raise ToolInsertError("could not encode the preview image")
    return TraceResult(
        mm_per_px=round(mm_per_px, 6) if mm_per_px else None,
        paper_found=paper_found,
        square_px=square_px,
        width_px=paper.shape[1],
        height_px=height_px,
        preview=f"data:image/jpeg;base64,{base64.b64encode(jpeg.tobytes()).decode()}",
        tools=tools,
        notes=notes,
    )


# --- 2. Outlines -> tray ---


def _tool_shape(outline: list[list[float]], notches: list[dict]) -> mf.CrossSection:
    """One tool's footprint: its outline (any winding) plus round finger notches."""
    try:
        pts = np.asarray(outline, dtype=float)
    except ValueError as exc:  # ragged point lists
        raise ToolInsertError("every outline point must be [x, y]") from exc
    if pts.ndim != 2 or pts.shape[1] != 2 or len(pts) < 3 or not np.isfinite(pts).all():
        raise ToolInsertError("every outline needs at least 3 finite [x, y] points")
    # Positive fill rule accepts either winding; self-intersections resolve to the filled area.
    shape = mf.CrossSection([pts], mf.FillRule.NonZero)
    if shape.area() < 1.0:
        raise ToolInsertError("an outline encloses almost no area; check its points")
    for n in notches:
        circle = mf.CrossSection.circle(n["d"] / 2, 48).translate((n["x"], n["y"]))
        shape = shape + circle
    return shape


def make_insert(
    output_dir: Path,
    tools: list[dict],
    *,
    clearance_mm: float,
    depth_mm: float,
    floor_mm: float,
    wall_mm: float,
    tray: str,
    corner_radius_mm: float,
    chamfer_mm: float,
    height_units: int | None = None,
) -> ToolInsertResult:
    """
    Build a tray with a pocket for each tool and write it to tool_insert.stl.

    Args:
        output_dir: where the STL goes.
        tools: [{"outline_mm": [[x, y], ...], "notches": [{"x", "y", "d"}, ...]}], in mm.
        clearance_mm: gap added around each outline.
        depth_mm: pocket depth.
        floor_mm: material left under the pockets.
        wall_mm: minimum material between pockets and the tray edge.
        tray: "fit" (a rounded box around the pockets) or "gridfinity".
        corner_radius_mm: corner radius of a "fit" tray.
        chamfer_mm: stepped chamfer on the pocket's top edge (0 = none).
        height_units: Gridfinity height in 7 mm units (None = just tall enough).
    Returns:
        ToolInsertResult describing the tray.
    Raises:
        ToolInsertError: bad outlines or settings that leave no floor.
    """
    if not tools:
        raise ToolInsertError("add at least one tool outline")
    notes: list[str] = []
    shapes = [_tool_shape(t["outline_mm"], t.get("notches", [])) for t in tools]
    footprint = mf.CrossSection.batch_boolean(shapes, mf.OpType.Add)
    if footprint.area() < sum(s.area() for s in shapes) - 1.0:
        notes.append("Some outlines overlap; their pockets were merged.")
    # simplify() drops near-duplicate points the offset leaves where shapes meet; they would
    # otherwise become coincident vertices in the STL, which slicers report as non-manifold.
    pocket = footprint.offset(clearance_mm, mf.JoinType.Round, circular_segments=48).simplify(0.01)
    (x0, y0), (x1, y1) = pocket.bounds()[:2], pocket.bounds()[2:]
    pocket = pocket.translate((-(x0 + x1) / 2, -(y0 + y1) / 2))  # centre the pockets on the tray
    span_x, span_y = x1 - x0 + 2 * chamfer_mm, y1 - y0 + 2 * chamfer_mm

    grid = None
    if tray == "gridfinity":
        nx = max(1, math.ceil((span_x + 2 * wall_mm) / gridfinity.GRID_MM))
        ny = max(1, math.ceil((span_y + 2 * wall_mm) / gridfinity.GRID_MM))
        needed = gridfinity.FOOT_HEIGHT_MM + floor_mm + depth_mm
        units = max(2, math.ceil(needed / gridfinity.HEIGHT_UNIT_MM))
        if height_units is not None:
            if height_units < units:
                notes.append(f"{height_units} height units is too short for this depth; used {units}.")
            units = max(units, height_units)
        top = units * gridfinity.HEIGHT_UNIT_MM
        solid = gridfinity.base(nx, ny) + gridfinity.body(nx, ny, top)
        grid = [nx, ny, units]
        notes.append("Gridfinity insert without a stacking lip, so it sits flush; magnet holes are not added.")
    else:
        outline = gridfinity.rounded_rect(span_x + 2 * wall_mm, span_y + 2 * wall_mm, corner_radius_mm)
        top = floor_mm + depth_mm
        solid = mf.Manifold.extrude(outline, top)

    cut_from = top - depth_mm
    # The pocket starts 0.5 mm above the top so the cut never leaves a skin.
    cuts = [mf.Manifold.extrude(pocket, depth_mm + 0.5).translate((0, 0, cut_from))]
    if chamfer_mm > 0:
        step = chamfer_mm / _CHAMFER_STEPS
        for i in range(1, _CHAMFER_STEPS + 1):
            ring = pocket.offset(i * step, mf.JoinType.Round, circular_segments=48).simplify(0.01)
            cuts.append(
                mf.Manifold.extrude(ring, chamfer_mm - (i - 1) * step + 0.5).translate(
                    (0, 0, top - chamfer_mm + (i - 1) * step)
                )
            )
    insert = solid - mf.Manifold.batch_boolean(cuts, mf.OpType.Add)
    if insert.status() != mf.Error.NoError or insert.is_empty():
        raise ToolInsertError(f"could not build the tray ({insert.status().name})")

    size_mm = geometry.save(insert, output_dir / OUTPUT_FILENAME)
    logger.info("tool insert: %d tool(s), tray %s, size %s", len(tools), tray, size_mm)
    return ToolInsertResult(
        parts=[
            SplitPart(
                filename=OUTPUT_FILENAME, role="piece", size_mm=size_mm, fits=True, print_hint="print flat, pockets up"
            )
        ],
        tray=tray,
        grid_units=grid,
        size_mm=size_mm,
        pocket_depth_mm=depth_mm,
        floor_mm=round(cut_from - (gridfinity.FOOT_HEIGHT_MM if tray == "gridfinity" else 0.0), 2),
        pocket_area_mm2=round(pocket.area(), 1),
        notes=notes,
    )
