from __future__ import annotations

import importlib.util
import hashlib
import json
from argparse import Namespace
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_memora_fama.py"
GENERATOR = ROOT / "scripts" / "generate_memora_memphant_answers.py"
LOCK = ROOT / "benchmarks" / "manifests" / "memora.lock.json"
GENERATION_LOCK = ROOT / "benchmarks" / "manifests" / "memora_generation.v1.json"
READER_LATTICE = ROOT / "benchmarks" / "manifests" / "reader_lattices.v1.json"
FIXTURE = ROOT / "tests" / "fixtures" / "memora_generation_small.json"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def open_test_ledger(module, path: Path, screen: str = "test"):
    return module.ProviderAttemptLedger(
        path, hashlib.sha256(b"fp").hexdigest(), screen, 1_000_000_000_000, 0
    )


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def direct_source(unit_id: str, body: str = "memory") -> dict:
    return {
        "tenant_id": "tenant-1", "unit_id": unit_id,
        "memory_unit_body": body, "source_episode_id": f"episode-{unit_id}",
        "source_episode_body": "[session 0001]\nuser: source",
    }


def test_memora_lock_pins_release_dataset_license_and_native_scorer() -> None:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))

    assert lock["benchmark"] == "Memora/FAMA"
    assert lock["code"] == {
        "repo": "https://github.com/geniesinc/Memora.git",
        "revision": "a6493188efc836d6511ed5e4163fe3ba87da30ff",
    }
    assert lock["dataset"]["session_count"] == 27_614
    assert lock["dataset"]["question_count"] == 600
    assert lock["dataset"]["evaluation_subquestion_count"] == 6_415
    assert lock["dataset"]["file_count"] == 27_645
    assert lock["dataset"]["tree_sha256"] == (
        "12d63b7d86d8d1751ab4da1f282c5a0729a93ef87bd5ef720ccee85cffd47d58"
    )
    assert lock["license"] == {
        "file": "LICENSE",
        "sha256": "c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4",
        "spdx": "Apache-2.0",
    }
    native = lock["native_scorer"]
    assert native["entrypoint"] == "evals/agent_eval/memory_to_answer.py"
    assert native["strict_three_judge_required"] is True
    assert native["judge_models"] == {
        "anthropic": "anthropic/claude-haiku-4.5",
        "google": "google/gemini-2.5-flash",
        "openai": "openai/gpt-4.1",
    }
    assert "evals/agent_eval/memory_to_answer.py" in native["files"]
    assert "evals/model_eval/api_client.py" in native["files"]


def test_generation_plan_is_chronological_and_seals_gold() -> None:
    generator = load(GENERATOR, "generate_memora")
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    plan = generator.build_group_plan(
        "weekly",
        fixture["persona"],
        fixture["sessions"],
        fixture,
    )

    assert [session["session_id"] for session in plan["sessions"]] == [1, 2]
    assert plan["date_range"] == {
        "from": "2025-06-01T00:00:00Z",
        "to": "2025-06-03T00:00:00Z",
    }
    assert plan["queries"] == [
        {
            "period": "weekly",
            "persona": "software_engineer",
            "question_id": "fixture_q1",
            "question": "Which language do I use now?",
            "question_date": "2025-06-07",
            "task_type": "Remembering",
        }
    ]
    serialized = json.dumps(plan)
    assert "DO_NOT_EXPOSE" not in serialized
    for forbidden in (
        "operation_details",
        "share_memory",
        "memory_evidence",
        "forgetting_evidence",
        "evaluation",
    ):
        assert forbidden not in serialized


def test_formatter_keeps_memory_neutral_distractors_but_strips_oracle_labels() -> None:
    generator = load(GENERATOR, "generate_memora_full_conversation")
    session = {
        "session_id": 103,
        "session_type": "memory_introduction",
        "operation": "add",
        "operation_details": {"gold": "hidden"},
        "date": "2025-06-05",
        "persona": "software_engineer",
        "conversation": [
            {
                "turn": 1,
                "speaker": "user_agent",
                "message": "People have 70,000 thoughts per day.",
                "share_memory": False,
            },
            {
                "turn": 2,
                "speaker": "user_agent",
                "message": "I spent $6.80 on breakfast.",
                "share_memory": True,
            },
        ],
    }

    formatted = generator.format_session(session, "weekly", "software_engineer")

    assert [turn["turn"] for turn in formatted["turns"]] == [1, 2]
    assert [turn["message"] for turn in formatted["turns"]] == [
        "People have 70,000 thoughts per day.",
        "I spent $6.80 on breakfast.",
    ]
    assert formatted["body"].index("70,000 thoughts") < formatted["body"].index("$6.80")
    serialized = json.dumps(formatted)
    assert "share_memory" not in serialized
    assert "operation_details" not in serialized
    assert "memory_introduction" not in serialized


def test_generation_plan_rejects_date_range_that_does_not_match_sessions() -> None:
    generator = load(GENERATOR, "generate_memora_date_range")
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    fixture["date_range"]["end_date"] = "2025-06-07"
    with pytest.raises(ValueError, match="date_range does not match its sessions"):
        generator.build_group_plan(
            "weekly", fixture["persona"], fixture["sessions"], fixture
        )


def test_generation_rejects_non_dialogue_and_duplicate_question_identity() -> None:
    generator = load(GENERATOR, "generate_memora_invalid")
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    fixture["sessions"][0]["conversation"][0]["speaker"] = "system"
    with pytest.raises(ValueError, match="speaker"):
        generator.build_group_plan("weekly", fixture["persona"], fixture["sessions"], fixture)

    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    fixture["questions"]["remembering"].append(
        dict(fixture["questions"]["remembering"][0])
    )
    with pytest.raises(ValueError, match="duplicate question identity"):
        generator.build_group_plan("weekly", fixture["persona"], fixture["sessions"], fixture)


def test_answers_pair_exactly_once_and_contain_no_gold() -> None:
    runner = load(RUNNER, "run_memora")
    identities = {
        ("weekly", "software_engineer", "one"),
        ("weekly", "software_engineer", "two"),
    }
    def answer(question_id: str) -> dict:
        evidence = [{"rank": 1, "body": "permitted dialogue"}]
        return {
            "period": "weekly",
            "persona": "software_engineer",
            "question_id": question_id,
            "question": "q",
            "question_date": "2025-06-07",
            "task_type": "Remembering",
            "answer": "a",
            "evidence": evidence,
            "trace": {
                "trace_id": "trace",
                "degraded": False,
                "evidence_sha256": runner.sha256_json(evidence),
            },
        }
    runner.verify_answers(identities, [answer("one"), answer("two")])
    with pytest.raises(ValueError, match="exactly match"):
        runner.verify_answers(identities, [answer("one")])
    with pytest.raises(ValueError, match="duplicate"):
        runner.verify_answers(identities, [answer("one"), answer("one")])
    bad = answer("two")
    bad["evaluation"] = {}
    with pytest.raises(ValueError, match="keys"):
        runner.verify_answers(identities, [answer("one"), bad])


def test_native_scorer_group_selection_remains_exactly_paired() -> None:
    runner = load(RUNNER, "run_memora_group")
    questions = [
        {
            "period": "weekly",
            "persona": "software_engineer",
            "question_id": "one",
            "evaluation": {"evaluation_questions": [{}, {}]},
        },
        {
            "period": "monthly",
            "persona": "software_engineer",
            "question_id": "two",
            "evaluation": {"evaluation_questions": [{}]},
        },
    ]

    selected, identities, subquestions = runner.select_group(
        questions, "weekly/software_engineer"
    )

    assert [row["question_id"] for row in selected] == ["one"]
    assert identities == {("weekly", "software_engineer", "one")}
    assert subquestions == 2
    with pytest.raises(ValueError, match="not found"):
        runner.select_group(questions, "quarterly/software_engineer")


def test_native_judge_meter_requires_exact_three_fresh_attempts_per_subquestion() -> None:
    runner = load(RUNNER, "run_memora_judge_meter")
    models = {
        "openai": "openai/gpt-4.1",
        "anthropic": "anthropic/claude-haiku-4.5",
        "google": "google/gemini-2.5-flash",
    }
    attempts = []
    for index, model in enumerate(models.values(), 1):
        request_hash = f"{index:064x}"
        result_hash = f"{index + 10:064x}"
        response = {
            "response_id": f"response-{index}",
            "requested_model": model,
            "served_model": model,
            "provider": "fixture-provider",
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "cost": 0.01,
            },
            "elapsed_seconds": 0.1,
            "retry_index": 0,
            "request_sha256": request_hash,
            "result_sha256": result_hash,
            "parse_status": "provider_response_validated",
        }
        attempts.append({
            "attempt_id": index,
            "request_key": request_hash,
            "retry_index": 0,
            "start": {
                "requested_model": model,
                "request_sha256": request_hash,
                "retry_index": 0,
            },
            "status": "result",
            "result": {"response": response},
            "error": None,
        })
    ledger = {
        "provider_attempts": 3,
        "priced_provider_attempts": 3,
        "unpriced_provider_attempts": 0,
        "reported_cost_usd": 0.03,
        "attempts_sha256": runner.sha256_json(attempts),
        "attempts": attempts,
    }

    runner.verify_judge_ledger(ledger, expected_subquestions=1, judge_models=models)
    ledger["attempts"] = attempts[:-1]
    ledger["provider_attempts"] = 2
    ledger["priced_provider_attempts"] = 2
    ledger["attempts_sha256"] = runner.sha256_json(ledger["attempts"])
    with pytest.raises(RuntimeError, match="attempt count"):
        runner.verify_judge_ledger(ledger, expected_subquestions=1, judge_models=models)


