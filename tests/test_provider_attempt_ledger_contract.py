"""Contract tests for `scripts/provider_attempts.py` — the SHARED paid-provider
campaign ledger, attempt journal, and shared meter.

Extracted from `tests/test_temporal_benchmark_contract.py` on 2026-07-31 when
the STALE harness was deleted (one-plan Phase C). These tests were living in
that file for historical reasons only: `provider_attempts.py` is imported by
`run_reader.py`, `run_memora_fama.py`, `run_forgeteval_proposals.py`,
`generate_forgeteval_proposals.py`, `code_lane_reader_packet.py`,
`run_packing_sufficiency_screen.py`, and `validate_memora_reasoning_proof.py`,
all of which are live. Only the `test_stale_*` half of the original file was
deleted.
"""

from __future__ import annotations

import importlib.util
import hashlib
import json
from decimal import Decimal
from pathlib import Path
import subprocess
import sys
import types

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_attempts():
    spec = importlib.util.spec_from_file_location(
        "provider_attempts", ROOT / "scripts" / "provider_attempts.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def open_test_ledger(attempts, path: Path, scope: str = "fixture", screen: str = "test"):
    authorization = (
        scope
        if len(scope) == 64 and all(character in "0123456789abcdef" for character in scope)
        else hashlib.sha256(scope.encode()).hexdigest()
    )
    return attempts.ProviderAttemptLedger(path, authorization, screen, 1_000_000_000_000, 0)


def paid_response(response_id: str = "gen-1", *, cost: object = 0.01) -> dict:
    return {
        "response_id": response_id,
        "requested_model": "openai/gpt-5.6-luna-pro",
        "served_model": "openai/gpt-5.6-luna-pro-20260709",
        "provider": "OpenAI",
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "cost": cost,
        },
        "elapsed_seconds": 0.2,
        "retry_index": 0,
        "parse_status": "provider_response_validated",
        "request_sha256": "1" * 64,
        "result_sha256": "2" * 64,
    }


def test_campaign_ledger_enforces_cumulative_liability_across_screens(
    tmp_path: Path,
) -> None:
    attempts = load_attempts()
    path = tmp_path / "campaign.jsonl"
    auth = "a" * 64
    start = {
        "retry_index": 0,
        "requested_model": "openai/gpt-5.6-luna-pro",
        "request_sha256": "1" * 64,
    }
    inventory = [{
        "reservation_id": "opening",
        "reserved_nanos": 10_000,
        "receipt_sha256": "b" * 64,
        "proof_sha256": "c" * 64,
    }]
    first = attempts.ProviderAttemptLedger(
        path,
        auth,
        "screen-a",
        10_000_200_000,
        10_000,
        opening_reservations=inventory,
    )
    first.record("start", "a", {**start, "max_liability_nanos": 100_000})
    first.record(
        "result", "a", {"response": paid_response(cost="0.00005")}
    )
    first.close()

    second = attempts.ProviderAttemptLedger(
        path,
        auth,
        "screen-b",
        10_000_200_000,
        10_000,
        opening_reservations=inventory,
    )
    with pytest.raises(RuntimeError, match="campaign unallocated reserve"):
        second.record(
            "start", "b", {**start, "max_liability_nanos": 150_001}
        )


def test_campaign_authorization_opens_one_scope_and_journal_for_every_screen(
    tmp_path: Path,
) -> None:
    attempts = load_attempts()
    journal = tmp_path / "campaign.jsonl"
    packet = tmp_path / "authorization.json"
    opening = 5_141_664_250
    scope = {"campaign": {
        "journal_path": journal.name,
        "hard_ceiling_nanos": attempts.CAMPAIGN_HARD_CEILING_NANOS,
        "opening_liability_nanos": opening,
        "unallocated_reserve_nanos": attempts.CAMPAIGN_UNALLOCATED_RESERVE_NANOS,
        "opening_reservations": [{
            "reservation_id": "historical-opening",
            "reserved_nanos": opening,
            "receipt_sha256": "b" * 64,
            "proof_sha256": "c" * 64,
        }],
    }}
    authorization = attempts._sha256_json(scope)
    packet.write_text(json.dumps({
        "status": "AUTHORIZED_STATE_MEMORY_CAMPAIGN",
        "authorization": {"authorization_scope_sha256": authorization},
        **scope,
    }))

    first = attempts.open_campaign_ledger(packet, screen_id="screen-a")
    assert first.path == journal.resolve()
    assert first.authorization_sha256 == authorization
    first.close()
    second = attempts.open_campaign_ledger(
        packet, screen_id="screen-b", expected_journal_path=journal
    )
    assert second.path == first.path
    assert second.authorization_sha256 == first.authorization_sha256
    second.close()

    with pytest.raises(ValueError, match="canonical campaign journal path"):
        attempts.open_campaign_ledger(
            packet,
            screen_id="screen-c",
            expected_journal_path=tmp_path / "alternate.jsonl",
        )


