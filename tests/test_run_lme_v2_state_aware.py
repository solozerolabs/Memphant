from __future__ import annotations

import json
import importlib.util
import os
import shutil
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


def _postgres_toolchain_fixture(runner):
    identities = {}
    for name in ("pg_dump", "pg_restore"):
        path = Path(shutil.which(name) or sys.executable).resolve()
        core = {
            "tool": name,
            "executable": name,
            "bytes": path.stat().st_size,
            "sha256": runner._sha256_file(path),
            "version": f"{name} (PostgreSQL) 17.0",
            "server_version_num": 170000,
        }
        identities[name] = {**core, "identity_sha256": runner.sha256_json(core)}
    core = {"identities": identities}
    return {**core, "toolchain_sha256": runner.sha256_json(core)}


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


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1.75e-6, b'{"value":1.75e-6}'),
        (1e-7, b'{"value":1e-7}'),
        (-1e-7, b'{"value":-1e-7}'),
        (1e20, b'{"value":1e+20}'),
        (-0.0, b'{"value":-0.0}'),
    ],
)
def test_rust_json_hash_matches_serde_numeric_encoding(
    value: float, expected: bytes
) -> None:
    runner = _load_runner()
    assert runner.sha256_rust_json({"value": value}) == hashlib.sha256(
        expected
    ).hexdigest()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_rust_json_hash_rejects_nonfinite_numbers(value: float) -> None:
    runner = _load_runner()
    with pytest.raises(ValueError, match="Out of range float values"):
        runner.sha256_rust_json({"value": value})


