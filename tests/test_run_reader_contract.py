from __future__ import annotations

import importlib.util
import hashlib
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_run_reader():
    spec = importlib.util.spec_from_file_location(
        "run_reader", ROOT / "scripts" / "run_reader.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_provider_attempts():
    spec = importlib.util.spec_from_file_location(
        "provider_attempts_reader_test", ROOT / "scripts" / "provider_attempts.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _open_test_ledger(attempts, path: Path, screen: str = "reader-test"):
    return attempts.ProviderAttemptLedger(
        path,
        hashlib.sha256(b"reader-fixture").hexdigest(),
        screen,
        1_000_000_000_000,
        0,
    )


def _load_fetch_longmemeval():
    spec = importlib.util.spec_from_file_location(
        "fetch_longmemeval", ROOT / "scripts" / "fetch_longmemeval.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_longmemeval_downloads_use_cleaned_files_at_immutable_revision() -> None:
    fetch = _load_fetch_longmemeval()
    assert fetch.REPO == "xiaowu0162/longmemeval-cleaned"
    assert len(fetch.REVISION) == 40 and fetch.REVISION != "main"
    assert fetch.resolve_url("longmemeval_s").endswith(
        f"/{fetch.REVISION}/longmemeval_s_cleaned.json"
    )
    assert fetch.resolve_url("longmemeval_oracle").endswith(
        f"/{fetch.REVISION}/longmemeval_oracle.json"
    )


def test_dataset_download_verifies_pin_before_replacing_destination(tmp_path, monkeypatch) -> None:
    fetch = _load_fetch_longmemeval()
    monkeypatch.setattr(fetch, "DATA_DIR", tmp_path)
    dest = tmp_path / "longmemeval_s.json"
    dest.write_bytes(b"known-good")

    class CorruptResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size):
            if getattr(self, "done", False):
                return b""
            self.done = True
            return b"corrupt"

    monkeypatch.setattr(fetch.urllib.request, "urlopen", lambda request: CorruptResponse())
    expected = hashlib.sha256(b"known-good").hexdigest()
    try:
        fetch.download("longmemeval_s", expected)
        raise AssertionError("expected pin mismatch")
    except ValueError as error:
        assert "sha256" in str(error)
    assert dest.read_bytes() == b"known-good"


def test_corrupt_first_download_leaves_no_dataset_file(tmp_path, monkeypatch) -> None:
    fetch = _load_fetch_longmemeval()
    monkeypatch.setattr(fetch, "DATA_DIR", tmp_path)

    class CorruptResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size):
            if getattr(self, "done", False):
                return b""
            self.done = True
            return b"corrupt"

    monkeypatch.setattr(fetch.urllib.request, "urlopen", lambda request: CorruptResponse())
    try:
        fetch.download("longmemeval_s", hashlib.sha256(b"expected").hexdigest())
        raise AssertionError("expected pin mismatch")
    except ValueError:
        pass
    assert not (tmp_path / "longmemeval_s.json").exists()


def test_cleaned_split_manifest_recomputes_exposure_and_answer_session_disjointness() -> None:
    dataset_path = ROOT / "benchmarks" / "data" / "longmemeval_s.json"
    if not dataset_path.exists():
        return
    fetch = _load_fetch_longmemeval()
    split = fetch.build_split_manifest(dataset_path)
    assert split["exposed_development"]["count"] == 178
    assert split["answer_bearing_session_disjoint_confirmation"]["count"] == 319
    assert split["strict_all_haystack_session_disjoint_confirmation"]["count"] == 0
    rows = {row["question_id"]: row for row in json.loads(dataset_path.read_text())}
    exposed_answers = {
        session_id
        for question_id in split["exposed_development"]["question_ids"]
        for session_id in rows[question_id]["answer_session_ids"]
    }
    for question_id in split["answer_bearing_session_disjoint_confirmation"][
        "question_ids"
    ]:
        assert exposed_answers.isdisjoint(rows[question_id]["answer_session_ids"])


def test_normalized_containment_is_case_and_punct_insensitive() -> None:
    reader = _load_run_reader()
    assert reader.contains_gold("The answer is Business Administration.", "business administration")
    assert reader.contains_gold("It was on May 30, 2023!", "May 30 2023")
    assert not reader.contains_gold("I don't know", "Business Administration")
    # Empty gold never matches (no vacuous credit).
    assert not reader.contains_gold("anything", "")
    # Word-boundary: "2" must not match inside "32".
    assert not reader.contains_gold("It was 32 degrees", "2")
    assert reader.contains_gold("The answer is 2 miles", "2")


def test_reader_output_is_strict_json_with_exact_schema() -> None:
    reader = _load_run_reader()
    assert reader.parse_reader_output(
        '{"notes":"The evidence says Paris.","answer":"Paris","abstain":false}'
    ) == {"notes": "The evidence says Paris.", "answer": "Paris", "abstain": False}
    for invalid in [
        '```json\n{"notes":"","answer":"Paris","abstain":false}\n```',
        '{"notes":"","answer":"Paris","abstain":false,"extra":1}',
        '{"notes":[],"answer":"Paris","abstain":false}',
        '{"notes":"","answer":"","abstain":false}',
        '{"notes":"","answer":null,"abstain":false}',
        '{"notes":"","answer":"Paris","abstain":true}',
    ]:
        try:
            reader.parse_reader_output(invalid)
            raise AssertionError(f"expected invalid output: {invalid}")
        except ValueError:
            pass


def test_judge_grades_only_structured_answer_not_notes_or_negated_gold(tmp_path) -> None:
    reader = _load_run_reader()
    cli = reader.ReaderCli("codex", "reader", "judge", tmp_path, 0)
    calls = []
    cli.call = lambda kind, system, prompt: calls.append(prompt) or "no"
    row = {
        "question": "Where did I go?",
        "question_type": "single-session-user",
        "gold_answer": "Paris",
        "is_abstention": False,
    }
    correct, method = reader.judge_row(
        cli,
        row,
        {"notes": "Paris appears in evidence.", "answer": "London", "abstain": False},
    )
    assert (correct, method) == (False, "llm_judge")
    assert "Paris appears in evidence" not in calls[0]
    correct, method = reader.judge_row(
        cli, row, {"notes": "", "answer": "not Paris", "abstain": False}
    )
    assert (correct, method) == (False, "llm_judge")


def test_mismatched_final_number_and_exact_abstention_are_incorrect(tmp_path) -> None:
    reader = _load_run_reader()
    cli = reader.ReaderCli("codex", "reader", "judge", tmp_path, 0)
    cli.call = lambda kind, system, prompt: "no"
    row = {
        "question": "How many?",
        "question_type": "multi-session",
        "gold_answer": "50",
        "is_abstention": False,
    }
    assert reader.judge_row(
        cli, row, {"notes": "60 - 10 = 50", "answer": "40", "abstain": False}
    ) == (False, "llm_judge")
    abstention = {
        "question": "Unknown?",
        "question_type": "single-session-user",
        "gold_answer": "",
        "is_abstention": True,
    }
    assert reader.judge_row(
        cli, abstention, {"notes": "", "answer": None, "abstain": True}
    ) == (True, "abstention_exact")
    assert reader.judge_row(
        cli, abstention, {"notes": "", "answer": "I don't know", "abstain": False}
    ) == (False, "abstention_exact")


def test_non_exact_judge_verdict_is_a_failure(tmp_path) -> None:
    reader = _load_run_reader()
    cli = reader.ReaderCli("codex", "reader", "judge", tmp_path, 0)
    cli.call = lambda kind, system, prompt: "yes, because it is equivalent"
    try:
        reader.judge_row(
            cli,
            {
                "question": "Where?",
                "question_type": "single-session-user",
                "gold_answer": "Paris",
                "is_abstention": False,
            },
            {"notes": "", "answer": "the French capital", "abstain": False},
        )
        raise AssertionError("expected JudgeFailure")
    except reader.JudgeFailure:
        pass


def test_bootstrap_ci_is_deterministic_and_brackets_mean() -> None:
    reader = _load_run_reader()
    deltas = [1.0, 0.0, 1.0, 1.0, 0.0, 1.0, 1.0, 1.0]
    first = reader.bootstrap_ci(deltas, 1000, 20260710)
    second = reader.bootstrap_ci(deltas, 1000, 20260710)
    assert first == second
    assert first["ci95_low"] <= first["mean"] <= first["ci95_high"]
    assert first["ci_excludes_zero"]
    null = reader.bootstrap_ci([0.0, 0.0, 1.0, -1.0], 1000, 7)
    assert not null["ci_excludes_zero"]
    empty = reader.bootstrap_ci([], 1000, 7)
    assert not empty["ci_excludes_zero"]


def test_reader_prompt_contains_evidence_and_question_date() -> None:
    reader = _load_run_reader()
    row = {
        "question": "What did I adopt?",
        "question_date": "2023/05/30 (Tue) 23:40",
        "evidence": [
            {"rank": 1, "session_id": "s1", "body": "[session s1] [date d] user: I adopted a dog"}
        ],
    }
    prompt = reader.build_reader_prompt(row)
    assert "I adopted a dog" in prompt
    assert "Question date: 2023/05/30 (Tue) 23:40" in prompt
    assert prompt.rstrip().endswith("What did I adopt?")
    empty = reader.build_reader_prompt(
        {"question": "Q?", "question_date": None, "evidence": []}
    )
    assert "(no evidence was retrieved)" in empty
    assert "Question date: unknown" in empty


def test_reader_cli_cache_is_keyed_by_engine_and_model(tmp_path) -> None:
    reader = _load_run_reader()
    codex = reader.ReaderCli("codex", "gpt-5.6-luna", "gpt-5.6-terra", tmp_path, 0)
    claude = reader.ReaderCli(
        "claude", "gpt-5.6-luna", "gpt-5.6-terra", tmp_path, 0
    )
    # Same prompts, different engine -> different cache entries.
    codex_key = codex._cache_path("reader", "sys", "prompt")
    claude_key = claude._cache_path("reader", "sys", "prompt")
    assert codex_key != claude_key
    # Judge calls key on the judge model, reader calls on the reader model.
    assert codex._cache_path("judge", "sys", "prompt") != codex_key
    # A pre-seeded cache entry is served without spending budget (max_calls=0).
    codex_key.write_text('{"reply": "cached-answer"}')
    assert codex.call("reader", "sys", "prompt") == "cached-answer"
    assert codex.fresh_calls == 0 and codex.cached_calls == 1
    # Exhausted budget must raise rather than fall through.
    codex_judge = codex._cache_path("judge", "sys", "prompt")
    assert not codex_judge.exists()
    try:
        codex.call("judge", "sys", "prompt")
        raise AssertionError("expected CallBudgetExceeded")
    except reader.CallBudgetExceeded:
        pass


def test_unknown_engine_is_rejected(tmp_path) -> None:
    reader = _load_run_reader()
    try:
        reader.ReaderCli("not-a-real-engine", "m", "m", tmp_path, 0)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_openrouter_requires_api_key(tmp_path, monkeypatch) -> None:
    reader = _load_run_reader()
    attempts = _load_provider_attempts()
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    cli = reader.ReaderCli(
        "openrouter",
        "m",
        "m",
        tmp_path / "cache",
        1,
        max_spend_usd=Decimal("1"),
        max_price_per_million={"prompt": Decimal("1"), "completion": Decimal("1")},
    )
    cli.set_provider_attempt_ledger(
        _open_test_ledger(attempts, tmp_path / "attempts.json")
    )
    try:
        cli.call("reader", "sys", "prompt")
        raise AssertionError("expected RuntimeError")
    except RuntimeError as error:
        assert "OPENROUTER_API_KEY" in str(error)


def test_paid_authorization_drift_fails_before_reader_or_provider_access(
    tmp_path, monkeypatch
) -> None:
    reader = _load_run_reader()
    evidence = tmp_path / "evidence.jsonl"
    retrieval = tmp_path / "retrieval.json"
    evidence.write_text("{}\n")
    retrieval.write_text("{}\n")
    frozen = {
        "baseline_evidence_sha256": reader._file_sha256(evidence),
        "baseline_retrieval_sha256": reader._file_sha256(retrieval),
        "reader_runner_sha256": reader._file_sha256(Path(reader.__file__)),
        "provider_attempts_sha256": reader._file_sha256(
            ROOT / "scripts" / "provider_attempts.py"
        ),
    }
    models = {
        "provider": "OpenRouter",
        "reader": "reader-model",
        "reader_reasoning_effort": "medium",
        "judge": "judge-model",
        "judge_profile": "rag-supported-v1",
        "prompt_version": 3,
        "max_output_tokens_per_request": 1024,
        "provider_max_price_usd_per_million": {
            "prompt": "2.75", "completion": "16.5"
        },
    }
    hard_limits = {
        "baseline": {
            "max_logical_calls": 10,
            "max_provider_attempts": 10,
            "max_spend_usd": "3",
        }
    }
    execution = {"baseline": {
        "run_id": "baseline",
        "attempt_ledger": "attempts.jsonl",
        "cache_dir": "cache",
        "output": "out.json",
    }}
    scope = {
        "frozen_inputs": frozen,
        "models": models,
        "hard_limits": hard_limits,
        "execution": execution,
    }
    manifest = {
        "schema_version": 2,
        "status": "AUTHORIZED_FOR_PAID_EXECUTION",
        **scope,
        "authorization": {
            "authorized_by": "test-user",
            "authorized_at": "2026-07-23T00:00:00Z",
            "authorization_scope_sha256": reader.sha256_text(
                json.dumps(scope, sort_keys=True, separators=(",", ":"))
            ),
        },
    }
    manifest_path = tmp_path / "authorization.json"
    manifest_path.write_text(json.dumps(manifest))
    evidence.write_text('{"drift":true}\n')
    constructors = []
    monkeypatch.setattr(reader, "ReaderCli", lambda *_args, **_kwargs: constructors.append(1))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_reader.py", "--evidence", str(evidence), "--retrieval-report", str(retrieval),
            "--out", str(tmp_path / "out.json"), "--engine", "openrouter",
            "--model", "reader-model", "--judge-model", "judge-model",
            "--judge-profile", "rag-supported-v1", "--prompt-version", "3",
            "--reasoning-effort", "medium", "--max-calls", "10",
            "--max-output-tokens", "1024", "--max-provider-attempts", "10",
            "--max-spend-usd", "3", "--max-price-prompt-per-million", "2.75",
            "--max-price-completion-per-million", "16.5", "--attempt-ledger",
            str(tmp_path / "attempts.jsonl"), "--authorization-manifest", str(manifest_path),
            "--authorization-arm", "baseline",
        ],
    )

    with pytest.raises(SystemExit):
        reader.main()
    assert constructors == []
    assert not (tmp_path / "attempts.jsonl").exists()


