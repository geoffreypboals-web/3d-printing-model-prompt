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

## 5. README + separate troubleshooting doc

This project keeps two docs at its root:

- **`README.md`** — overview of what the project is, setup/install steps, and how to use it.
- **`TROUBLESHOOTING.md`** — a separate, living document of common issues and their fixes. Add to it whenever a non-obvious bug or configuration problem gets solved, so the same issue doesn't have to be re-debugged from scratch later.