def test_rust_json_exponent_normalization_does_not_rewrite_strings() -> None:
    runner = _load_runner()
    expected = b'{"label":"e-007 and e+020","value":1e-7}'
    assert runner.sha256_rust_json(
        {"label": "e-007 and e+020", "value": 1e-7}
    ) == hashlib.sha256(expected).hexdigest()


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

    inventory = runner._reader_liability_inventory(
        processor_rows, reader, judge, native_judge_question_ids={"image"}
    )
    by_id = {row["question_id"]: row for row in inventory["rows"]}
    assert by_id["image"]["input_reservation_units"] == 262_144
    assert by_id["text"]["input_reservation_units"] == 200_527
    assert by_id["image"]["per_arm_liability_nanos"] == 160_662_150
    assert by_id["text"]["per_arm_liability_nanos"] == 23_052_700
    assert by_id["image"]["native_judge_required"] is True
    assert by_id["text"]["native_judge_required"] is False
    assert inventory["native_judge_rows"] == 1
    assert inventory["reader_arm_liability_nanos"] == 183_714_850
    assert inventory["image_rows"] == 1
    assert inventory["text_rows"] == 1
    changed_local_diagnostic = [dict(row) for row in processor_rows]
    changed_local_diagnostic[0]["local_processor_input_tokens"] = 99_999
    changed = runner._reader_liability_inventory(
        changed_local_diagnostic,
        reader,
        judge,
        native_judge_question_ids={"image"},
    )
    assert changed["rows"][0]["input_reservation_units"] == 262_144

    reader["multimodal_provider_prompt_ceiling_tokens"] = 262_143
    with pytest.raises(RuntimeError, match="provider prompt ceiling drift"):
        runner._reader_liability_inventory(
            processor_rows,
            reader,
            judge,
            native_judge_question_ids={"image"},
        )


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
                "eval_function": (
                    "llm_abstention_checker"
                    if index < 128
                    else (
                        "llm_gotchas_checker"
                        if index < 156
                        else "norm_phrase_set_match"
                    )
                ),
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
            "native_judge_required": False,
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

    with pytest.raises(RuntimeError, match="reader liability inventory drift"):
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
    events[3]["observation_sha256"] = runner.sha256_rust_json(observations)
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
        "response_id": "generation-1",
        "source_attempt_id": "attempt-1",
        "source_started_event_sha256": runner.sha256_rust_json(events[2]),
        "source_result_event_sha256": runner.sha256_rust_json(events[3]),
        "provider_result_sha256": "5" * 64,
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
        events,
        cache_hit_receipts=[hit],
        authorization_sha256="9" * 64,
        cache_namespace="longmemeval-v2-construction-v1",
    )
    assert cache_proof["paid_key_count"] == 2
    assert cache_proof["cache_hit_key_count"] == 1
    with pytest.raises(RuntimeError, match="authorization identity"):
        runner.validate_and_settle_construction_wave(
            _WaveLedger(), wave, events, cache_hit_receipts=[hit]
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
        "binding_sha256": "9" * 64,
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


def test_runner_creates_canonical_construction_binding_once(monkeypatch, tmp_path):
    runner = _load_runner()
    artifact_root = tmp_path / "campaign"
    artifact_root.mkdir()
    paths = runner._campaign_artifact_paths(artifact_root)
    Path(paths["construction_subledger"]).write_bytes(b"")
    Path(paths["journal"]).write_bytes(b"")
    Path(paths["observation_cache"]).mkdir()
    Path(paths["cache_hits"]).mkdir()
    plan = {
        "extraction_key": "8" * 64,
        "request_sha256": "7" * 64,
        "per_attempt_reservation_nanos": 10,
        "requested_model": "qwen/qwen3.5-9b-20260310",
        "maximum_attempts": 3,
        "source_kind": "resource",
        "source_body_sha256": "6" * 64,
        "batch_index": 0,
        "evidence_slices_sha256": "5" * 64,
    }
    construction = {
        "state_mode": "structured-resource-v1",
        "model": "qwen/qwen3.5-9b-20260310",
        "response_model": "qwen/qwen3.5-9b",
        "provider": "deepinfra",
        "prompt_sha256": "4" * 64,
        "code_sha256s": {
            "crates/memphant-runtime/src/structured_state_openrouter.rs": "3" * 64
        },
        "input_price_nanos_per_million": 100_000_000,
        "output_price_nanos_per_million": 150_000_000,
        "maximum_output_tokens": 4096,
        "maximum_attempts": 3,
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"construction": construction}))
    census_core = {
        "manifest_sha256": runner._sha256_file(manifest_path),
        "construction": {
            "plan_inventory": [plan],
            "plan_inventory_sha256": runner.sha256_json([plan]),
            "input_manifest_sha256": "2" * 64,
            "construction_identity_sha256": runner.sha256_json(construction),
        },
    }
    census = {**census_core, "census_sha256": runner.sha256_json(census_core)}
    census_path = artifact_root / "CAMPAIGN-CENSUS.json"
    census_path.write_text(json.dumps(census))
    wave_core = {
        "schema_version": 1,
        "campaign_census_sha256": census["census_sha256"],
        "ordered_plans_sha256": census["construction"]["plan_inventory_sha256"],
        "plans": [plan],
    }
    wave = {**wave_core, "wave_sha256": runner.sha256_json(wave_core)}
    wave_path = Path(paths["construction_wave"])
    wave_path.write_text(json.dumps(wave))
    scope = {
        "inputs": {
            "census_sha256": census["census_sha256"],
            "census_file_sha256": runner._sha256_file(census_path),
            "manifest_sha256": runner._sha256_file(manifest_path),
        },
        "artifacts": paths,
        "execution": {"cache_namespace": "fixture-v1"},
    }
    packet = {
        "schema_version": 1,
        "status": "AUTHORIZED_STATE_MEMORY_CAMPAIGN",
        **scope,
        "authorization": {
            "authorization_scope_sha256": runner.sha256_json(scope)
        },
    }
    authorization_path = artifact_root / "CAMPAIGN-AUTHORIZATION.json"
    authorization_path.write_text(json.dumps(packet))
    monkeypatch.setattr(runner, "CAMPAIGN_ARTIFACT_ROOT", artifact_root)
    monkeypatch.setattr(runner, "CANONICAL_CAMPAIGN_AUTHORIZATION", authorization_path)
    monkeypatch.setattr(runner, "CANONICAL_CAMPAIGN_CENSUS", census_path)
    monkeypatch.setattr(runner, "CANONICAL_CAMPAIGN_MANIFEST", manifest_path)

    binding_path = runner.create_construction_binding(authorization_path, [plan])
    binding = json.loads(binding_path.read_text())

    assert binding_path == Path(paths["construction_bindings"]) / (
        runner.sha256_json([plan]) + ".json"
    )
    assert binding["authority"]["authorization_scope_sha256"] == runner.sha256_json(
        scope
    )
    assert Path(binding["cache"]["observation_cache_path"]) == Path(
        paths["observation_cache"]
    ).resolve()
    assert runner.create_construction_binding(authorization_path, [plan]) == binding_path


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
        "execution_plan_sha256": "c" * 64,
        "reservation_plan_sha256": "d" * 64,
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
    sealed = tmp_path / "prefix.enc"
    status_path = tmp_path / "status.json"
    private.write_text(
        json.dumps(
                                {
                "schema_version": 1,
                "execution_plan_sha256": "c" * 64,
                "reservation_plan_sha256": "d" * 64,
                "cases": [
                    {
                        "sequence": index + 1,
                        "question_id": f"q-{index:03}",
                        "rows": [
                            {
                                "arm": arm,
                                "row_key": f"q-{index:03}:{arm}",
                                "answer": "ORCHID-17",
                                "output_sha256": "e" * 64,
                                "receipt_sha256": "f" * 64,
                                "structurally_valid": True,
                                "receipt_valid": True,
                                    "settled": True,
                                    "official_row": {"question_id": f"q-{index:03}"},
                                    "provider_record": {},
                                    **({"deep_provider_record": {}} if arm == "deep" else {}),
                                }
                            for arm in ("fast", "deep")
                        ],
                    }
                    for index in range(12)
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    previous = os.environ.get("TEST_PREFIX_SEAL")
    os.environ["TEST_PREFIX_SEAL"] = "fixture-passphrase"
    try:
        status = runner.seal_prefix(
            private,
            sealed,
            status_path,
            "a" * 64,
            "TEST_PREFIX_SEAL",
            execution_plan_sha256="c" * 64,
            reservation_plan_sha256="d" * 64,
        )
    finally:
        if previous is None:
            os.environ.pop("TEST_PREFIX_SEAL", None)
        else:
            os.environ["TEST_PREFIX_SEAL"] = previous
    assert b"ORCHID-17" not in sealed.read_bytes()
    assert status == json.loads(status_path.read_text(encoding="utf-8"))
    assert not runner._contains_oracle_key(status)
    assert not private.exists(), "plaintext prefix output must be deleted after sealing"


def test_sealed_prefix_keeps_sole_private_evidence_when_encryption_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _load_runner()
    private = tmp_path / "private.json"
    private.write_text(
        json.dumps(
                                {
                "schema_version": 1,
                "execution_plan_sha256": "c" * 64,
                "reservation_plan_sha256": "d" * 64,
                "cases": [
                    {
                        "sequence": index + 1,
                        "question_id": f"q-{index:03}",
                        "rows": [
                            {
                                "arm": arm,
                                "row_key": f"q-{index:03}:{arm}",
                                "answer": "PAID-PRIVATE",
                                "output_sha256": "e" * 64,
                                "receipt_sha256": "f" * 64,
                                "structurally_valid": True,
                                "receipt_valid": True,
                                    "settled": True,
                                    "official_row": {"question_id": f"q-{index:03}"},
                                    "provider_record": {},
                                    **({"deep_provider_record": {}} if arm == "deep" else {}),
                                }
                            for arm in ("fast", "deep")
                        ],
                    }
                    for index in range(12)
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(runner.shutil, "which", lambda name: "/usr/bin/openssl")
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, b"", b"fail"),
    )
    monkeypatch.setenv("TEST_PREFIX_SEAL", "fixture-passphrase")

    with pytest.raises(RuntimeError, match="failed to seal"):
        runner.seal_prefix(
            private,
            tmp_path / "prefix.enc",
            tmp_path / "status.json",
            "a" * 64,
            "TEST_PREFIX_SEAL",
            execution_plan_sha256="c" * 64,
            reservation_plan_sha256="d" * 64,
        )
    assert private.is_file()
    assert "PAID-PRIVATE" in private.read_text(encoding="utf-8")
    assert not (tmp_path / "prefix.enc").exists()


def test_sealed_prefix_keeps_plaintext_and_removes_unverifiable_ciphertext(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _load_runner()
    private_root = tmp_path / "private"
    private_root.mkdir(mode=0o755)
    private = private_root / "private.json"
    private.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "execution_plan_sha256": "c" * 64,
                "reservation_plan_sha256": "d" * 64,
                "cases": [
                    {
                        "sequence": index + 1,
                        "question_id": f"q-{index:03}",
                        "rows": [
                            {
                                "arm": arm,
                                "row_key": f"q-{index:03}:{arm}",
                                "answer": "SOLE-PAID-EVIDENCE",
                                "output_sha256": "e" * 64,
                                "receipt_sha256": "f" * 64,
                                "structurally_valid": True,
                                "receipt_valid": True,
                                "settled": True,
                                "official_row": {"question_id": f"q-{index:03}"},
                                "provider_record": {},
                                **({"deep_provider_record": {}} if arm == "deep" else {}),
                            }
                            for arm in ("fast", "deep")
                        ],
                    }
                    for index in range(12)
                ],
            }
        ),
        encoding="utf-8",
    )

    def fake_openssl(command, **_kwargs):
        output = Path(command[command.index("-out") + 1])
        if "-d" in command:
            output.write_bytes(b"not the paid plaintext")
        else:
            output.write_bytes(b"ciphertext")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(runner.shutil, "which", lambda name: "/usr/bin/openssl")
    monkeypatch.setattr(runner.subprocess, "run", fake_openssl)
    monkeypatch.setenv("TEST_PREFIX_SEAL", "fixture-passphrase")
    sealed = tmp_path / "prefix.enc"

    with pytest.raises(RuntimeError, match="ciphertext verification failed"):
        runner.seal_prefix(
            private,
            sealed,
            tmp_path / "status.json",
            "a" * 64,
            "TEST_PREFIX_SEAL",
            execution_plan_sha256="c" * 64,
            reservation_plan_sha256="d" * 64,
        )

    assert private.is_file()
    assert "SOLE-PAID-EVIDENCE" in private.read_text(encoding="utf-8")
    assert private.stat().st_mode & 0o777 == 0o600
    assert private_root.stat().st_mode & 0o777 == 0o700
    assert not sealed.exists()


def test_execution_plan_freezes_exact_paired_order_without_oracle_input(
    tmp_path: Path,
) -> None:
    runner = _load_runner()
    data_root = tmp_path / "official"
    haystack_path = data_root / "haystacks/lme_v2_medium.json"
    haystack_path.parent.mkdir(parents=True)
    # Reverse insertion order proves that the campaign order is derived from
    # the frozen lexicographic identity contract, not source-file ordering.
    haystack = {
        f"question-{index:03}": [f"trajectory-{index % 17:02}"]
        for index in reversed(range(451))
    }
    haystack_path.write_text(json.dumps(haystack), encoding="utf-8")
    case_order = sorted(haystack)
    census = {
        "enumeration": {
            "question_pairs": 451,
            "case_order_sha256": runner.sha256_json(case_order),
            "sealed_prefix_ids_sha256": runner.sha256_json(case_order[:12]),
            "remaining_ids_sha256": runner.sha256_json(case_order[12:]),
        }
    }

    plan = runner.build_execution_plan(census, data_root)

    assert plan["case_count"] == 451
    assert plan["row_count"] == 902
    assert [row["arm"] for row in plan["rows"][:4]] == [
        "fast",
        "deep",
        "fast",
        "deep",
    ]
    assert [row["question_id"] for row in plan["rows"][:4]] == [
        case_order[0],
        case_order[0],
        case_order[1],
        case_order[1],
    ]
    assert plan["prefix"] == {
        "count": 12,
        "ids_sha256": runner.sha256_json(case_order[:12]),
        "row_count": 24,
    }
    assert plan["remaining"] == {
        "count": 439,
        "ids_sha256": runner.sha256_json(case_order[12:]),
        "row_count": 878,
    }
    assert plan["execution_plan_sha256"] == runner.sha256_json(
        {key: value for key, value in plan.items() if key != "execution_plan_sha256"}
    )
    assert runner._contains_oracle_key(plan) is False


def test_execution_plan_rejects_census_identity_drift(tmp_path: Path) -> None:
    runner = _load_runner()
    data_root = tmp_path / "official"
    haystack_path = data_root / "haystacks/lme_v2_medium.json"
    haystack_path.parent.mkdir(parents=True)
    haystack = {
        f"question-{index:03}": [f"trajectory-{index:03}"]
        for index in range(451)
    }
    haystack_path.write_text(json.dumps(haystack), encoding="utf-8")
    census = {
        "enumeration": {
            "question_pairs": 451,
            "case_order_sha256": "0" * 64,
            "sealed_prefix_ids_sha256": runner.sha256_json(sorted(haystack)[:12]),
            "remaining_ids_sha256": runner.sha256_json(sorted(haystack)[12:]),
        }
    }

    with pytest.raises(RuntimeError, match="execution inventory differs from census"):
        runner.build_execution_plan(census, data_root)


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql://user:pass@example.com:5432/memphant",
        "postgresql://user:pass@10.0.0.7:5432/memphant",
        "postgresql://user:pass@localhost:5432/production",
        "sqlite:///tmp/memphant.db",
    ],
)
def test_scratch_case_bank_contract_rejects_remote_or_noncanonical_base(
    database_url: str,
) -> None:
    runner = _load_runner()
    with pytest.raises(RuntimeError, match="local scratch Postgres base"):
        runner.scratch_case_database_contract(database_url, "question-000")


def test_scratch_case_bank_contract_uses_three_distinct_content_addressed_databases() -> None:
    runner = _load_runner()
    contract = runner.scratch_case_database_contract(
        "postgresql://memphant:memphant@localhost:5432/memphant",
        "question/with unsafe punctuation?",
    )

    assert set(contract["databases"]) == {"source", "fast", "deep"}
    assert len(set(contract["databases"].values())) == 3
    assert all(
        name.startswith("memphant_lme2_") and len(name) <= 63
        for name in contract["databases"].values()
    )
    assert contract["base_identity"] == {
        "scheme": "postgresql",
        "host": "localhost",
        "port": 5432,
        "database": "memphant",
    }
    assert "memphant:memphant" not in json.dumps(contract)


def test_case_bank_dump_is_data_only_and_excludes_ephemeral_or_secret_rows(
    tmp_path: Path,
) -> None:
    runner = _load_runner()
    archive = tmp_path / "case-bank.dump"
    command = runner.case_bank_dump_command(
        "postgresql://memphant:memphant@localhost:5432/memphant_lme2_deadbeef_source",
        archive,
    )

    assert command[:4] == ["pg_dump", "--format=custom", "--data-only", "--schema=memphant"]
    assert command[-2:] == [
        f"--file={archive.resolve()}",
        "postgresql://memphant:memphant@localhost:5432/memphant_lme2_deadbeef_source",
    ]
    excluded = {
        item.split("=", 1)[1]
        for item in command
        if item.startswith("--exclude-table-data=")
    }
    assert excluded == {
        "memphant.api_key",
        "memphant.event_outbox",
        "memphant.job_state",
        "memphant.retrieval_trace",
        "memphant.review_event",
        "memphant.review_event_unit",
        "memphant.schema_migrations",
    }


def test_cache_only_construction_environment_drops_provider_credentials(
    tmp_path: Path,
) -> None:
    runner = _load_runner()
    binding_path = tmp_path / "binding.json"
    observation_cache = tmp_path / "observations"
    source_receipts = tmp_path / "receipts"
    subledger = tmp_path / "construction.jsonl"
    campaign_journal = tmp_path / "campaign.jsonl"
    binding = {
        "authorization": {
            "authorization_sha256": "a" * 64,
            "campaign_sha256": "b" * 64,
            "screen_id": "state-aware-full",
        },
        "provider": {
            "requested_model": "qwen/qwen3.5-9b-20260310",
            "served_model": "qwen/qwen3.5-9b",
            "served_provider": "DeepInfra",
            "input_price_nanos_per_million": 100_000_000,
            "output_price_nanos_per_million": 150_000_000,
        },
        "cache": {
            "namespace": "longmemeval-v2-construction-v1",
            "observation_cache_path": str(observation_cache),
            "source_receipts_path": str(source_receipts),
        },
        "ledger": {
            "subledger_path": str(subledger),
            "campaign_journal_path": str(campaign_journal),
            "source_ledger_prefix_bytes": 0,
            "source_ledger_prefix_sha256": hashlib.sha256(b"").hexdigest(),
        },
        "coverage": {
            "plans": [{"per_attempt_reservation_nanos": 12_345}],
        },
    }
    binding_path.write_text(json.dumps(binding), encoding="utf-8")
    manifest = {
        "construction": {
            "model": "qwen/qwen3.5-9b-20260310",
            "prompt_path": "benchmarks/prompts/structured_state_extractor.v1.md",
            "tokenizer_path": "tokenizer/tokenizer.json",
            "tokenizer_config_path": "tokenizer/tokenizer_config.json",
        }
    }
    environment = runner.cache_only_construction_environment(
        binding_path=binding_path,
        binding=binding,
        manifest=manifest,
        data_root=tmp_path,
        database_url=(
            "postgresql://memphant:memphant@localhost:5432/"
            "memphant_lme2_deadbeef_source"
        ),
        base_environment={
            "PATH": "/usr/bin",
            "OPENROUTER_API_KEY": "must-not-cross",
            "OPENAI_API_KEY": "must-not-cross",
            "DEEPINFRA_API_KEY": "must-not-cross",
        },
    )

    assert environment["MEMPHANT_STRUCTURED_STATE_CACHE_ONLY"] == "on"
    assert environment["MEMPHANT_LME_CONSTRUCTION_BINDING"] == str(
        binding_path.resolve()
    )
    assert environment["MEMPHANT_STRUCTURED_STATE_OBSERVATION_CACHE"] == str(
        observation_cache.resolve()
    )
    assert environment["PATH"] == "/usr/bin"
    assert not any("API_KEY" in key for key in environment)


def test_case_bank_manifest_binds_archive_construction_and_logical_inventory(
    tmp_path: Path, monkeypatch,
) -> None:
    runner = _load_runner()
    archive = tmp_path / "bank.dump"
    archive.write_bytes(b"immutable-bank")
    proof = _proof(runner)
    binding_core = {
        "schema_version": 1,
        "authorization": proof["authorization"],
        "selection": proof["selection"],
        "compiler": {
            key: proof["compiler"][key]
            for key in ("prompt_sha256", "schema_sha256", "provider_code_sha256")
        },
        "provider": proof["provider"],
        "coverage": {"plans": ["plan"]},
    }
    binding = {
        **binding_core,
        "binding_sha256": runner.sha256_json(binding_core),
    }
    binding_path = tmp_path / "binding.json"
    binding_path.write_text(json.dumps(binding), encoding="utf-8")
    proof["binding_sha256"] = binding["binding_sha256"]
    proof["construction_proof_sha256"] = runner.sha256_json(
        {
            key: value
            for key, value in proof.items()
            if key != "construction_proof_sha256"
        }
    )
    proof_path = tmp_path / "construction-proof.json"
    proof_path.write_text(json.dumps(proof), encoding="utf-8")
    contract = runner.scratch_case_database_contract(
        "postgresql://memphant:memphant@localhost:5432/memphant",
        "question-000",
    )
    output = tmp_path / "case-bank.json"
    materialization = {
        "trajectory_count": 2,
        "trajectory_ids_sha256": "1" * 64,
        "trajectory_content_sha256": "2" * 64,
    }
    logical_inventory = {
        "schema_migrations": 3,
        "tenant": 1,
        "episode": 8,
        "resource": 3,
        "memory_unit": 11,
    }
    monkeypatch.setattr(
        runner, "_load_canonical_construction_binding", lambda *args, **kwargs: binding
    )
    monkeypatch.setattr(
        runner,
        "_derive_canonical_construction_receipts",
        lambda value: (proof["cache"], proof["ledger"]),
    )
    authority = {
        key: tmp_path / key
        for key in (
            "authorization_path",
            "census_path",
            "manifest_path",
            "wave_path",
            "binding_root",
        )
    }

    manifest = runner.write_case_bank_manifest(
        archive=archive,
        output=output,
        contract=contract,
        binding_path=binding_path,
        binding_authority=authority,
        construction_proof_path=proof_path,
        materialization=materialization,
        logical_inventory=logical_inventory,
        postgres_toolchain=_postgres_toolchain_fixture(runner),
    )

    assert manifest == json.loads(output.read_text(encoding="utf-8"))
    assert manifest["archive"] == {
        "bytes": len(b"immutable-bank"),
        "sha256": hashlib.sha256(b"immutable-bank").hexdigest(),
        "format": "pg_dump-custom-data-only-v1",
    }
    assert manifest["construction"]["binding_sha256"] == binding["binding_sha256"]
    assert manifest["materialization"] == materialization
    assert manifest["logical_inventory_sha256"] == runner.sha256_json(
        logical_inventory
    )
    assert runner._contains_oracle_key(manifest) is False

    unrelated = json.loads(json.dumps(proof))
    unrelated["selection"]["selection_sha256"] = hashlib.sha256(
        b"unrelated-selection"
    ).hexdigest()
    unrelated["construction_proof_sha256"] = runner.sha256_json(
        {
            key: value
            for key, value in unrelated.items()
            if key != "construction_proof_sha256"
        }
    )
    unrelated_path = tmp_path / "unrelated-proof.json"
    unrelated_path.write_text(json.dumps(unrelated), encoding="utf-8")
    with pytest.raises(
        RuntimeError, match="construction proof differs from binding"
    ):
        runner.write_case_bank_manifest(
            archive=archive,
            output=tmp_path / "unrelated-bank.json",
            contract=contract,
            binding_path=binding_path,
            binding_authority=authority,
            construction_proof_path=unrelated_path,
            materialization=materialization,
            logical_inventory=logical_inventory,
            postgres_toolchain=_postgres_toolchain_fixture(runner),
        )


def test_committed_case_bank_evidence_has_no_local_paths_or_placeholder_hashes() -> None:
    artifact_root = (
        ROOT
        / "docs/build-log/artifacts/state-memory-sota/longmemeval-v2-pilot"
        / "scratch-case-bank-smoke"
    )
    files = sorted(artifact_root.glob("*.json"))
    assert files
    forbidden_paths = (
        "/Users/",
        "/home/",
        "/private/var/",
        "/tmp/",
        ".codex/worktrees",
        "\\Users\\",
    )
    placeholders = {character * 64 for character in "0123456789abcdef"}
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert not any(value in text for value in forbidden_paths), path
        assert not any(f'"{value}"' in text for value in placeholders), path
    proof = json.loads(
        (artifact_root / "CONSTRUCTION-PROOF.v2.json").read_text(
            encoding="utf-8"
        )
    )
    assert {
        fingerprint["path"] for fingerprint in proof["compiler"]["binaries"].values()
    } == {
        "target/debug/memphant-server",
        "target/debug/memphant-cli",
        "target/debug/memphant-worker",
    }


def test_restore_case_bank_pair_creates_fresh_migrated_fast_and_deep_databases(
    tmp_path: Path,
) -> None:
    runner = _load_runner()
    base = "postgresql://memphant:memphant@localhost:5432/memphant"
    question_id = "question-000"
    contract = runner.scratch_case_database_contract(base, question_id)
    archive = tmp_path / "bank.dump"
    archive.write_bytes(b"immutable-bank")
    manifest = {
        "schema_version": 1,
        "contract": contract,
        "archive": {
            "bytes": archive.stat().st_size,
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "format": "pg_dump-custom-data-only-v1",
        },
        "construction": {
            "binding_sha256": "a" * 64,
            "proof_sha256": "b" * 64,
        },
        "materialization": {
            "trajectory_count": 1,
            "trajectory_ids_sha256": "c" * 64,
            "trajectory_content_sha256": "d" * 64,
        },
        "logical_inventory": {"schema_migrations": 3, "tenant": 1},
        "logical_inventory_sha256": runner.sha256_json(
            {"schema_migrations": 3, "tenant": 1}
        ),
        "postgres_toolchain": _postgres_toolchain_fixture(runner),
    }
    manifest["case_bank_sha256"] = runner.sha256_json(manifest)
    commands = []

    def run_command(command, **kwargs):
        commands.append((command, kwargs))
        stdout = ""
        if command[0] == "psql" and "select tablename" in command[-1]:
            stdout = "schema_migrations\ntenant\n"
        elif command[0] == "psql" and '"schema_migrations"' in command[-1]:
            stdout = "3\n"
        elif command[0] == "psql" and '"tenant"' in command[-1]:
            stdout = "1\n"
        return subprocess.CompletedProcess(command, 0, stdout, "")

    clone = runner.restore_case_bank_pair(
        base_database_url=base,
        question_id=question_id,
        archive=archive,
        manifest=manifest,
        run_command=run_command,
    )

    assert clone["databases"] == {
        "fast": contract["databases"]["fast"],
        "deep": contract["databases"]["deep"],
    }
    flat = [command for command, _ in commands]
    for arm in ("fast", "deep"):
        name = contract["databases"][arm]
        assert [
            "dropdb",
            f"--maintenance-db={base}",
            "--if-exists",
            "--force",
            name,
        ] in flat
        assert ["createdb", f"--maintenance-db={base}", name] in flat
        assert any(
            command[:2] == [sys.executable, str(ROOT / "scripts/apply_memphant_migrations.py")]
            and command[-1].endswith(f"/{name}")
            for command in flat
        )
        assert any(
            Path(command[0]).name == "pg_restore"
            and command[1:4]
            == ["--exit-on-error", "--single-transaction", "--data-only"]
            and command[-2:] == [
                f"--dbname={runner._database_url_for_name(base, name)}",
                str(archive.resolve()),
            ]
            for command in flat
        )
    assert clone["clone_sha256"] == runner.sha256_json(
        {key: value for key, value in clone.items() if key != "clone_sha256"}
    )


def test_row_reservation_plan_exactly_decomposes_census_reader_judge_and_deep() -> None:
    runner = _load_runner()
    execution_rows = [
        {
            "sequence": sequence,
            "question_id": f"q-{(sequence - 1) // 2:03}",
            "arm": "fast" if sequence % 2 else "deep",
            "row_key": f"row-{sequence}",
        }
        for sequence in range(1, 903)
    ]
    execution_core = {
        "row_count": 902,
        "rows": execution_rows,
        "rows_sha256": runner.sha256_json(execution_rows),
    }
    execution = {
        **execution_core,
        "execution_plan_sha256": runner.sha256_json(execution_core),
    }
    reader_rows = [
        {
            "question_id": f"q-{index:03}",
            "reader_liability_nanos": 1000 + index,
            "native_judge_required": index < 156,
            "judge_liability_nanos": 2000 if index < 156 else 0,
            "per_arm_liability_nanos": (3000 + index if index < 156 else 1000 + index),
        }
        for index in range(451)
    ]
    reader_sum = sum(row["per_arm_liability_nanos"] for row in reader_rows)
    census = {
        "census_sha256": "c" * 64,
        "terms": {"R_sum": reader_sum, "S": 5000},
        "liability_derivation": {
            "reader_inventory": {
                "rows": reader_rows,
                "row_count": 451,
                "reader_arm_liability_nanos": reader_sum,
                "inventory_sha256": runner.sha256_json(reader_rows),
            }
        },
    }

    plan = runner.build_row_reservation_plan(execution, census)

    assert plan["row_count"] == 902
    assert plan["rows"][0]["components"] == {
        "reader": 1000,
        "judge": 2000,
    }
    assert plan["rows"][400]["components"] == {"reader": 1200}
    assert plan["rows"][1]["components"] == {
        "deep_recall": 5000,
        "reader": 1000,
        "judge": 2000,
    }
    assert plan["total_liability_nanos"] == 2 * reader_sum + 451 * 5000
    assert plan["reservation_plan_sha256"] == runner.sha256_json(
        {key: value for key, value in plan.items() if key != "reservation_plan_sha256"}
    )


def test_row_reservation_plan_rejects_mutated_execution_authority() -> None:
    runner = _load_runner()
    rows = [
        {
            "sequence": sequence,
            "question_id": f"q-{(sequence - 1) // 2:03}",
            "arm": "fast" if sequence % 2 else "deep",
            "row_key": f"q-{(sequence - 1) // 2:03}:"
            + ("fast" if sequence % 2 else "deep"),
        }
        for sequence in range(1, 903)
    ]
    execution_core = {
        "schema_version": 1,
        "benchmark": "LongMemEval-V2/medium",
        "case_count": 451,
        "row_count": 902,
        "case_order_sha256": "a" * 64,
        "prefix": {"count": 12, "ids_sha256": "b" * 64, "row_count": 24},
        "remaining": {"count": 439, "ids_sha256": "c" * 64, "row_count": 878},
        "rows": rows,
        "rows_sha256": runner.sha256_json(rows),
    }
    execution = {
        **execution_core,
        "execution_plan_sha256": runner.sha256_json(execution_core),
    }
    reader_rows = [
        {
            "question_id": f"q-{index:03}",
            "reader_liability_nanos": 100,
            "native_judge_required": True,
            "judge_liability_nanos": 200,
            "per_arm_liability_nanos": 300,
        }
        for index in range(451)
    ]
    census = {
        "census_sha256": "d" * 64,
        "terms": {"R_sum": 451 * 300, "S": 400},
        "liability_derivation": {
            "reader_inventory": {
                "rows": reader_rows,
                "row_count": 451,
                "reader_arm_liability_nanos": 451 * 300,
                "inventory_sha256": runner.sha256_json(reader_rows),
            }
        },
    }
    execution["rows"][0]["row_key"] = "tampered:fast"

    with pytest.raises(RuntimeError, match="execution plan identity"):
        runner.build_row_reservation_plan(execution, census)


def _strict_row_plan(runner):
    rows = []
    for sequence in range(1, 903):
        question = (sequence - 1) // 2
        arm = "fast" if sequence % 2 else "deep"
        components = {"reader": 1000}
        if question < 156:
            components["judge"] = 4_000_000
        if arm == "deep":
            components = {"deep_recall": 3000, **components}
        rows.append(
            {
                "sequence": sequence,
                "row_key": f"q-{question:03}:{arm}",
                "question_id": f"q-{question:03}",
                "arm": arm,
                "native_judge_required": question < 156,
                "components": components,
                "maximum_liability_nanos": sum(components.values()),
            }
        )
    core = {
        "schema_version": 1,
        "execution_plan_sha256": "e" * 64,
        "census_sha256": "c" * 64,
        "row_count": 902,
        "rows": rows,
        "rows_sha256": runner.sha256_json(rows),
        "total_liability_nanos": sum(
            row["maximum_liability_nanos"] for row in rows
        ),
    }
    return {**core, "reservation_plan_sha256": runner.sha256_json(core)}


def _load_provider_attempts():
    spec = importlib.util.spec_from_file_location(
        "state_memory_provider_attempts", ROOT / "scripts/provider_attempts.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _priced_component_response(response_id: str, cost: str) -> dict[str, object]:
    return {
        "response_id": response_id,
        "requested_model": "qwen/qwen3.5-9b-20260310",
        "served_model": "fixture/served",
        "provider": "Fixture",
        "usage": {
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
            "cost": cost,
        },
        "request_sha256": "1" * 64,
        "result_sha256": "2" * 64,
        "elapsed_seconds": 0.1,
        "retry_index": 0,
        "parse_status": "provider_response_validated",
    }


def test_row_state_machine_resolves_exact_liability_and_crash_resume(
    tmp_path: Path,
) -> None:
    runner = _load_runner()
    attempts = _load_provider_attempts()
    ledger = attempts.ProviderAttemptLedger(
        tmp_path / "campaign.jsonl",
        "a" * 64,
        "row-fixture",
        20_000_000_000,
        0,
    )
    plan = _strict_row_plan(runner)
    machine = runner.RowExecutionStateMachine(plan, ledger, admitted_case_count=12)

    with pytest.raises(RuntimeError, match="requires settled deep_recall"):
        machine.start(
            "q-000:deep",
            "reader",
            requested_model="qwen/qwen3.5-9b-20260310",
            request_sha256="1" * 64,
        )
    request_key = machine.start(
        "q-000:deep",
        "deep_recall",
        requested_model="qwen/qwen3.5-9b-20260310",
        request_sha256="1" * 64,
    )
    assert ledger.snapshot()["attempts"][0]["start"]["max_liability_nanos"] == 3000
    machine.result(
        "q-000:deep",
        "deep_recall",
        _priced_component_response("deep-0", "0.0000025"),
    )
    machine.start(
        "q-000:deep",
        "reader",
        requested_model="qwen/qwen3.5-9b-20260310",
        request_sha256="1" * 64,
    )
    machine.result(
        "q-000:deep",
        "reader",
        _priced_component_response("reader-0", "0.0000005"),
    )
    assert request_key.endswith(":q-000:deep:deep_recall")

    resumed = runner.RowExecutionStateMachine(plan, ledger, admitted_case_count=12)
    assert resumed.component_status("q-000:deep", "reader") == "result"
    with pytest.raises(RuntimeError, match="already has a terminal attempt"):
        resumed.start(
            "q-000:deep",
            "reader",
            requested_model="qwen/qwen3.5-9b-20260310",
            request_sha256="1" * 64,
        )
    with pytest.raises(RuntimeError, match="outside the admitted case prefix"):
        resumed.start(
            "q-012:fast",
            "reader",
            requested_model="qwen/qwen3.5-9b-20260310",
            request_sha256="1" * 64,
        )
    ledger.close()


def test_strict_reader_proxy_uses_frozen_row_reservation_and_exact_generation(
    tmp_path: Path,
) -> None:
    runner = _load_runner()
    attempts = _load_provider_attempts()
    ledger = attempts.ProviderAttemptLedger(
        tmp_path / "campaign.jsonl",
        "a" * 64,
        "reader-fixture",
        20_000_000_000,
        0,
    )
    machine = runner.RowExecutionStateMachine(
        _strict_row_plan(runner), ledger, admitted_case_count=12
    )
    payload = {
        "model": "qwen/qwen3.5-9b-20260310",
        "messages": [{"role": "user", "content": "private prompt"}],
        "max_tokens": 20_000,
        "temperature": 0.6,
        "top_p": 0.95,
        "top_k": 20,
        "provider": {
            "only": ["deepinfra"],
            "allow_fallbacks": False,
            "require_parameters": True,
            "data_collection": "deny",
            "zdr": True,
            "quantizations": ["bf16"],
            "max_price": {"prompt": 0.1, "completion": 0.15},
        },
    }
    persisted = []

    response = runner.execute_strict_reader_call(
        payload=payload,
        row_key="q-000:fast",
        row_state=machine,
        transport=lambda request: {
            "response": {
                "id": "generation-reader-0",
                "model": "qwen/qwen3.5-9b",
                "choices": [{"message": {"content": "PRIVATE ANSWER"}}],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "total_tokens": 12,
                    "cost": 0.0000005,
                },
            },
            "generation": {
                "id": "generation-reader-0",
                "model": "qwen/qwen3.5-9b",
                "provider_name": "DeepInfra",
                "tokens_prompt": 10,
                "tokens_completion": 2,
                "total_cost": 0.0000005,
            },
        },
        persist_private=lambda raw, receipt: persisted.append((raw, receipt)),
    )

    assert response["choices"][0]["message"]["content"] == "PRIVATE ANSWER"
    snapshot = ledger.snapshot()
    assert snapshot["unresolved_max_liability_nanos"] == 0
    assert snapshot["attempts"][0]["start"]["max_liability_nanos"] == 1000
    receipt = snapshot["attempts"][0]["result"]["response"]
    assert persisted[0][0]["response"]["id"] == "generation-reader-0"
    assert persisted[0][0]["generation"]["provider_name"] == "DeepInfra"
    assert receipt["response_id"] == "generation-reader-0"
    assert "choices" not in receipt
    assert "PRIVATE ANSWER" not in json.dumps(receipt)
    ledger.close()


class _InMemoryAttemptLedger:
    def __init__(self, attempts=None):
        self.attempts = list(attempts or [])

    def snapshot(self):
        return {"attempts": self.attempts}

    def record(self, event, request_key, payload):
        latest_sequence = max(
            (
                value
                for attempt in self.attempts
                for value in (
                    attempt.get("start_sequence"),
                    attempt.get("result_sequence"),
                )
                if type(value) is int
            ),
            default=0,
        )
        if event == "start":
            self.attempts.append(
                {
                    "attempt_id": len(self.attempts) + 1,
                    "request_key": request_key,
                    "retry_index": payload.get("retry_index", 0),
                    "start_sequence": latest_sequence + 1,
                    "result_sequence": None,
                    "start": payload,
                    "status": "started",
                    "result": None,
                    "error": None,
                }
            )
            return
        attempt = next(
            row
            for row in reversed(self.attempts)
            if row["request_key"] == request_key and row["status"] == "started"
        )
        attempt["status"] = event
        attempt["result_sequence"] = latest_sequence + 1
        attempt[event] = payload


def _completed_reader_attempts(plan):
    attempts = []
    for row in plan["rows"]:
        request_key = (
            f"lme-v2-row:{row['sequence']}:{row['row_key']}:reader"
        )
        attempts.append(
            {
                "attempt_id": len(attempts) + 1,
                "request_key": request_key,
                "retry_index": 0,
                "start": {
                    "max_liability_nanos": row["components"]["reader"],
                    "retry_index": 0,
                    "requested_model": "qwen/qwen3.5-9b-20260310",
                    "request_sha256": "1" * 64,
                },
                "status": "result",
                "result": {"response": _priced_component_response("reader", "0.0000005")},
                "error": None,
            }
        )
    return attempts


def test_native_judge_proxy_waits_for_all_readers_and_writes_priced_receipt() -> None:
    runner = _load_runner()
    plan = _strict_row_plan(runner)
    ledger = _InMemoryAttemptLedger(_completed_reader_attempts(plan))
    machine = runner.RowExecutionStateMachine(
        plan, ledger, admitted_case_count=451
    )
    payload = {
        "model": "gpt-5.2-2025-12-11",
        "messages": [{"role": "user", "content": "private answer and reference"}],
        "reasoning_effort": "medium",
        "max_completion_tokens": 2048,
    }

    response = runner.execute_native_judge_call(
        payload=payload,
        row_key="q-000:fast",
        row_state=machine,
        transport=lambda request: {
            "id": "response-judge-0",
            "model": "gpt-5.2-2025-12-11",
            "choices": [
                {"message": {"role": "assistant", "content": '{"label":1}'}}
            ],
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 100,
                "total_tokens": 1100,
            },
        },
        persist_private=lambda raw, receipt: None,
    )

    assert response["choices"][0]["message"]["content"] == '{"label":1}'
    receipt = ledger.attempts[-1]["result"]["response"]
    assert receipt["usage"] == {
        "prompt_tokens": 1000,
        "completion_tokens": 100,
        "total_tokens": 1100,
        "cost": "0.00315",
    }
    assert "choices" not in receipt


def test_local_reader_proxy_replays_identical_sdk_retry_without_paid_redispatch() -> None:
    runner = _load_runner()
    plan = _strict_row_plan(runner)
    ledger = _InMemoryAttemptLedger([])
    machine = runner.RowExecutionStateMachine(plan, ledger, admitted_case_count=12)
    upstream_calls = []

    def transport(payload):
        upstream_calls.append(payload)
        response = {
            "id": "generation-reader-proxy",
            "model": "qwen/qwen3.5-9b",
            "choices": [{"message": {"content": "boxed answer"}}],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "total_tokens": 110,
                "cost": "0.0000005",
            },
        }
        return {
            "response": response,
            "generation": {
                "id": response["id"],
                "model": response["model"],
                "provider_name": "DeepInfra",
                "tokens_prompt": 100,
                "tokens_completion": 10,
                "total_cost": "0.0000005",
            },
        }

    proxy, base_url = runner.start_reader_proxy(
        row_key="q-000:fast",
        row_state=machine,
        transport=transport,
        persist_private=lambda raw, receipt: None,
    )
    payload = {
        "model": "Qwen/Qwen3.5-9B",
        "messages": [{"role": "user", "content": "private prompt"}],
        "max_tokens": 20_000,
        "temperature": 0.6,
        "top_p": 0.95,
        "extra_body": {"top_k": 20},
    }
    try:
        responses = []
        for _ in range(2):
            request = runner.urllib.request.Request(
                base_url + "/chat/completions",
                data=runner.canonical_json(payload),
                headers={"content-type": "application/json"},
            )
            with runner.urllib.request.urlopen(request) as response:
                responses.append(json.loads(response.read()))
    finally:
        proxy.shutdown()
        proxy.server_close()

    assert responses[0] == responses[1]
    assert len(upstream_calls) == 1
    assert proxy.dispatch_count == 1
    assert len(ledger.attempts) == 1


