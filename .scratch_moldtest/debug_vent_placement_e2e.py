"""
End-to-end check: build a real direct_cast mold around a model with an
off-center bump, then ray-cast into the exported top half to confirm the
vent hole was actually cut near the bump's apex (not at the old fixed
0.35*cavity_size offset), while the sprue is still centered.

Run: blender --background --python debug_vent_placement_e2e.py
(this script itself calls blender.exe as a subprocess to run the real
make_mold.py CLI, then re-launches blender to inspect the result - same
harness shape as debug_offset_investigate.py)
"""
import json
import os
import struct
import subprocess
import tempfile

OUT_DIR = os.path.join(tempfile.gettempdir(), "debug_vent_e2e_out")
STL_PATH = os.path.join(tempfile.gettempdir(), "debug_vent_e2e_model.stl")
REPORT = os.path.join(tempfile.gettempdir(), "debug_vent_e2e_report.json")
BLENDER = r"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe"
SCRIPT = r"D:\3d-printing-model-prompt\src\threedprompt\blender_scripts\make_mold.py"

# 20x20x6mm box (spanning -10..10 in X/Y, 0..6 in Z) with one top corner
# lifted to (8, -8, 9) - an unambiguous off-center bump, far enough from
# the sprue (centered at 0,0) to survive the exclude-radius filter.
verts = {
    "000": (-10.0, -10.0, 0.0),
    "100": (10.0, -10.0, 0.0),
    "010": (-10.0, 10.0, 0.0),
    "110": (10.0, 10.0, 0.0),
    "001": (-10.0, -10.0, 6.0),
    "101": (10.0, -10.0, 6.0),
    "011": (-10.0, 10.0, 6.0),
    "111": (8.0, -8.0, 9.0),  # lifted corner = the bump apex
}
tris = [
    ("000", "010", "110"), ("000", "110", "100"),
    ("001", "101", "111"), ("001", "111", "011"),
    ("000", "100", "101"), ("000", "101", "001"),
    ("010", "011", "111"), ("010", "111", "110"),
    ("000", "001", "011"), ("000", "011", "010"),
    ("100", "110", "111"), ("100", "111", "101"),
]
with open(STL_PATH, "wb") as f:
    f.write(b"\x00" * 80)
    f.write(struct.pack("<I", len(tris)))
    for a, b, c in tris:
        f.write(struct.pack("<3f", 0.0, 0.0, 0.0))
        for v in (verts[a], verts[b], verts[c]):
            f.write(struct.pack("<3f", *v))
        f.write(struct.pack("<H", 0))

os.makedirs(OUT_DIR, exist_ok=True)
cmd = [
    BLENDER, "--background", "--python", SCRIPT, "--",
    "--input", STL_PATH,
    "--output-dir", OUT_DIR,
    "--report-output", REPORT,
    "--mode", "direct_cast",
    "--direct-mold-wall-mm", "3.0",
    "--parting-axis", "z",
    "--parting-offset-mm", "0.0",
    "--shell-thickness-mm", "3.0",
    "--skin-pour-wall-mm", "3.0",
    "--support-jacket-wall-mm", "5.0",
    "--cast-wall-thickness-mm", "4.0",
    "--clearance-mm", "8.0",
    "--pour-box-wall-mm", "4.0",
    "--key-diameter-mm", "6.0",
    "--sprue-diameter-mm", "3.0",
    "--vent-diameter-mm", "1.5",
    "--clamp-wall-mm", "6.0",
    "--clamp-flange-width-mm", "12.0",
    "--bolt-hole-diameter-mm", "4.5",
    "--max-dimension-mm", "300.0",
]
result = subprocess.run(cmd, capture_output=True, text=True)
print("rc", result.returncode)
print("STDERR:", result.stderr)
with open(REPORT) as f:
    report = json.load(f)
print(report)

verify = os.path.join(tempfile.gettempdir(), "debug_vent_e2e_verify.py")
with open(verify, "w") as vf:
    vf.write(f"""
import bpy
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.wm.stl_import(filepath=r"{report['paths']['direct_mold_top']}")
obj = bpy.context.selected_objects[0]
dg = bpy.context.evaluated_depsgraph_get()

def hits_solid(x, y, z_start=20.0, z_end=-1.0):
    origin = (x, y, z_start)
    direction = (0.0, 0.0, z_end - z_start)
    success, loc, normal, idx = obj.ray_cast(origin, direction)
    return success

fallback_xy = (0.35 * 20.0, 0.35 * 20.0)
bump_xy = (8.0, -8.0)
sprue_xy = (0.0, 0.0)

print("sprue location (must be a hole, no hit):", hits_solid(*sprue_xy))
print("bump apex location (must be a hole, no hit):", hits_solid(*bump_xy))
print("old fixed-offset location (must be solid, hit expected):", hits_solid(*fallback_xy))
""")
vr = subprocess.run([BLENDER, "--background", "--python", verify], capture_output=True, text=True)
print(vr.stdout)
print(vr.stderr[-2000:])
