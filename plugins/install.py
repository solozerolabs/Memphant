#!/usr/bin/env python3
"""One-command MemPhant install: `python3 plugins/install.py`.

Wires MemPhant memory into a coding-agent harness AND the current repo so the
user edits nothing by hand. Idempotent — safe to re-run; a second run changes
nothing. Every step is independent and best-effort: a failure in one prints a
warning and the rest still run.

What it does:
  1. REPO   — write the STABLE MemPhant block into <repo>/AGENTS.md (retrieval-led
              instruction + a pointer to .memphant/MEMORY.md) and add `.memphant/`
              to <repo>/.gitignore. This alone makes the always-in-context `file`
              and `cli` surfaces work (the surfaces that actually got used).
  2. HOOKS  — register the UserPromptSubmit (inject) + Stop (capture/project) hooks
              for the detected harness(es): Codex (~/.codex or $CODEX_HOME) and
              Claude Code (~/.claude). Merged into the harness hooks.json by
              command string, so re-running never duplicates an entry.
  3. NEXT   — print the two deployment-specific env vars the user must set
              (endpoint + key); those cannot be guessed.

Usage:
  python3 plugins/install.py [--repo DIR] [--harness auto|codex|claude-code|none]
                             [--print-env]
Stdlib only; never raises; exit 0 unless --repo is not a directory.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

PLUGIN_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(PLUGIN_ROOT, "_shared"))
from memphant_projection import STABLE_BLOCK, instructions_path, upsert_managed_block  # noqa: E402

# The two hooks each harness wires, as (event, plugin-relative script).
_CODEX_HOOKS = [
    ("UserPromptSubmit", "codex-memphant/hooks/user_prompt_submit.py", 8),
    ("Stop", "codex-memphant/hooks/session_capture.py", 20),
]
_CLAUDE_HOOKS = [
    ("UserPromptSubmit", "claude-code-memphant/hooks/memphant_hook.py", 8),
    ("Stop", "claude-code-memphant/hooks/capture_session.py", 20),
]


# --- repo wiring (surface: file + cli) ------------------------------------

def write_agents_block(repo: str) -> str:
    """Upsert the one-line MemPhant pointer into the repo's agent-instructions
    file — an existing AGENTS.md/CLAUDE.md/GEMINI.md, else a newly created
    AGENTS.md. Returns a status naming the file (e.g. `AGENTS.md:written`)."""
    path = instructions_path(repo)
    name = os.path.basename(path)
    try:
        existing = open(path, encoding="utf-8").read()
    except OSError:
        existing = ""
    updated = upsert_managed_block(existing, STABLE_BLOCK)
    if updated == existing:
        return f"{name}:unchanged"
    open(path, "w", encoding="utf-8").write(updated)
    return f"{name}:{'created' if not existing else 'written'}"


def gitignore_memphant(repo: str) -> str:
    """Add `.memphant/` to <repo>/.gitignore if absent (the projection + receipt
    are session-local). Returns a status."""
    path = os.path.join(repo, ".gitignore")
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        lines = []
    if any(ln.strip().rstrip("/") == ".memphant" for ln in lines):
        return "gitignore:present"
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(("" if not lines or lines[-1] == "" else "\n") + ".memphant/\n")
    return "gitignore:added"


# --- harness wiring (surface: hooks) --------------------------------------

def merge_hooks(existing: dict, plugin_root: str, hooks: list) -> dict:
    """Return `existing` hooks.json with our entries merged in — appended to each
    event, keyed by command string so a re-run never duplicates. Never removes a
    user's own hooks."""
    root = existing.get("hooks") if isinstance(existing.get("hooks"), dict) else {}
    merged = {event: list(entries) for event, entries in root.items() if isinstance(entries, list)}
    for event, script, timeout in hooks:
        command = f'python3 "{os.path.join(plugin_root, script)}"'
        entries = merged.setdefault(event, [])
        already = any(
            isinstance(g, dict)
            and any(isinstance(h, dict) and h.get("command") == command for h in g.get("hooks", []))
            for g in entries
        )
        if not already:
            entries.append({"hooks": [{"type": "command", "command": command, "timeout": timeout}]})
    out = dict(existing)
    out["hooks"] = merged
    return out


def register_hooks(config_dir: str, plugin_root: str, hooks: list) -> str:
    """Merge our hooks into <config_dir>/hooks.json. Returns a status."""
    if not config_dir or not os.path.isdir(config_dir):
        return "hooks:no_config_dir"
    path = os.path.join(config_dir, "hooks.json")
    try:
        existing = json.load(open(path, encoding="utf-8"))
        if not isinstance(existing, dict):
            existing = {}
    except (OSError, ValueError):
        existing = {}
    merged = merge_hooks(existing, plugin_root, hooks)
    before = json.dumps(existing, sort_keys=True)
    if json.dumps(merged, sort_keys=True) == before:
        return "hooks:unchanged"
    json.dump(merged, open(path, "w", encoding="utf-8"), indent=2)
    return "hooks:registered"


def detect_harnesses() -> list:
    """[(name, config_dir, hooks), ...] for each harness config dir present."""
    found = []
    codex = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
    if os.path.isdir(codex):
        found.append(("codex", codex, _CODEX_HOOKS))
    claude = os.path.expanduser("~/.claude")
    if os.path.isdir(claude):
        found.append(("claude-code", claude, _CLAUDE_HOOKS))
    return found


_ENV_HELP = """\
Set these (deployment-specific — the endpoint + key for your MemPhant server):
  export MEMPHANT_MCP_URL=http://127.0.0.1:8092/mcp        # hooks arm (MCP inject)
  export MEMPHANT_CAPTURE_URL=http://127.0.0.1:8091/v1/episodes  # capture + file/cli
  export MEMPHANT_API_KEY=mk_...                            # your coding key
  export MEMPHANT_URL=http://127.0.0.1:8091                 # CLI base (else derived)
The identity ids (MEMPHANT_SUBJECT_ID/SCOPE_ID/ACTOR_ID/AGENT_NODE_ID/
SUBJECT_GENERATION) come from your bind_context handshake."""


def install(repo: str, harness: str = "auto", plugin_root: str = PLUGIN_ROOT) -> list:
    steps = [write_agents_block(repo), gitignore_memphant(repo)]
    targets = [] if harness == "none" else detect_harnesses()
    if harness not in ("auto", "none"):
        targets = [t for t in targets if t[0] == harness]
        if not targets:
            steps.append(f"hooks:{harness}_not_found")
    for name, config_dir, hooks in targets:
        steps.append(f"{name}:{register_hooks(config_dir, plugin_root, hooks)}")
    return steps


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Install MemPhant into a harness + repo.")
    parser.add_argument("--repo", default=os.getcwd())
    parser.add_argument("--harness", default="auto", choices=["auto", "codex", "claude-code", "none"])
    parser.add_argument("--print-env", action="store_true", help="print the env vars to set and exit")
    args = parser.parse_args(argv)
    if args.print_env:
        print(_ENV_HELP)
        return 0
    if not os.path.isdir(args.repo):
        print(f"memphant-install: --repo is not a directory: {args.repo}", file=sys.stderr)
        return 2
    print("MemPhant install:")
    for step in install(args.repo, args.harness):
        print(f"  {step}")
    print()
    print(_ENV_HELP)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
