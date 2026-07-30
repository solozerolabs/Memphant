#!/usr/bin/env python3
"""Deterministic file/search control for the pinned internal coding corpus.

Each immutable source event is one search document. BM25 ranks raw event text
only; there is no generated summary, embedding, memory state, or outcome
feedback. This is the reproducible lexical analogue of searching transcript
files before introducing a memory substrate.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import code_lane_run_memphant as memphant_runner  # noqa: E402
import gate_common as gc  # noqa: E402

TOKEN_RE = re.compile(r"[a-z0-9_./-]+")


def tokens(value: str) -> list[str]:
    return TOKEN_RE.findall(value.lower())


def event_documents(corpus_rows: list[dict]) -> list[dict]:
    documents: list[dict] = []
    for row in corpus_rows:
        for event in row["events"]:
            documents.append(
                {
                    "attempt_id": row["attempt_id"],
                    "sequence": event["sequence"],
                    "body": event["text"],
                    "tokens": tokens(event["text"]),
                }
            )
    return documents


def bm25_search(documents: list[dict], query: str, k: int) -> list[str]:
    query_terms = sorted(set(tokens(query)))
    if not documents or not query_terms or k <= 0:
        return []
    average_length = sum(len(document["tokens"]) for document in documents) / len(documents)
    document_frequency = {
        term: sum(term in set(document["tokens"]) for document in documents)
        for term in query_terms
    }
    scored: list[tuple[float, str, int, str]] = []
    for document in documents:
        frequencies = Counter(document["tokens"])
        length = len(document["tokens"])
        score = 0.0
        for term in query_terms:
            frequency = frequencies[term]
            if frequency == 0:
                continue
            df = document_frequency[term]
            inverse_document_frequency = math.log(
                1 + (len(documents) - df + 0.5) / (df + 0.5)
            )
            denominator = frequency + 1.2 * (
                1 - 0.75 + 0.75 * length / max(average_length, 1.0)
            )
            score += inverse_document_frequency * frequency * 2.2 / denominator
        if score > 0:
            scored.append(
                (-score, document["attempt_id"], document["sequence"], document["body"])
            )
    scored.sort()
    return [body for _, _, _, body in scored[:k]]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--golden", required=True, type=Path)
    parser.add_argument("--out-evidence", required=True, type=Path)
    parser.add_argument("--out-provenance", required=True, type=Path)
    parser.add_argument("--k", type=int, default=10)
    args = parser.parse_args()
    lock = json.loads(memphant_runner.golden_lock_path(args.golden).read_text())
    corpus_rows, goldens = memphant_runner.verify_input_contract(
        args.corpus, args.golden, lock
    )
    documents = event_documents(corpus_rows)
    evidence_rows = []
    per_question = []
    for golden in goldens:
        bodies = bm25_search(documents, golden["question"], args.k)
        evidence_rows.append(gc.evidence_row(golden, bodies, args.k))
        per_question.append(
            {
                "question_id": golden["question_id"],
                "returned_items": len(bodies),
                "hit_at_5": gc.provenance_hit(golden, bodies, min(5, args.k)),
                "hit_at_10": gc.provenance_hit(golden, bodies, min(10, args.k)),
            }
        )
    gc.write_jsonl(args.out_evidence, evidence_rows)
    count = len(per_question)
    report = {
        "engine": "deterministic_file_search",
        "lane": "code",
        "mechanism": "BM25 over one immutable raw source-event document",
        "generated_memory": False,
        "outcome_feedback": False,
        "corpus_sha256": memphant_runner.corpus_contract(lock)["corpus_sha256"],
        "golden_sha256": lock["sha256"],
        "document_count": len(documents),
        "golden_count": count,
        "k": args.k,
        "recall_at_5": sum(row["hit_at_5"] for row in per_question) / count,
        "recall_at_10": sum(row["hit_at_10"] for row in per_question) / count,
        "per_question": per_question,
    }
    args.out_provenance.parent.mkdir(parents=True, exist_ok=True)
    args.out_provenance.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("recall_at_5", "recall_at_10")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
