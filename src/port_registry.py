"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/port_registry.py
Description: Read/write access to the shared, suite-wide port registry
    (~/.suite/ports.json) and the free-port probe every SuiteControl-
    managed project's own launch.py uses -- deliberately copy-pasted from
    one audited implementation rather than shared as a library, so this
    project keeps working standalone even when SuiteControl or any
    sibling project isn't installed. See launch.py for how this project
    uses it: resolve a free port, publish it to Docker via an env var,
    and record it here so SuiteControl's dashboard (if present) can find
    it.
Inputs: A project key (this project's own: "3d-printing-model-prompt"),
    a service dict ({"key", "default_port"}), and the registry dict
    itself.
Outputs: The resolved port to use, and side effects on
    ~/.suite/ports.json (read/write).
Troubleshooting:
    - If `launch.py up` reports a surprising port, check
      ~/.suite/ports.json for a stale entry from a previous run --
      `python launch.py relocate` clears this project's own entry and
      re-resolves.
    - On Docker Desktop for Windows, a published container port can
      still bind() successfully from the Windows host side even though
      something is listening there (the real listener lives in the
      WSL2/Hyper-V layer) -- is_port_free() checks BOTH a live TCP
      connect and a bind() for exactly this reason.
"""
import json
import socket
from pathlib import Path

REGISTRY_PATH = Path.home() / ".suite" / "ports.json"


def is_port_free(port: int, host: str = "127.0.0.1") -> bool:
    """A port counts as free only if NEITHER check finds it occupied --
    see this module's own docstring for why bind() alone isn't enough on
    Docker Desktop for Windows."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.3)
        if probe.connect_ex((host, port)) == 0:
            return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def is_port_up(port: int, host: str = "127.0.0.1") -> bool:
    """The inverse framing of is_port_free, for status reporting:
    something is listening there right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.3)
        return probe.connect_ex((host, port)) == 0


def load_registry() -> dict:
    """Returns {} (never raises) on a missing or corrupt registry file --
    a fresh machine with no registry yet is a normal, expected state."""
    if REGISTRY_PATH.exists():
        try:
            return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_registry(reg: dict) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(json.dumps(reg, indent=2), encoding="utf-8")


def registry_claimed_ports(reg: dict, exclude_project: str | None = None) -> set:
    """Every port currently claimed by any OTHER project in the registry
    -- used so this project's own port resolution never collides with a
    sibling's, even if that sibling isn't running right now."""
    claimed = set()
    for proj, services in reg.items():
        if proj == exclude_project:
            continue
        for svc in services.values():
            claimed.add(svc.get("port"))
    return claimed


def resolve_port(project: str, service: dict, reg: dict) -> int:
    """Reuse a prior port if it's still ours/free, else the documented
    default if free, else scan default+1..default+50. Raises SystemExit
    (same as every other project's launch.py) if none of those 51
    candidates is free -- an extremely unlikely, genuinely exceptional
    situation worth stopping hard for rather than silently picking an
    arbitrary port."""
    default = service["default_port"]
    claimed = registry_claimed_ports(reg, exclude_project=project)

    prior = reg.get(project, {}).get(service["key"], {}).get("port")
    if prior and prior not in claimed and (prior == default or is_port_free(prior)):
        return prior

    if default not in claimed and is_port_free(default):
        return default
    for candidate in range(default + 1, default + 51):
        if candidate in claimed:
            continue
        if is_port_free(candidate):
            return candidate
    raise SystemExit(f"No free port found near {default} after scanning 50 candidates.")


def get_project_service_port(reg: dict, project_key: str, service_key: str) -> int | None:
    """Looks up a project's ACTUAL resolved port -- returns None if that
    project has never been started via its own launch.py."""
    return reg.get(project_key, {}).get(service_key, {}).get("port")
