#!/usr/bin/env python3
"""GitHub-lane coding-memory golden bank extractor.

Preregistration: ``docs/build-log/2026-07-31-github-lane-bar-and-privacy.md``.
Every threshold this file enforces is fixed there, before the first run.

The governing constraint is **leak-freedom**. The mined Track R bank failed
because an LLM writing "causally identifying" questions copied identifiers out
of the target: 0.396 question->target token coverage against 0.094 on a
non-target, 4.19x. Commit messages and PR descriptions carry the identical
defect — they are written by the person who just made the change, describing
that change. So no stratum here takes its QUERY from a commit message, a PR
title, or a PR body. Commit text may appear on the TARGET side; the bar is on
the query side only.

Six strata, by who wrote the query:

    S1 ci_failure_fix     a CI runner that had not seen the fix
    S2 revert_supersession  templated by this file from the touched path/symbol
    S3 fix_of_a_fix         templated by this file from the touched path/symbol
    S4 coderabbit_review    a model  -- QUARANTINED, never in a headline
    S5 human_issue_review   a human  -- measured, and it comes out EMPTY (see below)
    P1 public_human_review  a human, CC-BY-4.0 (foundry-ai/swe-prbench)

S5 is retained as a *measured* stratum precisely because it is empty. All 15
"human" review comments in Syndai are the repo owner replying to CodeRabbit, and
11 of them open with "Addressed in <sha>: ..." — the owner describing the change
he just made, which is the exact shape the prereg bars. The other 4 are
rebuttals with no following change. All 16 RecMe issues are open, zero-comment,
never-closed feature-planning tickets by the same owner, in a repo with no CI
history and no fix to point at. Reporting that as a zero with reasons is the
finding; manufacturing it into goldens would be the failure.

Determinism (prereg §4.4): no network call, no model call, no clock read. The
extractor is a pure function of the on-disk cache written by
``github_lane_fetch.py`` plus read-only ``git log``/``git show`` against the
local clones. ``--check`` re-cuts and asserts byte-identity against the
committed lock.

    python3 scripts/github_lane_extract.py --out benchmarks/data/github_lane_golden.jsonl
    python3 scripts/github_lane_extract.py --check
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import github_lane_secrets as secrets  # noqa: E402

CACHE = Path(os.path.expanduser("~/.memphant-private/github-lane/cache"))
MIRROR = Path(os.path.expanduser("~/.memphant-private/github-lane"))
OWNER = "solozerolabs"
REPOS = ["Syndai", "Finn", "yurivan", "RecMe", "eternex"]
CLONES = {name: Path(f"/Users/sidsharma/{name}") for name in REPOS}

DOC_CLIP = 4000  # same event clip the Track R corpus used
QUERY_CLIP = 2000
MAX_PER_SOURCE = 2  # prereg §4.2, private strata
FIX_OF_FIX_WINDOW_DAYS = 30


# --- read-only git -----------------------------------------------------------


def git(repo: str, *args: str) -> str:
    """Read-only git. No fetch, no checkout, no ref write (prereg §5.4)."""
    return subprocess.run(
        ["git", "-C", str(CLONES[repo]), *args],
        capture_output=True,
        text=True,
        check=False,
    ).stdout


def head_sha(repo: str) -> str:
    return git(repo, "rev-parse", "HEAD").strip()


def commit_facts(repo: str, sha: str) -> dict | None:
    raw = git(repo, "show", "-s", "--format=%H%x1f%aI%x1f%s%x1f%b", sha)
    if not raw.strip():
        return None
    parts = raw.split("\x1f")
    if len(parts) < 3:
        return None
    files = [f for f in git(repo, "show", "--name-only", "--format=", sha).splitlines() if f]
    stat = git(repo, "show", "--stat", "--format=", sha).strip()
    return {
        "sha": parts[0].strip(),
        "date": parts[1],
        "subject": parts[2],
        "body": parts[3] if len(parts) > 3 else "",
        "files": files,
        "stat": stat[:DOC_CLIP],
    }


def commit_diff(repo: str, sha: str, paths: list[str] | None = None) -> str:
    args = ["show", "--format=", "--unified=3", sha]
    if paths:
        args += ["--", *paths[:10]]
    return git(repo, *args)


# --- CI failure excerpt ------------------------------------------------------

# The excerpt is the QUERY. It must be machine-emitted text that identifies the
# failure, and it must not contain anything a human wrote about the fix.
SIGNATURES: list[tuple[str, re.Pattern]] = [
    ("pytest_failed", re.compile(r"^(?:FAILED|ERROR) \S+::")),
    ("pytest_assert", re.compile(r"^E   \w")),
    ("tsc", re.compile(r"error TS\d+:")),
    ("mypy", re.compile(r"^\S+\.py:\d+: error:")),
    ("ruff", re.compile(r"^\S+\.py:\d+:\d+: [A-Z]+\d+")),
    ("biome_eslint", re.compile(r"(?:lint/\w+/\w+|✖ \d+ problem)")),
    ("cargo", re.compile(r"^error(?:\[E\d+\])?: \S")),
    ("jest_vitest", re.compile(r"^\s*(?:FAIL|●|×) \S")),
    ("playwright", re.compile(r"^\s*\d+\) \[.*?\] › \S")),
    ("schema_guard", re.compile(r"(?i)(?:schema|contract|drift|migration)\S*\s.{0,60}?(?:mismatch|drift|violation|not in sync|out of sync)")),
    ("runner_error", re.compile(r"^##\[error\](?!Process completed)\S.{10,}")),
]

TIMESTAMP = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.]+Z\s?")

# A commit subject leaking into a log would smuggle human change-description text
# onto the query side. Deploy-family workflows that do this are already excluded
# by the fetcher; this is the belt-and-braces line filter for everything else.
SHA_SUBJECT_LINE = re.compile(r"^\s*[0-9a-f]{7,40}\s+\S+.*")


def failure_excerpt(log_text: str) -> tuple[str, list[str]] | None:
    """Pull the identifying failure lines out of a job log. Query side."""
    lines = [TIMESTAMP.sub("", line).rstrip() for line in log_text.splitlines()]
    picked: list[str] = []
    kinds: list[str] = []
    seen: set[str] = set()
    for line in lines:
        if not line or len(line) > 400:
            continue
        if SHA_SUBJECT_LINE.match(line):
            continue
        for kind, pattern in SIGNATURES:
            if not pattern.search(line):
                continue
            key = line.strip()
            if key in seen:
                break
            seen.add(key)
            picked.append(key)
            kinds.append(kind)
            break
        if len(picked) >= 20:
            break
    if not picked:
        return None
    strong = [k for k in kinds if k != "runner_error"]
    if not strong:
        return None
    text = "\n".join(picked)[:QUERY_CLIP]
    # prereg §4.3: the excerpt must name a concrete file path, test, or symbol.
    if not re.search(r"[\w/\-]+\.(?:py|ts|tsx|js|jsx|rs|go|sql|yml|yaml|json|swift|kt)\b|::\w+|\w+\.\w+\(", text):
        return None
    return text, sorted(set(kinds))


# --- helpers -----------------------------------------------------------------


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def clip(text: str, limit: int = DOC_CLIP) -> str:
    return (text or "")[:limit]


def golden_id(stratum: str, *parts) -> str:
    raw = "|".join([stratum, *[str(p) for p in parts]])
    return f"{stratum}-{hashlib.sha256(raw.encode()).hexdigest()[:12]}"


def symbols_of(paths: list[str]) -> list[str]:
    """Path stems, used to template a query without touching any commit message."""
    out = []
    for path in paths:
        stem = Path(path).stem
        if len(stem) >= 4 and stem not in {"index", "main", "init", "__init__", "mod"}:
            out.append(stem)
    return sorted(set(out))


# --- S1: CI failure -> fix ---------------------------------------------------


def build_s1(reject: Counter) -> tuple[list[dict], list[dict]]:
    index_path = CACHE / "ci_candidates.json"
    if not index_path.exists():
        return [], []
    candidates = json.loads(index_path.read_text())["candidates"]
    goldens, docs = [], []
    for cand in candidates:
        repo = cand["repo"]
        log_path = CACHE / "logs" / f"{repo}-{cand['job_id']}.txt"
        if not log_path.exists():
            reject["log_unavailable"] += 1
            continue
        extracted = failure_excerpt(log_path.read_text(errors="replace"))
        if extracted is None:
            reject["no_identifying_failure_text"] += 1
            continue
        query, kinds = extracted
        fixes = [commit_facts(repo, c["sha"]) for c in cand["commits"]]
        fixes = [f for f in fixes if f and f["files"]]
        if not fixes:
            reject["fix_commit_unresolvable"] += 1
            continue
        fix = fixes[-1]
        paths = fix["files"]
        target_text = "\n".join(
            [
                f"commit {fix['sha']}",
                fix["subject"],
                clip(fix["body"], 800),
                "files: " + ", ".join(paths[:20]),
                fix["stat"],
                clip(commit_diff(repo, fix["sha"], paths), 2500),
            ]
        )
        hit = secrets.scan(query, target_text)
        if hit:
            reject[f"secret_excluded:{hit}"] += 1
            continue
        doc_id = f"{repo}:{fix['sha']}"
        docs.append({"doc_id": doc_id, "scope_key": f"private:{repo}", "scope_key_alt": f"private:{repo}", "text": clip(target_text)})
        goldens.append(
            {
                "golden_id": golden_id("S1", repo, cand["fail_run_id"], fix["sha"]),
                "stratum": "S1",
                "stratum_name": "ci_failure_fix",
                "query_author": "machine",
                "privacy_class": "private_repo",
                "scope_key": f"private:{repo}",
                "scope_key_alt": f"private:{repo}",
                    "query": query,
                "query_kinds": kinds,
                "target_doc_id": doc_id,
                "observable_correct_behavior": (
                    f"Attribute this failure to commit {fix['sha'][:10]} in {repo}, which touched "
                    f"{', '.join(paths[:5])}, and report the check as green from that commit onward."
                ),
                "forbidden_behavior": (
                    f"Report the check as passing at {cand['fail_sha'][:10]}, or propose re-applying the "
                    f"pre-fix state of {', '.join(paths[:3])}."
                ),
                "provenance": {
                    "repo": f"{OWNER}/{repo}",
                    "workflow": cand["workflow"],
                    "branch": cand["branch"],
                    "fail_run_id": cand["fail_run_id"],
                    "fail_sha": cand["fail_sha"],
                    "green_run_id": cand["green_run_id"],
                    "green_sha": cand["green_sha"],
                    "fix_sha": fix["sha"],
                    "commits_in_range": len(cand["commits"]),
                },
                "source_key": f"{repo}:{fix['sha']}",
            }
        )
    return goldens, docs


# --- S2: revert / supersession ----------------------------------------------

REVERTS_LINE = re.compile(r"This reverts commit ([0-9a-f]{40})")


def build_s2(reject: Counter) -> tuple[list[dict], list[dict]]:
    goldens, docs = [], []
    for repo in REPOS:
        raw = git(repo, "log", "--all", "--format=%H", "--grep=This reverts commit")
        for sha in [s for s in raw.split() if s]:
            revert = commit_facts(repo, sha)
            if not revert:
                continue
            match = REVERTS_LINE.search(revert["body"] or "")
            if not match:
                reject["revert_target_not_named"] += 1
                continue
            original = commit_facts(repo, match.group(1))
            if not original:
                reject["reverted_commit_unresolvable"] += 1
                continue
            paths = revert["files"] or original["files"]
            syms = symbols_of(paths)
            if not paths or not syms:
                reject["revert_no_paths"] += 1
                continue
            # Query is templated from the touched path/symbol ONLY. Neither
            # commit's message contributes a token.
            query = (
                f"In {repo}, what is the current state of {', '.join(paths[:4])}"
                f"{' (' + ', '.join(syms[:4]) + ')' if syms else ''}? "
                "An earlier change to these files was later undone; report what is in effect now, "
                "not the version that was withdrawn."
            )
            target_text = "\n".join(
                [
                    f"commit {revert['sha']}",
                    revert["subject"],
                    clip(revert["body"], 800),
                    "files: " + ", ".join(paths[:20]),
                    clip(commit_diff(repo, revert["sha"], paths), 2500),
                ]
            )
            superseded_text = "\n".join(
                [
                    f"commit {original['sha']}",
                    original["subject"],
                    clip(original["body"], 800),
                    clip(commit_diff(repo, original["sha"], original["files"]), 2500),
                ]
            )
            hit = secrets.scan(query, target_text, superseded_text)
            if hit:
                reject[f"secret_excluded:{hit}"] += 1
                continue
            doc_id = f"{repo}:{revert['sha']}"
            docs.append({"doc_id": doc_id, "scope_key": f"private:{repo}", "scope_key_alt": f"private:{repo}", "text": clip(target_text)})
            docs.append(
                {
                    "doc_id": f"{repo}:{original['sha']}",
                    "scope_key": f"private:{repo}",
                    "scope_key_alt": f"private:{repo}",
                    "text": clip(superseded_text),
                }
            )
            goldens.append(
                {
                    "golden_id": golden_id("S2", repo, revert["sha"]),
                    "stratum": "S2",
                    "stratum_name": "revert_supersession",
                    "query_author": "template",
                    "privacy_class": "private_repo",
                    "scope_key": f"private:{repo}",
                    "scope_key_alt": f"private:{repo}",
                    "query": query,
                    "target_doc_id": doc_id,
                    "superseded_doc_id": f"{repo}:{original['sha']}",
                    "observable_correct_behavior": (
                        f"State the post-revert behavior of {', '.join(paths[:4])} as established by "
                        f"{revert['sha'][:10]}, and say that the earlier change was withdrawn."
                    ),
                    "forbidden_behavior": (
                        f"Present commit {original['sha'][:10]}'s change to {', '.join(paths[:3])} as the "
                        "current state, or answer as though it is still in effect."
                    ),
                    "provenance": {
                        "repo": f"{OWNER}/{repo}",
                        "revert_sha": revert["sha"],
                        "reverted_sha": original["sha"],
                    },
                    "source_key": f"{repo}:{revert['sha']}",
                }
            )
    return goldens, docs


# --- S3: fix-of-a-fix --------------------------------------------------------


def build_s3(s1_goldens: list[dict], reject: Counter) -> tuple[list[dict], list[dict]]:
    """A fix that repairs a path an EARLIER CI-attested fix had just touched.

    Both ends are attested by the repository's own CI, not by us: CI said broken,
    a commit fixed it, then CI said broken again on an overlapping path and a
    later commit fixed that. The correction is externally witnessed, which is
    the property the prereg requires of this shape.
    """
    goldens, docs = [], []
    by_repo: dict[str, list[dict]] = defaultdict(list)
    for golden in s1_goldens:
        by_repo[golden["provenance"]["repo"]].append(golden)
    for repo_full, group in by_repo.items():
        repo = repo_full.split("/")[-1]
        enriched = []
        for golden in group:
            facts = commit_facts(repo, golden["provenance"]["fix_sha"])
            if facts:
                enriched.append((facts, golden))
        enriched.sort(key=lambda pair: pair[0]["date"])
        for i, (later, later_golden) in enumerate(enriched):
            best = None
            for earlier, earlier_golden in enriched[:i]:
                overlap = sorted(set(later["files"]) & set(earlier["files"]))
                if not overlap:
                    continue
                delta = _days_between(earlier["date"], later["date"])
                if delta is None or delta > FIX_OF_FIX_WINDOW_DAYS:
                    continue
                if best is None or delta < best[2]:
                    best = (earlier, overlap, delta)
            if best is None:
                continue
            earlier, overlap, delta = best
            syms = symbols_of(overlap)
            query = (
                f"In {repo}, {', '.join(overlap[:4])}"
                f"{' (' + ', '.join(syms[:4]) + ')' if syms else ''} was changed to fix a failing check, "
                "and then changed again shortly afterwards because the first attempt was not right. "
                "What is the correct handling now?"
            )
            target_text = "\n".join(
                [
                    f"commit {later['sha']}",
                    later["subject"],
                    clip(later["body"], 800),
                    "files: " + ", ".join(later["files"][:20]),
                    clip(commit_diff(repo, later["sha"], overlap), 2500),
                ]
            )
            superseded_text = "\n".join(
                [
                    f"commit {earlier['sha']}",
                    earlier["subject"],
                    clip(earlier["body"], 800),
                    clip(commit_diff(repo, earlier["sha"], overlap), 2500),
                ]
            )
            hit = secrets.scan(query, target_text, superseded_text)
            if hit:
                reject[f"secret_excluded:{hit}"] += 1
                continue
            doc_id = f"{repo}:{later['sha']}"
            docs.append({"doc_id": doc_id, "scope_key": f"private:{repo}", "scope_key_alt": f"private:{repo}", "text": clip(target_text)})
            docs.append(
                {
                    "doc_id": f"{repo}:{earlier['sha']}",
                    "scope_key": f"private:{repo}",
                    "scope_key_alt": f"private:{repo}",
                    "text": clip(superseded_text),
                }
            )
            goldens.append(
                {
                    "golden_id": golden_id("S3", repo, earlier["sha"], later["sha"]),
                    "stratum": "S3",
                    "stratum_name": "fix_of_a_fix",
                    "query_author": "template",
                    "privacy_class": "private_repo",
                    "scope_key": f"private:{repo}",
                    "scope_key_alt": f"private:{repo}",
                    "query": query,
                    "target_doc_id": doc_id,
                    "superseded_doc_id": f"{repo}:{earlier['sha']}",
                    "observable_correct_behavior": (
                        f"Describe the handling established by {later['sha'][:10]} for "
                        f"{', '.join(overlap[:4])}, and note it corrects the earlier attempt."
                    ),
                    "forbidden_behavior": (
                        f"Recommend the superseded approach from {earlier['sha'][:10]}, or present it as "
                        "the current state of these files."
                    ),
                    "provenance": {
                        "repo": repo_full,
                        "first_fix_sha": earlier["sha"],
                        "second_fix_sha": later["sha"],
                        "overlap_paths": overlap[:10],
                        "days_between": delta,
                        "first_fix_attested_by_run": earlier_golden["provenance"]["fail_run_id"],
                        "second_fix_attested_by_run": later_golden["provenance"]["fail_run_id"],
                    },
                    "source_key": f"{repo}:{later['sha']}:fixfix",
                }
            )
    return goldens, docs


def _days_between(a: str, b: str) -> float | None:
    import datetime as dt

    try:
        ta = dt.datetime.fromisoformat(a)
        tb = dt.datetime.fromisoformat(b)
    except ValueError:
        return None
    return abs((tb - ta).total_seconds()) / 86400.0


# --- S4: CodeRabbit review -> change (QUARANTINED) ---------------------------


def _load_cached(prefix: str, repo: str) -> list[dict]:
    rows: list[dict] = []
    directory = CACHE / prefix
    if not directory.exists():
        return rows
    for path in sorted(directory.glob(f"{repo}-*.json")):
        blob = json.loads(path.read_text())
        if isinstance(blob, list):
            rows.extend(blob)
    return rows


def build_review_strata(reject: Counter) -> tuple[list[dict], list[dict], dict]:
    """S4 (model-authored) and S5 (human) from the private repos' review surface."""
    goldens, docs = [], []
    s5_audit = {
        "human_review_comments": 0,
        "human_comments_self_describing_the_change": 0,
        "human_comments_rebuttal_no_change": 0,
        "issues_total": 0,
        "issues_open_never_closed_no_comments": 0,
        "s5_admitted": 0,
    }
    self_desc = re.compile(
        r"^\s*(?:addressed|fixed|done|resolved|implemented|applied)\b|"
        r"\b(?:addressed|fixed) in [0-9a-f]{7,}",
        re.IGNORECASE,
    )
    rebuttal = re.compile(
        r"^\s*(?:not applicable|false positive|intentional|by design|this guard is not|won'?t fix)",
        re.IGNORECASE,
    )
    for repo in REPOS:
        prs = {p["number"]: p for p in _load_cached("prs", repo)}
        for comment in _load_cached("review_comments", repo):
            login = comment.get("user", {}).get("login", "")
            body = (comment.get("body") or "").strip()
            path = comment.get("path")
            pr_number = int(comment["pull_request_url"].rstrip("/").split("/")[-1])
            pull = prs.get(pr_number)
            is_bot = "coderabbit" in login.lower() or login.endswith("[bot]")
            if not is_bot:
                s5_audit["human_review_comments"] += 1
                if self_desc.search(body[:200]):
                    # The prereg §1 bar: the query would be written by the actor
                    # who made the change, describing that change. Track R's
                    # defect exactly.
                    s5_audit["human_comments_self_describing_the_change"] += 1
                    reject["s5_query_describes_own_change"] += 1
                    continue
                if rebuttal.search(body[:120]):
                    s5_audit["human_comments_rebuttal_no_change"] += 1
                    reject["s5_rebuttal_no_following_change"] += 1
                    continue
                reject["s5_no_attributable_following_change"] += 1
                continue
            if not pull or not pull.get("merge_commit_sha") or not path:
                reject["s4_no_merge_commit_or_path"] += 1
                continue
            if len(body) < 80:
                reject["s4_comment_too_short"] += 1
                continue
            merge_sha = pull["merge_commit_sha"]
            facts = commit_facts(repo, merge_sha)
            if not facts:
                reject["s4_merge_commit_unresolvable"] += 1
                continue
            diff = commit_diff(repo, merge_sha, [path])
            if not diff.strip():
                reject["s4_reviewed_path_not_in_merge"] += 1
                continue
            target_text = "\n".join(
                [f"commit {merge_sha}", facts["subject"], f"path: {path}", clip(diff, 3000)]
            )
            hit = secrets.scan(body, target_text)
            if hit:
                reject[f"secret_excluded:{hit}"] += 1
                continue
            doc_id = f"{repo}:{merge_sha}:{path}"
            docs.append({"doc_id": doc_id, "scope_key": f"private:{repo}", "scope_key_alt": f"private:{repo}", "text": clip(target_text)})
            goldens.append(
                {
                    "golden_id": golden_id("S4", repo, comment["id"]),
                    "stratum": "S4",
                    "stratum_name": "coderabbit_review",
                    "query_author": "model",
                    "privacy_class": "private_repo",
                    "scope_key": f"private:{repo}",
                    "quarantined": True,
                    "query": clip(body, QUERY_CLIP),
                    "target_doc_id": doc_id,
                    "observable_correct_behavior": (
                        f"Produce the change the review asked for at {path}, as merged in "
                        f"{merge_sha[:10]}."
                    ),
                    "forbidden_behavior": (
                        f"Leave {path} unchanged, or re-propose the reviewed code as it stood before "
                        f"PR #{pr_number} merged."
                    ),
                    "provenance": {
                        "repo": f"{OWNER}/{repo}",
                        "pr_number": pr_number,
                        "review_comment_id": comment["id"],
                        "reviewer": login,
                        "path": path,
                        "merge_commit_sha": merge_sha,
                    },
                    "source_key": f"{repo}:pr{pr_number}",
                }
            )
        issues = [i for i in _load_cached("issues", repo) if "pull_request" not in i]
        s5_audit["issues_total"] += len(issues)
        for issue in issues:
            if issue["state"] == "open" and issue["comments"] == 0:
                s5_audit["issues_open_never_closed_no_comments"] += 1
                reject["s5_issue_open_backlog_no_fix"] += 1
            else:
                reject["s5_issue_no_attributable_fix"] += 1
    return goldens, docs, s5_audit