def test_paid_authorization_rejects_fresh_ledger_replay(tmp_path, monkeypatch) -> None:
    reader = _load_run_reader()
    monkeypatch.setattr(reader, "REPO_ROOT", tmp_path)
    evidence = tmp_path / "evidence.jsonl"
    retrieval = tmp_path / "retrieval.json"
    evidence.write_text("{}\n")
    retrieval.write_text("{}\n")
    canonical_ledger = tmp_path / "attempts.jsonl"
    cache = tmp_path / "cache"
    output = tmp_path / "out.json"
    frozen = {
        "baseline_evidence_sha256": reader._file_sha256(evidence),
        "baseline_retrieval_sha256": reader._file_sha256(retrieval),
        "reader_runner_sha256": reader._file_sha256(Path(reader.__file__)),
        "provider_attempts_sha256": reader._file_sha256(ROOT / "scripts/provider_attempts.py"),
    }
    models = {
        "provider": "OpenRouter", "reader": "reader", "reader_reasoning_effort": "medium",
        "judge": "judge", "judge_profile": "rag-supported-v1", "prompt_version": 3,
        "max_output_tokens_per_request": 1024,
        "provider_max_price_usd_per_million": {"prompt": "2.75", "completion": "16.5"},
    }
    hard_limits = {"baseline": {
        "max_logical_calls": 10, "max_provider_attempts": 10, "max_spend_usd": "3",
    }}
    execution = {"baseline": {
        "run_id": "baseline", "attempt_ledger": "attempts.jsonl",
        "cache_dir": "cache", "output": "out.json",
    }}
    scope = {"frozen_inputs": frozen, "models": models, "hard_limits": hard_limits, "execution": execution}
    manifest = {
        "schema_version": 2, "status": "AUTHORIZED_FOR_PAID_EXECUTION", **scope,
        "authorization": {
            "authorized_by": "test", "authorized_at": "2026-07-23T00:00:00Z",
            "authorization_scope_sha256": reader.sha256_text(
                json.dumps(scope, sort_keys=True, separators=(",", ":"))
            ),
        },
    }
    manifest_path = tmp_path / "authorization.json"
    manifest_path.write_text(json.dumps(manifest))
    kwargs = dict(
        arm="baseline", evidence_path=evidence, retrieval_report_path=retrieval,
        reader_model="reader", judge_model="judge", judge_profile="rag-supported-v1",
        prompt_version=3, reasoning_effort="medium", max_calls=10,
        max_provider_attempts=10, max_output_tokens=1024, max_spend_usd=Decimal("3"),
        max_price_prompt_per_million=Decimal("2.75"),
        max_price_completion_per_million=Decimal("16.5"), cache_dir=cache,
        output_path=output,
    )
    reader.validate_paid_authorization(
        manifest_path, attempt_ledger_path=canonical_ledger, **kwargs
    )
    with pytest.raises(ValueError, match="attempt_ledger drift"):
        reader.validate_paid_authorization(
            manifest_path, attempt_ledger_path=tmp_path / "fresh.jsonl", **kwargs
        )