def test_native_judges_disable_internal_retries_and_fail_closed() -> None:
    runner = load(RUNNER, "run_memora_strict_judges")
    calls = []

    class Client:
        def generate_response(self, **kwargs):
            calls.append(kwargs)
            return "response"

    class Evaluator:
        judge_clients = {"openai": Client()}
        def _evaluate_with_single_judge(self, *_args, **_kwargs):
            return {"llm_answer": "error", "is_correct": False, "error": "boom"}

    evaluator = Evaluator()
    runner.harden_native_evaluator(evaluator)
    evaluator.judge_clients["openai"].generate_response(
        system_prompt="system", user_prompt="user", temperature=0.0,
    )
    assert calls == [{
        "system_prompt": "system", "user_prompt": "user", "temperature": 0.0,
        "max_retries": 1,
    }]
    with pytest.raises(RuntimeError, match="official Memora judge failed"):
        evaluator._evaluate_with_single_judge(None, None, None, None, None)


def test_reasoning_diagnostic_keeps_full_ingest_plan_but_filters_questions() -> None:
    generator = load(GENERATOR, "generate_memora_task_selection")
    runner = load(RUNNER, "run_memora_task_selection")
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    plan = generator.build_group_plan(
        "weekly", fixture["persona"], fixture["sessions"], fixture
    )
    reasoning = dict(
        plan["queries"][0], question_id="reasoning_q", task_type="Reasoning"
    )
    plan["queries"].append(reasoning)

    selected_plans = generator.select_task([plan], "reasoning")
    assert [session["session_id"] for session in selected_plans[0]["sessions"]] == [1, 2]
    assert [query["question_id"] for query in selected_plans[0]["queries"]] == [
        "reasoning_q"
    ]
    assert len(plan["queries"]) == 2, "selection must not mutate the sealed plan"

    official = [
        dict(query, evaluation={"evaluation_questions": [{}, {}]})
        for query in plan["queries"]
    ]
    questions, identities, subquestions = runner.select_task(official, "reasoning")
    assert [question["question_id"] for question in questions] == ["reasoning_q"]
    assert identities == {("weekly", fixture["persona"], "reasoning_q")}
    assert subquestions == 2


def test_native_results_fail_closed_unless_all_three_judges_succeed() -> None:
    runner = load(RUNNER, "run_memora_native")
    result = {
        "question_id": "q",
        "evaluation_questions": [
            {
                    "evaluation_result": {
                        "is_correct": True,
                        "num_judges": 3,
                    "num_valid_judges": 3,
                    "per_judge_results": {
                        "openai": {"llm_answer": "yes", "is_correct": True},
                        "anthropic": {"llm_answer": "yes", "is_correct": True},
                        "google": {"llm_answer": "no", "is_correct": False},
                    },
                }
            }
        ],
    }
    runner.verify_native_results([result], expected_questions=1, expected_subquestions=1)
    result["evaluation_questions"][0]["evaluation_result"]["num_valid_judges"] = 2
    with pytest.raises(RuntimeError, match="three valid judges"):
        runner.verify_native_results([result], expected_questions=1, expected_subquestions=1)

    result["evaluation_questions"][0]["evaluation_result"]["num_valid_judges"] = 3
    result["evaluation_questions"][0]["evaluation_result"]["per_judge_results"]["google"] = {
        "llm_answer": "unclear", "is_correct": False, "parse_error": "ambiguous"
    }
    with pytest.raises(RuntimeError, match="invalid result"):
        runner.verify_native_results([result], expected_questions=1, expected_subquestions=1)


def test_generation_lock_freezes_accuracy_first_reader_and_retrieval() -> None:
    lock = json.loads(GENERATION_LOCK.read_text(encoding="utf-8"))
    assert lock["protocol"] == "memora-memphant-generation-v1"
    assert lock["reader"] == {
        "candidates": [
            {
                "canonical_model_snapshot": "openai/gpt-5.6-luna-pro-20260709",
                "prior_screen": "normal-development-reader",
                "reasoning_effort": "high",
                "requested_model": "openai/gpt-5.6-luna-pro",
            },
            {
                "prior_screen": "rejected-contract-invalid",
                "reasoning_effort": "high",
                "requested_model": "google/gemini-3.5-flash",
            },
        ],
        "selection": "paired-memora-capability-cost-screen",
    }
    assert lock["reader_lattice_sha256"] == file_sha(READER_LATTICE)
    assert lock["retrieval"] == {
        "aggregation_window": "official_group_date_range",
        "budget_tokens": 8192,
        "cross_rerank": False,
        "embed_model": "small",
        "limit": 10,
        "mode": "deep",
    }
    assert lock["temporal_contract"] == {
        "evaluation_snapshot": "final state after full period/persona ingest and reflection drain",
        "official_question_date_semantics": "context only, not retrieval filtering",
        "question_date_use": "reader prompt context only",
        "session_date_use": "chronological ordering, retained dialogue text, and the explicit half-open group aggregation window",
    }
    assert lock["allowed_session_fields"] == ["date", "persona", "session_id"]
    assert lock["allowed_turn_fields"] == ["message", "speaker", "turn"]
    assert lock["forbidden_fields"] == [
        "evaluation",
        "forgetting_evidence",
        "memory_evidence",
        "operation",
        "operation_details",
        "session_type",
        "share_memory",
    ]


def test_executor_freezes_runtime_behavior_environment(monkeypatch) -> None:
    generator = load(GENERATOR, "generate_memora_env")
    monkeypatch.setenv("MEMPHANT_CROSS_RERANK", "1")
    monkeypatch.setenv("MEMPHANT_RECALL_POOL_DEPTH", "999")
    monkeypatch.setenv("MEMPHANT_UNEXPECTED_BEHAVIOR", "on")
    monkeypatch.setenv("OPENROUTER_API_KEY", "keep-me")

    effective = generator.freeze_behavior_environment()

    assert effective == generator.BEHAVIOR_ENV
    assert {key: value for key, value in generator.os.environ.items() if key.startswith("MEMPHANT_")} == generator.BEHAVIOR_ENV
    assert generator.os.environ["OPENROUTER_API_KEY"] == "keep-me"


def test_model_arm_binds_reader_and_accuracy_first_extractor_reasoning(
    tmp_path: Path, monkeypatch
) -> None:
    generator = load(GENERATOR, "generate_memora_model_arm")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-not-real")
    generator.configure_model("google/gemini-3.5-flash")
    assert generator.MODEL == "google/gemini-3.5-flash"
    assert generator.STRUCTURED_STATE_MODEL == "google/gemini-3.5-flash"
    assert generator.BEHAVIOR_ENV["MEMPHANT_STRUCTURED_STATE_MODEL"] == generator.MODEL
    assert generator.BEHAVIOR_ENV["MEMPHANT_STRUCTURED_STATE_REASONING_EFFORT"] == "high"
    assert generator.BEHAVIOR_ENV["MEMPHANT_STRUCTURED_STATE_CONCURRENCY"] == "4"
    assert "MEMPHANT_STRUCTURED_STATE_PREFETCH_CONCURRENCY" not in generator.BEHAVIOR_ENV
    assert generator.OpenRouterReader(tmp_path, 0).model == "google/gemini-3.5-flash"
    generator.verify_generation_lock(GENERATION_LOCK)

    generator.configure_model("openai/gpt-5.6-luna-pro")
    assert "MEMPHANT_STRUCTURED_STATE_REASONING_EFFORT" not in generator.BEHAVIOR_ENV
    generator.verify_generation_lock(GENERATION_LOCK)
    generator.configure_model(
        "openai/gpt-5.6-luna-pro", "google/gemini-3.5-flash"
    )
    assert generator.MODEL == "openai/gpt-5.6-luna-pro"
    assert generator.STRUCTURED_STATE_MODEL == "google/gemini-3.5-flash"
    assert generator.BEHAVIOR_ENV["MEMPHANT_STRUCTURED_STATE_REASONING_EFFORT"] == "high"


