import json
import os
import subprocess
import tempfile

CUBE_STL = os.path.join(tempfile.gettempdir(), "debug_investigate_cube.stl")
OUT_DIR = os.path.join(tempfile.gettempdir(), "debug_investigate_out")
REPORT = os.path.join(tempfile.gettempdir(), "debug_investigate_report.json")

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
with open(CUBE_STL, "wb") as f:
    f.write(b"\x00" * 80)
    f.write(struct.pack("<I", len(tris)))
    for a, b, c in tris:
        f.write(struct.pack("<3f", 0.0, 0.0, 0.0))
        for v in (verts[a], verts[b], verts[c]):
            f.write(struct.pack("<3f", *v))
        f.write(struct.pack("<H", 0))

os.makedirs(OUT_DIR, exist_ok=True)
script = r"D:\3d-printing-model-prompt\src\threedprompt\blender_scripts\make_mold.py"
blender = r"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe"

cmd = [
    blender, "--background", "--python", script, "--",
    "--input", CUBE_STL,
    "--output-dir", OUT_DIR,
    "--report-output", REPORT,
    "--mode", "direct_cast",
    "--direct-mold-wall-mm", "3.0",
    "--parting-axis", "z",
    "--parting-offset-mm", "0.2",
    "--shell-thickness-mm", "3.0",
    "--skin-pour-wall-mm", "3.0",
    "--support-jacket-wall-mm", "5.0",
    "--cast-wall-thickness-mm", "4.0",
    "--clearance-mm", "8.0",
    "--pour-box-wall-mm", "4.0",
    "--key-diameter-mm", "6.0",
    "--sprue-diameter-mm", "0.4",
    "--vent-diameter-mm", "0.2",
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

verify = os.path.join(tempfile.gettempdir(), "debug_investigate_verify.py")
with open(verify, "w") as f:
    f.write(f"""
import bpy
def bbox(path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.wm.stl_import(filepath=path)
    obj = bpy.context.selected_objects[0]
    zs = [v.co.z for v in obj.data.vertices]
    return min(zs), max(zs)
print("BOTTOM", bbox(r"{report['paths']['direct_mold_bottom']}"))
print("TOP", bbox(r"{report['paths']['direct_mold_top']}"))
""")
vr = subprocess.run([blender, "--background", "--python", verify], capture_output=True, text=True)
print(vr.stdout)
