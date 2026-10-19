"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/threedprompt/terrain.py
Description: Tabletop terrain tiles from a seeded height field: rolling
    hills, ridges, craters or mesas, optionally stepped into flat terraces
    so miniatures can stand, with the edges eased down to the base height
    so any two tiles butt together, and optional magnet holes underneath
    to join them. Same seed + settings = same tile.
Inputs: make_terrain(output_dir, **params) - params mirror TerrainRequest.
Outputs: terrain_tile.stl and a report (heights, how much is flat enough
    to stand a miniature on).
Troubleshooting:
    - Miniatures tip over: turn on terraces (terrace_mm) or lower
      max_height_mm; the report's standable_percent is the area under
      15 degrees.
    - Tiles don't line up: keep edge_fade_mm > 0, so every edge is at the
      base height whatever the seed.
    - File too big / slow: raise resolution_mm (1.5-2 mm is plenty for
      28-32 mm scale).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import trimesh

from threedprompt import geometry
from threedprompt.models import TemplatePart, TemplateResult

TERRAIN_FILENAME = "terrain_tile.stl"
STANDABLE_DEG = 15.0  # a 25 mm base stands on slopes under about this
MAGNET_INSET_MM = 8.0  # magnet hole centres from each corner, along both edges


def _value_noise(rng: np.random.Generator, nx: int, ny: int, cells: int) -> np.ndarray:
    """Smooth random field on an (ny, nx) grid: random values on a cells x cells lattice, smoothstep-blended."""
    lattice = rng.random((cells + 1, cells + 1))
    u = np.linspace(0, cells, nx, endpoint=False) + cells / nx / 2
    v = np.linspace(0, cells, ny, endpoint=False) + cells / ny / 2
    i, j = np.floor(u).astype(int), np.floor(v).astype(int)
    fu, fv = u - i, v - j
    su, sv = fu * fu * (3 - 2 * fu), fv * fv * (3 - 2 * fv)
    a = lattice[np.ix_(j, i)]
    b = lattice[np.ix_(j, i + 1)]
    c = lattice[np.ix_(j + 1, i)]
    d = lattice[np.ix_(j + 1, i + 1)]
    top = a + (b - a) * su[None, :]
    bottom = c + (d - c) * su[None, :]
    return top + (bottom - top) * sv[:, None]


def _fbm(rng: np.random.Generator, nx: int, ny: int, base_cells: int, octaves: int, roughness: float) -> np.ndarray:
    """Fractal sum of value-noise octaves, normalised to 0..1."""
    field = np.zeros((ny, nx))
    amp, total = 1.0, 0.0
    for o in range(octaves):
        field += amp * _value_noise(rng, nx, ny, base_cells * 2**o)
        total += amp
        amp *= roughness
    field /= total
    return (field - field.min()) / max(np.ptp(field), 1e-9)


def height_field(
    *,
    style: str,
    nx: int,
    ny: int,
    width_mm: float,
    depth_mm: float,
    seed: int,
    roughness: float,
    feature_mm: float,
) -> np.ndarray:
    """Relative heights 0..1 on an (ny, nx) grid for one terrain style."""
    rng = np.random.default_rng(seed)
    base_cells = max(1, round(max(width_mm, depth_mm) / feature_mm))
    if style == "hills":  # few octaves: rolling, not gravel
        return _fbm(rng, nx, ny, base_cells, 3, roughness) ** 1.6
    if style == "ridges":
        r = 1 - np.abs(2 * _fbm(rng, nx, ny, base_cells, 5, roughness) - 1)
        return r**2
    if style == "mesa":
        # Flat tops on steep sides: a hard sigmoid of a smooth field, no fine detail on top.
        n = _fbm(rng, nx, ny, base_cells, 2, roughness)
        h = 1 / (1 + np.exp(-25 * (n - np.quantile(n, 0.6))))
        return (h - h.min()) / max(np.ptp(h), 1e-9)
    n = _fbm(rng, nx, ny, base_cells, 4, roughness)
    if style == "craters":
        x = np.linspace(0, width_mm, nx)[None, :]
        y = np.linspace(0, depth_mm, ny)[:, None]
        h = 0.35 * n + 0.45
        for _ in range(int(rng.integers(2, 5))):
            r = rng.uniform(0.12, 0.28) * min(width_mm, depth_mm)
            cx, cy = rng.uniform(r, width_mm - r), rng.uniform(r, depth_mm - r)
            d = np.hypot(x - cx, y - cy) / r
            bowl = np.where(d < 1, -0.45 * (1 - d**2), 0.0)
            rim = 0.3 * np.exp(-(((d - 1.0) / 0.18) ** 2))
            h = h + bowl + rim
        return (h - h.min()) / max(np.ptp(h), 1e-9)
    raise geometry.TemplateError(f"unknown terrain style {style!r}")


def _edge_fade(nx: int, ny: int, width_mm: float, depth_mm: float, fade_mm: float) -> np.ndarray:
    """0 at every edge, rising smoothly to 1 at fade_mm in, so tiles meet at the base height."""
    if fade_mm <= 0:
        return np.ones((ny, nx))
    x = np.linspace(0, width_mm, nx)
    y = np.linspace(0, depth_mm, ny)
    dx = np.minimum(x, width_mm - x)[None, :]
    dy = np.minimum(y, depth_mm - y)[:, None]
    t = np.clip(np.minimum(dx, dy) / fade_mm, 0, 1)
    return t * t * (3 - 2 * t)


