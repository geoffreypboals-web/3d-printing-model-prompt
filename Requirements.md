# 3d-printing-model-prompt — Requirements

**Purpose of this document:** a living reference for what this project is,
what's built, and what's still open — so a future AI session (or human)
doesn't have to re-derive context from scratch. Update it whenever a
requirement is completed or newly scoped.

## What this project is (as far as it's been defined)

The repo name and `CLAUDE.md` (this project's own coding-practice
standards document) imply the intent: building structured prompts for
generating 3D-printable model descriptions from user input — see
`CLAUDE.md`'s own worked example under "File header block," which
describes a `prompt_builder.py` that "builds structured prompts for
generating 3D-printable model descriptions from user input, and
validates the resulting spec before it's sent to the generation
backend." That example is illustrative, not a confirmed spec — **confirm
actual scope with the project owner before writing real code**, the same
caveat as any other placeholder-named project in this suite.

`CLAUDE.md` also establishes 24 concrete standards this project commits
to once code exists: Docker-first cross-platform development, file
header blocks + inline docstrings on every source file, Ollama for
AI/LLM work (never a paid API, per rule 6), secure-by-default, tested
golden-path-plus-edge-cases, small reviewed commits, no silent failures,
enforced linting, CI on every push, reproducible dependency versions,
and more — read the full file before starting any implementation work
here, since it's more prescriptive than most sibling projects' standards.

## Completed requirements

None. This repository currently contains `CLAUDE.md` (standards, no
code), a blank `README.md`, and this documentation scaffolding pass — no
source code, no tests, no Dockerfile, no LICENSE.

## Open development tasks

1. **Confirm scope with the project owner.** What does "3D printing model
   prompt" concretely mean — a standalone prompt-generation library, a
   CLI tool, a service other suite projects call into (e.g. feeding
   `Shopify-App`'s listing pipeline), something else? Nothing below can
   be scoped further without this answer.
2. **Add the `LICENSE` file this repo's own `CLAUDE.md` requires** (rule
   19: "This project gets an actual LICENSE file at its root — without
   one, a public repo isn't really open source, regardless of intent,"
   MIT stated as the default). This repo is public and currently has
   none — flagged in `Project-Audit-Report.md`'s Top-5 list, item 1.
3. **Once scoped:** set up the project per `CLAUDE.md`'s own rules from
   the start — Dockerfile (rule 4), README with real install/run
   instructions plus a troubleshooting doc (rule 5), CI (rule 15),
   `.env.example` for any config (rule 22) — rather than retrofitting
   compliance with this project's own stated standards after the fact.

See `Project-Audit-Report.md` for the audit finding that first flagged
this repo as unpopulated (note: that report predates this pass — it was
written during a read-only review and is being committed for the first
time alongside this file).
