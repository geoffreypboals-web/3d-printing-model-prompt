# Project: 3D Printing Model Prompt
# File: /home/user/3d-printing-model-prompt/Dockerfile
# Description: Builds the app container -- minimal official base image,
#     non-root process user, no secrets baked into any layer (CLAUDE.md
#     rule 8). Cross-platform by construction: the image runs identically
#     regardless of whether it was built on a Windows or Linux host, since
#     nothing here depends on the host's path separators or line endings.
# Inputs: requirements.txt, app.py, src/, static/.
# Outputs: A container listening on 8000 internally (mapped to the
#     resolved host port by docker-compose.yml / launch.py).
# Troubleshooting:
#     - Build fails on a Windows host with odd line-ending errors in a
#       copied script: see .gitattributes at the repo root, which
#       normalizes line endings for exactly this reason.
FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (own layer) so a code-only change doesn't
# bust the dependency-install cache on every rebuild.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY src/ ./src/
COPY static/ ./static/

# Least privilege (CLAUDE.md rule 8): the app process never needs root.
RUN useradd --create-home --uid 1000 appuser
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
