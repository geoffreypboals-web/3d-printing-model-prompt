"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/mesh_repair/__init__.py
Description: Package init for the mesh watertight-analysis-and-repair
    feature. Re-exports the small public surface callers need
    (analyze_mesh, repair_mesh, MeshRepairError, and the report
    dataclasses) so other modules can `from mesh_repair import
    analyze_mesh` instead of reaching into service.py/models.py directly.
Inputs: N/A.
Outputs: N/A.
Troubleshooting:
    - ImportError on `import bpy` anywhere under this package outside of
      blender_scripts/: that's expected — only files in
      blender_scripts/ run inside Blender's own Python. Everything else
      here (service.py, classifier.py, models.py, api.py) is plain
      Python and talks to Blender only via subprocess.
"""

from .classifier import classify_report
from .models import (
    BoundingBox,
    FlippedNormalIsland,
    Hole,
    HoleClassification,
    RepairResult,
    WatertightReport,
)
from .service import MeshRepairError, analyze_mesh, repair_mesh

__all__ = [
    "analyze_mesh",
    "repair_mesh",
    "classify_report",
    "MeshRepairError",
    "WatertightReport",
    "RepairResult",
    "Hole",
    "HoleClassification",
    "FlippedNormalIsland",
    "BoundingBox",
]