def test_local_reader_proxy_rejects_official_wire_drift_before_reservation() -> None:
    runner = _load_runner()
    ledger = _InMemoryAttemptLedger([])
    machine = runner.RowExecutionStateMachine(
        _strict_row_plan(runner), ledger, admitted_case_count=12
    )
    proxy, _ = runner.start_reader_proxy(
        row_key="q-000:fast",
        row_state=machine,
        transport=lambda payload: pytest.fail("provider transport must not run"),
        persist_private=lambda raw, receipt: pytest.fail("must not persist"),
    )
    try:
        with pytest.raises(RuntimeError, match="official Qwen reader wire"):
            proxy.dispatch(
                {
                    "model": "floating-model",
                    "messages": [{"role": "user", "content": "private"}],
                    "max_tokens": 20_000,
                    "temperature": 0.6,
                    "top_p": 0.95,
                    "top_k": 20,
                }
            )
    finally:
        proxy.shutdown()
        proxy.server_close()
    assert ledger.attempts == []


def test_private_output_middle_state_reconciles_without_provider_replay(
    tmp_path: Path,
) -> None:
    runner = _load_runner()
    plan = _strict_row_plan(runner)
    ledger = _InMemoryAttemptLedger([])
    machine = runner.RowExecutionStateMachine(plan, ledger, admitted_case_count=12)
    machine.start(
        "q-000:fast",
        "reader",
        requested_model="qwen/qwen3.5-9b-20260310",
        request_sha256="1" * 64,
    )
    response = {"id": "raw-private", "answer": "SECRET"}
    receipt = {
        **_priced_component_response("raw-private", "0.0000005"),
        "request_sha256": "1" * 64,
        "result_sha256": runner.sha256_json(response),
    }
    authority = {
        key: f"{index + 1:064x}"
        for index, key in enumerate(sorted(runner.PRIVATE_OUTPUT_AUTHORITY_FIELDS))
    }
    path = tmp_path / "private" / "reader.json"
    runner.persist_private_provider_output(
        path,
        private_root=tmp_path / "private",
        row_key="q-000:fast",
        component="reader",
        response=response,
        receipt=receipt,
        authority=authority,
    )

    runner.reconcile_private_provider_output(
        path,
        row_state=machine,
        row_key="q-000:fast",
        component="reader",
        authority=authority,
    )

    assert ledger.attempts[0]["status"] == "result"
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700