def test_executor_rejects_unpinned_or_unpriced_reader_response() -> None:
    generator = load(GENERATOR, "generate_memora_reader_proof")
    valid = {
        "response_id": "reader-valid",
        "requested_model": "openai/gpt-5.6-luna-pro",
        "served_model": "openai/gpt-5.6-luna-pro-20260709",
        "provider": "OpenAI",
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "cost": 0.01,
        },
        "elapsed_seconds": 0.1,
        "retry_index": 0,
        "parse_status": "provider_response_validated",
        "request_sha256": "1" * 64,
        "result_sha256": "2" * 64,
    }
    assert generator.validate_reader_metadata(valid) == valid
    with pytest.raises(RuntimeError, match="served model"):
        generator.validate_reader_metadata(valid | {"served_model": "other/model"})
    missing_cost = dict(valid, usage={key: value for key, value in valid["usage"].items() if key != "cost"})
    with pytest.raises(RuntimeError, match="usage/cost"):
        generator.validate_reader_metadata(missing_cost)
    null_cost = dict(valid, usage=dict(valid["usage"], cost=None))
    with pytest.raises(RuntimeError, match="usage/cost"):
        generator.validate_reader_metadata(null_cost)


def test_provider_attempt_ledger_survives_cache_before_answer_checkpoint(
    tmp_path: Path, monkeypatch
) -> None:
    generator = load(GENERATOR, "generate_memora_attempt_ledger")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-not-real")
    payload = {
        "id": "reader-response-1",
        "model": "openai/gpt-5.6-luna-pro",
        "openrouter_metadata": {
            "endpoints": {"available": [{"provider": "OpenAI", "selected": True}]}
        },
        "usage": {
            "prompt_tokens": 10, "completion_tokens": 5,
            "total_tokens": 15, "cost": 0.02,
        },
        "choices": [{"message": {"content": '{"notes":"","answer":"Rust","abstain":false}'}}],
    }
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def read(self): return json.dumps(payload).encode()
    monkeypatch.setattr(
        generator.run_reader.urllib.request, "urlopen",
        lambda request, timeout=None: Response(),
    )
    monkeypatch.setattr(
        generator.run_reader,
        "openrouter_generation_lookup",
        lambda _api_key: lambda _response_id: {
            "model": "openai/gpt-5.6-luna-pro-20260709",
            "provider_name": "OpenAI",
            "tokens_prompt": 10,
            "tokens_completion": 5,
            "total_cost": 0.02,
        },
    )
    ledger_path = tmp_path / "attempts.json"
    cache_dir = tmp_path / "cache"
    query = {
        "period": "weekly", "persona": "software_engineer",
        "question_id": "q", "question": "Which language?",
        "question_date": "2025-06-07", "task_type": "Remembering",
    }
    evidence = [{"rank": 1, "body": "Use Rust now."}]

    first_ledger = open_test_ledger(generator, ledger_path, "first")
    first = generator.OpenRouterReader(cache_dir, 4)
    first.set_attempt_ledger(first_ledger)
    first.answer(query, evidence)
    assert first_ledger.snapshot()["provider_attempts"] == 1
    assert first_ledger.snapshot()["reported_cost_usd"] == 0.02

    # Simulate process death after ReaderCli atomically cached the paid response,
    # but before execute_groups could checkpoint the answer/proof row.
    first_ledger.close()
    resumed_ledger = open_test_ledger(generator, ledger_path, "resumed")
    resumed = generator.OpenRouterReader(cache_dir, 4)
    resumed.set_attempt_ledger(resumed_ledger)
    answer, facts = resumed.answer(query, evidence)
    assert answer == "Rust"
    assert facts["cache_hit"] is True
    assert facts["cost_usd"] == 0
    assert resumed_ledger.snapshot()["provider_attempts"] == 1
    assert resumed_ledger.snapshot()["reported_cost_usd"] == 0.02

    null_ledger = open_test_ledger(generator, tmp_path / "null-cost.json")
    null_ledger.record("start", "other-cache-key", {"max_liability_nanos": 100_000_000})
    null_payload = dict(payload, usage=dict(payload["usage"], cost=None))
    metadata = {
        "model": null_payload["model"], "provider": "OpenAI",
        "usage": null_payload["usage"],
    }
    null_ledger.record("result", "other-cache-key", {"response": metadata})
    assert null_ledger.snapshot()["reported_cost_usd"] == 0
    assert null_ledger.snapshot()["unpriced_provider_attempts"] == 1
    with pytest.raises(RuntimeError, match="interrupted or unpriced"):
        generator.validate_provider_attempt_ledger(null_ledger.snapshot())

    zero_ledger = open_test_ledger(generator, tmp_path / "zero-usage.json")
    zero_ledger.record("start", "zero-cache-key", {"max_liability_nanos": 100_000_000})
    zero_payload = {
        "model": generator.MODEL,
        "provider": "Google",
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost": 0},
    }
    zero_ledger.record("result", "zero-cache-key", {"response": zero_payload})
    assert zero_ledger.snapshot()["priced_provider_attempts"] == 0
    assert zero_ledger.snapshot()["unpriced_provider_attempts"] == 1
    with pytest.raises(RuntimeError, match="interrupted or unpriced"):
        generator.validate_provider_attempt_ledger(zero_ledger.snapshot())

    interrupted = open_test_ledger(generator, tmp_path / "interrupted.json")
    interrupted.record("start", "never-finished", {"max_liability_nanos": 100_000_000})
    with pytest.raises(RuntimeError, match="interrupted or unpriced"):
        generator.validate_provider_attempt_ledger(interrupted.snapshot())


def test_native_scorer_requires_complete_generation_artifact(tmp_path: Path) -> None:
    runner = load(RUNNER, "run_memora_complete_artifact")
    path = tmp_path / "answers.json"
    path.write_text(json.dumps({"summary": {"complete": False}, "data": []}))
    with pytest.raises(ValueError, match="complete sealed"):
        runner.load_answers(path)
    path.write_text(json.dumps({"summary": {"complete": True}, "data": []}))
    assert runner.load_answers(path) == []


def test_reader_abstention_is_preserved_as_a_scorable_answer(tmp_path: Path) -> None:
    generator = load(GENERATOR, "generate_memora_abstention")
    metadata = {
        "response_id": "reader-abstain",
        "requested_model": "openai/gpt-5.6-luna-pro",
        "served_model": "openai/gpt-5.6-luna-pro-20260709",
        "provider": "OpenAI",
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 2,
            "total_tokens": 12,
            "cost": 0.01,
        },
        "elapsed_seconds": 0.1,
        "retry_index": 0,
        "parse_status": "provider_response_validated",
        "request_sha256": "1" * 64,
        "result_sha256": "2" * 64,
    }

    class Cli:
        fresh_calls = 0
        cached_calls = 0
        provider_attempts = 0
        provider_attempt_log = [{"response": metadata}]
        last_call_metadata = metadata

        def call(self, *_args):
            self.fresh_calls += 1
            self.provider_attempts += 1
            return '{"notes":"missing evidence","answer":null,"abstain":true}'

    reader = object.__new__(generator.OpenRouterReader)
    reader.cli = Cli()
    reader.last_metadata = None
    answer, proof = reader.answer(
        {
            "period": "weekly",
            "persona": "software_engineer",
            "question_id": "q",
            "question": "How much did I spend?",
            "question_date": "2025-06-07",
        },
        [],
    )

    assert answer == "I cannot answer from the retrieved memory."
    assert proof["abstained"] is True


def test_reader_evidence_canonicalizes_runtime_episode_ids() -> None:
    generator = load(GENERATOR, "generate_memora_stable_prompt")
    first = [{
        "rank": 1,
        "body": "[episode 019f5f11-3337-7041-b814-4abd38880094] [kind user] [turns 1-4]\nUser: hello",
    }]
    replay = [{
        "rank": 1,
        "body": "[episode 019f6000-aaaa-7bbb-8ccc-1234567890ab] [kind user] [turns 1-4]\nUser: hello",
    }]

    assert generator.render_reader_evidence(first) == generator.render_reader_evidence(replay)
    assert "episode-1" in generator.render_reader_evidence(first)
    assert "019f5f11" not in generator.render_reader_evidence(first)


def test_checkpoint_rejects_deleted_or_divergent_attempt_ledger(tmp_path: Path) -> None:
    generator = load(GENERATOR, "generate_memora_ledger_prefix")
    ledger_path = tmp_path / "attempts.json"
    ledger = open_test_ledger(generator, ledger_path)
    ledger.record("start", "cache-a", {
        "retry_index": 0,
        "requested_model": "openai/gpt-5.6-luna-pro",
        "request_sha256": "1" * 64,
        "max_liability_nanos": 100_000_000,
    })
    ledger.record("result", "cache-a", {"response": {
        "response_id": "cache-a-response",
        "requested_model": "openai/gpt-5.6-luna-pro",
        "served_model": "openai/gpt-5.6-luna-pro-20260709",
        "provider": "OpenAI",
        "usage": {
            "prompt_tokens": 1, "completion_tokens": 1,
            "total_tokens": 2, "cost": 0.001,
        },
        "elapsed_seconds": 0.1, "retry_index": 0,
        "parse_status": "provider_response_validated",
        "request_sha256": "1" * 64,
        "result_sha256": "2" * 64,
    }})
    output, proof = generator.output_objects(
        [], [], [], [], "fp", {"fixture": True}, ledger.snapshot()
    )
    checkpoint = tmp_path / "checkpoint.json"
    answers = tmp_path / "answers.json"
    proof_path = tmp_path / "proof.json"
    generator.write_checkpoint(checkpoint, answers, proof_path, output, proof)

    ledger.close()
    ledger_path.unlink()
    deleted = open_test_ledger(generator, ledger_path)
    with pytest.raises(ValueError, match="truncated"):
        generator.execute_groups(
            [], object(), object(), answers, proof_path, checkpoint,
            generation_fingerprint="fp", runtime_proof={"fixture": True},
            attempt_ledger=deleted,
        )

    divergent_path = tmp_path / "divergent.json"
    divergent = open_test_ledger(generator, divergent_path)
    divergent.record("start", "cache-b", {"max_liability_nanos": 100_000_000})
    divergent.record("result", "cache-b", {"error": "offline"})
    with pytest.raises(ValueError, match="diverged"):
        generator.execute_groups(
            [], object(), object(), answers, proof_path, checkpoint,
            generation_fingerprint="fp", runtime_proof={"fixture": True},
            attempt_ledger=divergent,
        )


