#!/usr/bin/env python3
"""Track R repo-memory golden miner (accuracy-first program, Phase 1a-R).

Mines a 150-200 golden repo-memory bank from the pinned, CC-BY-4.0
``nebius/SWE-rebench-openhands-trajectories`` corpus materialized by
``scripts/materialize_public_code_lane.py`` (the proven adapter path), across
three shapes: ``state-churn``, ``file-symbol-grounding``, ``task-resumption``.

The mechanism from ``code_lane_mine.py`` is reused (verbatim span location,
too-generic span rejection, seeded round-robin sampling, lexical overlap); what
is new is the enforcement of the three failure modes recorded in
``docs/build-log/artifacts/c3-public-code-lane-v3/rejection-receipt.json`` and
preregistered numerically in ``docs/build-log/2026-07-30-track-r-golden-bar.md``:

1. **causal identification** — a question ships only if it contains >= 2
   *distinguishing* tokens (attempt-level corpus document frequency <=
   ``--df-max``) that also occur in the target event, AND the conjunction of
   those tokens narrows the whole 64k-event corpus to <= ``--narrow-max``
   events *including the target*. A generic template cannot pass: its tokens are
   corpus-common by construction.
2. **distractors adjudicated** — the non-target members of that narrowed set
   ARE the plausible distractors. Every one is put to the adjudicator; a single
   ``also_answers: true`` rejects the golden; the per-distractor verdicts are
   recorded on the golden.
3. **no generic templates** — questions are reduced to a skeleton (quoted
   strings, paths, dotted/underscored/CamelCase identifiers and digits erased)
   and a skeleton may back at most ``--skeleton-cap`` goldens.

Generation and adjudication run on **subscription-model agent calls** (the plan's
marginal-$0 accounting), not on a paid API. Each call is a request file this
program emits plus a reply file an agent writes into the cache directory, keyed
by ``sha256(kind + system + prompt)`` in the same discipline as
``run_reader.ReaderCli``'s cache. Consequence: a warm-cache rerun re-emits
byte-identical goldens for free, and ``--verify-lock`` fails if it does not.

Run it as a loop. Each ``--stage mine`` run consumes what is cached, stops at
the first pending call, and emits request batches (including lookahead) for the
agent to fulfill:

    python3 scripts/track_r_mine.py --stage mine     # -> exit 2 while pending
    # ... agent fulfills docs/build-log/artifacts/track-r/requests/*.json ...
    python3 scripts/track_r_mine.py --stage mine     # repeat until exit 0
    python3 scripts/track_r_mine.py --verify-lock

Outputs: ``benchmarks/data/track_r_repo_memory_golden.jsonl`` (gitignored
bodies), ``..._spotcheck.jsonl`` (15-golden owner sample, gitignored), and the
ONE committed artifact ``..._golden.lock.json``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate_common as gc  # noqa: E402
import gate_mine_goldens as gm  # noqa: E402
from code_lane_mine import lexical_overlap, locate_span_in_event, too_generic  # noqa: E402

ART = gc.MEMPHANT_ROOT / "docs" / "build-log" / "artifacts" / "track-r"
CORPUS_PATH = ART / "corpus.jsonl"
CORPUS_LOCK_PATH = ART / "corpus-adapter.lock.json"
CACHE_DIR = ART / "agent-cache"
REQUEST_DIR = ART / "requests"
GOLDEN_PATH = gc.MEMPHANT_ROOT / "benchmarks" / "data" / "track_r_repo_memory_golden.jsonl"
SPOTCHECK_PATH = gc.MEMPHANT_ROOT / "benchmarks" / "data" / "track_r_repo_memory_spotcheck.jsonl"
BAR_DOC = "docs/build-log/2026-07-30-track-r-golden-bar.md"

SHAPES = ("state-churn", "file-symbol-grounding", "task-resumption")
SAMPLE_SEED = 20260730
TARGET_TOTAL = 180
SHAPE_TARGET = 60
SHAPE_MIN = 40
DF_MAX = 5
NARROW_MAX = 8
MIN_DISTINGUISHING = 2
SKELETON_CAP = 2
PER_ATTEMPT_CAP = 3
PER_REPO_CAP = 4
MIN_SPAN_CHARS = 8
MAX_SPAN_CHARS = 200
MAX_OVERLAP = 0.60
TOO_GENERIC_THRESHOLD = 3
EVENT_MIN_CHARS = 200
EVENT_MAX_CHARS_PROMPT = 3000
PREVIEW_MAX_EVENTS = 3
PREVIEW_MAX_CHARS_EACH = 400
DISTRACTOR_MAX = 5
DISTRACTOR_EXCERPT_CHARS = 1200
CANDIDATES_PER_SHAPE = 200
PREFETCH = 260
GEN_BATCH = 12
ADJ_BATCH = 8
SPOTCHECK_N = 15

IDENT_RE = re.compile(r"[a-z0-9_]{4,}")
DOTTED_RE = re.compile(r"[a-z0-9_]+(?:\.[a-z0-9_]+)+")
PATH_RE = re.compile(r"[\w./-]*\.(?:py|pyx|pyi|rs|js|ts|toml|cfg|ini|txt|md|yaml|yml|json)\b")
SYMBOL_RE = re.compile(r"\b(?:def|class)\s+([A-Za-z_]\w*)")
PENDING_DIAGNOSTIC_RE = re.compile(
    r"(?:Error|Exception|Traceback|FAILED|failed|assert|AssertionError|TODO|"
    r"still needs|remaining|next step|not yet)",
)

SKELETON_ERASE = (
    re.compile(r"'[^']*'|\"[^\"]*\""),
    re.compile(r"[\w./-]*/[\w./-]+"),
    re.compile(r"\b\w+\.\w[\w.]*\b"),
    re.compile(r"\b\w*_\w[\w_]*\b"),
    re.compile(r"\b[A-Za-z]+[A-Z]\w*\b"),
    re.compile(r"\d+"),
)


# --- pure helpers (unit-tested in tests/test_track_r_mine.py) ---------------


def tokens(text: str) -> set[str]:
    low = text.lower()
    return set(IDENT_RE.findall(low)) | set(DOTTED_RE.findall(low))


def skeleton(question: str) -> str:
    text = question
    for pattern in SKELETON_ERASE:
        text = pattern.sub(" ", text)
    return " ".join(re.sub(r"[^a-z ]+", " ", text.lower()).split())


def cache_key(kind: str, system: str, prompt: str) -> str:
    return hashlib.sha256("\x1e".join(["agent", kind, system, prompt]).encode()).hexdigest()


def file_paths(text: str) -> list[str]:
    return sorted({m.group(0) for m in PATH_RE.finditer(text) if len(m.group(0)) > 6})


def candidate_key(candidate: dict) -> str:
    return f"{candidate['shape']}::{candidate['attempt_id']}::{candidate['sequence']}"


def shape_candidates_for_attempt(row: dict, min_chars: int) -> list[dict]:
    """Every candidate target event this attempt can support, tagged by shape.

    - ``state-churn``: a file path touched at >=2 separated points; the target
      is a later touch, so answering from the earlier state is wrong.
    - ``file-symbol-grounding``: the event carries both a concrete file path and
      a ``def``/``class`` symbol.
    - ``task-resumption``: the event sits in the last 40% of the attempt and
      carries an unresolved-diagnostic / pending-work marker.
    """
    events = row["events"]
    total = len(events)
    out: list[dict] = []
    path_positions: dict[str, list[int]] = {}
    for index, event in enumerate(events):
        for path in file_paths(event["text"]):
            path_positions.setdefault(path, []).append(index)
    for index, event in enumerate(events):
        text = event["text"]
        if len(text) < min_chars:
            continue
        paths = file_paths(text)
        symbols = SYMBOL_RE.findall(text)
        base = {
            "attempt_id": row["attempt_id"],
            "run_id": row["run_id"],
            "repository": row["repository"],
            "sequence": event["sequence"],
            "event_index": index,
            "attempt_event_count": total,
            "role": event["role"],
            "event_id": event["event_id"],
            "text": text,
        }
        churn_paths = [
            path
            for path in paths
            if len([p for p in path_positions.get(path, []) if p < index - 1]) >= 1
            and len(path_positions.get(path, [])) >= 2
        ]
        if churn_paths:
            out.append({**base, "shape": "state-churn", "focus": churn_paths[0]})
        if paths and symbols:
            out.append(
                {**base, "shape": "file-symbol-grounding", "focus": f"{paths[0]}::{symbols[0]}"}
            )
        if index >= int(total * 0.60) and PENDING_DIAGNOSTIC_RE.search(text):
            out.append({**base, "shape": "task-resumption", "focus": ""})
    return out


def draw_candidates(pool: list[dict], seed: int, per_shape: int) -> list[dict]:
    """Seeded round-robin across the three shape buckets over a candidate list
    sorted by stable key — deterministic given the corpus and the seed. At most
    one candidate per (shape, attempt) is drawn so a single verbose trajectory
    cannot dominate a shape."""
    rng = random.Random(seed)
    by_shape: dict[str, list[dict]] = {shape: [] for shape in SHAPES}
    for candidate in pool:
        by_shape[candidate["shape"]].append(candidate)
    for shape in SHAPES:
        by_shape[shape].sort(key=candidate_key)
        rng.shuffle(by_shape[shape])
        seen: set[str] = set()
        deduped = []
        for candidate in by_shape[shape]:
            if candidate["attempt_id"] in seen:
                continue
            seen.add(candidate["attempt_id"])
            deduped.append(candidate)
        by_shape[shape] = deduped[:per_shape]
    out: list[dict] = []
    cursors = {shape: 0 for shape in SHAPES}
    while any(cursors[s] < len(by_shape[s]) for s in SHAPES):
        for shape in SHAPES:
            if cursors[shape] < len(by_shape[shape]):
                out.append(by_shape[shape][cursors[shape]])
                cursors[shape] += 1
    return out


def build_context_preview(events: list[dict], target_index: int) -> str:
    start = max(0, target_index - PREVIEW_MAX_EVENTS)
    return "\n\n".join(
        f"{event['role']}: {event['text'][:PREVIEW_MAX_CHARS_EACH]}"
        for event in events[start:target_index]
    )


# --- corpus indices --------------------------------------------------------


def attempt_document_frequency(rows: list[dict]) -> Counter:
    df: Counter = Counter()
    for row in rows:
        seen: set[str] = set()
        for event in row["events"]:
            seen |= tokens(event["text"])
        df.update(seen)
    return df


def build_narrowing_index(
    rows: list[dict], watched: set[str]
) -> dict[str, list[tuple[str, int]]]:
    """token -> sorted [(attempt_id, sequence)] for the rare tokens we actually
    need (those occurring in a drawn candidate target). Restricting the index to
    watched tokens is what keeps a 64k-event inverted index affordable."""
    index: dict[str, list[tuple[str, int]]] = {}
    for row in rows:
        for event in row["events"]:
            for token in tokens(event["text"]) & watched:
                index.setdefault(token, []).append((row["attempt_id"], event["sequence"]))
    for token in index:
        index[token] = sorted(set(index[token]))
    return index


# --- prompts ---------------------------------------------------------------


GEN_SYSTEM = {
    "state-churn": (
        "You author one repo-memory question over a slice of an autonomous AI "
        "coding agent's execution transcript. This question tests STATE CHURN: "
        "the FOCUS FILE was touched more than once in this run, and the answer "
        "must come from the LATER state shown in the TARGET EVENT, so that an "
        "answer taken from the earlier touch would be wrong."
    ),
    "file-symbol-grounding": (
        "You author one repo-memory question over a slice of an autonomous AI "
        "coding agent's execution transcript. This question tests FILE/SYMBOL "
        "GROUNDING: it must ask about a concrete code fact tied to a named file "
        "and a named symbol appearing in the TARGET EVENT."
    ),
    "task-resumption": (
        "You author one repo-memory question over a slice of an autonomous AI "
        "coding agent's execution transcript. This question tests TASK "
        "RESUMPTION: phrase it as someone picking this specific run back up "
        "later and needing the concrete unresolved detail recorded in the "
        "TARGET EVENT."
    ),
}

GEN_RULES = (
    " Requirements, ALL mandatory. (1) The answer MUST be a span of roughly 8 "
    "to 200 characters copied VERBATIM, character-for-character, from the "
    "TARGET EVENT text only — never paraphrased, never invented, never taken "
    "from the preceding context. (2) The question MUST causally identify THIS "
    "event: it must name at least two concrete, distinctive things that appear "
    "in the target event — a specific file path, function or class name, "
    "module, exception type, test id, command, or config key. A question that "
    "would fit any other run in any other repository is a FAILURE; do not "
    "write a template. (3) Do NOT reveal the answer: paraphrase so the "
    "question shares as few words as possible with the answer span, and never "
    "quote the answer span inside the question. (4) Never ask a meta question "
    "about the transcript, its format, or turn numbers. Output ONLY a JSON "
    'object with keys "question" and "answer_span". No markdown, no code '
    "fence, no commentary."
)


def gen_system(shape: str) -> str:
    return GEN_SYSTEM[shape] + GEN_RULES


def gen_prompt(candidate: dict, preview: str) -> str:
    parts = [f"REPOSITORY: {candidate['repository']}"]
    if candidate.get("focus"):
        parts.append(f"FOCUS: {candidate['focus']}")
    if preview:
        parts.append(
            "PRECEDING CONTEXT (background only — the answer_span must NOT come "
            "from here):\n" + preview
        )
    parts.append(
        "TARGET EVENT (role: "
        f"{candidate['role']}, event {candidate['event_index']} of "
        f"{candidate['attempt_event_count']}) — the answer_span MUST be copied "
        "verbatim from HERE:\n" + candidate["text"][:EVENT_MAX_CHARS_PROMPT]
    )
    return "\n\n".join(parts)


ADJ_SYSTEM = (
    "You adjudicate one candidate benchmark question over an AI coding agent's "
    "execution transcripts. You are given the QUESTION, the CLAIMED ANSWER "
    "copied from the TARGET EVENT, the target event, and numbered CANDIDATE "
    "DISTRACTORS: other events from the same corpus that a retrieval system "
    "could plausibly return for this question. Decide two things. First, "
    "whether the question causally or semantically identifies the target event "
    "— i.e. a competent engineer reading the question would know which "
    "specific moment of which specific run is being asked about, and the "
    "claimed answer is genuinely responsive to it. Second, for EACH numbered "
    "distractor, whether that event ALSO answers the question, meaning a "
    "reader given only that event could produce a defensible answer to the "
    "question as asked. Be strict: if the question is vague enough that a "
    "distractor answers it too, say so. Output ONLY a JSON object: "
    '{"target_identified": true|false, "reason": "<one sentence>", '
    '"distractors": [{"index": <int>, "also_answers": true|false, "why": '
    '"<short>"}]}. Include exactly one entry per numbered distractor. No '
    "markdown, no code fence, no commentary."
)


def adj_prompt(question: str, answer: str, candidate: dict, distractors: list[dict]) -> str:
    parts = [
        f"QUESTION: {question}",
        f"CLAIMED ANSWER (verbatim from the target event): {answer}",
        f"TARGET EVENT (repository {candidate['repository']}, role "
        f"{candidate['role']}):\n{candidate['text'][:DISTRACTOR_EXCERPT_CHARS]}",
    ]
    if distractors:
        for number, event in enumerate(distractors, start=1):
            parts.append(
                f"CANDIDATE DISTRACTOR {number} (repository "
                f"{event['repository']}, role {event['role']}):\n"
                f"{event['text'][:DISTRACTOR_EXCERPT_CHARS]}"
            )
    else:
        parts.append(
            "CANDIDATE DISTRACTORS: none — no other corpus event carries the "
            "question's distinguishing terms. Return an empty distractors list."
        )
    return "\n\n".join(parts)


# --- agent-call cache ------------------------------------------------------


class AgentCache:
    """Content-hash cache of subscription-model agent replies. A miss is not an
    error: it is recorded as a pending request for an agent to fulfill by
    writing the reply JSON to ``<cache_dir>/<key>.json``."""

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        self.hits = 0
        self.misses = 0
        self.pending: dict[str, dict] = {}

    def get(self, kind: str, system: str, prompt: str) -> str | None:
        key = cache_key(kind, system, prompt)
        path = self.cache_dir / f"{key}.json"
        if path.exists():
            self.hits += 1
            return path.read_text()
        self.misses += 1
        self.pending.setdefault(
            key, {"cache_key": key, "kind": kind, "system": system, "prompt": prompt}
        )
        return None


def emit_requests(cache: AgentCache, request_dir: Path) -> list[str]:
    """Group pending requests into per-kind batch files for agent fulfillment.
    Batch contents are deterministic: pending requests sorted by cache key."""
    for stale in sorted(request_dir.glob("*.json")):
        stale.unlink()
    request_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    by_kind: dict[str, list[dict]] = {}
    for request in cache.pending.values():
        by_kind.setdefault(request["kind"], []).append(request)
    for kind in sorted(by_kind):
        requests = sorted(by_kind[kind], key=lambda r: r["cache_key"])
        size = GEN_BATCH if kind.startswith("generate") else ADJ_BATCH
        for number, start in enumerate(range(0, len(requests), size), start=1):
            batch = requests[start : start + size]
            path = request_dir / f"{kind}-{number:03d}.json"
            path.write_text(
                json.dumps(
                    {
                        "kind": kind,
                        "reply_dir": str(CACHE_DIR),
                        "instructions": (
                            "For each item, answer the prompt under the shared "
                            "system instruction and write the reply — the bare "
                            "JSON object it asks for, nothing else — to "
                            "<reply_dir>/<cache_key>.json"
                        ),
                        "system": batch[0]["system"],
                        "items": [
                            {"cache_key": r["cache_key"], "prompt": r["prompt"]} for r in batch
                        ],
                    },
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n"
            )
            written.append(path.name)
    return written


def parse_adjudication(reply: str, n_distractors: int) -> dict | None:
    try:
        obj = json.loads(gm.strip_json(reply))
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict) or not isinstance(obj.get("target_identified"), bool):
        return None
    entries = obj.get("distractors")
    if not isinstance(entries, list) or len(entries) != n_distractors:
        return None
    verdicts = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("also_answers"), bool):
            return None
        verdicts.append(
            {
                "also_answers": entry["also_answers"],
                "why": str(entry.get("why", ""))[:240],
            }
        )
    return {
        "target_identified": obj["target_identified"],
        "reason": str(obj.get("reason", ""))[:240],
        "distractors": verdicts,
    }


# --- mining ---------------------------------------------------------------

REJECT_REASONS = (
    "parse_failed",
    "span_not_located",
    "span_length",
    "too_generic_span",
    "overlap_too_high",
    "insufficient_distinguishing_tokens",
    "identification_not_narrowed",
    "generic_skeleton",
    "per_attempt_cap",
    "per_repo_cap",
    "shape_target_met",
    "adjudication_parse_failed",
    "adjudication_target_not_identified",
    "distractor_also_answers",
)


class Miner:
    def __init__(self, args, rows: list[dict], df: Counter) -> None:
        self.args = args
        self.rows = rows
        self.df = df
        self.events_by_attempt = {row["attempt_id"]: row["events"] for row in rows}
        self.rows_by_attempt = {row["attempt_id"]: row for row in rows}
        self.corpus_index = {
            row["attempt_id"]: "\n\n".join(e["text"] for e in row["events"]) for row in rows
        }
        self.narrowing: dict[str, list[tuple[str, int]]] = {}
        self.cache = AgentCache(CACHE_DIR)

    def event_text(self, attempt_id: str, sequence: int) -> dict | None:
        row = self.rows_by_attempt.get(attempt_id)
        if row is None:
            return None
        for event in row["events"]:
            if event["sequence"] == sequence:
                return {
                    "attempt_id": attempt_id,
                    "repository": row["repository"],
                    "sequence": sequence,
                    "role": event["role"],
                    "text": event["text"],
                    "event_id": event["event_id"],
                }
        return None

    def distinguishing(self, candidate: dict, question: str) -> list[str]:
        target = tokens(candidate["text"])
        asked = tokens(question)
        return sorted(
            token
            for token in target & asked
            if self.df.get(token, 0) <= self.args.df_max and len(token) >= 4
        )

    def narrow(self, terms: list[str]) -> list[tuple[str, int]]:
        sets = [set(self.narrowing.get(term, [])) for term in terms]
        if not sets:
            return []
        keep = set.intersection(*sets)
        return sorted(keep)

    def mechanical(self, candidate: dict) -> tuple[dict | None, str | None, bool]:
        """Generation + every mechanical gate. Returns
        ``(payload, reject_reason, pending)``."""
        events = self.events_by_attempt[candidate["attempt_id"]]
        preview = build_context_preview(events, candidate["event_index"])
        system = gen_system(candidate["shape"])
        prompt = gen_prompt(candidate, preview)
        reply = self.cache.get(f"generate-{candidate['shape']}", system, prompt)
        if reply is None:
            return None, None, True
        obj = gm.parse_reply(reply, ("question", "answer_span"))
        if obj is None:
            return None, "parse_failed", False
        question = obj["question"].strip()
        located = locate_span_in_event(candidate["text"], obj["answer_span"].strip())
        if located is None:
            return None, "span_not_located", False
        start, end, exact = located
        if not (self.args.min_span_chars <= len(exact) <= self.args.max_span_chars):
            return None, "span_length", False
        if too_generic(exact, self.corpus_index, self.args.too_generic_threshold):
            return None, "too_generic_span", False
        overlap = lexical_overlap(question, exact)
        if overlap > self.args.max_overlap:
            return None, "overlap_too_high", False
        terms = self.distinguishing(candidate, question)
        if len(terms) < self.args.min_distinguishing:
            return None, "insufficient_distinguishing_tokens", False
        narrowed = self.narrow(terms)
        target_ref = (candidate["attempt_id"], candidate["sequence"])
        if target_ref not in narrowed or len(narrowed) > self.args.narrow_max:
            return None, "identification_not_narrowed", False
        distractor_refs = [ref for ref in narrowed if ref != target_ref][: self.args.distractor_max]
        return (
            {
                "question": question,
                "span": exact,
                "char_start": start,
                "char_end": end,
                "overlap": overlap,
                "terms": terms,
                "narrowed": len(narrowed),
                "distractor_refs": distractor_refs,
            },
            None,
            False,
        )

    def adjudicate(self, candidate: dict, payload: dict) -> tuple[dict | None, str | None, bool]:
        distractors = [
            event
            for event in (self.event_text(*ref) for ref in payload["distractor_refs"])
            if event is not None
        ]
        prompt = adj_prompt(payload["question"], payload["span"], candidate, distractors)
        reply = self.cache.get("adjudicate", ADJ_SYSTEM, prompt)
        if reply is None:
            return None, None, True
        verdict = parse_adjudication(reply, len(distractors))
        if verdict is None:
            return None, "adjudication_parse_failed", False
        if not verdict["target_identified"]:
            return None, "adjudication_target_not_identified", False
        adjudicated = []
        for event, entry in zip(distractors, verdict["distractors"]):
            if entry["also_answers"]:
                return None, "distractor_also_answers", False
            adjudicated.append(
                {
                    "attempt_id": event["attempt_id"],
                    "event_sequence": event["sequence"],
                    "event_id": event["event_id"],
                    "also_answers": False,
                    "why": entry["why"],
                }
            )
        return (
            {
                "target_identified": True,
                "reason": verdict["reason"],
                "distractors": adjudicated,
            },
            None,
            False,
        )

    def run(self, candidates: list[dict]) -> tuple[list[dict], Counter, int, int]:
        goldens: list[dict] = []
        rejects: Counter = Counter()
        per_shape: Counter = Counter()
        per_attempt: Counter = Counter()
        per_repo: Counter = Counter()
        skeletons: Counter = Counter()
        attempted = 0
        consumed = 0
        for candidate in candidates:
            if len(goldens) >= self.args.target:
                break
            consumed += 1
            if per_shape[candidate["shape"]] >= self.args.shape_target:
                rejects["shape_target_met"] += 1
                continue
            if per_attempt[candidate["attempt_id"]] >= self.args.per_attempt_cap:
                rejects["per_attempt_cap"] += 1
                continue
            if per_repo[candidate["repository"]] >= self.args.per_repo_cap:
                rejects["per_repo_cap"] += 1
                continue
            payload, reason, pending = self.mechanical(candidate)
            if pending:
                consumed -= 1
                break
            attempted += 1
            if reason is not None:
                rejects[reason] += 1
                continue
            skeleton_text = skeleton(payload["question"])
            if skeletons[skeleton_text] >= self.args.skeleton_cap:
                rejects["generic_skeleton"] += 1
                continue
            verdict, reason, pending = self.adjudicate(candidate, payload)
            if pending:
                consumed -= 1
                attempted -= 1
                break
            if reason is not None:
                rejects[reason] += 1
                continue
            skeletons[skeleton_text] += 1
            per_shape[candidate["shape"]] += 1
            per_attempt[candidate["attempt_id"]] += 1
            per_repo[candidate["repository"]] += 1
            goldens.append(self.emit(candidate, payload, verdict, skeleton_text, len(goldens) + 1))
        return goldens, rejects, attempted, consumed

    def emit(
        self, candidate: dict, payload: dict, verdict: dict, skeleton_text: str, number: int
    ) -> dict:
        return {
            "question_id": f"track_r_{number:03d}",
            "question_type": candidate["shape"],
            "is_abstention": False,
            "question": payload["question"],
            "question_date": self.rows_by_attempt[candidate["attempt_id"]]["started_at"],
            "gold_answer": payload["span"],
            "multi_hop": False,
            "repository": candidate["repository"],
            "provenance": [
                {
                    "role": "answer",
                    "attempt_id": candidate["attempt_id"],
                    "run_id": candidate["run_id"],
                    "event_sequence": candidate["sequence"],
                    "event_role": candidate["role"],
                    "event_id": candidate["event_id"],
                    "event_index": candidate["event_index"],
                    "attempt_event_count": candidate["attempt_event_count"],
                    "span": payload["span"],
                    "char_start": payload["char_start"],
                    "char_end": payload["char_end"],
                }
            ],
            "identification": {
                "distinguishing_terms": payload["terms"],
                "narrowed_event_count": payload["narrowed"],
                "lexical_overlap": round(payload["overlap"], 4),
            },
            "adjudication": {
                "adjudicator": "subscription_agent",
                "target_identified": True,
                "reason": verdict["reason"],
                "distractors_considered": len(verdict["distractors"]),
                "distractors": verdict["distractors"],
            },
            "question_skeleton": skeleton_text,
            "source_event_key": candidate_key(candidate),
        }

    def prefetch(self, candidates: list[dict], start: int, count: int) -> None:
        """Speculatively request the next ``count`` candidates' generation, and
        the adjudication of any whose generation is already cached, so the agent
        can fulfill a full wave per round. Cache-only: no gate state is
        mutated."""
        for candidate in candidates[start : start + count]:
            payload, reason, pending = self.mechanical(candidate)
            if pending or reason is not None:
                continue
            self.adjudicate(candidate, payload)


# --- lock / report --------------------------------------------------------


def build_lock(args, goldens, rejects, attempted, corpus_lock, cache, spotcheck_ids) -> dict:
    golden_bytes = GOLDEN_PATH.read_bytes()
    overlaps = [row["identification"]["lexical_overlap"] for row in goldens]
    skeleton_counts = Counter(row["question_skeleton"] for row in goldens)
    with_distractors = sum(
        1 for row in goldens if row["adjudication"]["distractors_considered"] > 0
    )
    strata = Counter(row["question_type"] for row in goldens)
    count = len(goldens)
    checks = {
        "size_150_200": 150 <= count <= 200,
        "per_shape_min_40": all(strata.get(shape, 0) >= SHAPE_MIN for shape in SHAPES),
        "distinct_attempts_min_50": len({row["provenance"][0]["attempt_id"] for row in goldens})
        >= 50,
        "per_attempt_cap_3": max(
            Counter(row["provenance"][0]["attempt_id"] for row in goldens).values(), default=0
        )
        <= 3,
        "per_repo_cap_4": max(
            Counter(row["repository"] for row in goldens).values(), default=0
        )
        <= 4,
        "identification_100pct": all(
            len(row["identification"]["distinguishing_terms"]) >= args.min_distinguishing
            and row["identification"]["narrowed_event_count"] <= args.narrow_max
            for row in goldens
        ),
        "mean_overlap_le_0_25": bool(overlaps) and sum(overlaps) / len(overlaps) <= 0.25,
        "max_overlap_le_0_60": bool(overlaps) and max(overlaps) <= 0.60,
        "adjudicated_100pct": all(row["adjudication"]["target_identified"] for row in goldens),
        "with_distractors_ge_50pct": bool(count) and with_distractors / count >= 0.50,
        "distractor_also_answers_zero": all(
            not d["also_answers"] for row in goldens for d in row["adjudication"]["distractors"]
        ),
        "max_skeleton_share_le_3pct": bool(count)
        and (max(skeleton_counts.values()) / count) <= 0.03,
        "distinct_skeleton_ratio_ge_0_80": bool(count)
        and len(skeleton_counts) / count >= 0.80,
        "accept_rate_ge_0_40": bool(attempted) and count / attempted >= 0.40,
        "spotcheck_15_emitted": len(spotcheck_ids) == SPOTCHECK_N,
    }
    return {
        "schema": "memphant.eval.track-r-golden.v1",
        "bar_doc": BAR_DOC,
        "golden_path": GOLDEN_PATH.relative_to(gc.MEMPHANT_ROOT).as_posix(),
        "sha256": gc.sha256_hex(golden_bytes),
        "bytes": len(golden_bytes),
        "count": count,
        "strata": dict(sorted(strata.items())),
        "params": {
            "sample_seed": args.seed,
            "target": args.target,
            "shape_target": args.shape_target,
            "candidates_per_shape": args.candidates_per_shape,
            "df_max": args.df_max,
            "narrow_max": args.narrow_max,
            "min_distinguishing": args.min_distinguishing,
            "skeleton_cap": args.skeleton_cap,
            "per_attempt_cap": args.per_attempt_cap,
            "per_repo_cap": args.per_repo_cap,
            "min_span_chars": args.min_span_chars,
            "max_span_chars": args.max_span_chars,
            "max_overlap": args.max_overlap,
            "too_generic_threshold": args.too_generic_threshold,
            "distractor_max": args.distractor_max,
            "event_min_chars": args.event_min_chars,
            "generator": "subscription_agent",
            "adjudicator": "subscription_agent",
            "paid_api_spend_usd": 0,
        },
        "corpus": {
            "dataset": corpus_lock["extraction"]["source_dataset"],
            "revision": corpus_lock["extraction"]["source_revision"],
            "license": corpus_lock["extraction"]["source_license"],
            "source_url": corpus_lock["extraction"]["source_url"],
            "classification": corpus_lock["extraction"]["classification"],
            "transform": corpus_lock["extraction"]["transform"],
            "corpus_sha256": corpus_lock["extraction"]["corpus_sha256"],
            "sampled_attempts": corpus_lock["extraction"]["sampled_attempts"],
            "emitted_events": corpus_lock["extraction"]["emitted_events"],
            "materializer_sha256": corpus_lock["extraction"]["materializer_sha256"],
        },
        "stats": {
            "generation_calls_attempted": attempted,
            "accepted": count,
            "accept_rate": round(count / attempted, 4) if attempted else None,
            "rejects": {reason: rejects.get(reason, 0) for reason in REJECT_REASONS},
            "reject_total": sum(rejects.values()),
            "cache_hits": cache.hits,
            "distinct_attempts": len({row["provenance"][0]["attempt_id"] for row in goldens}),
            "distinct_repositories": len({row["repository"] for row in goldens}),
            "distinct_skeletons": len(skeleton_counts),
            "max_skeleton_count": max(skeleton_counts.values(), default=0),
            "max_skeleton_share": round(max(skeleton_counts.values(), default=0) / count, 4)
            if count
            else None,
            "mean_lexical_overlap": round(sum(overlaps) / len(overlaps), 4) if overlaps else None,
            "max_lexical_overlap": max(overlaps, default=None),
            "goldens_with_adjudicated_distractors": with_distractors,
            "distractors_adjudicated_total": sum(
                row["adjudication"]["distractors_considered"] for row in goldens
            ),
        },
        "spot_check": {
            "path": SPOTCHECK_PATH.relative_to(gc.MEMPHANT_ROOT).as_posix(),
            "gitignored": True,
            "sample_size": len(spotcheck_ids),
            "sample_seed": args.seed,
            "question_ids": spotcheck_ids,
            "state": "emitted_pending_owner_review",
        },
        "bar_checks": checks,
        "bar_passed": all(checks.values()),
    }


def load_corpus_lock() -> dict:
    return json.loads(CORPUS_LOCK_PATH.read_text())


def prepare(args) -> tuple[Miner, list[dict], dict]:
    rows = gc.load_goldens(Path(args.corpus))
    df = attempt_document_frequency(rows)
    pool: list[dict] = []
    for row in rows:
        pool.extend(shape_candidates_for_attempt(row, args.event_min_chars))
    candidates = draw_candidates(pool, args.seed, args.candidates_per_shape)
    miner = Miner(args, rows, df)
    watched: set[str] = set()
    for candidate in candidates:
        watched |= {
            token
            for token in tokens(candidate["text"])
            if df.get(token, 0) <= args.df_max and len(token) >= 4
        }
    miner.narrowing = build_narrowing_index(rows, watched)
    print(
        f"corpus attempts={len(rows)} candidate_pool={len(pool)} "
        f"candidates_drawn={len(candidates)} watched_rare_tokens={len(watched)}",
        file=sys.stderr,
    )
    return miner, candidates, load_corpus_lock()


def add_args(parser) -> None:
    parser.add_argument("--corpus", default=str(CORPUS_PATH))
    parser.add_argument("--stage", choices=("mine",), default="mine")
    parser.add_argument("--verify-lock", action="store_true")
    parser.add_argument("--seed", type=int, default=SAMPLE_SEED)
    parser.add_argument("--target", type=int, default=TARGET_TOTAL)
    parser.add_argument("--shape-target", type=int, default=SHAPE_TARGET)
    parser.add_argument("--candidates-per-shape", type=int, default=CANDIDATES_PER_SHAPE)
    parser.add_argument("--df-max", type=int, default=DF_MAX)
    parser.add_argument("--narrow-max", type=int, default=NARROW_MAX)
    parser.add_argument("--min-distinguishing", type=int, default=MIN_DISTINGUISHING)
    parser.add_argument("--skeleton-cap", type=int, default=SKELETON_CAP)
    parser.add_argument("--per-attempt-cap", type=int, default=PER_ATTEMPT_CAP)
    parser.add_argument("--per-repo-cap", type=int, default=PER_REPO_CAP)
    parser.add_argument("--min-span-chars", type=int, default=MIN_SPAN_CHARS)
    parser.add_argument("--max-span-chars", type=int, default=MAX_SPAN_CHARS)
    parser.add_argument("--max-overlap", type=float, default=MAX_OVERLAP)
    parser.add_argument("--too-generic-threshold", type=int, default=TOO_GENERIC_THRESHOLD)
    parser.add_argument("--distractor-max", type=int, default=DISTRACTOR_MAX)
    parser.add_argument("--event-min-chars", type=int, default=EVENT_MIN_CHARS)
    parser.add_argument("--prefetch", type=int, default=PREFETCH)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_args(parser)
    args = parser.parse_args()

    if not Path(args.corpus).exists():
        print(
            f"corpus not found: {args.corpus} (run materialize_public_code_lane.py)",
            file=sys.stderr,
        )
        return 1

    miner, candidates, corpus_lock = prepare(args)
    goldens, rejects, attempted, consumed = miner.run(candidates)
    pending_before_prefetch = len(miner.cache.pending)

    if pending_before_prefetch:
        miner.prefetch(candidates, consumed, args.prefetch)
        written = emit_requests(miner.cache, REQUEST_DIR)
        print(
            f"PENDING agent calls: {len(miner.cache.pending)} "
            f"(blocking={pending_before_prefetch}) accepted_so_far={len(goldens)} "
            f"attempted={attempted} rejects={dict(rejects)}\n"
            f"request batches written to {REQUEST_DIR}: {len(written)}",
            file=sys.stderr,
        )
        return 2

    if args.verify_lock:
        lock_path = GOLDEN_PATH.with_name(GOLDEN_PATH.stem + ".lock.json")
        recorded = json.loads(lock_path.read_text())
        body = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in goldens)
        actual = gc.sha256_hex(body.encode())
        ok = actual == recorded["sha256"] and len(goldens) == recorded["count"]
        print(
            f"determinism check: recorded={recorded['sha256'][:12]} "
            f"remined={actual[:12]} count={len(goldens)}/{recorded['count']} "
            f"{'OK' if ok else 'MISMATCH'}",
            file=sys.stderr,
        )
        return 0 if ok else 1

    gc.write_jsonl(GOLDEN_PATH, goldens)
    rng = random.Random(args.seed)
    spotcheck = sorted(rng.sample(goldens, min(SPOTCHECK_N, len(goldens))), key=lambda r: r["question_id"])
    gc.write_jsonl(SPOTCHECK_PATH, spotcheck)
    lock = build_lock(
        args,
        goldens,
        rejects,
        attempted,
        corpus_lock,
        miner.cache,
        [row["question_id"] for row in spotcheck],
    )
    lock_path = GOLDEN_PATH.with_name(GOLDEN_PATH.stem + ".lock.json")
    lock_path.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")

    failed = [name for name, ok in lock["bar_checks"].items() if not ok]
    print(
        f"mined={len(goldens)} attempted={attempted} "
        f"accept_rate={lock['stats']['accept_rate']} strata={lock['strata']} "
        f"rejects={dict(rejects)} sha256={lock['sha256'][:12]}",
        file=sys.stderr,
    )
    print(
        f"BAR {'PASSED' if lock['bar_passed'] else 'FAILED'}"
        + (f" failing={failed}" if failed else ""),
        file=sys.stderr,
    )
    return 0 if lock["bar_passed"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
