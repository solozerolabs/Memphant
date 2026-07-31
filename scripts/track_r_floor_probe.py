#!/usr/bin/env python3
"""Empirical floor probe: how low CAN question->target concentration go and still
leave an answerable, uniquely-identifying question? (FREE, W0.1 methodology.)

The paraphrase bar (``docs/build-log/2026-07-31-track-r-paraphrase-bar.md`` §4.1)
sets concentration <= 1.50 by argument. This measures the number instead.

There is a floor imposed by the task itself. A question that is genuinely *about*
an event shares domain vocabulary with it; concentration 1.0 would mean the
question's vocabulary is no more present in its own target than in an arbitrary
sibling event, i.e. the question carries no signal about which event answers it
and is unanswerable by any method — lexical or semantic. So the question is not
"can concentration reach 1.0" (it cannot, and should not) but "what is the lowest
concentration compatible with the question still being answerable and unique".

Method. For a seeded, shape-stratified sample of targets drawn from the SAME
candidate stream the paraphrase bank uses, ask the agent for the *most
aggressively abstracted* question it can write that a competent reader could
still answer from this event and no other — explicitly optimising for minimal
shared vocabulary rather than for naturalness. Then put that question through the
SAME uniqueness adjudication and the SAME BM25-nearest distractor set the bank
uses. Two figures come out:

* ``unconstrained`` — concentration over the whole sample, i.e. what you get when
  you push abstraction as hard as possible and do not care whether the question
  survives. This is a **lower bound that is not usable**: some of these questions
  are not answerable.
* ``answerable_unique`` — concentration over the subset the adjudicator certifies
  as uniquely identifying. **This is the floor estimate**: the lowest measured
  concentration at which the construct still works.

This is a rough instrument and is reported as one: a single generator, one
corpus, agent-judged uniqueness, and a small n. It bounds the achievable floor;
it does not pin it.

    python3 scripts/track_r_floor_probe.py --stage probe   # -> exit 2 while pending
    python3 scripts/track_r_floor_probe.py --report
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate_common as gc  # noqa: E402
import gate_mine_goldens as gm  # noqa: E402
import track_r_leakage as leak  # noqa: E402
import track_r_paraphrase_mine as pm  # noqa: E402

ART = gc.MEMPHANT_ROOT / "docs" / "build-log" / "artifacts" / "track-r-floor"
CACHE_DIR = ART / "agent-cache"
REQUEST_DIR = ART / "requests"
OUT_PATH = ART / "floor-probe.json"
PER_SHAPE = 12

FLOOR_SYSTEM = (
    "You are probing the limits of abstraction on a benchmark question, not "
    "authoring a natural one. You are given one event from an AI coding agent's "
    "execution transcript, plus a list of FORBIDDEN TOKENS.\n"
    "Write the MOST ABSTRACT question you can about a concrete fact recorded in "
    "this event, subject to one hard requirement: a competent engineer who has "
    "read this entire run must still be able to tell that THIS event, and no "
    "other event of the run, is the one being asked about, and must be able to "
    "answer it from this event.\n"
    "Optimise explicitly for MINIMAL SHARED VOCABULARY with the event. Deliberate "
    "techniques: replace every noun with a superordinate ('the file' not the "
    "filename, 'the failure' not the exception class, 'the helper' not the "
    "function); refer to things by their role in the run rather than their name; "
    "describe position in the run ('right after the first attempt to reproduce "
    "the report') rather than content; use pronouns and demonstratives where they "
    "remain unambiguous. Do NOT reuse the event's distinctive words when a duller "
    "word will do. Count the words you are borrowing and borrow fewer.\n"
    "Hard constraints, unchanged from the main bank: no token from the FORBIDDEN "
    "TOKENS list in any casing; no file path, dotted, snake_case or CamelCase "
    "identifier from the event; the answer_span must be 8-200 characters copied "
    "character-for-character from the event; never quote the answer or four "
    "consecutive words of it in the question.\n"
    "You are being measured on how FEW of the event's words your question "
    "contains, conditional on it remaining answerable and unique. A question that "
    "loses uniqueness scores nothing.\n"
    'Output ONLY a JSON object with keys "question" and "answer_span". No '
    "markdown, no code fence, no commentary."
)


def sample_targets(miner, candidates, per_shape: int) -> list[tuple[dict, dict]]:
    """Seeded, shape-stratified draw from the candidates whose paraphrase-bank
    generation is already cached AND mechanically passing. Reusing the bank's own
    stream keeps the target distribution identical, so the floor estimate is
    about the questions and not about a different slice of the corpus."""
    picked: dict[str, list] = {shape: [] for shape in pm.SHAPES}
    for candidate in candidates:
        shape = candidate["shape"]
        if len(picked[shape]) >= per_shape:
            if all(len(picked[s]) >= per_shape for s in pm.SHAPES):
                break
            continue
        payload, reason, pending = miner.mechanical(candidate)
        if pending or reason is not None:
            continue
        picked[shape].append((candidate, payload))
    return [pair for shape in pm.SHAPES for pair in picked[shape]]


def run(args) -> int:
    parser_args = argparse.Namespace(**vars(args))
    miner, candidates, _ = pm.prepare(parser_args)
    sample = sample_targets(miner, candidates, args.per_shape)
    floor_cache = pm.tr.AgentCache(CACHE_DIR)

    rows = []
    for candidate, bank_payload in sample:
        events = miner.events_by_attempt[candidate["attempt_id"]]
        rare = miner.rare_target_terms(candidate)
        banned = miner.banned_tokens(candidate, rare)
        prompt = pm.gen_prompt(candidate, "", banned)
        reply = floor_cache.get("floor-generate", FLOOR_SYSTEM, prompt)
        if reply is None:
            continue
        obj = gm.parse_reply(reply, ("question", "answer_span"))
        if obj is None:
            rows.append({"candidate": candidate, "status": "parse_failed"})
            continue
        question = obj["question"].strip()
        from code_lane_mine import locate_span_in_event  # noqa: PLC0415

        located = locate_span_in_event(candidate["text"], obj["answer_span"].strip())
        if located is None:
            rows.append({"candidate": candidate, "status": "span_not_located"})
            continue
        _, _, exact = located
        leaked = sorted(set(rare) & pm.tr.tokens(question)) + pm.leaked_identifiers(
            question, candidate["text"]
        )
        sequences = pm.bm25_rank_events(
            events, question, candidate["sequence"], args.distractor_max
        )
        distractors = [miner.event_ref(candidate["attempt_id"], s) for s in sequences]
        adj = pm.adj_prompt(question, exact, candidate, distractors)
        verdict_reply = floor_cache.get("floor-adjudicate", pm.ADJ_SYSTEM, adj)
        if verdict_reply is None:
            continue
        verdict = pm.parse_adjudication(verdict_reply, len(distractors))
        target = next(e for e in events if e["sequence"] == candidate["sequence"])
        others = [e for e in events if e["sequence"] != candidate["sequence"]]
        rows.append(
            {
                "candidate": candidate,
                "status": "measured",
                "question": question,
                "bank_question": bank_payload["question"],
                "leaked": leaked,
                "target_coverage": leak.coverage(question, target["text"]),
                "floor_coverage": statistics.fmean(
                    leak.coverage(question, e["text"]) for e in others
                ),
                "bank_target_coverage": leak.coverage(
                    bank_payload["question"], target["text"]
                ),
                "bank_floor_coverage": statistics.fmean(
                    leak.coverage(bank_payload["question"], e["text"]) for e in others
                ),
                "verdict": verdict,
            }
        )

    if floor_cache.pending:
        pm.tr.emit_requests(floor_cache, REQUEST_DIR)
        for path in sorted(REQUEST_DIR.glob("*.json")):
            body = json.loads(path.read_text())
            body["reply_dir"] = str(CACHE_DIR)
            path.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n")
        print(
            f"PENDING floor-probe calls: {len(floor_cache.pending)} "
            f"sample={len(sample)} measured={sum(1 for r in rows if r['status']=='measured')}\n"
            f"request batches written to {REQUEST_DIR}",
            file=sys.stderr,
        )
        return 2

    measured = [r for r in rows if r["status"] == "measured"]
    clean = [r for r in measured if not r["leaked"]]
    unique = [
        r
        for r in clean
        if r["verdict"]
        and r["verdict"]["target_identified"]
        and r["verdict"]["uniquely_identified_within_attempt"]
    ]

    def block(subset: list[dict], key_t: str, key_f: str) -> dict | None:
        if not subset:
            return None
        targets = [r[key_t] for r in subset]
        floors = [r[key_f] for r in subset]
        return {
            "n": len(subset),
            "target_mean": round(statistics.fmean(targets), 4),
            "target_median": round(statistics.median(targets), 4),
            "floor_mean": round(statistics.fmean(floors), 4),
            "concentration": round(statistics.fmean(targets) / statistics.fmean(floors), 4)
            if statistics.fmean(floors)
            else None,
        }

    report = {
        "schema": "memphant.eval.track-r-floor-probe.v1",
        "question": (
            "what is the lowest question->target concentration compatible with the "
            "question still being answerable and uniquely identifying"
        ),
        "method": (
            "seeded shape-stratified sample from the paraphrase bank's own candidate "
            "stream; the agent is asked for maximum abstraction subject to remaining "
            "answerable and unique; the SAME uniqueness adjudication and the SAME "
            "BM25-nearest distractor set as the bank"
        ),
        "paid_api_spend_usd": 0,
        "corpus_sha256": leak.gc.sha256_hex(Path(args.corpus).read_bytes()),
        "sample_size": len(sample),
        "measured": len(measured),
        "identifier_clean": len(clean),
        "answerable_unique": len(unique),
        "uniqueness_survival_rate": round(len(unique) / len(clean), 4) if clean else None,
        "unconstrained_max_abstraction": block(clean, "target_coverage", "floor_coverage"),
        "answerable_unique_floor": block(unique, "target_coverage", "floor_coverage"),
        "same_targets_bank_questions": block(
            clean, "bank_target_coverage", "bank_floor_coverage"
        ),
        "reference_original_bank": {
            "target_mean": pm.ORIGINAL_TARGET_MEAN,
            "floor_mean": pm.ORIGINAL_FLOOR_MEAN,
            "concentration": 3.9286,
        },
        "by_shape_answerable_unique": {
            shape: block(
                [r for r in unique if r["candidate"]["shape"] == shape],
                "target_coverage",
                "floor_coverage",
            )
            for shape in pm.SHAPES
        },
        "roughness": (
            "SMALL SAMPLE, SINGLE GENERATOR, AGENT-JUDGED UNIQUENESS. n per cell is "
            "in the tens, one model wrote every probe question, and 'uniquely "
            "identifying' is an agent verdict rather than a human one. Read this as "
            "a bound on the achievable floor, not a pinned value. It is recorded as "
            "a finding; it does not re-preregister any bar."
        ),
        "per_question": [
            {
                "shape": r["candidate"]["shape"],
                "attempt_id": r["candidate"]["attempt_id"],
                "sequence": r["candidate"]["sequence"],
                "status": r["status"],
                "leaked": r.get("leaked"),
                "target_coverage": round(r["target_coverage"], 4)
                if "target_coverage" in r
                else None,
                "floor_coverage": round(r["floor_coverage"], 4)
                if "floor_coverage" in r
                else None,
                "unique": bool(
                    r.get("verdict")
                    and r["verdict"]["target_identified"]
                    and r["verdict"]["uniquely_identified_within_attempt"]
                ),
            }
            for r in rows
        ],
        "reject_status_counts": dict(Counter(r["status"] for r in rows)),
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    printable = {k: v for k, v in report.items() if k != "per_question"}
    print(json.dumps(printable, indent=2, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    pm.add_args(parser)
    parser.add_argument("--per-shape", type=int, default=PER_SHAPE)
    args = parser.parse_args()
    args.prefetch = 0
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
