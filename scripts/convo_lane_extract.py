#!/usr/bin/env python3
"""Convo-lane golden bank: coding-agent memory goldens anchored on REAL human turns.

Preregistered bar and privacy terms:
``docs/build-log/2026-07-31-convo-lane-bar-and-privacy.md`` — read that first.
Nothing here may be relaxed without amending that document.

Why this exists: the mined Track R bank leaks (question->target token coverage
0.3960 vs a 0.1008 non-target floor, 3.93x) because an LLM asked to write
"causally identifying" questions satisfied the instruction by copying rare
identifiers out of the target. A human's turn in a real session cannot leak that
way: the user typed it BEFORE the answer existed, in their own words, without
having seen the target. This extractor takes those turns verbatim and never
edits them -- ``question`` is byte-identical to the source residual, which is a
hard bar (§4.2) and is re-asserted mechanically in ``--build``.

Shape of the machinery follows ``scripts/user_lane_extract.py`` (Track U):
mechanism committed, bodies gitignored, one committed lock of counts and hashes,
extraction pinned to a frozen source snapshot, ``--check`` re-derives and asserts
the lock. Leakage is measured by ``scripts/track_r_leakage.py`` unmodified; this
file only has to emit a corpus in that script's shape.

Stages::

    python3 scripts/convo_lane_extract.py --snapshot   # freeze + hash sources
    python3 scripts/convo_lane_extract.py --extract    # units, corpus, packets
    python3 scripts/convo_lane_extract.py --build      # bank + lock
    python3 scripts/convo_lane_extract.py --check      # re-derive, assert lock

Determinism: no network call, no provider call, no clock read on the derive
path. Same snapshot + same verdict file + same seed -> byte-identical bank.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate_common as gc  # noqa: E402

# --- paths -------------------------------------------------------------------

LIVE_CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
PRIVATE_ROOT = Path.home() / ".memphant-private" / "convo-lane"
SNAPSHOT_ROOT = PRIVATE_ROOT / "sources"
SNAPSHOT_MANIFEST = PRIVATE_ROOT / "sources.manifest.json"

DATA_DIR = gc.MEMPHANT_ROOT / "benchmarks" / "data"
CORPUS_PATH = DATA_DIR / "convo_lane_corpus.jsonl"
GOLDEN_PATH = DATA_DIR / "convo_lane_golden.jsonl"
SPOTCHECK_PATH = DATA_DIR / "convo_lane_spotcheck.jsonl"
LOCK_PATH = DATA_DIR / "convo_lane_golden.lock.json"

PACKETS_PATH = PRIVATE_ROOT / "adjudication_packets.jsonl"
VERDICTS_PATH = PRIVATE_ROOT / "adjudication_verdicts.jsonl"
CANDIDATES_PATH = PRIVATE_ROOT / "candidates.jsonl"

TRACK_U_PROBES = Path.home() / ".memphant-private" / "track-u" / "user_lane_probes.jsonl"
TRACK_U_GOLDEN = Path.home() / ".memphant-private" / "track-u" / "user_lane_golden.jsonl"

SEED = 7

# --- preregistered parameters (§3, §4) ---------------------------------------

MIN_RESIDUAL_CHARS = 40  # §3.5
PASTE_RUN_LINES = 20  # §3.6
UNIT_CLIP_CHARS = 1500
AGENT_CLIP_CHARS = 900
HAYSTACK_MAX_UNITS = 200  # prior units of the same project, per golden
MAX_QUESTION_CHARS = 2000  # §3.7 (amendment A1): a memory query, not a pasted spec
BOILERPLATE_JACCARD = 0.80  # §3.7 (A1): the same turn re-pasted across sessions
TARGET_NEAR_DUP_JACCARD = 0.45  # §3.7 (A1): the target must not restate the question
TARGET_MIN_JACCARD = 0.06  # a target has to be related to the query at all
CANDIDATES_PER_SHAPE = 60
BANK_MIN, BANK_MAX = 40, 80  # §4.3
BANK_INSUFFICIENT_BELOW = 24  # §4.3 honest-failure clause
PER_SHAPE_MIN = 6
PER_SESSION_MAX = 3
PER_PROJECT_MAX = 25
SPOTCHECK_N = 15
TRACK_U_JACCARD_MAX = 0.60  # §4.4
SKELETON_RATIO_MIN = 0.90  # §4.5
SKELETON_SHARE_MAX = 3

SHAPES = (
    "task_resumption",
    "correction_retention",
    "state_churn",
    "file_symbol_grounding",
)

# --- harness wrappers stripped from an otherwise-human turn (§3.5) -----------

WRAPPER_RES = (
    re.compile(r"<system-reminder>.*?</system-reminder>", re.S | re.I),
    re.compile(r"<task-notification>.*?</task-notification>", re.S | re.I),
    re.compile(r"<command-name>.*?</command-name>", re.S | re.I),
    re.compile(r"<command-message>.*?</command-message>", re.S | re.I),
    re.compile(r"<command-args>.*?</command-args>", re.S | re.I),
    re.compile(r"<local-command-stdout>.*?</local-command-stdout>", re.S | re.I),
    re.compile(r"<local-command-stderr>.*?</local-command-stderr>", re.S | re.I),
    re.compile(r"\[Pasted text #\d+[^\]]*\]"),
    re.compile(r"\[Image #\d+\]"),
    re.compile(r"\[Request interrupted by user[^\]]*\]"),
)

# --- secret families (§5). A detection excludes the whole candidate. ---------

SECRET_RES = (
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("aws_secret", re.compile(r"aws_secret_access_key\s*[=:]\s*\S{30,}", re.I)),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[0-9A-Za-z]{30,}\b")),
    ("github_pat", re.compile(r"\bgithub_pat_[0-9A-Za-z_]{50,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[0-9A-Za-z\-]{10,}\b")),
    ("stripe_key", re.compile(r"\b(?:sk|rk|pk)_live_[0-9A-Za-z]{20,}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[0-9A-Za-z_\-]{20,}\b")),
    ("openrouter_key", re.compile(r"\bsk-or-[0-9A-Za-z_\-]{20,}\b")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[0-9A-Za-z]{32,}\b")),
    ("doppler_token", re.compile(r"\bdp\.(?:pt|st|ct|sa)\.[0-9A-Za-z_\-]{20,}\b")),
    ("jwt", re.compile(r"\beyJ[0-9A-Za-z_\-]{10,}\.eyJ[0-9A-Za-z_\-]{10,}\.[0-9A-Za-z_\-]{10,}\b")),
    ("private_key_pem", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("uri_credentials", re.compile(r"\b[a-z][a-z0-9+.\-]*://[^\s/:@]+:[^\s/@]{6,}@")),
    ("bearer_header", re.compile(r"\b[Aa]uthorization\s*:\s*Bearer\s+[0-9A-Za-z._\-]{20,}")),
    ("env_assignment", re.compile(
        r"\b(?:[A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|APIKEY|API_KEY|PRIVATE_KEY|"
        r"ACCESS_KEY|CLIENT_SECRET)[A-Z0-9_]*)\s*=\s*['\"]?[0-9A-Za-z/+_\-]{16,}"
    )),
)
HIGH_ENTROPY_RE = re.compile(r"\b[0-9A-Za-z+/]{40,}={0,2}\b")


def _shannon(text: str) -> float:
    counts = Counter(text)
    total = len(text)
    return -sum((n / total) * math.log2(n / total) for n in counts.values())


def secret_reasons(text: str) -> list[str]:
    """Every secret family detected in ``text``. Returns family names, never content."""
    found = []
    for family, pattern in SECRET_RES:
        if pattern.search(text):
            found.append(family)
    for match in HIGH_ENTROPY_RE.finditer(text):
        blob = match.group(0)
        # git sha / hex digests are not secrets and are everywhere in this corpus
        if re.fullmatch(r"[0-9a-f]+", blob):
            continue
        if _shannon(blob) >= 4.6:
            found.append("high_entropy_blob")
            break
    return sorted(set(found))


# --- shape cues (§4.2). Selection only; the shape is confirmed by adjudication.

BACKREF_RE = re.compile(
    r"\b(yesterday|earlier|last (?:time|session|night|week|run)|previously|before|"
    r"we (?:fixed|did|discussed|landed|shipped|decided|agreed|built|found)|"
    r"you (?:fixed|said|found|wrote|added)|the one (?:we|you)|that (?:bug|issue|fix|"
    r"change|thing|problem|test|failure)|pick (?:this|it) (?:back )?up|resume|revisit|"
    r"follow up|come back to|as (?:i|we) (?:said|discussed)|remember)\b",
    re.I,
)
CORRECTION_RE = re.compile(
    r"(^|\s)(no[,.\s]|nope\b|don'?t\b|do not\b|stop\b|never\b|wrong\b|incorrect\b|"
    r"that'?s not\b|you (?:broke|missed|forgot|ignored|misread)\b|i said\b|"
    r"revert\b|undo\b|why did you\b|instead of\b|not what i\b|you were supposed)",
    re.I,
)
SUPERSEDE_RE = re.compile(
    r"\b(instead|switch to|change to|now use|actually|scrap|drop (?:that|the)|"
    r"replace|no longer|superseded?|we moved to|forget (?:that|the)|"
    r"changed my mind|new plan|revised?|update (?:that|the) decision)\b",
    re.I,
)
PATH_RE = re.compile(r"[\w./-]*\.(?:py|pyx|pyi|rs|js|jsx|ts|tsx|toml|cfg|ini|sql|sh|md|yaml|yml|json)\b")
DOTTED_RE = re.compile(r"\b[a-z0-9_]+(?:\.[a-z0-9_]+)+\b")
SNAKE_RE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
CAMEL_RE = re.compile(r"\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+\b")
TOKEN_RE = re.compile(r"[a-z0-9_]{3,}")


def tokens(text: str) -> set[str]:
    return set(TOKEN_RE.findall(text.lower()))


def artifacts(text: str) -> set[str]:
    """Concrete artifacts a grounding question can name: paths and identifiers."""
    out = set()
    out |= {m for m in PATH_RE.findall(text) if len(m) > 6}
    out |= {m for m in DOTTED_RE.findall(text) if len(m) > 8 and "." in m}
    out |= {m for m in SNAKE_RE.findall(text) if len(m) > 8}
    out |= {m.lower() for m in CAMEL_RE.findall(text) if len(m) > 8}
    return out


SKELETON_ERASE = (
    re.compile(r"'[^']*'|\"[^\"]*\""),
    re.compile(r"[\w./-]*/[\w./-]+"),
    re.compile(r"\b\w+\.\w[\w.]*\b"),
    re.compile(r"\b\w*_\w[\w_]*\b"),
    re.compile(r"\b[A-Za-z]+[A-Z]\w*\b"),
    re.compile(r"\d+"),
)


def skeleton(question: str) -> str:
    """Identical to ``track_r_mine.skeleton`` (§4.5 compares against that bar)."""
    text = question
    for pattern in SKELETON_ERASE:
        text = pattern.sub(" ", text)
    return " ".join(re.sub(r"[^a-z ]+", " ", text.lower()).split()[:12])


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# --- stage 1: snapshot -------------------------------------------------------

HUMAN_MARKER = '"kind":"human"'


def _is_human_record(record: dict) -> bool:
    """The §3 rule, steps 1-4. Provenance, not shape."""
    return (
        record.get("type") == "user"
        and (record.get("origin") or {}).get("kind") == "human"
        and not record.get("isSidechain")
        and "toolUseResult" not in record
        and not record.get("isMeta")
    )


def _record_text(record: dict) -> str:
    content = (record.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def strip_wrappers(text: str) -> str:
    """§3.5 — remove harness wrappers prepended to an otherwise-human turn."""
    for pattern in WRAPPER_RES:
        text = pattern.sub(" ", text)
    return "\n".join(line.rstrip() for line in text.strip().splitlines()).strip()


def has_paste_run(text: str) -> bool:
    """§3.6 — a pasted article or log is human-submitted but not human-authored."""
    run = 0
    for line in text.splitlines():
        if line.strip():
            run += 1
            if run >= PASTE_RUN_LINES:
                return True
        else:
            run = 0
    return "```" in text and text.count("\n") >= PASTE_RUN_LINES


def make_snapshot(live_root: Path) -> dict:
    """Freeze the qualifying session files. Read-only on the live tree, always."""
    if SNAPSHOT_ROOT.exists():
        shutil.rmtree(SNAPSHOT_ROOT)
    SNAPSHOT_ROOT.mkdir(parents=True)
    entries = []
    scanned = 0
    for path in sorted(live_root.rglob("*.jsonl")):
        scanned += 1
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if HUMAN_MARKER.encode() not in raw:
            continue
        rel = path.relative_to(live_root)
        target = SNAPSHOT_ROOT / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        entries.append(
            {"path": rel.as_posix(), "sha256": gc.sha256_hex(raw), "bytes": len(raw)}
        )
    entries.sort(key=lambda row: row["path"])
    digest = hashlib.sha256()
    for row in entries:
        digest.update(f"{row['path']}\x1e{row['sha256']}\n".encode())
    manifest = {
        "schema": "memphant.eval.convo-lane-sources.v1",
        "source_root": live_root.as_posix(),
        "files_scanned": scanned,
        "file_count": len(entries),
        "total_bytes": sum(row["bytes"] for row in entries),
        "snapshot_sha256": digest.hexdigest(),
        "files": entries,
    }
    SNAPSHOT_MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def snapshot_provenance() -> dict:
    if not SNAPSHOT_MANIFEST.is_file():
        return {"pinned": False, "note": "no frozen snapshot; refusing to read live"}
    manifest = json.loads(SNAPSHOT_MANIFEST.read_text())
    return {
        "pinned": True,
        "snapshot_sha256": manifest["snapshot_sha256"],
        "file_count": manifest["file_count"],
        "files_scanned": manifest["files_scanned"],
        "total_bytes": manifest["total_bytes"],
    }


# --- stage 2: units ----------------------------------------------------------


def parse_session(path: Path) -> list[dict]:
    """Memory units: one human turn plus the agent's reply, in session order.

    A turn pair is the natural episodic unit for a coding-agent memory system and
    it bounds the corpus at the number of human turns rather than the number of
    tool calls.
    """
    units: list[dict] = []
    pending: dict | None = None
    agent_chunks: list[str] = []

    def close() -> None:
        nonlocal pending, agent_chunks
        if pending is None:
            return
        agent = "\n".join(chunk for chunk in agent_chunks if chunk).strip()
        pending["agent_text"] = agent[:AGENT_CLIP_CHARS]
        units.append(pending)
        pending, agent_chunks = None, []

    for line in path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if _is_human_record(record):
            close()
            pending = {
                "raw_text": _record_text(record),
                "timestamp": record.get("timestamp") or "",
                "uuid": record.get("uuid") or "",
                "cwd": record.get("cwd") or "",
                "git_branch": record.get("gitBranch") or "",
            }
            agent_chunks = []
        elif pending is not None and record.get("type") == "assistant":
            if not record.get("isSidechain"):
                agent_chunks.append(_record_text(record))
    close()
    return units


def derive_units(stats: Counter) -> list[dict]:
    """Every human turn in the snapshot, filtered by §3 and the §5 secret scan."""
    units: list[dict] = []
    for path in sorted(SNAPSHOT_ROOT.rglob("*.jsonl")):
        rel = path.relative_to(SNAPSHOT_ROOT)
        project = rel.parts[0]
        session = rel.stem
        for index, unit in enumerate(parse_session(path)):
            stats["human_turns_stamped"] += 1
            residual = strip_wrappers(unit["raw_text"])
            if not residual:
                stats["reject:harness_wrapper_only"] += 1
                continue
            if len(residual) < MIN_RESIDUAL_CHARS:
                stats["reject:too_short"] += 1
                continue
            if has_paste_run(residual):
                stats["reject:paste_guard"] += 1
                continue
            if len(residual) > MAX_QUESTION_CHARS:
                stats["reject:oversized_prompt"] += 1
                continue
            body = residual + "\n" + unit["agent_text"]
            found = secret_reasons(body)
            if found:
                for family in found:
                    stats[f"reject:secret_detected:{family}"] += 1
                stats["reject:secret_detected"] += 1
                continue
            stats["human_turns_admitted"] += 1
            units.append(
                {
                    "unit_id": f"{project}::{session}::{index:04d}",
                    "project": project,
                    "session": session,
                    "index": index,
                    "timestamp": unit["timestamp"],
                    "git_branch": unit["git_branch"],
                    "question": residual,
                    "agent_text": unit["agent_text"],
                    "text": (residual + "\n\n[agent] " + unit["agent_text"])[:UNIT_CLIP_CHARS],
                }
            )
    units.sort(key=lambda row: (row["project"], row["timestamp"], row["session"], row["index"]))
    for order, unit in enumerate(units):
        unit["order"] = order

    # §3.7 amendment A1 -- re-pasted boilerplate. The owner keeps standing
    # "handoff / resume" prompts and pastes them into session after session.
    # They are human-typed once and human-*pasted* thereafter, so as a query they
    # are not a spontaneous turn, and their nearest prior unit is a copy of
    # themselves -- which would hand the leakage metric a fake 1.0. A unit whose
    # question is >=0.80 Jaccard to a question in a DIFFERENT session is barred
    # from being a query. It stays in the haystack, where it belongs.
    signatures = [tokens(unit["question"]) for unit in units]
    for i, unit in enumerate(units):
        unit["can_query"] = True
    for i in range(len(units)):
        if not units[i]["can_query"]:
            continue
        for j in range(i + 1, len(units)):
            if units[j]["session"] == units[i]["session"]:
                continue
            if jaccard(signatures[i], signatures[j]) >= BOILERPLATE_JACCARD:
                units[i]["can_query"] = False
                units[j]["can_query"] = False
    stats["reject:boilerplate_repasted"] = sum(1 for u in units if not u["can_query"])
    return units


# --- stage 3: candidates -----------------------------------------------------


def haystack_for(units: list[dict], by_project: dict[str, list[dict]], unit: dict) -> list[dict]:
    """The realistic memory pool at query time: prior units of the same project."""
    prior = [row for row in by_project[unit["project"]] if row["order"] < unit["order"]]
    return prior[-HAYSTACK_MAX_UNITS:]


def select_candidates(units: list[dict]) -> list[dict]:
    """Mechanical, seeded, stable-key shape selection. Adjudication decides truth."""
    by_project: dict[str, list[dict]] = defaultdict(list)
    for unit in units:
        by_project[unit["project"]].append(unit)

    candidates: list[dict] = []
    for unit in units:
        if not unit.get("can_query", True):
            continue
        prior = haystack_for(units, by_project, unit)
        if not prior:
            continue
        question = unit["question"]
        qtokens = tokens(question)
        qartifacts = artifacts(question)

        # Target proposal. Ranked by SHARED CONCRETE ARTIFACTS first (that is
        # what "the thing the user is referring to" means), lexical overlap only
        # as a tiebreak, recency last. Deliberately NOT ranked by raw lexical
        # similarity: that ordering systematically picks the leakiest possible
        # target, which is the ordering this bank exists to avoid.
        scored = []
        for other in prior:
            overlap = jaccard(qtokens, tokens(other["text"]))
            if overlap >= TARGET_NEAR_DUP_JACCARD:
                continue  # §3.7 A1: a restatement of the question is not a target
            if overlap < TARGET_MIN_JACCARD:
                continue
            scored.append((len(qartifacts & artifacts(other["text"])), overlap, other["order"], other))
        if not scored:
            continue
        shared_count, overlap, _, best = max(scored, key=lambda row: row[:3])
        if shared_count == 0 and overlap < 0.10:
            continue

        cross_session = any(row["session"] != unit["session"] for row in prior)
        shapes: list[str] = []
        if BACKREF_RE.search(question) and cross_session and unit["index"] <= 4:
            shapes.append("task_resumption")
        if CORRECTION_RE.search(question) and unit["index"] >= 1:
            shapes.append("correction_retention")
        if SUPERSEDE_RE.search(question):
            shapes.append("state_churn")
        if len(qartifacts & artifacts(best["text"])) >= 1 and len(qartifacts) >= 1:
            shapes.append("file_symbol_grounding")
        for shape in shapes:
            candidates.append(
                {
                    "candidate_id": f"{shape}::{unit['unit_id']}",
                    "shape": shape,
                    "unit_id": unit["unit_id"],
                    "project": unit["project"],
                    "session": unit["session"],
                    "timestamp": unit["timestamp"],
                    "question": question,
                    "proposed_target_unit_id": best["unit_id"],
                    "proposed_target_order": best["order"],
                    "haystack_size": len(prior),
                    "haystack_unit_ids": [row["unit_id"] for row in prior],
                    "affinity": round(shared_count + overlap, 4),
                    "target_question_jaccard": round(overlap, 4),
                    "shared_artifacts": sorted(qartifacts & artifacts(best["text"])),
                }
            )

    # seeded, stable-key round robin so the mix is not dominated by one project
    buckets: dict[str, list[dict]] = defaultdict(list)
    for candidate in candidates:
        buckets[candidate["shape"]].append(candidate)
    picked: list[dict] = []
    for shape in SHAPES:
        rows = sorted(
            buckets.get(shape, []),
            key=lambda row: (
                -row["affinity"],
                hashlib.sha256(f"{SEED}:{row['candidate_id']}".encode()).hexdigest(),
            ),
        )
        seen_session: Counter = Counter()
        chosen = []
        for row in rows:
            if seen_session[row["session"]] >= PER_SESSION_MAX:
                continue
            seen_session[row["session"]] += 1
            chosen.append(row)
            if len(chosen) >= CANDIDATES_PER_SHAPE:
                break
        picked.extend(chosen)
    picked.sort(key=lambda row: row["candidate_id"])
    return picked


def build_packet(candidate: dict, by_id: dict[str, dict]) -> dict:
    """One adjudication packet. Private mirror only -- it carries session content.

    The target MUST be visible to the adjudicator, plus the recent tail for
    temporal context. Without that, a target older than the tail is judged
    blind, which is how a bank gets a rubber stamp instead of an adjudication.
    """
    ids = candidate["haystack_unit_ids"]
    index = ids.index(candidate["proposed_target_unit_id"])
    keep = dict.fromkeys(ids[max(0, index - 1) : index + 2] + ids[-10:])
    window = [by_id[uid] for uid in ids if uid in keep]
    return {
        "candidate_id": candidate["candidate_id"],
        "shape": candidate["shape"],
        "question": candidate["question"],
        "proposed_target_unit_id": candidate["proposed_target_unit_id"],
        "prior_context": [
            {"unit_id": row["unit_id"], "timestamp": row["timestamp"],
             "text": row["text"][:900]}
            for row in window
        ],
        "content_sha256": hashlib.sha256(
            json.dumps(
                [candidate["question"], candidate["shape"],
                 [row["unit_id"] for row in window]],
                sort_keys=True,
            ).encode()
        ).hexdigest(),
    }


def packet_hashes(candidates: list[dict], units: list[dict]) -> dict[str, str]:
    by_id = {unit["unit_id"]: unit for unit in units}
    return {
        row["candidate_id"]: build_packet(row, by_id)["content_sha256"]
        for row in candidates
    }


def write_packets(candidates: list[dict], units: list[dict]) -> None:
    by_id = {unit["unit_id"]: unit for unit in units}
    with PACKETS_PATH.open("w") as handle:
        for candidate in candidates:
            handle.write(json.dumps(build_packet(candidate, by_id), sort_keys=True) + "\n")


# --- stage 4: build ----------------------------------------------------------


def load_verdicts(packet_hashes: dict[str, str] | None = None) -> dict[str, dict]:
    """Adjudication verdicts, content-hash validated.

    This IS the reply cache the bar requires: a verdict is keyed by the packet's
    ``content_sha256``, so a rerun over an unchanged packet costs nothing and a
    rerun over a *changed* packet is refused rather than silently reused. $0 paid
    spend -- adjudication runs on subscription-model agent calls, never
    OpenRouter (whose path requires an authorized spend ledger).
    """
    sources = sorted((PRIVATE_ROOT / "verdicts").glob("*.jsonl")) if (
        PRIVATE_ROOT / "verdicts"
    ).is_dir() else []
    if VERDICTS_PATH.is_file():
        sources.append(VERDICTS_PATH)
    out: dict[str, dict] = {}
    for path in sources:
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "candidate_id" not in row:
                continue
            if packet_hashes is not None:
                expected = packet_hashes.get(row["candidate_id"])
                if expected and row.get("content_sha256") not in (None, expected):
                    continue  # stale verdict for a packet that has since changed
            out[row["candidate_id"]] = row
    return out


def track_u_token_sets() -> list[set[str]]:
    sets = []
    for path in (TRACK_U_PROBES, TRACK_U_GOLDEN):
        if not path.is_file():
            continue
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            sets.append(tokens(json.dumps(json.loads(line), sort_keys=True)))
    return sets


def build_bank(units: list[dict], candidates: list[dict], stats: Counter) -> tuple[list[dict], list[dict], dict]:
    by_id = {unit["unit_id"]: unit for unit in units}
    verdicts = load_verdicts(packet_hashes(candidates, units))
    track_u = track_u_token_sets()

    accepted: list[dict] = []
    per_shape: Counter = Counter()
    per_session: Counter = Counter()
    per_project: Counter = Counter()
    cross_jaccard: list[float] = []

    ordered = sorted(candidates, key=lambda row: row["candidate_id"])
    for candidate in ordered:
        verdict = verdicts.get(candidate["candidate_id"])
        if verdict is None:
            stats["reject:unadjudicated"] += 1
            continue
        if not verdict.get("accept"):
            stats[f"reject:adjudication_rejected:{verdict.get('reason', 'unspecified')}"] += 1
            stats["reject:adjudication_rejected"] += 1
            continue
        shape = verdict.get("shape") or candidate["shape"]
        if shape not in SHAPES:
            stats["reject:adjudication_rejected:bad_shape"] += 1
            continue
        target_id = verdict.get("target_unit_id") or candidate["proposed_target_unit_id"]
        if target_id not in by_id:
            stats["reject:target_missing"] += 1
            continue
        if target_id not in candidate["haystack_unit_ids"]:
            stats["reject:target_outside_haystack"] += 1
            continue
        correct = (verdict.get("observable_correct_behavior") or "").strip()
        forbidden = (verdict.get("forbidden_behavior") or "").strip()
        if not correct or not forbidden:
            stats["reject:missing_end_behavior"] += 1
            continue
        found = secret_reasons(correct + "\n" + forbidden)
        if found:
            for family in found:
                stats[f"reject:secret_detected:{family}"] += 1
            stats["reject:secret_detected"] += 1
            continue
        if verdict.get("content_sensitive"):
            stats["reject:content_sensitive_excluded"] += 1
            continue
        if per_shape[shape] >= BANK_MAX:
            continue
        if per_session[candidate["session"]] >= PER_SESSION_MAX:
            stats["reject:per_session_cap"] += 1
            continue
        if per_project[candidate["project"]] >= PER_PROJECT_MAX:
            stats["reject:per_project_cap"] += 1
            continue

        record = {
            "question_id": candidate["candidate_id"],
            "question_type": shape,
            "question": candidate["question"],  # verbatim human turn, never edited
            "observable_correct_behavior": correct,
            "forbidden_behavior": forbidden,
            "provenance": [
                {
                    "attempt_id": candidate["candidate_id"],
                    "event_sequence": candidate["haystack_unit_ids"].index(target_id),
                    "target_unit_id": target_id,
                    "query_unit_id": candidate["unit_id"],
                    "project": candidate["project"],
                    "session": candidate["session"],
                    "timestamp": candidate["timestamp"],
                }
            ],
            "identification": {
                "human_authored": True,
                "authored_before_answer_existed": True,
                "haystack_size": candidate["haystack_size"],
                "adjudicator_note": (verdict.get("note") or "")[:400],
            },
            "skeleton": skeleton(candidate["question"]),
        }
        qtokens = tokens(candidate["question"])
        overlap = max((jaccard(qtokens, other) for other in track_u), default=0.0)
        if overlap >= TRACK_U_JACCARD_MAX:
            stats["reject:track_u_duplicate"] += 1
            continue
        cross_jaccard.append(overlap)
        record["track_u_max_jaccard"] = round(overlap, 4)

        accepted.append(record)
        per_shape[shape] += 1
        per_session[candidate["session"]] += 1
        per_project[candidate["project"]] += 1
        stats["accepted"] += 1

    # §4.5 template guard, applied after selection
    skeleton_counts = Counter(row["skeleton"] for row in accepted)
    kept = []
    seen: Counter = Counter()
    for row in accepted:
        if seen[row["skeleton"]] >= SKELETON_SHARE_MAX:
            stats["reject:skeleton_cap"] += 1
            continue
        seen[row["skeleton"]] += 1
        kept.append(row)
    accepted = kept

    # the corpus the leakage metric measures against: per-golden scoped haystack
    by_candidate = {row["candidate_id"]: row for row in candidates}
    corpus = []
    for row in accepted:
        candidate = by_candidate[row["question_id"]]
        corpus.append(
            {
                "attempt_id": row["question_id"],
                "events": [
                    {"sequence": index, "text": by_id[uid]["text"]}
                    for index, uid in enumerate(candidate["haystack_unit_ids"])
                ],
            }
        )

    diagnostics = {
        "per_shape": dict(sorted(per_shape.items())),
        "per_project": dict(sorted(per_project.items())),
        "distinct_sessions": len({row["provenance"][0]["session"] for row in accepted}),
        "distinct_projects": len({row["provenance"][0]["project"] for row in accepted}),
        "skeleton_distinct": len({row["skeleton"] for row in accepted}),
        "skeleton_ratio": round(
            len({row["skeleton"] for row in accepted}) / len(accepted), 4
        ) if accepted else 0.0,
        "skeleton_max_share": max(skeleton_counts.values()) if skeleton_counts else 0,
        "track_u_max_jaccard": round(max(cross_jaccard), 4) if cross_jaccard else 0.0,
        "track_u_mean_jaccard": round(sum(cross_jaccard) / len(cross_jaccard), 4)
        if cross_jaccard
        else 0.0,
        "haystack_size_mean": round(
            sum(row["identification"]["haystack_size"] for row in accepted) / len(accepted), 2
        ) if accepted else 0.0,
    }
    return accepted, corpus, diagnostics


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def mirror(paths: list[Path]) -> dict[str, str]:
    PRIVATE_ROOT.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for path in paths:
        if not path.is_file():
            continue
        data = path.read_bytes()
        (PRIVATE_ROOT / path.name).write_bytes(data)
        hashes[path.name] = gc.sha256_hex(data)
    return hashes


LEAKAGE_PATH = DATA_DIR / "convo_lane_leakage.json"
LEAKAGE_SCRIPT = Path(__file__).resolve().parent / "track_r_leakage.py"
# Reference points the achieved distribution is published against (§4.1).
LEAKAGE_REFERENCES = {
    "track_r_original": {"target_mean": 0.3960, "floor_mean": 0.1008, "concentration": 3.93},
    "track_r_paraphrase": {"target_mean": 0.135, "floor_mean": 0.067, "concentration": 2.05},
}
CONCENTRATION_SHIP_BAR = 1.50
CONCENTRATION_CONSTRUCT_PREDICTION = 1.30
TARGET_MEAN_BAR = 0.25
TARGET_MAX_BAR = 0.60


def measure_leakage(goldens: list[dict], corpus: list[dict]) -> dict:
    """§4.1, computed by ``scripts/track_r_leakage.py`` -- byte-identical to the
    script the Track R paraphrase bar pins at ``1dd9435e…``. Unmodified: this
    extractor only has to emit inputs in that script's shape."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("track_r_leakage", LEAKAGE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.measure(goldens, corpus, module.NON_TARGET_SEED)
    report["leakage_script_sha256"] = gc.file_sha256(LEAKAGE_SCRIPT)
    report["references"] = LEAKAGE_REFERENCES
    return report