def test_row_state_rejects_rehashed_extra_paid_component() -> None:
    runner = _load_runner()
    plan = json.loads(json.dumps(_strict_row_plan(runner)))
    plan["rows"][0]["components"]["shadow_judge"] = 1
    plan["rows"][0]["maximum_liability_nanos"] += 1
    plan["rows_sha256"] = runner.sha256_json(plan["rows"])
    core = {
        key: value for key, value in plan.items() if key != "reservation_plan_sha256"
    }
    plan["reservation_plan_sha256"] = runner.sha256_json(core)

    with pytest.raises(RuntimeError, match="reservation inventory is malformed"):
        runner.RowExecutionStateMachine(
            plan, _InMemoryAttemptLedger([]), admitted_case_count=12
        )


def test_reader_result_append_failure_preserves_reconcilable_started_state(
    tmp_path: Path,
) -> None:
    runner = _load_runner()

    class FailingResultLedger(_InMemoryAttemptLedger):
        def __init__(self):
            super().__init__([])
            self.fail_result = True

        def record(self, event, request_key, payload):
            if event == "result" and self.fail_result:
                self.fail_result = False
                raise RuntimeError("fsync failure")
            super().record(event, request_key, payload)

    ledger = FailingResultLedger()
    machine = runner.RowExecutionStateMachine(
        _strict_row_plan(runner), ledger, admitted_case_count=12
    )
    private_root = tmp_path / "private"
    path = private_root / "q-000-fast" / "reader.json"
    authority = {
        key: f"{index + 1:064x}"
        for index, key in enumerate(sorted(runner.PRIVATE_OUTPUT_AUTHORITY_FIELDS))
    }

    with pytest.raises(RuntimeError, match="fsync failure"):
        runner.execute_strict_reader_call(
            payload={
                "model": "qwen/qwen3.5-9b-20260310",
                "messages": [{"role": "user", "content": "private"}],
                "max_tokens": 20_000,
                "temperature": 0.6,
                "top_p": 0.95,
                "top_k": 20,
                "provider": {
                    "only": ["deepinfra"],
                    "allow_fallbacks": False,
                    "require_parameters": True,
                    "data_collection": "deny",
                    "zdr": True,
                    "quantizations": ["bf16"],
                    "max_price": {"prompt": 0.1, "completion": 0.15},
                },
            },
            row_key="q-000:fast",
            row_state=machine,
            transport=lambda _payload: {
                "response": {
                    "id": "reader-crash",
                    "model": "qwen/qwen3.5-9b",
                    "choices": [{"message": {"content": "PRIVATE"}}],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                        "cost": "0.0000005",
                    },
                },
                "generation": {
                    "id": "reader-crash",
                    "model": "qwen/qwen3.5-9b",
                    "provider_name": "DeepInfra",
                    "tokens_prompt": 1,
                    "tokens_completion": 1,
                    "total_cost": "0.0000005",
                },
            },
            persist_private=lambda raw, receipt: runner.persist_private_provider_output(
                path,
                private_root=private_root,
                row_key="q-000:fast",
                component="reader",
                response=raw,
                receipt=receipt,
                authority=authority,
            ),
        )

    assert machine.component_status("q-000:fast", "reader") == "started"
    runner.reconcile_private_provider_output(
        path,
        row_state=machine,
        row_key="q-000:fast",
        component="reader",
        authority=authority,
    )
    assert machine.component_status("q-000:fast", "reader") == "result"