def test_openrouter_cache_key_includes_engine_model_and_effort(tmp_path, monkeypatch) -> None:
    reader = _load_run_reader()
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-not-real")
    a = reader.ReaderCli("openrouter", "openai/gpt-5.6-terra", "anthropic/claude-sonnet-5", tmp_path, 0)
    b = reader.ReaderCli(
        "openrouter", "openai/gpt-5.6-terra", "anthropic/claude-sonnet-5", tmp_path, 0,
        reasoning_effort="low",
    )
    # Same engine/models, reasoning effort differs -> different cache entries.
    assert a._cache_path("reader", "sys", "prompt") != b._cache_path("reader", "sys", "prompt")
    # Reader and judge use different models on this engine -> different cache entries.
    assert a._cache_path("reader", "sys", "prompt") != a._cache_path("judge", "sys", "prompt")
    # A codex ReaderCli with the same reader model still keys separately (engine is part of the key).
    codex = reader.ReaderCli("codex", "openai/gpt-5.6-terra", "anthropic/claude-sonnet-5", tmp_path, 0)
    assert codex._cache_path("reader", "sys", "prompt") != a._cache_path("reader", "sys", "prompt")


def test_cache_key_includes_response_schema_and_decoding_identity(tmp_path, monkeypatch) -> None:
    reader = _load_run_reader()
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-not-real")
    cli = reader.ReaderCli("openrouter", "reader", "judge", tmp_path, 0)
    original = cli._cache_path("reader", "sys", "prompt")
    capped = reader.ReaderCli(
        "openrouter", "reader", "judge", tmp_path, 0, max_output_tokens=2048
    )
    assert capped._cache_path("reader", "sys", "prompt") != original
    monkeypatch.setitem(
        reader.READER_JSON_SCHEMA["properties"]["notes"], "maxLength", 10
    )
    assert cli._cache_path("reader", "sys", "prompt") != original


def test_openrouter_uses_strict_provider_schemas_for_reader_and_judge(
    tmp_path, monkeypatch
) -> None:
    reader = _load_run_reader()
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-not-real")
    payloads = []

    def fake_urlopen(request, timeout=None):
        payload = json.loads(request.data)
        payloads.append(payload)
        content = (
            '{"notes":"","answer":"Paris","abstain":false}'
            if payload["response_format"]["json_schema"]["name"] == "reader_output"
            else '{"verdict":"yes"}'
        )
        return _FakeHttpResponse({"choices": [{"message": {"content": content}}]})

    monkeypatch.setattr(reader.urllib.request, "urlopen", fake_urlopen)
    cli = reader.ReaderCli("openrouter", "reader", "judge", tmp_path, 2)
    assert cli._call_openrouter("reader", "sys", "prompt").startswith("{")
    assert cli._call_openrouter("judge", "sys", "prompt") == '{"verdict":"yes"}'
    reader_format, judge_format = [p["response_format"] for p in payloads]
    assert reader_format["type"] == judge_format["type"] == "json_schema"
    assert reader_format["json_schema"]["strict"] is True
    assert reader_format["json_schema"]["schema"] == reader.READER_JSON_SCHEMA
    assert judge_format["json_schema"]["strict"] is True
    assert judge_format["json_schema"]["schema"] == reader.JUDGE_JSON_SCHEMA
    assert all(payload["provider"]["require_parameters"] is True for payload in payloads)