def leakage_summary(report: dict) -> dict:
    concentration = report["concentration_vs_exhaustive"]
    return {
        "n": report["n"],
        "leakage_script_sha256": report["leakage_script_sha256"],
        "leakage_script_modified": report["leakage_script_sha256"]
        != "1dd9435e13dc2a6cc893923dd8ef8aeed201309d4548026527988976121395f5",
        "metric": report["metric"],
        "target": report["target"],
        "non_target_exhaustive": report["non_target_exhaustive"],
        "non_target_sampled": report["non_target_sampled"],
        "concentration_vs_exhaustive": concentration,
        "concentration_vs_sampled": report["concentration_vs_sampled"],
        "by_shape": report["by_shape"],
        "references": LEAKAGE_REFERENCES,
        "verdict": (
            "below_construct_prediction"
            if concentration is not None and concentration <= CONCENTRATION_CONSTRUCT_PREDICTION
            else "ships_but_weaker_than_predicted"
            if concentration is not None and concentration <= CONCENTRATION_SHIP_BAR
            else "FAILS_SHIP_BAR"
        ),
    }


def make_lock(
    goldens: list[dict],
    corpus: list[dict],
    stats: Counter,
    diagnostics: dict,
    leakage: dict,
) -> dict:
    reject_counts = {
        key.split("reject:", 1)[1]: value
        for key, value in sorted(stats.items())
        if key.startswith("reject:")
    }
    bar = {
        "size_40_to_80": BANK_MIN <= len(goldens) <= BANK_MAX,
        "insufficient_below_24": len(goldens) < BANK_INSUFFICIENT_BELOW,
        "per_shape_min_6": all(
            diagnostics["per_shape"].get(shape, 0) >= PER_SHAPE_MIN for shape in SHAPES
        ),
        "distinct_sessions_min_20": diagnostics["distinct_sessions"] >= 20,
        "skeleton_ratio_min_0_90": diagnostics["skeleton_ratio"] >= SKELETON_RATIO_MIN,
        "skeleton_share_max_3": diagnostics["skeleton_max_share"] <= SKELETON_SHARE_MAX,
        "track_u_no_duplicate": diagnostics["track_u_max_jaccard"] < TRACK_U_JACCARD_MAX,
        "end_behavior_100pct": all(
            row["observable_correct_behavior"] and row["forbidden_behavior"] for row in goldens
        ),
        "question_verbatim_100pct": True,  # asserted mechanically by verify_verbatim
        "leakage_concentration_max_1_50": (
            leakage["concentration_vs_exhaustive"] is not None
            and leakage["concentration_vs_exhaustive"] <= CONCENTRATION_SHIP_BAR
        ),
        "leakage_target_mean_max_0_25": leakage["target"]["mean"] <= TARGET_MEAN_BAR,
        "leakage_target_max_0_60": leakage["target"]["max"] <= TARGET_MAX_BAR,
        "leakage_meets_construct_prediction_1_30": (
            leakage["concentration_vs_exhaustive"] is not None
            and leakage["concentration_vs_exhaustive"] <= CONCENTRATION_CONSTRUCT_PREDICTION
        ),
    }
    return {
        "schema": "memphant.eval.convo-lane-golden.v1",
        "prereg": "docs/build-log/2026-07-31-convo-lane-bar-and-privacy.md",
        "seed": SEED,
        "extractor_sha256": gc.file_sha256(Path(__file__)),
        "source_snapshot": snapshot_provenance(),
        "golden_path": GOLDEN_PATH.relative_to(gc.MEMPHANT_ROOT).as_posix(),
        "golden_sha256": gc.file_sha256(GOLDEN_PATH),
        "golden_bytes": GOLDEN_PATH.stat().st_size,
        "corpus_path": CORPUS_PATH.relative_to(gc.MEMPHANT_ROOT).as_posix(),
        "corpus_sha256": gc.file_sha256(CORPUS_PATH),
        "corpus_bytes": CORPUS_PATH.stat().st_size,
        "goldens": len(goldens),
        "corpus_scopes": len(corpus),
        "corpus_events": sum(len(row["events"]) for row in corpus),
        "human_turn_rule": {
            "marker": "type=user AND origin.kind=human AND isSidechain=false "
            "AND no toolUseResult AND not isMeta",
            "min_residual_chars": MIN_RESIDUAL_CHARS,
            "paste_run_lines": PASTE_RUN_LINES,
        },
        "yield": {
            "human_turns_stamped": stats["human_turns_stamped"],
            "human_turns_admitted": stats["human_turns_admitted"],
            "candidates_selected": stats["candidates_selected"],
            "accepted": len(goldens),
        },
        "reject_counts_by_reason": reject_counts,
        "diagnostics": diagnostics,
        "leakage": leakage,
        "bar": bar,
        "spotcheck_state": "emitted_pending_owner_review",
        "spend_usd": 0.0,
    }


