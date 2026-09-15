"""
Throwaway verification (not part of the test suite): confirms
analyze_draft.py's "undercut" (negative draft angle) branch actually
triggers, not just "insufficient_draft" (the 0deg vertical-wall case
already verified in debug_draft.py). A single downward-facing quad
(outward normal -Z) placed entirely in the TOP half (z > the mesh's own
bbox midpoint) is exactly the "underside of an overhanging cap" case: the
top half's pull direction is +Z, so a surface facing -Z there can only
release by passing back through solid material first - a genuine
undercut, expected draft angle -90deg.

A single low anchor point at z=0 is included purely so the bbox midpoint
(the parting plane) sits below the quad, at z=0.4, without needing a full
watertight solid - analyze_draft.py only reads face normals/centroids,
it never checks watertightness.
Run: blender --background --python debug_draft_undercut.py
"""

import json
import os
import struct
import subprocess
import tempfile

STL_PATH = os.path.join(tempfile.gettempdir(), "debug_draft_undercut.stl")
REPORT = os.path.join(tempfile.gettempdir(), "debug_draft_undercut_report.json")

# Downward-facing quad (outward normal -Z) at z=0.8, plus one lone
# degenerate anchor triangle collapsed at z=0.0 purely to pull the mesh's
# bounding box (and therefore the parting plane) down to z=0.4.
v0 = (-1.0, -1.0, 0.8)
v1 = (-1.0, 2.0, 0.8)
v2 = (2.0, -1.0, 0.8)
v3 = (2.0, 2.0, 0.8)
anchor = (0.0, 0.0, 0.0)

triangles = [
    (v0, v1, v2),  # normal (0,0,-1) - see docstring's cross-product derivation
    (v1, v3, v2),  # normal (0,0,-1) too (consistent winding across the quad's diagonal)
    (anchor, anchor, anchor),  # degenerate (zero area) - contributes only a bbox point, no real face
]

with open(STL_PATH, "wb") as f:
    f.write(b"\x00" * 80)
    f.write(struct.pack("<I", len(triangles)))
    for a, b, c in triangles:
        f.write(struct.pack("<3f", 0.0, 0.0, 0.0))
        for v in (a, b, c):
            f.write(struct.pack("<3f", *v))
        f.write(struct.pack("<H", 0))

repo_root = r"D:\3d-printing-model-prompt"
script = os.path.join(repo_root, "src", "threedprompt", "blender_scripts", "analyze_draft.py")
blender = r"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe"

cmd = [
    blender, "--background", "--python", script, "--",
    "--input", STL_PATH,
    "--output", REPORT,
    "--pull-axis", "z",
    "--min-draft-angle-deg", "2.0",
]
result = subprocess.run(cmd, capture_output=True, text=True)
print("returncode:", result.returncode)
print("stderr tail:\n", "\n".join(result.stderr.splitlines()[-20:]))

with open(REPORT) as f:
    report = json.load(f)
print(json.dumps(report, indent=2))

assert report["releasable"] is False
islands = [isl for isl in report["problem_islands"] if isl["classification"] == "undercut"]
assert len(islands) == 1, f"expected exactly one undercut island, got {report['problem_islands']}"
angle = islands[0]["min_draft_angle_deg"]
assert abs(angle - (-90.0)) < 1e-3, f"expected -90deg undercut, got {angle}"
print("OK: downward-facing top-half quad correctly classified as a -90deg undercut")