# --- P1: public human review (CC-BY-4.0) ------------------------------------

BOT_AUTHOR = re.compile(
    r"(?:bot|gemini-code-assist|coderabbit|copilot|sourcery|codium|qodo|greptile|cursor|devin|sweep)",
    re.IGNORECASE,
)
DIFF_FILE_HEADER = re.compile(r"^diff --git a/(\S+) b/(\S+)$", re.MULTILINE)


def _file_section(patch: str, path: str) -> str | None:
    """The unified-diff section for one path — the change the reviewer's comment preceded."""
    matches = list(DIFF_FILE_HEADER.finditer(patch))
    for i, match in enumerate(matches):
        if match.group(2) != path and match.group(1) != path:
            continue
        end = matches[i + 1].start() if i + 1 < len(matches) else len(patch)
        return patch[match.start() : end]
    return None


def build_p1(reject: Counter, p1_audit: dict) -> tuple[list[dict], list[dict]]:
    source = CACHE / "public" / "prs.jsonl"
    if not source.exists():
        return [], []
    goldens, docs = [], []
    rows = [json.loads(line) for line in source.read_text().splitlines() if line.strip()]
    p1_audit["source_prs"] = len(rows)
    for row in rows:
        comments = row.get("human_review_comments") or []
        p1_audit["source_comments"] += len(comments)
        # prereg §2b: filter BY AUTHOR. The card's `ai_comments_removed` field is
        # 0 on rows that plainly contain bot comments, so it is not trusted.
        human = []
        for comment in comments:
            if BOT_AUTHOR.search(comment.get("author") or ""):
                p1_audit["bot_comments_excluded"] += 1
                p1_audit["bot_prs"].add(row["task_id"])
                continue
            human.append(comment)
        p1_audit["human_comments"] += len(human)
        patch = row.get("diff_patch") or ""
        # One golden per source PR (prereg §4.2). Deterministic pick: the longest
        # human comment whose reviewed path is actually present in the patch.
        best = None
        for comment in human:
            body = (comment.get("body") or "").strip()
            path = comment.get("path")
            if len(body) < 80 or not path:
                continue
            section = _file_section(patch, path)
            if not section:
                continue
            key = (len(body), comment.get("line") or 0, path)
            if best is None or key > best[0]:
                best = (key, comment, body, path, section)
        if best is None:
            reject["p1_no_usable_human_comment"] += 1
            continue
        _, comment, body, path, section = best
        target_text = "\n".join(
            [
                f"{row['repo']} PR #{row['pr_number']}",
                f"path: {path}",
                clip(section, 3500),
            ]
        )
        doc_id = f"prbench:{row['task_id']}:{path}"
        # prereg §4.1 fixes the non-target scope as "other target documents of the
        # SAME REPOSITORY". Language scoping was tried and is published as the
        # secondary figure, but the repository is the preregistered unit and it is
        # the one the gate is computed on.
        scope = f"public:{row['repo']}"
        scope_alt = f"public-lang:{row.get('language') or 'unknown'}"
        docs.append({"doc_id": doc_id, "scope_key": scope, "scope_key_alt": scope_alt,
                     "text": clip(target_text)})
        goldens.append(
            {
                "golden_id": golden_id("P1", row["task_id"], path),
                "stratum": "P1",
                "stratum_name": "public_human_review",
                "query_author": "human",
                "privacy_class": "public_cc_by_4_0",
                "scope_key": scope,
                "scope_key_alt": scope_alt,
                "query": clip(body, QUERY_CLIP),
                "target_doc_id": doc_id,
                "observable_correct_behavior": (
                    f"Produce the change this reviewer asked for at {path}, as merged in "
                    f"{row['repo']} PR #{row['pr_number']}."
                ),
                "forbidden_behavior": (
                    f"Leave {path} at its pre-review state, or answer from a different file in the "
                    "same pull request."
                ),
                "provenance": {
                    "dataset": "foundry-ai/swe-prbench",
                    "license": "CC-BY-4.0",
                    "attribution": "SWE-PRBench (foundry-ai), CC BY 4.0",
                    "task_id": row["task_id"],
                    "repo": row["repo"],
                    "pr_number": row["pr_number"],
                    "pr_url": row["pr_url"],
                    "base_commit": row["base_commit"],
                    "head_commit": row["head_commit"],
                    "merged_at": row["merged_at"],
                    "language": row.get("language"),
                    "reviewer": comment.get("author"),
                    "path": path,
                    "line": comment.get("line"),
                },
                "source_key": f"prbench:{row['task_id']}",
            }
        )
    return goldens, docs