def test_private_output_rejects_symlinked_parent(tmp_path: Path) -> None:
    runner = _load_runner()
    private_root = tmp_path / "private"
    outside = tmp_path / "outside"
    private_root.mkdir()
    outside.mkdir()
    (private_root / "row").symlink_to(outside, target_is_directory=True)
    response = {"id": "raw"}
    receipt = {
        **_priced_component_response("raw", "0.0000005"),
        "result_sha256": runner.sha256_json(response),
    }
    authority = {
        key: f"{index + 1:064x}"
        for index, key in enumerate(sorted(runner.PRIVATE_OUTPUT_AUTHORITY_FIELDS))
    }
    with pytest.raises(RuntimeError, match="parent is a symlink"):
        runner.persist_private_provider_output(
            private_root / "row/output.json",
            private_root=private_root,
            row_key="q-000:fast",
            component="reader",
            response=response,
            receipt=receipt,
            authority=authority,
        )


def test_deep_proxy_reserves_from_plan_and_requires_complete_server_receipt() -> None:
    runner = _load_runner()
    plan = _strict_row_plan(runner)
    ledger = _InMemoryAttemptLedger()
    machine = runner.RowExecutionStateMachine(plan, ledger, admitted_case_count=12)
    runner.reserve_deep_recall(
        row_key="q-000:deep",
        row_state=machine,
        request_sha256="1" * 64,
    )
    deep_core = {
        "requested_model": "qwen/qwen3.5-9b-20260310",
        "served_models": ["qwen/qwen3.5-9b"],
        "served_providers": ["DeepInfra"],
        "allow_fallbacks": False,
        "attempt_count": 2,
        "dispatch_count": 2,
        "generation_ids": ["deep-1", "deep-2"],
        "generation_receipts": [{"id": "deep-1"}, {"id": "deep-2"}],
        "generation_receipts_sha256": runner.sha256_json(
            [{"id": "deep-1"}, {"id": "deep-2"}]
        ),
        "deep_attempt_journal_sha256": "2" * 64,
        "prompt_tokens": 10,
        "completion_tokens": 2,
        "total_tokens": 12,
        "settled_nanos": 2500,
        "request_sha256": "1" * 64,
    }
    runner.settle_deep_recall(
        row_key="q-000:deep",
        row_state=machine,
        receipt={**deep_core, "receipt_sha256": runner.sha256_json(deep_core)},
        persist_private=lambda raw, receipt: None,
    )
    assert ledger.attempts[0]["start"]["max_liability_nanos"] == 3000
    response = ledger.attempts[0]["result"]["response"]
    assert response["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 2,
        "total_tokens": 12,
        "cost": "0.0000025",
    }


