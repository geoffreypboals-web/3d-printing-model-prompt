"""
Throwaway end-to-end verification (not part of the test suite) for
Phase 5's hollow_cast mode: runs make_mold.py's real CLI against the
unit-cube fixture, then ray-casts into hollow_cast_bottom.stl to confirm
the cavity is genuinely open (same methodology as debug_direct_cast.py /
debug_form_fitting.py), and checks the core is a smaller, separate solid
nested inside that cavity's footprint.
Run: blender --background --python debug_hollow_cast_full.py
"""

import json
import os
import struct
import subprocess
import tempfile

CUBE_STL = os.path.join(tempfile.gettempdir(), "debug_hollow_cast_cube.stl")
OUT_DIR = os.path.join(tempfile.gettempdir(), "debug_hollow_cast_out")
REPORT = os.path.join(tempfile.gettempdir(), "debug_hollow_cast_report.json")


def _write_unit_cube_stl(path):
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
    "--mode", "hollow_cast",
    "--direct-mold-wall-mm", "3.0",
    "--cast-wall-thickness-mm", "0.1",
    "--shell-thickness-mm", "3.0",
    "--skin-pour-wall-mm", "3.0",
    "--support-jacket-wall-mm", "5.0",
    "--clearance-mm", "8.0",
    "--pour-box-wall-mm", "4.0",
    "--key-diameter-mm", "6.0",
    "--sprue-diameter-mm", "10.0",
    "--vent-diameter-mm", "4.0",
    "--clamp-wall-mm", "6.0",
    "--clamp-flange-width-mm", "8.0",
    "--bolt-hole-diameter-mm", "4.5",
    "--max-dimension-mm", "300.0",
]
result = subprocess.run(cmd, capture_output=True, text=True)
print("returncode:", result.returncode)
print("stdout tail:\n", "\n".join(result.stdout.splitlines()[-30:]))
print("stderr tail:\n", "\n".join(result.stderr.splitlines()[-30:]))

with open(REPORT) as f:
    report = json.load(f)
print("report:", report)
if "error" in report:
    raise SystemExit(1)

bottom_path = report["paths"]["hollow_cast_bottom"]
core_path = report["paths"]["hollow_cast_core"]

verify_script = os.path.join(tempfile.gettempdir(), "debug_hollow_cast_verify.py")
with open(verify_script, "w") as f:
    f.write(
        f"""
import bpy
import bmesh
from mathutils import Vector
from mathutils.bvhtree import BVHTree

def load(path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.wm.stl_import(filepath=path)
    obj = bpy.context.selected_objects[0]
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.transform(obj.matrix_world)
    return bm

bottom_bm = load(r"{bottom_path}")
bvh = BVHTree.FromBMesh(bottom_bm)

def is_inside(bvh, point):
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

cavity_point = (0.5, 0.5, 0.2)
wall_point = (0.5, 0.5, -2.5)
print("CAVITY_POINT_INSIDE_SOLID=", is_inside(bvh, cavity_point))
print("WALL_POINT_INSIDE_SOLID=", is_inside(bvh, wall_point))

core_bm = load(r"{core_path}")
core_bbox_min = [min(v.co[i] for v in core_bm.verts) for i in range(3)]
core_bbox_max = [max(v.co[i] for v in core_bm.verts) for i in range(3)]
print("CORE_BBOX_MIN=", core_bbox_min)
print("CORE_BBOX_MAX=", core_bbox_max)
print("CORE_VOLUME=", core_bm.calc_volume(signed=True))
"""
    )

verify_result = subprocess.run(
    [blender, "--background", "--python", verify_script], capture_output=True, text=True
)
print("verify stdout:\n", verify_result.stdout)
print("verify stderr tail:\n", "\n".join(verify_result.stderr.splitlines()[-20:]))
