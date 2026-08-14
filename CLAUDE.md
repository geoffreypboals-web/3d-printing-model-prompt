# Coding Practice Standards

These are the coding practice standards for this project, agreed with the project owner. They apply on top of (and travel with the repo independently of) any global Claude Code standards.

## 1. Always use full absolute paths

- Whenever referencing a file, folder, or telling the owner where to find or run something, give the **full absolute path** (e.g. `/home/user/3d-printing-model-prompt/src/generator.py`), never a bare filename, a relative path, or an assumption about which folder something lives in.
- Do not assume prior knowledge of this project's folder structure — spell it out every time, even mid-conversation.
- This applies to instructions for running commands, editing files, finding config, and troubleshooting steps alike.

## 2. File header block (every source file)

Every source code file (including helper/utility files, not just entry points) must start with a header comment, in the comment style native to that language, containing:

1. **Project** — the name of the overall project this file belongs to
2. **File** — this file's name/path within the project
3. **Description** — what this file/module does, in plain language
4. **Inputs** — what this file expects (function args, env vars, config, CLI args, upstream data, etc.)
5. **Outputs** — what this file produces or returns (return values, side effects, files written, API responses, etc.)
6. **Troubleshooting** — common issues specific to this file and how to resolve them

Example (Python):

```python
"""
Project: 3D Printing Model Prompt
File: /home/user/3d-printing-model-prompt/src/prompt_builder.py
Description: Builds structured prompts for generating 3D-printable model
    descriptions from user input, and validates the resulting spec before
    it's sent to the generation backend.
Inputs: Raw user prompt text, model constraints (size limits, printer
    profile) from config.py
Outputs: A validated PromptSpec object ready for the generation pipeline
Troubleshooting:
    - If validation keeps rejecting valid input, check the size-limit
      constants in config.py match the active printer profile.
    - If prompts silently produce empty specs, check that build_spec()
      isn't swallowing exceptions from the backend call.
"""
```

Adapt the comment syntax to the language (`//` or `/* */` for JS/TS/C-family, `#` for shell, etc.) but keep the same six fields.

## 3. Inline documentation for every function

- Every function/method gets an inline doc comment (docstring, JSDoc block, etc. — whatever is idiomatic for the language) describing what it does, its parameters, and its return value.
- Keep these accurate and up to date when a function changes — a stale docstring is treated as a bug.

## 4. Develop with Docker deployment in mind, cross-platform (Windows + Linux)

- This project should be developed with the expectation that it will run in Docker, and should include a `Dockerfile` (and `docker-compose.yml` where the project has multiple services or dependencies like a database).
- Write code and Dockerfiles so the container runs the same on both Windows and Linux hosts:
  - Don't hardcode OS-specific path separators (`/` vs `\`) — use language-native path-joining (e.g. Python's `pathlib`/`os.path.join`, Node's `path.join`).
  - Watch for line-ending issues (`LF` vs `CRLF`) in scripts copied into the image; add a `.gitattributes` to normalize line endings if the project has shell scripts or config files that are sensitive to it.
  - Avoid relying on host-specific environment assumptions (case-sensitive filesystems, host-only binaries, absolute Windows drive letters, etc.) inside the container.
- `README.md` must include the Docker build/run instructions (full commands, e.g. `docker build -t <name> .` and `docker run ...` or `docker compose up`), using full absolute paths per rule 1 where relevant.
- If something genuinely can't be containerized (e.g. it must run natively for OS-level access), say so explicitly and explain why, rather than silently skipping Docker.

## 5. AI/LLM integration — plan for Ollama, minimize ongoing cost

- The project owner has paid subscriptions to GitHub Copilot and Claude and is fine using them freely for initial development/stand-up of a project. But any AI/LLM capability the *program itself* calls at runtime (not just tools used to write the code) should be designed so it isn't permanently locked into a paid API.
- **Ollama** (local, no-cost inference) should be treated as an available, no-cost backend option for this project if it needs LLM/AI functionality at runtime.
- Put any LLM calls behind a small abstraction/interface (a single client wrapper, config-driven provider selection, etc.) so the backend can be swapped — e.g. via an env var or config value — between a paid API (Claude, OpenAI, etc.) and a local Ollama model, without rewriting call sites.
- Default expectation: it's fine to prototype and initially stand up against a paid model for speed/quality, but plan and document a path to switch the default runtime backend to Ollama (or another free/local option) once stood up, to minimize ongoing operating cost.
- If a task genuinely requires a paid model's quality/capability and Ollama can't reasonably substitute, say so explicitly rather than silently defaulting to the paid option.
- Document in `README.md` which backend is the default, which are supported, and how to switch between them (model name/env var, Ollama setup requirement, etc.).

