#!/usr/bin/env python3
"""Zero-paid-call MiniLM screen over the frozen S8 top-64 fused pool."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import gate_common as gc  # noqa: E402
import s8_hybrid_common as s8  # noqa: E402
from instrument_power import min_detectable_effect  # noqa: E402
from track_r_retrieval_arm_compare import mcnemar_exact_p  # noqa: E402

POOL_DEPTH = 64
K = 10
EXPECTED_N = 180
EXPECTED_FUSER_HITS = 112
EXPECTED_CEILING_HITS = 164
MAX_P50_MS = 200.0
MIN_N_D = 6
POOL_SHA256 = "bdfa1fa00d6c990766f6f18bc896f5f23813178506b6c581f9a01db624a26d49"
GOLDEN_SHA256 = "4aed8e99dbf13d942d0e1d79b637ca5ee37b3dc30707a65ea3e9ffcd22bf4326"
CORPUS_SHA256 = "c008142e992179e8caf69822961330ccf285ba5741b9de79522402ea914c9669"
MODEL_SHA256 = "e9d8ebf845c413e981c175bfe49a3bfa9b3dcce2a3ba54875ee5df5a58639fbe"
PREREG = ROOT / "docs/build-log/artifacts/track-r-minilm/preregistration.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * p)] if ordered else 0.0


def validate_inputs(pool: Path, golden: Path, model_dir: Path) -> tuple[list[dict], list[dict]]:
    expected = ((pool, POOL_SHA256), (golden, GOLDEN_SHA256), (model_dir / "model_quantized.onnx", MODEL_SHA256))
    for path, digest in expected:
        actual = sha256(path)
        if actual != digest:
            raise RuntimeError(f"{path}: sha256 {actual} != frozen {digest}")
    pools, goldens = s8.load_pool_dump(pool), load_jsonl(golden)
    golden_ids = [row["question_id"] for row in goldens]
    if len(goldens) != EXPECTED_N or len(set(golden_ids)) != EXPECTED_N:
        raise RuntimeError("golden must contain 180 unique question ids")
    if set(pools) != set(golden_ids):
        raise RuntimeError("pool and golden question ids differ")
    return [pools[qid] for qid in golden_ids], goldens


def candidate_rows(pool_rows: list[dict]) -> list[dict]:
    """Adapt the frozen pool to rerank-pool input, deliberately stripping gold labels."""
    rows = []
    for row in pool_rows:
        docs = [
            {"doc_id": item["unit_id"], "text": item["body"], "chunks": []}
            for item in row["pool"][:POOL_DEPTH]
        ]
        if docs:  # empty pools are known retrieval misses; the model has nothing to score
            rows.append({"qid": row["question_id"], "question": row["query"], "docs": docs})
    return rows


def write_candidates(pool_rows: list[dict], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(candidate_rows(pool_rows), separators=(",", ":")) + "\n")


def selections_from_scores(pool_rows: list[dict], score_rows: list[dict]) -> tuple[dict[str, list[str]], dict]:
    pools = {row["question_id"]: row for row in pool_rows}
    scores_by_qid = {row["qid"]: row for row in score_rows}
    expected_scored = {qid for qid, row in pools.items() if row["pool"][:POOL_DEPTH]}
    if len(scores_by_qid) != len(score_rows) or set(scores_by_qid) != expected_scored:
        raise RuntimeError("score rows do not exactly cover every non-empty top-64 pool")

    selections: dict[str, list[str]] = {}
    docs_scored = 0
    containment_violations = 0
    for qid, pool_row in pools.items():
        candidates = pool_row["pool"][:POOL_DEPTH]
        if not candidates:
            selections[qid] = []
            continue
        score_row = scores_by_qid[qid]
        score_map = score_row.get("scores") or {}
        expected_ids = [item["unit_id"] for item in candidates]
        containment_violations += len(set(score_map) - set(expected_ids))
        if set(score_map) != set(expected_ids):
            raise RuntimeError(f"{qid}: score ids do not exactly match the handed pool")
        if score_row.get("docs_scored") != len(candidates):
            raise RuntimeError(f"{qid}: docs_scored does not match handed pool")
        if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in score_map.values()):
            raise RuntimeError(f"{qid}: scores must be finite numbers")
        ranked = sorted(enumerate(candidates), key=lambda pair: (-score_map[pair[1]["unit_id"]], pair[0]))
        selections[qid] = [item["body"] for _, item in ranked[:K]]
        docs_scored += len(candidates)
    return selections, {
        "pool_containment_violations": containment_violations,
        "raw_event_access": False,
        "gold_labels_in_model_input": False,
        "score_rows": len(score_rows),
        "empty_pool_rows": len(pools) - len(score_rows),
        "docs_scored": docs_scored,
    }


def paired(treatment: dict[str, bool], control: dict[str, bool], order: list[str]) -> dict:
    b = sum(treatment[q] and not control[q] for q in order)
    c = sum(control[q] and not treatment[q] for q in order)
    psi = (b + c) / len(order)
    return {
        "n": len(order), "b": b, "c": c, "n_d": b + c,
        "delta": (b - c) / len(order), "psi_observed": psi,
        "mcnemar_exact_p": mcnemar_exact_p(b, c),
        "mde_at_80": min_detectable_effect(len(order), psi),
    }


def analyze(pool: Path, golden: Path, model_dir: Path, candidates: Path, scores: Path, out: Path, binary: Path) -> dict:
    pool_rows, goldens = validate_inputs(pool, golden, model_dir)
    expected_candidates = candidate_rows(pool_rows)
    if json.loads(candidates.read_text()) != expected_candidates:
        raise RuntimeError("candidate file is not the exact blinded top-64 projection")
    selections, containment = selections_from_scores(pool_rows, json.loads(scores.read_text()))
    order = [row["question_id"] for row in goldens]
    by_qid = {row["question_id"]: row for row in goldens}
    fuser_selections = {row["question_id"]: [item["body"] for item in row["pool"][:K]] for row in pool_rows}
    ceiling_selections = {row["question_id"]: [item["body"] for item in row["pool"][:POOL_DEPTH]] for row in pool_rows}
    hits = lambda selected: {qid: gc.provenance_hit(by_qid[qid], selected[qid], len(selected[qid])) for qid in order}
    minilm_hits, fuser_hits, ceiling_hits = hits(selections), hits(fuser_selections), hits(ceiling_selections)
    observed = (sum(fuser_hits.values()), sum(ceiling_hits.values()))
    if observed != (EXPECTED_FUSER_HITS, EXPECTED_CEILING_HITS):
        raise RuntimeError(f"frozen pool controls changed: observed {observed}")
    stats = paired(minilm_hits, fuser_hits, order)
    latencies = [float(row["elapsed_ms"]) for row in json.loads(scores.read_text())]
    p50 = percentile(latencies, 0.5)
    accuracy_pass = bool(
        stats["n_d"] >= MIN_N_D and stats["mde_at_80"] is not None
        and stats["delta"] >= stats["mde_at_80"] and stats["mcnemar_exact_p"] < 0.05
    )
    latency_pass = p50 <= MAX_P50_MS
    prereg_sha = sha256(PREREG)
    report = {
        "schema": "memphant.eval.track-r-minilm-screen.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stage": s8.ENDPOINT_CONTRACT,
        "n": EXPECTED_N,
        "pool_depth": POOL_DEPTH,
        "k": K,
        "arms": {
            "minilm": {"hits_at_10": sum(minilm_hits.values()), "rate": sum(minilm_hits.values()) / EXPECTED_N},
            "shipped_fuser": {"hits_at_10": observed[0], "rate": observed[0] / EXPECTED_N},
            "in_pool_ceiling": {"hits_at_10": observed[1], "rate": observed[1] / EXPECTED_N},
        },
        "paired_vs_shipped_fuser": stats,
        "ceiling": {"recovered_fraction": (sum(minilm_hits.values()) - observed[0]) / (observed[1] - observed[0]), "remaining_hits": observed[1] - sum(minilm_hits.values())},
        "latency_ms": {"p50": p50, "p95": percentile(latencies, 0.95), "max": max(latencies), "questions_timed": len(latencies)},
        "containment": containment,
        "paid_api_spend_usd": 0,
        "lineage": s8.lineage({
            "pool_dump": pool, "golden": golden, "preregistration": PREREG,
            "candidates": candidates, "scores": scores, "model_onnx": model_dir / "model_quantized.onnx",
            "model_tokenizer": model_dir / "tokenizer.json", "memphant_eval_binary": binary,
            "runner": Path(__file__), "s8_contract": ROOT / "scripts/s8_hybrid_common.py",
            "production_seam": ROOT / "crates/memphant-eval/src/pool_tools.rs",
        }),
        "decision": {
            "accuracy_rule_met": accuracy_pass,
            "latency_rule_met": latency_pass,
            "instrument_promotion_eligible": False,
            "default_change_eligible": False,
            "default_changed": False,
            "reason": "Track R paraphrase is diagnostic-only: its preregistered leakage bar failed and its licence is not pinned by an actual licence artifact.",
            "preregistration_sha256": prereg_sha,
        },
    }
    report["evidence_contract"] = {
        "schema_version": 1,
        "decisional": False,
        "claim": f"On the frozen S8 Track R top-64 pool, local MiniLM scores {sum(minilm_hits.values())}/180 at stage-equalized hit@10 versus the shipped fused order's {observed[0]}/180 and the in-pool ceiling's {observed[1]}/180; paid API spend is $0.",
        "power": {"test": "two-sided exact (conditional binomial) McNemar", "n": stats["n"], "b": stats["b"], "c": stats["c"], "n_d": stats["n_d"], "psi_observed": stats["psi_observed"], "mde_at_80": stats["mde_at_80"], "computed_by": "scripts/instrument_power.py:min_detectable_effect", "source": str(out.relative_to(ROOT))},
        "harness": {"embed_model": "small (bge-small-en-v1.5), inherited in the frozen S8 pool", "scorer": "local Xenova ms-marco-MiniLM-L-6-v2 int8 through memphant_runtime::build_cross_reranker", "k": K, "budget": 8192, "flags": ["pool depth 64", "granularity=doc", "max_length=512", "batch_size=256"], "command": "python3 scripts/track_r_minilm_screen.py run"},
        "corpus": {"sha256": CORPUS_SHA256, "snapshot_id": "S8 pool dump bdfa1fa00d6c over track_r_paraphrase_golden@4aed8e99dbf1", "n_items": EXPECTED_N},
        "leakage": {"unit_definition": "one content event of the attempt, 4000-char clip", "absolute_target_coverage": 0.1346, "floor": 0.0667, "floor_kind": "exhaustive", "concentration": 2.018, "provenance_class": "machine_generated"},
        "mechanism_enabled": True,
        "mechanism_evidence": f"The production BYO seam produced {len(latencies)} score rows and {containment['docs_scored']} finite candidate scores from model sha256 {MODEL_SHA256}; every score id matched its handed top-64 pool.",
        "probe_kind": "lever",
        "attribution": {"method": "unverified", "note": "No bisect; this is an offline same-pool ordering contrast, not a commit attribution."},
        "instrument_verification": {"rows_counted": EXPECTED_N, "fields_counted": {"goldens": EXPECTED_N, "questions_scored": EXPECTED_N}, "shipped_rows_verified": True, "license_id": "CC-BY-4.0", "license_source": "unverified", "license_evidence": "HuggingFace dataset-card assertion only", "license_note": "No pinned LICENSE blob."},
        "notes": "Diagnostic only. The bank fails its own preregistered leakage bar (2.018 > 1.50), and its licence is not pinned by a licence artifact. Retrieval/reranking only; no reader, judge, or paid call. No default changes from this result.",
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "analyze", "run"))
    parser.add_argument("--pool", type=Path, default=Path.home() / ".memphant-private/track-r-paraphrase/run-s8/pool-dump.jsonl")
    parser.add_argument("--golden", type=Path, default=Path.home() / ".memphant-private/track-r-paraphrase/track_r_paraphrase_golden.jsonl")
    parser.add_argument("--model-dir", type=Path, default=Path.home() / ".cache/memphant-byo-minilm")
    parser.add_argument("--binary", type=Path, default=ROOT / "target/release/memphant-eval")
    parser.add_argument("--work-dir", type=Path, default=Path.home() / ".memphant-private/track-r-paraphrase/run-minilm")
    parser.add_argument("--out", type=Path, default=ROOT / "docs/build-log/artifacts/track-r-minilm/result.json")
    args = parser.parse_args()
    candidates, scores = args.work_dir / "candidates.json", args.work_dir / "scores.json"
    pool_rows, _ = validate_inputs(args.pool, args.golden, args.model_dir)
    if args.command in ("prepare", "run"):
        write_candidates(pool_rows, candidates)
    if args.command == "run":
        env = dict(os.environ)
        env.update(MEMPHANT_RERANKER="byo", MEMPHANT_RERANK_BYO_DIR=str(args.model_dir), MEMPHANT_RERANK_CANDIDATE_LIMIT=str(POOL_DEPTH), MEMPHANT_RERANK_MAX_LENGTH="512", MEMPHANT_RERANK_BATCH_SIZE="256", MEMPHANT_RERANK_TIMEOUT_MS="0")
        subprocess.run([str(args.binary), "rerank-pool", "--candidates", str(candidates), "--granularity", "doc", "--out", str(scores)], check=True, env=env)
    if args.command in ("analyze", "run"):
        report = analyze(args.pool, args.golden, args.model_dir, candidates, scores, args.out, args.binary)
        print(json.dumps({"minilm_hits": report["arms"]["minilm"]["hits_at_10"], "fuser_hits": EXPECTED_FUSER_HITS, "ceiling_hits": EXPECTED_CEILING_HITS, "default_change_eligible": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
