# Contributing

Early-stage project (`0.1.0`). Issues and pull requests are welcome; for
anything larger than a small fix, open an issue first.

## Dev setup

Replace `<path-to>` with where you cloned the repo. Tests mock every CAD and
LLM call, so you need neither OpenSCAD/Blender nor an LLM to run them.

Linux/Mac (bash):

```bash
cd <path-to>/3d-printing-model-prompt && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt && pip install -e . && pytest -q
```

Windows (PowerShell):

```powershell
cd <path-to>\3d-printing-model-prompt; python -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install -r requirements-dev.txt; pip install -e .; pytest -q
```

`*_integration` tests are skipped unless a real `blender` is on `PATH`;
`tests/test_cadquery_gen.py` needs Linux with Landlock, so run it in the
image (see the README's CadQuery section). To run the real stack use Docker:
see the README quick start.

## Before you push

CI (`.github/workflows/ci.yml`) runs exactly these, so run them locally:

```bash
cd <path-to>/3d-printing-model-prompt && ruff check src tests && black --check src tests && pytest -q
```

Windows (PowerShell):

```powershell
cd <path-to>\3d-printing-model-prompt; ruff check src tests; black --check src tests; pytest -q
```

Line length is 120 (`pyproject.toml`); the vendored
`src/threedprompt/blender_scripts/vendor/` is excluded. The security workflow
(`.github/workflows/security.yml`) also runs gitleaks and pip-audit; the
repo ships a `.pre-commit-config.yaml` with a gitleaks hook
(`pip install pre-commit && pre-commit install`).

## Conventions (from `CLAUDE.md`, which is the authoritative list)

- **Branches**, not direct commits to `main`, named for the work:
  `feature/...`, `fix/...`, `chore/...`, `docs/...` (history also has
  `claude/...` and `ai/...` branches from automated sessions).
- **Commits**: Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`,
  `deps:`), one logical change each.
- **Pull requests** against `main`; the history is merge commits from PRs.
  Review your own diff first: tests pass, docs updated if behaviour changed,
  no secrets, diff scoped to what the PR claims.
- **Every source file** starts with the six-field header block (Project,
  File, Description, Inputs, Outputs, Troubleshooting) and every function has
  a docstring.
- **Config**: new settings go in `src/threedprompt/config.py` and
  `.env.example`, and in the README configuration table. Never hardcode
  environment-specific values.
- **Tests**: new logic gets a happy-path and one edge-case test; a bug fix
  gets a regression test.
- **Docs**: update `README.md`, `TROUBLESHOOTING.md` (for non-obvious fixes),
  `CHANGELOG.md`, and add an ADR in `docs/adr/` for a significant design
  decision.
- **Dependencies**: prefer permissive licences; flag anything GPL/paid
  before adding it. Pin versions in `requirements.txt`.
- **LLM calls** go through `llm_client.py`; keep Ollama as the default.

## Licence

MIT (`LICENSE`). By contributing you agree your work is released under it.
See also [SECURITY.md](SECURITY.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