# --- assembly ----------------------------------------------------------------


def cap_per_source(goldens: list[dict], reject: Counter) -> list[dict]:
    seen: Counter = Counter()
    kept = []
    for golden in sorted(goldens, key=lambda g: g["golden_id"]):
        cap = 1 if golden["stratum"] == "P1" else MAX_PER_SOURCE
        if seen[golden["source_key"]] >= cap:
            reject["over_per_source_cap"] += 1
            continue
        seen[golden["source_key"]] += 1
        kept.append(golden)
    return kept


def build() -> tuple[list[dict], list[dict], dict]:
    reject: Counter = Counter()
    p1_audit = {
        "source_prs": 0,
        "source_comments": 0,
        "human_comments": 0,
        "bot_comments_excluded": 0,
        "bot_prs": set(),
    }
    s1, d1 = build_s1(reject)
    s2, d2 = build_s2(reject)
    s3, d3 = build_s3(s1, reject)
    s45, d45, s5_audit = build_review_strata(reject)
    p1, dp1 = build_p1(reject, p1_audit)
    goldens = cap_per_source(s1 + s2 + s3 + s45 + p1, reject)
    docs = {d["doc_id"]: d for d in (d1 + d2 + d3 + d45 + dp1)}
    goldens.sort(key=lambda g: (g["stratum"], g["golden_id"]))
    corpus = sorted(docs.values(), key=lambda d: d["doc_id"])
    p1_audit["bot_prs"] = len(p1_audit["bot_prs"])
    audit = {"reject": dict(sorted(reject.items())), "s5": s5_audit, "p1": p1_audit}
    return goldens, corpus, audit


