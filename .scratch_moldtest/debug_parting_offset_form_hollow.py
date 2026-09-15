"""
Throwaway follow-up check (not part of the test suite): confirms
form_fitting and hollow_cast modes still build successfully with a
non-zero parting_offset_mm (exercising the _add_registration_keys()
parting_z fix, since keys used to assume cavity_center[2] == parting_z,
which is no longer true with an offset) and that their subtract-based
volume calculators (offset/model difference) produce sane, non-negative
numbers.
Run: blender --background --python debug_parting_offset_form_hollow.py
"""
import json
import os
import struct
import subprocess
import tempfile

BOX_STL = os.path.join(tempfile.gettempdir(), "debug_parting2_box.stl")
OUT_DIR = os.path.join(tempfile.gettempdir(), "debug_parting2_out")


def _write_box_stl(path, sx, sy, sz):
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
    "--direct-mold-wall-mm", "3.0",
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


def run(mode, out_subdir, extra):
    out_dir = os.path.join(OUT_DIR, out_subdir)
    os.makedirs(out_dir, exist_ok=True)
    report_path = os.path.join(out_dir, "report.json")
    cmd = [
        blender, "--background", "--python", script, "--",
        "--input", BOX_STL,
        "--output-dir", out_dir,
        "--report-output", report_path,
        "--mode", mode,
        *BASE_ARGS,
        *extra,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    with open(report_path) as f:
        report = json.load(f)
    if "error" in report:
        print(f"[{out_subdir}] ERROR:", report["error"])
        print(result.stdout[-3000:])
        print(result.stderr[-3000:])
        raise SystemExit(1)
    return report


r1 = run(
    "form_fitting",
    "form_offset",
    ["--shell-thickness-mm", "1.0", "--skin-pour-wall-mm", "2.0", "--support-jacket-wall-mm", "3.0",
     "--cast-wall-thickness-mm", "0.1", "--parting-axis", "z", "--parting-offset-mm", "1.0"],
)
print("form_fitting (offset=1.0) OK, cavity_volume_mm3:", r1["cavity_volume_mm3"], "(expect > 0, shell volume)")

r2 = run(
    "hollow_cast",
    "hollow_offset",
    ["--shell-thickness-mm", "1.0", "--skin-pour-wall-mm", "2.0", "--support-jacket-wall-mm", "3.0",
     "--cast-wall-thickness-mm", "0.1", "--parting-axis", "y", "--parting-offset-mm", "-0.5"],
)
print("hollow_cast (axis=y, offset=-0.5) OK, cavity_volume_mm3:", r2["cavity_volume_mm3"], "(expect > 0, box minus core)")