## 6. Open source is the goal — flag any paid/proprietary licensing

- The default intent for this project is **open source**, not a private/commercial sale. Don't design around a paywall, license-key, or proprietary-distribution model unless explicitly told otherwise.
- When choosing a library, framework, SDK, API, or other integration, prefer open-source / permissively-licensed options (MIT, Apache-2.0, BSD, etc.) suitable for open-source redistribution.
- **Always explicitly warn the project owner** before integrating anything that:
  - requires purchasing a commercial license or a paid tier to use in the way the project needs it,
  - is "source available" but not OSI-approved / not freely redistributable,
  - carries a copyleft license (e.g. GPL/AGPL) that could impose obligations on the rest of the project, or restrict how it can be distributed, or
  - is free for personal/non-commercial use only, which could be a problem if the project is later distributed publicly.
- Don't silently add a dependency with one of the above license issues — surface it and let the project owner decide before integrating it.
- Where relevant, note dependency licenses in `README.md` (or a `NOTICE`/`LICENSES` file) so the project's licensing posture stays visible.

## 7. README + separate troubleshooting doc

This project keeps two docs at its root:

- **`README.md`** — overview of what the project is, setup/install steps, and how to use it.
- **`TROUBLESHOOTING.md`** — a separate, living document of common issues and their fixes. Add to it whenever a non-obvious bug or configuration problem gets solved, so the same issue doesn't have to be re-debugged from scratch later.

## 8. Security — secure by default

- **No secrets in code, ever.** API keys, tokens, passwords, and connection strings go in environment variables or a secrets manager, never hardcoded or committed. `.env` files are `.gitignore`'d, with a checked-in `.env.example` showing required keys with placeholder values. If a secret is ever accidentally committed, treat it as compromised — rotate it, don't just delete the commit.
- **Validate and sanitize all external input** (user input, API responses, file uploads, query params) at the boundary. Use parameterized queries/ORM methods to prevent SQL injection, and output-encode to prevent XSS. Never trust client-side validation alone.
- **Least privilege by default** — DB users, API tokens, container users, and file permissions get only the access they need, not broad/admin scope for convenience.
- **Never silently weaken security for convenience.** Disabling TLS verification, wildcard CORS, disabling auth checks, or hardcoding a test credential "just for now" must be flagged explicitly and confirmed — including in code meant to be temporary, since temporary code has a way of shipping.
- **Keep dependencies patched.** Pin versions, and flag when a dependency has a known CVE rather than silently using it. Enable automated dependency scanning where the platform supports it (e.g. GitHub Dependabot).
- **Docker specifically:** use minimal/official base images, avoid running the container process as root when avoidable, and never bake secrets into image layers.

## 9. Performance — profile before optimizing, avoid known traps

- Write clear, correct code first — don't prematurely optimize. But avoid well-known traps by default: N+1 database queries, O(n²) algorithms on data that can grow, loading entire files/result sets into memory when streaming or pagination is available.
- Where performance actually matters (user-facing latency, large batch jobs), **measure before optimizing** — profile it, don't guess. If an optimization trades off readability, say why in a comment or commit message.
- Use caching deliberately (in-memory, Redis, HTTP cache headers) for expensive or repeated work, but always document the invalidation strategy — a cache with unclear invalidation is a future bug, not a free win.
- Any list/search/query endpoint gets pagination or limits by default — never an unbounded result set.
- Prefer async/non-blocking I/O for network- or disk-bound work in languages where it's idiomatic (Node, Python `asyncio`, etc.), especially in code handling concurrent requests.

## 10. Cost controls — default cheap, guard against runaway spend

