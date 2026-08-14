# Coding Practice Standards

These are the coding practice standards for this project, agreed with the project owner. They apply on top of (and travel with the repo independently of) any global Claude Code standards.

**Index:** [1](#1-full-absolute-paths-always) Full absolute paths · [2](#2-file-header-block--every-source-file-gets-one) File headers · [3](#3-inline-function-documentation) Inline docs · [4](#4-docker-first-development--cross-platform-windows--linux) Docker · [5](#5-readme--troubleshooting-doc--one-per-project) README/Troubleshooting · [6](#6-aillm-integration--plan-for-ollama-minimize-ongoing-cost) AI/Ollama cost · [7](#7-open-source-is-the-goal--flag-any-paidproprietary-licensing) Open-source licensing · [8](#8-security--secure-by-default) Security · [9](#9-performance--profile-before-optimizing-avoid-known-traps) Performance · [10](#10-cost-controls--default-cheap-guard-against-runaway-spend) Cost controls · [11](#11-testing--cover-the-golden-path-and-edge-cases) Testing · [12](#12-version-control--commits--small-scoped-changes-reviewed-before-merge) Version control · [13](#13-error-handling--logging--no-silent-failures) Error handling · [14](#14-linting--formatting--enforced-not-a-matter-of-taste) Linting · [15](#15-ci-automation--lint-test-and-build-on-every-push) CI · [16](#16-dependency--versioning-hygiene--reproducible-builds-semantic-versions) Dependencies · [17](#17-data-privacy--collect-only-whats-needed-never-commit-it) Data privacy · [18](#18-backups--data-durability--assume-data-loss-is-possible) Backups · [19](#19-license--contributingmd--make-open-source-real) LICENSE · [20](#20-naming-conventions--one-term-one-style-everywhere) Naming · [21](#21-observability--health-checks--make-failures-visible) Observability · [22](#22-config--environment-parity--env-vars-not-hardcoded-branches) Config parity · [23](#23-self-review-checklist--a-last-pass-before-merge) Self-review · [24](#24-architecture-decision-records--capture-the-why) ADRs · [25](#25-claude-code-session-efficiency--spend-tokens-deliberately) Session efficiency

## 1. Full absolute paths, always

- Whenever referencing a file or folder, or telling the owner where to find or run something, give the **full absolute path** (e.g. `/home/user/3d-printing-model-prompt/src/generator.py`) — never a bare filename, a relative path, or an assumed folder.
- Don't assume prior knowledge of this project's folder structure; spell it out every time, even mid-conversation.
- This applies equally to commands, file edits, config locations, and troubleshooting steps.

## 2. File header block — every source file gets one

Every source code file (including helper/utility files, not just entry points) starts with a header comment, in the comment style native to that language, containing:

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

## 3. Inline function documentation

- Every function/method gets an inline doc comment (docstring, JSDoc block, etc. — whatever is idiomatic for the language) describing what it does, its parameters, and its return value.
- Keep these accurate as functions change — a stale docstring is a bug, not a formality.

## 4. Docker-first development — cross-platform (Windows + Linux)

- This project is developed expecting to run in Docker, and includes a `Dockerfile` (plus `docker-compose.yml` if it grows multiple services or dependencies like a database).
- Write code and Dockerfiles so the container behaves identically on Windows and Linux hosts:
  - No hardcoded OS-specific path separators (`/` vs `\`) — use language-native path-joining (`pathlib`/`os.path.join` in Python, `path.join` in Node).
  - Watch for line-ending issues (`LF` vs `CRLF`) in scripts copied into the image; add a `.gitattributes` to normalize line endings for shell scripts or sensitive config.
  - Avoid host-specific assumptions (case-sensitive filesystems, host-only binaries, Windows drive letters) inside the container.
- `README.md` includes the exact Docker build/run commands (`docker build -t <name> .`, `docker run ...` or `docker compose up`), with full absolute paths per rule 1 where relevant.
- If something genuinely can't be containerized (e.g. it needs OS-level access), say so explicitly and explain why, rather than silently skipping Docker.

## 5. README + troubleshooting doc — one per project

This project keeps two docs at its root:

- **`README.md`** — what the project is, setup/install steps, and how to use it.
- **`TROUBLESHOOTING.md`** — a separate, living log of common issues and fixes. Add to it whenever a non-obvious bug or config problem gets solved, so it doesn't get re-debugged from scratch later.

## 6. AI/LLM integration — plan for Ollama, minimize ongoing cost

- The project owner has paid subscriptions to GitHub Copilot and Claude and is fine using them freely for initial development/stand-up. But any AI/LLM capability the *program itself* calls at runtime (not just tools used to write the code) should be designed so it isn't permanently locked into a paid API.
- **Ollama** (local, no-cost inference) is treated as an available, no-cost backend option for this project if it needs LLM/AI functionality at runtime.
- Put any LLM calls behind a small abstraction (a client wrapper, config-driven provider selection) so the backend can be swapped — via an env var or config value — between a paid API (Claude, OpenAI, etc.) and a local Ollama model, without rewriting call sites.
- It's fine to prototype and initially stand up against a paid model for speed/quality, but plan and document a path to switch the default runtime backend to Ollama (or another free/local option) once stood up, to minimize ongoing operating cost.
- If a task genuinely requires a paid model's quality and Ollama can't reasonably substitute, say so explicitly rather than silently defaulting to the paid option.
- Document in `README.md` which backend is the default, which are supported, and how to switch (model name/env var, Ollama setup requirement, etc.).

## 7. Open source is the goal — flag any paid/proprietary licensing

- The default intent for this project is **open source**, not a private/commercial sale. Don't design around a paywall, license key, or proprietary-distribution model unless explicitly told otherwise.
- When choosing a library, framework, SDK, API, or other integration, prefer open-source / permissively-licensed options (MIT, Apache-2.0, BSD) suitable for open-source redistribution.
- **Always warn the project owner explicitly** before integrating anything that:
  - requires purchasing a commercial license or paid tier to use as the project needs it,
  - is "source available" but not OSI-approved or freely redistributable,
  - carries a copyleft license (GPL/AGPL) that could impose obligations on or restrict distribution of the rest of the project, or
  - is free for personal/non-commercial use only, which could be a problem once the project is distributed publicly.
- Never add a dependency with one of the above license issues silently — surface it and let the project owner decide first.
- Note dependency licenses in `README.md` (or a `NOTICE`/`LICENSES` file) so the project's licensing posture stays visible.

## 8. Security — secure by default

- **No secrets in code, ever.** API keys, tokens, passwords, and connection strings go in environment variables or a secrets manager, never hardcoded or committed. `.env` files are `.gitignore`'d, with a checked-in `.env.example` showing required keys as placeholders. A secret that's accidentally committed is treated as compromised — rotate it, don't just delete the commit.
- **Validate and sanitize all external input** (user input, API responses, file uploads, query params) at the boundary. Use parameterized queries/ORM methods to prevent SQL injection, and output-encode to prevent XSS. Never trust client-side validation alone.
- **Least privilege by default** — DB users, API tokens, container users, and file permissions get only the access they need, never broad/admin scope for convenience.
- **Never silently weaken security for convenience.** Disabling TLS verification, wildcard CORS, disabling auth checks, or hardcoding a "just for now" test credential must be flagged and confirmed explicitly — including in code meant to be temporary, since temporary code has a way of shipping.
- **Keep dependencies patched.** Pin versions, and flag known CVEs rather than silently using an affected version. Enable automated dependency scanning where the platform supports it (e.g. GitHub Dependabot).
- **Docker specifically:** use minimal/official base images, avoid running the container process as root when avoidable, and never bake secrets into image layers.

## 9. Performance — profile before optimizing, avoid known traps

- Write clear, correct code first — don't prematurely optimize — but do avoid well-known traps by default: N+1 database queries, O(n²) algorithms on data that can grow, and loading entire files or result sets into memory when streaming or pagination is available.
- Where performance genuinely matters (user-facing latency, large batch jobs), **measure before optimizing** — profile it, don't guess. If an optimization trades off readability, say why in a comment or commit message.
- Use caching deliberately (in-memory, Redis, HTTP cache headers) for expensive or repeated work, and always document the invalidation strategy — a cache with unclear invalidation is a future bug, not a free win.
- Every list/search/query endpoint gets pagination or limits by default — never an unbounded result set.
- Prefer async/non-blocking I/O for network- or disk-bound work where it's idiomatic (Node, Python `asyncio`), especially in code handling concurrent requests.

## 10. Cost controls — default cheap, guard against runaway spend

- Default to free/open-source/self-hosted options before reaching for a paid service (reinforcing rule 6's Ollama-first stance) — if a paid service really is the right call, say so and name the cost driver.
- Any external call that costs money per invocation (LLM APIs, metered SaaS, serverless functions) gets basic guardrails: rate limiting, a request cap, and caching of repeated/identical requests so the same paid call isn't made twice.
- No unbounded loops, uncapped retries, or polling patterns that could spike usage-based billing (compute, API calls, egress) if something misbehaves — always cap and back off.
- Any cloud infrastructure gets budget alerts and resource limits (autoscaling ceilings, timeouts) rather than being left unbounded by default.
- Flag any design choice with a recurring cost implication (managed DB vs. self-hosted, per-invocation serverless pricing vs. a fixed-cost VM) before committing to it, so it's a deliberate call, not a default.

## 11. Testing — cover the golden path and edge cases

- New logic doesn't ship without at least a basic test: the golden/happy path plus one meaningful edge case (empty input, error condition, boundary value).
- Prefer fast, deterministic unit tests close to the code under test; use integration tests for cross-component behavior (DB, API calls) rather than trying to unit-test everything.
- When fixing a bug, add a test that reproduces it first, so it can't silently regress.
- Don't chase 100% coverage as a goal in itself — prioritize business logic, error handling, and anything that's bitten us before (see `TROUBLESHOOTING.md`).

## 12. Version control & commits — small, scoped changes, reviewed before merge

- Use a consistent commit message convention (Conventional Commits style: `feat:`, `fix:`, `docs:`, `chore:`) so history is scannable and could drive an automated changelog later.
- Work happens on a branch, not directly on `main`/`master` — even solo, branch and review the diff before merging, so mistakes get caught before they're baked into history.
- Keep commits scoped to one logical change; don't bundle unrelated fixes into one commit.
- Branch names describe the work (`feature/prompt-templates`, `fix/model-size-validation`), never a generic `update` or `changes`.

## 13. Error handling & logging — no silent failures

- Never swallow an exception or ignore an error return without at least logging it; if something is genuinely safe to ignore, say why in a comment.
- Use structured, consistent logging (a real logging library, not scattered `print`/`console.log`) with severity levels (debug/info/warn/error), so logs can be filtered and searched.
- Logs never contain secrets, credentials, or personal data — ties directly to rule 8's security requirements.
- Error messages surfaced to users are actionable, not raw stack traces; full detail belongs in the log, not the UI/API response.

## 14. Linting & formatting — enforced, not a matter of taste

- Every project gets an enforced formatter/linter for its language (`ruff`/`black` for Python, `eslint`/`prettier` for JS/TS, `gofmt` for Go), configured in the repo so it runs identically for anyone.
- Formatting is automatic — run the formatter rather than hand-formatting to match a style.
- Lint failures get fixed, not suppressed with a blanket disable; a targeted, commented suppression is fine when a rule genuinely doesn't apply.

## 15. CI automation — lint, test, and build on every push

- This project gets a CI pipeline (GitHub Actions by default) that runs lint, tests, and a build check on every push/PR.
- CI fails loudly and specifically — a red check should make it obvious what broke, not just "build failed."
- Keep CI fast enough to actually be used (cache dependencies, skip unnecessary steps); a 20-minute pipeline on a small project stops being useful.

## 16. Dependency & versioning hygiene — reproducible builds, semantic versions

- Lockfiles (`package-lock.json`, `poetry.lock`, `Pipfile.lock`) are committed, so builds are reproducible.
- Follow semantic versioning (`MAJOR.MINOR.PATCH`) once the project has real usage, and keep a `CHANGELOG.md` noting what changed at each version, especially breaking changes.
- Update dependencies deliberately, never auto-merged without review — a dependency bump gets a quick look, particularly for anything with licensing (rule 7) or security (rule 8) implications.

## 17. Data privacy — collect only what's needed, never commit it

- Don't collect personal or user data without a clear, stated reason tied to the project's function.
- Because the goal is open source (rule 7), nothing sensitive — personal data, internal credentials, private user content — ever gets committed to this public repo, including in test fixtures, sample data, or checked-in logs.
- If this project does handle personal data (accounts, contact info), document what's collected and why in the README, and store the minimum necessary.

## 18. Backups & data durability — assume data loss is possible

- If this project ever holds persistent state (a database, generated models, uploaded prompts/assets), it needs a documented backup/restore procedure.
- Document the backup approach and how to restore from it in `TROUBLESHOOTING.md` or `README.md`, including where backups live and how often they run.
- Before a schema migration or destructive data operation, confirm a backup/rollback path exists.

## 19. LICENSE + CONTRIBUTING.md — make "open source" real

- This project gets an actual `LICENSE` file at its root — without one, a public repo isn't really open source, regardless of intent (rule 7). MIT is the default choice; flag it and confirm before picking anything else.
- Once the project is public/stable enough for outside contributions, add a `CONTRIBUTING.md` covering dev environment setup, running tests, and submitting changes.
- If it isn't ready to be public yet, note that in the README rather than leaving licensing ambiguous.

## 20. Naming conventions — one term, one style, everywhere

- Use the naming convention idiomatic to the language (camelCase for JS/TS variables and functions, snake_case for Python, PascalCase for classes/types) — consistently, not mixed within the project.
- Use the same term for the same concept everywhere (don't call the same entity "job" in one file and "task" in another) — pick one term early and stick to it in code, docs, and comments.
- File and directory names are descriptive and consistently cased (all kebab-case or all snake_case, matching language convention).

## 21. Observability & health checks — make failures visible

- Any long-running or service-style component (a server, a background worker, anything running in Docker continuously) exposes a basic health/status check (a `/health` endpoint or equivalent).
- Errors that matter (a failed generation, a lost connection, a crashed worker) surface somewhere visible — not just to a log file nobody's watching. At minimum, log clearly at `error` level; where relevant, surface status in the app itself rather than requiring a log grep to find out something's broken.
- Don't over-build this — a simple health check and clear error logging is enough; full metrics/alerting infrastructure is only worth it once the project has real uptime requirements.

## 22. Config & environment parity — env vars, not hardcoded branches

- All environment-specific values (dev/staging/prod: DB URLs, API endpoints, feature toggles) come from environment variables or config files, never hardcoded.
- Keep a single, documented config schema (one `config.py`/`config.ts` module, or a documented set of env vars in `.env.example`) rather than scattering `if env == "prod"` branches through the codebase.
- Keep dev and prod as close as reasonably possible (same Docker image, different config) so "works on my machine" bugs don't surface only in production — reinforcing rule 4's Docker/cross-platform goal.

## 23. Self-review checklist — a last pass before merge

Before merging any branch into `main` (per rule 12), confirm:
- Tests pass locally (and in CI, per rule 15)
- Docs (`README.md`, `TROUBLESHOOTING.md`, header blocks, inline docs) are updated if behavior changed
- No secrets, credentials, or personal data slipped into the diff
- The diff is scoped to what the commit/PR claims to do — no unrelated changes riding along
- The linter/formatter has been run (rule 14)

## 24. Architecture decision records — capture the "why"

- For any significant design decision (choice of database, framework, architecture pattern, a non-obvious tradeoff), write a short dated note explaining what was decided and why.
- Keep these in `docs/adr/` as numbered files (e.g. `0001-why-sqlite.md`), a few paragraphs each — not a full design doc, just enough that a future reader understands the reasoning without re-deriving it.
- Only write one when the decision was genuinely non-obvious or could reasonably be second-guessed later — routine choices don't need one.

## 25. Claude Code session efficiency — spend tokens deliberately

- **Pick a cheaper model when the task allows it.** Haiku 4.5 is much cheaper than Sonnet/Opus and fine for simple edits, lookups, or mechanical changes — switch with `/model` (an interactive terminal command, not available mid-session).
- **Don't over-delegate to subagents.** Each spawned agent starts cold and re-derives context from scratch, burning extra tokens — only spawn one when a task is genuinely independent or would otherwise bloat the main context.
- **Keep sessions focused.** Prompt caching gives a 1-hour TTL discount, but only while staying on-topic — jumping between unrelated tasks in one long session forces bigger re-reads and defeats caching more than starting fresh would.
- **Avoid dumping huge files/outputs into context.** Ask for targeted reads (specific line ranges, grep results) instead of "read the whole repo" or pasting large logs.
- **Use CLAUDE.md for persistent project context** instead of re-explaining conventions/architecture in every prompt.
- **Trim verbose output requests.** Asking for concise answers/diffs instead of long explanations reduces output tokens, which are pricier than input tokens.
