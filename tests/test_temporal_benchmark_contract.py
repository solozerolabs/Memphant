from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import types

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_stale.py"
GENERATOR = ROOT / "scripts" / "generate_stale_memphant_answers.py"
MANIFEST = ROOT / "benchmarks" / "manifests" / "stale.lock.json"
GENERATION_MANIFEST = ROOT / "benchmarks" / "manifests" / "stale_generation.v1.json"
GENERATION_FIXTURE = ROOT / "tests" / "fixtures" / "stale_generation_small.json"


def load_attempts():
    spec = importlib.util.spec_from_file_location(
        "provider_attempts", ROOT / "scripts" / "provider_attempts.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def paid_response(response_id: str = "gen-1") -> dict:
    return {
        "response_id": response_id,
        "requested_model": "openai/gpt-5.6-luna-pro",
        "served_model": "openai/gpt-5.6-luna-pro-20260709",
        "provider": "OpenAI",
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "cost": 0.01,
        },
        "elapsed_seconds": 0.2,
        "retry_index": 0,
        "parse_status": "provider_response_validated",
        "request_sha256": "1" * 64,
        "result_sha256": "2" * 64,
    }


def deep_disposition() -> dict:
    return {
        "contract_revision": "memphant.evidence_disposition.v1",
        "status": "supported",
        "answer_policy": "answer_normally",
        "reason": "Current evidence supports the answer.",
    }


def test_shared_attempt_ledger_rejects_interruption_duplicate_ids_and_hash_drift(
    tmp_path: Path,
) -> None:
    attempts = load_attempts()
    ledger = attempts.ProviderAttemptLedger(tmp_path / "attempts.json", "fingerprint")
    start = {
        "retry_index": 0,
        "requested_model": "openai/gpt-5.6-luna-pro",
        "request_sha256": "1" * 64,
    }
    ledger.record("start", "request-a", start)
    with pytest.raises(RuntimeError, match="interrupted"):
        attempts.validate_provider_attempt_ledger(ledger.snapshot())

    ledger.record("result", "request-a", {"response": paid_response("duplicate")})
    ledger.record("start", "request-b", start)
    ledger.record("result", "request-b", {"response": paid_response("duplicate")})
    with pytest.raises(RuntimeError, match="duplicate response ID"):
        attempts.validate_provider_attempt_ledger(ledger.snapshot())

    malformed = attempts.ProviderAttemptLedger(tmp_path / "malformed.json", "malformed")
    malformed.record("start", "request-c", start)
    bad_response = paid_response("bad-metadata")
    bad_response["provider"] = ""
    malformed.record("result", "request-c", {"response": bad_response})
    with pytest.raises(RuntimeError, match="interrupted or unpriced"):
        attempts.validate_provider_attempt_ledger(malformed.snapshot())

    path = tmp_path / "attempts.json"
    lines = path.read_text(encoding="utf-8").splitlines()
    terminal = json.loads(lines[2])
    terminal["payload"]["response"]["provider"] = "tampered"
    lines[2] = json.dumps(terminal, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ledger.close()
    with pytest.raises(ValueError, match="event hash mismatch"):
        attempts.ProviderAttemptLedger(tmp_path / "attempts.json", "fingerprint")


def test_attempt_journal_appends_without_rewriting_and_rejects_truncation_or_forks(
    tmp_path: Path,
) -> None:
    attempts = load_attempts()
    path = tmp_path / "attempts.jsonl"
    ledger = attempts.ProviderAttemptLedger(path, "fingerprint")
    start = {
        "retry_index": 0,
        "requested_model": "openai/gpt-5.6-luna-pro",
        "request_sha256": "1" * 64,
    }
    ledger.record("start", "request-a", start)
    prefix = path.read_bytes()
    ledger.record("result", "request-a", {"response": paid_response("response-a")})
    complete = path.read_bytes()
    assert complete.startswith(prefix)
    assert len(complete) > len(prefix)

    truncated = tmp_path / "truncated.jsonl"
    truncated.write_bytes(complete[:-1])
    with pytest.raises(ValueError, match="truncated"):
        attempts.ProviderAttemptLedger(truncated, "fingerprint")

    malformed = tmp_path / "malformed.jsonl"
    malformed.write_bytes(complete + b"not-json\n")
    with pytest.raises(ValueError, match="malformed"):
        attempts.ProviderAttemptLedger(malformed, "fingerprint")

    forked = tmp_path / "forked.jsonl"
    forked.write_bytes(complete)
    prior = json.loads(complete.splitlines()[-1])
    terminal = {
        "event": "error",
        "attempt_id": 1,
        "request_key": "request-a",
        "payload": {"type": "late-error"},
        "sequence": 3,
        "previous_event_sha256": attempts.hashlib.sha256(
            attempts._event_bytes(prior)
        ).hexdigest(),
    }
    terminal["event_sha256"] = attempts._sha256_json(terminal)
    with forked.open("ab") as handle:
        handle.write(attempts._event_bytes(terminal) + b"\n")
    with pytest.raises(ValueError, match="forked terminal transition"):
        attempts.ProviderAttemptLedger(forked, "fingerprint")


def test_attempt_journal_rejects_a_second_process_before_provider_work(
    tmp_path: Path,
) -> None:
    attempts = load_attempts()
    path = tmp_path / "attempts.jsonl"
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import sys,time; sys.path.insert(0, sys.argv[1]); "
                "from provider_attempts import ProviderAttemptLedger; "
                "ledger=ProviderAttemptLedger(__import__('pathlib').Path(sys.argv[2]), 'fingerprint'); "
                "print('locked', flush=True); time.sleep(30)"
            ),
            str(ROOT / "scripts"),
            str(path),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None and child.stdout.readline().strip() == "locked"
        with pytest.raises(RuntimeError, match="already active"):
            attempts.ProviderAttemptLedger(path, "fingerprint")
    finally:
        child.terminate()
        child.wait(timeout=5)