def test_run_fingerprint_binds_output_and_attempt_ledger_paths(tmp_path: Path) -> None:
    generator = load(GENERATOR, "generate_memora_path_fingerprint")
    runtime = {"fixture": True}
    base = generator.run_fingerprint(
        runtime, "weekly/software_engineer", tmp_path / "a.json",
        tmp_path / "a.attempts.json", tmp_path / "cache-a",
    )
    assert base != generator.run_fingerprint(
        runtime, "weekly/software_engineer", tmp_path / "b.json",
        tmp_path / "a.attempts.json", tmp_path / "cache-a",
    )
    assert base != generator.run_fingerprint(
        runtime, "weekly/software_engineer", tmp_path / "a.json",
        tmp_path / "b.attempts.json", tmp_path / "cache-a",
    )
    assert base != generator.run_fingerprint(
        runtime, "weekly/software_engineer", tmp_path / "a.json",
        tmp_path / "a.attempts.json", tmp_path / "cache-b",
    )


def test_fresh_ledger_rejects_populated_cache_but_priced_resume_allows_it(
    tmp_path: Path,
) -> None:
    generator = load(GENERATOR, "generate_memora_cache_contract")
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "response.json").write_text("{}")
    fresh = open_test_ledger(generator, tmp_path / "fresh.json")
    with pytest.raises(ValueError, match="empty reader cache"):
        generator.validate_reader_cache_contract(cache, fresh, ledger_existed=False)

    path = tmp_path / "resume.json"
    resumed = open_test_ledger(generator, path)
    resumed.record("start", "cache-key", {
        "retry_index": 0,
        "requested_model": "openai/gpt-5.6-luna-pro",
        "request_sha256": "1" * 64,
        "max_liability_nanos": 100_000_000,
    })
    resumed.record("result", "cache-key", {"response": {
        "response_id": "cache-response", "requested_model": "openai/gpt-5.6-luna-pro",
        "served_model": "openai/gpt-5.6-luna-pro-20260709", "provider": "OpenAI",
        "usage": {
            "prompt_tokens": 1, "completion_tokens": 1,
            "total_tokens": 2, "cost": 0.001,
        },
        "elapsed_seconds": 0.1, "retry_index": 0,
        "parse_status": "provider_response_validated",
        "request_sha256": "1" * 64,
        "result_sha256": "2" * 64,
    }})
    generator.validate_reader_cache_contract(cache, resumed, ledger_existed=True)


def test_runtime_contract_is_complete_and_reaches_output_proof(tmp_path: Path) -> None:
    generator = load(GENERATOR, "generate_memora_runtime_contract")
    binaries = {}
    for name in ("server", "worker", "cli"):
        path = tmp_path / name
        path.write_bytes(name.encode())
        binaries[f"{name}_bin"] = path
    args = Namespace(
        generation_lock=GENERATION_LOCK,
        max_provider_attempts=60,
        behavior_environment=dict(generator.BEHAVIOR_ENV),
        **binaries,
    )
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    generation = json.loads(GENERATION_LOCK.read_text(encoding="utf-8"))

    runtime = generator.runtime_contract(args, lock, generation)

    assert runtime is not None
    assert set(runtime) == {
        "source", "dataset", "manifest_sha256", "generation_manifest_sha256",
        "reader_lattice_sha256", "binaries", "openapi_sha256", "config_sha256",
        "prompt_sha256", "model_sha256", "behavior_environment", "harness_sha256",
    }
    assert set(runtime["binaries"]) == {"server", "worker", "cli"}
    assert all(len(value["sha256"]) == 64 for value in runtime["binaries"].values())
    output, proof = generator.output_objects([], [], [], [], "fp", runtime)
    assert output["summary"]["complete"] is True
    assert proof["runtime"] == runtime


def test_packaged_runtime_resolves_context_and_sends_subject_bound_episode(monkeypatch) -> None:
    generator = load(GENERATOR, "generate_memora_source_kind")
    payloads = []
    binding = {
        "subject_id": "subject",
        "scope_id": "scope",
        "actor_id": "actor",
        "agent_node_id": "agent-node",
        "subject_generation": 0,
        "agent_level": 0,
        "policy_revision": "policy-v1",
    }

    class Client:
        tenant_id = "tenant"

        def __init__(self, *_args):
            pass

        def put(self, path, payload):
            payloads.append((path, payload))
            return binding

        def post(self, path, payload):
            payloads.append((path, payload))
            return {"episode_id": "episode"}

    monkeypatch.setattr(
        generator.gate_runtime,
        "provision_tenant",
        lambda *_args: ("tenant", "mk_fixture"),
    )
    monkeypatch.setattr(generator.gate_runtime, "ApiClient", Client)
    runtime = object.__new__(generator.PackagedRuntime)
    runtime.args = Namespace(cli_bin="cli", database_url="postgres://fixture", port=39433)
    runtime.client = None
    runtime.bank_groups = None
    runtime.bank_replay = False
    runtime.open_group({
        "scope": {"period": "weekly", "persona": "software_engineer"},
        "date_range": {"from": "2026-07-01T00:00:00Z", "to": "2026-07-08T00:00:00Z"},
    })
    runtime.retain({"session_id": 1, "date": "2026-07-13", "body": "User: hello"})

    assert payloads[0] == (
        "/v1/context-bindings/memora-weekly-software_engineer",
        {
            "subject": {"external_ref": "memora:subject:weekly-software_engineer", "kind": "user"},
            "actor": {"external_ref": "memora:actor:weekly-software_engineer", "kind": "system"},
            "scope": {
                "external_ref": "memora:scope:weekly-software_engineer",
                "kind": "user_root",
                "parent_external_ref": None,
            },
            "agent_node": {
                "external_ref": "memora:agent:weekly-software_engineer",
                "parent_external_ref": None,
            },
            "access_policies": [],
        },
    )
    assert payloads[1][0] == "/v1/episodes"
    assert payloads[1][1] == {
        "subject_id": "subject",
        "scope_id": "scope",
        "actor_id": "actor",
        "agent_node_id": "agent-node",
        "subject_generation": 0,
        "source_ref": "memora:session:0001",
        "observed_at": "2026-07-13T00:00:00Z",
        "payload": {"episode": {"source_kind": "user", "body": "User: hello"}},
    }


def test_packaged_runtime_routes_extractor_attempts_into_proof(tmp_path: Path, monkeypatch) -> None:
    generator = load(GENERATOR, "generate_memora_extractor_proof")
    ledger = tmp_path / "extractor.jsonl"
    proof = {}
    calls = []
    monkeypatch.setattr(
        generator.gate_runtime,
        "drain_worker",
        lambda worker, database, embed, structured_attempt_ledger=None,
        structured_requested_model=None: (
            calls.append((
                worker, database, embed, structured_attempt_ledger,
                structured_requested_model,
            )) or 2
        ),
    )
    expected = {
        "provider_attempts": 2,
        "reported_cost_usd": 0.01,
        "cost_status": "all_successful_responses_priced",
    }
    monkeypatch.setattr(
        generator.gate_runtime,
        "structured_extractor_attempt_summary",
        lambda path, model, **_kwargs: expected if (path, model) == (ledger, generator.STRUCTURED_STATE_MODEL) else None,
    )
    runtime = object.__new__(generator.PackagedRuntime)
    runtime.args = Namespace(worker_bin=tmp_path / "worker", database_url="postgres://fixture")
    runtime.runtime_proof = proof
    runtime.extractor_attempt_ledger = ledger
    runtime.extractor_attempt_summary = None

    assert runtime.drain() == 2
    assert calls == [(
        str(tmp_path / "worker"), "postgres://fixture", generator.EMBED_MODEL,
        ledger, generator.STRUCTURED_STATE_MODEL,
    )]
    assert runtime.extractor_attempt_summary == expected
    assert proof["structured_extractor"] == expected