def test_deep_attempt_journal_replays_durable_turn_and_reconciles_receipt(
    tmp_path: Path,
) -> None:
    runner = _load_runner()
    private_root = tmp_path / "private"
    journal = private_root / "row/deep-attempts.private.jsonl"
    authority = {
        key: f"{index + 1:064x}"
        for index, key in enumerate(sorted(runner.PRIVATE_OUTPUT_AUTHORITY_FIELDS))
    }
    payload = {
        "model": "qwen/qwen3.5-9b-20260310",
        "messages": [{"role": "user", "content": "private turn"}],
        "tools": [{"type": "function", "function": {"name": "recall"}}],
        "tool_choice": "required",
        "max_completion_tokens": 4096,
        "stream": True,
        "provider": {
            "only": ["deepinfra"],
            "allow_fallbacks": False,
            "require_parameters": True,
            "data_collection": "deny",
            "zdr": True,
            "quantizations": ["bf16"],
            "max_price": {"prompt": 0.1, "completion": 0.15},
        },
    }
    upstream = []

    def transport(url, *, api_key, body):
        upstream.append((url, body))
        if body is not None:
            return 200, {"X-Generation-Id": "gen-1"}, b"data: [DONE]\n\n"
        generation = {
            "data": {
                "id": "gen-1",
                "model": "qwen/qwen3.5-9b",
                "provider_name": "DeepInfra",
                "tokens_prompt": 10,
                "tokens_completion": 2,
                "total_cost": "0.0000025",
            }
        }
        return 200, {}, runner.canonical_json(generation)

    first = runner._DeepRecallProxy(
        "key",
        journal_path=journal,
        private_root=private_root,
        row_key="q-000:deep",
        authority=authority,
        transport=transport,
    )
    try:
        assert first.dispatch(payload)[0] == 200
    finally:
        first.server_close()

    resumed = runner._DeepRecallProxy(
        "key",
        journal_path=journal,
        private_root=private_root,
        row_key="q-000:deep",
        authority=authority,
        transport=transport,
    )
    try:
        assert resumed.dispatch(payload)[0] == 200
        receipt = resumed.receipt("1" * 64)
    finally:
        resumed.server_close()

    assert len([call for call in upstream if call[1] is not None]) == 1
    assert len([call for call in upstream if call[1] is None]) == 1
    assert receipt["attempt_count"] == 1
    assert receipt["deep_attempt_journal_sha256"] == runner._sha256_file(journal)
    assert journal.stat().st_mode & 0o777 == 0o600