- Default to free/open-source/self-hosted options before reaching for a paid service (this reinforces rule 5's Ollama-first stance) — if a paid service really is the right call, say so and name the cost driver.
- Any external call that costs money per invocation (LLM APIs, metered SaaS, serverless functions) gets basic guardrails: rate limiting, a request cap, and caching of repeated/identical requests so the same paid call isn't made twice.
- No unbounded loops, uncapped retries, or polling patterns that could spike usage-based billing (compute, API calls, egress) if something misbehaves — always cap and back off.
- For any cloud infrastructure, set budget alerts and resource limits (autoscaling ceilings, timeouts) rather than leaving them unbounded by default.
- Flag any design choice with a recurring cost implication (managed DB vs. self-hosted, per-invocation serverless pricing vs. a fixed-cost VM) before committing to it, so it's a deliberate call, not a default.

## 11. Testing — cover the golden path and edge cases

- New logic doesn't ship without at least a basic test: the golden/happy path plus one meaningful edge case (empty input, error condition, boundary value).
- Prefer fast, deterministic unit tests close to the code under test; use integration tests for cross-component behavior (e.g. DB, API calls) rather than trying to unit-test everything.
- When fixing a bug, add a test that reproduces it first, so it can't silently regress.
- Don't chase 100% coverage as a goal in itself — prioritize tests around business logic, error handling, and anything that's bitten us before (see `TROUBLESHOOTING.md`).

## 12. Version control & commit practices

- Use a consistent commit message convention (Conventional Commits style: `feat:`, `fix:`, `docs:`, `chore:`, etc.) so history is scannable and could drive an automated changelog later.
- Work happens on a branch, not directly on `main`/`master` — even solo, branch and review the diff before merging, so mistakes get caught before they're baked into history.
- Keep commits scoped to one logical change; don't bundle unrelated fixes into one commit.
- Branch names should describe the work (e.g. `feature/prompt-templates`, `fix/model-size-validation`), not be generic (`update`, `changes`).

## 13. Error handling & logging

- No silent failures — never swallow an exception or ignore an error return without at least logging it; if something is genuinely safe to ignore, say why in a comment.
- Use structured, consistent logging (a real logging library, not scattered `print`/`console.log`) with severity levels (debug/info/warn/error), so logs can be filtered and searched.
- Logs must never contain secrets, credentials, or personal data — ties directly to rule 8's security requirements.
- Error messages surfaced to users should be actionable, not raw stack traces; the full detail belongs in the log, not the UI/API response.

## 14. Linting & formatting

- Every project gets an enforced formatter/linter appropriate to its language (e.g. `ruff`/`black` for Python, `eslint`/`prettier` for JS/TS, `gofmt` for Go), configured in the repo so it runs the same for anyone.
- Formatting is automatic, not a matter of taste or debate — run the formatter rather than hand-formatting to match a style.
- Lint failures should be fixed, not suppressed with a blanket disable — a targeted, commented suppression is fine when a rule genuinely doesn't apply.

## 15. CI automation

- This project gets a CI pipeline (GitHub Actions by default) that runs lint, tests, and a build check on every push/PR.
- CI should fail loudly and specifically — a red check should make it obvious what broke, not just "build failed."
- Keep CI fast enough to actually be used (cache dependencies, avoid unnecessary steps); a CI pipeline that takes 20 minutes for a small project stops being useful.

## 16. Dependency & versioning hygiene

- Lockfiles (`package-lock.json`, `poetry.lock`, `Pipfile.lock`, etc.) are committed, so builds are reproducible.
- Follow semantic versioning (`MAJOR.MINOR.PATCH`) once the project has any real usage, and keep a `CHANGELOG.md` noting what changed at each version, especially breaking changes.
- Update dependencies deliberately, not auto-merged without review — a dependency bump gets a quick look, particularly for anything with licensing (rule 6) or security (rule 8) implications.

## 17. Data privacy

- Don't collect personal or user data without a clear, stated reason tied to the project's function.
- Because the goal is open source (rule 6), nothing sensitive — personal data, internal credentials, private user content — ever gets committed to this public repo, including in test fixtures, sample data, or logs checked into the repo.
- If this project does handle personal data (e.g. accounts, contact info), document what's collected and why in the README, and default to storing the minimum necessary.

## 18. Backups & data durability

- If this project ever holds persistent state (a database, generated models, uploaded prompts/assets), it needs a documented backup/restore procedure — don't assume data loss can't happen.
- Document the backup approach and how to restore from it in `TROUBLESHOOTING.md` or `README.md`, including where backups live and how often they run.
- Before a schema migration or destructive data operation, confirm a backup/rollback path exists.
