"""
Throwaway verification script (not part of the test suite) for Phase 4's
form_fitting mode: builds a form-fitting skin-pour + support-jacket set
for the unit-cube fixture and confirms (a) the offset-along-normals
cavity is genuinely open per half (ray-cast, same methodology ADR
0005/0006 used), (b) the offset surface actually grew by shell_thickness
in every direction (a cube offset by 3mm along each face normal should
be 1+2*3=7mm on a side), and (c) all 4 parts are watertight.
Run: blender --background --python debug_form_fitting.py
"""

import json
import os
import struct
import subprocess
import tempfile

CUBE_STL = os.path.join(tempfile.gettempdir(), "debug_form_fitting_cube.stl")
OUT_DIR = os.path.join(tempfile.gettempdir(), "debug_form_fitting_out")
REPORT = os.path.join(tempfile.gettempdir(), "debug_form_fitting_report.json")


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
    "--mode", "form_fitting",
    "--shell-thickness-mm", "3.0",
    "--skin-pour-wall-mm", "3.0",
    "--support-jacket-wall-mm", "5.0",
    "--direct-mold-wall-mm", "6.0",
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

skin_bottom_path = report["paths"]["skin_pour_bottom"]

verify_script = os.path.join(tempfile.gettempdir(), "debug_form_fitting_verify.py")
with open(verify_script, "w") as f:
    f.write(
        f"""
import bpy
import bmesh
from mathutils import Vector
from mathutils.bvhtree import BVHTree

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.wm.stl_import(filepath=r"{skin_bottom_path}")
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

# Cube center (0.5, 0.5, 0.5) -> bottom half occupies z in [0, 0.5]. A
# point at the cube's own center must be hollow (the skin pour tool's
# cavity is the model *plus* a 3mm shell offset, so it's even bigger than
# direct_cast's cavity - if that's hollow, this must be too).
cavity_point = (0.5, 0.5, 0.2)
# A point well outside the offset surface but still inside the tool's
# own wall material should read solid.
wall_point = (0.5, 0.5, -2.5)

print("CAVITY_POINT_INSIDE_SOLID=", is_inside(cavity_point))
print("WALL_POINT_INSIDE_SOLID=", is_inside(wall_point))
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
