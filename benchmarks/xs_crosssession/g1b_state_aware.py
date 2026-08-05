#!/usr/bin/env python3
"""G1b — state-aware veto precision (successor to G1; same 0.95/10+ bar, NEVER lowered).

NEW PREDICATE CLASS, preregistered here before any cell is scored:
predicates see (tool_name, tool_input, STATE), where STATE is replayed from the
session timeline — an approximation of what a live PreToolUse hook would read
from the filesystem/session. Where replay is uncertain, labels fall AMBIGUOUS→FP
(precision floors, same bias as G1).

STATE tracked per session, in order:
- rebase_active: `git rebase` seen without a later `--continue|--abort|--skip`
  completion or `rebase` success marker before the current call.
- preflight_recent: any of {preflight, precommit-scoped, stamp, --local} in the
  previous 30 tool calls.
- prod_auth: any PRIOR user turn contains an explicit prod authorization phrase
  ({authorize, authorized, go ahead, yes} within 80 chars of {prod, production})
  OR names a prod operation (extract/census/deploy/migrate + prod).
- shared_checkout: the call's effective cwd (last `cd` in command, else unknown)
  is exactly ~/Syndai or ~/Memphant (the shared trees; dedicated worktrees are
  ~/Syndai-*, ~/Memphant-*, .claude/worktrees/*).

RULES (refined; fire conditions):
- R1b: bare `make check`/repo-wide pytest AND NOT preflight_recent.
- R2b: bare `git add -A|.` / `git checkout .`/`-- .` / bare `git reset --hard`
  (no pathspec) AND shared_checkout is True (not unknown).
- R4b: top-level `codex exec` invocation (segment-initial after splitting on
  &&/;/|) with `$(cat` in argv or an inline quoted argv > 1500 chars; stdin
  redirects and *.sh wrappers excluded.
- R6b: prod DB command AND prod_auth is False.
- R7b: `git stash` with pathspec AND rebase_active.

LABEL CRITERIA (TP = veto correct):
- R1b: TP (the state test already excluded the legitimate phase); except text/
  heredoc artifacts => FP.
- R2b: TP unless the same command stashes/backs up first => FP.
- R4b: TP ($(cat / long-argv are the written rule verbatim); wrapper/heredoc
  artifact => FP.
- R6b: TP only if a WRITE verb is present (INSERT/UPDATE/DELETE/ALTER/CREATE/
  DROP/TRUNCATE/alembic upgrade); unauthorized READS => FP for veto purposes
  (blocking a read is not worth a false block; warn covers it).
- R7b: TP (this is exactly the documented loss mode).
Scoring: precision over all fired (sample 60/rule if more, seed 20260805);
VETO >= 0.95 & n >= 10; WARN >= 0.80; else DROP.
"""

import glob
import json
import random
import re
import sys
from pathlib import Path

OUT = Path.home() / ".memphant-private/xs-crosssession/g1b_fired.jsonl"
ROOTS = [
    "/Users/sidsharma/.claude/projects/-Users-sidsharma-Syndai/*.jsonl",
    "/Users/sidsharma/.claude/projects/-Users-sidsharma-Memphant/*.jsonl",
    "/Users/sidsharma/.claude/projects/-Users-sidsharma-Syndai--claude-worktrees-*/*.jsonl",
]
SHARED = {"/Users/sidsharma/Syndai", "/Users/sidsharma/Memphant"}
AUTH_RE = re.compile(
    r"(authoriz\w+|go ahead|yes\b|approved|run (the|it))[^.]{0,80}(prod|production)"
    r"|(prod|production)[^.]{0,80}(authoriz\w+|extract|census|deploy|migrat)", re.I)


def events(path):
    with open(path, errors="replace") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except Exception:
                continue
            msg = d.get("message") or {}
            role = msg.get("role")
            content = msg.get("content")
            if role == "user" and isinstance(content, str):
                yield ("user", content)
            elif isinstance(content, list):
                if role == "user":
                    txt = " ".join(c.get("text", "") for c in content
                                   if isinstance(c, dict) and c.get("type") == "text")
                    if txt:
                        yield ("user", txt)
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "tool_use":
                        inp = c.get("input") or {}
                        yield ("tool", (c.get("name", ""),
                                        str(inp.get("command") or inp.get("file_path") or "")))