def test_executor_runs_write_drain_recall_reader_and_resumes(tmp_path: Path) -> None:
    generator = load(GENERATOR, "generate_memora_executor")
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    plan = generator.build_group_plan(
        "weekly", fixture["persona"], fixture["sessions"], fixture
    )

    class FakeRuntime:
        def __init__(self) -> None:
            self.events = []
            self.sessions = []

        def open_group(self, group: dict) -> None:
            self.events.append((
                "open", group["scope"]["period"], group["scope"]["persona"],
                group["date_range"],
            ))

        def retain(self, session: dict) -> None:
            self.sessions.append(session)
            self.events.append(("retain", session["session_id"]))

        def drain(self) -> int:
            self.events.append(("drain", len(self.sessions)))
            return len(self.sessions)

        def recall(self, query: dict) -> tuple[list[dict], str, dict]:
            self.events.append(("recall", query["question_id"]))
            assert [row["session_id"] for row in self.sessions] == [1, 2]
            evidence = [{"rank": 1, "unit_id": "u2", "body": "Use Rust now."}]
            trace = {
                "id": "trace-1", "tenant_id": "tenant-1", "scope_id": "scope-1",
                "actor_id": "actor-1",
                "context_items": [{"unit_id": "u2", "derived_from_unit_ids": []}],
                "citations": [],
            }
            retrieval = {
                "trace": trace,
                "trace_sha256": generator.sha256_json(trace),
                "derived_sources": [],
                "derived_sources_sha256": generator.sha256_json([]),
                "direct_sources": [direct_source("u2")],
                "direct_sources_sha256": generator.sha256_json([direct_source("u2")]),
            }
            return evidence, "trace-1", retrieval

    class FakeReader:
        model = "openai/gpt-5.6-luna-pro"

        def __init__(self) -> None:
            self.calls = 0

        def answer(self, query: dict, evidence: list[dict]) -> tuple[str, dict]:
            self.calls += 1
            assert query["question_id"] == "fixture_q1"
            assert evidence[0]["body"] == "Use Rust now."
            return "Rust", {"cache_hit": False, "fresh_call": True, "usage": {}}

    checkpoint = tmp_path / "checkpoint.json"
    output = tmp_path / "answers.json"
    proof = tmp_path / "proof.json"
    runtime = FakeRuntime()
    reader = FakeReader()
    generator.execute_groups(
        [plan], runtime, reader, output, proof, checkpoint,
        generation_fingerprint="fixture-fingerprint", runtime_proof={"fixture": True},
    )

    answers = json.loads(output.read_text(encoding="utf-8"))
    assert answers["summary"]["complete"] is True
    assert answers["data"][0]["answer"] == "Rust"
    assert answers["data"][0]["trace"]["degraded"] is False
    generator.run_memora_fama.verify_answers(
        {("weekly", "software_engineer", "fixture_q1")}, answers["data"]
    )
    assert [event[0] for event in runtime.events] == [
        "open", "retain", "retain", "drain", "recall"
    ]
    assert runtime.events[0][3] == {
        "from": "2025-06-01T00:00:00Z",
        "to": "2025-06-03T00:00:00Z",
    }
    assert reader.calls == 1

    resumed_runtime = FakeRuntime()
    resumed_reader = FakeReader()
    generator.execute_groups(
        [plan], resumed_runtime, resumed_reader, output, proof, checkpoint,
        generation_fingerprint="fixture-fingerprint", runtime_proof={"fixture": True},
    )
    assert resumed_runtime.events == []
    assert resumed_reader.calls == 0

    bank_runtime = FakeRuntime()
    bank_runtime.bank_replay = True
    bank_runtime.sessions = plan["sessions"]
    bank_reader = FakeReader()
    generator.execute_groups(
        [plan], bank_runtime, bank_reader,
        tmp_path / "bank-answers.json",
        tmp_path / "bank-proof.json",
        tmp_path / "bank-checkpoint.json",
        generation_fingerprint="bank-fingerprint", runtime_proof={"fixture": True},
    )
    assert [event[0] for event in bank_runtime.events] == ["open", "recall"]
    assert bank_reader.calls == 1


def test_extraction_bank_uses_content_addressed_data_only_archive(
    tmp_path: Path, monkeypatch,
) -> None:
    generator = load(GENERATOR, "generate_memora_bank_dump")
    commands = []

    def run(command, label):
        commands.append((command, label))
        output = next(arg.split("=", 1)[1] for arg in command if arg.startswith("--file="))
        Path(output).write_bytes(b"bank")

    monkeypatch.setattr(generator, "run_postgres_command", run)
    monkeypatch.setattr(
        generator, "postgres_tool_identity",
        lambda *_args: {"major": 17, "server_major": 17, "version": "PostgreSQL 17"},
    )
    archive, digest, tool = generator.dump_extraction_bank("postgres://fixture", tmp_path)

    assert archive == tmp_path / f"{digest}.dump"
    assert digest == hashlib.sha256(b"bank").hexdigest()
    assert tool["major"] == 17
    command, label = commands[0]
    assert label == "dump"
    assert "--format=custom" in command
    assert "--data-only" in command
    assert "--schema=memphant" in command
    assert "--exclude-table-data=memphant.schema_migrations" in command
    assert "--exclude-table-data=memphant.api_key" in command


def test_extraction_bank_rejects_mismatched_postgres_tool_major(monkeypatch) -> None:
    generator = load(GENERATOR, "generate_memora_bank_pg_version")

    class Result:
        returncode = 0
        stdout = "pg_dump (PostgreSQL) 14.18\n"
        stderr = ""

    monkeypatch.setattr(generator.subprocess, "run", lambda *_args, **_kwargs: Result())
    monkeypatch.setattr(generator, "psql_json", lambda *_args: [{"major": 17}])

    with pytest.raises(ValueError, match="tool major 14.*server major 17"):
        generator.postgres_tool_identity("pg_dump", "postgres://fixture")


def test_extraction_bank_identity_ignores_reader_query_selection() -> None:
    generator = load(GENERATOR, "generate_memora_bank_plan_identity")
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    plan = generator.build_group_plan(
        "weekly", fixture["persona"], fixture["sessions"], fixture
    )
    subset = dict(plan, queries=[])

    assert generator.extraction_plan_sha256([plan]) == generator.extraction_plan_sha256([subset])


def test_retrieval_only_screen_proves_fresh_restore_with_bounded_displacement(
    tmp_path: Path,
) -> None:
    generator = load(GENERATOR, "generate_memora_bank_retrieval_screen")
    queries = [
        {
            "period": "weekly", "persona": "software_engineer",
            "question_id": "activity_food_total_163", "question": "food total",
        },
        {
            "period": "weekly", "persona": "software_engineer",
            "question_id": "unaffected", "question": "calendar",
        },
        {
            "period": "weekly", "persona": "software_engineer",
            "question_id": "goal_food_expenses_coffee_163_0",
            "question": "coffee goal",
        },
    ]
    plan = {"queries": queries}

    class Runtime:
        def __init__(self, aggregate: bool):
            self.aggregate = aggregate

        def open_group(self, _plan): pass
        def recall(self, query, *, aggregate=True, limit=generator.RECALL_LIMIT):
            assert aggregate is self.aggregate
            assert limit in {generator.RECALL_LIMIT, generator.RECALL_LIMIT - 1}
            ordinary = [{"rank": 1, "unit_id": "u1", "body": "ordinary"}]
            evidence = ordinary
            if query["question_id"] == "activity_food_total_163":
                ordinary = [
                    {
                        "rank": 1, "unit_id": "unrelated-goal",
                        "body": generator.GOAL_COMPANION_BODY_BY_QUESTION[
                            "goal_food_expenses_coffee_163_0"
                        ],
                    },
                    {"rank": 2, "unit_id": "u1", "body": "ordinary"},
                ]
                evidence = ordinary
            if aggregate and query["question_id"] == "activity_food_total_163":
                evidence = [
                    {
                        "rank": 1, "unit_id": "rollup",
                        "body": "quantity rollup quantity_event_v1/food_spending/food_spending "
                        "(usd); window=[2025-06-01T00:00:00Z,2025-06-08T00:00:00Z); "
                        "filter=all; total=1; average=1 (rounded to 6 decimal places when "
                        "needed); count=1; min=1; max=1",
                    },
                    *[
                        dict(item, rank=index)
                        for index, item in enumerate(ordinary, 2)
                    ],
                ]
            if aggregate and query["question_id"] == "goal_food_expenses_coffee_163_0":
                evidence = [
                    {
                        "rank": 1, "unit_id": "rollup",
                        "body": "quantity rollup quantity_event_v1/food_spending/food_spending "
                        "(usd); window=[2025-06-01T00:00:00Z,2025-06-08T00:00:00Z); "
                        "filter=expense_type=coffee; total=1; average=1 (rounded to 6 decimal "
                        "places when needed); count=1; min=1; max=1",
                    },
                    {
                        "rank": 2, "unit_id": "goal",
                        "body": "coffee_spending_goal item spending_limit: "
                        "{\"expense_type\":\"coffee\",\"frequency\":\"weekly\","
                        "\"target_amount\":\"30\"}",
                    },
                    {"rank": 3, "unit_id": "u1", "body": "ordinary"},
                ]
            return evidence, f"trace-{aggregate}", {"aggregate": aggregate}

    bank = {
        "archive_sha256": "a" * 64,
        "extractor_ledger_sha256": "b" * 64,
        "compiler_versions": {"compiler": "1"},
        "construction_runtime_sha256": "c" * 64,
        "manifest_sha256": "d" * 64,
    }
    baseline = generator.execute_retrieval_arm(
        [plan], Runtime(False), "baseline", "scratch-a", bank,
    )
    candidate = generator.execute_retrieval_arm(
        [plan], Runtime(True), "candidate", "scratch-b", bank,
    )
    report = generator.compare_retrieval_arms(
        baseline, candidate, tmp_path / "report.json", tmp_path / "proof.json",
    )

    assert report["eligible"] is True
    assert report["questions"] == 3
    assert [row["rollup_count"] for row in report["results"]] == [1, 0, 1]
    assert [row["goal_companion_count"] for row in report["results"]] == [0, 0, 1]
    proof = json.loads((tmp_path / "proof.json").read_text())
    assert proof["paid_calls"] == 0
    assert proof["fresh_restore_per_arm"] is True
    assert proof["scratch_database_identities"] == {
        "baseline": "scratch-a", "candidate": "scratch-b",
    }
    assert proof["extraction_bank"] == bank


