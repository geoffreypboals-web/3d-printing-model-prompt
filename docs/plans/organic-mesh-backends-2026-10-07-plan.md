# Plan: AI mesh backends (Meshy and TripoSR) with GPU checkout

Owner request, 2026-10-07: link the 3D prompt tool to **Meshy AI** (owner has
the **Pro** plan), add an **open-source** generator as well, and have the local
one **check out a GPU pair only when it's needed**, because AI Story Writer is
the main user of lambda02's GPUs.

Status: plan. Built so far: the Meshy model and prompt library (`scripts/meshy_library_sync.py`, 2026-10-07). Board: WP-61 to WP-65, H-11, H-12.

## 0. Licence check results (WP-61, 2026-10-07)

| Component | Licence | Commercial use | Note |
|---|---|---|---|
| Meshy outputs (Pro plan, incl. API) | Meshy Terms of Use 3.2 | ✅ owned by the customer | Don't remove Meshy's AI-identification metadata; don't use outputs to train AI that competes with Meshy (2.6(xi)) |
| TripoSR code | MIT | ✅ | |
| TripoSR weights (stabilityai/TripoSR) | MIT | ✅ | Model card asks not to make disturbing/offensive content (guideline) |
| torchmcubes | MPL-2.0 | ✅ | File-level copyleft: only changes to its own files must be shared; use as-is |
| rembg (code) | MIT | ✅ | |
| rembg **default** model (BRIA RMBG 2.0) | BRIA licence | ❌ **paid agreement for commercial use** | **Never use the default.** Pin `u2net` (U-2-Net, Apache-2.0 code and weights) |

## 0b. Model and prompt library (owner request, 2026-10-07)

Every Meshy result is kept with its prompt, so prompts can be improved from
real results. `scripts/meshy_library_sync.py` downloads each finished Meshy
task's files and saves `PROMPT.md` (prompt, settings, a notes section) and
`record.json`, plus `index.md`/`index.csv`, into `output/meshy_library/`, and
copies new items to the owner's commercial-licence folder on mastercomputerb:
`C:\Users\maritime407\OneDrive\3d printing\01-Commercial License\00-boals made\Meshy library`.
Meshy's download links expire a few days after a task finishes, so it runs
daily from cron on lambda02 now, and the Meshy backend (WP-62) will run it
after every generation. Items made on the Meshy website show up in the API
lists too; their files are saved only if the sync runs before they expire.


## 1. Why

Prompts are classified SIMPLE (mechanical: brackets, boxes, holders) or
COMPLEX (organic: figurines, animals, characters). SIMPLE prompts go to the code
generators (OpenSCAD templates, CadQuery, FreeCAD) and those work well. COMPLEX
prompts go to `blender_generator`, where a local LLM writes a Blender script.
That is the weak spot: an LLM writing Python can't sculpt a dwarf.

AI mesh generators produce organic shapes directly. The tool already has what
turns an arbitrary mesh into something printable: watertight check and repair
(`watertight.py`), wall thickening (`thickness.py`), molds (`mold.py`), STEP and
thumbnails. So a new backend only has to produce a mesh; the existing pipeline
does the rest.

## 2. What gets built

| Backend | Where it runs | Input | Cost | Quality | When it's used |
|---|---|---|---|---|---|
| **Meshy** (new) | Meshy's cloud, via the API | text or image | credits (Pro: 1,000 a month) | Best | COMPLEX prompts by default when allowed, or on request |
| **TripoSR** (new) | lambda02, one GPU from a checked-out pair | image (text later, phase 4) | free | Fair | When a GPU pair is free, or on request |
| Blender script (existing) | lambda02 CPU + LLM | text | free | Weak for organic shapes | Fallback |
| OpenSCAD / CadQuery / FreeCAD (existing) | lambda02 | text | free | Good for mechanical parts | SIMPLE prompts, unchanged |

Selection (`ORGANIC_BACKEND`, default `auto`) for COMPLEX prompts:
1. A request may name a backend (`"backend": "meshy" | "triposr" | "blender"`).
2. `auto`: **TripoSR** if a GPU pair can be checked out now (input is an
   image), else **Meshy** if today's credit budget allows, else **Blender**.
3. An explicit request for TripoSR waits for a pair (up to
   `GPU_WAIT_MINUTES`, default 30) and reports "waiting for a GPU" meanwhile.

## 3. Meshy backend