def verify_verbatim(goldens: list[dict], units: list[dict]) -> None:
    """§4.2 hard bar: the question is byte-identical to the source residual."""
    by_id = {unit["unit_id"]: unit for unit in units}
    for row in goldens:
        source = by_id[row["provenance"][0]["query_unit_id"]]
        if row["question"] != source["question"]:
            raise SystemExit(
                f"FATAL: question not verbatim for {row['question_id']} -- "
                "§4.2 is a hard reject, not a repair"
            )


# --- CLI ---------------------------------------------------------------------


def derive() -> tuple[list[dict], list[dict], Counter]:
    if not SNAPSHOT_MANIFEST.is_file():
        raise SystemExit("no frozen snapshot; run --snapshot first (§7 forbids live reads)")
    stats: Counter = Counter()
    units = derive_units(stats)
    candidates = select_candidates(units)
    stats["candidates_selected"] = len(candidates)
    return units, candidates, stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", action="store_true")
    parser.add_argument("--extract", action="store_true")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--live-root", type=Path, default=LIVE_CLAUDE_PROJECTS)
    args = parser.parse_args()

    if args.snapshot:
        manifest = make_snapshot(args.live_root)
        print(json.dumps({k: v for k, v in manifest.items() if k != "files"}, indent=2))
        return 0

    if args.extract:
        units, candidates, stats = derive()
        PRIVATE_ROOT.mkdir(parents=True, exist_ok=True)
        write_jsonl(CANDIDATES_PATH, candidates)
        write_packets(candidates, units)
        print(json.dumps({
            "units": len(units),
            "candidates": len(candidates),
            "by_shape": dict(Counter(row["shape"] for row in candidates)),
            "stats": dict(sorted(stats.items())),
            "packets": PACKETS_PATH.as_posix(),
        }, indent=2))
        return 0

    if args.build or args.check:
        units, candidates, stats = derive()
        goldens, corpus, diagnostics = build_bank(units, candidates, stats)
        verify_verbatim(goldens, units)
        if args.check:
            if not LOCK_PATH.is_file():
                raise SystemExit("no lock to check against; run --build first")
            previous = json.loads(LOCK_PATH.read_text())
            tmp_golden = json.dumps([json.dumps(r, sort_keys=True) for r in goldens])
            digest = hashlib.sha256(
                ("\n".join(json.dumps(r, sort_keys=True) for r in goldens) + "\n").encode()
            ).hexdigest()
            ok = digest == previous.get("golden_sha256") and len(goldens) == previous.get("goldens")
            print(json.dumps({
                "recomputed_goldens": len(goldens),
                "recomputed_sha256": digest,
                "locked_sha256": previous.get("golden_sha256"),
                "match": ok,
            }, indent=2))
            return 0 if ok else 1
        if not goldens:
            raise SystemExit("no goldens accepted; adjudicate first (see §4.3)")
        write_jsonl(GOLDEN_PATH, goldens)
        write_jsonl(CORPUS_PATH, corpus)
        rng_order = sorted(
            goldens,
            key=lambda row: hashlib.sha256(f"{SEED}:spot:{row['question_id']}".encode()).hexdigest(),
        )
        write_jsonl(SPOTCHECK_PATH, rng_order[:SPOTCHECK_N])
        # canonical, sorted verdict ledger -- the reply cache, mirrored
        ledger = sorted(
            load_verdicts(packet_hashes(candidates, units)).values(),
            key=lambda row: row["candidate_id"],
        )
        write_jsonl(VERDICTS_PATH, ledger)
        report = measure_leakage(goldens, corpus)
        LEAKAGE_PATH.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        lock = make_lock(goldens, corpus, stats, diagnostics, leakage_summary(report))
        lock["mirror_sha256"] = mirror(
            [GOLDEN_PATH, CORPUS_PATH, SPOTCHECK_PATH, VERDICTS_PATH, LEAKAGE_PATH]
        )
        LOCK_PATH.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")
        mirror([LOCK_PATH])
        printable = {k: v for k, v in lock.items() if k != "mirror_sha256"}
        print(json.dumps(printable, indent=2, sort_keys=True))
        return 0

    parser.error("pick a stage: --snapshot | --extract | --build | --check")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
