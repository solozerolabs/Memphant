#!/usr/bin/env python3
"""Audit Memora and score sealed MemPhant answers with native FAMA code."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import types
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from provider_attempts import (  # noqa: E402
    ProviderAttemptLedger,
    install_openai_meter,
    openrouter_generation_lookup,
    validate_provider_attempt_ledger,
)
DEFAULT_MANIFEST = ROOT / "benchmarks" / "manifests" / "memora.lock.json"
PERIODS = ("weekly", "monthly", "quarterly")
TASKS = ("remembering", "reasoning", "recommending")
ANSWER_KEYS = {
    "period", "persona", "question_id", "question", "question_date",
    "task_type", "answer", "evidence", "trace",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def sha256_tree(root: Path, relative_root: str) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    files = sorted(path for path in (root / relative_root).rglob("*") if path.is_file())
    size = 0
    for path in files:
        relative = path.relative_to(root).as_posix().encode()
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
        size += len(content)
    return digest.hexdigest(), len(files), size


def acquire_official_repo(cache_dir: Path, manifest: dict[str, Any]) -> Path:
    revision = manifest["code"]["revision"]
    destination = cache_dir / revision
    if destination.is_dir():
        return destination
    cache_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache_dir) as temp:
        checkout = Path(temp) / "repo"
        subprocess.run(
            ["git", "clone", "--filter=blob:none", manifest["code"]["repo"], str(checkout)],
            check=True,
        )
        subprocess.run(["git", "-C", str(checkout), "checkout", "--detach", revision], check=True)
        os.replace(checkout, destination)
    return destination


def verify_official_repo(repo: Path, manifest: dict[str, Any], *, verify_revision: bool = True) -> None:
    if verify_revision:
        revision = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        if revision != manifest["code"]["revision"]:
            raise ValueError(f"official Memora revision mismatch: {revision}")
    for relative, expected in manifest["native_scorer"]["files"].items():
        path = repo / relative
        if not path.is_file() or sha256_file(path) != expected:
            raise ValueError(f"official Memora source hash mismatch for {relative}")
    license_lock = manifest["license"]
    if sha256_file(repo / license_lock["file"]) != license_lock["sha256"]:
        raise ValueError("official Memora license hash mismatch")


def load_official_questions(repo: Path) -> tuple[list[dict[str, Any]], set[tuple[str, str, str]], int]:
    questions: list[dict[str, Any]] = []
    identities: set[tuple[str, str, str]] = set()
    subquestions = 0
    for period in PERIODS:
        for persona_dir in sorted(path for path in (repo / "data" / period).iterdir() if path.is_dir()):
            files = list(persona_dir.glob("evaluation_questions_*.json"))
            if len(files) != 1:
                raise ValueError(f"expected one question file under {persona_dir}")
            document = json.loads(files[0].read_text(encoding="utf-8"))
            if document.get("persona") != persona_dir.name:
                raise ValueError(f"persona mismatch in {files[0]}")
            for task in TASKS:
                for question in document.get("questions", {}).get(task, []):
                    identity = (period, persona_dir.name, question.get("question_id"))
                    if identity in identities:
                        raise ValueError(f"duplicate official question identity: {identity}")
                    identities.add(identity)
                    copied = dict(question)
                    copied.update(period=period, persona=persona_dir.name, task_type=task.title())
                    questions.append(copied)
                    subquestions += len(question.get("evaluation", {}).get("evaluation_questions", []))
    return questions, identities, subquestions


def verify_dataset(repo: Path, manifest: dict[str, Any]) -> tuple[list[dict[str, Any]], set[tuple[str, str, str]], int]:
    lock = manifest["dataset"]
    digest, file_count, size = sha256_tree(repo, lock["root"])
    if (digest, file_count, size) != (lock["tree_sha256"], lock["file_count"], lock["size_bytes"]):
        raise ValueError("official Memora dataset tree mismatch")
    sessions = list((repo / "data").glob("*/*/conversations/session_*.json"))
    questions, identities, subquestions = load_official_questions(repo)
    if len(sessions) != lock["session_count"] or len(questions) != lock["question_count"]:
        raise ValueError("official Memora dataset counts mismatch")
    if subquestions != lock["evaluation_subquestion_count"]:
        raise ValueError("official Memora evaluation subquestion count mismatch")
    return questions, identities, subquestions


def select_group(
    questions: list[dict[str, Any]], selection: str
) -> tuple[list[dict[str, Any]], set[tuple[str, str, str]], int]:
    period, separator, persona = selection.partition("/")
    if not separator:
        raise ValueError("--group must be PERIOD/PERSONA")
    selected = [
        question
        for question in questions
        if question["period"] == period and question["persona"] == persona
    ]
    if not selected:
        raise ValueError(f"Memora group not found: {selection}")
    identities = {
        (question["period"], question["persona"], question["question_id"])
        for question in selected
    }
    subquestions = sum(
        len(question.get("evaluation", {}).get("evaluation_questions", []))
        for question in selected
    )
    return selected, identities, subquestions


def select_task(
    questions: list[dict[str, Any]], task: str
) -> tuple[list[dict[str, Any]], set[tuple[str, str, str]], int]:
    if task not in TASKS:
        raise ValueError(f"unknown Memora task: {task}")
    selected = [question for question in questions if question["task_type"] == task.title()]
    if not selected:
        raise ValueError(f"Memora task has no questions: {task}")
    identities = {
        (question["period"], question["persona"], question["question_id"])
        for question in selected
    }
    subquestions = sum(
        len(question.get("evaluation", {}).get("evaluation_questions", []))
        for question in selected
    )
    return selected, identities, subquestions


def load_answers(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("summary", {}).get("complete") is not True:
        raise ValueError("Memora answers must be a complete sealed generation artifact")
    value = value.get("data")
    if not isinstance(value, list):
        raise ValueError("Memora answers artifact must contain a data list")
    return value


def answer_identity(row: dict[str, Any]) -> tuple[str, str, str]:
    return row.get("period"), row.get("persona"), row.get("question_id")


def verify_answers(expected: set[tuple[str, str, str]], answers: list[dict[str, Any]]) -> None:
    identities = [answer_identity(row) for row in answers]
    if len(identities) != len(set(identities)):
        raise ValueError("Memora answers contain a duplicate question identity")
    if len(identities) != len(expected) or set(identities) != expected:
        raise ValueError("Memora answer identities must exactly match official questions")
    for row in answers:
        if set(row) != ANSWER_KEYS:
            raise ValueError(f"Memora answer keys violate the sealed schema: {answer_identity(row)}")
        if not isinstance(row["answer"], str) or not row["answer"].strip():
            raise ValueError(f"Memora answer must be non-empty: {answer_identity(row)}")
        if not isinstance(row["evidence"], list):
            raise ValueError(f"Memora evidence must be a list: {answer_identity(row)}")
        for rank, item in enumerate(row["evidence"], 1):
            if not isinstance(item, dict) or set(item) - {"rank", "body", "unit_id"}:
                raise ValueError("Memora evidence item keys violate the sealed schema")
            if item.get("rank") != rank or not isinstance(item.get("body"), str) or not item["body"].strip():
                raise ValueError("Memora evidence ranks/bodies are invalid")
        trace = row["trace"]
        if set(trace) != {"trace_id", "degraded", "evidence_sha256"} or trace["degraded"] is not False:
            raise ValueError("Memora answer trace is incomplete or degraded")
        if not isinstance(trace["trace_id"], str) or not trace["trace_id"].strip():
            raise ValueError("Memora trace ID is invalid")
        if trace["evidence_sha256"] != sha256_json(row["evidence"]):
            raise ValueError("Memora evidence digest does not match evidence")


def verify_native_results(results: list[dict[str, Any]], *, expected_questions: int, expected_subquestions: int) -> None:
    if len(results) != expected_questions:
        raise ValueError("native FAMA result question count mismatch")
    seen = 0
    for result in results:
        if result.get("error"):
            raise RuntimeError(f"native FAMA scorer error for {result.get('question_id')}")
        for evaluation in result.get("evaluation_questions", []):
            seen += 1
            judged = evaluation.get("evaluation_result", {})
            per_judge = judged.get("per_judge_results", {})
            if (
                type(judged.get("is_correct")) is not bool
                or judged.get("num_judges") != 3
                or judged.get("num_valid_judges") != 3
            ):
                raise RuntimeError("native FAMA requires three valid judges for every subquestion")
            if set(per_judge) != {"openai", "anthropic", "google"}:
                raise RuntimeError("native FAMA judge set drifted")
            if any(
                value.get("llm_answer") not in {"yes", "no"}
                or type(value.get("is_correct")) is not bool
                or "error" in value
                or "parse_error" in value
                for value in per_judge.values()
            ):
                raise RuntimeError("native FAMA judge returned an invalid result")
    if seen != expected_subquestions:
        raise ValueError("native FAMA result subquestion count mismatch")


def verify_judge_ledger(
    ledger: dict[str, Any], *, expected_subquestions: int,
    judge_models: dict[str, str],
) -> None:
    validate_provider_attempt_ledger(ledger)
    expected_attempts = expected_subquestions * len(judge_models)
    if ledger.get("provider_attempts") != expected_attempts:
        raise RuntimeError(
            f"native FAMA judge attempt count mismatch: "
            f"{ledger.get('provider_attempts')} != {expected_attempts}"
        )
    counts: dict[str, int] = defaultdict(int)
    for attempt in ledger["attempts"]:
        response = attempt["result"]["response"]
        if attempt.get("retry_index") != 0 or response.get("retry_index") != 0:
            raise RuntimeError("native FAMA judge ledger contains a retry")
        counts[response["requested_model"]] += 1
    expected_models = set(judge_models.values())
    if set(counts) != expected_models or any(
        counts[model] != expected_subquestions for model in expected_models
    ):
        raise RuntimeError("native FAMA judge model attempt counts mismatch")


def harden_native_evaluator(evaluator):
    """Keep the official scorer, but make every paid judge transport single-shot."""
    for client in evaluator.judge_clients.values():
        original_generate = client.generate_response

        def generate_response(*args, _original=original_generate, **kwargs):
            kwargs["max_retries"] = 1
            return _original(*args, **kwargs)

        client.generate_response = generate_response
    original_evaluate = evaluator._evaluate_with_single_judge

    def evaluate(*args, **kwargs):
        result = original_evaluate(*args, **kwargs)
        if (
            not isinstance(result, dict)
            or result.get("llm_answer") not in {"yes", "no"}
            or "error" in result
            or "parse_error" in result
        ):
            raise RuntimeError("official Memora judge failed or returned invalid JSON")
        return result

    evaluator._evaluate_with_single_judge = evaluate
    return evaluator


def load_native_module(repo: Path, entrypoint: str):
    path = repo / entrypoint
    spec = importlib.util.spec_from_file_location("memora_native_fama", path)
    if not spec or not spec.loader:
        raise RuntimeError("cannot import official Memora scorer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_native_evaluator(native, manifest: dict[str, Any], output_dir: Path):
    evaluator = native.MemoryQuestionAnswering.__new__(native.MemoryQuestionAnswering)
    evaluator.memory_system_name = "memphant"
    evaluator.user_id = "sealed-per-period-persona"
    evaluator.model = "precomputed-memphant-answer"
    evaluator.output_dir = output_dir
    evaluator.use_multi_judge = True
    evaluator.strict_judges = True
    evaluator.judge_models = manifest["native_scorer"]["judge_models"]
    client_class = native.import_openrouter_client()
    evaluator.judge_clients = {
        name: client_class(model=model) for name, model in evaluator.judge_models.items()
    }
    if len(evaluator.judge_clients) != 3:
        raise RuntimeError("native FAMA requires all three judge clients")
    evaluator.stats = {key: 0 for key in (
        "total_questions", "answered", "failed", "no_memories_found",
        "with_memories_found", "total_evaluations", "passed_evaluations",
        "questions_with_evaluations", "memory_presence_total",
        "memory_presence_passed", "forgetting_absence_total",
        "forgetting_absence_passed",
    )}

    def search_memories(self, question, limit=50, session_date=None, date_range=None):
        del question, limit, session_date, date_range
        return [dict(item) for item in self._sealed_answer["evidence"]]

    def generate_answer(self, question, memories, question_context=None):
        del question, memories, question_context
        return self._sealed_answer["answer"]

    evaluator.search_memories = types.MethodType(search_memories, evaluator)
    evaluator.generate_answer = types.MethodType(generate_answer, evaluator)
    return evaluator


def score(evaluator, questions: list[dict[str, Any]], answers: list[dict[str, Any]]) -> dict[str, Any]:
    answer_by_id = {answer_identity(row): row for row in answers}
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    all_results = []
    for question in questions:
        identity = (question["period"], question["persona"], question["question_id"])
        evaluator._sealed_answer = answer_by_id[identity]
        native_question = dict(question)
        native_question["session_date"] = question.get("question_date")
        result = evaluator.answer_question(native_question)
        result["period"] = question["period"]
        result["persona"] = question["persona"]
        grouped[(question["period"], question["persona"])].append(result)
        all_results.append(result)
    reports = {
        f"{period}/{persona}": evaluator._generate_report({"results": results, "metadata": {}})
        for (period, persona), results in grouped.items()
    }
    return {"results": all_results, "native_reports": reports}


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--official-repo", type=Path)
    parser.add_argument("--cache-dir", type=Path, default=Path.home() / ".cache/memphant-bench/memora")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--answers", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--attempt-ledger", type=Path)
    parser.add_argument("--group", help="optional PERIOD/PERSONA pilot selection")
    parser.add_argument("--task", choices=TASKS, help="optional task-only diagnostic selection")
    parser.add_argument("--verify-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    repo = args.official_repo or acquire_official_repo(args.cache_dir, manifest)
    verify_official_repo(repo, manifest)
    questions, identities, subquestions = verify_dataset(repo, manifest)
    session_count = manifest["dataset"]["session_count"]
    if args.group:
        questions, identities, subquestions = select_group(questions, args.group)
        period, persona = args.group.split("/", 1)
        session_count = len(
            list((repo / "data" / period / persona / "conversations").glob("session_*.json"))
        )
    if args.task:
        questions, identities, subquestions = select_task(questions, args.task)
    if args.answers:
        answers = load_answers(args.answers)
        verify_answers(identities, answers)
    else:
        answers = []
    if args.verify_only:
        print(json.dumps({"questions": len(questions), "sessions": session_count, "subquestions": subquestions, "paid_calls": 0}, sort_keys=True))
        return
    if not args.answers or not args.out:
        raise SystemExit("scoring requires --answers and --out")
    proof_path = args.out.with_suffix(args.out.suffix + ".proof.json")
    judge_ledger_path = args.attempt_ledger or args.out.with_suffix(
        args.out.suffix + ".attempts.json"
    )
    for path in (args.out, proof_path, judge_ledger_path):
        if path.exists():
            raise ValueError(f"Memora scoring requires a fresh artifact path: {path}")
    api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPEN_ROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("Memora scoring requires OPENROUTER_API_KEY")
    import openai
    judge_ledger = ProviderAttemptLedger(
        judge_ledger_path,
        hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
        "memora-native-judge",
        200_000_000_000,
        4_258_002_400,
    )
    install_openai_meter(
        openai,
        judge_ledger,
        max_liability_nanos=10_000_000_000,
        context={
            "benchmark": manifest["benchmark"],
            "official_revision": manifest["code"]["revision"],
            "selection": {"group": args.group or "all", "task": args.task or "all"},
        },
        generation_lookup=openrouter_generation_lookup(api_key),
    )
    native = load_native_module(repo, manifest["native_scorer"]["entrypoint"])
    evaluator = harden_native_evaluator(
        make_native_evaluator(native, manifest, args.out.parent)
    )
    output = score(evaluator, questions, answers)
    verify_native_results(output["results"], expected_questions=len(questions), expected_subquestions=subquestions)
    judge_snapshot = judge_ledger.snapshot()
    verify_judge_ledger(
        judge_snapshot,
        expected_subquestions=subquestions,
        judge_models=manifest["native_scorer"]["judge_models"],
    )
    atomic_json(args.out, output)
    proof = {
        "answers_sha256": sha256_file(args.answers),
        "benchmark": manifest["benchmark"],
        "manifest_sha256": sha256_file(args.manifest),
        "official_revision": manifest["code"]["revision"],
        "selection": {"group": args.group or "all", "task": args.task or "all"},
        "questions": len(questions),
        "result_sha256": sha256_file(args.out),
        "judge_attempt_ledger_sha256": sha256_file(judge_ledger_path),
        "judge_attempts_sha256": judge_snapshot["attempts_sha256"],
        "judge_provider_attempts": judge_snapshot["provider_attempts"],
        "judge_reported_cost_usd": judge_snapshot["reported_cost_usd"],
        "judge_models": manifest["native_scorer"]["judge_models"],
        "meter_source_sha256": sha256_file(ROOT / "scripts" / "provider_attempts.py"),
        "subquestions": subquestions,
        "valid_judges_per_subquestion": 3,
    }
    atomic_json(proof_path, proof)


if __name__ == "__main__":
    main()