def test_flash_reader_pins_ai_studio_and_has_reasoning_headroom(
    tmp_path, monkeypatch
) -> None:
    reader = _load_run_reader()
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-not-real")
    payloads = []

    def fake_urlopen(request, timeout=None):
        payloads.append(json.loads(request.data))
        return _FakeHttpResponse(
            {
                "model": reader.FLASH_MODEL,
                "provider": "Google AI Studio",
                "choices": [
                    {
                        "message": {
                            "content": '{"notes":"","answer":"7640","abstain":false}'
                        }
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "cost": 0.01},
            }
        )

    monkeypatch.setattr(reader.urllib.request, "urlopen", fake_urlopen)
    cli = reader.ReaderCli(
        "openrouter", reader.FLASH_MODEL, reader.FLASH_MODEL, tmp_path, 1,
        reasoning_effort="high",
    )
    assert reader.parse_reader_output(cli._call_openrouter("reader", "sys", "prompt"))["answer"] == "7640"
    assert payloads[0]["max_tokens"] == 8192
    assert payloads[0]["provider"] == {
        "require_parameters": True,
        "only": [reader.FLASH_PROVIDER],
        "allow_fallbacks": True,
    }


def test_luna_reader_omits_unsupported_temperature(tmp_path, monkeypatch) -> None:
    reader = _load_run_reader()
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-not-real")
    payloads = []

    def fake_urlopen(request, timeout=None):
        payloads.append(json.loads(request.data))
        return _FakeHttpResponse({
            "model": "openai/gpt-5.6-luna-pro",
            "provider": "OpenAI",
            "choices": [{"message": {"content": '{"notes":"","answer":"ok","abstain":false}'}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": 0.01},
        })

    monkeypatch.setattr(reader.urllib.request, "urlopen", fake_urlopen)
    cli = reader.ReaderCli(
        "openrouter", "openai/gpt-5.6-luna-pro", "openai/gpt-5.6-luna-pro",
        tmp_path, 1, reasoning_effort="high",
    )
    cli._call_openrouter("reader", "sys", "prompt")

    assert payloads[0]["max_tokens"] == 8192
    assert "temperature" not in payloads[0]


def test_terra_proposal_uses_supported_decoding_and_exact_schema(
    tmp_path, monkeypatch
) -> None:
    reader = _load_run_reader()
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-not-real")
    payloads = []

    def fake_urlopen(request, timeout=None):
        payloads.append(json.loads(request.data))
        return _FakeHttpResponse({
            "model": "openai/gpt-5.6-terra",
            "provider": "OpenAI",
            "choices": [{"message": {"content": (
                '{"selected_indices":[1],"replacement_text":"updated"}'
            )}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": 0.001},
        })

    monkeypatch.setattr(reader.urllib.request, "urlopen", fake_urlopen)
    cli = reader.ReaderCli(
        "openrouter", "openai/gpt-5.6-terra", "judge-model", tmp_path, 1,
        reasoning_effort="medium", max_output_tokens=256,
    )
    cli._call_openrouter("forget_supersede", "sys", "prompt")

    payload = payloads[0]
    assert payload["model"] == "openai/gpt-5.6-terra"
    assert payload["max_tokens"] == 256
    assert "temperature" not in payload
    assert payload["reasoning"] == {"effort": "medium"}
    assert payload["response_format"]["json_schema"] == {
        "name": "forget_supersede_output",
        "strict": True,
        "schema": reader.FORGET_SUPERSEDE_JSON_SCHEMA,
    }


def test_forgeteval_release_schema_allows_empty_selection() -> None:
    reader = _load_run_reader()
    schema = reader.response_contract(
        "openrouter", "forget_release", "openai/gpt-5.6-terra"
    )["response_format"]["json_schema"]["schema"]
    assert "uniqueItems" not in schema["properties"]["selected_indices"]
    assert "minItems" not in schema["properties"]["selected_indices"]


def test_packing_sufficiency_schema_is_strict_and_bounded() -> None:
    reader = _load_run_reader()
    contract = reader.response_contract(
        "openrouter", "packing_sufficiency", "openai/gpt-5.6-terra"
    )
    schema = contract["response_format"]["json_schema"]["schema"]

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["properties"]["selected_ranks"]["items"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 10,
    }


def test_judge_parser_requires_schema_valid_exact_enum() -> None:
    reader = _load_run_reader()
    assert reader.parse_judge_output('{"verdict":"yes"}', "openrouter") == "yes"
    assert reader.parse_judge_output("no", "codex") == "no"
    for invalid in [
        '{"verdict":"maybe"}',
        '{"verdict":"yes","reason":"ok"}',
        '{"verdict":true}',
        "yes",
    ]:
        try:
            reader.parse_judge_output(invalid, "openrouter")
            raise AssertionError(f"expected invalid judge output: {invalid}")
        except reader.JudgeFailure:
            pass


def test_rag_supported_parser_is_strict_and_validates_evidence_ranks() -> None:
    reader = _load_run_reader()
    raw = (
        '{"answer_correct":true,"fully_supported":true,'
        '"supporting_evidence_ranks":[1,3]}'
    )
    assert reader.parse_rag_supported_judge_output(raw, {1, 2, 3}) == {
        "answer_correct": True,
        "fully_supported": True,
        "supporting_evidence_ranks": [1, 3],
    }
    invalid = [
        '{"answer_correct":true,"fully_supported":true,"supporting_evidence_ranks":[]}',
        '{"answer_correct":true,"fully_supported":true,"supporting_evidence_ranks":[4]}',
        '{"answer_correct":true,"fully_supported":true,"supporting_evidence_ranks":[1,1]}',
        '{"answer_correct":1,"fully_supported":true,"supporting_evidence_ranks":[1]}',
        '{"answer_correct":true,"fully_supported":true,"supporting_evidence_ranks":[1],"extra":1}',
    ]
    for reply in invalid:
        try:
            reader.parse_rag_supported_judge_output(reply, {1, 2, 3})
            raise AssertionError(f"expected invalid RAG judge output: {reply}")
        except reader.JudgeFailure:
            pass


def test_rag_supported_openrouter_schema_uses_provider_supported_subset() -> None:
    reader = _load_run_reader()
    ranks = reader.RAG_SUPPORTED_JUDGE_JSON_SCHEMA["properties"][
        "supporting_evidence_ranks"
    ]
    assert "uniqueItems" not in ranks
    try:
        reader.parse_rag_supported_judge_output(
            '{"answer_correct":true,"fully_supported":true,'
            '"supporting_evidence_ranks":[1,1]}',
            {1},
        )
        raise AssertionError("duplicate ranks must remain invalid")
    except reader.JudgeFailure:
        pass


def test_rag_supported_judge_records_raw_parse_and_no_fallback(tmp_path) -> None:
    reader = _load_run_reader()
    cli = reader.ReaderCli("codex", "reader", "judge", tmp_path, 0)
    calls = []
    cli.call = lambda kind, system, prompt: calls.append((kind, prompt)) or "not-json"
    row = {
        "question": "Which store is used?",
        "question_type": "specs",
        "gold_answer": "Postgres",
        "is_abstention": False,
        "evidence": [{"rank": 1, "body": "The service uses Postgres."}],
    }
    result = reader.judge_rag_row(
        cli, row, {"notes": "hidden", "answer": "Postgres", "abstain": False}
    )
    assert result["correct"] is False
    assert result["judge_raw_response"] == "not-json"
    assert result["judge_parse_status"] == "invalid"
    assert result["judge_fallback_used"] is False
    assert result["judge_error"]
    assert calls[0][0] == "rag_judge"
    assert "The service uses Postgres." in calls[0][1]
    assert "Reference answer: Postgres" in calls[0][1]
    assert "hidden" not in calls[0][1]


def test_openrouter_rag_and_pair_judges_use_distinct_strict_contracts(
    tmp_path, monkeypatch
) -> None:
    reader = _load_run_reader()
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-not-real")
    cli = reader.ReaderCli("openrouter", "reader", "judge", tmp_path, 0)
    assert reader.response_contract("openrouter", "rag_judge")["response_format"][
        "json_schema"
    ]["schema"] == reader.RAG_SUPPORTED_JUDGE_JSON_SCHEMA
    assert reader.response_contract("openrouter", "pair_judge")["response_format"][
        "json_schema"
    ]["schema"] == reader.PAIRED_RAG_JUDGE_JSON_SCHEMA
    keys = {
        cli._cache_path(kind, "sys", "prompt")
        for kind in ("judge", "rag_judge", "pair_judge")
    }
    assert len(keys) == 3
    assert cli.model_for("rag_judge") == cli.model_for("pair_judge") == "judge"


def test_swapped_pair_adjudication_requires_position_consistency(tmp_path) -> None:
    reader = _load_run_reader()
    cli = reader.ReaderCli("codex", "reader", "judge", tmp_path, 0)
    prompts = []

    def position_consistent_reply(kind, system, prompt):
        prompts.append(prompt)
        current_is_a = prompt.index("Current evidence") < prompt.index("Answer B")
        return json.dumps({"verdict": "a" if current_is_a else "b"})

    cli.call = position_consistent_reply
    common = {
        "question_id": "docs-q1",
        "question": "Which store is used?",
        "gold_answer": "Postgres",
    }
    current = {
        "answer": "Postgres",
        "evidence": [{"rank": 1, "body": "Current evidence says Postgres."}],
        "correct": True,
    }
    baseline = {
        "answer": "SQLite",
        "evidence": [{"rank": 1, "body": "Baseline evidence says SQLite."}],
        "correct": False,
    }
    result = reader.adjudicate_supported_flip(
        cli, common, current, baseline, seed=20260713
    )
    assert result["status"] == "resolved"
    assert result["canonical_verdicts"] == ["current", "current"]
    assert len(result["raw_responses"]) == 2
    assert result["orders"][0] != result["orders"][1]
    assert all("Current evidence says Postgres." in prompt for prompt in prompts)

    cli2 = reader.ReaderCli("codex", "reader", "judge", tmp_path / "other", 0)
    inconsistent = iter(['{"verdict":"a"}', '{"verdict":"a"}'])
    cli2.call = lambda kind, system, prompt: next(inconsistent)
    result = reader.adjudicate_supported_flip(
        cli2, common, current, baseline, seed=20260713
    )
    assert result["status"] == "position_disagreement"
    assert set(result["canonical_verdicts"]) == {"current", "baseline"}


def test_rag_supported_main_binds_audit_and_swapped_flip_adjudication(
    tmp_path, monkeypatch
) -> None:
    reader = _load_run_reader()

    def write_evidence(path, body):
        path.write_text(
            json.dumps(
                {
                    "question_id": "docs-q1",
                    "question_type": "specs",
                    "is_abstention": False,
                    "question": "Which store is used?",
                    "question_date": None,
                    "gold_answer": "Postgres",
                    "evidence": [{"rank": 1, "session_id": None, "body": body}],
                }
            )
            + "\n"
        )

    baseline_evidence = tmp_path / "baseline-evidence.jsonl"
    current_evidence = tmp_path / "current-evidence.jsonl"
    write_evidence(baseline_evidence, "The old draft says SQLite.")
    write_evidence(current_evidence, "The current specification says Postgres.")
    baseline_report = tmp_path / "baseline-report.json"
    current_report = tmp_path / "current-report.json"

    def fake_call(self, kind, system_prompt, prompt):
        if kind == "reader":
            answer = "Postgres" if "current specification" in prompt else "SQLite"
            return json.dumps({"notes": "", "answer": answer, "abstain": False})
        if kind == "rag_judge":
            correct = "Candidate answer: Postgres" in prompt
            return json.dumps(
                {
                    "answer_correct": correct,
                    "fully_supported": True,
                    "supporting_evidence_ranks": [1],
                }
            )
        if kind == "pair_judge":
            a_is_current = prompt.index("current specification") < prompt.index("Answer B")
            return json.dumps({"verdict": "a" if a_is_current else "b"})
        raise AssertionError(kind)

    monkeypatch.setattr(reader.ReaderCli, "call", fake_call)

    def run(evidence, out, label, baseline=None):
        argv = [
            "run_reader.py",
            "--evidence",
            str(evidence),
            "--out",
            str(out),
            "--label",
            label,
            "--judge-profile",
            "rag-supported-v1",
            "--cache-dir",
            str(tmp_path / "cache"),
            "--seed",
            "20260713",
        ]
        if baseline is not None:
            argv.extend(["--baseline", str(baseline)])
        monkeypatch.setattr(reader.sys, "argv", argv)
        assert reader.main() == 0

    run(baseline_evidence, baseline_report, "baseline")
    run(current_evidence, current_report, "current", baseline_report)
    report = json.loads(current_report.read_text())
    row = report["per_question"][0]
    assert report["judge_profile"] == "rag-supported-v1"
    assert row["answer_correct"] is True and row["fully_supported"] is True
    assert row["judge_parse_status"] == "strict_valid"
    assert row["judge_fallback_used"] is False
    assert row["judge_raw_response"]
    adjudication = report["paired_vs_baseline"]["supported_flip_adjudication"]
    assert adjudication[0]["status"] == "resolved"
    assert len(adjudication[0]["raw_responses"]) == 2
    fingerprint = report["evaluator_fingerprint"]
    assert fingerprint["judge_profile"] == "rag-supported-v1"
    assert fingerprint["fallback_policy"] == "none_fail_closed"
    assert fingerprint["rag_supported_judge_schema_sha256"] == reader.sha256_text(
        json.dumps(
            reader.RAG_SUPPORTED_JUDGE_JSON_SCHEMA,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def test_reasoning_effort_is_part_of_cache_identity_and_codex_or_openrouter_only(tmp_path) -> None:
    reader = _load_run_reader()
    default = reader.ReaderCli("codex", "gpt-5.6-terra", "gpt-5.6-terra", tmp_path, 0)
    medium = reader.ReaderCli(
        "codex", "gpt-5.6-terra", "gpt-5.6-terra", tmp_path, 0,
        reasoning_effort="medium",
    )
    assert default._cache_path("reader", "sys", "p") != medium._cache_path(
        "reader", "sys", "p"
    )
    assert medium.cache_model_for("reader") == "gpt-5.6-terra@medium"
    assert medium.model_for("reader") == "gpt-5.6-terra"
    try:
        reader.ReaderCli("claude", "m", "m", tmp_path, 0, reasoning_effort="high")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_every_reader_system_prompt_requires_the_structured_output_contract() -> None:
    reader = _load_run_reader()
    for prompt in (
        reader.READER_SYSTEM_PROMPT,
        reader.READER_SYSTEM_PROMPT_V2,
        reader.READER_SYSTEM_PROMPT_V3_TERSE,
    ):
        assert '{"notes": string, "answer": string|null, "abstain": boolean}' in prompt
        assert "reply exactly: I don't know" not in prompt
        assert "final line" not in prompt
    assert reader.READER_SYSTEM_PROMPTS[1] is reader.READER_SYSTEM_PROMPT
    assert reader.READER_SYSTEM_PROMPTS[2] == reader.READER_SYSTEM_PROMPT_V2
    assert reader.READER_SYSTEM_PROMPT_V2 != reader.READER_SYSTEM_PROMPT


def test_reader_prompt_hashes_are_stable() -> None:
    reader = _load_run_reader()
    assert {
        "v1": reader.sha256_text(reader.READER_SYSTEM_PROMPT),
        "v2": reader.sha256_text(reader.READER_SYSTEM_PROMPT_V2),
        "v3_terse": reader.sha256_text(reader.READER_SYSTEM_PROMPT_V3_TERSE),
    } == {
        "v1": "60a7d7da236692982f9ad7d127bc5fcf358edbd07a5f7eb55ca540dd5b68c7fb",
        "v2": "eeb2e2c42a4d603f90ef3bd75f380e3d946b047ffb68649c26fdc6136d41ae99",
        "v3_terse": "97eeeae3e9cffe3485d7fa19db8eeedb6407bbd9c5f3759980c89621786327e1",
    }


def test_prompt_v3_router_table() -> None:
    reader = _load_run_reader()
    # temporal-reasoning always routes to the v2 CoT prompt, regardless of
    # question text.
    route, prompt = reader.route_v3("temporal-reasoning", "What did I do first?")
    assert route == "cot"
    assert prompt == reader.READER_SYSTEM_PROMPT_V2
    # A counting cue routes to CoT even in a non-temporal stratum.
    route, prompt = reader.route_v3(
        "multi-session", "How many books did I mention reading this year?"
    )
    assert route == "cot"
    assert prompt == reader.READER_SYSTEM_PROMPT_V2
    # A non-counting, non-temporal question routes to the terse v3 route.
    route, prompt = reader.route_v3(
        "single-session-preference", "What is my favorite coffee order?"
    )
    assert route == "terse"
    assert prompt == reader.READER_SYSTEM_PROMPT_V3_TERSE


def test_prompt_v3_counting_cues_are_case_insensitive_and_whole_word() -> None:
    reader = _load_run_reader()
    for cue_question in [
        "How many dogs do I have?",
        "how much did I spend total?",
        "How often do I go running?",
        "What is the number of items I bought?",
        "What was the total cost?",
        "Please count the sessions.",
    ]:
        assert reader.is_counting_question(cue_question), cue_question
    # Whole-word guard: "totally"/"discount"/"recount" must not false-positive.
    for non_cue_question in [
        "What did I totally forget to mention?",
        "Did I get a discount on the ticket?",
        "Can you recount the story?",
    ]:
        assert not reader.is_counting_question(non_cue_question), non_cue_question


def test_prompt_v3_abstention_text_present_in_both_routes() -> None:
    reader = _load_run_reader()
    assert "Abstain only if NO evidence item bears" in reader.READER_SYSTEM_PROMPT_V2
    assert (
        "Abstain only if NO evidence item bears"
        in reader.READER_SYSTEM_PROMPT_V3_TERSE
    )


def test_prompt_v3_terse_route_keeps_terse_phrasing_but_not_v1_abstention() -> None:
    reader = _load_run_reader()
    assert (
        "Be terse: put only the concise answer"
        in reader.READER_SYSTEM_PROMPT_V3_TERSE
    )
    # v3's terse route does NOT carry v1's plain abstention line -- it uses
    # v2's calibrated-abstention instruction instead (brief requirement 1).
    assert (
        "If the evidence is insufficient to answer, reply exactly"
        not in reader.READER_SYSTEM_PROMPT_V3_TERSE
    )
    assert reader.READER_SYSTEM_PROMPT_V3_TERSE != reader.READER_SYSTEM_PROMPT
    assert reader.READER_SYSTEM_PROMPT_V3_TERSE != reader.READER_SYSTEM_PROMPT_V2


def test_prompt_v3_routing_counts_in_report(tmp_path, monkeypatch) -> None:
    reader = _load_run_reader()
    rows = [
        {
            "question_id": "q1",
            "question_type": "temporal-reasoning",
            "is_abstention": False,
            "question": "What did I do first, the museum or the park?",
            "question_date": "2023/01/01",
            "gold_answer": "the museum",
            "evidence": [],
        },
        {
            "question_id": "q2",
            "question_type": "multi-session",
            "is_abstention": False,
            "question": "How many books did I mention reading?",
            "question_date": "2023/01/01",
            "gold_answer": "3",
            "evidence": [],
        },
        {
            "question_id": "q3",
            "question_type": "single-session-preference",
            "is_abstention": False,
            "question": "What is my favorite coffee order?",
            "question_date": "2023/01/01",
            "gold_answer": "latte",
            "evidence": [],
        },
    ]
    evidence_path = tmp_path / "evidence.jsonl"
    evidence_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    out_path = tmp_path / "report.json"

    # Every reader call just abstains; judge_row handles structured abstention
    # without a "judge"-kind call, so this
    # stub only needs to handle "reader".
    monkeypatch.setattr(
        reader.ReaderCli,
        "call",
        lambda self, kind, system_prompt, prompt: '{"notes":"","answer":null,"abstain":true}',
    )
    monkeypatch.setattr(
        reader.sys,
        "argv",
        [
            "run_reader.py",
            "--evidence",
            str(evidence_path),
            "--out",
            str(out_path),
            "--label",
            "test-v3-routing",
            "--prompt-version",
            "3",
            "--cache-dir",
            str(tmp_path / "cache"),
        ],
    )
    reader.main()
    report = json.loads(out_path.read_text())
    assert report["prompt_version"] == 3
    assert report["routing"] == {"cot": 2, "terse": 1}
    assert report["evaluator_fingerprint"]["response_contract"] == {
        "reader": reader.response_contract("claude", "reader"),
        "judge": reader.response_contract("claude", "judge"),
    }
    fingerprint = report["evaluator_fingerprint"]
    payload = {key: value for key, value in fingerprint.items() if key != "sha256"}
    assert fingerprint["sha256"] == reader.sha256_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":"))
    )
    assert fingerprint["judge_system_prompt_sha256"] == reader.sha256_text(
        reader.JUDGE_SYSTEM_PROMPT
    )
    assert fingerprint["active_reader_prompt_sha256"] == {
        "cot": reader.sha256_text(reader.READER_SYSTEM_PROMPT_V2),
        "terse": reader.sha256_text(reader.READER_SYSTEM_PROMPT_V3_TERSE),
    }


def test_prompt_v1_and_v2_reports_carry_no_routing_breakdown(tmp_path, monkeypatch) -> None:
    reader = _load_run_reader()
    rows = [
        {
            "question_id": "q1",
            "question_type": "single-session-user",
            "is_abstention": False,
            "question": "What's my dog's name?",
            "question_date": "2023/01/01",
            "gold_answer": "Waffles",
            "evidence": [],
        }
    ]
    evidence_path = tmp_path / "evidence.jsonl"
    evidence_path.write_text(json.dumps(rows[0]) + "\n")
    out_path = tmp_path / "report.json"
    monkeypatch.setattr(
        reader.ReaderCli,
        "call",
        lambda self, kind, system_prompt, prompt: '{"notes":"","answer":null,"abstain":true}',
    )
    monkeypatch.setattr(
        reader.sys,
        "argv",
        [
            "run_reader.py",
            "--evidence",
            str(evidence_path),
            "--out",
            str(out_path),
            "--label",
            "test-v1-no-routing",
            "--cache-dir",
            str(tmp_path / "cache"),
        ],
    )
    reader.main()
    report = json.loads(out_path.read_text())
    assert report["prompt_version"] == 1
    assert report["routing"] is None


def test_invalid_baseline_still_writes_ineligible_report(tmp_path, monkeypatch) -> None:
    reader = _load_run_reader()
    row = {
        "question_id": "q1",
        "question_type": "single-session-user",
        "is_abstention": True,
        "question": "What cannot be known?",
        "question_date": None,
        "gold_answer": "",
        "evidence": [],
    }
    evidence_path = tmp_path / "evidence.jsonl"
    evidence_path.write_text(json.dumps(row) + "\n")
    baseline_path = tmp_path / "invalid-baseline.json"
    baseline_path.write_text("{}")
    out_path = tmp_path / "report.json"
    monkeypatch.setattr(
        reader.ReaderCli,
        "call",
        lambda self, kind, system_prompt, prompt: '{"notes":"","answer":null,"abstain":true}',
    )
    monkeypatch.setattr(
        reader.sys,
        "argv",
        [
            "run_reader.py",
            "--evidence",
            str(evidence_path),
            "--out",
            str(out_path),
            "--label",
            "invalid-baseline",
            "--baseline",
            str(baseline_path),
            "--cache-dir",
            str(tmp_path / "cache"),
        ],
    )

    assert reader.main() == 1
    report = json.loads(out_path.read_text())
    assert report["promotion_ineligible"] is True
    assert report["paired_vs_baseline"]["decision"].startswith("HOLD/INVALID")
    assert report["baseline_validation_error"]
    assert report["per_question"][0]["correct"] is True


def test_reader_report_binds_question_and_date_and_rejects_pair_mismatch(tmp_path, monkeypatch) -> None:
    reader = _load_run_reader()
    row = {
        "question_id": "q1",
        "question_type": "single-session-user",
        "is_abstention": True,
        "question": "What cannot be known?",
        "question_date": "2026/07/12",
        "gold_answer": "",
        "evidence": [],
    }
    evidence_path = tmp_path / "evidence.jsonl"
    evidence_path.write_text(json.dumps(row) + "\n")
    out_path = tmp_path / "report.json"
    monkeypatch.setattr(
        reader.ReaderCli,
        "call",
        lambda self, kind, system_prompt, prompt: '{"notes":"","answer":null,"abstain":true}',
    )
    monkeypatch.setattr(
        reader.sys,
        "argv",
        [
            "run_reader.py", "--evidence", str(evidence_path), "--out", str(out_path),
            "--label", "immutable-inputs", "--cache-dir", str(tmp_path / "cache"),
        ],
    )
    assert reader.main() == 0
    report = json.loads(out_path.read_text())
    assert report["per_question"][0]["question"] == row["question"]
    assert report["per_question"][0]["question_date"] == row["question_date"]
    altered = json.loads(json.dumps(report))
    altered["per_question"][0]["question_date"] = "2026/07/13"
    try:
        reader.validate_and_pair_reports(report, altered, "reader")
        raise AssertionError("expected immutable input mismatch")
    except ValueError as error:
        assert "question_date" in str(error)


def test_limit_is_smoke_only_and_never_promotion_eligible(tmp_path, monkeypatch) -> None:
    reader = _load_run_reader()
    rows = [
        {
            "question_id": f"q{index}",
            "question_type": "single-session-user",
            "is_abstention": True,
            "question": f"Question {index}?",
            "question_date": None,
            "gold_answer": "",
            "evidence": [],
        }
        for index in range(2)
    ]
    source = "".join(json.dumps(row) + "\n" for row in rows).encode()
    evidence_path = tmp_path / "evidence.jsonl"
    evidence_path.write_bytes(source)
    out_path = tmp_path / "report.json"
    monkeypatch.setattr(
        reader.ReaderCli,
        "call",
        lambda self, kind, system_prompt, prompt: '{"notes":"","answer":null,"abstain":true}',
    )
    monkeypatch.setattr(
        reader.sys,
        "argv",
        [
            "run_reader.py", "--evidence", str(evidence_path), "--out", str(out_path),
            "--label", "smoke", "--limit", "1", "--cache-dir", str(tmp_path / "cache"),
        ],
    )
    assert reader.main() == 0
    report = json.loads(out_path.read_text())
    assert report["smoke_only"] is True
    assert report["promotion_ineligible"] is True
    assert report["source_expected_n"] == 2
    assert report["evaluated_expected_n"] == report["expected_n"] == 1
    assert report["source_evidence_sha256"] == hashlib.sha256(source).hexdigest()
    evaluated = (json.dumps(rows[0]) + "\n").encode()
    assert report["evaluated_evidence_sha256"] == hashlib.sha256(evaluated).hexdigest()
    try:
        reader.validate_and_pair_reports(report, report, "reader")
        raise AssertionError("expected smoke report rejection")
    except ValueError as error:
        assert "smoke" in str(error)


def test_accuracy_counts_failures_as_incorrect() -> None:
    reader = _load_run_reader()
    rows = [
        {"correct": True},
        {"correct": False},
        {"correct": False},
    ]
    result = reader.accuracy(rows)
    assert result == {"n": 3, "n_scored": 3, "qa_accuracy": 1 / 3}


class _FakeHttpResponse:
    """Minimal stand-in for the context-managed object
    `urllib.request.urlopen` returns: supports `with ... as response:` and
    `.read()` -> bytes, exactly what `_call_openrouter` uses."""

    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode()

    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


def test_openrouter_call_retries_urlerror_and_empty_choices_then_succeeds(
    tmp_path, monkeypatch
) -> None:
    """`_call_openrouter` must retry a transient `URLError` and an
    empty-`choices` response, then return the reply on the third attempt —
    without sleeping for real (the real backoff is `(2, 8, 30)` seconds)."""
    reader = _load_run_reader()
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-not-real")
    sleeps: list[float] = []
    monkeypatch.setattr(reader.time, "sleep", lambda seconds: sleeps.append(seconds))

    outcomes = [
        reader.urllib.error.URLError("connection refused"),
        _FakeHttpResponse({"choices": []}),
        _FakeHttpResponse(
            {"choices": [{"message": {"content": " final answer "}}]}
        ),
    ]
    calls = {"n": 0}

    def fake_urlopen(request, timeout=None):
        outcome = outcomes[calls["n"]]
        calls["n"] += 1
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(reader.urllib.request, "urlopen", fake_urlopen)

    cli = reader.ReaderCli(
        "openrouter", "openai/gpt-5.6-terra", "openai/gpt-5.6-terra", tmp_path, 10
    )
    reply = cli._call_openrouter("reader", "sys", "prompt")

    assert reply == "final answer"
    assert calls["n"] == 3, "one URLError attempt, one empty-choices attempt, one success"
    assert sleeps == [2, 8], "backoff delays before attempts 2 and 3 (attempt 1 has no delay)"


def test_openrouter_call_captures_and_caches_served_model_provider_usage_and_cost(
    tmp_path, monkeypatch
) -> None:
    reader = _load_run_reader()
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-not-real")
    payload = {
        "id": "gen-reader-1",
        "model": "openai/gpt-5.6-luna-pro",
        "openrouter_metadata": {
            "endpoints": {
                "available": [
                    {
                        "model": "openai/gpt-5.6-luna-pro",
                        "provider": "OpenAI",
                        "selected": True,
                    }
                ]
            }
        },
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "cost": 0.0123,
        },
        "choices": [{"message": {"content": '{"notes":"","answer":"Rust","abstain":false}'}}],
    }
    generation = {
        "data": {
            "model": "openai/gpt-5.6-luna-pro-20260709",
            "provider_name": "OpenAI",
            "total_cost": 0.0123,
        }
    }
    requests = []
    def open_response(request, timeout=None):
        requests.append(request)
        if request.full_url == reader.OPENROUTER_URL:
            return _FakeHttpResponse(payload)
        return _FakeHttpResponse(generation)
    monkeypatch.setattr(reader.urllib.request, "urlopen", open_response)
    cli = reader.ReaderCli(
        "openrouter",
        "openai/gpt-5.6-luna-pro",
        "judge",
        tmp_path,
        1,
        max_spend_usd=Decimal("1"),
        max_price_per_million={"prompt": Decimal("1"), "completion": Decimal("6")},
    )
    attempts = _load_provider_attempts()
    ledger = _open_test_ledger(attempts, tmp_path / "attempts.json")
    cli.set_provider_attempt_ledger(ledger)
    assert cli.call("reader", "sys", "prompt").startswith("{")
    assert cli.last_call_metadata == {
        "response_id": "gen-reader-1",
        "requested_model": "openai/gpt-5.6-luna-pro",
        "served_model": "openai/gpt-5.6-luna-pro-20260709",
        "provider": "OpenAI",
        "usage": payload["usage"],
            "elapsed_seconds": cli.last_call_metadata["elapsed_seconds"],
            "retry_index": 0,
            "parse_status": "provider_response_validated",
            "request_sha256": cli.last_call_metadata["request_sha256"],
            "result_sha256": cli.last_call_metadata["result_sha256"],
        }
    assert cli.last_call_metadata["elapsed_seconds"] >= 0
    assert cli.provider_attempt_log == [{"response": cli.last_call_metadata}]
    assert cli.provider_attempts == 1
    assert len(requests) == 2
    assert requests[0].get_header("X-openrouter-metadata") == "enabled"

    cached = reader.ReaderCli(
        "openrouter",
        "openai/gpt-5.6-luna-pro",
        "judge",
        tmp_path,
        0,
        max_spend_usd=Decimal("1"),
        max_price_per_million={"prompt": Decimal("1"), "completion": Decimal("6")},
    )
    cached.set_provider_attempt_ledger(ledger)
    assert cached.call("reader", "sys", "prompt").startswith("{")
    assert cached.last_call_metadata == cli.last_call_metadata
    assert cached.provider_attempts == 0


def test_openrouter_generation_lookup_failure_is_terminal_and_durable(
    tmp_path, monkeypatch
) -> None:
    reader = _load_run_reader()
    attempts = _load_provider_attempts()
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-not-real")
    payload = {
        "id": "gen-paid-before-stats-failure",
        "model": "openai/gpt-5.6-luna-pro",
        "provider": "OpenAI",
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "cost": 0.0123,
        },
        "choices": [
            {"message": {"content": '{"notes":"","answer":"Rust","abstain":false}'}}
        ],
    }
    requests = []
    monkeypatch.setattr(attempts.time, "sleep", lambda _seconds: None)

    def open_response(request, timeout=None):
        requests.append(request.full_url)
        if request.full_url == reader.OPENROUTER_URL:
            return _FakeHttpResponse(payload)
        raise reader.urllib.error.URLError("stats unavailable")

    monkeypatch.setattr(reader.urllib.request, "urlopen", open_response)
    cache_dir = tmp_path / "cache"
    ledger_path = tmp_path / "attempts.json"
    ledger = _open_test_ledger(attempts, ledger_path)
    cli = reader.ReaderCli(
        "openrouter",
        "openai/gpt-5.6-luna-pro",
        "judge",
        cache_dir,
        1,
        max_spend_usd=Decimal("1"),
        max_price_per_million={"prompt": Decimal("1"), "completion": Decimal("6")},
    )
    cli.set_provider_attempt_ledger(ledger)

    try:
        cli.call("reader", "sys", "prompt")
        raise AssertionError("expected generation statistics failure")
    except RuntimeError as error:
        assert "generation statistics" in str(error)

    stored = attempts.load_provider_attempt_ledger_snapshot(ledger_path)
    assert len(stored["attempts"]) == 1
    attempt = stored["attempts"][0]
    assert attempt["status"] == "error"
    assert attempt["result"] is None
    response = attempt["error"]["response"]
    assert response["response_id"] == payload["id"]
    assert response["served_model"] == payload["model"]
    assert response["provider"] == payload["provider"]
    assert response["usage"] == payload["usage"]
    assert response["retry_index"] == 0
    assert response["parse_status"] == "generation_stats_lookup_failed"
    assert len(response["request_sha256"]) == len(response["result_sha256"]) == 64
    assert cli.provider_attempts == 1
    assert len(requests) == 7
    assert requests[0] == reader.OPENROUTER_URL
    assert len(set(requests[1:])) == 1
    assert not cache_dir.exists() or not list(cache_dir.iterdir())


def test_openrouter_generation_lookup_retries_eventual_404(monkeypatch) -> None:
    attempts = _load_provider_attempts()
    calls = []
    sleeps = []

    def open_response(request, timeout=None):
        calls.append(request.full_url)
        if len(calls) < 3:
            raise attempts.urllib.error.HTTPError(
                request.full_url, 404, "Not Found", {}, None
            )
        return _FakeHttpResponse({"data": {"id": "gen-eventual"}})

    monkeypatch.setattr(attempts.urllib.request, "urlopen", open_response)
    monkeypatch.setattr(attempts.time, "sleep", lambda seconds: sleeps.append(seconds))

    lookup = attempts.openrouter_generation_lookup("sk-test-not-real")
    assert lookup("gen-eventual") == {"id": "gen-eventual"}
    assert len(calls) == 3
    assert sleeps == [1, 2]


def test_openrouter_generation_lookup_retries_transient_metadata_failures(
    monkeypatch,
) -> None:
    attempts = _load_provider_attempts()
    calls = []
    sleeps = []

    def open_response(request, timeout=None):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise attempts.urllib.error.URLError("connection reset by peer")
        if len(calls) == 2:
            raise attempts.urllib.error.HTTPError(
                request.full_url,
                503,
                "Service Unavailable",
                {"Retry-After": "7"},
                None,
            )
        return _FakeHttpResponse({"data": {"id": "gen-recovered"}})

    monkeypatch.setattr(attempts.urllib.request, "urlopen", open_response)
    monkeypatch.setattr(attempts.time, "sleep", lambda seconds: sleeps.append(seconds))

    lookup = attempts.openrouter_generation_lookup("sk-test-not-real")
    assert lookup("gen-recovered") == {"id": "gen-recovered"}
    assert len(calls) == 3
    assert sleeps == [1, 7]


def test_openrouter_call_raises_runtime_error_after_four_failed_attempts(
    tmp_path, monkeypatch
) -> None:
    """All 4 attempts failing (transient `URLError` every time) must raise a
    `RuntimeError` naming the attempt count, never fall through silently."""
    reader = _load_run_reader()
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-not-real")
    sleeps: list[float] = []
    monkeypatch.setattr(reader.time, "sleep", lambda seconds: sleeps.append(seconds))

    def fake_urlopen(request, timeout=None):
        raise reader.urllib.error.URLError("connection refused")

    monkeypatch.setattr(reader.urllib.request, "urlopen", fake_urlopen)

    cli = reader.ReaderCli(
        "openrouter", "openai/gpt-5.6-terra", "openai/gpt-5.6-terra", tmp_path, 10
    )
    try:
        cli._call_openrouter("reader", "sys", "prompt")
        raise AssertionError("expected RuntimeError")
    except RuntimeError as error:
        assert "4/4" in str(error), f"error must name the attempt count: {error}"
    assert sleeps == [2, 8, 30], "all three backoff delays are used before giving up"


def test_openrouter_provider_attempt_limit_stops_internal_retries(
    tmp_path, monkeypatch
) -> None:
    reader = _load_run_reader()
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-not-real")
    monkeypatch.setattr(reader.time, "sleep", lambda _seconds: None)
    calls = {"n": 0}

    def fail(_request, timeout=None):
        calls["n"] += 1
        raise reader.urllib.error.URLError("offline")

    monkeypatch.setattr(reader.urllib.request, "urlopen", fail)
    cli = reader.ReaderCli("openrouter", "reader", "judge", tmp_path, 1)
    cli.set_provider_attempt_limit(2)
    try:
        cli._call_openrouter("reader", "sys", "prompt")
        raise AssertionError("expected provider attempt budget exhaustion")
    except reader.CallBudgetExceeded as error:
        assert "provider attempt budget" in str(error)
    assert calls["n"] == cli.provider_attempts == 2


def test_openrouter_spend_control_sets_provider_price_caps_and_settles_cost(
    tmp_path, monkeypatch
) -> None:
    reader = _load_run_reader()
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-not-real")
    payload = {
        "id": "gen-priced",
        "model": "reader",
        "provider": "fixture",
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 2,
            "total_tokens": 12,
            "cost": 0.0012,
        },
        "choices": [
            {"message": {"content": '{"notes":"","answer":"ok","abstain":false}'}}
        ],
    }
    requests = []

    def open_response(request, timeout=None):
        requests.append(request)
        if request.full_url == reader.OPENROUTER_URL:
            return _FakeHttpResponse(payload)
        return _FakeHttpResponse(
            {"data": {"model": "reader", "provider_name": "fixture", "total_cost": 0.0012}}
        )

    monkeypatch.setattr(reader.urllib.request, "urlopen", open_response)
    cli = reader.ReaderCli(
        "openrouter",
        "reader",
        "judge",
        tmp_path,
        1,
        max_spend_usd=reader.Decimal("1"),
        max_price_per_million={
            "prompt": reader.Decimal("2.5"),
            "completion": reader.Decimal("10"),
        },
        max_output_tokens=1024,
    )
    attempts = _load_provider_attempts()
    cli.set_provider_attempt_ledger(
        _open_test_ledger(attempts, tmp_path / "attempts.json")
    )
    assert cli.call("reader", "sys", "prompt").startswith("{")
    sent = json.loads(requests[0].data)
    assert sent["provider"]["max_price"] == {"prompt": 2.5, "completion": 10.0}
    assert sent["max_tokens"] == 1024
    assert cli.response_contract_for("reader")["decoding"]["max_tokens"] == 1024
    assert cli.reported_spend_usd == reader.Decimal("0.0012")
    assert cli.unsettled_liability_usd == 0


def test_openrouter_spend_control_reserves_ambiguous_retry_liability(
    tmp_path, monkeypatch
) -> None:
    reader = _load_run_reader()
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-not-real")
    monkeypatch.setattr(reader.time, "sleep", lambda _seconds: None)
    calls = {"n": 0}

    def fail(_request, timeout=None):
        calls["n"] += 1
        raise reader.urllib.error.URLError("ambiguous transport failure")

    monkeypatch.setattr(reader.urllib.request, "urlopen", fail)
    prices = {
        "prompt": reader.Decimal("1"),
        "completion": reader.Decimal("1"),
    }
    probe = reader.ReaderCli(
        "openrouter", "reader", "judge", tmp_path / "probe", 1,
        max_spend_usd=reader.Decimal("1"), max_price_per_million=prices,
    )
    body = json.dumps(
        {
            "model": "reader",
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "prompt"},
            ],
            **reader.openrouter_decoding("reader"),
            "response_format": reader.response_contract("openrouter", "reader")["response_format"],
            "provider": reader.openrouter_provider_preferences("reader", prices),
        }
    ).encode()
    one_attempt = probe._attempt_liability_usd(body)
    cli = reader.ReaderCli(
        "openrouter", "reader", "judge", tmp_path / "actual", 1,
        max_spend_usd=one_attempt, max_price_per_million=prices,
    )
    try:
        cli._call_openrouter("reader", "sys", "prompt")
        raise AssertionError("expected fail-closed spend exhaustion")
    except reader.CallBudgetExceeded as error:
        assert "spend ceiling" in str(error)
    assert calls["n"] == 1
    assert cli.reported_spend_usd == 0
    assert cli.unsettled_liability_usd == one_attempt


