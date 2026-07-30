#!/usr/bin/env python3
"""Pure span/candidate helpers shared by the code-lane golden miners.

Originally the R0-T6 miner CLI for the private ``coding_events_*`` corpus. That
CLI is gone: its corpus is gitignored and absent, its output bank was rejected
wholesale (generic templates, unadjudicated distractors —
``docs/build-log/artifacts/c3-public-code-lane-v3/rejection-receipt.json``), and
golden mining is now done by ``scripts/track_r_mine.py`` against the public
pinned nebius corpus with causal identification and adjudicated distractors. The
CLI was also dead as written: it called ``gate_mine_goldens.MinerCli``, which
does not exist, so it raised ``AttributeError`` before issuing any model call.

What survives is the pure, deterministic half — no I/O, no provider call, no
model — imported by ``scripts/track_r_mine.py`` (``lexical_overlap``,
``locate_span_in_event``, ``too_generic``) and covered by
``tests/test_code_lane_mine.py``.

``benchmarks/data/coding_events_golden.lock.json`` stays committed as the
historical record of that bank; ``tests/test_code_lane_gate_contract.py`` still
asserts its shape. It was already unregenerable before this change (absent
corpus, raising CLI), so nothing regenerable was lost.
"""

from __future__ import annotations

import random
import re

WORD_RE = re.compile(r"[a-z0-9]+")

# --- pure functions (TDD'd in tests/test_code_lane_mine.py) -----------------


def locate_span_in_event(event_text: str, span: str) -> tuple[int, int, str] | None:
    """Locate ``span`` in ``event_text``: verbatim first, then a
    whitespace-normalized fallback (the model may reflow whitespace across an
    embedded newline). Returns ``(start, end, exact_text)`` where
    ``event_text[start:end] == exact_text`` holds by construction — the
    canonical span recorded in the golden. ``None`` if unlocatable (never
    fabricated)."""
    idx = event_text.find(span)
    if idx != -1:
        return idx, idx + len(span), span
    span_norm = re.sub(r"\s+", " ", span.strip())
    if not span_norm:
        return None
    pattern = re.compile(r"\s+".join(re.escape(tok) for tok in span_norm.split(" ")))
    match = pattern.search(event_text)
    if match:
        return match.start(), match.end(), match.group(0)
    return None


def too_generic(span: str, corpus_index: dict[str, str], threshold: int) -> bool:
    """True when ``span`` (verbatim substring) appears in MORE than
    ``threshold`` distinct attempts' concatenated text in ``corpus_index``
    (``attempt_id -> full text``) — too generic a fact to be a good
    single-hop provenance probe."""
    count = sum(1 for text in corpus_index.values() if span in text)
    return count > threshold


def build_candidate_pool(corpus_rows: list[dict], min_chars: int) -> list[dict]:
    """Flattens the corpus into individual content-event candidates
    (``{attempt_id, sequence, role, text, event_id}``) substantial enough to
    mine a verbatim-span question from (``>= min_chars``)."""
    pool: list[dict] = []
    for row in corpus_rows:
        for event in row["events"]:
            if len(event["text"]) < min_chars:
                continue
            pool.append(
                {
                    "attempt_id": row["attempt_id"],
                    "sequence": event["sequence"],
                    "role": event["role"],
                    "text": event["text"],
                    "event_id": event["event_id"],
                }
            )
    return pool


def candidate_key(candidate: dict) -> str:
    return f"{candidate['attempt_id']}::{candidate['sequence']}"


def stratified_candidates(pool: list[dict], seed: int, want: int) -> list[dict]:
    """Round-robins across role buckets (sorted by candidate key, then
    seeded-shuffled within each bucket — deterministic given ``seed``) until
    ``want`` candidates are collected or the pool is exhausted. Mirrors the
    docs miner's bucket-round-robin sampling, with event role standing in for
    doc-directory bucket."""
    rng = random.Random(seed)
    by_role: dict[str, list[dict]] = {}
    for candidate in pool:
        by_role.setdefault(candidate["role"], []).append(candidate)
    for role in by_role:
        by_role[role].sort(key=candidate_key)
        rng.shuffle(by_role[role])
    roles_sorted = sorted(by_role)
    cursors = {role: 0 for role in roles_sorted}
    out: list[dict] = []
    while len(out) < want and any(cursors[r] < len(by_role[r]) for r in roles_sorted):
        for role in roles_sorted:
            if cursors[role] < len(by_role[role]):
                out.append(by_role[role][cursors[role]])
                cursors[role] += 1
                if len(out) >= want:
                    break
    return out


def build_context_preview(
    events: list[dict], target_index: int, max_events: int, max_chars_each: int
) -> str:
    """A short "role: clipped-text" preview of up to ``max_events``
    immediately-preceding events in the SAME attempt, for continuity flavor
    in the generator prompt. Never includes the target event or anything
    after it (the model must not lift the answer span from context)."""
    start = max(0, target_index - max_events)
    preceding = events[start:target_index]
    lines = []
    for event in preceding:
        clipped = event["text"][:max_chars_each]
        lines.append(f"{event['role']}: {clipped}")
    return "\n\n".join(lines)


def content_words(text: str) -> set[str]:
    return {w for w in WORD_RE.findall(text.lower()) if len(w) > 2}


def lexical_overlap(question: str, span: str) -> float:
    q = content_words(question)
    s = content_words(span)
    if not q or not s:
        return 0.0
    return len(q & s) / len(q | s)
