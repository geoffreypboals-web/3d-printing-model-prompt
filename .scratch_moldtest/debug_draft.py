"""
Throwaway verification script (not part of the test suite) for FR-4's
draft/undercut analysis: builds a small pyramid-with-undercut test mesh
by hand (a truncated cone-ish shape isn't easy to hand-write as STL
triangles, so instead: a unit cube with one side wall replaced by a wall
that leans back INTO the part, creating a genuine undercut on that one
face) and checks analyze_draft() correctly flags it while leaving the
other (vertical, undrafted -> "insufficient_draft" not "undercut") walls
correctly classified.

Simpler first check: run against the plain watertight unit cube fixture
(vertical walls everywhere, 0 draft) and confirm every side wall is
flagged "insufficient_draft" (0 degrees < the 2 degree default
threshold) while the top/bottom caps are NOT flagged (their normals are
perfectly aligned with their half's own pull direction -> +90 degrees).
Run: blender --background --python debug_draft.py
"""

import json
import os
import subprocess
import struct
import tempfile

CUBE_STL = os.path.join(tempfile.gettempdir(), "debug_draft_cube.stl")
REPORT = os.path.join(tempfile.gettempdir(), "debug_draft_report.json")


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

repo_root = r"D:\3d-printing-model-prompt"
script = os.path.join(repo_root, "src", "threedprompt", "blender_scripts", "analyze_draft.py")
blender = r"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe"

cmd = [
    blender, "--background", "--python", script, "--",
    "--input", CUBE_STL,
    "--output", REPORT,
    "--pull-axis", "z",
    "--min-draft-angle-deg", "2.0",
]
result = subprocess.run(cmd, capture_output=True, text=True)
print("returncode:", result.returncode)
print("stdout tail:\n", "\n".join(result.stdout.splitlines()[-20:]))
print("stderr tail:\n", "\n".join(result.stderr.splitlines()[-20:]))

with open(REPORT) as f:
    report = json.load(f)
print(json.dumps(report, indent=2))

assert report["releasable"] is False, "a plain vertical-walled cube should NOT be releasable at a 2deg threshold"
total_flagged_faces = sum(isl["face_count"] for isl in report["problem_islands"])
print("total flagged faces:", total_flagged_faces, "of", report["face_count"])
# 4 side walls * 2 triangles each = 8 triangles should be flagged; the 2
# top-cap and 2 bottom-cap triangles should NOT be (they're perfectly
# aligned with their own half's pull direction -> +90deg draft).
assert total_flagged_faces == 8, f"expected exactly the 4 side walls (8 tris) flagged, got {total_flagged_faces}"
for isl in report["problem_islands"]:
    assert isl["classification"] == "insufficient_draft", isl
    assert abs(isl["min_draft_angle_deg"] - 0.0) < 1e-6, isl
print("OK: side walls flagged insufficient_draft at 0deg, caps correctly excluded")