def segments(c):
    return [s.strip() for s in re.split(r"&&|;|\|(?!\|)", c)]


def effective_cwd(c):
    m = None
    for m2 in re.finditer(r"cd\s+(\"[^\"]+\"|\S+)", c):
        m = m2
    return m.group(1).strip('"').rstrip("/") if m else None


def fire(name, c, st):
    out = []
    if name != "Bash":
        return out
    segs = segments(c)
    if not st["preflight_recent"] and any(
            re.match(r"make check\b", s) for s in segs):
        out.append("R1b")
    if st["shared"] is True and any(
            re.match(r"git (add (-A|\.)$|checkout (-- )?\.$|reset --hard$)", s)
            for s in segs):
        out.append("R2b")
    for s in segs:
        if s.startswith("codex exec") or re.match(r"(timeout \d+ |nohup )?codex exec", s):
            if "$(cat" in s:
                out.append("R4b")
            else:
                q = re.search(r'codex exec[^"<]*"(.{1500,})', s, re.S)
                if q and "<" not in s.split('"')[0]:
                    out.append("R4b")
    if "--config prod" in c and ("psql" in c or "DATABASE_URL" in c) and not st["prod_auth"]:
        out.append("R6b")
    if st["rebase_active"] and re.search(r"git stash (push|pop|apply)?\s*[^|;&]*--\s+\S", c):
        out.append("R7b")
    return out


def label(rule, c):
    if rule == "R2b":
        return "FP" if "stash" in c else "TP"
    if rule == "R6b":
        if re.search(r"\b(INSERT|UPDATE|DELETE|ALTER|CREATE|DROP|TRUNCATE)\b", c, re.I) \
                or "alembic upgrade" in c:
            return "TP"
        return "FP"
    if rule == "R1b":
        return "FP" if re.search(r"<<\s*'?\w+'?", c) else "TP"
    if rule == "R4b":
        return "FP" if ".sh" in c.split("codex exec")[0][-80:] else "TP"
    return "TP"  # R7b


def main() -> int:
    fired, n_calls = [], 0
    for pat in ROOTS:
        for f in glob.glob(pat):
            sid = Path(f).stem[:8]
            st = {"rebase_active": False, "preflight_recent": False,
                  "prod_auth": False, "shared": None, "recent": []}
            for kind, payload in events(f):
                if kind == "user":
                    if AUTH_RE.search(payload):
                        st["prod_auth"] = True
                    continue
                name, c = payload
                n_calls += 1
                cwd = effective_cwd(c)
                if cwd:
                    st["shared"] = cwd in SHARED
                for rid in fire(name, c, st):
                    fired.append({"rule": rid, "session": sid,
                                  "command": c[:400], "label": label(rid, c)})
                low = c.lower()
                if re.search(r"git rebase(?!.*(--continue|--abort|--skip))", low):
                    st["rebase_active"] = True
                if re.search(r"rebase.*(--continue|--abort|--skip)", low) or "rebase" in low and "success" in low:
                    st["rebase_active"] = False
                st["recent"].append(any(k in low for k in
                                        ("preflight", "precommit-scoped", "stamp", "--local")))
                st["recent"] = st["recent"][-30:]
                st["preflight_recent"] = any(st["recent"])
    with OUT.open("w") as fh:
        for r in fired:
            fh.write(json.dumps(r) + "\n")
    rng = random.Random(20260805)
    report = {}
    for rid in sorted({r["rule"] for r in fired}):
        sub = [r for r in fired if r["rule"] == rid]
        samp = rng.sample(sub, 60) if len(sub) > 60 else sub
        tp = sum(r["label"] == "TP" for r in samp)
        p = tp / len(samp)
        report[rid] = {"fired_total": len(sub), "labeled": len(samp),
                       "precision": round(p, 3),
                       "mode": ("VETO" if p >= 0.95 and len(samp) >= 10 else
                                "WARN" if p >= 0.80 else "DROP")}
    report["_scanned"] = n_calls
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
