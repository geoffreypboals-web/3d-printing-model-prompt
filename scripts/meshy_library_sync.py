"""
Project: 3D Printing Model Prompt
File: scripts/meshy_library_sync.py
Description: Keeps a local library of every model made with Meshy (owner
    request 2026-10-07): for each finished Meshy task it downloads the model
    files, textures, images and thumbnail, and saves the exact prompt and
    settings next to them, so there is a library of prompts and results to
    learn from. Optionally copies new items to the owner's commercial-licence
    folder on mastercomputerb. Meshy's download links expire a few days after
    a task finishes, so this should run soon after every generation (the Meshy
    backend will call it; until then, run it by hand or from cron).
Inputs: MESHY_API_KEY (environment or .env; never printed). Options:
    --library (default output/meshy_library), --copy-to (an scp target such as
    'mastercomputerb:C:/Users/maritime407/OneDrive/3d printing/01-Commercial License/00-boals made'),
    --refresh (re-download items already in the library).
Outputs: <library>/<date>_<type>_<name>_<id8>/ with the files, record.json
    (the task as Meshy returned it, minus download links) and PROMPT.md;
    <library>/index.csv and index.md (one row per item). Exit code 1 if any
    item failed to download.
Troubleshooting:
    - "expired" in the report: Meshy no longer serves that item's files;
      export it from the Meshy website instead. Its prompt is still saved.
    - Copy fails: check `ssh mastercomputerb` works without a password and
      the folder exists.
    - An item made on the Meshy website is missing: the API lists tasks made
      through the API and Meshy's own tools; download others from the site.
"""
import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.meshy.ai/openapi"
# Every task list Meshy's API offers; a 404 for one is skipped.
ENDPOINTS = ["v2/text-to-3d", "v1/image-to-3d", "v1/multi-image-to-3d", "v1/text-to-image",
             "v1/image-to-image", "v1/remesh", "v1/retexture", "v1/rigging", "v1/animations"]
PROMPT_FIELDS = ["prompt", "object_prompt", "style_prompt", "texture_prompt", "negative_prompt",
                 "text_style_prompt", "art_style", "ai_model", "model_type", "topology", "target_polycount",
                 "should_remesh", "should_texture", "enable_pbr", "texture_resolution", "hd_texture",
                 "texture_image_url", "image_style_url"]
LICENCE_NOTE = ("Made with Meshy on a paid (Pro) plan: the customer owns this output (Meshy Terms of Use 3.2); "
                "commercial use and selling prints allowed. Don't remove Meshy's AI-identification metadata; "
                "don't use these files to train AI models that compete with Meshy.")


def api_key() -> str:
    """MESHY_API_KEY from the environment, else from the repo's .env."""
    key = os.environ.get("MESHY_API_KEY", "").strip()
    if not key and (ROOT / ".env").exists():
        for line in (ROOT / ".env").read_text().splitlines():
            if line.startswith("MESHY_API_KEY="):
                key = line.split("=", 1)[1].strip()
    if not key:
        sys.exit("MESHY_API_KEY isn't set (environment or .env).")
    return key


def get_json(path: str, key: str):
    """GET an API path; None on 404 (that list doesn't exist)."""
    req = urllib.request.Request(f"{API}/{path}", headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def list_tasks(key: str) -> list:
    """Every task from every list endpoint, all pages."""
    tasks, seen = [], set()
    for endpoint in ENDPOINTS:
        page = 1
        while True:
            data = get_json(f"{endpoint}?page_num={page}&page_size=50", key)
            if data is None:
                break
            items = data if isinstance(data, list) else data.get("result") or data.get("data") or []
            for item in items:
                item.setdefault("_endpoint", endpoint)
                # Two lists can return the same task (text-to-image also
                # lists image-to-image ones): keep each task once.
                if item.get("id") not in seen:
                    seen.add(item.get("id"))
                    tasks.append(item)
            if len(items) < 50:
                break
            page += 1
    return tasks


def slug(text: str) -> str:
    """A short filesystem-safe name (also safe on Windows)."""
    text = re.sub(r"[^A-Za-z0-9]+", "-", text or "").strip("-").lower()
    return text[:40] or "untitled"


def urls_of(task: dict) -> list:
    """(label, url) for every downloadable file in a task."""
    out = []
    for fmt, url in (task.get("model_urls") or {}).items():
        if url:
            out.append((f"model.{fmt}", url))
    if task.get("thumbnail_url"):
        out.append(("thumbnail", task["thumbnail_url"]))
    for i, textures in enumerate(task.get("texture_urls") or []):
        for kind, url in (textures or {}).items():
            if url:
                out.append((f"texture_{i}_{kind}", url))
    for field in ("image_urls", "multiview_image_urls"):
        for i, url in enumerate(task.get(field) or []):
            if url:
                out.append((f"{field[:-5]}_{i}", url))
    return out


def extension(url: str, label: str) -> str:
    """File extension from the URL path, or from the label for model.<fmt>."""
    ext = Path(urllib.parse.urlparse(url).path).suffix.lower()
    if label.startswith("model.") and not ext:
        ext = "." + label.split(".", 1)[1]
    return ext or ".bin"


def strip_urls(task: dict) -> dict:
    """The task record without its (expiring, signed) download links."""
    clean = {}
    for k, v in task.items():
        if k.endswith("_url") or k.endswith("_urls") or k == "model_url":
            continue
        clean[k] = v
    return clean


def when(ms) -> str:
    """Meshy's millisecond timestamp as 'YYYY-MM-DD HH:MM UTC' ('' if missing)."""
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError):
        return ""