def build_lock(goldens: list[dict], corpus: list[dict], audit: dict,
               golden_path: Path, corpus_path: Path) -> dict:
    by_stratum = Counter(g["stratum"] for g in goldens)
    private = [g for g in goldens if g["privacy_class"] == "private_repo"]
    private_non_s4 = [g for g in private if g["stratum"] != "S4"]
    by_repo = Counter(
        g["provenance"].get("repo", "") for g in private_non_s4
    )
    secret_counts = {
        key.split(":", 1)[1]: value
        for key, value in audit["reject"].items()
        if key.startswith("secret_excluded:")
    }
    p1 = [g for g in goldens if g["stratum"] == "P1"]
    distinct_private_sources = len({g["source_key"] for g in private_non_s4})
    top_repo_share = (
        max(by_repo.values()) / len(private_non_s4) if private_non_s4 else 0.0
    )
    # The composition bars of prereg §4.2, evaluated in the committed artifact so
    # a failure is recorded where the numbers live, not only in prose.
    composition_bars = {
        "private_non_s4_ge_40": {
            "bar": 40, "observed": len(private_non_s4),
            "verdict": "PASS" if len(private_non_s4) >= 40 else "FAIL",
        },
        "p1_ge_100": {
            "bar": 100, "observed": len(p1),
            "verdict": "PASS" if len(p1) >= 100 else "FAIL",
        },
        "distinct_private_source_commits_ge_30": {
            "bar": 30, "observed": distinct_private_sources,
            "verdict": "PASS" if distinct_private_sources >= 30 else "FAIL",
        },
        "max_repo_share_of_private_le_0_60": {
            "bar": 0.60, "observed": round(top_repo_share, 4),
            "verdict": "PASS" if top_repo_share <= 0.60 else "FAIL",
        },
    }
    return {
        "schema": "memphant.eval.github-lane-bank.v1",
        "preregistration": "docs/build-log/2026-07-31-github-lane-bar-and-privacy.md",
        "golden_path": golden_path.as_posix(),
        "golden_sha256": sha256_hex(golden_path.read_bytes()),
        "golden_bytes": golden_path.stat().st_size,
        "corpus_path": corpus_path.as_posix(),
        "corpus_sha256": sha256_hex(corpus_path.read_bytes()),
        "corpus_bytes": corpus_path.stat().st_size,
        "composition_bars": composition_bars,
        "composition_bars_all_pass": all(
            v["verdict"] == "PASS" for v in composition_bars.values()
        ),
        "leakage_gate_artifact": "docs/build-log/artifacts/github-lane/leakage.json",
        "n_goldens": len(goldens),
        "n_corpus_docs": len(corpus),
        "by_stratum": dict(sorted(by_stratum.items())),
        "by_stratum_name": dict(
            sorted(Counter(g["stratum_name"] for g in goldens).items())
        ),
        "by_query_author": dict(sorted(Counter(g["query_author"] for g in goldens).items())),
        "by_privacy_class": dict(sorted(Counter(g["privacy_class"] for g in goldens).items())),
        "private_non_s4_total": len(private_non_s4),
        "private_by_repo": dict(sorted(by_repo.items())),
        "distinct_source_commits_private": len(
            {g["source_key"] for g in private_non_s4}
        ),
        "source_head_shas": {
            f"{OWNER}/{name}": head_sha(name) for name in REPOS
        },
        "public_sources": [
            {
                "dataset": "foundry-ai/swe-prbench",
                "file": "dataset/prs.jsonl",
                "license": "CC-BY-4.0",
                "attribution": "SWE-PRBench (foundry-ai), CC BY 4.0",
                "sha256": sha256_hex((CACHE / "public" / "prs.jsonl").read_bytes())
                if (CACHE / "public" / "prs.jsonl").exists()
                else None,
            }
        ],
        "secret_excluded": secret_counts,
        "secret_excluded_total": sum(secret_counts.values()),
        "reject_by_reason": audit["reject"],
        "s5_audit": audit["s5"],
        "p1_audit": audit["p1"],
        "parameters": {
            "doc_clip": DOC_CLIP,
            "query_clip": QUERY_CLIP,
            "max_per_source_private": MAX_PER_SOURCE,
            "max_per_source_p1": 1,
            "fix_of_fix_window_days": FIX_OF_FIX_WINDOW_DAYS,
        },
    }


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path,
                        default=Path("benchmarks/data/github_lane_golden.jsonl"))
    parser.add_argument("--corpus", type=Path,
                        default=Path("benchmarks/data/github_lane_corpus.jsonl"))
    parser.add_argument("--lock", type=Path,
                        default=Path("benchmarks/data/github_lane_golden.lock.json"))
    parser.add_argument("--check", action="store_true",
                        help="re-cut and assert byte-identity against the committed lock")
    args = parser.parse_args()

    goldens, corpus, audit = build()
    if args.check:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            gold = Path(tmp) / "g.jsonl"
            corp = Path(tmp) / "c.jsonl"
            write_jsonl(gold, goldens)
            write_jsonl(corp, corpus)
            fresh = build_lock(goldens, corpus, audit, args.out, args.corpus)
            committed = json.loads(args.lock.read_text())
            fresh["golden_sha256"] = sha256_hex(gold.read_bytes())
            fresh["golden_bytes"] = gold.stat().st_size
            fresh["corpus_sha256"] = sha256_hex(corp.read_bytes())
            fresh["corpus_bytes"] = corp.stat().st_size
            diffs = [
                key
                for key in sorted(set(fresh) | set(committed))
                if fresh.get(key) != committed.get(key)
            ]
            if diffs:
                print("CHECK FAILED, differing keys:", diffs)
                for key in diffs:
                    print(f"  {key}\n    fresh    = {json.dumps(fresh.get(key))[:400]}"
                          f"\n    committed= {json.dumps(committed.get(key))[:400]}")
                return 1
        print("CHECK OK — bank reproduces byte-identically against the committed lock.")
        return 0

    write_jsonl(args.out, goldens)
    write_jsonl(args.corpus, corpus)
    lock = build_lock(goldens, corpus, audit, args.out, args.corpus)
    args.lock.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")
    MIRROR.mkdir(parents=True, exist_ok=True)
    for path in (args.out, args.corpus, args.lock):
        (MIRROR / path.name).write_bytes(path.read_bytes())
    printable = {k: v for k, v in lock.items() if k != "reject_by_reason"}
    print(json.dumps(printable, indent=2, sort_keys=True))
    print("\nreject_by_reason:", json.dumps(lock["reject_by_reason"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