def test_restore_spend_from_attempts_keeps_errors_as_liability() -> None:
    reader = _load_run_reader()
    reported, unsettled = reader.restore_spend_from_attempts(
        [
            {
                "status": "result",
                "start": {"max_liability_nanos": 50_000_000},
                "result": {"response": {"usage": {"cost": 0.012}}},
            },
            {
                "status": "error",
                "start": {"max_liability_nanos": 70_000_000},
                "error": {"error": "timeout"},
            },
        ]
    )
    assert reported == reader.Decimal("0.012")
    assert unsettled == reader.Decimal("0.07")


def test_reader_cache_write_is_atomic_on_replace_failure(tmp_path, monkeypatch) -> None:
    reader = _load_run_reader()

    def reply(cli, _kind, _system, _prompt):
        cli.last_call_metadata = {
            "model": "reader", "provider": "fixture",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost": 0},
        }
        return '{"notes":"","answer":"ok","abstain":false}'

    monkeypatch.setattr(reader.ReaderCli, "_call_codex", reply)
    cli = reader.ReaderCli("codex", "reader", "judge", tmp_path, 1)
    cache_path = cli._cache_path("reader", "sys", "prompt")
    monkeypatch.setattr(reader.os, "replace", lambda _source, _target: (_ for _ in ()).throw(OSError("crash")))
    try:
        cli.call("reader", "sys", "prompt")
        raise AssertionError("expected atomic replace failure")
    except OSError as error:
        assert "crash" in str(error)
    assert not cache_path.exists()
    assert list(tmp_path.iterdir()) == []