def test_campaign_authorization_accepts_its_scoped_opening_reservations(
    tmp_path: Path,
) -> None:
    attempts = load_attempts()
    opening = 5_434_506_750
    scope = {"campaign": {
        "journal_path": "campaign.jsonl",
        "hard_ceiling_nanos": attempts.CAMPAIGN_HARD_CEILING_NANOS,
        "opening_liability_nanos": opening,
        "unallocated_reserve_nanos": attempts.CAMPAIGN_UNALLOCATED_RESERVE_NANOS,
        "opening_reservations": [{
            "reservation_id": "sealed-prior-campaign",
            "reserved_nanos": opening,
            "receipt_sha256": "b" * 64,
            "proof_sha256": "c" * 64,
        }],
    }}
    packet = tmp_path / "authorization.json"
    packet.write_text(json.dumps({
        "status": "AUTHORIZED_STATE_MEMORY_CAMPAIGN",
        "authorization": {
            "authorization_scope_sha256": attempts._sha256_json(scope),
        },
        **scope,
    }))

    ledger = attempts.open_campaign_ledger(packet, screen_id="screen-a")
    try:
        assert ledger.snapshot()["opening_liability_nanos"] == opening
    finally:
        ledger.close()


def test_campaign_start_preserves_exact_ten_dollar_reserve(tmp_path: Path) -> None:
    attempts = load_attempts()
    ledger = attempts.ProviderAttemptLedger(
        tmp_path / "campaign.jsonl",
        "a" * 64,
        "screen-a",
        attempts.CAMPAIGN_HARD_CEILING_NANOS,
        0,
    )
    start = {
        "retry_index": 0,
        "requested_model": "model",
        "request_sha256": "1" * 64,
    }
    ledger.record(
        "start",
        "allowed",
        {**start, "max_liability_nanos": 190_000_000_000},
    )
    ledger.assert_open()
    with pytest.raises(RuntimeError, match="campaign unallocated reserve"):
        ledger.record(
            "start", "reserve-invader", {**start, "max_liability_nanos": 1}
        )


def test_campaign_ledger_retains_unresolved_reservations_and_rounds_cost_up(
    tmp_path: Path,
) -> None:
    attempts = load_attempts()
    ledger = attempts.ProviderAttemptLedger(
        tmp_path / "campaign.jsonl",
        "a" * 64,
        "screen-a",
        10_000_001_000,
        10,
        opening_reservations=[{
            "reservation_id": "opening",
            "reserved_nanos": 10,
            "receipt_sha256": "b" * 64,
            "proof_sha256": "c" * 64,
        }],
    )
    start = {
        "retry_index": 0,
        "requested_model": "model",
        "request_sha256": "1" * 64,
        "max_liability_nanos": 100,
    }
    ledger.record("start", "interrupted", start)
    ledger.record("start", "error", start)
    ledger.record("error", "error", {"type": "OSError"})
    ledger.record("start", "priced", start)
    ledger.record(
        "result", "priced", {"response": paid_response(cost="0.0000000011")}
    )
    ledger.record("start", "unpriced", start)
    ledger.record(
        "result", "unpriced", {"response": paid_response(cost=None)}
    )

    snapshot = ledger.snapshot()
    assert snapshot["settled_nanos"] == 2
    assert snapshot["unresolved_max_liability_nanos"] == 300
    assert snapshot["total_liability_nanos"] == 312


def test_campaign_ledger_assert_open_allows_exact_ceiling(
    tmp_path: Path,
) -> None:
    attempts = load_attempts()
    ledger = attempts.ProviderAttemptLedger(
        tmp_path / "campaign.jsonl",
        "a" * 64,
        "cache-screen",
        attempts.CAMPAIGN_HARD_CEILING_NANOS,
        attempts.CAMPAIGN_HARD_CEILING_NANOS,
        opening_reservations=[{
            "reservation_id": "opening",
            "reserved_nanos": attempts.CAMPAIGN_HARD_CEILING_NANOS,
            "receipt_sha256": "b" * 64,
            "proof_sha256": "c" * 64,
        }],
    )

    ledger.assert_open()


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
    ledger = open_test_ledger(attempts, tmp_path / "attempts.json", "fingerprint")
    start = {
        "retry_index": 0,
        "requested_model": "openai/gpt-5.6-luna-pro",
        "request_sha256": "1" * 64,
        "max_liability_nanos": 100_000_000,
    }
    ledger.record("start", "request-a", start)
    with pytest.raises(RuntimeError, match="interrupted"):
        attempts.validate_provider_attempt_ledger(ledger.snapshot())

    ledger.record("result", "request-a", {"response": paid_response("duplicate")})
    ledger.record("start", "request-b", start)
    ledger.record("result", "request-b", {"response": paid_response("duplicate")})
    with pytest.raises(RuntimeError, match="duplicate response ID"):
        attempts.validate_provider_attempt_ledger(ledger.snapshot())

    malformed = open_test_ledger(attempts, tmp_path / "malformed.json", "malformed")
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
        open_test_ledger(attempts, tmp_path / "attempts.json", "fingerprint")