def test_deep_receipt_rejects_dispatch_without_unique_priced_generation(
    tmp_path: Path,
) -> None:
    runner = _load_runner()
    private_root = tmp_path / "private"
    authority = {
        key: f"{index + 1:064x}"
        for index, key in enumerate(sorted(runner.PRIVATE_OUTPUT_AUTHORITY_FIELDS))
    }
    proxy = runner._DeepRecallProxy(
        "key",
        journal_path=private_root / "row/deep-attempts.private.jsonl",
        private_root=private_root,
        row_key="q-000:deep",
        authority=authority,
        transport=lambda url, **kwargs: (429, {}, b'{"error":"limited"}'),
    )
    payload = {
        "model": "qwen/qwen3.5-9b-20260310",
        "messages": [{"role": "user", "content": "private turn"}],
        "tools": [{"type": "function", "function": {"name": "recall"}}],
        "tool_choice": "required",
        "max_completion_tokens": 4096,
        "stream": True,
        "provider": {
            "only": ["deepinfra"],
            "allow_fallbacks": False,
            "require_parameters": True,
            "data_collection": "deny",
            "zdr": True,
            "quantizations": ["bf16"],
            "max_price": {"prompt": 0.1, "completion": 0.15},
        },
    }
    try:
        assert proxy.dispatch(payload)[0] == 429
        with pytest.raises(RuntimeError, match="dispatch/generation settlement mismatch"):
            proxy.receipt("1" * 64)
        with pytest.raises(RuntimeError, match="terminally rejected"):
            proxy.dispatch(payload)
    finally:
        proxy.server_close()


def test_official_runtime_code_rejects_incomplete_canonical_checkpoint(
    tmp_path: Path,
) -> None:
    runner = _load_runner()
    lock = json.loads(
        (ROOT / "benchmarks/manifests/longmemeval_v2.lock.json").read_text()
    )
    runtime = tmp_path / "runtime-code" / lock["code"]["commit"]
    runtime.mkdir(parents=True)
    with pytest.raises(RuntimeError, match="checkpoint is incomplete"):
        runner.acquire_official_runtime_code(tmp_path)


def test_official_runtime_code_acquisition_is_code_only_and_hash_bound(
    monkeypatch, tmp_path: Path,
) -> None:
    runner = _load_runner()
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_longmemeval_v2 as adapter

    downloads = []

    def fake_download(url, destination):
        downloads.append(url)
        destination.write_bytes(b"pinned archive fixture")

    def fake_extract(_archive, destination):
        root = destination / "LongMemEval-V2-fixture"
        (root / "memory_modules").mkdir(parents=True)
        (root / "memory_modules/memory.py").write_text("MEMORY_REGISTRY = {}\n")
        return root

    monkeypatch.setattr(adapter, "_download", fake_download)
    monkeypatch.setattr(adapter, "_extract_archive", fake_extract)
    monkeypatch.setattr(adapter, "verify_code", lambda path, files: None)
    official, proof = runner.acquire_official_runtime_code(tmp_path)

    assert (official / "memory_modules/memory.py").is_file()
    assert proof["commit"] in downloads[0]
    assert proof["archive"]["sha256"] == runner._sha256_file(
        official.parent / "official.tar.gz"
    )
    assert not (tmp_path / "data").exists()


def _execution_for_row_plan(runner, plan):
    rows = [
        {
            "sequence": row["sequence"],
            "question_id": row["question_id"],
            "arm": row["arm"],
            "row_key": row["row_key"],
        }
        for row in plan["rows"]
    ]
    core = {
        "schema_version": 1,
        "benchmark": "LongMemEval-V2/medium",
        "case_count": 451,
        "row_count": 902,
        "case_order_sha256": "a" * 64,
        "prefix": {"count": 12, "ids_sha256": "b" * 64, "row_count": 24},
        "remaining": {"count": 439, "ids_sha256": "c" * 64, "row_count": 878},
        "rows": rows,
        "rows_sha256": runner.sha256_json(rows),
    }
    return {**core, "execution_plan_sha256": runner.sha256_json(core)}


def _bind_row_plan_to_execution(runner, plan, execution):
    core = {
        key: value
        for key, value in plan.items()
        if key != "reservation_plan_sha256"
    }
    core["execution_plan_sha256"] = execution["execution_plan_sha256"]
    return {**core, "reservation_plan_sha256": runner.sha256_json(core)}