def tile_mesh(z: np.ndarray, width_mm: float, depth_mm: float) -> trimesh.Trimesh:
    """Closed solid: the height field z (top, mm) over a flat bottom at z=0, with vertical sides."""
    ny, nx = z.shape
    xs, ys = np.meshgrid(np.linspace(0, width_mm, nx), np.linspace(0, depth_mm, ny))
    top = np.column_stack([xs.ravel(), ys.ravel(), z.ravel()])
    bottom = np.column_stack([xs.ravel(), ys.ravel(), np.zeros(nx * ny)])
    verts = np.vstack([top, bottom])
    off = nx * ny
    idx = np.arange(nx * ny).reshape(ny, nx)
    a, b, c, d = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel(), idx[1:, :-1].ravel(), idx[1:, 1:].ravel()
    faces = [np.column_stack([a, b, d]), np.column_stack([a, d, c])]  # top, normals up
    faces += [np.column_stack([a, d, b]) + off, np.column_stack([a, c, d]) + off]  # bottom, normals down
    # Sides: walk the boundary counter-clockwise (seen from above), one quad per edge segment.
    ring = np.concatenate([idx[0, :], idx[1:, -1], idx[-1, -2::-1], idx[-2:0:-1, 0]])
    p, q = ring, np.roll(ring, -1)
    faces += [np.column_stack([p, p + off, q]), np.column_stack([q, p + off, q + off])]
    mesh = trimesh.Trimesh(vertices=verts, faces=np.vstack(faces), process=False)
    trimesh.repair.fix_normals(mesh)
    return mesh


def make_terrain(
    output_dir: Path,
    *,
    style: str,
    width_mm: float,
    depth_mm: float,
    base_mm: float,
    max_height_mm: float,
    feature_mm: float,
    roughness: float,
    terrace_mm: float,
    edge_fade_mm: float,
    resolution_mm: float,
    seed: int,
    magnet_holes: bool,
    magnet_diameter_mm: float,
    magnet_depth_mm: float,
) -> TemplateResult:
    """
    Build one terrain tile and write it to terrain_tile.stl.

    Args:
        output_dir: where the STL goes.
        style: "hills", "ridges", "craters" or "mesa".
        width_mm, depth_mm: tile size (152.4 = 6 in, 304.8 = 12 in).
        base_mm: solid slab under the terrain (also the edge height).
        max_height_mm: the highest point above the base.
        feature_mm: rough size of one hill / valley.
        roughness: 0.3 smooth .. 0.7 rocky (fractal gain per octave).
        terrace_mm: step the surface into flat levels this tall (0 = smooth).
        edge_fade_mm: ease the terrain down to the base this far from each edge.
        resolution_mm: grid spacing of the height field.
        seed: same seed + settings = same tile.
        magnet_holes: holes in the bottom near each corner, to join tiles.
        magnet_diameter_mm, magnet_depth_mm: size of those holes.
    Returns:
        TemplateResult with the one part and a report.
    Raises:
        geometry.TemplateError: settings that can't make a tile.
    """
    if magnet_holes and magnet_depth_mm >= base_mm - 0.6:
        raise geometry.TemplateError("the magnet holes would go through the base; make the base thicker")
    nx = int(round(width_mm / resolution_mm)) + 1
    ny = int(round(depth_mm / resolution_mm)) + 1
    if nx * ny > 400_000:
        raise geometry.TemplateError("too fine for this tile size; raise resolution_mm")
    rel = height_field(
        style=style,
        nx=nx,
        ny=ny,
        width_mm=width_mm,
        depth_mm=depth_mm,
        seed=seed,
        roughness=roughness,
        feature_mm=feature_mm,
    )
    h = rel * max_height_mm * _edge_fade(nx, ny, width_mm, depth_mm, edge_fade_mm)
    if terrace_mm > 0:
        h = np.floor(h / terrace_mm) * terrace_mm
    z = base_mm + h
    solid = geometry.to_manifold(tile_mesh(z, width_mm, depth_mm))
    notes: list[str] = []
    if magnet_holes:
        holes = [
            geometry.cylinder(magnet_diameter_mm / 2, magnet_depth_mm + 0.5, -0.5, 48).translate((x, y, 0))
            for x in (MAGNET_INSET_MM, width_mm - MAGNET_INSET_MM)
            for y in (MAGNET_INSET_MM, depth_mm - MAGNET_INSET_MM)
        ]
        for hole in holes:
            solid = solid - hole
        notes.append("Magnet holes are in the bottom corners: alternate the poles so any two tiles attract.")
    size_mm = geometry.save(solid, output_dir / TERRAIN_FILENAME)
    gy, gx = np.gradient(h, resolution_mm)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    standable = float((slope < STANDABLE_DEG).mean() * 100)
    if standable < 50:
        notes.append(f"Only {standable:.0f} % is flat enough for a miniature; try terraces or a lower max height.")
    return TemplateResult(
        parts=[TemplatePart(filename=TERRAIN_FILENAME, size_mm=size_mm, print_hint="flat side down, no supports")],
        notes=notes,
        report={
            "style": style,
            "seed": seed,
            "grid": [nx, ny],
            "highest_mm": round(float(z.max()), 2),
            "standable_percent": round(standable, 1),
            "volume_cm3": round(solid.volume() / 1000, 1),
        },
    )