def test_shared_meter_hard_caps_native_scorer_output(tmp_path: Path) -> None:
    attempts = load_attempts()
    captured = []

    class Completions:
        def create(self, **kwargs):
            captured.append(kwargs)
            payload = {
                "id": "judge-1",
                "model": "judge",
                "provider": "OpenAI",
                "choices": [{"message": {"content": "{}"}}],
                "usage": {
                    "prompt_tokens": 2,
                    "completion_tokens": 1,
                    "total_tokens": 3,
                    "cost": 0.01,
                },
            }
            return types.SimpleNamespace(
                **payload, model_dump=lambda **_kwargs: payload
            )

    class OpenAI:
        def __init__(self, **_kwargs):
            self.chat = types.SimpleNamespace(completions=Completions())

    sdk = types.SimpleNamespace(OpenAI=OpenAI)
    attempts.install_openai_meter(
        sdk, tmp_path / "judge.jsonl", max_output_tokens=4096
    )
    sdk.OpenAI().chat.completions.create(model="judge", messages=[])

    assert captured[0]["max_tokens"] == 4096


def load_script():
    spec = importlib.util.spec_from_file_location("run_stale", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_generator():
    spec = importlib.util.spec_from_file_location("generate_stale", GENERATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_stale_lock_pins_official_code_dataset_and_native_scorer() -> None:
    lock = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert lock["benchmark"] == "STALE"
    assert lock["code"] == {
        "repo": "https://github.com/icedreamc/STALE.git",
        "revision": "ea7d391103a151927cd29d2f01d87597a782bdcb",
    }
    assert lock["dataset"]["repo"] == "STALEproj/STALE"
    assert lock["dataset"]["revision"] == "617c51dc200b5ab09970834144c7e51c77959af0"
    assert lock["dataset"]["file"] == "T1_T2_400_FULL.json"
    assert lock["dataset"]["sha256"] == (
        "5f3ec375179e20e2e94469e018189188f34e2e7e5f21cbecbd99fcfa648c1876"
    )
    assert lock["dataset"]["size_bytes"] == 305_908_212
    assert lock["dataset"]["record_count"] == 400
    assert lock["native_scorer"]["entrypoint"] == (
        "STALE/Evaluation/full_eval_performance.py"
    )
    assert set(lock["native_scorer"]["files"]) == {
        "STALE/Evaluation/full_eval_performance.py",
        "STALE/Evaluation/judge_prompts.py",
        "STALE/Generation/clients.py",
    }
    assert lock["native_scorer"]["requirements_sha256"] == (
        "d7670540fe00b54ee0b615499f7e633a97fa3938888b8bb2225ad21d8aad6180"
    )


def test_stale_answers_must_pair_exactly_once_with_dataset_unless_explicit_smoke() -> (
    None
):
    run_stale = load_script()
    dataset = [
        {"uid": "one", "probing_queries": {}},
        {"uid": "two", "probing_queries": {}},
    ]
    answer = lambda uid: {  # noqa: E731
        "uid": uid,
        "target_model_responses": {
            "dim1_response": "a",
            "dim2_response": "b",
            "dim3_response": "c",
        },
    }

    run_stale.verify_answers(dataset, [answer("one"), answer("two")])
    with pytest.raises(ValueError, match="exactly match"):
        run_stale.verify_answers(dataset, [answer("one")])
    run_stale.verify_answers(dataset, [answer("one")], smoke=True)
    with pytest.raises(ValueError, match="pinned dataset prefix"):
        run_stale.verify_answers(dataset, [answer("two")], smoke=True)
    with pytest.raises(ValueError, match="strict subset"):
        run_stale.verify_answers(dataset, [answer("one"), answer("two")], smoke=True)
    with pytest.raises(ValueError, match="duplicate"):
        run_stale.verify_answers(dataset, [answer("one"), answer("one")])
    with pytest.raises(ValueError, match="non-empty string"):
        bad = answer("two")
        bad["target_model_responses"]["dim3_response"] = ""
        run_stale.verify_answers(dataset, [answer("one"), bad])


def test_stale_official_checkout_verification_fails_on_source_drift(
    tmp_path: Path,
) -> None:
    run_stale = load_script()
    repo = tmp_path / "STALE"
    scorer = repo / "STALE" / "Evaluation" / "full_eval_performance.py"
    scorer.parent.mkdir(parents=True)
    scorer.write_text("changed", encoding="utf-8")
    manifest = {
        "code": {"revision": "ignored-for-this-unit-test"},
        "native_scorer": {"files": {str(scorer.relative_to(repo)): "0" * 64}},
    }

    with pytest.raises(ValueError, match="hash mismatch"):
        run_stale.verify_official_repo(repo, manifest, verify_revision=False)


def test_stale_native_result_must_be_complete_and_error_free() -> None:
    run_stale = load_script()
    result = {
        "config": {"num_samples": 2},
        "details": [
            {"uid": "one", "judge_meta": {"elapsed_seconds": 1.0, "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}}},
            {"uid": "two", "judge_meta": {"elapsed_seconds": 1.0, "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}}},
        ],
    }

    run_stale.verify_native_result(result, {"one", "two"})
    result["details"][1]["judge_meta"] = {"error": "provider failed"}
    with pytest.raises(RuntimeError, match="judge error"):
        run_stale.verify_native_result(result, {"one", "two"})
    with pytest.raises(ValueError, match="exactly match"):
        run_stale.verify_native_result(
            {"config": {"num_samples": 1}, "details": result["details"][:1]},
            {"one", "two"},
        )
    with pytest.raises(ValueError, match="judge metadata"):
        run_stale.verify_native_result(
            {"config": {"num_samples": 1}, "details": [{"uid": "one"}]},
            {"one"},
        )


def test_stale_generation_lock_freezes_reader_and_evidence_contract() -> None:
    lock = json.loads(GENERATION_MANIFEST.read_text(encoding="utf-8"))

    assert lock["protocol"] == "stale-memphant-generation-v1"
    assert lock["reader"] == {
        "canonical_model_snapshot": "openai/gpt-5.6-luna-pro-20260709",
        "reasoning_effort": "high",
        "requested_model": "openai/gpt-5.6-luna-pro",
    }
    assert lock["smoke"] == {"structured_state": "off"}
    assert lock["retrieval"] == {
        "budget_tokens": 8192,
        "cross_rerank": False,
        "embed_model": "small",
        "limit": 10,
        "mode": "deep",
    }
    assert set(lock["prompt_sha256"]) == {"dim1", "dim2", "dim3"}
    assert len(lock["output_schema_sha256"]) == 64
    assert len(lock["reader_response_contract_sha256"]) == 64


def test_stale_record_plan_is_chronological_and_never_exposes_gold() -> None:
    generator = load_generator()
    record = {
        "uid": "fixture-uid",
        "M_old": "DO_NOT_EXPOSE_OLD",
        "M_new": "DO_NOT_EXPOSE_NEW",
        "explanation": "DO_NOT_EXPOSE_EXPLANATION",
        "relevant_session_index": [1, 0],
        "type": "T1",
        "haystack_session": [
            [{"role": "user", "content": "later"}],
            [{"role": "assistant", "content": "earlier"}],
        ],
        "timestamps": ["2025-02-01 00:00", "2025-01-01 00:00"],
        "probing_queries": {
            "dim1_query": "state?",
            "dim2_query": "premise?",
            "dim3_query": "action?",
        },
    }

    plan = generator.build_record_plan(record)

    assert [session["timestamp"] for session in plan["sessions"]] == [
        "2025-01-01T00:00:00Z",
        "2025-02-01T00:00:00Z",
    ]
    assert plan["queries"] == {
        "dim1": "state?",
        "dim2": "premise?",
        "dim3": "action?",
    }
    serialized = json.dumps(plan)
    assert "DO_NOT_EXPOSE" not in serialized
    assert "relevant_session_index" not in serialized


def test_stale_reader_prompt_preserves_deep_premise_disposition() -> None:
    generator = load_generator()
    prompt = generator.build_reader_prompt(
        ["The current destination is Kyoto."],
        "Which Tokyo hotel should I book?",
        "dim2",
        {
            "contract_revision": "memphant.evidence_disposition.v1",
            "status": "contradicts-premise",
            "answer_policy": "state_premise_false",
            "reason": "The current state supersedes the question premise.",
        },
    )

    assert "contradicts-premise" in prompt
    assert "state_premise_false" in prompt
    assert "The current state supersedes the question premise." in prompt


def test_stale_prompt_lock_hashes_the_complete_rendered_prompt(monkeypatch) -> None:
    generator = load_generator()
    original = generator.prompt_hashes()
    build = generator.build_reader_prompt
    monkeypatch.setattr(
        generator,
        "build_reader_prompt",
        lambda *args, **kwargs: build(*args, **kwargs) + "\ncontract mutation",
    )

    assert generator.prompt_hashes() != original


def test_stale_runtime_requests_use_api_key_tenant_and_canonical_context() -> None:
    generator = load_generator()

    class Client:
        tenant_id = "tenant-derived-from-api-key"

        def __init__(self) -> None:
            self.requests = []

        def post(self, path, payload):
            self.requests.append((path, payload))
            if path == "/v1/episodes":
                return {"episode_id": "episode-1"}
            return {
                "degraded": False,
                "trace_id": "trace-1",
                "items": [],
                "citations": [],
                "deep": {
                    "evidence": {
                        "contract_revision": "memphant.evidence_disposition.v1",
                        "status": "insufficient",
                        "answer_policy": "abstain_unknown",
                        "reason": "No current evidence.",
                    }
                },
            }

        def put(self, path, payload):
            self.requests.append((path, payload))
            return {
                "subject_id": "subject-1",
                "scope_id": "scope-1",
                "actor_id": "actor-1",
                "agent_node_id": "agent-1",
                "subject_generation": 7,
            }

        def get(self, path):
            assert path == (
                "/v1/traces/trace-1?subject_id=subject-1&scope_id=scope-1"
                "&actor_id=actor-1&agent_node_id=agent-1&subject_generation=7"
            )
            return {"id": "trace-1"}

    client = Client()
    subject_id, scope_id, actor_id, agent_node_id, generation = (
        generator.bind_record_context(client, "fixture-uid")
    )
    plan = {
        "uid": "fixture-uid",
        "sessions": [
            {"timestamp": "2026-01-01T00:00:00Z", "body": "hello"}
        ],
    }
    assert (
        generator.ingest_plan(
            client, plan, subject_id, scope_id, actor_id, agent_node_id, generation
        )
        == 1
    )
    assert generator.recall_plan(
        client,
        subject_id,
        scope_id,
        actor_id,
        agent_node_id,
        generation,
        "state?",
    ) == (
        [],
        "trace-1",
        {
            "contract_revision": "memphant.evidence_disposition.v1",
            "status": "insufficient",
            "answer_policy": "abstain_unknown",
            "reason": "No current evidence.",
        },
        [],
    )

    required_context = {
        "subject_id": subject_id,
        "scope_id": scope_id,
        "actor_id": actor_id,
        "agent_node_id": agent_node_id,
        "subject_generation": 7,
    }
    assert [path for path, _ in client.requests] == [
        "/v1/context-bindings/stale%3Afixture-uid",
        "/v1/episodes",
        "/v1/recall",
    ]
    binding = client.requests[0][1]
    assert binding["subject"] == {"external_ref": "stale:fixture-uid", "kind": "user"}
    assert binding["actor"] == binding["subject"]
    episode_payload = client.requests[1][1]
    assert "tenant_id" not in episode_payload
    assert {key: episode_payload[key] for key in required_context} == required_context
    assert episode_payload["source_ref"] == "stale:fixture-uid:session:0001"
    assert episode_payload["payload"]["episode"] == {
        "source_kind": "user",
        "body": "hello",
    }
    recall_payload = client.requests[2][1]
    assert "tenant_id" not in recall_payload
    assert {key: recall_payload[key] for key in required_context} == required_context


def test_stale_generation_rejects_malformed_sessions_and_partial_resume() -> None:
    generator = load_generator()
    assert all(
        "Never set abstain=true" in prompt
        for prompt in generator.SYSTEM_PROMPTS.values()
    )
    record = {
        "uid": "fixture-uid",
        "haystack_session": [[{"role": "system", "content": "not allowed"}]],
        "timestamps": ["2025-01-01 00:00"],
        "probing_queries": {
            "dim1_query": "state?",
            "dim2_query": "premise?",
            "dim3_query": "action?",
        },
    }
    with pytest.raises(ValueError, match="role"):
        generator.build_record_plan(record)

    answers = {
        "summary": {"generation_fingerprint": "frozen"},
        "data": [
            {
                "uid": "done",
                "target_model": "openai/gpt-5.6-sol-pro",
                "target_model_responses": {
                    "dim1_response": "one",
                    "dim2_response": "two",
                    "dim3_response": "three",
                },
                "target_model_meta": {},
            }
        ],
    }
    proof = {"generation_fingerprint": "frozen", "records": []}
    with pytest.raises(ValueError, match="resume UID sets"):
        generator.validate_resume(answers, proof, "frozen")


def test_stale_smoke_requires_fresh_generation_artifacts(tmp_path: Path) -> None:
    generator = load_generator()
    selection = {"smoke_only": True}
    artifact_names = (
        "answers",
        "proof",
        "checkpoint",
        "attempt-ledger",
        "server-log",
        "cache",
    )
    for artifact_name in artifact_names:
        root = tmp_path / artifact_name
        out = root / "answers.json"
        proof = out.with_suffix(out.suffix + ".proof.json")
        checkpoint = out.with_suffix(out.suffix + ".checkpoint.json")
        cache_dir = root / "reader-cache"
        args = types.SimpleNamespace(
            out=out,
            proof=None,
            checkpoint=None,
            cache_dir=cache_dir,
        )
        paths = {
            "answers": out,
            "proof": proof,
            "checkpoint": checkpoint,
            "attempt-ledger": proof.with_suffix(proof.suffix + ".attempts.json"),
            "server-log": root / "stale-memphant-server.log",
        }
        if artifact_name == "cache":
            cache_dir.mkdir(parents=True)
            (cache_dir / "cached.json").write_text("{}", encoding="utf-8")
        else:
            paths[artifact_name].parent.mkdir(parents=True, exist_ok=True)
            paths[artifact_name].write_text("{}", encoding="utf-8")

        with pytest.raises(ValueError, match="fresh smoke"):
            generator.require_fresh_smoke_artifacts(args, selection)

        generator.require_fresh_smoke_artifacts(
            args, {"smoke_only": False}
        )


def test_stale_generation_runtime_hashes_shared_meter_and_bootstrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    generator = load_generator()
    binaries = {}
    for name in ("server", "worker", "cli"):
        path = tmp_path / name
        path.write_bytes(name.encode())
        binaries[name] = path
    monkeypatch.setattr(
        generator.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            _args, 0, stdout="commit\n", stderr=""
        ),
    )
    runtime = generator.runtime_contract(
        types.SimpleNamespace(
            server_bin=binaries["server"],
            worker_bin=binaries["worker"],
            cli_bin=binaries["cli"],
        ),
        "dataset-sha",
        {"enabled": False},
    )

    hashes = runtime["harness_sha256"]
    assert hashes["provider_attempts"] == generator.sha256_file(
        ROOT / "scripts" / "provider_attempts.py"
    )
    assert hashes["stale_bootstrap"] == generator.sha256_file(
        ROOT / "benchmarks" / "stale" / "harness_bootstrap.py"
    )


def test_stale_answer_row_matches_upstream_shape() -> None:
    generator = load_generator()
    assert generator.UPSTREAM_ROW_SCHEMA["additionalProperties"] is False
    assert (
        generator.UPSTREAM_ROW_SCHEMA["properties"]["target_model_responses"][
            "additionalProperties"
        ]
        is False
    )
    responses = {
        dimension: {
            "answer": dimension,
            "elapsed_seconds": 1.0,
            "usage": {},
        }
        for dimension in ("dim1", "dim2", "dim3")
    }
    row = generator.build_answer_row("uid", responses)

    assert set(row) == {
        "uid",
        "target_model",
        "target_model_responses",
        "target_model_meta",
    }
    assert row["target_model"] == "openai/gpt-5.6-luna-pro-20260709"
    assert row["target_model_responses"] == {
        "dim1_response": "dim1",
        "dim2_response": "dim2",
        "dim3_response": "dim3",
    }
    expected_hash = hashlib.sha256(
        json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert generator.answer_row_sha256(row) == expected_hash
    with pytest.raises(ValueError, match="target_model_responses"):
        bad = dict(row)
        bad["target_model_responses"] = {"dim1_response": "only one"}
        generator.validate_answer_row(bad)


def test_stale_smoke_selection_is_deterministic_and_promotion_ineligible() -> None:
    generator = load_generator()
    plans = [{"uid": "one"}, {"uid": "two"}, {"uid": "three"}]

    selected, selection = generator.select_plans(plans, 1)

    assert selected == [{"uid": "one"}]
    assert selection == {
        "method": "pinned_dataset_prefix",
        "limit": 1,
        "source_record_count": 3,
        "smoke_only": True,
        "promotion_ineligible": True,
    }
    with pytest.raises(ValueError, match="strict subset"):
        generator.select_plans(plans, 3)

    pilot_plans = [
        {"uid": "t1-a", "conflict_type": "T1", "record_sha256": "1" * 64},
        {"uid": "t2-a", "conflict_type": "T2", "record_sha256": "2" * 64},
        {"uid": "t1-b", "conflict_type": "T1", "record_sha256": "3" * 64},
        {"uid": "t2-b", "conflict_type": "T2", "record_sha256": "4" * 64},
        {"uid": "unused", "conflict_type": "T1", "record_sha256": "5" * 64},
    ]
    manifest = {
        "protocol": "stale-paired-pilot-v1",
        "upstream_revision": "617c51dc200b5ab09970834144c7e51c77959af0",
        "dataset_sha256": "a" * 64,
        "records": [
            {key: plan[key] for key in ("uid", "conflict_type", "record_sha256")}
            for plan in pilot_plans[:4]
        ],
    }
    pilot, pilot_selection = generator.select_plans(
        pilot_plans, None, manifest, "a" * 64
    )
    assert [plan["uid"] for plan in pilot] == ["t1-a", "t2-a", "t1-b", "t2-b"]
    assert pilot_selection["method"] == "answer_blind_paired_manifest"
    assert pilot_selection["query_count"] == 12
    assert pilot_selection["promotion_ineligible"] is True
    with pytest.raises(ValueError, match="two T1 and two T2"):
        generator.select_plans(
            pilot_plans,
            None,
            manifest | {"records": manifest["records"][:3] + [manifest["records"][0]]},
            "a" * 64,
        )

    dimension = {
        "elapsed_seconds": 0.1,
        "usage": paid_response()["usage"],
        "cache_hit": False,
        "fresh_call": True,
        "trace_id": "trace",
        "returned_items": 1,
        "evidence_sha256": "0" * 64,
        "degraded": False,
    }
    answer = {
        "uid": "one",
        "target_model": "openai/gpt-5.6-luna-pro-20260709",
        "target_model_responses": {
            "dim1_response": "one",
            "dim2_response": "two",
            "dim3_response": "three",
        },
        "target_model_meta": {f"dim{i}_meta": dimension for i in (1, 2, 3)},
    }
    generator.validate_answer_row(answer)
    missing_cost = json.loads(json.dumps(answer))
    missing_cost["target_model_meta"]["dim1_meta"]["usage"] = {}
    with pytest.raises(ValueError, match="usage"):
        generator.validate_answer_row(missing_cost)
    missing_response_id = json.loads(json.dumps(answer))
    del missing_response_id["target_model_meta"]["dim1_meta"]["trace_id"]
    with pytest.raises(ValueError, match="provenance"):
        generator.validate_answer_row(missing_response_id)
    proof_row = {
        "uid": "one",
        "answer_row_sha256": generator.answer_row_sha256(answer),
        "dimensions": {
            f"dim{i}": {"trace_id": "trace", "degraded": False} for i in (1, 2, 3)
        },
    }
    output, proof = generator.output_objects(
        "fingerprint", 1, [answer], [proof_row], {}, 2, selection
    )
    assert output["summary"]["promotion_ineligible"] is True
    assert output["summary"]["smoke_only"] is True
    assert proof["promotion_ineligible"] is True
    assert proof["smoke_only"] is True


def test_stale_smoke_contract_requires_structured_state_off(monkeypatch) -> None:
    generator = load_generator()
    monkeypatch.setenv("MEMPHANT_STRUCTURED_STATE", "on")
    with pytest.raises(ValueError, match="structured state off"):
        generator.structured_state_contract(smoke=True)

    monkeypatch.setenv("MEMPHANT_STRUCTURED_STATE", "off")
    assert generator.structured_state_contract(smoke=True) == {
        "enabled": False,
        "model": None,
        "prompt_sha256": None,
    }


def test_stale_reader_preserves_openrouter_usage_and_cost() -> None:
    generator = load_generator()

    class Reader:
        fresh_calls = 0
        cached_calls = 0
        provider_attempts = 0
        last_call_metadata = None

        def set_provider_attempt_limit(self, limit):
            self.provider_attempt_limit = limit

        def call(self, *_args):
            self.fresh_calls += 1
            self.last_call_metadata = {
                "response_id": "reader-dim1",
                "requested_model": "openai/gpt-5.6-luna-pro",
                "served_model": "openai/gpt-5.6-luna-pro-20260709",
                "provider": "OpenAI",
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 20,
                    "total_tokens": 30,
                    "cost": 0.001,
                },
                "elapsed_seconds": 0.1,
                "retry_index": 0,
                "parse_status": "provider_response_validated",
                "request_sha256": "1" * 64,
                "result_sha256": "2" * 64,
            }
            return json.dumps({"notes": "", "answer": "current", "abstain": False})

    result = generator.run_reader_dimension(
        Reader(), "dim1", "state?", ["evidence"], deep_disposition()
    )
    assert result["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 20,
        "total_tokens": 30,
        "cost": 0.001,
    }
    assert result["response_id"] == "reader-dim1"
    assert result["served_model"] == "openai/gpt-5.6-luna-pro-20260709"

    class MissingUsage(Reader):
        def call(self, *_args):
            self.fresh_calls += 1
            self.last_call_metadata = {"usage": {}}
            return json.dumps({"notes": "", "answer": "current", "abstain": False})

    with pytest.raises(RuntimeError, match="usage cost"):
        generator.run_reader_dimension(
            MissingUsage(), "dim1", "state?", ["evidence"], deep_disposition()
        )


def test_stale_dimension_caps_transport_to_one_paid_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-not-real")
    generator = load_generator()
    monkeypatch.setattr(generator.run_reader.time, "sleep", lambda _seconds: None)
    calls = []

    def fail(request, timeout=None):
        calls.append(request.full_url)
        raise generator.run_reader.urllib.error.URLError("offline")

    monkeypatch.setattr(generator.run_reader.urllib.request, "urlopen", fail)
    cache_dir = tmp_path / "cache"
    ledger = generator.ProviderAttemptLedger(
        tmp_path / "attempts.json", "stale-dimension"
    )
    reader = generator.run_reader.ReaderCli(
        "openrouter",
        generator.REQUESTED_MODEL,
        generator.REQUESTED_MODEL,
        cache_dir,
        3,
        generator.REASONING_EFFORT,
    )
    reader.set_provider_attempt_hook(ledger.record)

    with pytest.raises(Exception):
        generator.run_reader_dimension(
            reader, "dim1", "state?", ["evidence"], deep_disposition()
        )

    snapshot = ledger.snapshot()
    assert calls == [generator.run_reader.OPENROUTER_URL]
    assert reader.provider_attempts == 1
    assert snapshot["provider_attempts"] == 1
    assert snapshot["attempts"][0]["status"] == "error"
    assert snapshot["attempts"][0]["retry_index"] == 0
    assert not cache_dir.exists() or not list(cache_dir.iterdir())


def test_stale_dimension_attempt_archive_stays_out_of_official_answer_metadata() -> None:
    generator = load_generator()
    result = {
        "answer": "current",
        **paid_response(),
        "cache_hit": False,
        "fresh_call": True,
    }
    attempt = {"response": paid_response()}

    answer_facts, proof_facts = generator.dimension_artifacts(
        result, "trace-1", ["evidence"], deep_disposition(), [{"receipt": "one"}], [attempt]
    )
    row = generator.build_answer_row(
        "uid", {dimension: answer_facts for dimension in ("dim1", "dim2", "dim3")}
    )

    generator.validate_answer_row(row)
    assert "provider_attempts" not in row["target_model_meta"]["dim1_meta"]
    assert proof_facts["provider_attempts"] == [attempt]
    assert proof_facts["parse_status"] == "parsed"
    assert proof_facts["evidence_disposition"] == deep_disposition()
    assert proof_facts["verified_receipt_count"] == 1


def test_stale_smoke_binds_dimension_archives_to_global_ledger_and_answer_hash(
    tmp_path: Path,
) -> None:
    attempts = load_attempts()
    run_stale = load_script()

    def ledger(
        name: str,
        response_ids: list[str],
        *,
        requested_model: str = "openai/gpt-5.6-luna-pro",
        served_model: str = "openai/gpt-5.6-luna-pro-20260709",
        context: dict | None = None,
    ):
        value = attempts.ProviderAttemptLedger(tmp_path / f"{name}.json", name)
        responses = []
        for response_id in response_ids:
            response = paid_response(response_id)
            response["requested_model"] = requested_model
            response["served_model"] = served_model
            response.update(context or {})
            value.record(
                "start",
                response_id,
                {
                    "retry_index": 0,
                    "requested_model": response["requested_model"],
                    "request_sha256": response["request_sha256"],
                },
            )
            value.record("result", response_id, {"response": response})
            responses.append(response)
        return value.snapshot(), responses

    reader, responses = ledger("reader", ["dim-1", "dim-2", "dim-3"])
    judge, _ = ledger(
        "judge",
        ["judge-1"],
        requested_model="judge-model",
        served_model="judge-model-served",
        context={"benchmark": "STALE", "arm": "judge"},
    )
    answer = {
        "uid": "one",
        "target_model": "openai/gpt-5.6-luna-pro-20260709",
        "target_model_responses": {"dim1_response": "a"},
    }
    proof = {
        "provider_attempt_ledger": reader,
        "provider_attempt_ledger_sha256": reader["attempts_sha256"],
        "records": [
            {
                "uid": "one",
                "answer_row_sha256": run_stale.sha256_json(answer),
                "dimensions": {
                    f"dim{index}": {
                        "trace_id": f"trace-{index}",
                        "degraded": False,
                        "parse_status": "parsed",
                        "response_id": response["response_id"],
                        "provider_attempts": [
                            {"response": json.loads(json.dumps(response))}
                        ],
                    }
                    for index, response in enumerate(responses, 1)
                },
            }
        ],
    }

    reader_contract = {
        "requested_model": "openai/gpt-5.6-luna-pro",
        "canonical_model_snapshot": "openai/gpt-5.6-luna-pro-20260709",
    }
    run_stale.verify_smoke_provenance(
        proof, judge, 1, [answer], reader_contract, "judge-model"
    )
    with pytest.raises(ValueError, match="reader model mismatch"):
        run_stale.verify_smoke_provenance(
            proof,
            judge,
            1,
            [answer],
            reader_contract | {"requested_model": "other-model"},
            "judge-model",
        )
    with pytest.raises(ValueError, match="judge model"):
        run_stale.verify_smoke_provenance(
            proof, judge, 1, [answer], reader_contract, "other-judge"
        )
    proof["records"][0]["dimensions"]["dim1"]["provider_attempts"][0][
        "response"
    ]["provider"] = "tampered"
    with pytest.raises(ValueError, match="attempt rows"):
        run_stale.verify_smoke_provenance(
            proof, judge, 1, [answer], reader_contract, "judge-model"
        )


def test_stale_reader_proof_fails_before_native_judge_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_stale = load_script()
    answers_path = tmp_path / "answers.json"
    answers_path.write_text(json.dumps({"summary": {}, "data": []}), encoding="utf-8")
    answers_path.with_suffix(".json.proof.json").write_text("{}", encoding="utf-8")
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text("[]", encoding="utf-8")
    args = types.SimpleNamespace(
        official_repo=tmp_path,
        answers=answers_path,
        out=tmp_path / "result.json",
        dataset=dataset_path,
        manifest=run_stale.DEFAULT_MANIFEST,
        cache_dir=tmp_path,
        model_method="memphant",
        conflict_type="T1_T2",
        judge_model=None,
        judge_provider=None,
        concurrency=1,
        judge_max_output_tokens=4096,
        verify_only=False,
        smoke=True,
    )
    answer = {"uid": "one", "target_model": "pinned"}
    monkeypatch.setattr(run_stale, "parse_args", lambda: args)
    monkeypatch.setattr(run_stale, "verify_official_repo", lambda *_args: None)
    monkeypatch.setattr(
        run_stale, "verify_dataset", lambda *_args: [{"uid": "one"}, {"uid": "two"}]
    )
    monkeypatch.setattr(run_stale, "load_records", lambda *_args: [answer])
    monkeypatch.setattr(run_stale, "verify_smoke_contract", lambda *_args: None)
    monkeypatch.setattr(run_stale, "verify_answers", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        run_stale,
        "verify_reader_provenance",
        lambda *_args: (_ for _ in ()).throw(ValueError("bad reader proof")),
    )
    launched = []
    monkeypatch.setattr(run_stale.subprocess, "run", lambda *_args, **_kwargs: launched.append(True))

    with pytest.raises(ValueError, match="bad reader proof"):
        run_stale.main()
    assert launched == []


def test_stale_smoke_requires_fresh_judge_artifacts(tmp_path: Path) -> None:
    run_stale = load_script()
    for artifact_name, suffix in (
        ("result", ""),
        ("attempt-ledger", ".attempts.json"),
        ("proof", ".proof.json"),
    ):
        root = tmp_path / artifact_name
        out = root / "result.json"
        artifact = Path(str(out) + suffix)
        artifact.parent.mkdir(parents=True)
        artifact.write_text("{}", encoding="utf-8")
        args = types.SimpleNamespace(out=out, smoke=True)

        with pytest.raises(ValueError, match="fresh smoke"):
            run_stale.require_fresh_smoke_result(args)

        run_stale.require_fresh_smoke_result(
            types.SimpleNamespace(out=out, smoke=False)
        )


def test_stale_judge_proof_hashes_shared_meter_and_bootstrap() -> None:
    run_stale = load_script()
    assert set(run_stale.PROOF_HARNESS_FILES) == {
        "runner",
        "stale_bootstrap",
        "provider_attempts",
    }
    assert {
        name: run_stale.sha256_file(path)
        for name, path in run_stale.PROOF_HARNESS_FILES.items()
    }["provider_attempts"] == run_stale.sha256_file(
        ROOT / "scripts" / "provider_attempts.py"
    )


def test_stale_scorer_smoke_contract_is_explicit() -> None:
    run_stale = load_script()
    answers = [{"uid": "one"}]
    summary = {
        "num_items": 1,
        "expected_items": 1,
        "source_record_count": 2,
        "smoke_only": True,
        "promotion_ineligible": True,
    }
    run_stale.verify_smoke_contract(summary, answers, dataset_count=2)
    with pytest.raises(ValueError, match="promotion-ineligible"):
        run_stale.verify_smoke_contract({}, answers, dataset_count=2)


def test_stale_generation_dry_run_never_needs_runtime_or_model(tmp_path: Path) -> None:
    out = tmp_path / "dry-run.json"
    result = subprocess.run(
        [
            sys.executable,
            str(GENERATOR),
            "--dataset",
            str(GENERATION_FIXTURE),
            "--out",
            str(out),
            "--cache-dir",
            str(tmp_path / "cache"),
            "--fixture",
            "--dry-run",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["source_status"] == "dry_run_no_answers"
    assert report["record_count"] == 1
    assert report["session_count"] == 2
    assert set(report["prompt_sha256"]) == {"dim1", "dim2", "dim3"}
    assert "data" not in report
