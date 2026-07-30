#!/usr/bin/env python3
"""Phase 1c: compare BM25 and MemPhant on the SAME haystack and the SAME stage.

The three executed Phase 1b/1c arms compared two different constructs. BM25's
``hit_at_10`` was a plain ranked top-10 over all 64,055 corpus events; MemPhant's
``hit_at_10`` was over the 10 items that survived packing, drawn from a candidate
pool confined to one coding attempt. Two asymmetries, pointing opposite ways.

This script removes both, using only executed-arm provenance:

* **Same stage.** MemPhant's ranked top-k is read off ``gold_fused_rank`` (the
  best-ranked gold-bearing pool unit under the same ``gate_common.contains_gold``
  matcher the graders use). Every Track R golden carries exactly ONE required
  span, so ``gold_fused_rank <= k`` is exactly ``provenance_hit`` over the fused
  top-k; the script asserts that precondition rather than assuming it.
* **Same haystack.** The BM25 arm is re-run with ``--scope attempt``, which
  restricts its haystack to the attempt MemPhant's recall is bound to. The
  scoping is verified empirically here, per question, against MemPhant's own
  ``pool_size`` and the corpus attempt sizes — not asserted from a flag.

No kill-gate verdict, no bar amendment, no reader, no paid call.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import code_lane_run_memphant as memphant_runner  # noqa: E402
import gate_common as gc  # noqa: E402


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def rate(rows: list[dict], predicate) -> float | None:
    return (sum(bool(predicate(row)) for row in rows) / len(rows)) if rows else None


def recall_block(rows: list[dict], predicate5, predicate10) -> dict:
    return {
        "n": len(rows),
        "recall_at_5": rate(rows, predicate5),
        "recall_at_10": rate(rows, predicate10),
    }


def split_blocks(rows: list[dict], key, predicate5, predicate10) -> dict:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(key(row), []).append(row)
    return {
        name: recall_block(groups[name], predicate5, predicate10)
        for name in sorted(groups)
    }


def scoping_witness(
    goldens: list[dict], corpus_rows: list[dict], bm25: dict, memphant: dict
) -> dict:
    """Empirical proof the scoped BM25 haystack is MemPhant's haystack.

    For each question: MemPhant's ``pool_size`` must be a subset-size of the
    bound attempt (MemPhant additionally lexically prefilters its pool), and the
    scoped BM25 ``documents_searched`` must equal that attempt's event count.
    """
    events_by_attempt = {row["attempt_id"]: len(row["events"]) for row in corpus_rows}
    unique_bodies_by_attempt = {
        row["attempt_id"]: len(
            {
                memphant_runner.contextual_event_body(row["events"], index)
                for index in range(len(row["events"]))
            }
        )
        for row in corpus_rows
    }
    bm25_rows = {row["question_id"]: row for row in bm25["per_question"]}
    memphant_rows = {row["question_id"]: row for row in memphant["per_question"]}
    matches_attempt_events = 0
    pool_within_attempt = 0
    pool_leq_scoped_documents = 0
    ratios: list[float] = []
    violations: list[dict] = []
    for golden in goldens:
        question_id = golden["question_id"]
        attempt_id = golden["provenance"][0]["attempt_id"]
        scoped = bm25_rows[question_id]["documents_searched"]
        pool = memphant_rows[question_id]["pool_size"]
        if scoped == events_by_attempt[attempt_id]:
            matches_attempt_events += 1
        else:
            violations.append(
                {
                    "question_id": question_id,
                    "documents_searched": scoped,
                    "attempt_events": events_by_attempt[attempt_id],
                }
            )
        if pool <= unique_bodies_by_attempt[attempt_id]:
            pool_within_attempt += 1
        if pool <= scoped:
            pool_leq_scoped_documents += 1
        if scoped:
            ratios.append(pool / scoped)
    return {
        "n": len(goldens),
        "memphant_scoping_rule": (
            "recall is bound per attempt: bind_attempt_context() binds "
            "scope_ref/actor_ref/agent_node_ref = code-lane:*:{attempt_id} and the "
            "evaluation recalls through evaluation_contexts[golden.provenance[0]"
            ".attempt_id], so the candidate pool cannot leave that one attempt"
        ),
        "bm25_documents_searched_equals_attempt_events": matches_attempt_events,
        "memphant_pool_size_within_attempt_unique_bodies": pool_within_attempt,
        "memphant_pool_size_le_bm25_documents_searched": pool_leq_scoped_documents,
        "pool_over_scoped_documents_ratio": {
            "min": min(ratios),
            "max": max(ratios),
            "mean": sum(ratios) / len(ratios),
        },
        "violations": violations,
        "reading": (
            "scoped BM25 ranks the whole bound attempt; MemPhant ranks a lexically "
            "prefiltered subset of the same attempt, so the scoped control is if "
            "anything given the larger of the two haystacks"
        ),
    }


def paired(rows: list[dict], left, right) -> dict:
    both = sum(1 for row in rows if left(row) and right(row))
    left_only = sum(1 for row in rows if left(row) and not right(row))
    right_only = sum(1 for row in rows if right(row) and not left(row))
    return {
        "both_hit": both,
        "left_only": left_only,
        "right_only": right_only,
        "neither": len(rows) - both - left_only - right_only,
        "discordant": left_only + right_only,
    }


def build(
    golden_path: Path,
    corpus_path: Path,
    bm25_corpus: dict,
    bm25_attempt: dict,
    memphant: dict,
) -> dict:
    lock = json.loads(memphant_runner.golden_lock_path(golden_path).read_text())
    corpus_rows, goldens = memphant_runner.verify_input_contract(
        corpus_path, golden_path, lock
    )
    if any(len(gc.required_spans(golden)) != 1 for golden in goldens):
        raise RuntimeError(
            "fused-rank -> provenance_hit equivalence needs single-span goldens"
        )
    if bm25_attempt["scope"] != "attempt" or bm25_corpus["scope"] != "corpus":
        raise RuntimeError("arm scopes are not the ones this comparison names")
    for report in (bm25_corpus, bm25_attempt):
        if report["golden_sha256"] != memphant["golden_sha256"]:
            raise RuntimeError("arms did not run on the same golden bank")
        if report["corpus_sha256"] != memphant["corpus_sha256"]:
            raise RuntimeError("arms did not run on the same corpus")

    flags = {
        golden["question_id"]: bool((golden.get("adjudication") or {}).get("distractors"))
        for golden in goldens
    }
    shapes = {golden["question_id"]: golden["question_type"] for golden in goldens}
    memphant_rows = {row["question_id"]: row for row in memphant["per_question"]}
    rows = []
    for row in bm25_attempt["per_question"]:
        question_id = row["question_id"]
        fused_rank = memphant_rows[question_id]["gold_fused_rank"]
        rows.append(
            {
                "question_id": question_id,
                "shape": shapes[question_id],
                "has_adjudicated_distractor": flags[question_id],
                "bm25_scoped_gold_rank": row["gold_rank"],
                "bm25_scoped_hit_at_5": bool(row["hit_at_5"]),
                "bm25_scoped_hit_at_10": bool(row["hit_at_10"]),
                "memphant_gold_fused_rank": fused_rank,
                "memphant_fused_hit_at_5": fused_rank is not None and fused_rank <= 5,
                "memphant_fused_hit_at_10": fused_rank is not None and fused_rank <= 10,
                "memphant_packed_hit_at_10": bool(memphant_rows[question_id]["hit_at_10"]),
            }
        )

    def bm5(row):
        return row["bm25_scoped_hit_at_5"]

    def bm10(row):
        return row["bm25_scoped_hit_at_10"]

    def mp5(row):
        return row["memphant_fused_hit_at_5"]

    def mp10(row):
        return row["memphant_fused_hit_at_10"]

    with_distractor = [row for row in rows if row["has_adjudicated_distractor"]]
    without = [row for row in rows if not row["has_adjudicated_distractor"]]
    return {
        "schema": "memphant.eval.track-r-phase1c-scoped-bm25.v1",
        "golden_path": str(golden_path),
        "golden_sha256": memphant["golden_sha256"],
        "corpus_sha256": memphant["corpus_sha256"],
        "paid_api_spend_usd": 0,
        "gold_predicate": {
            "bm25_arm": "gate_common.provenance_hit",
            "memphant_arm": "gate_common.provenance_hit",
            "memphant_fused_stage": "gate_common.contains_gold (via gold_bearing_units)",
            "identical": True,
            "note": (
                "both runners grade with gate_common.provenance_hit over the returned "
                "bodies; the fused-stage rank uses gate_common.contains_gold, the same "
                "matcher provenance_hit calls per span, and all 180 goldens are "
                "single-span, so gold_fused_rank <= k == provenance_hit at k"
            ),
        },
        "query_string": {
            "bm25_arm": "code_lane_run_memphant.retrieval_query(golden)",
            "memphant_arm": "code_lane_run_memphant.retrieval_query(golden)",
            "identical": True,
            "bank_has_retrieval_query_field": any(
                "retrieval_query" in golden for golden in goldens
            ),
        },
        "scoping_witness": scoping_witness(goldens, corpus_rows, bm25_attempt, memphant),
        "arms": {
            "bm25_corpus_scope": {
                "haystack": "all corpus events",
                "document_count": bm25_corpus["document_count"],
                "n": bm25_corpus["golden_count"],
                "recall_at_5": bm25_corpus["recall_at_5"],
                "recall_at_10": bm25_corpus["recall_at_10"],
            },
            "bm25_attempt_scope": {
                "haystack": "events of the bound attempt only",
                "corpus_document_count": bm25_attempt["document_count"],
                "documents_searched": {
                    "min": min(
                        row["documents_searched"] for row in bm25_attempt["per_question"]
                    ),
                    "median": sorted(
                        row["documents_searched"] for row in bm25_attempt["per_question"]
                    )[len(bm25_attempt["per_question"]) // 2],
                    "max": max(
                        row["documents_searched"] for row in bm25_attempt["per_question"]
                    ),
                },
                **recall_block(rows, bm5, bm10),
                "with_adjudicated_distractor": recall_block(with_distractor, bm5, bm10),
                "without_adjudicated_distractor": recall_block(without, bm5, bm10),
                "by_shape": split_blocks(rows, lambda row: row["shape"], bm5, bm10),
            },
            "memphant_fused_rank": {
                "haystack": "lexically prefiltered candidate pool of the bound attempt",
                **recall_block(rows, mp5, mp10),
                "with_adjudicated_distractor": recall_block(with_distractor, mp5, mp10),
                "without_adjudicated_distractor": recall_block(without, mp5, mp10),
                "by_shape": split_blocks(rows, lambda row: row["shape"], mp5, mp10),
            },
        },
        "paired_same_haystack_same_stage": {
            "left": "memphant_fused_hit_at_10",
            "right": "bm25_scoped_hit_at_10",
            "at_10": paired(rows, mp10, bm10),
            "at_5": paired(rows, mp5, bm5),
            "by_shape_at_10": {
                shape: paired([row for row in rows if row["shape"] == shape], mp10, bm10)
                for shape in sorted({row["shape"] for row in rows})
            },
            "with_adjudicated_distractor_at_10": paired(with_distractor, mp10, bm10),
            "without_adjudicated_distractor_at_10": paired(without, mp10, bm10),
        },
        "paired_reference_memphant_packed_vs_scoped_bm25_at_10": paired(
            rows, lambda row: row["memphant_packed_hit_at_10"], bm10
        ),
        "per_question": rows,
        "note": (
            "Measurement only. The kill gate, the ownership question and the golden "
            "bar remain owner decisions."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", required=True, type=Path)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--bm25-corpus-scope", required=True, type=Path)
    parser.add_argument("--bm25-attempt-scope", required=True, type=Path)
    parser.add_argument("--memphant", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    summary = build(
        args.golden,
        args.corpus,
        json.loads(args.bm25_corpus_scope.read_text()),
        json.loads(args.bm25_attempt_scope.read_text()),
        json.loads(args.memphant.read_text()),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    printable = {key: value for key, value in summary.items() if key != "per_question"}
    print(json.dumps(printable, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
