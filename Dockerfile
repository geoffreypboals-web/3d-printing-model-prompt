# Project: 3D Printing Model Prompt
# File: /home/user/3d-printing-model-prompt/Dockerfile
# Description: Container image for the mesh watertight-repair API + viewer
#     (src/mesh_repair/api.py). Installs Blender headless (used as an
#     external subprocess, not a Python library — see docs/adr/0001-*)
#     plus the numpy package Blender's own glTF exporter addon needs when
#     Blender is installed from the distro package (it links against the
#     system Python rather than bundling its own, per
#     TROUBLESHOOTING.md). Built to behave the same on a Windows or Linux
#     host per CLAUDE.md rule 4 — everything below the FROM line runs
#     inside a fixed Linux container regardless of host OS.
# Troubleshooting:
#     - "ModuleNotFoundError: No module named 'numpy'" from inside a
#       Blender glTF export: the python3-numpy apt package below is what
#       fixes that for this distro Blender build — see
#       TROUBLESHOOTING.md for why.
#     - Slow/failed `apt-get install blender`: the Ubuntu/Debian Blender
#       package can lag behind blender.org's own releases; pin a newer
#       version by downloading a blender.org tarball instead if a
#       specific Blender release is required.
FROM python:3.11-slim

# Blender (headless mesh analysis/repair engine, invoked as a subprocess —
# see docs/adr/0001-blender-subprocess-not-bpy-library.md) and the numpy
# package its bundled glTF exporter addon imports at runtime.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        blender \
        python3-numpy \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY viewer/ viewer/

# Least-privilege: run as a non-root user (rule 8), owning only the
# writable data directory it needs.
RUN useradd --create-home --uid 1000 meshrepair \
    && mkdir -p /app/data/uploads \
    && chown -R meshrepair:meshrepair /app
USER meshrepair

ENV MESH_REPAIR_DATA_DIR=/app/data/uploads \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "mesh_repair.api:app", "--host", "0.0.0.0", "--port", "8000"]