def test_attempt_journal_appends_without_rewriting_and_rejects_truncation_or_forks(
    tmp_path: Path,
) -> None:
    attempts = load_attempts()
    path = tmp_path / "attempts.jsonl"
    ledger = open_test_ledger(attempts, path, "fingerprint")
    start = {
        "retry_index": 0,
        "requested_model": "openai/gpt-5.6-luna-pro",
        "request_sha256": "1" * 64,
        "max_liability_nanos": 100_000_000,
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
        open_test_ledger(attempts, truncated, "fingerprint")

    malformed = tmp_path / "malformed.jsonl"
    malformed.write_bytes(complete + b"not-json\n")
    with pytest.raises(ValueError, match="malformed"):
        open_test_ledger(attempts, malformed, "fingerprint")

    forked = tmp_path / "forked.jsonl"
    forked.write_bytes(complete)
    prior = json.loads(complete.splitlines()[-1])
    terminal = {
        "event": "error",
        "screen_id": "test",
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
        open_test_ledger(attempts, forked, "fingerprint")


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
                "ledger=ProviderAttemptLedger(__import__('pathlib').Path(sys.argv[2]), '" + hashlib.sha256(b"fingerprint").hexdigest() + "', 'child', 1000000000000, 0); "
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
            open_test_ledger(attempts, path, "fingerprint", "parent")
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
    ledger = open_test_ledger(attempts, tmp_path / "judge.jsonl", "judge")
    attempts.install_openai_meter(
        sdk, ledger, max_liability_nanos=100_000_000, max_output_tokens=4096
    )
    sdk.OpenAI().chat.completions.create(model="judge", messages=[])

    assert captured[0]["max_tokens"] == 4096


def test_shared_meter_complete_inline_evidence_skips_generation_lookup(
    tmp_path: Path,
) -> None:
    attempts = load_attempts()

    class Completions:
        def create(self, **_kwargs):
            payload = {
                "id": "judge-inline-complete",
                "model": "judge",
                "provider": "OpenAI",
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

    ledger = open_test_ledger(attempts, tmp_path / "judge.jsonl", "judge")
    sdk = types.SimpleNamespace(OpenAI=OpenAI)
    attempts.install_openai_meter(
        sdk,
        ledger,
        max_liability_nanos=100_000_000,
        generation_lookup=lambda _response_id: (_ for _ in ()).throw(
            AssertionError("complete inline evidence must not use /generation")
        ),
    )

    sdk.OpenAI().chat.completions.create(model="judge", messages=[])
    assert ledger.snapshot()["priced_provider_attempts"] == 1


def test_shared_meter_context_cannot_override_reserved_liability(tmp_path: Path) -> None:
    attempts = load_attempts()

    class Completions:
        def create(self, **_kwargs):
            payload = {
                "id": "judge-1",
                "model": "judge",
                "provider": "OpenAI",
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

    ledger = open_test_ledger(attempts, tmp_path / "judge.jsonl", "judge")
    sdk = types.SimpleNamespace(OpenAI=OpenAI)
    attempts.install_openai_meter(
        sdk,
        ledger,
        max_liability_nanos=100_000_000,
        context={"max_liability_nanos": 1},
    )
    sdk.OpenAI().chat.completions.create(model="judge", messages=[])

    assert ledger.attempts[0]["start"]["max_liability_nanos"] == 100_000_000


def test_shared_meter_namespaces_context_away_from_authoritative_evidence(
    tmp_path: Path,
) -> None:
    attempts = load_attempts()

    class Completions:
        def create(self, **_kwargs):
            payload = {
                "id": "judge-authoritative",
                "model": "served-authoritative",
                "provider": "OpenAI",
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

    collisions = {
        "usage": {"cost": "0.000000001"},
        "response": {"usage": {"cost": "0.000000001"}},
        "request_sha256": "f" * 64,
        "result_sha256": "e" * 64,
        "requested_model": "caller-model",
        "served_model": "caller-served",
        "provider": "caller-provider",
    }
    ledger = open_test_ledger(attempts, tmp_path / "judge.jsonl", "judge")
    sdk = types.SimpleNamespace(OpenAI=OpenAI)
    attempts.install_openai_meter(
        sdk, ledger, max_liability_nanos=100_000_000, context=collisions
    )
    sdk.OpenAI().chat.completions.create(model="requested-authoritative", messages=[])

    row = ledger.snapshot()["attempts"][0]
    assert row["start"]["context"] == collisions
    assert row["result"]["context"] == collisions
    assert row["result"]["response"]["usage"]["cost"] == 0.01
    assert row["result"]["response"]["requested_model"] == "requested-authoritative"
    assert row["result"]["response"]["served_model"] == "served-authoritative"
    assert row["result"]["response"]["provider"] == "OpenAI"
    assert ledger.snapshot()["settled_nanos"] == 10_000_000


# --- transient-retry tolerance (2026-08-05) -------------------------------
# A real HorizonBench paid pilot completed all logical rows and priced every
# one, but a transient URLError attempt (retried to a priced result on the same
# request_key) left an "error" row in the append-only journal. The completeness
# gate rejected any non-"result" attempt, so the campaign could not close AFTER
# the money was spent. These pin the fix: a transient error SUPERSEDED by a
# successful retry of the SAME request_key is tolerated and releases its
# reservation; an UN-superseded error stays incomplete and unresolved.

def _retry_response(response_id: str, retry_index: int, *, cost: object) -> dict:
    return {
        "response_id": response_id,
        "requested_model": "anthropic/claude-opus-4-6",
        "served_model": "claude-opus-4-6",
        "provider": "Anthropic",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "cost": cost},
        "elapsed_seconds": 0.2,
        "retry_index": retry_index,
        "parse_status": "provider_response_validated",
        "request_sha256": "1" * 64,
        "result_sha256": "2" * 64,
    }


def _start(retry_index: int, *, max_liability_nanos: int = 100_000) -> dict:
    return {
        "retry_index": retry_index,
        "requested_model": "anthropic/claude-opus-4-6",
        "request_sha256": "1" * 64,
        "max_liability_nanos": max_liability_nanos,
    }


def test_transient_error_superseded_by_retry_closes_clean(tmp_path: Path) -> None:
    attempts = load_attempts()
    ledger = open_test_ledger(attempts, tmp_path / "retry.jsonl", "retry")
    # request "a": first attempt errors (URLError), retry succeeds and is priced.
    ledger.record("start", "a", _start(0))
    ledger.record("error", "a", {"error": "URLError", "retry_index": 0, "elapsed_seconds": 0.1})
    ledger.record("start", "a", _start(1))
    ledger.record("result", "a", {"response": _retry_response("gen-a", 1, cost="0.5")})
    # request "b": clean single-attempt success.
    ledger.record("start", "b", _start(0))
    ledger.record("result", "b", {"response": _retry_response("gen-b", 0, cost="0.25")})

    snapshot = ledger.snapshot()
    assert attempts.provider_attempt_ledger_is_complete(snapshot), snapshot
    # The superseded error released its reservation: only the two real charges
    # settle, and there is no lingering unresolved liability.
    assert snapshot["unresolved_max_liability_nanos"] == 0
    assert snapshot["unpriced_provider_attempts"] == 1  # the one superseded error
    assert snapshot["priced_provider_attempts"] == 2
    assert abs(snapshot["reported_cost_usd"] - 0.75) < 1e-9
    # validate_provider_attempt_ledger must not choke on the error row.
    attempts.validate_provider_attempt_ledger(snapshot)
    # And the campaign closes.
    closure = ledger.close_campaign(tmp_path / "closure.json")
    assert closure["unresolved_max_liability_nanos"] == 0


def test_unsuperseded_error_stays_incomplete_and_unresolved(tmp_path: Path) -> None:
    attempts = load_attempts()
    ledger = open_test_ledger(attempts, tmp_path / "dangling.jsonl", "dangling")
    ledger.record("start", "a", _start(0))
    ledger.record("result", "a", {"response": _retry_response("gen-a", 0, cost="0.25")})
    # request "b" errors and is NEVER retried to success — a real failure.
    ledger.record("start", "b", _start(0))
    ledger.record("error", "b", {"error": "URLError", "retry_index": 0, "elapsed_seconds": 0.1})

    snapshot = ledger.snapshot()
    assert not attempts.provider_attempt_ledger_is_complete(snapshot)
    # Fail-closed preserved: the un-superseded error's reservation is unresolved.
    assert snapshot["unresolved_max_liability_nanos"] == 100_000


def test_dangling_started_without_terminal_stays_incomplete(tmp_path: Path) -> None:
    attempts = load_attempts()
    ledger = open_test_ledger(attempts, tmp_path / "started.jsonl", "started")
    ledger.record("start", "a", _start(0))
    ledger.record("result", "a", {"response": _retry_response("gen-a", 0, cost="0.25")})
    # request "b" started but the process died before any terminal event.
    ledger.record("start", "b", _start(0))

    snapshot = ledger.snapshot()
    assert not attempts.provider_attempt_ledger_is_complete(snapshot)
    assert snapshot["unresolved_max_liability_nanos"] == 100_000
