"""
Throwaway verification script (not part of the test suite) for Phase 1's
direct_cast mold mode: builds a direct-cast mold for a known unit-cube
fixture, then ray-casts into each half's would-be cavity region to
confirm the model's own volume is genuinely hollowed out (not a bug that
leaves the box solid, or a bug that fails to carve anything at all).
Run: blender --background --python debug_direct_cast.py
"""

import os
import subprocess
import sys
import tempfile

CUBE_STL = os.path.join(tempfile.gettempdir(), "debug_direct_cast_cube.stl")
OUT_DIR = os.path.join(tempfile.gettempdir(), "debug_direct_cast_out")
REPORT = os.path.join(tempfile.gettempdir(), "debug_direct_cast_report.json")


def _write_unit_cube_stl(path):
    import struct

    verts = {
        "000": (0.0, 0.0, 0.0), "100": (1.0, 0.0, 0.0), "010": (0.0, 1.0, 0.0), "110": (1.0, 1.0, 0.0),
        "001": (0.0, 0.0, 1.0), "101": (1.0, 0.0, 1.0), "011": (0.0, 1.0, 1.0), "111": (1.0, 1.0, 1.0),
    }
    tris = [
        ("000", "010", "110"), ("000", "110", "100"),
        ("001", "101", "111"), ("001", "111", "011"),
        ("000", "100", "101"), ("000", "101", "001"),
        ("010", "011", "111"), ("010", "111", "110"),
        ("000", "001", "011"), ("000", "011", "010"),
        ("100", "110", "111"), ("100", "111", "101"),
    ]
    with open(path, "wb") as f:
        f.write(b"\x00" * 80)
        f.write(struct.pack("<I", len(tris)))
        for a, b, c in tris:
            f.write(struct.pack("<3f", 0.0, 0.0, 0.0))
            for v in (verts[a], verts[b], verts[c]):
                f.write(struct.pack("<3f", *v))
            f.write(struct.pack("<H", 0))


_write_unit_cube_stl(CUBE_STL)
os.makedirs(OUT_DIR, exist_ok=True)

repo_root = r"D:\3d-printing-model-prompt"
script = os.path.join(repo_root, "src", "threedprompt", "blender_scripts", "make_mold.py")
blender = r"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe"

cmd = [
    blender, "--background", "--python", script, "--",
    "--input", CUBE_STL,
    "--output-dir", OUT_DIR,
    "--report-output", REPORT,
    "--mode", "direct_cast",
    "--direct-mold-wall-mm", "3.0",
    "--clearance-mm", "5.0",
    "--pour-box-wall-mm", "3.0",
    "--key-diameter-mm", "6.0",
    "--sprue-diameter-mm", "10.0",
    "--vent-diameter-mm", "4.0",
    "--clamp-wall-mm", "4.0",
    "--clamp-flange-width-mm", "8.0",
    "--bolt-hole-diameter-mm", "4.5",
    "--max-dimension-mm", "300.0",
]
result = subprocess.run(cmd, capture_output=True, text=True)
print("returncode:", result.returncode)
print("stdout tail:\n", "\n".join(result.stdout.splitlines()[-30:]))
print("stderr tail:\n", "\n".join(result.stderr.splitlines()[-30:]))

import json

with open(REPORT) as f:
    report = json.load(f)
print("report:", report)
if "error" in report:
    sys.exit(1)

# Now ray-cast into the bottom half's would-be cavity to confirm it's
# genuinely open (a point at the cube's own center, inside its bottom
# half, should NOT register as solid material).
bottom_path = report["paths"]["direct_mold_bottom"]

verify_script = os.path.join(tempfile.gettempdir(), "debug_direct_cast_verify.py")
with open(verify_script, "w") as f:
    f.write(
        f"""
import bpy
import bmesh
from mathutils import Vector
from mathutils.bvhtree import BVHTree

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.wm.stl_import(filepath=r"{bottom_path}")
obj = bpy.context.selected_objects[0]
bm = bmesh.new()
bm.from_mesh(obj.data)
bm.transform(obj.matrix_world)
bvh = BVHTree.FromBMesh(bm)

def is_inside(point):
    direction = Vector((0, 0, 1))
    hits = 0
    origin = Vector(point)
    while True:
        hit = bvh.ray_cast(origin, direction)
        if hit[0] is None:
            break
        hits += 1
        origin = hit[0] + direction * 1e-4
    return hits % 2 == 1

# Cube center (0.5, 0.5, 0.5) -> its bottom half occupies z in [0, 0.5].
# A point at (0.5, 0.5, 0.2) sits inside the cube's own volume, which the
# direct-cast cavity should have removed entirely from the bottom half.
cavity_point = (0.5, 0.5, 0.2)
# A point just outside the cube but still within the mold's own wall
# material (wall=3mm around a 1mm cube) should read as solid.
wall_point = (0.5, 0.5, -1.5)

print("CAVITY_POINT_INSIDE_SOLID=", is_inside(cavity_point))
print("WALL_POINT_INSIDE_SOLID=", is_inside(wall_point))
print("VERT_COUNT=", len(bm.verts))
bbox_min = [min(v.co[i] for v in bm.verts) for i in range(3)]
bbox_max = [max(v.co[i] for v in bm.verts) for i in range(3)]
print("BBOX_MIN=", bbox_min)
print("BBOX_MAX=", bbox_max)
"""
    )

verify_result = subprocess.run(
    [blender, "--background", "--python", verify_script], capture_output=True, text=True
)
print("verify stdout:\n", verify_result.stdout)
print("verify stderr tail:\n", "\n".join(verify_result.stderr.splitlines()[-20:]))