def main_prompt(task: dict) -> str:
    """The task's most descriptive prompt text (prompt, then the others)."""
    for field in ("prompt", "object_prompt", "texture_prompt", "style_prompt", "text_style_prompt"):
        if task.get(field):
            return task[field]
    return ""


def save_item(task: dict, library: Path, refresh: bool) -> dict:
    """Downloads one task's files and writes record.json and PROMPT.md."""
    date = when(task.get("created_at"))[:10] or "undated"
    folder = library / f"{date}_{task.get('type') or task['_endpoint'].split('/')[-1]}_{slug(task.get('name') or main_prompt(task))}_{task['id'][:8]}"
    record = folder / "record.json"
    row = {"date": date, "type": task.get("type", ""), "name": task.get("name", ""), "prompt": main_prompt(task),
           "ai_model": task.get("ai_model", ""), "status": task.get("status", ""), "task_id": task["id"],
           "folder": folder.name, "files": "", "problems": ""}
    if record.exists() and not refresh:
        saved = json.loads(record.read_text())
        row["files"] = ", ".join(saved.get("_files", []))
        row["problems"] = ", ".join(saved.get("_problems", []))
        return row | {"_new": False}
    folder.mkdir(parents=True, exist_ok=True)
    files, problems = [], []
    for label, url in urls_of(task):
        target = folder / f"{label}{extension(url, label)}"
        try:
            with urllib.request.urlopen(url, timeout=120) as resp:
                target.write_bytes(resp.read())
            files.append(target.name)
        except urllib.error.HTTPError as exc:
            problems.append(f"{label}: {'expired' if exc.code in (403, 404) else exc.code}")
        except urllib.error.URLError as exc:
            problems.append(f"{label}: {exc.reason}")
    data = strip_urls(task) | {"_files": files, "_problems": problems, "_saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                               "_licence": LICENCE_NOTE}
    record.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    lines = [f"# {task.get('name') or main_prompt(task)[:60] or task['id']}", "",
             f"- Type: {task.get('type', '')} ({task['_endpoint']})", f"- Made: {when(task.get('created_at'))}",
             f"- Status: {task.get('status', '')}; credits: {task.get('consumed_credits', '')}",
             f"- Meshy task: {task['id']}", "", "## Prompt and settings", ""]
    for field in PROMPT_FIELDS:
        if task.get(field) not in (None, "", [], False):
            lines.append(f"- **{field}**: {task[field]}")
    lines += ["", "## Files", ""] + [f"- {f}" for f in files] + [f"- (not saved) {p}" for p in problems]
    lines += ["", "## Licence", "", LICENCE_NOTE, "", "## Notes for improving the prompt", "",
              "- What came out well:", "- What to change next time:", ""]
    (folder / "PROMPT.md").write_text("\n".join(lines), encoding="utf-8")
    row["files"], row["problems"] = ", ".join(files), ", ".join(problems)
    return row | {"_new": True}


def write_index(library: Path, rows: list) -> None:
    """index.csv and index.md: one row per item, newest first."""
    rows = sorted(rows, key=lambda r: r["date"], reverse=True)
    cols = ["date", "type", "name", "prompt", "ai_model", "status", "files", "problems", "folder", "task_id"]
    with open(library / "index.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    md = ["# Meshy model and prompt library", "",
          f"Updated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}. {len(rows)} items. "
          "Each folder has the files, PROMPT.md (prompt, settings, notes) and record.json.", "",
          "| Date | Type | Name | Prompt | Files |", "|---|---|---|---|---|"]
    for r in rows:
        prompt = (r["prompt"] or "").replace("|", "/").replace("\n", " ")[:120]
        md.append(f"| {r['date']} | {r['type']} | [{r['name'] or r['folder']}]({r['folder']}/PROMPT.md) | {prompt} | "
                  f"{len(r['files'].split(', ')) if r['files'] else 0}{' (some expired)' if r['problems'] else ''} |")
    (library / "index.md").write_text("\n".join(md) + "\n", encoding="utf-8")


def copy_to(library: Path, target: str, folders: list) -> bool:
    """scp new item folders and the index to `target` (host:path)."""
    host, path = target.split(":", 1)
    ok = True
    for name in folders + ["index.csv", "index.md"]:
        proc = subprocess.run(["scp", "-q", "-r", str(library / name), f'{host}:"{path}/"'],
                              capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            print(f"  copy failed for {name}: {proc.stderr.strip()[:200]}")
            ok = False
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description="Download every Meshy model and its prompt into a local library.")
    ap.add_argument("--library", default=str(ROOT / "output" / "meshy_library"))
    ap.add_argument("--copy-to", default="")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--copy-all", action="store_true", help="copy every item, not only new ones")
    args = ap.parse_args()
    key = api_key()
    library = Path(args.library)
    library.mkdir(parents=True, exist_ok=True)
    tasks = [t for t in list_tasks(key) if t.get("status") == "SUCCEEDED"]
    rows = [save_item(t, library, args.refresh) for t in tasks]
    write_index(library, rows)
    new = [r["folder"] for r in rows if r["_new"]]
    bad = [r for r in rows if r["problems"]]
    print(f"{len(rows)} items in {library} ({len(new)} new); {len(bad)} with files Meshy no longer serves")
    for r in bad:
        print(f"  {r['folder']}: {r['problems']}")
    to_copy = [r["folder"] for r in rows] if args.copy_all else new
    if args.copy_to and to_copy:
        print("copied to " + args.copy_to if copy_to(library, args.copy_to, to_copy) else "copy had errors")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
