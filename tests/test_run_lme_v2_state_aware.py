from __future__ import annotations

import json
import importlib.util
import os
from fractions import Fraction
import hashlib
from pathlib import Path
import struct
import subprocess
import sys
import zlib

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/run_lme_v2_state_aware.py"
STATE_AWARE_MANIFEST = (
    ROOT / "benchmarks/manifests/longmemeval_v2.state_aware_full.v1.json"
)


def _load_runner():
    spec = importlib.util.spec_from_file_location("run_lme_v2_state_aware", RUNNER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_census_refuses_authorization_without_exact_bounds(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    output = tmp_path / "CAMPAIGN-CENSUS.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "benchmark": {"questions": 451, "memory_context_max_tokens": 200000},
                "construction": {},
                "reader_judge": {},
                "deep_recall": {},
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "census",
            "--manifest",
            str(manifest),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    census = json.loads(output.read_text(encoding="utf-8"))
    assert census["benchmark"]["memory_context_max_tokens"] == 200000
    assert census["admission"]["formula"] == (
        "4258002400+C+2*R_sum+451*S+10000000000<=200000000000"
    )
    assert census["admission"]["authorized"] is False


def test_qwen_tokenizer_identity_and_chat_template_overhead_fixture_are_pinned() -> None:
    construction = json.loads(STATE_AWARE_MANIFEST.read_text(encoding="utf-8"))[
        "construction"
    ]
    assert construction["tokenizer_revision"] == (
        "c202236235762e1c871ad0ccb60c8ee5ba337b9a"
    )
    assert construction["tokenizer_sha256"] == (
        "5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42"
    )
    assert construction["chat_template_sha256"] == (
        "a4aee8afcf2e0711942cf848899be66016f8d14a889ff9ede07bca099c28f715"
    )
    assert construction["chat_template_fixture_toolchain"] == {
        "tokenizers": "0.22.2",
        "transformers": "5.14.1",
    }
    assert len(construction["empty_system_user_generation_token_ids"]) == (
        construction["chat_template_overhead_tokens"]
    )


def test_admitted_profile_pins_qwen_deepinfra_native_2048_and_aggregate_cap() -> None:
    manifest = json.loads(STATE_AWARE_MANIFEST.read_text(encoding="utf-8"))
    assert manifest["reader_judge"]["judge"]["maximum_output_tokens"] == 2048
    assert manifest["deep_recall"]["model"] == "qwen/qwen3.5-9b-20260310"
    assert manifest["deep_recall"]["response_model"] == "qwen/qwen3.5-9b"
    assert manifest["deep_recall"]["provider"] == "deepinfra"
    assert manifest["deep_recall"]["allow_fallbacks"] is False
    policy = manifest["construction"]["wave_policy"]
    assert policy["maximum_internal_attempts"] == 1
    assert policy["retry_pool_nanos"] == 10_000_000_000
    assert policy["rust_subledger_aggregate_cap_env"] == (
        "MEMPHANT_STRUCTURED_STATE_AGGREGATE_RESERVATION_NANOS"
    )


def test_reader_liability_inventory_stratifies_text_and_multimodal_billing() -> None:
    runner = _load_runner()
    reader = {
        "model": "reader",
        "provider": "fixed-provider",
        "pricing_source": "https://example.invalid/reader",
        "pricing_sha256": "a" * 64,
        "memory_context_max_tokens": 200_000,
        "multimodal_provider_prompt_ceiling_tokens": 262_144,
        "provider_prompt_ceiling_source": "https://example.invalid/reader",
        "provider_prompt_ceiling_source_sha256": "c" * 64,
        "maximum_output_tokens": 20_000,
        "maximum_attempts": 1,
        "input_price_nanos_per_million": 100_000_000,
        "output_price_nanos_per_million": 150_000_000,
    }
    judge = {
        "model": "judge",
        "provider": "fixed-provider",
        "pricing_source": "https://example.invalid/judge",
        "pricing_sha256": "b" * 64,
        "maximum_fixed_serialized_bytes": 2_345,
        "reader_response_insertions": 2,
        "reader_maximum_output_tokens": 20_000,
        "maximum_input_reservation_units": 42_345,
        "maximum_output_tokens": 4_096,
        "maximum_attempts": 1,
        "input_price_nanos_per_million": 1_750_000_000,
        "output_price_nanos_per_million": 14_000_000_000,
    }
    processor_rows = [
        {"question_id": "image", "has_image": True, "local_processor_input_tokens": 1_188},
        {"question_id": "text", "has_image": False, "local_processor_input_tokens": 527},
    ]

    inventory = runner._reader_liability_inventory(processor_rows, reader, judge)
    by_id = {row["question_id"]: row for row in inventory["rows"]}
    assert by_id["image"]["input_reservation_units"] == 262_144
    assert by_id["text"]["input_reservation_units"] == 200_527
    assert by_id["image"]["per_arm_liability_nanos"] == 160_662_150
    assert by_id["text"]["per_arm_liability_nanos"] == 154_500_450
    assert inventory["reader_arm_liability_nanos"] == 315_162_600
    assert inventory["image_rows"] == 1
    assert inventory["text_rows"] == 1
    changed_local_diagnostic = [dict(row) for row in processor_rows]
    changed_local_diagnostic[0]["local_processor_input_tokens"] = 99_999
    changed = runner._reader_liability_inventory(
        changed_local_diagnostic, reader, judge
    )
    assert changed["rows"][0]["input_reservation_units"] == 262_144

    reader["multimodal_provider_prompt_ceiling_tokens"] = 262_143
    with pytest.raises(RuntimeError, match="provider prompt ceiling drift"):
        runner._reader_liability_inventory(processor_rows, reader, judge)


def test_reader_processor_proof_binds_images_processor_and_oracle_free_fixture() -> None:
    runner = _load_runner()
    proof = {
        "reader_shape_fixture_sha256": "a" * 64,
        "reader_shape_rows": 451,
        "reader_shape_image_inventory_sha256": "d" * 64,
        "reader_shape_image_manifest_sha256": "e" * 64,
        "reader_tokenizer_sha256": "b" * 64,
        "reader_chat_template_sha256": "c" * 64,
        "reader_preprocessor_config_sha256": "f" * 64,
        "reader_processor_source_sha256": "1" * 64,
        "reader_image_processor_source_sha256": "2" * 64,
        "reader_processor_toolchain_sha256": "3" * 64,
        "reader_local_processor_maximum_input_tokens": 1_188,
        "reader_row_token_inventory_sha256": "",
        "rows": [
            {
                "question_id": f"q-{index:03}",
                "has_image": index < 29,
                "local_processor_input_tokens": 1_188 if index < 29 else 527,
            }
            for index in range(451)
        ],
    }
    proof["reader_row_token_inventory_sha256"] = runner.sha256_json(proof["rows"])
    expected = {
        "fixture_sha256": "a" * 64,
        "rows": 451,
        "image_inventory_sha256": "d" * 64,
        "image_manifest_sha256": "e" * 64,
        "tokenizer_sha256": "b" * 64,
        "chat_template_sha256": "c" * 64,
        "preprocessor_config_sha256": "f" * 64,
        "processor_source_sha256": "1" * 64,
        "image_processor_source_sha256": "2" * 64,
        "processor_toolchain_sha256": "3" * 64,
    }

    assert runner._validated_reader_processor_proof(proof, expected) == proof["rows"]
    for field in (
        "reader_shape_fixture_sha256",
        "reader_shape_image_inventory_sha256",
        "reader_shape_image_manifest_sha256",
        "reader_tokenizer_sha256",
        "reader_chat_template_sha256",
        "reader_preprocessor_config_sha256",
        "reader_processor_source_sha256",
        "reader_image_processor_source_sha256",
        "reader_processor_toolchain_sha256",
    ):
        tampered = dict(proof)
        tampered[field] = "9" * 64
        with pytest.raises(RuntimeError, match="reader processor proof drift"):
            runner._validated_reader_processor_proof(tampered, expected)


def _png(width: int, height: int) -> bytes:
    signature = b"\x89PNG\r\n\x1a\n"

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFF_FFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    scanlines = b"".join(b"\x00" + b"\x00\x00\x00" * width for _ in range(height))
    return signature + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(scanlines)) + chunk(b"IEND", b"")


def _write_reader_source(root: Path, *, image_dimensions: tuple[int, int] = (2, 1)) -> None:
    upstream = root / "upstream/evaluation"
    upstream.mkdir(parents=True, exist_ok=True)
    (upstream / "harness.py").write_text(
        "DOMAIN_SYSTEM_PROMPTS = {'web': 'system prompt'}\n", encoding="utf-8"
    )
    screenshot_root = root / "question_screenshots"
    screenshot_root.mkdir(exist_ok=True)
    rows = []
    checksums = []
    for index in range(451):
        relative = None
        if index < 29:
            relative = f"question_screenshots/question-{index:03}.png"
            payload = _png(*image_dimensions)
            (root / relative).write_bytes(payload)
            checksums.append(f"{hashlib.sha256(payload).hexdigest()}  {relative}\n")
        rows.append(
            {
                "id": f"question-{index:03}",
                "domain": "web",
                "question": f"Question {index}?",
                "image": relative,
                "answer": f"SECRET-{index}",
                "eval_function": "oracle",
            }
        )
    (root / "questions.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    (root / "checksums.sha256").write_text("".join(checksums), encoding="utf-8")


def test_reader_shape_fixture_excludes_oracles_and_hashes_every_question(
    tmp_path: Path,
) -> None:
    runner = _load_runner()
    _write_reader_source(tmp_path)
    questions = tmp_path / "questions.jsonl"
    fixture = tmp_path / "reader-shapes.jsonl"

    first = runner._materialize_reader_shapes(tmp_path, fixture)
    fixture_text = fixture.read_text(encoding="utf-8")
    assert first["rows"] == 451
    assert first["image_rows"] == 29
    assert first["image_manifest_sha256"] == runner._sha256_file(
        tmp_path / "checksums.sha256"
    )
    first_row = json.loads(fixture_text.splitlines()[0])
    assert first_row["question_image"] == {
        "bytes": len(_png(2, 1)),
        "height": 1,
        "mime_type": "image/png",
        "path": "question_screenshots/question-000.png",
        "sha256": hashlib.sha256(_png(2, 1)).hexdigest(),
        "width": 2,
    }
    assert "SECRET" not in fixture_text
    assert "eval_function" not in fixture_text
    rows = [json.loads(line) for line in questions.read_text(encoding="utf-8").splitlines()]
    rows[0]["question"] = "A changed question?"
    questions.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    second = runner._materialize_reader_shapes(tmp_path, fixture)
    assert second["fixture_sha256"] != first["fixture_sha256"]


def test_reader_shape_fixture_rejects_missing_or_tampered_screenshot(
    tmp_path: Path,
) -> None:
    runner = _load_runner()
    _write_reader_source(tmp_path)
    fixture = tmp_path / "reader-shapes.jsonl"
    image = tmp_path / "question_screenshots/question-000.png"
    image.unlink()
    with pytest.raises(RuntimeError, match="question screenshot is missing"):
        runner._materialize_reader_shapes(tmp_path, fixture)
    image.write_bytes(_png(3, 1))
    with pytest.raises(RuntimeError, match="question screenshot checksum drift"):
        runner._materialize_reader_shapes(tmp_path, fixture)


def test_reader_shape_dimension_change_invalidates_bound_inventory(tmp_path: Path) -> None:
    runner = _load_runner()
    _write_reader_source(tmp_path, image_dimensions=(2, 1))
    fixture = tmp_path / "reader-shapes.jsonl"
    first = runner._materialize_reader_shapes(tmp_path, fixture)
    _write_reader_source(tmp_path, image_dimensions=(3, 1))
    second = runner._materialize_reader_shapes(tmp_path, fixture)
    assert second["image_inventory_sha256"] != first["image_inventory_sha256"]
    assert json.loads(fixture.read_text(encoding="utf-8").splitlines()[0])[
        "question_image"
    ]["width"] == 3


def test_deep_liability_is_derived_from_full_runtime_limits_and_hard_stop() -> None:
    runner = _load_runner()
    config = {
        "model": "deep",
        "response_model": "deep",
        "provider": "azure",
        "pricing_source": "https://example.invalid/deep",
        "pricing_sha256": "c" * 64,
        "runtime_code_sha256": "d" * 64,
        "input_price_nanos_per_million": 1_000_000_000,
        "output_price_nanos_per_million": 6_000_000_000,
        "maximum_context_tokens": 96_000,
        "maximum_output_tokens_per_turn": 4_096,
        "maximum_tool_iterations": 24,
        "maximum_retries_per_turn": 2,
        "maximum_attempts_per_turn": 3,
        "maximum_spend_micros": 300_000,
        "maximum_liability_nanos": 300_000_000,
    }

    proof = runner._deep_liability(config)
    assert proof["maximum_dispatches"] == 23
    assert proof["token_mix_liability_nanos"] == 567_040_000
    assert proof["maximum_liability_nanos"] == 300_000_000
    config["maximum_liability_nanos"] -= 1
    with pytest.raises(RuntimeError, match="Deep liability drift"):
        runner._deep_liability(config)


def _synthetic_parent_census(runner):
    core = {
        "schema_version": 1,
        "benchmark": {
            "name": "LongMemEval-V2",
            "tier": "medium",
            "questions": 451,
            "memory_context_max_tokens": 200000,
            "code_commit": "a" * 40,
            "dataset_revision": "b" * 40,
        },
        "enumeration": {
            "input_jsonl_sha256": "c" * 64,
            "question_pairs": 451,
        },
        "construction": {
            "construction_liability_nanos": 300,
            "maximum_attempts": 3,
            "planned_requests": 2,
            "processed_plans": 2,
            "input_manifest_sha256": "c" * 64,
            "tokenizer_bound": True,
        },
        "terms": {"C": 300, "R": 10, "S": 20},
        "liability_derivation": {},
        "admission": {"authorized": False},
        "manifest_path": "parent.json",
        "manifest_sha256": "d" * 64,
        "paid_models_run": False,
        "spend_nanos": 0,
    }
    return {**core, "census_sha256": runner.sha256_json(core)}


def _synthetic_reader_inventory(runner, per_arm_nanos: int = 10):
    rows = [
        {
            "question_id": f"q-{index:03}",
            "has_image": index < 29,
            "local_processor_input_tokens": 1,
            "billing_authority": (
                "provider_prompt_ceiling"
                if index < 29
                else "pinned_local_text_processor"
            ),
            "input_reservation_units": 262_144 if index < 29 else 200_001,
            "reader_liability_nanos": per_arm_nanos,
            "judge_liability_nanos": 0,
            "per_arm_liability_nanos": per_arm_nanos,
        }
        for index in range(451)
    ]
    return {
        "schema_version": 1,
        "rows": rows,
        "row_count": 451,
        "text_rows": 422,
        "image_rows": 29,
        "reader_arm_liability_nanos": 451 * per_arm_nanos,
        "inventory_sha256": runner.sha256_json(rows),
    }


def test_recost_decomposes_first_attempts_and_preserves_retry_and_reserve_pools() -> None:
    runner = _load_runner()
    parent = _synthetic_parent_census(runner)
    result = runner.recost_census_values(
        parent,
        reader_inventory=_synthetic_reader_inventory(runner, 125_945_400),
        s_term=14_310_400,
        retry_pool_nanos=10_000_000_000,
        manifest_path="current.json",
        manifest_sha256="e" * 64,
        runtime_hashes={"deep": "f" * 64, "structured": "1" * 64},
    )

    assert result["construction"]["first_attempt_liability_nanos"] == 100
    assert result["construction"]["retry_pool_nanos"] == 10_000_000_000
    assert result["terms"]["C"] == 10_000_000_100
    assert result["admission"]["contingency_nanos"] == 10_000_000_000
    assert result["derivation"]["parent_census_sha256"] == parent["census_sha256"]
    assert result["paid_models_run"] is False
    assert result["spend_nanos"] == 0

    parent["construction"]["construction_liability_nanos"] += 1
    with pytest.raises(RuntimeError, match="parent census sha256 mismatch"):
        runner.recost_census_values(
            parent,
            reader_inventory=_synthetic_reader_inventory(runner, 1),
            s_term=1,
            retry_pool_nanos=1,
            manifest_path="current.json",
            manifest_sha256="e" * 64,
            runtime_hashes={"deep": "f" * 64},
        )


class _WaveLedger:
    def __init__(self):
        self.events = []

    def record(self, event, request_key, payload):
        self.events.append((event, request_key, payload))

    def snapshot(self):
        return {"attempts": []}


def test_forged_self_hashed_over_cap_census_fails_before_reservation_or_launch(
    tmp_path: Path,
) -> None:
    runner = _load_runner()
    source_census = ROOT / (
        "docs/build-log/artifacts/state-memory-sota/longmemeval-v2-pilot/"
        "CAMPAIGN-CENSUS.json"
    )
    census = json.loads(source_census.read_text(encoding="utf-8"))
    census["terms"]["C"] = runner.HARD_CEILING_NANOS
    census["construction"]["construction_liability_nanos"] = (
        runner.HARD_CEILING_NANOS
    )
    census["construction"]["retry_pool_nanos"] = (
        runner.HARD_CEILING_NANOS
        - census["construction"]["first_attempt_liability_nanos"]
    )
    census["admission"]["authorized"] = True
    census["admission"]["total_nanos"] = 1
    manifest = json.loads(STATE_AWARE_MANIFEST.read_text(encoding="utf-8"))
    census["manifest_sha256"] = runner._sha256_file(STATE_AWARE_MANIFEST)
    census["construction"]["construction_identity_sha256"] = runner.sha256_json(
        manifest["construction"]
    )
    census["census_sha256"] = runner.sha256_json(
        {key: value for key, value in census.items() if key != "census_sha256"}
    )
    census_path = tmp_path / "forged-census.json"
    census_path.write_text(json.dumps(census), encoding="utf-8")
    ledger = _WaveLedger()
    launched = []

    with pytest.raises(RuntimeError, match="admission equation drift"):
        runner.authorize_construction_wave(
            ledger,
            census_path,
            STATE_AWARE_MANIFEST,
            [],
            wave_kind="first_attempt",
            launch=lambda: launched.append(True),
        )

    assert ledger.events == []
    assert launched == []


def test_stale_census_binary_provenance_is_rejected(tmp_path: Path) -> None:
    runner = _load_runner()
    expected = {
        "binary_sha256": "a" * 64,
        "cargo_lock_sha256": "b" * 64,
        "source_set_sha256": "c" * 64,
        "rustc_vv_sha256": "d" * 64,
        "cargo_version_sha256": "e" * 64,
        "build_profile": "release",
        "cargo_locked": True,
        "package": "memphant-cli",
    }
    stale = {**expected, "binary_sha256": "f" * 64}

    with pytest.raises(RuntimeError, match="census binary provenance drift"):
        runner._validate_census_binary_provenance(expected, stale)

    fresh_binary = tmp_path / "fresh-cli"
    stale_binary = tmp_path / "stale-cli"
    fresh_binary.write_bytes(b"fresh locked build")
    stale_binary.write_bytes(b"older planner")
    fresh_sha256 = runner._sha256_file(fresh_binary)
    assert runner._verify_selected_census_binary(fresh_sha256, fresh_binary) is None
    with pytest.raises(RuntimeError, match="differs from the fresh locked build"):
        runner._verify_selected_census_binary(fresh_sha256, stale_binary)


def test_construction_wave_reserves_before_launch_and_requires_exact_subledger_coverage() -> None:
    runner = _load_runner()
    census = runner.recost_census_values(
        _synthetic_parent_census(runner),
        reader_inventory=_synthetic_reader_inventory(runner, 1),
        s_term=1,
        retry_pool_nanos=100,
        manifest_path="current.json",
        manifest_sha256="e" * 64,
        runtime_hashes={"structured": "f" * 64},
    )
    plans = [
        {"extraction_key": "1" * 64, "request_sha256": "2" * 64, "per_attempt_reservation_nanos": 40, "requested_model": "qwen/qwen3.5-9b-20260310", "maximum_attempts": 3, "source_kind": "resource", "source_body_sha256": "7" * 64, "batch_index": 0, "evidence_slices_sha256": runner.sha256_json([{"id": "slice-1", "body": "Oslo is home.", "source_span": "0:13"}])},
        {"extraction_key": "3" * 64, "request_sha256": "4" * 64, "per_attempt_reservation_nanos": 60, "requested_model": "qwen/qwen3.5-9b-20260310", "maximum_attempts": 3, "source_kind": "resource", "source_body_sha256": "8" * 64, "batch_index": 0, "evidence_slices_sha256": runner.sha256_json([{"id": "slice-2", "body": "Paris is home.", "source_span": "0:14"}])},
    ]
    ledger = _WaveLedger()
    launched = []
    wave = runner._authorize_validated_construction_wave(
        ledger, census, plans, wave_kind="first_attempt", launch=lambda: launched.append(True)
    )
    assert ledger.events[0][0] == "start"
    assert ledger.events[0][2]["max_liability_nanos"] == 200
    assert launched == [True]
    retry_one = runner.plan_construction_retry_wave(wave, plans[:1], [])
    retry_two = runner.plan_construction_retry_wave(wave, plans[:1], [retry_one])
    assert retry_one["campaign_attempt"] == 2
    assert retry_two["campaign_attempt"] == 3
    assert len(ledger.events) == 1, "retry subsets must not create a second campaign reservation"
    with pytest.raises(RuntimeError, match="at most two retry waves"):
        runner.plan_construction_retry_wave(wave, plans[:1], [retry_one, retry_two])

    events = []
    for index, plan in enumerate(plans):
        attempt_id = f"attempt-{index}"
        events.extend(
            [
                {
                    "event": "started",
                    "attempt_id": attempt_id,
                    "attempt": 1,
                    "campaign_attempt": 1,
                    **plan,
                },
                {
                    "event": "result",
                    "attempt_id": attempt_id,
                    "attempt": 1,
                    "campaign_attempt": 1,
                    **plan,
                    "reservation_status": "settled",
                    "response_id": f"generation-{index}",
                    "served_model": "qwen/qwen3.5-9b",
                    "served_provider": "DeepInfra",
                    "parse_status": "decoded",
                    "error": None,
                    "result_sha256": "5" * 64,
                    "observation_sha256": "6" * 64,
                    "observation_count": 1,
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12, "cost": "0.000000001"},
                },
            ]
        )
    proof = runner.validate_and_settle_construction_wave(ledger, wave, events)
    assert proof["settled_nanos"] == 2
    assert ledger.events[-1][0] == "result"

    with pytest.raises(RuntimeError, match="exact planned-key coverage"):
        runner.validate_and_settle_construction_wave(_WaveLedger(), wave, events[:-2])

    slices = [{"id": "slice-2", "body": "Paris is home.", "source_span": "0:14"}]
    observations = [{"namespace": "profile", "item_key": "city", "fields": {"value": "Paris"}, "disposition": "state", "evidence_slice_id": "slice-2", "evidence_quote": "Paris", "valid_from": None, "valid_to": None}]
    hit_core = {
        "schema_version": 1,
        "authorization_sha256": "9" * 64,
        "campaign_sha256": wave["campaign_census_sha256"],
        "cache_namespace": "longmemeval-v2-construction-v1",
        "cache_entry_sha256": "a" * 64,
        "extraction_key": plans[1]["extraction_key"],
        "request_sha256": plans[1]["request_sha256"],
        "source_kind": "resource",
        "source_body_sha256": plans[1]["source_body_sha256"],
        "batch_index": 0,
        "evidence_slices_sha256": runner.sha256_json(slices),
        "evidence_slices": slices,
        "requested_model": wave["requested_model"],
        "served_model": wave["response_model"],
        "served_provider": "DeepInfra",
        "response_id": "cached-generation",
        "source_attempt_id": "cached-attempt",
        "source_started_event_sha256": "b" * 64,
        "source_result_event_sha256": "c" * 64,
        "provider_result_sha256": "d" * 64,
        "observation_count": 1,
        "observation_sha256": runner.sha256_json(observations),
        "observations": observations,
        "reservation_status": "cache_hit",
        "settled_nanos": 0,
    }
    hit = {"core": hit_core, "cache_hit_sha256": runner.sha256_json(hit_core)}
    cache_proof = runner.validate_and_settle_construction_wave(
        _WaveLedger(),
        wave,
        events[:2],
        cache_hit_receipts=[hit],
        authorization_sha256="9" * 64,
    )
    assert cache_proof["paid_key_count"] == 1
    assert cache_proof["cache_hit_key_count"] == 1
    with pytest.raises(RuntimeError, match="authorization identity"):
        runner.validate_and_settle_construction_wave(
            _WaveLedger(), wave, events[:2], cache_hit_receipts=[hit]
        )

    retry_events = [dict(event) for event in events]
    retry_events[1] = {
        "event": "result",
        "attempt_id": "attempt-0",
        "attempt": 1,
        "campaign_attempt": 1,
        **plans[0],
        "reservation_status": "not_charged",
        "http_status": 503,
        "parse_status": "http_error",
        "error": "http_error",
        "result_sha256": "7" * 64,
        "response_id": None,
        "usage": None,
        "served_model": None,
        "served_provider": None,
    }
    retry_events.extend(
        [
            {
                "event": "started",
                "attempt_id": "attempt-0-retry",
                "attempt": 1,
                "campaign_attempt": 2,
                **plans[0],
            },
            {
                **events[1],
                "attempt_id": "attempt-0-retry",
                "campaign_attempt": 2,
                "response_id": "generation-0-retry",
            },
        ]
    )
    retry_proof = runner.validate_and_settle_construction_wave(
        _WaveLedger(), wave, retry_events, [retry_one]
    )
    assert retry_proof["settled_nanos"] == 2
    retry_events[1]["reservation_status"] = "unresolved"
    with pytest.raises(RuntimeError, match="unresolved construction attempt"):
        runner.validate_and_settle_construction_wave(
            _WaveLedger(), wave, retry_events, [retry_one]
        )


def test_authorization_packet_is_canonical_inventory_bound_and_create_only(
    tmp_path: Path,
) -> None:
    runner = _load_runner()
    census = runner.recost_census_values(
        _synthetic_parent_census(runner),
        reader_inventory=_synthetic_reader_inventory(runner, 1),
        s_term=1,
        retry_pool_nanos=100,
        manifest_path="manifest.json",
        manifest_sha256="e" * 64,
        runtime_hashes={"structured": "f" * 64},
    )
    plan = {
        "extraction_key": "1" * 64,
        "request_sha256": "2" * 64,
        "per_attempt_reservation_nanos": 100,
        "requested_model": "qwen/qwen3.5-9b-20260310",
        "maximum_attempts": 3,
        "source_kind": "resource",
        "source_body_sha256": "3" * 64,
        "batch_index": 0,
        "evidence_slices_sha256": "4" * 64,
    }
    census["construction"].update(
        {
            "plan_inventory": [plan],
            "plan_inventory_sha256": runner.sha256_json([plan]),
            "processed_plans": 1,
        }
    )
    census["census_sha256"] = runner.sha256_json(
        {key: value for key, value in census.items() if key != "census_sha256"}
    )
    census_path = tmp_path / "CAMPAIGN-CENSUS.json"
    manifest_path = tmp_path / "manifest.json"
    census_path.write_text(json.dumps(census), encoding="utf-8")
    manifest_path.write_text("{}", encoding="utf-8")
    qwen = {
        "requested_model": "qwen/qwen3.5-9b-20260310",
        "response_model": "qwen/qwen3.5-9b",
        "provider": "DeepInfra",
    }
    normalized = {
        "qwen_deepinfra": qwen,
        "openai_native_judge": {
            "requested_model": "gpt-5.2-2025-12-11",
            "reasoning_effort": "medium",
        },
    }
    refresh = {
        "normalized": normalized,
        "normalized_sha256": runner.sha256_json(normalized),
        "sources": {"public": "5" * 64},
    }
    opening = [
        {
            "reservation_id": "historical",
            "reserved_nanos": runner.OPENING_NANOS,
            "receipt_sha256": "6" * 64,
            "proof_sha256": "7" * 64,
        }
    ]
    packet = runner._build_campaign_authorization(
        census, census_path, manifest_path, refresh, tmp_path, opening
    )
    scope = {
        key: value
        for key, value in packet.items()
        if key not in {"schema_version", "status", "authorization"}
    }
    assert packet["authorization"]["authorization_scope_sha256"] == runner.sha256_json(scope)
    assert packet["inputs"]["plan_inventory_sha256"] == runner.sha256_json([plan])
    assert packet["execution"]["construction_max_workers"] == 32
    assert packet["execution"]["construction_hidden_retries"] == 0
    output = tmp_path / "CAMPAIGN-AUTHORIZATION.json"
    runner._create_json(output, packet)
    with pytest.raises(RuntimeError, match="already exists"):
        runner._create_json(output, packet)


def test_construction_aggregate_reservation_resumes_once_across_crash_boundaries(
    tmp_path: Path,
) -> None:
    runner = _load_runner()
    census = runner.recost_census_values(
        _synthetic_parent_census(runner),
        reader_inventory=_synthetic_reader_inventory(runner, 1),
        s_term=1,
        retry_pool_nanos=100,
        manifest_path="current.json",
        manifest_sha256="e" * 64,
        runtime_hashes={"structured": "f" * 64},
    )
    plans = [
        {"extraction_key": "1" * 64, "request_sha256": "2" * 64, "per_attempt_reservation_nanos": 40, "requested_model": "qwen/qwen3.5-9b-20260310", "maximum_attempts": 3},
        {"extraction_key": "3" * 64, "request_sha256": "4" * 64, "per_attempt_reservation_nanos": 60, "requested_model": "qwen/qwen3.5-9b-20260310", "maximum_attempts": 3},
    ]

    class Ledger:
        def __init__(self):
            self.attempts = []
            self.fail_record_once = False

        def record(self, event, request_key, payload):
            assert event == "start"
            if self.fail_record_once:
                self.fail_record_once = False
                raise RuntimeError("crash before reservation append")
            self.attempts.append({"request_key": request_key, "start": payload})

        def snapshot(self):
            return {"attempts": list(self.attempts)}

    ledger = Ledger()
    wave_path = tmp_path / "CONSTRUCTION-WAVE.json"
    ledger.fail_record_once = True
    with pytest.raises(RuntimeError, match="before reservation"):
        runner.authorize_or_resume_construction_wave(
            ledger, census, plans, wave_path, launch=lambda: None
        )
    assert wave_path.is_file()
    assert ledger.attempts == []

    with pytest.raises(RuntimeError, match="crash inside launch"):
        runner.authorize_or_resume_construction_wave(
            ledger,
            census,
            plans,
            wave_path,
            launch=lambda: (_ for _ in ()).throw(RuntimeError("crash inside launch")),
        )
    assert len(ledger.attempts) == 1
    runner.authorize_or_resume_construction_wave(
        ledger, census, plans, wave_path, launch=lambda: None
    )
    assert len(ledger.attempts) == 1


def test_provider_refresh_uses_only_public_exact_route_authority() -> None:
    runner = _load_runner()
    qwen = {
        "data": {
            "id": "qwen/qwen3.5-9b",
            "endpoints": [
                {
                    "provider_name": "DeepInfra",
                    "pricing": {"prompt": "0.0000001", "completion": "0.00000015"},
                    "context_length": 262144,
                    "max_completion_tokens": 81920,
                    "supported_parameters": ["seed", "response_format", "structured_outputs", "max_tokens"],
                    "status": 0,
                }
            ],
        }
    }
    html = " ".join(
        [
            "gpt-5.2-2025-12-11",
            "400,000<!-- --> context window",
            "128,000<!-- --> max output tokens",
            "$1.75",
            "$14.00",
            "Reasoning.effort supports: none (default), low, medium, high and xhigh.",
        ]
    ).encode()
    seen = []

    def fetch(url):
        seen.append(url)
        return json.dumps(qwen).encode() if "openrouter" in url else html

    refresh = runner.refresh_campaign_provider_authority(fetch)
    assert seen == [runner.QWEN_ENDPOINTS_URL, runner.OPENAI_GPT52_URL]
    assert refresh["normalized"]["openai_native_judge"]["reasoning_effort"] == "medium"


def test_exact_mcnemar_uses_the_frozen_one_sided_integer_tail() -> None:
    runner = _load_runner()
    assert runner.exact_mcnemar(2, 0) == Fraction(1, 4)
    assert runner.exact_mcnemar(5, 0) == Fraction(1, 32)


def _pairs(*, wins: int, losses: int, premise_regressions: int = 0):
    rows = []
    for index in range(451):
        if index < wins:
            fast, deep = False, True
        elif index < wins + losses:
            fast, deep = True, False
        else:
            fast = deep = True
        rows.append(
            {
                "question_id": f"q-{index:03d}",
                "ability": (
                    "premise_awareness"
                    if wins <= index < wins + premise_regressions
                    else "static_state"
                ),
                "fast_correct": fast,
                "deep_correct": deep,
                "native_judge_valid": True,
                "receipt_sha256": f"{index + 1:064x}",
                "settled": True,
            }
        )
    return rows


def test_paired_gate_requires_all_pairs_effect_interval_premise_and_settlement() -> None:
    runner = _load_runner()
    passing = {
        "pairs": _pairs(wins=30, losses=0),
        "lafs_gain": "0.01",
        "accepted_submission": False,
        "published_leaderboard_scores": ["0.02"],
        "submission_score": "0.03",
    }
    result = runner.validate_paired_results(passing)
    assert result["internal_benchmark_success"] is True
    assert result["external_sota"] is False
    assert float(result["paired_risk_difference_lower_bound"]) > 0

    with pytest.raises(RuntimeError, match="exactly 451"):
        runner.validate_paired_results({**passing, "pairs": passing["pairs"][:-1]})
    assert runner.validate_paired_results(
        {**passing, "pairs": _pairs(wins=22, losses=0)}
    )["internal_benchmark_success"] is False
    assert runner.validate_paired_results(
        {**passing, "pairs": _pairs(wins=30, losses=1, premise_regressions=1)}
    )["internal_benchmark_success"] is False
    unsettled = _pairs(wins=30, losses=0)
    unsettled[0]["settled"] = False
    assert runner.validate_paired_results(
        {**passing, "pairs": unsettled}
    )["internal_benchmark_success"] is False


def test_external_sota_requires_accepted_strict_leaderboard_win() -> None:
    runner = _load_runner()
    base = {
        "pairs": _pairs(wins=30, losses=0),
        "lafs_gain": "0.01",
        "accepted_submission": True,
        "published_leaderboard_scores": ["0.03"],
        "submission_score": "0.03",
    }
    assert runner.validate_paired_results(base)["external_sota"] is False
    assert runner.validate_paired_results(
        {**base, "submission_score": "0.0300001"}
    )["external_sota"] is True


def _proof(runner):
    core = {
        "schema_version": 2,
        "authorization": {
            "authorization_sha256": "a" * 64,
            "campaign_sha256": "b" * 64,
            "screen_id": "state-aware-full",
        },
        "selection": {
            "selection_sha256": "c" * 64,
            "input_manifest_sha256": "d" * 64,
            "state_mode": "structured-resource-v1",
        },
        "compiler": {
            "adapter_sha256": "e" * 64,
            "construction_params_sha256": "6" * 64,
            "prompt_sha256": "f" * 64,
            "schema_sha256": "1" * 64,
            "provider_code_sha256": "2" * 64,
            "binaries": {
                name: {"path": f"/bin/{name}", "bytes": 1, "sha256": "7" * 64}
                for name in ("server", "cli", "worker")
            },
        },
        "provider": {
            "requested_model": "qwen/qwen3.5-9b-20260310",
            "served_model": "qwen/qwen3.5-9b",
            "requested_provider": "deepinfra",
            "served_provider": "DeepInfra",
            "input_price_nanos_per_million": 100_000_000,
            "output_price_nanos_per_million": 150_000_000,
            "maximum_output_tokens": 4096,
            "maximum_attempts": 3,
        },
        "cache": {
            "namespace": "state-aware-full-v1",
            "source_receipts_sha256": "3" * 64,
        },
        "ledger": {
            "attempt_ids": ["attempt-1"],
            "before_event_sha256": "4" * 64,
            "after_event_sha256": "5" * 64,
            "campaign_journal_sha256": "8" * 64,
            "settled_nanos": 1,
            "unresolved_nanos": 0,
        },
        "isolation": {"tenant_id": "tenant-1"},
        "pairing": {"trajectory_count": 1},
    }
    return {**core, "construction_proof_sha256": runner.sha256_json(core)}


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("authorization", "campaign_sha256"),
        ("selection", "input_manifest_sha256"),
        ("selection", "state_mode"),
        ("compiler", "prompt_sha256"),
        ("provider", "served_model"),
        ("provider", "input_price_nanos_per_million"),
        ("cache", "source_receipts_sha256"),
        ("ledger", "attempt_ids"),
        ("ledger", "after_event_sha256"),
        ("ledger", "campaign_journal_sha256"),
        ("ledger", "settled_nanos"),
        ("ledger", "unresolved_nanos"),
    ],
)
def test_construction_proof_v2_rejects_every_bound_field_tamper(
    section: str, field: str
) -> None:
    runner = _load_runner()
    proof = _proof(runner)
    runner.validate_construction_proof_v2(proof)
    value = proof[section][field]
    proof[section][field] = ["tampered"] if isinstance(value, list) else "tampered"
    with pytest.raises(RuntimeError, match="sha256 mismatch"):
        runner.validate_construction_proof_v2(proof)


