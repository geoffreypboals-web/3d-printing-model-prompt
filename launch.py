"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/launch.py
Description: Suite-aware launcher, same up/open/status/down/relocate
    contract every SuiteControl-managed project uses -- see
    src/port_registry.py's docstring for why the port-resolution code is
    a deliberate copy rather than a shared import. Unlike SuiteControl
    itself (native, non-Docker), this project follows the ordinary
    pattern: `up` resolves a free host port, builds and starts the
    container via `docker compose up -d --build`, and publishes the
    resolved port to the container as APP_HOST_PORT.
Inputs: CLI argument (up/open/status/down/relocate); ~/.suite/ports.json
    (shared registry); docker-compose.yml in this same directory.
Outputs: Starts/stops the `app` container; opens a browser tab; prints
    status; writes the resolved port to the shared registry.
Troubleshooting:
    - "docker: command not found": Docker Desktop (Windows/Mac) or the
      Docker Engine (Linux) isn't installed/on PATH -- this project is
      Docker-first per CLAUDE.md rule 4, there's no non-Docker fallback.
    - `up` reports a port other than 8800: something else already had
      8800 -- this is expected, not an error; `status` always shows the
      real resolved port.
USAGE:
    python launch.py up        resolve a port, build+start the container
    python launch.py open      open the app in your browser
    python launch.py status    show the resolved port and whether it's up
    python launch.py down      stop the container (data persists -- see README.md)
    python launch.py relocate  force a fresh port scan
"""
import argparse
import os
import subprocess
import sys
import webbrowser
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from src import port_registry  # noqa: E402 -- must follow the sys.path.insert() above

PROJECT = "3d-printing-model-prompt"
APP_DIR = Path(__file__).resolve().parent
COMPOSE_FILE = APP_DIR / "docker-compose.yml"
SERVICE = {"key": "app", "default_port": 8800, "category": "internal"}


def _run(argv: list[str]) -> int:
    """Runs a docker/docker-compose command in the foreground -- output
    goes straight to this terminal (a human is running this directly),
    so nothing here needs its own logging."""
    result = subprocess.run(argv, cwd=str(APP_DIR), check=False)
    return result.returncode


def cmd_up(args) -> None:
    """Resolves a free host port (reusing a prior one if it's still free),
    publishes it to the container as APP_HOST_PORT, then builds and starts
    the service. Idempotent -- running this again while already up just
    rebuilds/restarts in place, same as `docker compose up -d --build`
    always does."""
    reg = port_registry.load_registry()
    port = port_registry.resolve_port(PROJECT, SERVICE, reg)
    if port != SERVICE["default_port"]:
        print(f"note: port {SERVICE['default_port']} was taken -- using {port} instead.")
    reg[PROJECT] = {SERVICE["key"]: {"port": port, "category": SERVICE["category"]}}
    port_registry.save_registry(reg)

    env = os.environ.copy()
    env["APP_HOST_PORT"] = str(port)
    result = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d", "--build"],
        cwd=str(APP_DIR), env=env, check=False,
    )
    if result.returncode != 0:
        sys.exit(result.returncode)
    print(f"3d-printing-model-prompt/app: http://localhost:{port}")
    print("Run `python launch.py open` to view it, or `python launch.py status` to check.")


def cmd_open(args) -> None:
    """Opens the resolved port in a browser -- reports "not started yet"
    instead of guessing a port if this project has never been `up`'d."""
    reg = port_registry.load_registry()
    entry = reg.get(PROJECT, {}).get(SERVICE["key"])
    if not entry:
        print("Not started yet -- run `python launch.py up` first.")
        return
    url = f"http://localhost:{entry['port']}/"
    print(f"Opening {url} ...")
    webbrowser.open(url)


def cmd_status(args) -> None:
    reg = port_registry.load_registry()
    entry = reg.get(PROJECT, {}).get(SERVICE["key"])
    if not entry:
        print("3d-printing-model-prompt: never started via launch.py.")
        return
    port = entry["port"]
    up = port_registry.is_port_up(port)
    print(f"3d-printing-model-prompt/app: port {port} -- {'UP' if up else 'not running'}")


def cmd_down(args) -> None:
    """Never passes -v/--volumes -- named volumes/bind mounts persist, so
    this is "save and shut down", not a data-losing action."""
    sys.exit(_run(["docker", "compose", "-f", str(COMPOSE_FILE), "down"]))


def cmd_relocate(args) -> None:
    reg = port_registry.load_registry()
    reg.pop(PROJECT, None)
    port_registry.save_registry(reg)
    port = port_registry.resolve_port(PROJECT, SERVICE, reg)
    reg[PROJECT] = {SERVICE["key"]: {"port": port, "category": SERVICE["category"]}}
    port_registry.save_registry(reg)
    print(f"3d-printing-model-prompt relocated: app -> {port}")
    print("Run `python launch.py up` again for the new port to take effect.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Docker-based launcher for 3D Printing Model Prompt.")
    parser.add_argument("command", choices=["up", "open", "status", "down", "relocate"])
    args = parser.parse_args()
    {"up": cmd_up, "open": cmd_open, "status": cmd_status, "down": cmd_down,
     "relocate": cmd_relocate}[args.command](args)


if __name__ == "__main__":
    main()
