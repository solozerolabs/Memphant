#!/usr/bin/env python3
"""Run the pinned official ForgetEval suites against packaged MemPhant.

The adapter intentionally exposes only operations that the public MemPhant API
can represent without inventing semantics. ``supersede`` maps the highest-ranked
current unit to ``POST /v1/correct``. ``release`` maps an explicitly selected
ranked set to exact-unit ``POST /v1/forget``. Selective hard purge-by-query has
no public MemPhant primitive, so ``purge`` is reported N/A by the unmodified
upstream runner.
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
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate_common as gc  # noqa: E402
import gate_runtime as gr  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
repository_identity = gr.repository_identity
migration_identity = gr.migration_identity
DEFAULT_BASE_DATABASE_URL = "postgres://memphant:memphant@localhost:5432/memphant"
UPSTREAM_COMMIT = "b6053b7bdacc78a91b9ea4bb25f32edad278c495"
UPSTREAM_REPO = "https://github.com/deeplethe/lethe.git"
RELEASE_SELECTIONS = ("adaptive_gap", "rank_one")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def load_confirmation_ledger(path: Path | None) -> dict[str, dict]:
    if path is None:
        return {}
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema_version") != 1:
        raise ValueError("confirmation ledger must use schema_version 1")
    by_input = {}
    for row in document.get("confirmations", []):
        input_sha256 = row.get("input_sha256")
        if not isinstance(input_sha256, str) or len(input_sha256) != 64:
            raise ValueError("confirmation input_sha256 must be a SHA-256 hex digest")
        if input_sha256 in by_input:
            raise ValueError(f"duplicate confirmation for {input_sha256}")
        if row.get("confirmed") is not True or not row.get("confirmed_by"):
            raise ValueError(f"unconfirmed proposal {input_sha256}")
        selected = row.get("selected_body_sha256")
        if not isinstance(selected, list) or len(selected) != len(set(selected)):
            raise ValueError(f"invalid selected_body_sha256 for {input_sha256}")
        if any(not isinstance(value, str) or len(value) != 64 for value in selected):
            raise ValueError(f"invalid selected body digest for {input_sha256}")
        by_input[input_sha256] = row
    return by_input


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

    def __init__(
        self,
        client,
        settle,
        *,
        budget_tokens: int = 4096,
        release_selection: str = "adaptive_gap",
        cross_rerank: bool = False,
        case_ids: list[str] | None = None,
        confirmations: dict[str, dict] | None = None,
        capture_proposals: bool = False,
    ) -> None:
        if release_selection not in RELEASE_SELECTIONS:
            raise ValueError(
                f"release_selection must be one of {RELEASE_SELECTIONS}, "
                f"got {release_selection!r}"
            )
        self.client = client
        self.settle = settle
        self.budget_tokens = budget_tokens
        self.release_selection = release_selection
        self.cross_rerank = cross_rerank
        self.case_ids = case_ids
        self.confirmations = confirmations or {}
        self.capture_proposals = capture_proposals
        self.case_number = 0
        self.mutation_number = 0
        self.write_number = 0
        self.ctx: dict | None = None
        self.last_trace_id: str | None = None
        self.last_trace: dict | None = None
        self.counts: Counter[str] = Counter()
        self.case_records: list[dict[str, Any]] = []
        self.current_case_record: dict[str, Any] | None = None
        self.proposal_inputs: list[dict[str, Any]] = []

    def reset(self) -> None:
        self.case_number += 1
        self.mutation_number = 0
        case_id = (
            self.case_ids[self.case_number - 1]
            if self.case_ids is not None
            else f"case-{self.case_number}"
        )
        ref = f"forgeteval:{self.case_number}"
        self.ctx = self.client.bind_context(
            ref,
            subject_ref=f"{ref}:subject",
            actor_ref="forgeteval:runner",
            actor_kind="system",
            scope_ref=f"{ref}:scope",
            agent_node_ref="forgeteval:adapter",
        )
        self.current_case_record = {
            "case_number": self.case_number,
            "case_id": case_id,
            "operations": [],
            "_final_texts": [],
        }
        self.case_records.append(self.current_case_record)
        self.counts["resets"] += 1

    def _require_context(self) -> dict:
        if self.ctx is None:
            raise RuntimeError("ForgetEval called an operation before reset")
        return self.ctx

    def _record(self, operation: dict[str, Any]) -> None:
        if self.current_case_record is None:
            raise RuntimeError("ForgetEval called an operation before reset")
        self.current_case_record["operations"].append(operation)

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
        self._record(
            {
                "operation": "inscribe",
                "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "unit_id": unit_ids[0],
            }
        )
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
        from urllib.parse import urlencode

        self.last_trace = self.client.get(
            f"/v1/traces/{self.last_trace_id}?{urlencode(self._require_context())}"
        )
        rerank_facts = self.last_trace.get("cross_rerank")
        if self.cross_rerank:
            if not isinstance(rerank_facts, dict):
                raise RuntimeError("cross-rerank recall trace omitted provenance")
            if rerank_facts.get("failure") != "none":
                raise RuntimeError(
                    f"cross-rerank failed closed: {rerank_facts.get('failure')!r}"
                )
        scores: dict[str, float] = {}
        for candidate in self.last_trace.get("candidates", []):
            score = candidate.get("fused_score")
            unit_id = candidate.get("unit_id")
            if isinstance(unit_id, str) and score is not None:
                scores[unit_id] = max(scores.get(unit_id, float("-inf")), float(score))
        safe_hits = [
            {
                "unit_id": item["unit_id"],
                "body_sha256": hashlib.sha256(item["body"].encode()).hexdigest(),
                "fused_score": scores.get(item["unit_id"]),
            }
            for item in response.get("items", [])
        ]
        self._record(
            {
                "operation": "recall",
                "query_sha256": hashlib.sha256(query.encode()).hexdigest(),
                "trace_id": self.last_trace_id,
                "cross_rerank": rerank_facts if self.cross_rerank else None,
                "returned": safe_hits,
            }
        )
        self.current_case_record["_final_texts"] = [
            item["body"] for item in response.get("items", [])
        ]
        self.counts["recall"] += 1
        return response.get("items", [])

    def recall_texts(self, query: str, k: int = 5) -> list[str]:
        return [item["body"] for item in self._recall(query, k)]

    def _proposal_input(
        self,
        operation: str,
        query: str,
        hits: list[dict],
        *,
        new_text: str | None = None,
    ) -> dict:
        if self.current_case_record is None:
            raise RuntimeError("ForgetEval called a mutation before reset")
        candidates = [
            {
                "index": index,
                "body": hit["body"],
                "body_sha256": hashlib.sha256(hit["body"].encode()).hexdigest(),
            }
            for index, hit in enumerate(hits)
        ]
        value = {
            "case_id": self.current_case_record["case_id"],
            "mutation_index": self.mutation_number,
            "operation": operation,
            "query": query,
            "new_text": new_text,
            "candidates": candidates,
        }
        value["input_sha256"] = sha256_json(value)
        self.proposal_inputs.append(value)
        return value

    def _confirmed_selection(
        self, proposal_input: dict, hits: list[dict]
    ) -> tuple[list[dict], dict | None]:
        if not self.confirmations:
            return [], None
        input_sha256 = proposal_input["input_sha256"]
        confirmation = self.confirmations.get(input_sha256)
        if confirmation is None:
            raise RuntimeError(f"missing explicit confirmation for {input_sha256}")
        if confirmation.get("case_id") != proposal_input["case_id"]:
            raise RuntimeError(f"confirmation case mismatch for {input_sha256}")
        if confirmation.get("operation") != proposal_input["operation"]:
            raise RuntimeError(f"confirmation operation mismatch for {input_sha256}")
        by_hash: dict[str, list[dict]] = {}
        for hit in hits:
            digest = hashlib.sha256(hit["body"].encode()).hexdigest()
            by_hash.setdefault(digest, []).append(hit)
        selected = []
        for digest in confirmation["selected_body_sha256"]:
            matches = by_hash.get(digest, [])
            if len(matches) != 1:
                raise RuntimeError(
                    f"confirmed body hash {digest} matched {len(matches)} candidates"
                )
            selected.append(matches[0])
        return selected, confirmation

    def supersede(self, old_query: str, new_text: str) -> None:
        self.mutation_number += 1
        hits = self._recall(
            old_query, 20 if self.confirmations or self.capture_proposals else 1
        )
        proposal_input = self._proposal_input(
            "supersede", old_query, hits, new_text=new_text
        )
        if not hits:
            created = self.inscribe(new_text)
            self._record(
                {
                    "operation": "supersede",
                    "query_sha256": hashlib.sha256(old_query.encode()).hexdigest(),
                    "selected_unit_id": None,
                    "created": [created],
                    "superseded": [],
                }
            )
            return
        confirmed, confirmation = self._confirmed_selection(proposal_input, hits)
        if confirmation is not None:
            if len(confirmed) != 1:
                raise RuntimeError("confirmed supersession must select exactly one candidate")
            hits = confirmed
            replacement_text = confirmation.get("replacement_text")
            if not isinstance(replacement_text, str) or not replacement_text.strip():
                raise RuntimeError("confirmed supersession requires replacement_text")
        else:
            replacement_text = new_text
        self.write_number += 1
        result = self.client.post(
            "/v1/correct",
            {
                **self._require_context(),
                "selector": {"memory_unit_id": hits[0]["unit_id"]},
                "correction": {
                    "value": replacement_text,
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
        self._record(
            {
                "operation": "supersede",
                "query_sha256": hashlib.sha256(old_query.encode()).hexdigest(),
                "selected_unit_id": hits[0]["unit_id"],
                "created": result.get("created", []),
                "superseded": result.get("superseded", []),
                "correction_id": result.get("correction_id"),
                "trace_ref": result.get("trace_ref"),
                "confirmation_input_sha256": (
                    proposal_input["input_sha256"] if confirmation is not None else None
                ),
                "proposal_sha256": (
                    confirmation.get("proposal_sha256")
                    if confirmation is not None
                    else None
                ),
            }
        )
        self.counts["supersede"] += 1

    def release(self, query: str) -> int:
        # The public API deliberately has exact selectors, not semantic delete.
        # Use the upstream adapter's documented adaptive-gap rule over the
        # immutable trace's fused scores, then exact-forget only returned units.
        self.mutation_number += 1
        hits = self._recall(query, 20)
        proposal_input = self._proposal_input("release", query, hits)
        confirmed, confirmation = self._confirmed_selection(proposal_input, hits)
        if not hits:
            self._record(
                {
                    "operation": "release",
                    "query_sha256": hashlib.sha256(query.encode()).hexdigest(),
                    "selected_unit_ids": [],
                    "threshold": None,
                    "receipts": [],
                }
            )
            return 0
        if self.last_trace is None:
            raise RuntimeError("release recall trace is missing")
        scores: dict[str, float] = {}
        for candidate in self.last_trace.get("candidates", []):
            score = candidate.get("fused_score")
            if score is not None:
                unit_id = candidate["unit_id"]
                scores[unit_id] = max(scores.get(unit_id, float("-inf")), float(score))
        ranked = [(hit["unit_id"], scores.get(hit["unit_id"])) for hit in hits]
        present_scores = [score for _, score in ranked if score is not None]
        if confirmation is not None:
            threshold = None
            selected = [hit["unit_id"] for hit in confirmed]
            selection_strategy = "confirmed_proposal"
        elif self.release_selection == "rank_one":
            threshold = None
            selected = [hits[0]["unit_id"]]
            selection_strategy = self.release_selection
        elif present_scores:
            threshold = self._gap_threshold(present_scores)
            selected = [
                unit_id
                for unit_id, score in ranked
                if score is not None and score >= threshold
            ]
            selection_strategy = self.release_selection
        else:
            threshold = None
            selected = [hits[0]["unit_id"]]
            selection_strategy = self.release_selection
        receipts = []
        for unit_id in selected:
            result = self.client.post(
                "/v1/forget",
                {
                    **self._require_context(),
                    "selector": {
                        "scope_id": self._require_context()["scope_id"],
                        "memory_unit_id": unit_id,
                    },
                    "reason": f"ForgetEval release {selection_strategy} match",
                },
            )
            if unit_id not in result.get("invalidated_units", []):
                raise RuntimeError("forget response did not invalidate selected unit")
            receipts.append(
                {
                    "selected_unit_id": unit_id,
                    "deletion_generation": result.get("deletion_generation"),
                    "invalidated_units": result.get("invalidated_units", []),
                    "policy": result.get("policy"),
                    "verification": result.get("verification"),
                    "trace_ref": result.get("trace_ref"),
                }
            )
            self.counts["release"] += 1
        self._record(
            {
                "operation": "release",
                "query_sha256": hashlib.sha256(query.encode()).hexdigest(),
                "selected_unit_ids": selected,
                "selection_strategy": selection_strategy,
                "threshold": threshold,
                "receipts": receipts,
                "confirmation_input_sha256": (
                    proposal_input["input_sha256"] if confirmation is not None else None
                ),
                "proposal_sha256": (
                    confirmation.get("proposal_sha256")
                    if confirmation is not None
                    else None
                ),
            }
        )
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
        self._record(
            {
                "operation": "purge",
                "query_sha256": hashlib.sha256(query.encode()).hexdigest(),
                "outcome": "not_supported",
            }
        )
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


def select_cases(cases: list, case_ids: list[str] | None) -> list:
    if not case_ids:
        return list(cases)
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("--case-id values must be unique")
    by_id = {case.id: case for case in cases}
    missing = [case_id for case_id in case_ids if case_id not in by_id]
    if missing:
        raise ValueError(f"unknown --case-id values: {missing}")
    return [by_id[case_id] for case_id in case_ids]


def _error_kind(error: str | None) -> str | None:
    if error is None:
        return None
    if "N/A" in error:
        return "not_supported"
    kind = error.split(":", 1)[0]
    return kind if kind.isidentifier() else "exception"


def case_rows(summary: dict, cases: list, records: list[dict[str, Any]]) -> list[dict]:
    if len(cases) != len(records):
        raise RuntimeError("ForgetEval case/decision record count mismatch")
    outcomes = {
        case_id: (passed, error)
        for rows in summary["by_family"].values()
        for case_id, passed, error in rows
    }
    rows = []
    for case, record in zip(cases, records, strict=True):
        passed, error = outcomes[case.id]
        texts = record.get("_final_texts", [])
        blob = " ".join(texts).lower()
        must_contain = list(getattr(case, "must_contain", []))
        must_not_contain = list(getattr(case, "must_not_contain", []))
        mutation_ops = [
            mutation[0] for mutation in getattr(case, "mutations", [])
        ] or [
            operation
            for operation in getattr(case, "requires", ())
            if operation not in {"inscribe", "recall"}
        ]
        row = {
            "case_id": case.id,
            "family": case.family,
            "attack_category": (
                case.id.removeprefix("adv_").rsplit("_", 1)[0]
                if case.id.startswith("adv_")
                else None
            ),
            "mutation_operations": mutation_ops,
            "outcome": (
                "pass" if passed else "not_applicable" if error and "N/A" in error else "fail"
            ),
            "error_kind": _error_kind(error),
            "missing_must_contain_indexes": [
                index
                for index, value in enumerate(must_contain)
                if value.lower() not in blob
            ],
            "present_must_not_contain_indexes": [
                index
                for index, value in enumerate(must_not_contain)
                if value.lower() in blob
            ],
            "adapter_decisions": record["operations"],
        }
        rows.append(row)
    return rows


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
    parser.add_argument("--case-id", action="append")
    parser.add_argument(
        "--release-selection", choices=RELEASE_SELECTIONS, default="adaptive_gap"
    )
    parser.add_argument("--cross-rerank", action="store_true")
    parser.add_argument("--reranker", default="fastembed")
    parser.add_argument("--proposal-input-out")
    parser.add_argument("--confirmation-ledger")
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
    source_case_count = len(cases)
    cases = select_cases(cases, args.case_id)
    confirmations = load_confirmation_ledger(
        Path(args.confirmation_ledger) if args.confirmation_ledger else None
    )

    gr.reexec_through_scratch_db(args.database_url)
    database_url = os.environ["DATABASE_URL"]
    gr.check_embed_model_key(args.embed_model)
    tenant_id, api_key = gr.provision_tenant(
        args.cli_bin, database_url, name_prefix="forgeteval"
    )
    server = gr.Server(
        args.server_bin,
        database_url,
        args.port,
        args.embed_model,
        cross_rerank=args.cross_rerank,
        reranker=args.reranker,
    )
    try:
        server.start()
        client = gr.ApiClient(args.port, api_key, tenant_id)
        adapter = MemphantForgetEvalAdapter(
            client,
            lambda: None,
            release_selection=args.release_selection,
            cross_rerank=args.cross_rerank,
            case_ids=[case.id for case in cases],
            confirmations=confirmations,
            capture_proposals=bool(args.proposal_input_out),
        )
        summary = run_adapter(adapter, cases, verbose=False)
        portable_argv, portable_command = gr.portable_command(sys.argv, ROOT)
        results = summarize(summary)
        results["cases"] = case_rows(summary, cases, adapter.case_records)
        report = {
            "benchmark": "ForgetEval",
            "suite": args.suite,
            "upstream": lock,
            "case_generation": {
                "scale_per_family": args.scale if args.suite == "template" else None,
                "seed": args.seed if args.suite == "template" else None,
                "distractors": args.distractors if args.suite == "template" else None,
                "source_case_count": source_case_count,
                "selected_case_ids": [case.id for case in cases],
            },
            "runtime": {
                "server": gr.portable_path(Path(args.server_bin), ROOT),
                "server_sha256": sha256_file(Path(args.server_bin).resolve()),
                "cli": gr.portable_path(Path(args.cli_bin), ROOT),
                "cli_sha256": sha256_file(Path(args.cli_bin).resolve()),
                "database": "run-owned ephemeral scratch Postgres",
                "mode": "fast",
                "embed_model": args.embed_model,
                "release_selection": args.release_selection,
                "cross_rerank": args.cross_rerank,
                "reranker": args.reranker if args.cross_rerank else None,
                "confirmation_ledger": (
                    gr.portable_path(Path(args.confirmation_ledger), ROOT)
                    if args.confirmation_ledger
                    else None
                ),
                "confirmation_ledger_sha256": (
                    sha256_file(Path(args.confirmation_ledger).resolve())
                    if args.confirmation_ledger
                    else None
                ),
                "confirmed_mutation_count": len(confirmations),
                "adapter_path": gr.portable_path(Path(__file__), ROOT),
                "adapter_sha256": sha256_file(Path(__file__).resolve()),
                "repository": gr.repository_identity(ROOT),
                "migrations": gr.migration_identity(ROOT),
                "argv": portable_argv,
                "command": portable_command,
            },
            "capabilities": {
                "inscribe": "synchronous direct-unit POST /v1/episodes",
                "recall": "POST /v1/recall",
                "supersede": "rank-1 recall then exact-unit POST /v1/correct",
                "release": (
                    f"{args.release_selection} over ranked recall then exact-unit "
                    "POST /v1/forget"
                ),
                "purge": "N/A: no selective hard purge-by-query public primitive",
            },
            "results": results,
            "operation_counts": dict(sorted(adapter.counts.items())),
        }
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if args.proposal_input_out:
            proposal_out = Path(args.proposal_input_out)
            proposal_out.parent.mkdir(parents=True, exist_ok=True)
            proposal_document = {
                "schema_version": 1,
                "benchmark": "ForgetEval",
                "upstream": lock,
                "case_ids": [case.id for case in cases],
                "repository": gr.repository_identity(ROOT),
                "adapter_sha256": sha256_file(Path(__file__).resolve()),
                "inputs": adapter.proposal_inputs,
            }
            proposal_out.write_text(
                json.dumps(proposal_document, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        print(
            json.dumps(
                {key: value for key, value in results.items() if key != "cases"},
                sort_keys=True,
            )
        )
        return 0
    finally:
        server.stop()


if __name__ == "__main__":
    raise SystemExit(main())