def test_public_prefix_status_rejects_answers_scores_and_uncommitted_tail() -> None:
    runner = _load_runner()
    status = {
        "schema_version": 1,
        "prefix_count": 12,
        "remaining_count": 439,
        "remaining_commitment_sha256": "a" * 64,
        "sealed_blob_sha256": "b" * 64,
        "rows": [
            {
                "sequence": index + 1,
                "structurally_valid": True,
                "receipt_valid": True,
                "settled": True,
            }
            for index in range(12)
        ],
    }
    runner.validate_public_prefix_status(status)
    status["rows"][0]["answer"] = "leaked"
    with pytest.raises(RuntimeError, match="oracle-bearing"):
        runner.validate_public_prefix_status(status)
    status["rows"][0].pop("answer")
    status["remaining_count"] = 438
    with pytest.raises(RuntimeError, match="remaining 439"):
        runner.validate_public_prefix_status(status)


def test_sealed_prefix_encrypts_private_answers_and_exposes_only_public_predicates(
    tmp_path: Path,
) -> None:
    runner = _load_runner()
    if runner.shutil.which("openssl") is None:
        pytest.skip("openssl is unavailable")
    private = tmp_path / "private.json"
    rows_path = tmp_path / "rows.json"
    sealed = tmp_path / "prefix.enc"
    status_path = tmp_path / "status.json"
    private.write_text('{"answer":"ORCHID-17"}\n', encoding="utf-8")
    rows = [
        {
            "sequence": index + 1,
            "structurally_valid": True,
            "receipt_valid": True,
            "settled": True,
        }
        for index in range(12)
    ]
    rows_path.write_text(json.dumps(rows), encoding="utf-8")
    previous = os.environ.get("TEST_PREFIX_SEAL")
    os.environ["TEST_PREFIX_SEAL"] = "fixture-passphrase"
    try:
        status = runner.seal_prefix(
            private,
            rows_path,
            sealed,
            status_path,
            "a" * 64,
            "TEST_PREFIX_SEAL",
        )
    finally:
        if previous is None:
            os.environ.pop("TEST_PREFIX_SEAL", None)
        else:
            os.environ["TEST_PREFIX_SEAL"] = previous
    assert b"ORCHID-17" not in sealed.read_bytes()
    assert status == json.loads(status_path.read_text(encoding="utf-8"))
    assert not runner._contains_oracle_key(status)