### API (from docs.meshy.ai, checked 2026-10-07)
- Auth: `Authorization: Bearer <key>`. Pro includes API keys. Free-tier models
  are CC BY; Pro models are owned outright, so selling prints is fine.
- Text to 3D: `POST https://api.meshy.ai/openapi/v2/text-to-3d` with
  `mode: "preview"` (geometry), then optionally `mode: "refine"` (textures).
  Fields used: `prompt` (max 800 chars), `ai_model: "latest"`,
  `should_remesh: true`, `topology: "triangle"`, `target_polycount`
  (100-300,000), `target_formats: ["stl"]` (add `"3mf"` for multi-colour).
- Image to 3D: `POST https://api.meshy.ai/openapi/v1/image-to-3d` with
  `image_url` (public URL or base64 data URI, jpg/png), `should_texture`,
  `should_remesh`, `target_polycount`, `target_formats`. **30 credits** per
  task (docs example).
- Status: `GET .../{id}` (poll) or `GET .../{id}/stream` (server-sent
  events): `PENDING`, `IN_PROGRESS`, `SUCCEEDED`, `FAILED` (credits refunded),
  `CANCELED`. Results in `model_urls.{stl,3mf,glb,...}` as **signed URLs that
  expire**: download immediately. `consumed_credits` gives the actual cost.

### How we use it
- **Single-colour prints need geometry only:** text-to-3D runs the preview
  step and skips refine; image-to-3D sets `should_texture: false`. That saves
  credits and time. Refine/texture only for a multi-colour (3MF) request.
- Poll every 5 s (cap 15 min), then download the STL into the model folder,
  record `meshy_task_id`, `consumed_credits` and `ai_model` in the model's spec.
- Then the normal pipeline: scale to the requested size (new
  `target_height_mm`, see 5), repair, thicken, thumbnail.

### Cost and safety controls
- `MESHY_API_KEY` only in `.env` (gitignored), never logged, never in an
  image. Requests fail clearly if it's unset.
- **Credit budget:** `MESHY_DAILY_CREDIT_CAP` (default 150) and
  `MESHY_MONTHLY_CREDIT_CAP` (default 800 of the 1,000), counted from
  `consumed_credits` in a small ledger (`data/meshy_ledger.jsonl`). Over the cap,
  `auto` skips Meshy and an explicit request is refused with the numbers.
- **The existing prompt cache** already returns an identical earlier prompt
  without generating again, so a repeat costs nothing.
- One Meshy task at a time; retries only on network errors, never on FAILED;
  no retry loop that could spend credits.
- `GET /meshy/usage`: credits used today and this month, the caps, the last
  tasks.
