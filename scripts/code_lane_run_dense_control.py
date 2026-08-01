#!/usr/bin/env python3
"""S4 Arm 2 — the no-memory dense-RAG control.

Cosine top-k over `bge-small-en-v1.5` vectors of the **raw source events**. No
memory unit, no compilation, no bitemporal state, no fusion, no packing, no
reranking, no lexical channel. This is the "60-line script" the substrate has to
beat.

Same embedder as the MemPhant treatment on purpose: MemDelta measured an
embedder swap alone moving accuracy +6.2pp, so holding it fixed is the only way
the comparison attributes anything to the substrate. `bge-small` takes **no**
query/document prefix — `memphant-runtime::embeddings::prefix_text` returns the
text unchanged for the bge family — so plain `TextEmbedding.embed` on both sides
is byte-faithful to what MemPhant sends its provider.

Haystack, query string and scoring stage are the shared ones in
`s4_controls_common`; nothing about the endpoint is decided in this file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import code_lane_run_memphant as memphant_runner  # noqa: E402
import s4_controls_common as s4  # noqa: E402

MODEL_ID = "BAAI/bge-small-en-v1.5"
EXPECTED_DIMS = 384


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--golden", required=True, type=Path)
    parser.add_argument("--out-evidence", required=True, type=Path)
    parser.add_argument("--out-provenance", required=True, type=Path)
    parser.add_argument("--k", type=int, default=10)
    args = parser.parse_args()

    import numpy as np
    from fastembed import TextEmbedding

    corpus_rows, goldens, lock = s4.load_contract(args.corpus, args.golden)
    by_attempt = s4.attempt_events(corpus_rows)

    started = time.time()
    embedder = TextEmbedding(model_name=MODEL_ID)

    # Embed each event of each attempt any golden points at, once.
    needed = sorted({s4.golden_attempt_id(golden) for golden in goldens})
    texts: list[str] = []
    index: list[tuple[str, int]] = []
    for attempt_id in needed:
        for position, event in enumerate(by_attempt[attempt_id]):
            texts.append(event["text"])
            index.append((attempt_id, position))
    doc_vectors = np.array(list(embedder.embed(texts)), dtype=np.float32)
    queries = [memphant_runner.retrieval_query(golden) for golden in goldens]
    query_vectors = np.array(list(embedder.embed(queries)), dtype=np.float32)

    # --- mechanism liveness, from this arm's own output (A.5) ---------------
    if doc_vectors.shape[1] != EXPECTED_DIMS or query_vectors.shape[1] != EXPECTED_DIMS:
        raise RuntimeError(
            f"embedding dim {doc_vectors.shape[1]}/{query_vectors.shape[1]} "
            f"!= {EXPECTED_DIMS}: the embedder did not fire as declared"
        )
    distinct = len({vector.tobytes() for vector in doc_vectors})
    distinct_ratio = distinct / len(doc_vectors)
    if distinct_ratio < 0.99:
        raise RuntimeError(
            f"only {distinct_ratio:.4f} of document vectors are distinct — a "
            "degenerate embedder scores identically to a neutral one"
        )

    def normalize(matrix):
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        return matrix / np.maximum(norms, 1e-12)

    doc_unit = normalize(doc_vectors)
    query_unit = normalize(query_vectors)

    offsets: dict[str, tuple[int, int]] = {}
    cursor = 0
    for attempt_id in needed:
        length = len(by_attempt[attempt_id])
        offsets[attempt_id] = (cursor, cursor + length)
        cursor += length

    selections: dict[str, list[str]] = {}
    ranks: dict[str, list[dict]] = {}
    for position, golden in enumerate(goldens):
        attempt_id = s4.golden_attempt_id(golden)
        start, end = offsets[attempt_id]
        scores = doc_unit[start:end] @ query_unit[position]
        order = np.argsort(-scores, kind="stable")[: args.k]
        events = by_attempt[attempt_id]
        selections[golden["question_id"]] = [events[int(i)]["text"] for i in order]
        ranks[golden["question_id"]] = [
            {
                "rank": rank + 1,
                "sequence": events[int(i)]["sequence"],
                "cosine": round(float(scores[int(i)]), 6),
            }
            for rank, i in enumerate(order)
        ]

    report = s4.score_arm(goldens, selections, k=args.k)
    report |= {
        "arm": "dense_rag_control",
        "engine": "no_memory_dense_rag",
        "lane": "code",
        "mechanism": (
            "cosine top-k over bge-small-en-v1.5 vectors of raw source events; "
            "no unit, no compilation, no bitemporal state, no fusion, no packing"
        ),
        "generated_memory": False,
        "outcome_feedback": False,
        "embed_model": MODEL_ID,
        "embed_dims": int(doc_vectors.shape[1]),
        "documents_embedded": int(doc_vectors.shape[0]),
        "attempts_scoped": len(needed),
        "corpus_sha256": memphant_runner.corpus_contract(lock)["corpus_sha256"],
        "golden_sha256": lock["sha256"],
        "liveness": {
            "distinct_document_vectors": distinct,
            "distinct_ratio": round(distinct_ratio, 6),
            "query_vector_sha256": hashlib.sha256(query_vectors.tobytes()).hexdigest(),
            "document_vector_sha256": hashlib.sha256(doc_vectors.tobytes()).hexdigest(),
        },
        "per_question_ranks": ranks,
        "elapsed_seconds": round(time.time() - started, 2),
        "lineage": s4.lineage({"corpus": args.corpus, "golden": args.golden}),
        "reported_spend_usd": 0.0,
    }
    s4.write_report(report, args.out_provenance, args.out_evidence)
    print(
        json.dumps(
            {
                key: report[key]
                for key in ("hits_at_10", "recall_at_5", "recall_at_10", "documents_embedded")
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
