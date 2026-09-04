# Project: 3D Printing Model Prompt
# File: /home/user/3d-printing-model-prompt/Dockerfile
# Description: Container image for the threedprompt HTTP service. Installs
#     OpenSCAD and headless Blender alongside the Python app so both CAD
#     backends are available without any host setup (rule 4: Docker-first,
#     cross-platform).
# Inputs: requirements.txt, src/, pyproject.toml (build context)
# Outputs: A runnable image exposing port 8000, running as a non-root user.
# Troubleshooting:
#     - If `docker build` fails on `apt-get install blender`, the Debian
#       base image's package index may be stale - rebuild with
#       `docker build --no-cache -t threedprompt .`.
#     - The image is large (~1.5GB+) because Blender pulls in Mesa/OpenGL
#       libraries even for headless use - that's expected, not a bug.
#     - watertight.py's viewer.glb export (glTF) fails inside Blender with
#       "No module named 'numpy'" without python3-numpy below - the Debian
#       apt `blender` package links the *system* Python rather than
#       bundling its own, so its glTF export addon needs numpy available
#       to that same system Python. See TROUBLESHOOTING.md.

FROM python:3.11-slim

# openscad + blender, python3-numpy (see the numpy note above - only
# thickness.py's STL-only Solidify path avoided needing this; watertight.py's
# glTF viewer export does not), and the shared libraries headless Blender
# needs at runtime even without a display (Mesa/X11 client libs).
RUN apt-get update && apt-get install -y --no-install-recommends \
    openscad \
    blender \
    python3-numpy \
    libgl1 \
    libglu1-mesa \
    libxi6 \
    libxrender1 \
    libxext6 \
    libxkbcommon0 \
    libsm6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir --no-deps -e .

RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/output \
    && chown -R appuser:appuser /app
USER appuser

ENV OUTPUT_DIR=/app/output \
    HOST=0.0.0.0 \
    PORT=8000

EXPOSE 8000

CMD ["uvicorn", "threedprompt.main:app", "--host", "0.0.0.0", "--port", "8000"]