def test_retrieval_only_screen_rejects_same_scratch_database(tmp_path: Path) -> None:
    generator = load(GENERATOR, "generate_memora_bank_same_scratch")
    bank = {
        "archive_sha256": "a" * 64,
        "extractor_ledger_sha256": "b" * 64,
        "compiler_versions": {"compiler": "1"},
        "construction_runtime_sha256": "c" * 64,
        "manifest_sha256": "d" * 64,
    }
    arm = {
        "arm": "baseline", "database_identity": "same", "extraction_bank": bank,
        "paid_calls": 0, "results": [],
    }

    with pytest.raises(RuntimeError, match="distinct fresh scratch databases"):
        generator.compare_retrieval_arms(
            arm, dict(arm, arm="candidate"),
            tmp_path / "report.json", tmp_path / "proof.json",
        )


def test_retrieval_only_orchestrator_launches_one_fresh_child_per_arm(
    tmp_path: Path, monkeypatch,
) -> None:
    generator = load(GENERATOR, "generate_memora_bank_orchestrator")
    bank = {
        "archive_sha256": "a" * 64, "extractor_ledger_sha256": "b" * 64,
        "compiler_versions": {"compiler": "1"},
        "construction_runtime_sha256": "c" * 64, "manifest_sha256": "d" * 64,
    }
    calls = []

    def run(command, **kwargs):
        arm = command[command.index("--retrieval-arm") + 1]
        out = Path(command[command.index("--out") + 1])
        proof = Path(command[command.index("--proof") + 1])
        report = {
            "arm": arm, "database_identity": f"scratch-{arm}",
            "extraction_bank": bank, "paid_calls": 0, "results": [],
        }
        out.write_text(json.dumps(report))
        proof.write_text(json.dumps({
            "runtime": {"contract": "same"},
            "report_sha256": generator.sha256_json(report), "paid_calls": 0,
        }))
        calls.append((arm, kwargs["env"]))
        return type("Result", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr(generator.subprocess, "run", run)
    monkeypatch.setenv("MEMPHANT_SCRATCH_ACTIVE", "1")
    monkeypatch.setenv("DATABASE_URL", "postgres://secret")
    out = tmp_path / "report.json"
    proof = tmp_path / "proof.json"
    generator.orchestrate_retrieval_screen(Namespace(out=out, proof=proof))

    assert [arm for arm, _env in calls] == ["baseline", "candidate"]
    assert all("MEMPHANT_SCRATCH_ACTIVE" not in env and "DATABASE_URL" not in env
               for _arm, env in calls)
    assert json.loads(proof.read_text())["scratch_database_identities"] == {
        "baseline": "scratch-baseline", "candidate": "scratch-candidate",
    }


def test_retrieval_screen_rejects_dropping_the_entire_baseline_prefix(tmp_path: Path) -> None:
    generator = load(GENERATOR, "generate_memora_bank_empty_prefix")
    bank = {
        "archive_sha256": "a" * 64, "extractor_ledger_sha256": "b" * 64,
        "compiler_versions": {"compiler": "1"},
        "construction_runtime_sha256": "c" * 64, "manifest_sha256": "d" * 64,
    }
    identity = ["weekly", "software_engineer", "activity_food_total_163"]
    rollup = {
        "rank": 1, "unit_id": "rollup",
        "body": "quantity rollup quantity_event_v1/food_spending/food_spending (usd); "
        "window=[2025-06-01T00:00:00Z,2025-06-08T00:00:00Z); filter=all; "
        "total=1; average=1 (rounded to 6 decimal places when needed); count=1; min=1; max=1",
    }
    baseline = {
        "arm": "baseline", "database_identity": "a", "extraction_bank": bank,
        "paid_calls": 0, "results": [{"identity": identity, "evidence": [
            {"rank": 1, "unit_id": "ordinary", "body": "ordinary"},
        ], "trace_id": "b", "retrieval": {}}],
    }
    candidate = {
        "arm": "candidate", "database_identity": "b", "extraction_bank": bank,
        "paid_calls": 0, "results": [{"identity": identity, "evidence": [rollup],
                                      "trace_id": "c", "retrieval": {}}],
    }

    with pytest.raises(RuntimeError, match="activity_food_total_163"):
        generator.compare_retrieval_arms(
            baseline, candidate, tmp_path / "report.json", tmp_path / "proof.json",
        )


@pytest.mark.parametrize("rollup_kind", ["duplicate", "wrong"])
def test_retrieval_screen_rejects_duplicate_or_wrong_rollup(
    tmp_path: Path, rollup_kind: str,
) -> None:
    generator = load(GENERATOR, f"generate_memora_bank_{rollup_kind}_rollup")
    bank = {
        "archive_sha256": "a" * 64, "extractor_ledger_sha256": "b" * 64,
        "compiler_versions": {"compiler": "1"},
        "construction_runtime_sha256": "c" * 64, "manifest_sha256": "d" * 64,
    }
    identity = ["weekly", "software_engineer", "activity_food_total_163"]
    correct = {
        "rank": 1, "unit_id": "rollup",
        "body": "quantity rollup quantity_event_v1/food_spending/food_spending (usd); "
        "window=[2025-06-01T00:00:00Z,2025-06-08T00:00:00Z); filter=all; "
        "total=1; average=1 (rounded to 6 decimal places when needed); count=1; min=1; max=1",
    }
    wrong = dict(correct, body=correct["body"].replace("filter=all", "filter=expense_type=coffee"))
    ordinary = {"rank": 1, "unit_id": "ordinary", "body": "ordinary"}
    candidate_evidence = ([correct, dict(correct, unit_id="duplicate")]
                          if rollup_kind == "duplicate" else [wrong])
    candidate_evidence.append(dict(ordinary, rank=len(candidate_evidence) + 1))
    baseline = {
        "arm": "baseline", "database_identity": "a", "extraction_bank": bank,
        "paid_calls": 0, "results": [{"identity": identity, "evidence": [ordinary],
                                      "trace_id": "b", "retrieval": {}}],
    }
    candidate = {
        "arm": "candidate", "database_identity": "b", "extraction_bank": bank,
        "paid_calls": 0, "results": [{"identity": identity, "evidence": candidate_evidence,
                                      "trace_id": "c", "retrieval": {}}],
    }

    with pytest.raises(RuntimeError, match="activity_food_total_163"):
        generator.compare_retrieval_arms(
            baseline, candidate, tmp_path / "report.json", tmp_path / "proof.json",
        )


def test_retrieval_screen_rejects_token_shaped_false_goal_companion(tmp_path: Path) -> None:
    generator = load(GENERATOR, "generate_memora_bank_false_goal")
    bank = {
        "archive_sha256": "a" * 64, "extractor_ledger_sha256": "b" * 64,
        "compiler_versions": {"compiler": "1"},
        "construction_runtime_sha256": "c" * 64, "manifest_sha256": "d" * 64,
    }
    identity = ["weekly", "software_engineer", "goal_food_expenses_coffee_163_0"]
    ordinary = {"rank": 1, "unit_id": "ordinary", "body": "ordinary"}
    candidate_evidence = [
        {
            "rank": 1, "unit_id": "rollup",
            "body": "quantity rollup quantity_event_v1/food_spending/food_spending (usd); "
            "window=[2025-06-01T00:00:00Z,2025-06-08T00:00:00Z); "
            "filter=expense_type=coffee; total=1; average=1 (rounded to 6 decimal places when "
            "needed); count=1; min=1; max=1",
        },
        {"rank": 2, "unit_id": "fake", "body": "coffee_goal item limit: {\"target\":30}"},
        dict(ordinary, rank=3),
    ]
    baseline = {
        "arm": "baseline", "database_identity": "a", "extraction_bank": bank,
        "paid_calls": 0, "results": [{"identity": identity, "evidence": [ordinary],
                                      "trace_id": "b", "retrieval": {}}],
    }
    candidate = {
        "arm": "candidate", "database_identity": "b", "extraction_bank": bank,
        "paid_calls": 0, "results": [{"identity": identity, "evidence": candidate_evidence,
                                      "trace_id": "c", "retrieval": {}}],
    }

    with pytest.raises(RuntimeError, match="goal_food_expenses_coffee_163_0"):
        generator.compare_retrieval_arms(
            baseline, candidate, tmp_path / "report.json", tmp_path / "proof.json",
        )


def test_goal_companion_validation_accepts_semantic_extractor_shapes() -> None:
    generator = load(GENERATOR, "generate_memora_semantic_goal_companions")

    assert generator.is_goal_companion(
        "goal_food_expenses_coffee_163_0",
        "coffee_spending_goal item spending_limit: "
        '{"expense_type":"coffee","frequency":"weekly","target_amount":"30"}',
    )
    assert generator.is_goal_companion(
        "goal_food_expenses_coffee_163_0",
        "financial_goals item weekly_coffee_spending_limit: "
        '{"expense_type":"coffee","goal":"keep coffee spending under 30 per week",'
        '"limit":"30","measure":"food_spending","period":"week","unit":"usd"}',
    )
    assert not generator.is_goal_companion(
        "goal_food_expenses_coffee_163_0",
        'coffee_goal item limit: {"expense_type":"coffee","target":"30"}',
    )


def test_extraction_bank_restore_verifies_archive_ledger_and_schema(
    tmp_path: Path, monkeypatch,
) -> None:
    generator = load(GENERATOR, "generate_memora_bank_restore")
    archive = tmp_path / "archive"
    archive.write_bytes(b"bank")
    archive_sha = hashlib.sha256(b"bank").hexdigest()
    archive.rename(tmp_path / f"{archive_sha}.dump")
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_bytes(b"attempt\n")
    ledger_sha = hashlib.sha256(ledger.read_bytes()).hexdigest()
    identity = {"schema_sha256": "s", "extensions_and_migrations_sha256": "m", "sha256": "i"}
    logical_identity = {"tables": {}, "sha256": generator.sha256_json({})}
    construction = {
        "kind": "direct_extraction",
        "artifacts": [
            {"role": "extractor_ledger", "file": ledger.name, "sha256": ledger_sha}
        ],
    }
    (tmp_path / "manifest.json").write_text(json.dumps({
        "format_version": generator.BANK_FORMAT_VERSION,
        "archive": f"{archive_sha}.dump",
        "archive_sha256": archive_sha,
        "extractor_ledger": ledger.name,
        "extractor_ledger_sha256": ledger_sha,
        "database_identity": identity,
        "logical_identity": logical_identity,
        "construction": construction,
        "construction_sha256": generator.sha256_json(construction),
        "postgres_major": 17,
        "compiler_versions": ["fixture-compiler"],
        "groups": {
            "weekly-software_engineer": {
                "tenant_id": "tenant", "subject_id": "subject",
                "scope_id": "scope", "actor_id": "actor",
                "agent_node_id": "agent", "subject_generation": 0,
                "agent_level": 0, "policy_revision": "policy",
            },
        },
    }))
    commands = []
    monkeypatch.setattr(
        generator.gate_common, "database_schema_identity", lambda *_args: identity
    )
    monkeypatch.setattr(
        generator, "postgres_tool_identity",
        lambda *_args: {"major": 17, "server_major": 17, "version": "PostgreSQL 17"},
    )
    monkeypatch.setattr(
        generator, "database_bank_identity", lambda *_args: logical_identity
    )
    monkeypatch.setattr(
        generator, "run_postgres_command",
        lambda command, label: commands.append((command, label)),
    )

    generator.restore_extraction_bank("postgres://fixture", tmp_path)

    command, label = commands[0]
    assert label == "restore"
    assert "--single-transaction" in command
    assert "--exit-on-error" in command
    assert "--no-owner" in command
    assert "--no-acl" in command
    ledger.write_bytes(b"tampered\n")
    with pytest.raises(ValueError, match="(ledger|construction artifact) hash mismatch"):
        generator.restore_extraction_bank("postgres://fixture", tmp_path)


def test_extraction_bank_restore_rejects_tampered_construction_artifact(
    tmp_path: Path,
) -> None:
    generator = load(GENERATOR, "generate_memora_bank_construction_tamper")
    proof = tmp_path / "proof.json"
    proof.write_text("{}\n", encoding="utf-8")
    construction = {
        "kind": "causal_composition",
        "artifacts": [
            {
                "role": "composition_proof",
                "file": proof.name,
                "sha256": hashlib.sha256(proof.read_bytes()).hexdigest(),
            }
        ],
    }

    generator.validate_bank_construction(
        {
            "construction": construction,
            "construction_sha256": generator.sha256_json(construction),
        },
        tmp_path,
    )
    proof.write_text('{"tampered":true}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="construction artifact hash mismatch"):
        generator.validate_bank_construction(
            {
                "construction": construction,
                "construction_sha256": generator.sha256_json(construction),
            },
            tmp_path,
        )


def test_seal_causal_bank_binds_source_proof_and_every_contributing_ledger(
    tmp_path: Path, monkeypatch,
) -> None:
    generator = load(GENERATOR, "generate_memora_seal_causal_bank")
    source = tmp_path / "source"
    source.mkdir()
    source_archive = source / "source.dump"
    source_archive.write_bytes(b"source-bank")
    source_ledger = source / "source.jsonl"
    source_ledger.write_bytes(b"source-attempt\n")
    source_manifest = {
        "archive": source_archive.name,
        "archive_sha256": hashlib.sha256(source_archive.read_bytes()).hexdigest(),
        "extractor_ledger": source_ledger.name,
        "extractor_ledger_sha256": hashlib.sha256(source_ledger.read_bytes()).hexdigest(),
        "extractor_model": generator.STRUCTURED_STATE_MODEL,
        "extractor_summary": {"provider_attempts": 1},
        "compiler_versions": ["compiler-source"],
        "groups": {
            "weekly-software_engineer": {
                "tenant_id": "tenant", "subject_id": "subject",
                "scope_id": "scope", "actor_id": "actor",
                "agent_node_id": "agent", "subject_generation": 0,
                "agent_level": 0, "policy_revision": "policy",
            }
        },
        "runtime_sha256": "r" * 64,
        "logical_identity": {"sha256": "l" * 64},
    }
    (source / "manifest.json").write_text(json.dumps(source_manifest))
    ledger_dir = tmp_path / "ledgers"
    ledger_dir.mkdir()
    ledger_hashes = {}
    for name, body in (("one.jsonl", b"one\n"), ("two.jsonl", b"two\n")):
        path = ledger_dir / name
        path.write_bytes(body)
        ledger_hashes[name] = hashlib.sha256(body).hexdigest()
    proof = tmp_path / "proof.json"
    proof.write_text(json.dumps({
        "archive_sha256": source_manifest["archive_sha256"],
        "ledgers": ledger_hashes,
    }))
    bank = tmp_path / "causal-bank"
    archive_hash = hashlib.sha256(b"causal-bank").hexdigest()
    archive = bank / f"{archive_hash}.dump"
    def dump(*_args):
        bank.mkdir()
        archive.write_bytes(b"causal-bank")
        return archive, archive_hash, {"major": 17, "version": "PostgreSQL 17"}
    monkeypatch.setattr(
        generator, "dump_extraction_bank",
        dump,
    )
    monkeypatch.setattr(
        generator.gate_common, "database_schema_identity",
        lambda *_args: {"sha256": "schema"},
    )
    logical_identity = {"tables": {}, "sha256": generator.sha256_json({})}
    monkeypatch.setattr(generator, "database_bank_identity", lambda *_args: logical_identity)
    monkeypatch.setattr(generator, "psql_json", lambda *_args: [])

    manifest = generator.seal_causal_extraction_bank(
        "postgres://fixture", bank, source, proof, ledger_dir,
        extraction_plan_sha256="p" * 64,
        pg_dump_bin="pg_dump",
    )

    assert manifest["format_version"] == generator.BANK_FORMAT_VERSION
    assert manifest["construction"]["kind"] == "causal_composition"
    assert manifest["compiler_versions"] == ["compiler-source"]
    roles = [row["role"] for row in manifest["construction"]["artifacts"]]
    assert roles == [
        "source_manifest", "source_archive", "source_extractor_ledger", "composition_proof",
        "composition_ledger", "composition_ledger",
    ]
    generator.validate_bank_construction(manifest, bank)
    assert (bank / "manifest.json").is_file()


def test_retrieval_proof_exports_deduped_derived_source_bodies(monkeypatch) -> None:
    generator = load(GENERATOR, "generate_memora_provenance")
    tenant = "00000000-0000-0000-0000-000000000001"
    first = "00000000-0000-0000-0000-000000000101"
    second = "00000000-0000-0000-0000-000000000102"
    episode = "00000000-0000-0000-0000-000000000201"
    seen = []

    def fake_psql(database_url, sql):
        seen.append((database_url, sql))
        return [
            {
                "tenant_id": tenant, "unit_id": second,
                "memory_unit_body": "quantity two", "source_episode_id": episode,
                "source_episode_body": "[session 2]\nuser: walked 2 steps",
            },
            {
                "tenant_id": tenant, "unit_id": first,
                "memory_unit_body": "quantity one", "source_episode_id": episode,
                "source_episode_body": "[session 1]\nuser: walked 1 step",
            },
        ]

    monkeypatch.setattr(generator, "psql_json", fake_psql)
    evidence = [{"rank": 1, "unit_id": "rollup", "body": "3 steps"}]
    trace = {
        "id": "trace-1", "tenant_id": tenant, "scope_id": "scope-1",
        "actor_id": "actor-1",
        "context_items": [{
            "unit_id": "rollup", "derived_from_unit_ids": [second, first, first],
        }],
        "citations": [{"unit_id": "rollup", "derived_from_unit_ids": [second]}],
    }
    proof = generator.retrieval_proof(
        trace, evidence, tenant_id=tenant, scope_id="scope-1", actor_id="actor-1",
        database_url="postgres://scratch",
    )

    assert [row["unit_id"] for row in proof["derived_sources"]] == [first, second]
    assert proof["trace_sha256"] == generator.sha256_json(trace)
    assert proof["derived_sources_sha256"] == generator.sha256_json(
        proof["derived_sources"]
    )
    assert proof["direct_sources"] == []
    assert len(seen) == 1 and "memphant.memory_unit" in seen[0][1]
    assert "memphant.episode" in seen[0][1]

    monkeypatch.setattr(generator, "psql_json", lambda *_: [])
    with pytest.raises(RuntimeError, match="missing a derived source unit"):
        generator.export_derived_sources("postgres://scratch", tenant, [first])
    with pytest.raises(RuntimeError, match="pairing failed"):
        generator.retrieval_proof(
            trace, evidence, tenant_id=str(generator.uuid.uuid4()), scope_id="scope-1",
            actor_id="actor-1", database_url="postgres://scratch",
        )


def test_retrieval_proof_exports_direct_returned_units(monkeypatch) -> None:
    generator = load(GENERATOR, "generate_memora_direct_provenance")
    tenant = "00000000-0000-0000-0000-000000000001"
    unit = "00000000-0000-0000-0000-000000000101"
    episode = "00000000-0000-0000-0000-000000000201"
    source = {
        "tenant_id": tenant, "unit_id": unit,
        "memory_unit_body": "goals item coffee: {\"value\":30}",
        "source_episode_id": episode,
        "source_episode_body": "[session 0012]\nuser: coffee goal is $30",
    }
    monkeypatch.setattr(generator, "psql_json", lambda *_: [source])
    evidence = [{"rank": 1, "unit_id": unit, "body": source["memory_unit_body"]}]
    trace = {
        "id": "trace-1", "tenant_id": tenant, "scope_id": "scope-1",
        "actor_id": "actor-1",
        "context_items": [{"unit_id": unit, "derived_from_unit_ids": []}],
        "citations": [],
    }

    proof = generator.retrieval_proof(
        trace, evidence, tenant_id=tenant, scope_id="scope-1", actor_id="actor-1",
        database_url="postgres://scratch",
    )

    assert proof["derived_sources"] == []
    assert proof["direct_sources"] == [source]
    assert proof["direct_sources_sha256"] == generator.sha256_json([source])


def test_checkpoint_pairs_five_answers_to_full_retrieval_traces(tmp_path: Path) -> None:
    generator = load(GENERATOR, "generate_memora_five_trace_pairing")
    answers = []
    records = []
    for index in range(5):
        trace_id = f"trace-{index}"
        unit_id = f"unit-{index}"
        evidence = [{"rank": 1, "unit_id": unit_id, "body": f"memory {index}"}]
        row = {
            "period": "weekly", "persona": "software_engineer",
            "question_id": f"q-{index}", "question": f"question {index}",
            "question_date": "2025-06-07", "task_type": "Reasoning",
            "answer": f"answer {index}", "evidence": evidence,
            "trace": {
                "trace_id": trace_id, "degraded": False,
                "evidence_sha256": generator.evidence_hash(evidence),
            },
        }
        trace = {
            "id": trace_id, "tenant_id": "tenant-1", "scope_id": "scope-1",
            "actor_id": "actor-1",
            "context_items": [{"unit_id": unit_id, "derived_from_unit_ids": []}],
            "citations": [],
        }
        reader = {"cache_hit": False, "fresh_call": True, "usage": {}}
        answers.append(row)
        records.append({
            "identity": list(generator.question_identity(row)),
            "answer_sha256": generator.sha256_json(row),
            "trace_id": trace_id,
            "evidence_sha256": generator.evidence_hash(evidence),
            "returned_items": 1,
            "reader": reader,
            "reader_metadata_sha256": generator.sha256_json(reader),
            "retrieval": {
                "trace": trace, "trace_sha256": generator.sha256_json(trace),
                "derived_sources": [],
                "derived_sources_sha256": generator.sha256_json([]),
                "direct_sources": [direct_source(unit_id)],
                "direct_sources_sha256": generator.sha256_json([direct_source(unit_id)]),
            },
        })
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text(json.dumps({
        "answers": {
            "summary": {"generation_fingerprint": "fp"}, "data": answers,
        },
        "proof": {
            "generation_fingerprint": "fp", "records": records, "errors": [],
        },
    }), encoding="utf-8")

    loaded, loaded_records, errors, _ = generator.load_checkpoint(checkpoint, "fp")
    assert len(loaded) == len(loaded_records) == 5
    assert errors == []

    records[0]["retrieval"] = records[1]["retrieval"]
    checkpoint.write_text(json.dumps({
        "answers": {
            "summary": {"generation_fingerprint": "fp"}, "data": answers,
        },
        "proof": {
            "generation_fingerprint": "fp", "records": records, "errors": [],
        },
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="pairing"):
        generator.load_checkpoint(checkpoint, "fp")


def test_executor_rejects_incomplete_or_drifted_checkpoint(tmp_path: Path) -> None:
    generator = load(GENERATOR, "generate_memora_resume")
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text(
        json.dumps({"answers": {"summary": {"generation_fingerprint": "wrong"}, "data": []}, "proof": {"generation_fingerprint": "wrong", "records": []}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="fingerprint"):
        generator.load_checkpoint(checkpoint, "expected")


def test_executor_resumes_after_reader_failure_without_repeating_paid_answer(tmp_path: Path) -> None:
    generator = load(GENERATOR, "generate_memora_partial_resume")
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    plan = generator.build_group_plan(
        "weekly", fixture["persona"], fixture["sessions"], fixture
    )
    second = dict(plan["queries"][0], question_id="fixture_q2", question="What changed?")
    plan["queries"].append(second)

    class Runtime:
        def open_group(self, _group): pass
        def retain(self, _session): pass
        def drain(self): return 2
        def recall(self, query):
            trace_id = f"trace-{query['question_id']}"
            evidence = [{"rank": 1, "unit_id": "u1", "body": "Use Rust now."}]
            trace = {
                "id": trace_id, "tenant_id": "tenant-1", "scope_id": "scope-1",
                "actor_id": "actor-1",
                "context_items": [{"unit_id": "u1", "derived_from_unit_ids": []}],
                "citations": [],
            }
            return evidence, trace_id, {
                "trace": trace,
                "trace_sha256": generator.sha256_json(trace),
                "derived_sources": [],
                "derived_sources_sha256": generator.sha256_json([]),
                "direct_sources": [direct_source("u1")],
                "direct_sources_sha256": generator.sha256_json([direct_source("u1")]),
            }

    class Reader:
        def __init__(self, fail_second: bool) -> None:
            self.fail_second = fail_second
            self.calls = []
            self.provider_attempts = 0
            self.remaining = None
            self.last_metadata = None
        def set_attempt_budget(self, remaining):
            self.remaining = remaining
        def answer(self, query, _evidence):
            if self.remaining is not None and self.provider_attempts >= self.remaining:
                raise RuntimeError("provider attempt budget exhausted")
            self.provider_attempts += 1
            self.calls.append(query["question_id"])
            if self.fail_second and query["question_id"] == "fixture_q2":
                raise RuntimeError("interrupted")
            return "Rust", {
                "cache_hit": False, "fresh_call": True, "usage": {},
                "provider_attempts": 1, "cost_usd": 0,
            }

    paths = [tmp_path / name for name in ("answers.json", "proof.json", "checkpoint.json")]
    first = Reader(True)
    with pytest.raises(RuntimeError, match="interrupted"):
        generator.execute_groups(
            [plan], Runtime(), first, *paths,
            generation_fingerprint="fp", runtime_proof={"fixture": True},
            max_provider_attempts=3,
        )
    assert first.calls == ["fixture_q1", "fixture_q2"]
    failed_proof = json.loads(paths[1].read_text())
    assert failed_proof["errors"][0]["status"] == "failed"
    assert failed_proof["errors"][0]["retrieval"]["trace"]["id"] == "trace-fixture_q2"

    resumed = Reader(False)
    generator.execute_groups(
        [plan], Runtime(), resumed, *paths,
        generation_fingerprint="fp", runtime_proof={"fixture": True},
        max_provider_attempts=3,
    )
    assert resumed.calls == ["fixture_q2"]
    assert resumed.remaining == 1
    assert json.loads(paths[0].read_text())["summary"]["complete"] is True