- Prompts and images leave the house (Meshy's cloud). Shown in the UI next to
  the backend choice.

## 4. TripoSR backend (local, open source)

- **TripoSR** (Stability AI + Tripo), MIT licence, image to mesh in seconds,
  about 6 GB of GPU memory. Fits one RTX A4000.
- Rejected, with reasons (owner rule: tools must allow commercial use):
  TRELLIS/TRELLIS.2 depend on nvdiffrast, which is non-commercial, and v2 needs
  24 GB on one GPU; Hunyuan3D's licence excludes the EU/UK/South Korea and has a
  user cap; Stable Fast 3D's licence is revenue-capped.
- Runs in a separate **mesh-worker** container (PyTorch + CUDA, a few GB), not
  in the main app image, so the app stays small and starts without a GPU. It's
  given all GPUs (`nvidia.com/gpu=all`, the NVIDIA container runtime is already
  on lambda02) and each job runs as a subprocess with
  `CUDA_VISIBLE_DEVICES=<the checked-out GPU>`. The model is loaded per job and
  the process exits after it, so it **holds no GPU memory between jobs**.
- The worker listens on the Docker bridge only (`172.17.0.1`), like the app;
  the app calls `POST /mesh` with the image and gets an OBJ/STL back.
- Background removal before TripoSR: `rembg` (MIT) with the **`u2net`** model
  pinned (Apache-2.0). Its default model, BRIA RMBG, needs a paid commercial
  licence and must not be used.
- Output is scaled, repaired and thickened by the same pipeline as Meshy.

## 5. Printability (both new backends)

AI meshes aren't made for printers: arbitrary scale, holes, thin parts, no
flat base. Added to the pipeline, in order:
1. **Scale** to `target_height_mm` (new optional request field; default 80 mm).
2. **Repair** to watertight (existing `watertight.py` / `repair`).
3. **Thicken** walls below the minimum (existing `thickness.py`).
4. **Flat base** option: cut at the lowest few percent of height so it stands
   (new, small, trimesh plane cut).
5. **Report**: watertight yes/no, smallest wall, triangle count, volume,
   estimated print size, in the model's spec and the response.

## 6. GPU checkout (shared with AI Story Writer)

### Today
- Power layout: Ollama `ollama-pair01` on GPUs 0+1 (`:11434`) and `ollama-pair`
  on GPUs 2+3 (`:11436`), each holding `gemma3:27b` (about 24 GB with its
  context, across the pair's 32 GB).
- AI Story Writer pins each book to a pair (`data/gpu_pins.json`) and runs one
  job per book; its review/rewrite runs keep a pair busy for hours.
- Light users share the pairs too (3D Printer Workshop Manager's and this
  tool's LLM calls on `:11436`).
- There is no way for another app to borrow a pair.

### The rule
**AI Story Writer has priority.** The 3D tool may check out a pair only when
no Story Writer job is running on it, and Story Writer never waits more than
one 3D job for it.

### Lease registry (new, host level)
- A shared folder `/data/stacks/gpu-leases/` on lambda02, mounted into the
  Story Writer container and the 3D tool's containers.
- A lease is a file `pair01.json` / `pair23.json`:
  `{holder, job_id, gpus, port, since, expires, heartbeat}`, created atomically
  (`O_CREAT|O_EXCL`), so two apps can't both get it.
- Expiry: a lease lapses at `expires` (job estimate + margin, max 20 min) or
  when `heartbeat` is older than 60 s, so a crashed holder can't keep a pair.
- Library: one small module (`gpu_lease.py`) with `acquire(pair, holder,
  minutes)`, `heartbeat()`, `release()`, `holder_of(pair)`, `wanted(pair)`,
  copied into both apps (or a tiny shared package), with tests.

### Checking out a pair (3D tool)
1. **Free?** Read AI Story Writer's `GET /api/activity` (no sign-in needed).
   This needs one small Story Writer change: each job lists the **endpoint**
   (pair) it runs on. A pair is free if no running or queued Story Writer job
   uses it and no lease exists. Prefer the pair whose books are least likely
   to start soon (no queued job pinned to it).
2. **Acquire** the lease.
3. **Make room:** ask that pair's Ollama to unload its models (`POST
   /api/generate {"model": m, "keep_alive": 0}` for each model in `/api/ps`).
   TripoSR needs about 6 GB and gemma3:27b leaves too little beside it.
4. **Run** TripoSR on one GPU of the pair, sending a heartbeat every 15 s.
5. **Release** the lease. Ollama reloads its model on the next request (about
   30-60 s, paid once by whoever calls next).

### AI Story Writer side (small changes there)
- `/api/activity`: add each job's endpoint.
- Before a job starts on a pair (`web_jobs` / `gpu_pins`), check for a lease:
  - none: run as now;
  - leased: write a **want** file (`pair23.want`) and wait, showing "waiting
    for GPU pair 2+3 (checked out by the 3D prompt tool, about N min)". The 3D
    tool sees the want file, finishes the mesh in progress, releases, and
    takes no new job on that pair while the want file exists.
- A lease never interrupts a running Story Writer job (it can't be granted
  while one runs).

### Visibility
- SuiteControl's GPU host card shows each pair's lease: "GPUs 2+3: checked out
  by 3D prompt tool since 14:05 (TripoSR)".
- The 3D tool's UI shows "waiting for a GPU (AI Story Writer is using both
  pairs)" and offers Meshy instead.

## 7. API and UI changes (3D tool)

- `POST /generate`: optional `backend`, `image` (upload or URL),
  `target_height_mm`, `flat_base`. Response adds `backend` values `meshy` and
  `triposr`, credits used, printability report, and `status: waiting_for_gpu`
  for a queued TripoSR job (generation becomes a background job with
  `GET /jobs/{id}` for the wait and the Meshy poll).
- `GET /meshy/usage`, `GET /gpu/status` (leases, what's free).
- Web page: backend picker (Auto / Meshy / Local GPU / Blender), image upload,
  target height, credits left, waiting state.
- 3D Printer Workshop Manager's AI Model Generator calls `/generate` and gets
  the same options later (its own PR).

## 8. Configuration

| Setting | Default | Meaning |
|---|---|---|
| `ORGANIC_BACKEND` | `auto` | `auto`, `meshy`, `triposr`, `blender` for COMPLEX prompts |
| `MESHY_API_KEY` | unset | Meshy key (`.env` only) |
| `MESHY_AI_MODEL` | `latest` | Meshy model |
| `MESHY_TARGET_POLYCOUNT` | 100000 | Mesh detail |
| `MESHY_DAILY_CREDIT_CAP` / `MESHY_MONTHLY_CREDIT_CAP` | 150 / 800 | Credit budget |
| `MESH_WORKER_URL` | `http://172.17.0.1:8010` | The TripoSR worker |
| `GPU_LEASE_DIR` | `/data/stacks/gpu-leases` | Shared lease folder |
| `GPU_WAIT_MINUTES` | 30 | How long an explicit TripoSR request waits for a pair |
| `STORYWRITER_ACTIVITY_URL` | `https://lambda02.tail40d10c.ts.net:8900/api/activity` | Where to see Story Writer's jobs (inside lambda02: `https://host.docker.internal:8900/api/activity`) |

## 9. Phases

| Phase | What | Repos | Done when |
|---|---|---|---|
| 0 | Licence and install gate: Meshy API terms for outputs, TripoSR code+weights, rembg+weights, torchmcubes; licence register rows | local-ai-infrastructure | All rows commercial-OK, install approved |
| 1 | **Meshy backend**: client, credit ledger and caps, preview-only geometry, STL download, scale/repair/thicken, `/meshy/usage`, UI picker | this repo | 5 test prompts produce watertight, sliceable STLs; caps tested with a fake server; key never logged |
| 2 | **GPU lease registry** + Story Writer activity endpoint field + Story Writer lease check and want file + SuiteControl display | local-ai-infrastructure, AI_Story_Writer, SuiteControl | Two-process lease tests pass; a Story Writer job waits at most one 3D job; no lease while a Story Writer job runs |
| 3 | **TripoSR worker** (image to mesh) using the lease, Ollama unload, per-job process | this repo | An image becomes a printable STL on a free pair; no GPU memory held after the job; works while Story Writer runs on the other pair |
| 4 | Text to image for TripoSR (so text prompts can use it): pick a commercial-OK model (e.g. FLUX.1-schnell, Apache-2.0, needs a whole pair) or Meshy's text-to-image | this repo | Licence checked; blind comparison vs Meshy |
| 5 | Workshop Manager integration (backend option, credits shown) | 3dPrinterWorkshopManager | Generate from the Workshop UI with each backend |

## 10. Tests

- Unit: Meshy client against a fake HTTP server (preview flow, FAILED refund,
  expired URL, cap reached, key missing); lease acquire/expire/heartbeat/want
  with two processes; `auto` selection table; scaling and flat base.
- Story Writer: a job pinned to a leased pair waits and shows why; no lease
  is granted while its job runs.
- Live, recorded in `docs/` with dates: 5 organic prompts x (Meshy, TripoSR
  from a photo, Blender): watertight after repair, smallest wall, slices
  cleanly in the Workshop's slicer, time, credits; a blind look at the
  results.

## 11. Risks

- **Credits:** capped per day and month; cache prevents repeats.
- **Story Writer slowed:** only free pairs are leased; the want file bounds a
  wait to one 3D job; the first Story Writer call after a lease reloads the
  model (about a minute).
- **AI meshes that won't print:** repair/thicken/flat base plus the report;
  failures are shown, not hidden.
- **Meshy API changes or model retirement** (e.g. `lowpoly` retires
  2026-10-30): model name in settings; the client reads `consumed_credits`
  rather than assuming prices.
- **Privacy:** Meshy prompts and images go to the cloud; local TripoSR keeps
  them at home.

## Sources
- Meshy API quick start: https://docs.meshy.ai/en/api/quick-start
- Meshy text to 3D: https://docs.meshy.ai/en/api/text-to-3d
- Meshy image to 3D: https://docs.meshy.ai/en/api/image-to-3d
- Meshy pricing: https://www.meshy.ai/pricing
- nvdiffrast licence (non-commercial): https://github.com/NVlabs/nvdiffrast/blob/main/LICENSE.txt
- Open-source image-to-3D comparison: https://builderai.tools/blog/single-image-to-3d-model-pipeline-2026
