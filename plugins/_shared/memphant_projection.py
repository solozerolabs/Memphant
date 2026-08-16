#!/usr/bin/env python3
"""AGENTS.md projection: deliver memory through the always-in-context index.

Evidence (Vercel 2026 eval): an AGENTS.md that is always in context is read
100% of the time; on-demand skills fire only 53-79% (not triggered 56% of the
time). The winning shape is a COMPRESSED index in AGENTS.md pointing at
on-demand files, plus the instruction "prefer retrieval-led reasoning over
pre-training-led reasoning". This module renders exactly that from a recall:

  <cwd>/.memphant/MEMORY.md   ≤8KB, grouped Procedures / Facts / Preferences,
                              one compact line per unit, `[unconfirmed]` when
                              `inclusion_reason` says `captured_unconfirmed`,
                              `[confirmed]` otherwise; stable ordering.
  <cwd>/AGENTS.md             a STABLE managed block between
                              `<!-- memphant:begin -->` / `<!-- memphant:end -->`
                              holding only the retrieval-led instruction + a
                              pointer to `.memphant/MEMORY.md`. It does NOT carry
                              the changing memory index — that lives in MEMORY.md —
                              so a COMMITTED AGENTS.md is written once and never
                              churns across sessions. Created when absent; text
                              outside the markers is never touched.

CLI:  python3 memphant_projection.py --cwd <dir>
      fetches `/v1/recall` (general lane, include_beliefs, limit 20, budget 4096,
      query = "<repo_slug> gotchas conventions contracts procedures") using the
      MEMPHANT_CAPTURE_URL-derived base + bearer + identity env, then renders.
      Fail-safe: any error ⇒ silent, exit 0.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from typing import Callable, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from memphant_capture import DEFAULT_POST_TIMEOUT_SECONDS, repo_slug, resolve_capture_config  # noqa: E402

BEGIN_MARKER = "<!-- memphant:begin -->"
END_MARKER = "<!-- memphant:end -->"
MEMORY_RELPATH = os.path.join(".memphant", "MEMORY.md")
MAX_MEMORY_BYTES = 8 * 1024
MAX_INDEX_BYTES = 2 * 1024
MAX_LINE_CHARS = 240
RECALL_QUERY_SUFFIX = "gotchas conventions contracts procedures"

_INSTRUCTION = (
    "Prior-session memory for this project (conventions, gotchas, external "
    "contracts, procedures) is in `.memphant/MEMORY.md`; read it before deriving — "
    "prefer retrieval-led over pre-training-led reasoning."
)

# The managed block is a ONE-LINE STABLE pointer with inline markers, so a
# committed instructions file gains a single line, byte-identical every session —
# the changing memory index lives in `.memphant/MEMORY.md` (gitignored/on-demand).
STABLE_BLOCK = f"{BEGIN_MARKER} {_INSTRUCTION} {END_MARKER}"

# Agent-instructions files, in priority order. The projection/installer writes the
# pointer into the FIRST that already exists (so a CLAUDE.md-only repo is respected
# and no surprise file appears); if none exist it CREATES AGENTS.md — the de-facto
# standard every coding harness reads.
INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md", "GEMINI.md")


def instructions_path(cwd: str) -> str:
    for name in INSTRUCTION_FILES:
        candidate = os.path.join(cwd, name)
        if os.path.exists(candidate):
            return candidate
    return os.path.join(cwd, "AGENTS.md")
_GROUPS = (("Procedures", ("procedural",)), ("Facts", ("semantic", "belief", "episodic")), ("Preferences", ("preference",)))
_ANCHOR_RE = re.compile(r"[^a-z0-9]+")


# --- normalisation ---------------------------------------------------------

def _items(recall_json) -> list:
    if isinstance(recall_json, dict):
        raw = recall_json.get("items") or []
    elif isinstance(recall_json, list):
        raw = recall_json
    else:
        raw = []
    return [item for item in raw if isinstance(item, dict) and isinstance(item.get("body"), str)]


def _one_line(text: str) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= MAX_LINE_CHARS else text[: MAX_LINE_CHARS - 1].rstrip() + "…"


def _topic(item: dict) -> str:
    """The unit's topic: an explicit `subject`/`topic` when the payload carries
    one, else the first ~6 words of the body."""
    for key in ("subject", "topic"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())[:60]
    words = (item.get("body") or "").split()
    return " ".join(words[:6])[:60]


def _confirmed(item: dict) -> bool:
    return "captured_unconfirmed" not in str(item.get("inclusion_reason") or "")


def _anchor(topic: str) -> str:
    return _ANCHOR_RE.sub("-", topic.lower()).strip("-")[:40] or "item"


def _grouped(items: list) -> list:
    """[(group_name, [item, ...]), ...] in fixed group order; items sorted by
    (confirmed first, topic, unit_id) so the render is stable across runs."""
    out = []
    for name, kinds in _GROUPS:
        members = [it for it in items if str(it.get("kind") or "semantic").lower() in kinds]
        members.sort(key=lambda it: (not _confirmed(it), _topic(it).lower(), str(it.get("unit_id") or "")))
        out.append((name, members))
    return out


# --- renderers -------------------------------------------------------------

def _item_block(item: dict) -> list:
    """One memory as a headed block carrying its FULL body (bullets kept, one
    per line, indented). Fidelity per item is what the agent needs; the file
    stays bounded by dropping WHOLE items from the tail, never by clipping a
    body mid-contract."""
    label = "[confirmed]" if _confirmed(item) else "[unconfirmed]"
    head = f"- {label} ({_anchor(_topic(item))})"
    body_lines = [ln.strip() for ln in (item["body"] or "").splitlines() if ln.strip()]
    if len(body_lines) <= 1:
        return [f"{head} {' '.join(body_lines)}".rstrip()]
    return [head] + [f"  {ln}" for ln in body_lines]


def render_memory_md(items: list) -> str:
    lines = ["# MemPhant memory (projected)", "", "Prefer retrieval-led reasoning over pre-training-led reasoning for anything listed here.", ""]
    for name, members in _grouped(items):
        if not members:
            continue
        lines.append(f"## {name}")
        for item in members:
            lines.extend(_item_block(item))
        lines.append("")
    text = "\n".join(lines).rstrip() + "\n"
    while len(text.encode("utf-8")) > MAX_MEMORY_BYTES:
        # Drop the LAST whole item block (least-confirmed / last-sorted first):
        # from its `- ` head line through its indented body lines.
        kept = text.rstrip("\n").split("\n")
        idx = max((i for i, ln in enumerate(kept) if ln.startswith("- ")), default=None)
        if idx is None:
            break
        end = idx + 1
        while end < len(kept) and kept[end].startswith("  "):
            end += 1
        del kept[idx:end]
        text = "\n".join(kept).rstrip() + "\n"
    return text


def render_index_block(items: list) -> str:
    """The AGENTS.md managed block. STABLE by design (ignores `items`): it is a
    pointer to `.memphant/MEMORY.md`, not the memory index, so a committed
    AGENTS.md never churns. The index itself lives in MEMORY.md."""
    return STABLE_BLOCK


def upsert_managed_block(existing: str, block: str) -> str:
    """Replace the managed block in `existing` (or append it); text outside the
    markers is untouched."""
    start, end = existing.find(BEGIN_MARKER), existing.find(END_MARKER)
    if start != -1 and end != -1 and end >= start:
        return existing[:start] + block + existing[end + len(END_MARKER):]
    if not existing:
        return block + "\n"
    return existing.rstrip("\n") + "\n\n" + block + "\n"


def _write_if_changed(path: str, content: str) -> None:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            if handle.read() == content:
                return
    except OSError:
        pass
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def render_projection(cwd: str, recall_json) -> dict:
    """Write MEMORY.md + upsert the AGENTS.md block. Returns the two paths.
    Idempotent: equal inputs ⇒ byte-identical files (and no rewrite)."""
    items = _items(recall_json)
    memory_path = os.path.join(cwd, MEMORY_RELPATH)
    os.makedirs(os.path.dirname(memory_path), exist_ok=True)
    _write_if_changed(memory_path, render_memory_md(items))
    agents_path = instructions_path(cwd)
    try:
        with open(agents_path, "r", encoding="utf-8") as handle:
            existing = handle.read()
    except OSError:
        existing = ""
    _write_if_changed(agents_path, upsert_managed_block(existing, render_index_block(items)))
    return {"memory": memory_path, "agents": agents_path}


# --- fetch (REST /v1/recall) -----------------------------------------------

def recall_url_from_capture_url(capture_url: str) -> str:
    return re.sub(r"/v1/episodes/?$", "/v1/recall", (capture_url or "").rstrip())


def http_recall_fetch(url: str, bearer: str, identity: dict, timeout: float = DEFAULT_POST_TIMEOUT_SECONDS) -> Callable[[str], dict]:
    def fetch(query: str) -> dict:
        request_body = {
            "subject_id": identity["subject_id"],
            "scope_id": identity["scope_id"],
            "actor_id": identity["actor_id"],
            "agent_node_id": identity["agent_node_id"],
            "subject_generation": identity["subject_generation"],
            "query": query,
            "limit": 20,
            "budget_tokens": 4096,
            "include_beliefs": True,
        }
        data = json.dumps(request_body).encode("utf-8")
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {bearer}"}
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    return fetch


def project(cwd: str, fetch: Optional[Callable[[str], dict]] = None) -> str:
    """Fetch (live unless `fetch` is injected) and render. Returns a status
    code; never raises."""
    try:
        if fetch is None:
            config = resolve_capture_config(require_summarizer=False)
            if config is None:
                return "unconfigured"
            fetch = http_recall_fetch(recall_url_from_capture_url(config["url"]), config["bearer"], config["identity"])
        recall_json = fetch(f"{repo_slug(cwd)} {RECALL_QUERY_SUFFIX}")
        render_projection(cwd, recall_json)
        return "projected"
    except Exception:
        return "projection_error"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Render the MemPhant AGENTS.md projection.")
    parser.add_argument("--cwd", default=os.getcwd())
    args = parser.parse_args(argv)
    if not os.path.isdir(args.cwd):
        print("memphant-projection: skip code=no_cwd", file=sys.stderr)
        return 0
    print(f"memphant-projection: code={project(args.cwd)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