def _completed_prefix_attempts(plan):
    attempts = []
    for row in plan["rows"][:24]:
        components = ["reader"] if row["arm"] == "fast" else ["deep_recall", "reader"]
        for component in components:
            attempts.append(
                {
                    "attempt_id": len(attempts) + 1,
                    "request_key": (
                        f"lme-v2-row:{row['sequence']}:{row['row_key']}:{component}"
                    ),
                    "retry_index": 0,
                    "start": {
                        "max_liability_nanos": row["components"][component],
                        "retry_index": 0,
                        "requested_model": "qwen/qwen3.5-9b-20260310",
                        "request_sha256": "1" * 64,
                    },
                    "status": "result",
                    "result": {
                        "response": _priced_component_response(
                            f"prefix-{len(attempts)}", "0.0000005"
                        )
                    },
                    "error": None,
                }
            )
    return attempts


def test_remaining_commitment_is_exact_and_resume_is_deterministic() -> None:
    runner = _load_runner()
    plan = _strict_row_plan(runner)
    execution = _execution_for_row_plan(runner, plan)
    plan = _bind_row_plan_to_execution(runner, plan, execution)
    commitment = runner.build_remaining_commitment(execution, plan)
    assert commitment["remaining_count"] == 439
    assert commitment["remaining_row_count"] == 878
    assert not runner._contains_oracle_key(commitment)

    ledger = _InMemoryAttemptLedger(_completed_prefix_attempts(plan))
    actions = runner.remaining_resume_actions(commitment, plan, ledger)
    assert actions[:2] == [
        {
            "sequence": 25,
            "row_key": "q-012:fast",
            "component": "reader",
            "request_key": "lme-v2-row:25:q-012:fast:reader",
        },
        {
            "sequence": 26,
            "row_key": "q-012:deep",
            "component": "deep_recall",
            "request_key": "lme-v2-row:26:q-012:deep:deep_recall",
        },
    ]
    assert len(actions) == 878

    tampered = json.loads(json.dumps(commitment))
    tampered["remaining_rows_sha256"] = "f" * 64
    with pytest.raises(RuntimeError, match="remaining commitment identity"):
        runner.remaining_resume_actions(tampered, plan, ledger)


def test_remaining_commitment_rejects_shortened_rehashed_inventory() -> None:
    runner = _load_runner()
    plan = _strict_row_plan(runner)
    execution = _execution_for_row_plan(runner, plan)
    execution_core = {
        key: value
        for key, value in execution.items()
        if key != "execution_plan_sha256"
    }
    execution_core["rows"] = execution_core["rows"][:-2]
    execution_core["row_count"] = 900
    execution_core["rows_sha256"] = runner.sha256_json(execution_core["rows"])
    shortened_execution = {
        **execution_core,
        "execution_plan_sha256": runner.sha256_json(execution_core),
    }
    plan_core = {
        key: value for key, value in plan.items() if key != "reservation_plan_sha256"
    }
    plan_core["rows"] = plan_core["rows"][:-2]
    plan_core["row_count"] = 900
    plan_core["rows_sha256"] = runner.sha256_json(plan_core["rows"])
    plan_core["execution_plan_sha256"] = shortened_execution["execution_plan_sha256"]
    plan_core["total_liability_nanos"] = sum(
        row["maximum_liability_nanos"] for row in plan_core["rows"]
    )
    shortened_plan = {
        **plan_core,
        "reservation_plan_sha256": runner.sha256_json(plan_core),
    }

    with pytest.raises(RuntimeError, match="execution authority"):
        runner.build_remaining_commitment(shortened_execution, shortened_plan)


def _complete_row_attempts(plan):
    attempts = []

    def append(row, component, response_id):
        requested_model = (
            "gpt-5.2-2025-12-11"
            if component == "judge"
            else "qwen/qwen3.5-9b-20260310"
        )
        response = {
            **_priced_component_response(response_id, "0.0000005"),
            "requested_model": requested_model,
            "served_model": (
                "gpt-5.2-2025-12-11"
                if component == "judge"
                else "qwen/qwen3.5-9b"
            ),
            "provider": "OpenAI" if component == "judge" else "DeepInfra",
        }
        attempts.append(
            {
                "attempt_id": len(attempts) + 1,
                "request_key": (
                    f"lme-v2-row:{row['sequence']}:{row['row_key']}:{component}"
                ),
                "retry_index": 0,
                "start": {
                    "max_liability_nanos": row["components"][component],
                    "retry_index": 0,
                    "requested_model": requested_model,
                    "request_sha256": "1" * 64,
                },
                "status": "result",
                "result": {"response": response},
                "error": None,
            }
        )

    for row in plan["rows"]:
        if row["arm"] == "deep":
            append(row, "deep_recall", f"deep-{row['sequence']}")
        append(row, "reader", f"reader-{row['sequence']}")
    for row in plan["rows"]:
        if "judge" in row["components"]:
            append(row, "judge", f"judge-{row['sequence']}")
    for index, attempt in enumerate(attempts):
        attempt["start_sequence"] = index * 2 + 1
        attempt["result_sequence"] = index * 2 + 2
    return attempts


def _official_pairs():
    return [
        {
            "question_id": f"q-{index:03}",
            "ability": "state",
            "fast_correct": index >= 30,
            "deep_correct": True,
            "native_judge_valid": True,
            "settled": True,
            "receipt_sha256": f"{index + 1:064x}",
        }
        for index in range(451)
    ]


def test_all_451_row_settlement_builds_native_package_and_closes() -> None:
    runner = _load_runner()
    plan = _strict_row_plan(runner)
    attempts = _complete_row_attempts(plan)
    ledger = _InMemoryAttemptLedger(attempts)
    ledger.closed = False

    def close_campaign(path):
        ledger.closed = True
        return {
            "authorization_sha256": "a" * 64,
            "settled_nanos": len(attempts) * 500,
            "unresolved_max_liability_nanos": 0,
            "total_liability_nanos": len(attempts) * 500,
            "journal_sha256": "b" * 64,
        }

    ledger.close_campaign = close_campaign
    release_lock = json.loads(
        (ROOT / "benchmarks/manifests/longmemeval_v2.lock.json").read_text()
    )
    lafs_core = {
        "schema_version": 1,
        "compute_lafs_sha256": release_lock["code"]["files"][
            "leaderboard/compute_lafs.py"
        ],
        "summary": {"lafs_gain": 0.01},
    }
    lafs = {**lafs_core, "lafs_proof_sha256": runner.sha256_json(lafs_core)}
    metrics_core = {
        "schema_version": 1,
        "runtime_code_proof_sha256": "e" * 64,
        "runtime_code_commit": release_lock["code"]["commit"],
        "arms": {
            "fast": {"overall": {"overall_full_set": 0.5}},
            "deep": {"overall": {"overall_full_set": 0.5}},
        },
        "lafs": lafs,
    }
    official_metrics = {
        **metrics_core,
        "official_metrics_sha256": runner.sha256_json(metrics_core),
    }
    package = runner.build_native_official_package(
        pairs=_official_pairs(),
        reservation_plan=plan,
        ledger_snapshot=ledger.snapshot(),
        official_metrics_artifact=official_metrics,
        upstream_identity={
            "code_commit": release_lock["code"]["commit"],
            "dataset_revision": "b" * 40,
            "native_harness_sha256": "c" * 64,
        },
    )
    assert package["row_settlement"]["row_attempt_count"] == 1665
    assert package["official_metrics"]["pairs"] == 451
    assert package["official_metrics"]["internal_benchmark_success"] is True
    assert package["official_metrics"]["external_sota"] is False

    closure = runner.close_completed_row_campaign(
        ledger=ledger,
        reservation_plan=plan,
        native_package=package,
        official_metrics_artifact=official_metrics,
        closure_path=Path("closure.json"),
    )
    assert ledger.closed is True
    assert closure["unresolved_max_liability_nanos"] == 0

    broken = json.loads(json.dumps(ledger.snapshot()))
    broken["attempts"].pop()
    with pytest.raises(RuntimeError, match="exactly 1665"):
        runner.validate_complete_row_settlement(plan, broken)


def test_row_settlement_rejects_judge_started_before_reader_result() -> None:
    runner = _load_runner()
    plan = _strict_row_plan(runner)
    attempts = _complete_row_attempts(plan)
    first_judge_start = min(
        attempt["start_sequence"]
        for attempt in attempts
        if attempt["request_key"].endswith(":judge")
    )
    first_reader = next(
        attempt
        for attempt in attempts
        if attempt["request_key"].endswith(":reader")
    )
    first_reader["result_sequence"] = first_judge_start + 1
    with pytest.raises(RuntimeError, match="before all reader outputs settled"):
        runner.validate_complete_row_settlement(plan, {"attempts": attempts})
