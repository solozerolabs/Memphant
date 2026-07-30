#!/usr/bin/env python3
"""Track R **paraphrase** variant miner (W0.1, instrument validity).

Mines a second, separate 150-200 golden bank from the SAME pinned CC-BY-4.0
corpus as ``scripts/track_r_mine.py`` (``c008142e…``), across the SAME three
shapes, differing in exactly one thing: **where the identifying information
lives**.

The original bank established identification by requiring >=2 rare target tokens
to be present *in the question*. That is the lexical give-away the program spec's
§1 measured at 0.396 question->target coverage against a 0.1008 non-target floor.
Here the identifiers are **withheld from the question** and preserved only in the
adjudication record, and identification is established semantically instead:

1. **withholding (mechanical, hard reject)** — the question may contain **no**
   rare token of the target (length >=4, attempt-level corpus document frequency
   <= ``--df-max``), and no file path / dotted / snake_case / CamelCase token of
   the target, and no >=4-token contiguous run of the answer span. The rare
   target tokens are recorded on the golden as ``identification.withheld_terms``.
2. **semantic identification (adjudicated, hard reject)** — the adjudicator must
   affirm both ``target_identified`` and ``uniquely_identified_within_attempt``
   reading the question alone.
3. **adversarial distractors (adjudicated, hard reject)** — the distractor set is
   the top ``--distractor-max`` NON-target events of the same attempt as ranked
   by the deterministic BM25 control (``code_lane_run_deterministic.tokens`` +
   the same scoring formula) against the question. That is literally what a
   retrieval system returns for this question. Any ``also_answers: true``
   rejects the golden. In an attempt-scoped haystack this set is never empty, so
   the bar requires 100% distractor coverage rather than the original bank's 50%.

Everything else is reused verbatim from ``track_r_mine``: the candidate draw, the
skeleton anti-template gate, the verbatim-span/too-generic/overlap answer gates,
the seeded round-robin, and the content-hash agent-reply cache. Generation and
adjudication run on **subscription-model agent calls** (marginal $0), never a
paid API.

Preregistered bar: ``docs/build-log/2026-07-31-track-r-paraphrase-bar.md``.

    python3 scripts/track_r_paraphrase_mine.py --stage mine     # -> exit 2 while pending
    # ... agent fulfills docs/build-log/artifacts/track-r-paraphrase/requests/*.json ...
    python3 scripts/track_r_paraphrase_mine.py --stage mine     # repeat until exit 0
    python3 scripts/track_r_paraphrase_mine.py --verify-lock

Outputs: ``benchmarks/data/track_r_paraphrase_golden.jsonl`` (gitignored),
``..._spotcheck.jsonl`` (gitignored), and the ONE committed artifact
``benchmarks/data/track_r_paraphrase_golden.lock.json``.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate_common as gc  # noqa: E402
import gate_mine_goldens as gm  # noqa: E402
import track_r_leakage as leak  # noqa: E402
import track_r_mine as tr  # noqa: E402
from code_lane_mine import lexical_overlap, locate_span_in_event, too_generic  # noqa: E402
from code_lane_run_deterministic import tokens as control_tokens  # noqa: E402

ART = gc.MEMPHANT_ROOT / "docs" / "build-log" / "artifacts" / "track-r-paraphrase"
CORPUS_PATH = gc.MEMPHANT_ROOT / "docs" / "build-log" / "artifacts" / "track-r" / "corpus.jsonl"
CORPUS_LOCK_PATH = (
    gc.MEMPHANT_ROOT / "docs" / "build-log" / "artifacts" / "track-r" / "corpus-adapter.lock.json"
)
CACHE_DIR = ART / "agent-cache"
REQUEST_DIR = ART / "requests"
GOLDEN_PATH = gc.MEMPHANT_ROOT / "benchmarks" / "data" / "track_r_paraphrase_golden.jsonl"
SPOTCHECK_PATH = gc.MEMPHANT_ROOT / "benchmarks" / "data" / "track_r_paraphrase_spotcheck.jsonl"
BAR_DOC = "docs/build-log/2026-07-31-track-r-paraphrase-bar.md"

SHAPES = tr.SHAPES
SAMPLE_SEED = 20260731
TARGET_TOTAL = 180
SHAPE_TARGET = 60
SHAPE_MIN = 40
CANDIDATES_PER_SHAPE = 420
DISTRACTOR_MAX = 5

# Preregistered leakage bars (docs/build-log/2026-07-31-track-r-paraphrase-bar.md §4.1)
MAX_CONCENTRATION = 1.50
MIN_EXCESS_REDUCTION = 0.75
MAX_MEAN_TARGET_COVERAGE = 0.25
MAX_PER_GOLDEN_COVERAGE = 0.60
ORIGINAL_TARGET_MEAN = 0.3960
ORIGINAL_FLOOR_MEAN = 0.1008

SNAKE_RE = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)+")
CAMEL_RE = re.compile(r"[A-Za-z]+[A-Z][A-Za-z0-9]*")
ANSWER_RUN = 4

REJECT_REASONS = (
    "parse_failed",
    "span_not_located",
    "span_length",
    "too_generic_span",
    "overlap_too_high",
    "answer_run_leaked",
    "identifier_leaked",
    "insufficient_withheld_terms",
    "generic_skeleton",
    "no_distractors",
    "per_attempt_cap",
    "per_repo_cap",
    "shape_target_met",
    "adjudication_parse_failed",
    "adjudication_target_not_identified",
    "adjudication_not_unique_in_attempt",
    "distractor_also_answers",
)


# --- pure helpers (unit-tested in tests/test_track_r_paraphrase_mine.py) -----


def identifier_forms(text: str) -> set[str]:
    """Lowercased literal identifier surfaces of an event: file paths, dotted
    identifiers, snake_case and CamelCase tokens. These are the surfaces a
    question is forbidden to copy, independent of how rare they are."""
    low = text.lower()
    out: set[str] = set()
    out |= {m.group(0) for m in tr.PATH_RE.finditer(low) if len(m.group(0)) > 6}
    out |= {m for m in tr.DOTTED_RE.findall(low) if len(m) > 6}
    out |= {m for m in SNAKE_RE.findall(low) if len(m) >= 5}
    out |= {m.group(0).lower() for m in CAMEL_RE.finditer(text) if len(m.group(0)) >= 5}
    return out


def leaked_identifiers(question: str, target_text: str) -> list[str]:
    low = question.lower()
    return sorted(form for form in identifier_forms(target_text) if form in low)


def answer_run_leaked(question: str, span: str, run: int = ANSWER_RUN) -> bool:
    """True if any ``run``-token contiguous run of the answer span appears in the
    question. Catches a question that quotes a fragment of its own answer."""
    span_tokens = control_tokens(span)
    if len(span_tokens) < run:
        return False
    question_tokens = control_tokens(question)
    for start in range(len(span_tokens) - run + 1):
        window = span_tokens[start : start + run]
        for at in range(len(question_tokens) - run + 1):
            if question_tokens[at : at + run] == window:
                return True
    return False


def bm25_rank_events(events: list[dict], query: str, exclude_sequence: int, k: int) -> list[int]:
    """The deterministic control's own ranking, restricted to non-target events of
    one attempt, returning event sequences. Same tokenizer, same k1=1.2/b=0.75
    formula as ``code_lane_run_deterministic.bm25_search``; that module returns
    bodies, and the distractor record needs refs."""
    documents = [
        (event["sequence"], control_tokens(event["text"]))
        for event in events
        if event["sequence"] != exclude_sequence
    ]
    query_terms = sorted(set(control_tokens(query)))
    if not documents or not query_terms or k <= 0:
        return []
    average_length = sum(len(t) for _, t in documents) / len(documents)
    document_frequency = {
        term: sum(term in set(t) for _, t in documents) for term in query_terms
    }
    scored: list[tuple[float, int]] = []
    for sequence, document in documents:
        frequencies = Counter(document)
        length = len(document)
        score = 0.0
        for term in query_terms:
            frequency = frequencies[term]
            if frequency == 0:
                continue
            df = document_frequency[term]
            idf = math.log(1 + (len(documents) - df + 0.5) / (df + 0.5))
            denominator = frequency + 1.2 * (1 - 0.75 + 0.75 * length / max(average_length, 1.0))
            score += idf * frequency * 2.2 / denominator
        if score > 0:
            scored.append((-score, sequence))
    scored.sort()
    return [sequence for _, sequence in scored[:k]]


# --- prompts ---------------------------------------------------------------


GEN_SYSTEM = {
    "state-churn": (
        "You author one repo-memory question over a slice of an autonomous AI "
        "coding agent's execution transcript. This question tests STATE CHURN: "
        "the focus file was touched more than once in this run, and the answer "
        "must come from the LATER state shown in the TARGET EVENT, so that an "
        "answer taken from the earlier touch would be wrong."
    ),
    "file-symbol-grounding": (
        "You author one repo-memory question over a slice of an autonomous AI "
        "coding agent's execution transcript. This question tests FILE/SYMBOL "
        "GROUNDING: it must ask about a concrete code fact tied to a particular "
        "file and a particular symbol appearing in the TARGET EVENT."
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
    " This is the PARAPHRASE variant of the bank, and its defining constraint is "
    "that the question must carry the SEMANTICS of identification without the "
    "TOKENS. Requirements, ALL mandatory.\n"
    "(1) VERBATIM ANSWER. The answer_span must be roughly 8 to 200 characters "
    "copied character-for-character from the TARGET EVENT text only — never "
    "paraphrased, never invented, never taken from the preceding context.\n"
    "(2) NO IDENTIFIERS IN THE QUESTION. The question must contain NO file path, "
    "no filename, no module name, no function/class/variable name, no exception "
    "or error class name, no config key, no test id, no command name, and no "
    "other literal identifier that appears anywhere in the transcript. Every "
    "token in the FORBIDDEN TOKENS list below is banned from the question, in "
    "any casing. Refer to those things by their ROLE instead: not "
    "'tqdm_logger.py' but 'the progress-bar logging helper'; not "
    "'test_retry_backoff' but 'the test covering retry backoff'; not "
    "'KeyError' but 'the lookup failure the run hit'.\n"
    "(3) STILL IDENTIFY THE MOMENT. Despite (2), an engineer who knows this run "
    "must be able to tell WHICH moment of it you are asking about. Do this with "
    "described specifics — what the agent had just tried, what changed, what "
    "broke, what stage the work was at — not with names. If your question would "
    "fit three different moments of this same run equally well, it is a FAILURE. "
    "If it would fit a different run in a different repository, it is a worse "
    "FAILURE.\n"
    "(4) DO NOT REVEAL THE ANSWER. Never quote the answer span or any fragment "
    "of it; share as few words with it as you can.\n"
    "(5) Never ask a meta question about the transcript, its format, or turn "
    "numbers. Do not begin every question the same way — vary the sentence "
    "form.\n"
    'Output ONLY a JSON object with keys "question" and "answer_span". No '
    "markdown, no code fence, no commentary."
)


def gen_system(shape: str) -> str:
    return GEN_SYSTEM[shape] + GEN_RULES


def forbidden_list(banned: list[str], limit: int = 200) -> str:
    return ", ".join(banned[:limit])


def gen_prompt(candidate: dict, preview: str, banned: list[str]) -> str:
    parts = [f"REPOSITORY: {candidate['repository']}"]
    if preview:
        parts.append(
            "PRECEDING CONTEXT (background only — the answer_span must NOT come "
            "from here):\n" + preview
        )
    parts.append(
        "TARGET EVENT (role: "
        f"{candidate['role']}, event {candidate['event_index']} of "
        f"{candidate['attempt_event_count']}) — the answer_span MUST be copied "
        "verbatim from HERE:\n" + candidate["text"][:tr.EVENT_MAX_CHARS_PROMPT]
    )
    parts.append(
        "FORBIDDEN TOKENS — none of these may appear in the question, in any "
        "casing, and neither may any file path or dotted/underscored/CamelCase "
        "identifier from the target event:\n" + forbidden_list(banned)
    )
    return "\n\n".join(parts)


ADJ_SYSTEM = (
    "You adjudicate one candidate benchmark question over an AI coding agent's "
    "execution transcript. The question was written under a constraint: it may "
    "not name any file, symbol, error class, or other identifier, so it "
    "identifies its moment by description alone. You are given the QUESTION, the "
    "CLAIMED ANSWER copied from the TARGET EVENT, the target event, and numbered "
    "CANDIDATE DISTRACTORS — the events of this SAME run that a lexical "
    "retrieval system actually ranks highest for this question.\n"
    "Decide three things.\n"
    "First, target_identified: reading the QUESTION ALONE, would a competent "
    "engineer familiar with this run know which specific moment of it is being "
    "asked about, and is the claimed answer genuinely responsive to it? A "
    "question that is merely on-topic is NOT identified.\n"
    "Second, uniquely_identified_within_attempt: is the target the ONLY event of "
    "this run that the question picks out? If two or more moments of this run "
    "answer it equally well, this is false. Be strict — this is the gate that "
    "replaces the identifier tokens the question is forbidden to use.\n"
    "Third, for EACH numbered distractor, whether that event ALSO answers the "
    "question: could a reader given only that event produce a defensible answer "
    "to the question as asked?\n"
    'Output ONLY a JSON object: {"target_identified": true|false, '
    '"uniquely_identified_within_attempt": true|false, "reason": "<one '
    'sentence>", "distractors": [{"index": <int>, "also_answers": true|false, '
    '"why": "<short>"}]}. Include exactly one entry per numbered distractor. No '
    "markdown, no code fence, no commentary."
)


def adj_prompt(question: str, answer: str, candidate: dict, distractors: list[dict]) -> str:
    parts = [
        f"QUESTION: {question}",
        f"CLAIMED ANSWER (verbatim from the target event): {answer}",
        f"TARGET EVENT (repository {candidate['repository']}, role "
        f"{candidate['role']}, event {candidate['event_index']} of "
        f"{candidate['attempt_event_count']}):\n"
        f"{candidate['text'][:tr.EVENT_MAX_CHARS_PROMPT]}",
    ]
    for number, event in enumerate(distractors, start=1):
        parts.append(
            f"CANDIDATE DISTRACTOR {number} (same run, event {event['sequence']}, "
            f"role {event['role']}):\n{event['text'][:tr.DISTRACTOR_EXCERPT_CHARS]}"
        )
    return "\n\n".join(parts)


def parse_adjudication(reply: str, n_distractors: int) -> dict | None:
    try:
        obj = json.loads(gm.strip_json(reply))
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    if not isinstance(obj.get("target_identified"), bool):
        return None
    if not isinstance(obj.get("uniquely_identified_within_attempt"), bool):
        return None
    entries = obj.get("distractors")
    if not isinstance(entries, list) or len(entries) != n_distractors:
        return None
    verdicts = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("also_answers"), bool):
            return None
        verdicts.append(
            {"also_answers": entry["also_answers"], "why": str(entry.get("why", ""))[:240]}
        )
    return {
        "target_identified": obj["target_identified"],
        "uniquely_identified_within_attempt": obj["uniquely_identified_within_attempt"],
        "reason": str(obj.get("reason", ""))[:240],
        "distractors": verdicts,
    }


# --- mining ----------------------------------------------------------------


class ParaphraseMiner:
    def __init__(self, args, rows: list[dict], df: Counter) -> None:
        self.args = args
        self.rows = rows
        self.df = df
        self.events_by_attempt = {row["attempt_id"]: row["events"] for row in rows}
        self.rows_by_attempt = {row["attempt_id"]: row for row in rows}
        self.corpus_index = {
            row["attempt_id"]: "\n\n".join(e["text"] for e in row["events"]) for row in rows
        }
        self.cache = tr.AgentCache(CACHE_DIR)

    def rare_target_terms(self, candidate: dict) -> list[str]:
        return sorted(
            token
            for token in tr.tokens(candidate["text"])
            if len(token) >= 4 and self.df.get(token, 0) <= self.args.df_max
        )

    def banned_tokens(self, candidate: dict, rare: list[str]) -> list[str]:
        forms = identifier_forms(candidate["text"])
        return sorted(set(rare) | forms)

    def mechanical(self, candidate: dict) -> tuple[dict | None, str | None, bool]:
        events = self.events_by_attempt[candidate["attempt_id"]]
        preview = tr.build_context_preview(events, candidate["event_index"])
        rare = self.rare_target_terms(candidate)
        banned = self.banned_tokens(candidate, rare)
        system = gen_system(candidate["shape"])
        prompt = gen_prompt(candidate, preview, banned)
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
        if answer_run_leaked(question, exact):
            return None, "answer_run_leaked", False
        asked = tr.tokens(question)
        leaked = sorted(set(rare) & asked) + leaked_identifiers(question, candidate["text"])
        if leaked:
            return None, "identifier_leaked", False
        if len(rare) < self.args.min_withheld:
            return None, "insufficient_withheld_terms", False
        distractor_sequences = bm25_rank_events(
            events, question, candidate["sequence"], self.args.distractor_max
        )
        if not distractor_sequences:
            return None, "no_distractors", False
        return (
            {
                "question": question,
                "span": exact,
                "char_start": start,
                "char_end": end,
                "overlap": overlap,
                "withheld": rare[: self.args.withheld_record_max],
                "withheld_count": len(rare),
                "distractor_sequences": distractor_sequences,
            },
            None,
            False,
        )

    def event_ref(self, attempt_id: str, sequence: int) -> dict:
        row = self.rows_by_attempt[attempt_id]
        event = next(e for e in row["events"] if e["sequence"] == sequence)
        return {
            "attempt_id": attempt_id,
            "repository": row["repository"],
            "sequence": sequence,
            "role": event["role"],
            "text": event["text"],
            "event_id": event["event_id"],
        }

    def adjudicate(self, candidate: dict, payload: dict) -> tuple[dict | None, str | None, bool]:
        distractors = [
            self.event_ref(candidate["attempt_id"], sequence)
            for sequence in payload["distractor_sequences"]
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
        if not verdict["uniquely_identified_within_attempt"]:
            return None, "adjudication_not_unique_in_attempt", False
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
                "uniquely_identified_within_attempt": True,
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
            skeleton_text = tr.skeleton(payload["question"])
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
            "question_id": f"track_r_par_{number:03d}",
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
                "mode": "withheld_semantic",
                "withheld_terms": payload["withheld"],
                "withheld_term_count": payload["withheld_count"],
                "leaked_terms": [],
                "lexical_overlap": round(payload["overlap"], 4),
            },
            "adjudication": {
                "adjudicator": "subscription_agent",
                "target_identified": True,
                "uniquely_identified_within_attempt": True,
                "reason": verdict["reason"],
                "distractor_selector": "bm25_control_top_k_same_attempt",
                "distractors_considered": len(verdict["distractors"]),
                "distractors": verdict["distractors"],
            },
            "question_skeleton": skeleton_text,
            "source_event_key": tr.candidate_key(candidate),
        }

    def prefetch(self, candidates: list[dict], start: int, count: int) -> None:
        for candidate in candidates[start : start + count]:
            payload, reason, pending = self.mechanical(candidate)
            if pending or reason is not None:
                continue
            self.adjudicate(candidate, payload)


# --- lock ------------------------------------------------------------------


def build_lock(args, goldens, rejects, attempted, corpus_lock, cache, spotcheck_ids, leakage):
    golden_bytes = GOLDEN_PATH.read_bytes()
    overlaps = [row["identification"]["lexical_overlap"] for row in goldens]
    skeleton_counts = Counter(row["question_skeleton"] for row in goldens)
    with_distractors = sum(
        1 for row in goldens if row["adjudication"]["distractors_considered"] > 0
    )
    strata = Counter(row["question_type"] for row in goldens)
    count = len(goldens)
    coverages = [row["target_coverage"] for row in leakage["per_question"]]
    concentration = leakage["concentration_vs_exhaustive"]
    excess_new = leakage["target"]["mean"] - leakage["non_target_exhaustive"]["mean"]
    excess_original = ORIGINAL_TARGET_MEAN - ORIGINAL_FLOOR_MEAN
    excess_reduction = 1.0 - (excess_new / excess_original) if excess_original else None
    checks = {
        # 4.1 leakage — the headline criterion
        "leak_concentration_le_1_50": concentration is not None
        and concentration <= MAX_CONCENTRATION,
        "leak_excess_reduction_ge_0_75": excess_reduction is not None
        and excess_reduction >= MIN_EXCESS_REDUCTION,
        "leak_mean_target_coverage_le_0_25": leakage["target"]["mean"]
        <= MAX_MEAN_TARGET_COVERAGE,
        "leak_max_target_coverage_le_0_60": bool(coverages)
        and max(coverages) <= MAX_PER_GOLDEN_COVERAGE,
        # 4.2 identifier withholding
        "withholding_zero_leaks": all(
            not row["identification"]["leaked_terms"] for row in goldens
        ),
        "withheld_terms_ge_2": all(
            row["identification"]["withheld_term_count"] >= args.min_withheld for row in goldens
        ),
        # 4.3 guard 1 — causal identification, semantic
        "adjudicated_target_identified_100pct": all(
            row["adjudication"]["target_identified"] for row in goldens
        ),
        "adjudicated_unique_in_attempt_100pct": all(
            row["adjudication"]["uniquely_identified_within_attempt"] for row in goldens
        ),
        # 4.3 guard 2 — distractors
        "with_distractors_100pct": bool(count) and with_distractors == count,
        "distractor_also_answers_zero": all(
            not d["also_answers"] for row in goldens for d in row["adjudication"]["distractors"]
        ),
        # 4.3 guard 3 — no templates, plus the answer-side gates
        "max_skeleton_share_le_3pct": bool(count)
        and (max(skeleton_counts.values(), default=0) / count) <= 0.03,
        "distinct_skeleton_ratio_ge_0_80": bool(count)
        and len(skeleton_counts) / count >= 0.80,
        "mean_overlap_le_0_25": bool(overlaps) and sum(overlaps) / len(overlaps) <= 0.25,
        "max_overlap_le_0_60": bool(overlaps) and max(overlaps) <= 0.60,
        # 4.4 size and composition
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
        # 4.5 spot-check
        "spotcheck_15_emitted": len(spotcheck_ids) == tr.SPOTCHECK_N,
        # 4.6 accept rate
        "accept_rate_ge_0_20": bool(attempted) and count / attempted >= 0.20,
    }
    return {
        "schema": "memphant.eval.track-r-paraphrase-golden.v1",
        "bar_doc": BAR_DOC,
        "variant_of": {
            "bank": "benchmarks/data/track_r_repo_memory_golden.jsonl",
            "bank_sha256": "6f549daaa3cc5be6dae095d044a50d17a8fd4ab82a23f2e973901cbb52a89b6d",
            "relationship": (
                "same pinned corpus, same three shapes, same candidate mechanism; "
                "identification moved from lexical tokens in the question to "
                "withheld terms plus adjudicated semantic uniqueness"
            ),
        },
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
            "min_withheld": args.min_withheld,
            "skeleton_cap": args.skeleton_cap,
            "per_attempt_cap": args.per_attempt_cap,
            "per_repo_cap": args.per_repo_cap,
            "min_span_chars": args.min_span_chars,
            "max_span_chars": args.max_span_chars,
            "max_overlap": args.max_overlap,
            "too_generic_threshold": args.too_generic_threshold,
            "distractor_max": args.distractor_max,
            "distractor_selector": "bm25_control_top_k_same_attempt",
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
        "leakage": {
            "metric_script": "scripts/track_r_leakage.py",
            "target": leakage["target"],
            "non_target_exhaustive": leakage["non_target_exhaustive"],
            "non_target_sampled": leakage["non_target_sampled"],
            "concentration_vs_exhaustive": concentration,
            "concentration_vs_sampled": leakage["concentration_vs_sampled"],
            "excess_over_floor": round(excess_new, 4),
            "excess_over_floor_reduction_vs_original": round(excess_reduction, 4)
            if excess_reduction is not None
            else None,
            "by_shape": leakage["by_shape"],
            "reference_original_bank": {
                "target_mean": ORIGINAL_TARGET_MEAN,
                "non_target_exhaustive_mean": ORIGINAL_FLOOR_MEAN,
                "concentration_vs_exhaustive": 3.9286,
                "concentration_vs_sampled": 4.1905,
                "artifact": "docs/build-log/artifacts/track-r/leakage-original.json",
            },
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
            "mean_withheld_terms": round(
                sum(row["identification"]["withheld_term_count"] for row in goldens) / count, 2
            )
            if count
            else None,
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


def prepare(args):
    rows = gc.load_goldens(Path(args.corpus))
    df = tr.attempt_document_frequency(rows)
    pool: list[dict] = []
    for row in rows:
        pool.extend(tr.shape_candidates_for_attempt(row, args.event_min_chars))
    candidates = tr.draw_candidates(pool, args.seed, args.candidates_per_shape)
    miner = ParaphraseMiner(args, rows, df)
    print(
        f"corpus attempts={len(rows)} candidate_pool={len(pool)} "
        f"candidates_drawn={len(candidates)}",
        file=sys.stderr,
    )
    return miner, candidates, json.loads(CORPUS_LOCK_PATH.read_text())


def add_args(parser) -> None:
    parser.add_argument("--corpus", default=str(CORPUS_PATH))
    parser.add_argument("--stage", choices=("mine",), default="mine")
    parser.add_argument("--verify-lock", action="store_true")
    parser.add_argument("--seed", type=int, default=SAMPLE_SEED)
    parser.add_argument("--target", type=int, default=TARGET_TOTAL)
    parser.add_argument("--shape-target", type=int, default=SHAPE_TARGET)
    parser.add_argument("--candidates-per-shape", type=int, default=CANDIDATES_PER_SHAPE)
    parser.add_argument("--df-max", type=int, default=tr.DF_MAX)
    parser.add_argument("--min-withheld", type=int, default=2)
    parser.add_argument("--withheld-record-max", type=int, default=40)
    parser.add_argument("--skeleton-cap", type=int, default=tr.SKELETON_CAP)
    parser.add_argument("--per-attempt-cap", type=int, default=tr.PER_ATTEMPT_CAP)
    parser.add_argument("--per-repo-cap", type=int, default=tr.PER_REPO_CAP)
    parser.add_argument("--min-span-chars", type=int, default=tr.MIN_SPAN_CHARS)
    parser.add_argument("--max-span-chars", type=int, default=tr.MAX_SPAN_CHARS)
    parser.add_argument("--max-overlap", type=float, default=tr.MAX_OVERLAP)
    parser.add_argument("--too-generic-threshold", type=int, default=tr.TOO_GENERIC_THRESHOLD)
    parser.add_argument("--distractor-max", type=int, default=DISTRACTOR_MAX)
    parser.add_argument("--event-min-chars", type=int, default=tr.EVENT_MIN_CHARS)
    parser.add_argument("--prefetch", type=int, default=240)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_args(parser)
    args = parser.parse_args()

    if not Path(args.corpus).exists():
        print(f"corpus not found: {args.corpus}", file=sys.stderr)
        return 1

    miner, candidates, corpus_lock = prepare(args)
    goldens, rejects, attempted, consumed = miner.run(candidates)
    pending_before_prefetch = len(miner.cache.pending)

    if pending_before_prefetch:
        miner.prefetch(candidates, consumed, args.prefetch)
        written = tr.emit_requests(miner.cache, REQUEST_DIR)
        for path in sorted(REQUEST_DIR.glob("*.json")):
            body = json.loads(path.read_text())
            body["reply_dir"] = str(CACHE_DIR)
            path.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n")
        print(
            f"PENDING agent calls: {len(miner.cache.pending)} "
            f"(blocking={pending_before_prefetch}) accepted_so_far={len(goldens)} "
            f"attempted={attempted} rejects={dict(rejects)}\n"
            f"request batches written to {REQUEST_DIR}: {len(written)}",
            file=sys.stderr,
        )
        return 2

    lock_path = GOLDEN_PATH.with_name(GOLDEN_PATH.stem + ".lock.json")
    if args.verify_lock:
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
    spotcheck = sorted(
        rng.sample(goldens, min(tr.SPOTCHECK_N, len(goldens))), key=lambda r: r["question_id"]
    )
    gc.write_jsonl(SPOTCHECK_PATH, spotcheck)
    leakage = leak.measure(goldens, miner.rows, leak.NON_TARGET_SEED)
    lock = build_lock(
        args,
        goldens,
        rejects,
        attempted,
        corpus_lock,
        miner.cache,
        [row["question_id"] for row in spotcheck],
        leakage,
    )
    lock_path.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")
    leakage["golden_path"] = GOLDEN_PATH.as_posix()
    leakage["golden_sha256"] = lock["sha256"]
    leakage["corpus_sha256"] = lock["corpus"]["corpus_sha256"]
    leak_out = ART / "leakage-paraphrase.json"
    leak_out.parent.mkdir(parents=True, exist_ok=True)
    leak_out.write_text(json.dumps(leakage, indent=2, sort_keys=True) + "\n")

    failed = [name for name, ok in lock["bar_checks"].items() if not ok]
    print(
        f"mined={len(goldens)} attempted={attempted} "
        f"accept_rate={lock['stats']['accept_rate']} strata={lock['strata']} "
        f"rejects={dict(rejects)} sha256={lock['sha256'][:12]}\n"
        f"leakage target_mean={leakage['target']['mean']} "
        f"floor_mean={leakage['non_target_exhaustive']['mean']} "
        f"concentration={leakage['concentration_vs_exhaustive']}",
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
