#!/usr/bin/env python3
"""GitHub-lane raw fetcher: pulls every source this bank needs into a disk cache.

Preregistration: ``docs/build-log/2026-07-31-github-lane-bar-and-privacy.md``.
Read that before touching this file.

This is the ONLY component that talks to the network, and it is deliberately
split out from ``github_lane_extract.py`` so the extractor is a pure function of
the cache: once this has run, ``github_lane_extract.py`` makes zero network
calls and a re-cut is free and byte-deterministic (prereg §4.4).

Everything it fetches is a **GET**. No POST/PATCH/PUT/DELETE is issued anywhere
in this file; nothing is pushed, commented, or modified on any repository
(prereg §5.4). Local clones are read with ``git log``/``git show`` only, never
fetched — a fetch would mutate the owner's working repositories and un-pin the
corpus mid-run.

The cache lives at ``~/.memphant-private/github-lane/cache/`` — outside every
git repository, never committed. It is the only place raw private-repo text
lands (prereg §5.2).

Actions log retention is ~90 days, probed directly at prereg time: runs on
2026-05-01 and earlier return empty logs, 2026-05-20 onward return full text.
That is why logs are snapshotted here rather than re-fetched on demand: the bank
must survive GitHub expiring its own logs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

CACHE = Path(os.path.expanduser("~/.memphant-private/github-lane/cache"))
REPOS = ["Syndai", "Finn", "yurivan", "RecMe", "eternex"]
OWNER = "solozerolabs"
CLONES = {name: Path(f"/Users/sidsharma/{name}") for name in REPOS}

# Log-retention floor (prereg §2). Runs older than this have no retrievable log.
RUN_WINDOW_START = "2026-05-01"

# Deploy-family workflows are excluded by name (prereg §4.3). They dominate the
# raw transition count, are frequently infra flakes retried on unchanged
# content, and — the disqualifying property — emit COMMIT SUBJECT LINES into
# their logs, which would smuggle commit-message text onto the query side and
# reproduce the exact defect the prereg bars.
DEPLOY_WORKFLOW_RE = re.compile(
    r"deploy|reconcile|release|publish|drain|rollup|gif|dependency submission|"
    r"pages|cron|schedule|backup|sync",
    re.IGNORECASE,
)

MAX_COMMITS_BETWEEN = 3


def gh(path: str) -> object:
    """One authenticated GET against the GitHub API. Never a write."""
    out = subprocess.run(
        ["gh", "api", "-X", "GET", path],
        capture_output=True,
        text=True,
        check=False,
    )
    if out.returncode != 0:
        return None
    return json.loads(out.stdout)


def cached(rel: str, producer) -> object:
    """Fetch once, then serve from disk forever. Makes a re-run cost zero calls."""
    path = CACHE / rel
    if path.exists() and path.stat().st_size > 0:
        return json.loads(path.read_text())
    value = producer()
    if value is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return value


def cached_text(rel: str, producer) -> str | None:
    path = CACHE / rel
    if path.exists():
        return path.read_text(errors="replace")
    value = producer()
    if value is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)
    return value


def git(repo: str, *args: str) -> str:
    """Read-only git against a local clone. No fetch, no checkout, no ref write."""
    out = subprocess.run(
        ["git", "-C", str(CLONES[repo]), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    return out.stdout


def load_runs(repo: str) -> list[dict]:
    runs: dict[int, dict] = {}
    for page in range(1, 61):
        path = CACHE / "runs" / f"{repo}-{page}.json"
        if not path.exists():
            blob = gh(f"repos/{OWNER}/{repo}/actions/runs?per_page=100&page={page}")
            if not blob:
                break
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(blob))
        blob = json.loads(path.read_text())
        chunk = blob.get("workflow_runs", [])
        for run in chunk:
            runs[run["id"]] = run
        if len(chunk) < 100:
            break
    return sorted(runs.values(), key=lambda r: r["created_at"])


def transitions(repo: str) -> list[dict]:
    """Failure -> success on the same (workflow, branch) at a different head SHA.

    The pair is the evidence: a machine said "broken at A", then the same machine
    said "fixed at B", and neither statement was written by whoever wrote the fix.
    """
    runs = [r for r in load_runs(repo) if r.get("status") == "completed"]
    groups: dict[tuple, list[dict]] = {}
    for run in runs:
        groups.setdefault((run["workflow_id"], run["head_branch"]), []).append(run)
    found = []
    for group in groups.values():
        group.sort(key=lambda r: r["created_at"])
        for before, after in zip(group, group[1:]):
            if before.get("conclusion") != "failure":
                continue
            if after.get("conclusion") != "success":
                continue
            if before["head_sha"] == after["head_sha"]:
                continue
            if before["created_at"][:10] < RUN_WINDOW_START:
                continue
            if DEPLOY_WORKFLOW_RE.search(before.get("name") or ""):
                continue
            found.append(
                {
                    "repo": repo,
                    "workflow": before.get("name"),
                    "branch": before.get("head_branch"),
                    "fail_run_id": before["id"],
                    "fail_sha": before["head_sha"],
                    "fail_created_at": before["created_at"],
                    "green_run_id": after["id"],
                    "green_sha": after["head_sha"],
                    "green_created_at": after["created_at"],
                }
            )
    return found


def commits_between(repo: str, fail_sha: str, green_sha: str) -> list[dict] | None:
    """The candidate fix range, read from the local clone. None if unresolvable."""
    have = git(repo, "cat-file", "-t", fail_sha).strip()
    have_green = git(repo, "cat-file", "-t", green_sha).strip()
    if have != "commit" or have_green != "commit":
        return None
    raw = git(repo, "log", "--format=%H%x1f%an%x1f%aI%x1f%s", f"{fail_sha}..{green_sha}")
    rows = []
    for line in raw.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 4:
            rows.append(
                {"sha": parts[0], "author": parts[1], "date": parts[2], "subject": parts[3]}
            )
    return rows


def fetch_ci(limit: int | None) -> dict:
    """Stage 1: CI failure -> fix. Caches failing-job logs for every viable pair."""
    stats = {"transitions": 0, "unresolvable_range": 0, "range_too_wide": 0,
             "no_failed_job": 0, "log_unavailable": 0, "cached": 0}
    kept = []
    for repo in ["Syndai", "yurivan", "Finn"]:
        for pair in transitions(repo):
            stats["transitions"] += 1
            between = commits_between(repo, pair["fail_sha"], pair["green_sha"])
            if between is None:
                stats["unresolvable_range"] += 1
                continue
            if not between or len(between) > MAX_COMMITS_BETWEEN:
                stats["range_too_wide"] += 1
                continue
            jobs = cached(
                f"jobs/{repo}-{pair['fail_run_id']}.json",
                lambda p=pair, r=repo: gh(f"repos/{OWNER}/{r}/actions/runs/{p['fail_run_id']}/jobs"),
            )
            failed = [j for j in (jobs or {}).get("jobs", []) if j.get("conclusion") == "failure"]
            if not failed:
                stats["no_failed_job"] += 1
                continue
            job = failed[0]
            log = cached_text(
                f"logs/{repo}-{job['id']}.txt",
                lambda j=job, r=repo: (
                    subprocess.run(
                        ["gh", "api", "-X", "GET", f"repos/{OWNER}/{r}/actions/jobs/{j['id']}/logs"],
                        capture_output=True, text=True, check=False,
                    ).stdout
                ),
            )
            if not log or len(log) < 500:
                stats["log_unavailable"] += 1
                continue
            pair["job_id"] = job["id"]
            pair["job_name"] = job.get("name")
            pair["failed_steps"] = [
                s["name"] for s in (job.get("steps") or []) if s.get("conclusion") == "failure"
            ]
            pair["commits"] = between
            kept.append(pair)
            stats["cached"] += 1
            if limit and stats["cached"] >= limit:
                break
        if limit and stats["cached"] >= limit:
            break
    index = CACHE / "ci_candidates.json"
    index.write_text(json.dumps({"stats": stats, "candidates": kept}, indent=2))
    return stats


def fetch_reviews() -> dict:
    """Stage 2: every PR review comment, and every issue, in all five repos."""
    stats = {}
    for repo in REPOS:
        comments: list[dict] = []
        for page in range(1, 20):
            blob = cached(
                f"review_comments/{repo}-{page}.json",
                lambda r=repo, p=page: gh(
                    f"repos/{OWNER}/{r}/pulls/comments?per_page=100&page={p}"
                ),
            )
            if not blob:
                break
            comments.extend(blob)
            if len(blob) < 100:
                break
        issues: list[dict] = []
        for page in range(1, 10):
            blob = cached(
                f"issues/{repo}-{page}.json",
                lambda r=repo, p=page: gh(
                    f"repos/{OWNER}/{r}/issues?state=all&per_page=100&page={p}"
                ),
            )
            if not blob:
                break
            issues.extend([i for i in blob if "pull_request" not in i])
            if len(blob) < 100:
                break
        stats[repo] = {"review_comments": len(comments), "issues": len(issues)}
    (CACHE / "review_index.json").write_text(json.dumps(stats, indent=2))
    return stats


def fetch_prs() -> dict:
    """Stage 3: PR metadata, needed to resolve a review comment to its merge commit."""
    stats = {}
    for repo in REPOS:
        prs: list[dict] = []
        for page in range(1, 10):
            blob = cached(
                f"prs/{repo}-{page}.json",
                lambda r=repo, p=page: gh(
                    f"repos/{OWNER}/{r}/pulls?state=all&per_page=100&page={p}"
                ),
            )
            if not blob:
                break
            prs.extend(blob)
            if len(blob) < 100:
                break
        stats[repo] = len(prs)
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", default="all",
                        choices=["all", "ci", "reviews", "prs"])
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    if args.stage in ("all", "ci"):
        print("ci:", json.dumps(fetch_ci(args.limit), indent=2))
    if args.stage in ("all", "reviews"):
        print("reviews:", json.dumps(fetch_reviews(), indent=2))
    if args.stage in ("all", "prs"):
        print("prs:", json.dumps(fetch_prs(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
