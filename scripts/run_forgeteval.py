#!/usr/bin/env python3
"""Run the pinned official ForgetEval suites against packaged MemPhant.

The adapter intentionally exposes only operations that the public MemPhant API
can represent without inventing semantics. ``supersede`` maps the highest-ranked
current unit to ``POST /v1/correct``. ``release`` conservatively maps only the
highest-ranked current unit to exact-unit ``POST /v1/forget``. Selective hard
purge-by-query has no public MemPhant primitive, so ``purge`` is reported N/A by
the unmodified upstream runner.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate_common as gc  # noqa: E402
import gate_runtime as gr  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASE_DATABASE_URL = "postgres://memphant:memphant@localhost:5432/memphant"
UPSTREAM_COMMIT = "b6053b7bdacc78a91b9ea4bb25f32edad278c495"
UPSTREAM_REPO = "https://github.com/deeplethe/lethe.git"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_upstream(official_dir: Path) -> dict:
    """Fail closed unless the official checkout is the exact reviewed release."""
    head = subprocess.run(
        ["git", "-C", str(official_dir), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if head != UPSTREAM_COMMIT:
        raise RuntimeError(f"ForgetEval revision drift: {head} != {UPSTREAM_COMMIT}")
    license_path = official_dir / "LICENSE"
    license_text = license_path.read_text(encoding="utf-8")
    if "MIT License" not in license_text:
        raise RuntimeError("ForgetEval license drift: expected MIT License")
    return {
        "repository": UPSTREAM_REPO,
        "commit": head,
        "license": "MIT",
        "license_sha256": sha256_file(license_path),
        "adapter_sha256": sha256_file(official_dir / "bench/forgeteval/adapter.py"),
        "runner_sha256": sha256_file(official_dir / "bench/forgeteval/run.py"),
        "templates_sha256": sha256_file(official_dir / "bench/forgeteval/generate.py"),
        "adversarial_sha256": sha256_file(
            official_dir / "bench/forgeteval/adversarial.py"
        ),
    }


def load_upstream(official_dir: Path):
    sys.path.insert(0, str(official_dir))
    from bench.forgeteval.adversarial import ADVERSARIAL_TESTS
    from bench.forgeteval.generate import generate
    from bench.forgeteval.run import run_adapter
    from bench.forgeteval.tests import ALL_TESTS

    return ALL_TESTS, ADVERSARIAL_TESTS, generate, run_adapter


class MemphantForgetEvalAdapter:
    """Official ForgetEval adapter backed only by current public REST verbs."""

    name = "memphant-public-api"

    def __init__(self, client, settle, *, budget_tokens: int = 4096) -> None:
        self.client = client
        self.settle = settle
        self.budget_tokens = budget_tokens
        self.case_number = 0
        self.write_number = 0
        self.ctx: dict | None = None
        self.last_trace_id: str | None = None
        self.counts: Counter[str] = Counter()

    def reset(self) -> None:
        self.case_number += 1
        ref = f"forgeteval:{self.case_number}"
        self.ctx = self.client.bind_context(
            ref,
            subject_ref=f"{ref}:subject",
            actor_ref="forgeteval:runner",
            actor_kind="system",
            scope_ref=f"{ref}:scope",
            agent_node_ref="forgeteval:adapter",
        )
        self.counts["resets"] += 1

    def _require_context(self) -> dict:
        if self.ctx is None:
            raise RuntimeError("ForgetEval called an operation before reset")
        return self.ctx

    def inscribe(self, text: str) -> str:
        ctx = self._require_context()
        self.write_number += 1
        result = self.client.post(
            "/v1/episodes",
            {
                **ctx,
                "source_ref": f"forgeteval:case:{self.case_number}:write:{self.write_number}",
                "observed_at": "2026-07-23T00:00:00Z",
                "payload": {
                    "unit": {
                        "kind": "semantic",
                        "fact_key": (
                            f"forgeteval:{self.case_number}:fact:{self.write_number}"
                        ),
                        "predicate": "forgeteval_fact",
                        "body": text,
                        "confidence": 1.0,
                    }
                },
            },
        )
        unit_ids = result.get("unit_ids", [])
        if len(unit_ids) != 1:
            raise RuntimeError("direct-unit retain did not return exactly one unit_id")
        self.counts["inscribe"] += 1
        return unit_ids[0]

    def _recall(self, query: str, k: int) -> list[dict]:
        self.settle()
        response = self.client.post(
            "/v1/recall",
            {
                **self._require_context(),
                "query": query,
                "limit": k,
                "budget_tokens": self.budget_tokens,
                "mode": "fast",
            },
        )
        if response.get("degraded"):
            raise RuntimeError("ForgetEval recall degraded after worker settlement")
        self.last_trace_id = response.get("trace_id")
        if not self.last_trace_id:
            raise RuntimeError("recall response omitted trace_id")
        self.counts["recall"] += 1
        return response.get("items", [])

    def recall_texts(self, query: str, k: int = 5) -> list[str]:
        return [item["body"] for item in self._recall(query, k)]

    def supersede(self, old_query: str, new_text: str) -> None:
        hits = self._recall(old_query, 1)
        if not hits:
            self.inscribe(new_text)
            return
        self.write_number += 1
        result = self.client.post(
            "/v1/correct",
            {
                **self._require_context(),
                "selector": {"memory_unit_id": hits[0]["unit_id"]},
                "correction": {
                    "value": new_text,
                    "reason": "ForgetEval supersede",
                    "source_ref": (
                        f"forgeteval:case:{self.case_number}:write:{self.write_number}"
                    ),
                    "observed_at": "2026-07-23T00:00:01Z",
                },
            },
        )
        if hits[0]["unit_id"] not in result.get("superseded", []):
            raise RuntimeError("correct response did not supersede selected unit")
        self.counts["supersede"] += 1

    def release(self, query: str) -> int:
        # The public API deliberately has exact selectors, not semantic delete.
        # Use the upstream adapter's documented adaptive-gap rule over the
        # immutable trace's fused scores, then exact-forget only returned units.
        hits = self._recall(query, 20)
        if not hits:
            return 0
        from urllib.parse import urlencode

        trace = self.client.get(
            f"/v1/traces/{self.last_trace_id}?{urlencode(self._require_context())}"
        )
        scores: dict[str, float] = {}
        for candidate in trace.get("candidates", []):
            score = candidate.get("fused_score")
            if score is not None:
                unit_id = candidate["unit_id"]
                scores[unit_id] = max(scores.get(unit_id, float("-inf")), float(score))
        ranked = [(hit["unit_id"], scores.get(hit["unit_id"])) for hit in hits]
        present_scores = [score for _, score in ranked if score is not None]
        if present_scores:
            threshold = self._gap_threshold(present_scores)
            selected = [
                unit_id
                for unit_id, score in ranked
                if score is not None and score >= threshold
            ]
        else:
            selected = [hits[0]["unit_id"]]
        for unit_id in selected:
            result = self.client.post(
                "/v1/forget",
                {
                    **self._require_context(),
                    "selector": {
                        "scope_id": self._require_context()["scope_id"],
                        "memory_unit_id": unit_id,
                    },
                    "reason": "ForgetEval release adaptive-gap match",
                },
            )
            if unit_id not in result.get("invalidated_units", []):
                raise RuntimeError("forget response did not invalidate selected unit")
            self.counts["release"] += 1
        return len(selected)

    @staticmethod
    def _gap_threshold(scores: list[float], min_gap: float = 0.05) -> float:
        if not scores:
            return float("inf")
        ordered = sorted(scores, reverse=True)
        if len(ordered) == 1:
            return ordered[0] * 0.95
        gaps = [ordered[i] - ordered[i + 1] for i in range(len(ordered) - 1)]
        best = max(range(len(gaps)), key=gaps.__getitem__)
        if gaps[best] >= min_gap:
            return (ordered[best] + ordered[best + 1]) / 2.0
        return ordered[0] * 0.95

    def purge(self, query: str) -> int:
        raise NotImplementedError(
            "MemPhant has subject erasure and exact-unit forget, not selective "
            "hard purge by natural-language query"
        )


def summarize(summary: dict) -> dict:
    families = {}
    totals = Counter()
    for family, rows in summary["by_family"].items():
        passed = sum(1 for _, ok, _ in rows if ok)
        na = sum(1 for _, ok, error in rows if not ok and error and "N/A" in error)
        failed = len(rows) - passed - na
        families[family] = {
            "passed": passed,
            "failed": failed,
            "not_applicable": na,
            "total": len(rows),
        }
        totals.update(families[family])
    return {
        "families": families,
        "passed": totals["passed"],
        "failed": totals["failed"],
        "not_applicable": totals["not_applicable"],
        "total": totals["total"],
        "wall_seconds": summary["wall_seconds"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=DEFAULT_BASE_DATABASE_URL)
    parser.add_argument("--official-dir", required=True)
    parser.add_argument("--suite", choices=["smoke", "template", "adversarial"], required=True)
    parser.add_argument("--scale", type=int, default=200)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--distractors", type=int, default=4)
    parser.add_argument("--out", required=True)
    parser.add_argument("--embed-model", default="small")
    parser.add_argument("--port", type=int, default=39431)
    parser.add_argument("--server-bin", default=str(ROOT / "target/release/memphant-server"))
    parser.add_argument("--cli-bin", default=str(ROOT / "target/release/memphant-cli"))
    args = parser.parse_args()

    official_dir = Path(args.official_dir).resolve()
    lock = verify_upstream(official_dir)
    all_tests, adversarial_tests, generate, run_adapter = load_upstream(official_dir)
    if args.suite == "smoke":
        cases = all_tests
    elif args.suite == "template":
        if args.scale <= 0:
            raise ValueError("template scale must be positive")
        cases = generate(args.scale, seed=args.seed, distractors=args.distractors, lang="en")
    else:
        cases = adversarial_tests

    gr.reexec_through_scratch_db(args.database_url)
    database_url = os.environ["DATABASE_URL"]
    gr.check_embed_model_key(args.embed_model)
    tenant_id, api_key = gr.provision_tenant(
        args.cli_bin, database_url, name_prefix="forgeteval"
    )
    server = gr.Server(args.server_bin, database_url, args.port, args.embed_model)
    try:
        server.start()
        client = gr.ApiClient(args.port, api_key, tenant_id)
        adapter = MemphantForgetEvalAdapter(client, lambda: None)
        summary = run_adapter(adapter, cases, verbose=False)
        report = {
            "benchmark": "ForgetEval",
            "suite": args.suite,
            "upstream": lock,
            "case_generation": {
                "scale_per_family": args.scale if args.suite == "template" else None,
                "seed": args.seed if args.suite == "template" else None,
                "distractors": args.distractors if args.suite == "template" else None,
            },
            "runtime": {
                "server": str(Path(args.server_bin).resolve()),
                "database": "run-owned ephemeral scratch Postgres",
                "mode": "fast",
                "embed_model": args.embed_model,
            },
            "capabilities": {
                "inscribe": "synchronous direct-unit POST /v1/episodes",
                "recall": "POST /v1/recall",
                "supersede": "rank-1 recall then exact-unit POST /v1/correct",
                "release": "trace fused-score adaptive gap then exact-unit POST /v1/forget",
                "purge": "N/A: no selective hard purge-by-query public primitive",
            },
            "results": summarize(summary),
            "operation_counts": dict(sorted(adapter.counts.items())),
        }
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(report["results"], sort_keys=True))
        return 0
    finally:
        server.stop()


if __name__ == "__main__":
    raise SystemExit(main())
