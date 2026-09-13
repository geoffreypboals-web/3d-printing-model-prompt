"""
Throwaway end-to-end verification (not part of the test suite) for
Phase 6: configurable parting axis/offset (FR-6) and the casting-volume
calculator (FR-8). Uses a non-cubic 2x3x4mm box fixture (X=2, Y=3, Z=4)
so axis swaps and asymmetric splits are unambiguous, and checks:
  1. direct_cast with default parting_axis="z", offset=0 -> symmetric
     halves, cavity_volume_mm3 == 2*3*4 == 24 (the box's own volume).
  2. direct_cast with parting_axis="x" -> the model is rotated so X
     becomes Z internally; the resulting mold's own bbox should reflect
     the swapped extents (X<->Z), same math as debug_parting_axis.py.
  3. direct_cast with parting_offset_mm=1.0 (axis z) -> bottom half taller
     than top half by exactly 2mm (offset shifts the split by 1mm, so one
     side gains 1mm and the other loses 1mm -> 2mm difference).
  4. silicone_block cavity_volume_mm3 == analytic box-cavity volume.
Run: blender --background --python debug_parting_and_volume.py
"""

import json
import os
import struct
import subprocess
import tempfile

BOX_STL = os.path.join(tempfile.gettempdir(), "debug_parting_box.stl")
OUT_DIR = os.path.join(tempfile.gettempdir(), "debug_parting_out")
REPORT = os.path.join(tempfile.gettempdir(), "debug_parting_report.json")


def _write_box_stl(path, sx, sy, sz):
    """Axis-aligned box from (0,0,0) to (sx,sy,sz)."""
    verts = {
        "000": (0.0, 0.0, 0.0), "100": (sx, 0.0, 0.0), "010": (0.0, sy, 0.0), "110": (sx, sy, 0.0),
        "001": (0.0, 0.0, sz), "101": (sx, 0.0, sz), "011": (0.0, sy, sz), "111": (sx, sy, sz),
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


_write_box_stl(BOX_STL, 2.0, 3.0, 4.0)
os.makedirs(OUT_DIR, exist_ok=True)

repo_root = r"D:\3d-printing-model-prompt"
script = os.path.join(repo_root, "src", "threedprompt", "blender_scripts", "make_mold.py")
blender = r"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe"

BASE_ARGS = [
    "--shell-thickness-mm", "3.0",
    "--skin-pour-wall-mm", "3.0",
    "--support-jacket-wall-mm", "5.0",
    "--cast-wall-thickness-mm", "0.1",
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


def run(mode, direct_mold_wall_mm, parting_axis, parting_offset_mm, out_subdir):
    out_dir = os.path.join(OUT_DIR, out_subdir)
    os.makedirs(out_dir, exist_ok=True)
    report_path = os.path.join(out_dir, "report.json")
    cmd = [
        blender, "--background", "--python", script, "--",
        "--input", BOX_STL,
        "--output-dir", out_dir,
        "--report-output", report_path,
        "--mode", mode,
        "--direct-mold-wall-mm", str(direct_mold_wall_mm),
        "--parting-axis", parting_axis,
        "--parting-offset-mm", str(parting_offset_mm),
        *BASE_ARGS,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    with open(report_path) as f:
        report = json.load(f)
    if "error" in report:
        print(f"[{out_subdir}] ERROR:", report["error"])
        print(result.stdout[-2000:])
        print(result.stderr[-2000:])
        raise SystemExit(1)
    return report


def verify_bbox(stl_path):
    verify_script = os.path.join(tempfile.gettempdir(), "debug_parting_verify.py")
    with open(verify_script, "w") as f:
        f.write(
            f"""
import bpy
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.wm.stl_import(filepath=r"{stl_path}")
obj = bpy.context.selected_objects[0]
xs = [v.co.x for v in obj.data.vertices]
ys = [v.co.y for v in obj.data.vertices]
zs = [v.co.z for v in obj.data.vertices]
print("BBOX_MIN=", min(xs), min(ys), min(zs))
print("BBOX_MAX=", max(xs), max(ys), max(zs))
"""
        )
    result = subprocess.run([blender, "--background", "--python", verify_script], capture_output=True, text=True)
    out = result.stdout
    bmin = out.split("BBOX_MIN=")[1].split("\n")[0].strip()
    bmax = out.split("BBOX_MAX=")[1].split("\n")[0].strip()
    return bmin, bmax


# 1. Default z-axis, no offset - direct_cast
report1 = run("direct_cast", 3.0, "z", 0.0, "case1_default")
print("CASE 1 cavity_volume_mm3:", report1["cavity_volume_mm3"], "(expect 24.0, box is 2x3x4)")
bmin, bmax = verify_bbox(report1["paths"]["direct_mold_bottom"])
print("CASE 1 bottom bbox:", bmin, "to", bmax)

# 2. parting_axis=x - expect X/Z extents swapped in the outer mold vs case 1's own bottom half.
report2 = run("direct_cast", 3.0, "x", 0.0, "case2_axis_x")
bmin2, bmax2 = verify_bbox(report2["paths"]["direct_mold_bottom"])
print("CASE 2 (axis=x) bottom bbox:", bmin2, "to", bmax2)
print("CASE 2 cavity_volume_mm3:", report2["cavity_volume_mm3"], "(expect 24.0, volume is axis-independent)")

# 3. parting_offset_mm=1.0 on z axis - bottom half should be 1mm taller, top 1mm shorter than case 1.
report3 = run("direct_cast", 3.0, "z", 1.0, "case3_offset")
bmin3_bottom, bmax3_bottom = verify_bbox(report3["paths"]["direct_mold_bottom"])
bmin3_top, bmax3_top = verify_bbox(report3["paths"]["direct_mold_top"])
print("CASE 3 (offset=1.0) bottom bbox:", bmin3_bottom, "to", bmax3_bottom)
print("CASE 3 (offset=1.0) top bbox:", bmin3_top, "to", bmax3_top)

# 4. silicone_block cavity volume - analytic cavity box is (2+2*8)*(3+2*8)*(4+2*8) = 18*19*20.
report4 = run("silicone_block", 3.0, "z", 0.0, "case4_silicone")
expected = (2.0 + 2 * 8.0) * (3.0 + 2 * 8.0) * (4.0 + 2 * 8.0)
print("CASE 4 cavity_volume_mm3:", report4["cavity_volume_mm3"], f"(expect {expected})")

# 5. Bad offset should fail cleanly.
try:
    run("direct_cast", 3.0, "z", 100.0, "case5_bad_offset")
    print("CASE 5: UNEXPECTED SUCCESS (should have failed)")
except SystemExit:
    print("CASE 5: correctly rejected an out-of-range parting_offset_mm")
