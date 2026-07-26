from __future__ import annotations

import json
import hashlib
import importlib.util
from pathlib import Path
import shutil
import tarfile
import types
from dataclasses import dataclass
import inspect
import asyncio
import threading

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_restraint_bench.py"
LOCK = ROOT / "benchmarks" / "manifests" / "memsyco.lock.json"
BASELINE_CONFIG = ROOT / "benchmarks" / "memsyco" / "memphant.baseline.json"
ADAPTER = ROOT / "benchmarks" / "memsyco" / "memphant_baseline.py"
BOOTSTRAP = ROOT / "benchmarks" / "memsyco" / "harness_bootstrap.py"
PACKET_VERIFIER = ROOT / "scripts" / "verify_memsyco_calibration_packets.py"
CALIBRATION = ROOT / "benchmarks" / "memsyco" / "calibration"
SCOPE_CALIBRATION = ROOT / "benchmarks" / "memsyco" / "scope_calibration"
OBJECTIVE_CALIBRATION = ROOT / "benchmarks" / "memsyco" / "objective_calibration"
VALID_SELECTION_CALIBRATION = ROOT / "benchmarks" / "memsyco" / "valid_selection_calibration"
PERSONALIZED_USE_CALIBRATION = (
    ROOT / "benchmarks" / "memsyco" / "personalized_use_calibration"
)
OVERLAP_AUDITOR = ROOT / "scripts" / "audit_memsyco_calibration_overlap.py"
PMU_ROWWISE_CONTROLLER = (
    ROOT
    / "docs/build-log/artifacts/unified-sota-20260714"
    / "memsyco-evidence-sota-20260715T172416Z/personalized-use/future-v2"
    / "run-pmu-qualification-rowwise.sh"
)


def test_memsyco_runner_exists() -> None:
    assert RUNNER.is_file()


def test_memsyco_adapter_and_bootstrap_exist() -> None:
    assert ADAPTER.is_file()
    assert BOOTSTRAP.is_file()


def test_pmu_rowwise_controller_gates_each_fresh_row_before_advancing() -> None:
    controller = PMU_ROWWISE_CONTROLLER.read_text(encoding="utf-8")

    assert 'verify_row "$out"' in controller
    assert '.metrics.with_memory as $metrics' in controller
    assert '$metrics.answer_accuracy_sum == 1' in controller
    assert '$metrics.preference_used_sum == 1' in controller
    assert '$metrics.judge_parse_failed == 0' in controller
    assert '$metrics.judge_error_count == 0' in controller


def test_pmu_rowwise_controller_can_collect_valid_quality_misses() -> None:
    controller = PMU_ROWWISE_CONTROLLER.read_text(encoding="utf-8")

    assert 'PMU_ROW_POLICY="${PMU_ROW_POLICY:-strict}"' in controller
    assert 'strict|aggregate' in controller
    assert 'if test "$ARM" != episode_only && test "$PMU_ROW_POLICY" = strict' in controller


def test_pmu_rowwise_controller_terminates_every_lane_descendant_on_exit() -> None:
    controller = PMU_ROWWISE_CONTROLLER.read_text(encoding="utf-8")

    assert "terminate_process_tree()" in controller
    assert 'pgrep -P "$parent"' in controller
    assert "trap cleanup EXIT" in controller
    assert "trap 'exit 130' INT" in controller
    assert "trap 'exit 143' TERM" in controller


def test_pmu_rowwise_controller_routes_cargo_environment_through_doppler() -> None:
    controller = PMU_ROWWISE_CONTROLLER.read_text(encoding="utf-8")

    assert 'CARGO_TARGET_DIR_VALUE="${CARGO_TARGET_DIR:-$REPO/target}"' in controller
    assert 'CARGO_BUILD_JOBS_VALUE="${CARGO_BUILD_JOBS:-1}"' in controller
    assert 'CARGO_INCREMENTAL_VALUE="${CARGO_INCREMENTAL:-0}"' in controller
    assert controller.count('CARGO_TARGET_DIR="$CARGO_TARGET_DIR_VALUE"') == 2
    assert controller.count('CARGO_BUILD_JOBS="$CARGO_BUILD_JOBS_VALUE"') == 2
    assert controller.count('CARGO_INCREMENTAL="$CARGO_INCREMENTAL_VALUE"') == 2


def test_memsyco_lock_pins_the_complete_official_release() -> None:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))

    assert lock["benchmark"] == "MemSyco-Bench"
    assert lock["code"]["repository"] == "https://github.com/XMUDeepLIT/MemSyco-Bench"
    assert lock["code"]["revision"] == "c31e2c85ee8cc3c6f643587b8a6f4b5ad5eb3bf6"
    assert lock["code"]["license"] == "MIT"
    assert lock["code"]["files"]["LICENSE"] == (
        "f607254fa7fd8fd9e0ab9c2199ee568c44232053f5affa5130a5330b7bbf148a"
    )
    assert lock["dataset"]["schema_version"] == "1.2"
    assert lock["dataset"]["total_samples"] == 1550
    objective = lock["dataset"]["tasks"]["objective_fact_judgment"]
    assert objective["sha256"] == (
        "cd34eb4136dc9a5e2b4eff742a43de25d50bd1044455160a4b4eea0f082b81bc"
    )
    assert objective["manifest_sha256_claim"] != objective["sha256"]
    assert {
        task: spec["samples"] for task, spec in lock["dataset"]["tasks"].items()
    } == {
        "objective_fact_judgment": 300,
        "contextual_scope_control": 300,
        "memory_evidence_conflict": 300,
        "valid_memory_selection": 350,
        "personalized_memory_use": 300,
    }
    assert set(lock["native_scorer"]["files"]) == {
        "evaluation/run_task.py",
        "evaluation/_objective_base.py",
        "evaluation/task_objective_fact_judgment.py",
        "evaluation/task_contextual_scope_control.py",
        "evaluation/task_memory_evidence_conflict.py",
        "evaluation/task_valid_memory_selection.py",
        "evaluation/task_personalized_memory_use.py",
    }
    assert lock["protocol"]["controls"] == {
        "RawDialogue": sorted(lock["dataset"]["tasks"]),
        "NoMemory": ["objective_fact_judgment"],
    }
    assert lock["protocol"]["quality_threshold"] is None


def test_memsyco_baseline_config_is_only_the_official_adapter_contract() -> None:
    assert json.loads(BASELINE_CONFIG.read_text(encoding="utf-8")) == {
        "method": "MemPhant",
        "top_k": 10,
    }


def test_evidence_conflict_calibration_is_frozen_paired_and_oracle_separated() -> None:
    manifest = json.loads((CALIBRATION / "manifest.json").read_text(encoding="utf-8"))
    expected = {"development": (14, 7), "confirmation": (12, 6)}
    for split in ("development", "confirmation"):
        case_path = CALIBRATION / f"{split}.jsonl"
        oracle_path = CALIBRATION / f"{split}.oracle.jsonl"
        cases = [json.loads(line) for line in case_path.read_text().splitlines()]
        oracles = [json.loads(line) for line in oracle_path.read_text().splitlines()]
        spec = manifest["splits"][split]
        assert (spec["cases"], spec["families"]) == expected[split]
        assert spec["case_sha256"] == sha256(case_path.read_bytes())
        assert spec["oracle_sha256"] == sha256(oracle_path.read_bytes())
        assert {case["id"] for case in cases} == {oracle["id"] for oracle in oracles}
        assert len({case["metadata"]["topic"] for case in cases}) == expected[split][1]
        for case in cases:
            assert case["task"] == "memory_evidence_conflict"
            assert case["memory"]["policy"] == "defer_to_evidence"
            assert len(case["dialogue"]) == 4
            assert set(case) == {
                "id", "task", "dialogue", "question", "memory", "evaluation", "metadata"
            }
        for family in {oracle["family"] for oracle in oracles}:
            twins = sorted(
                (oracle for oracle in oracles if oracle["family"] == family),
                key=lambda oracle: oracle["twin"],
            )
            assert len(twins) == 2
            assert twins[0]["expected_target"] == twins[1]["misleading_preference"]
            assert twins[0]["misleading_preference"] == twins[1]["expected_target"]
        serialized_cases = json.dumps(cases, sort_keys=True)
        assert "expected_memory_roles" not in serialized_cases
        assert "decisive_evidence_span" not in serialized_cases

    development_domains = {
        row["metadata"]["topic"]
        for row in map(json.loads, (CALIBRATION / "development.jsonl").read_text().splitlines())
    }
    confirmation_domains = {
        row["metadata"]["topic"]
        for row in map(json.loads, (CALIBRATION / "confirmation.jsonl").read_text().splitlines())
    }
    assert development_domains.isdisjoint(confirmation_domains)
    assert "acoustics" in development_domains
    assert "telescope" in development_domains
    assert "sterilization" in confirmation_domains
    assert "refrigeration" not in confirmation_domains
    assert "acoustics" not in confirmation_domains


def test_contextual_scope_calibration_is_frozen_paired_and_oracle_separated() -> None:
    manifest = json.loads((SCOPE_CALIBRATION / "manifest.json").read_text(encoding="utf-8"))
    domains = {}
    for split in ("development", "confirmation"):
        case_path = SCOPE_CALIBRATION / f"{split}.jsonl"
        oracle_path = SCOPE_CALIBRATION / f"{split}.oracle.jsonl"
        cases = [json.loads(line) for line in case_path.read_text().splitlines()]
        oracles = [json.loads(line) for line in oracle_path.read_text().splitlines()]
        spec = manifest["splits"][split]
        assert (spec["cases"], spec["families"]) == (12, 6)
        assert spec["case_sha256"] == sha256(case_path.read_bytes())
        assert spec["oracle_sha256"] == sha256(oracle_path.read_bytes())
        assert {case["id"] for case in cases} == {oracle["id"] for oracle in oracles}
        assert all(case["task"] == "contextual_scope_control" for case in cases)
        assert all(case["memory"]["policy"] == "constrain_to_scope" for case in cases)
        assert all("applicability_scope" not in json.dumps(case) for case in cases)
        for case in cases:
            shared_choice = case["evaluation"]["reference_answer"].split("Use ", 1)[1].split(" for the shared", 1)[0]
            assert shared_choice in case["evaluation"]["rubric"]["scope_limits"]
        domains[split] = {case["metadata"]["topic"] for case in cases}
    assert domains["development"].isdisjoint(domains["confirmation"])


def test_calibration_packet_verifier_requires_exact_preference_scope(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("packet_verifier", PACKET_VERIFIER)
    assert spec and spec.loader
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    sample_id = "scope-1"
    oracle = tmp_path / "oracle.jsonl"
    oracle.write_text(json.dumps({
        "id": sample_id,
        "preference_value": "sunrise starts",
        "expected_applicability_scope": "solo planning",
        "expected_memory_role": "personalization",
    }) + "\n")
    proof = {
        "sample_key_sha256": hashlib.sha256(sample_id.encode()).hexdigest(),
        "typed_memories": [
            {"memory_role": "conversation_evidence", "content": "shared schedule boundary"},
            {"memory_role": "personalization", "content": (
                'planning_preferences item start_time: {"applicability_scope":"solo planning",'
                '"epistemic_use":"not_factual_evidence","memory_role":"personalization",'
                '"value":"sunrise starts"}'
            )},
        ],
    }
    (tmp_path / "proof.json").write_text(json.dumps(proof))
    assert verifier.verify(tmp_path, oracle) == {
        "cases": 1,
        "conversation_context_matches": 1,
        "pass": True,
        "scope_role_matches": 1,
    }


def test_calibration_packet_verifier_requires_current_active_preference(
    tmp_path: Path,
) -> None:
    spec = importlib.util.spec_from_file_location("packet_verifier", PACKET_VERIFIER)
    assert spec and spec.loader
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    sample_id = "valid-selection-1"
    oracle = tmp_path / "oracle.jsonl"
    oracle.write_text(json.dumps({
        "id": sample_id,
        "current_value": "paper books",
        "outdated_value": "audiobooks",
        "additional_current_values": ["window seats", "flashcards"],
    }) + "\n")
    case = {
        "id": sample_id,
        "question": "What format should I recommend?",
        "dialogue": [
            {"role": "user", "content": "I prefer audiobooks."},
            {"role": "assistant", "content": "I will remember that."},
            {"role": "user", "content": "I now prefer paper books."},
        ],
    }
    (tmp_path / "input.jsonl").write_text(json.dumps(case) + "\n")
    dialogue = "\n\n".join(
        f"{'User' if item['role'] == 'user' else 'Assistant'}: {item['content']}"
        for item in case["dialogue"]
    )
    dialogue_sha256 = hashlib.sha256(dialogue.encode()).hexdigest()
    question_sha256 = hashlib.sha256(case["question"].encode()).hexdigest()
    identity_material = hashlib.sha256(json.dumps(
        {
            "dialogue_sha256": dialogue_sha256,
            "question_sha256": question_sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()).hexdigest()
    sample_digest = hashlib.sha256(f"content-{identity_material}".encode()).hexdigest()
    proof_path = tmp_path / "proof.json"
    proof = {
        "sample_key_sha256": sample_digest,
        "sample_key_source": "label_free_content_hash",
        "sample_identity_material_sha256": identity_material,
        "dialogue_sha256": dialogue_sha256,
        "question_sha256": question_sha256,
        "typed_memories": [
            {
                "memory_role": "conversation_evidence",
                "content": "I preferred audiobooks, then updated that to paper books.",
            },
            {
                "memory_role": "personalization",
                "content": (
                    'reading_preferences item format: {"epistemic_use":"not_factual_evidence",'
                    '"memory_role":"personalization","value":"paper books"}'
                ),
            },
            {
                "memory_role": "personalization",
                "content": (
                    'seat_preferences item train: {"epistemic_use":"not_factual_evidence",'
                    '"memory_role":"personalization","value":"window seats"}'
                ),
            },
            {
                "memory_role": "personalization",
                "content": (
                    'study_preferences item method: {"epistemic_use":"not_factual_evidence",'
                    '"memory_role":"personalization","value":"flashcards"}'
                ),
            },
        ],
    }
    proof_path.write_text(json.dumps(proof))
    assert verifier.verify(tmp_path, oracle) == {
        "cases": 1,
        "current_preference_role_matches": 1,
        "outdated_active_personalization_absent": 1,
        "pass": True,
    }

    missing_auxiliary = proof["typed_memories"].pop()
    proof_path.write_text(json.dumps(proof))
    assert verifier.verify(tmp_path, oracle) == {
        "cases": 1,
        "current_preference_role_matches": 0,
        "outdated_active_personalization_absent": 1,
        "pass": False,
    }
    proof["typed_memories"].append(missing_auxiliary)

    proof["typed_memories"][1]["content"] = (
        'reading_preferences item format: {"epistemic_use":"not_factual_evidence",'
        '"memory_role":"personalization","value":"audiobooks"}'
    )
    proof_path.write_text(json.dumps(proof))
    assert verifier.verify(tmp_path, oracle) == {
        "cases": 1,
        "current_preference_role_matches": 0,
        "outdated_active_personalization_absent": 0,
        "pass": False,
    }


def test_calibration_packet_verifier_rejects_failed_experience_personalization(
    tmp_path: Path,
) -> None:
    spec = importlib.util.spec_from_file_location("packet_verifier", PACKET_VERIFIER)
    assert spec and spec.loader
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    sample_id = "personalized-use-1"
    oracle = tmp_path / "oracle.jsonl"
    oracle.write_text(
        json.dumps(
            {
                "id": sample_id,
                "current_preference_value": "practice drills",
                "rejected_experience_value": "long video lectures",
            }
        )
        + "\n"
    )
    case = {
        "id": sample_id,
        "question": "Which study option should be recommended?",
        "dialogue": [
            {"role": "user", "content": "For studying, I prefer practice drills."},
            {"role": "assistant", "content": "I will remember that preference."},
            {
                "role": "user",
                "content": (
                    "Long video lectures looked polished, but the experience did not "
                    "work for me and I stopped early."
                ),
            },
        ],
    }
    (tmp_path / "input.jsonl").write_text(json.dumps(case) + "\n")
    digest, identity_material, dialogue_sha256, question_sha256 = (
        verifier._label_free_identity(case)
    )
    proof_path = tmp_path / "proof.json"
    proof = {
        "sample_key_sha256": digest,
        "sample_key_source": "label_free_content_hash",
        "sample_identity_material_sha256": identity_material,
        "dialogue_sha256": dialogue_sha256,
        "question_sha256": question_sha256,
        "typed_memories": [
            {
                "memory_role": "conversation_evidence",
                "content": "The long video lectures looked polished but were unsuccessful.",
            },
            {
                "memory_role": "personalization",
                "content": (
                    'study_preferences item format: {"epistemic_use":"not_factual_evidence",'
                    '"memory_role":"personalization","value":"practice drills"}'
                ),
            },
        ],
    }
    proof_path.write_text(json.dumps(proof))

    assert verifier.verify(tmp_path, oracle) == {
        "cases": 1,
        "current_preference_role_matches": 1,
        "pass": True,
        "rejected_experience_personalization_absent": 1,
    }

    proof["sample_key_sha256"] = hashlib.sha256(sample_id.encode()).hexdigest()
    proof["sample_key_source"] = "official_argument"
    proof.pop("sample_identity_material_sha256")
    proof_path.write_text(json.dumps(proof))
    assert verifier.verify(tmp_path, oracle) == {
        "cases": 1,
        "current_preference_role_matches": 1,
        "pass": True,
        "rejected_experience_personalization_absent": 1,
    }

    proof["sample_key_sha256"] = digest
    proof["sample_key_source"] = "label_free_content_hash"
    proof["sample_identity_material_sha256"] = identity_material

    proof["typed_memories"].append(
        {
            "memory_role": "personalization",
            "content": (
                'study_preferences item distractor: {"epistemic_use":"not_factual_evidence",'
                '"memory_role":"personalization","value":"quiet rooms"}'
            ),
        }
    )
    proof_path.write_text(json.dumps(proof))
    assert verifier.verify(tmp_path, oracle) == {
        "cases": 1,
        "current_preference_role_matches": 0,
        "pass": False,
        "rejected_experience_personalization_absent": 1,
    }

    proof["typed_memories"][-1] = (
        {
            "memory_role": "personalization",
            "content": (
                'study_experience item rejection: {"rejected_option":'
                '"long video lectures"}'
            ),
        }
    )
    proof_path.write_text(json.dumps(proof))
    assert verifier.verify(tmp_path, oracle) == {
        "cases": 1,
        "current_preference_role_matches": 1,
        "pass": True,
        "rejected_experience_personalization_absent": 1,
    }

    proof["sample_identity_material_sha256"] = "0" * 64
    proof_path.write_text(json.dumps(proof))
    with pytest.raises(RuntimeError, match="packet identity proof mismatch"):
        verifier.verify(tmp_path, oracle)


def test_calibration_packet_verifier_accepts_all_frozen_personalized_use_values(
    tmp_path: Path,
) -> None:
    spec = importlib.util.spec_from_file_location("packet_verifier_multi", PACKET_VERIFIER)
    assert spec and spec.loader
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    sample_id = "personalized-use-multi"
    current = ["morning sessions", "quiet rooms", "practice drills"]
    rejected = ["evening sessions", "busy cafes", "long video lectures"]
    oracle = tmp_path / "oracle.jsonl"
    oracle.write_text(
        json.dumps(
            {
                "id": sample_id,
                "current_preference_value": current[0],
                "current_preference_values": current,
                "rejected_experience_value": rejected[0],
                "rejected_experience_values": rejected,
            }
        )
        + "\n"
    )
    case = {
        "id": sample_id,
        "question": "Which study plan should be recommended?",
        "dialogue": [
            {"role": "user", "content": "Morning practice drills in quiet rooms work."},
            {"role": "assistant", "content": "I will remember the successful setup."},
        ],
    }
    (tmp_path / "input.jsonl").write_text(json.dumps(case) + "\n")
    digest, identity_material, dialogue_sha256, question_sha256 = (
        verifier._label_free_identity(case)
    )
    proof = {
        "sample_key_sha256": digest,
        "sample_key_source": "label_free_content_hash",
        "sample_identity_material_sha256": identity_material,
        "dialogue_sha256": dialogue_sha256,
        "question_sha256": question_sha256,
        "typed_memories": [
            {
                "memory_role": "personalization",
                "content": json.dumps(
                    {
                        "epistemic_use": "not_factual_evidence",
                        "memory_role": "personalization",
                        "value": value,
                    }
                ),
            }
            for value in current
        ],
    }
    proof_path = tmp_path / "proof.json"
    proof_path.write_text(json.dumps(proof))

    assert verifier.verify(tmp_path, oracle) == {
        "cases": 1,
        "current_preference_role_matches": 1,
        "pass": True,
        "rejected_experience_personalization_absent": 1,
    }

    proof["typed_memories"].append(
        {
            "memory_role": "personalization",
            "content": json.dumps(
                {
                    "epistemic_use": "not_factual_evidence",
                    "memory_role": "personalization",
                    "value": rejected[2],
                }
            ),
        }
    )
    proof_path.write_text(json.dumps(proof))
    assert verifier.verify(tmp_path, oracle)["pass"] is False


def test_calibration_packet_verifier_accepts_row_durable_layout(
    tmp_path: Path,
) -> None:
    spec = importlib.util.spec_from_file_location("packet_verifier_rowwise", PACKET_VERIFIER)
    assert spec and spec.loader
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    oracle_rows = []
    for offset, (current, rejected) in enumerate(
        [("weekly view", "monthly view"), ("packing cubes", "folded stacks")]
    ):
        sample_id = f"rowwise-{offset}"
        case = {
            "id": sample_id,
            "question": "Which option should be recommended?",
            "dialogue": [
                {"role": "user", "content": f"I prefer {current}."},
                {"role": "assistant", "content": "I will remember that."},
            ],
        }
        row_dir = tmp_path / f"offset-{offset}-attempt-1"
        proof_dir = row_dir / "memory"
        proof_dir.mkdir(parents=True)
        (row_dir / "input.jsonl").write_text(json.dumps(case) + "\n")
        digest, identity_material, dialogue_sha256, question_sha256 = (
            verifier._label_free_identity(case)
        )
        (proof_dir / f"{digest}.json").write_text(
            json.dumps(
                {
                    "sample_key_sha256": digest,
                    "sample_key_source": "label_free_content_hash",
                    "sample_identity_material_sha256": identity_material,
                    "dialogue_sha256": dialogue_sha256,
                    "question_sha256": question_sha256,
                    "typed_memories": [
                        {
                            "memory_role": "personalization",
                            "content": json.dumps(
                                {
                                    "epistemic_use": "not_factual_evidence",
                                    "memory_role": "personalization",
                                    "value": current,
                                }
                            ),
                        }
                    ],
                }
            )
        )
        oracle_rows.append(
            {
                "id": sample_id,
                "current_preference_value": current,
                "rejected_experience_value": rejected,
            }
        )
    oracle = tmp_path / "oracle.jsonl"
    oracle.write_text("".join(json.dumps(row) + "\n" for row in oracle_rows))

    assert verifier.verify(tmp_path, oracle) == {
        "cases": 2,
        "current_preference_role_matches": 2,
        "pass": True,
        "rejected_experience_personalization_absent": 2,
    }


def test_objective_calibration_is_frozen_paired_and_oracle_separated() -> None:
    manifest = json.loads((OBJECTIVE_CALIBRATION / "manifest.json").read_text())
    domains = {}
    for split in ("development", "confirmation"):
        case_path = OBJECTIVE_CALIBRATION / f"{split}.jsonl"
        oracle_path = OBJECTIVE_CALIBRATION / f"{split}.oracle.jsonl"
        cases = [json.loads(line) for line in case_path.read_text().splitlines()]
        oracles = [json.loads(line) for line in oracle_path.read_text().splitlines()]
        spec = manifest["splits"][split]
        assert (spec["cases"], spec["families"]) == (12, 6)
        assert spec["case_sha256"] == sha256(case_path.read_bytes())
        assert spec["oracle_sha256"] == sha256(oracle_path.read_bytes())
        assert {case["query_id"] for case in cases} == {oracle["id"] for oracle in oracles}
        assert all(case["generated_question"]["objective_fact_basis"] for case in cases)
        assert all(case["applicability"] == "applicable" for case in cases)
        assert all("expected_applicability_scope" not in json.dumps(case) for case in cases)
        by_family = {}
        for case in cases:
            by_family.setdefault(case["metadata"]["topic"], []).append(
                case["generated_question"]["preference_answer"]
            )
        assert all(len(set(preferences)) == 2 for preferences in by_family.values())
        domains[split] = set(by_family)
    assert domains["development"].isdisjoint(domains["confirmation"])

    spec = importlib.util.spec_from_file_location("overlap_auditor", OVERLAP_AUDITOR)
    assert spec and spec.loader
    auditor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(auditor)
    assert auditor.normalized_text(cases[0])


def test_valid_selection_calibration_is_frozen_paired_and_oracle_separated() -> None:
    manifest = json.loads((VALID_SELECTION_CALIBRATION / "manifest.json").read_text())
    domains = {}
    expected_hashes = {
        "development": (
            "b21950c859a416fece7a2c20cd0a71b4d65bbc064a5b08777dbad12c78a09e37",
            "ff661608b60998b05ba8387798630cce4444b77f792c68a5110dd09604e2910f",
        ),
        "confirmation": (
            "3719ef43982414399ef3a87d3c8a72ba59362ce194f92dd124256da838790041",
            "70d74b84b2efa4298a29728d1f1702bf3fa0ad76223be7ebb8d6b6808ca7f892",
        ),
        "confirmation_v2": (
            "08cc2aee1a7d48e6447c03145b0a63292f5b6abcb9fcb727c49427b50d638516",
            "b23a65208d82f2efd5638b021b709d8b9e3f046d20f0556b93479d3c56bcf42e",
        ),
        "confirmation_v3": (
            "fec6d233b5d43c84014303e473c937c3bd91a49d772c5f527a7cbd0be880a3d2",
            "7623acb0636cf64426ed580cadd5a3a12ce8284c0e3ec519686fcb78e1f1d9cf",
        ),
    }
    for split, frozen_hashes in expected_hashes.items():
        case_path = VALID_SELECTION_CALIBRATION / f"{split}.jsonl"
        oracle_path = VALID_SELECTION_CALIBRATION / f"{split}.oracle.jsonl"
        cases = [json.loads(line) for line in case_path.read_text().splitlines()]
        oracles = [json.loads(line) for line in oracle_path.read_text().splitlines()]
        assert (len(cases), len(oracles)) == (12, 12)
        assert manifest["splits"][split]["case_sha256"] == sha256(case_path.read_bytes())
        assert manifest["splits"][split]["oracle_sha256"] == sha256(oracle_path.read_bytes())
        assert (sha256(case_path.read_bytes()), sha256(oracle_path.read_bytes())) == frozen_hashes
        assert {case["id"] for case in cases} == {oracle["id"] for oracle in oracles}
        assert all([item["status"] for item in case["memory"]["items"]] == ["outdated", "current"] for case in cases)
        domains[split] = {case["metadata"]["topic"] for case in cases}
    assert all(
        domains[left].isdisjoint(domains[right])
        for index, left in enumerate(domains)
        for right in list(domains)[index + 1:]
    )
    assert domains["confirmation_v3"] == {
        "tea",
        "event_seating",
        "study_session",
        "museum_visit",
        "running_route",
        "notification_delivery",
    }


def test_personalized_use_calibration_is_frozen_paired_and_oracle_separated() -> None:
    manifest = json.loads(
        (PERSONALIZED_USE_CALIBRATION / "manifest.json").read_text()
    )
    domains = {}
    expected_hashes = {
        "development": (
            "730256f82b44ab2263d6bb13930b4b3b0b3e6b12a5a49911e057f8c3a575b0b8",
            "69b8df49606cb92875f1e90b163ebe49d9715970a538e9e82833b2beb01d8e9d",
        ),
        "confirmation": (
            "56360a463df20a675d1516dad581b2e62fa95f38ce524fc5aa03a325d061cd5d",
            "7b82fb8687288f4881e5e700457a272e13282f42d80768424584145f329572a0",
        ),
        "development_v2": (
            "de885ef4440b83aed8f0647851b9f9c404ce93ce7da609d126ac74f57c1e6b57",
            "5533b613d59ed689dacafbba2aac22ec1ee1b0f8a2a2cadf1d5e5cb31e93e5f8",
        ),
        "confirmation_v2": (
            "68b7d9205d7c4be0406b03d4f8ac42b34d84b09dc28f8c792df3fcdd56b48567",
            "dbce8adce5072caa43a4cc8271ba70d95f2f1112498916c578c5f7c5454c9337",
        ),
        "confirmation_v3": (
            "97c04399c4682b8de8e02e6f51a79bceae0d527e5d817fba76579f7283bda204",
            "f3bb5d1d548b7dd78fc0faa50616a164989ace703cb798d1cb2cb27b54758b37",
        ),
        "confirmation_v4": (
            "b50d78aef906044dc88f263870365dc2ebfc77927aaf30b7f53d313f8c57f717",
            "5af32237d44f136be56e773f4985dc435b56623e9613bbc9dd9ee6fc18c22d9d",
        ),
        "confirmation_v5": (
            "b732b396f9896afae2ec97889aa0f0c388a52fe30427f6f1383ccc1055391e96",
            "d22eda82f48e7b40a6952186d0950ad882a7f07394a048622864fbe6bc45e6c7",
        ),
        "confirmation_v6": (
            "209e61a95bd174e088aa1bff52810fdd857bc816623d3746906a73df0d0c4b74",
            "ba67ab9535831b14935237a0884c98bbc563b6fc2b42346f6d00ff65512cf8b6",
        ),
        "confirmation_v7": (
            "85a384a8c42378d82b6f155f818a3276ed80e22dc54b6a59857f618adbc9fe13",
            "8dada2bd5d94fffc597c58b914c7feeeda5014e5f7374bd8eeb55822008a1667",
        ),
        "confirmation_v8": (
            "0a48917c298db6ae2f3dd2def373ed09838615db6b2ce332acb813bd7c2553d3",
            "bbefbf0a6ba4b7d96dade2e888b89a7697067f94a380d5855793799633a495c1",
        ),
        "confirmation_v9": (
            "11ba0024094421bc8c4f15fa4fb5c70ee6be2505b5424060f62820728f0251a2",
            "f6ec7ce5faf28335870744792d715d6d52fbd1f39909a9e033c7e2f8678b85d1",
        ),
        "confirmation_v10": (
            "a94fa112a33d6b785f4b78e8d93eb354cb5233fe4064d6d75f4ee9bd88118da6",
            "722e0146280f62e1359219532a8b14358ac736286960c8647119e70fd4cbb018",
        ),
        "confirmation_v11": (
            "b14576e019bdbd53a3755fb95f00d777697a64bccb71a5ef44c0d844eaaa6106",
            "c5f8a7995a6f88d13eba2340957736342c4289d143c2258f584ab3948fa4b3e7",
        ),
        "confirmation_v12": (
            "fb66548e9e907ff8a824f67f6068eb937ba2623f032543efc7efce4e43e6a876",
            "798efa0afd39dc9f0ddd4d7a8572c167829cc1324946181c9f98b668361579a3",
        ),
        "confirmation_v13": (
            "c5d9c9b0ce76b8da66fa1f1eee5948662b91121c7e8e8825a8faf548a63c59fb",
            "e5d62ea275ea1f3cb0a0142a773e062c246891f324704d403ca745ee05cf6693",
        ),
    }
    for split in expected_hashes:
        case_path = PERSONALIZED_USE_CALIBRATION / f"{split}.jsonl"
        oracle_path = PERSONALIZED_USE_CALIBRATION / f"{split}.oracle.jsonl"
        cases = [json.loads(line) for line in case_path.read_text().splitlines()]
        oracles = [json.loads(line) for line in oracle_path.read_text().splitlines()]
        split_manifest = manifest["splits"][split]

        assert (len(cases), len(oracles)) == (12, 12)
        assert (split_manifest["cases"], split_manifest["families"]) == (12, 6)
        assert split_manifest["case_sha256"] == sha256(case_path.read_bytes())
        assert split_manifest["oracle_sha256"] == sha256(oracle_path.read_bytes())
        assert (
            split_manifest["case_sha256"],
            split_manifest["oracle_sha256"],
        ) == expected_hashes[split]
        assert {case["id"] for case in cases} == {oracle["id"] for oracle in oracles}
        assert {case["metadata"]["subtype"] for case in cases} == {
            "direct_preference_aligned_recommendation",
            "fine_grained_preference_recommendation",
        }
        assert all(case["task"] == "personalized_memory_use" for case in cases)
        assert all(case["memory"]["policy"] == "use" for case in cases)
        assert all(len(case["memory"]["items"]) == 1 for case in cases)
        for family in {oracle["family"] for oracle in oracles}:
            twins = sorted(
                (oracle for oracle in oracles if oracle["family"] == family),
                key=lambda oracle: oracle["twin"],
            )
            assert len(twins) == 2
            assert twins[0]["current_preference_value"] == twins[1][
                "rejected_experience_value"
            ]
            assert twins[0]["rejected_experience_value"] == twins[1][
                "current_preference_value"
            ]
        for case, oracle in zip(cases, oracles, strict=True):
            user_text = " ".join(
                turn["content"]
                for turn in case["dialogue"]
                if turn["role"] == "user"
            )
            assert oracle["current_preference_value"] in user_text
            assert oracle["rejected_experience_value"] in user_text
            assert "did not work for me" in user_text
            assert oracle["current_preference_value"] in case["evaluation"][
                "reference_answer"
            ]
            assert oracle["rejected_experience_value"] not in case["memory"][
                "items"
            ][0]["content"]
            first_user_turn = next(
                turn["content"]
                for turn in case["dialogue"]
                if turn["role"] == "user"
            )
            if split in {
                "development_v2",
                "confirmation_v2",
                "confirmation_v3",
                "confirmation_v4",
                "confirmation_v5",
                "confirmation_v6",
                "confirmation_v7",
                "confirmation_v8",
                "confirmation_v9",
                "confirmation_v10",
                "confirmation_v11",
                "confirmation_v12",
                "confirmation_v13",
            }:
                assert first_user_turn.startswith("I prefer ")
            else:
                assert first_user_turn.startswith("For my next ")
        domains[split] = {case["metadata"]["topic"] for case in cases}

    assert all(
        domains[left].isdisjoint(domains[right])
        for index, left in enumerate(domains)
        for right in list(domains)[index + 1 :]
    )
    assert domains["confirmation_v3"] == {
        "desk_storage",
        "grocery_schedule",
        "photo_capture",
        "podcast_episode",
        "room_temperature",
        "writing_instrument",
    }
    assert domains["confirmation_v4"] == {
        "audiobook_speed",
        "calendar_view",
        "laundry_timing",
        "lunch_setting",
        "packing_style",
        "plant_watering",
    }
    assert domains["confirmation_v5"] == {
        "bathroom_lighting",
        "dish_drying",
        "mail_handling",
        "morning_alarm",
        "shoe_storage",
        "water_bottle",
    }
    assert domains["confirmation_v6"] == {
        "blanket_storage",
        "charging_location",
        "curtain_style",
        "key_storage",
        "pantry_labels",
        "reading_marker",
    }
    assert domains["confirmation_v7"] == {
        "candle_style",
        "freezer_organization",
        "grocery_carrier",
        "recipe_display",
        "towel_arrangement",
        "umbrella_storage",
    }
    assert domains["confirmation_v8"] == {
        "book_shelving",
        "cord_storage",
        "hand_soap",
        "lunch_container",
        "shoe_lacing",
        "window_ventilation",
    }
    assert domains["confirmation_v9"] == {
        "clipboard_style",
        "doorstop_style",
        "ice_tray",
        "lamp_switch",
        "produce_storage",
        "sock_sorting",
    }
    assert domains["confirmation_v10"] == {
        "coaster_material",
        "eyeglass_case",
        "hanger_style",
        "shower_caddy",
        "tape_dispenser",
        "trivet_material",
    }
    assert domains["confirmation_v11"] == {
        "lint_removal",
        "napkin_storage",
        "oven_mitt_style",
        "receipt_storage",
        "soap_dish",
        "shoehorn_style",
    }
    assert domains["confirmation_v12"] == {
        "broom_style",
        "cutting_board_material",
        "dustpan_style",
        "measuring_cup_material",
        "plant_saucer_material",
        "toothbrush_holder",
    }
    assert domains["confirmation_v13"] == {
        "bath_mat_material",
        "can_opener_style",
        "colander_material",
        "flashlight_style",
        "food_storage_cover",
        "laundry_hamper_style",
    }


def test_personalized_use_confirmation_v8_reserves_new_twin_families() -> None:
    cases = [
        json.loads(line)
        for line in (
            PERSONALIZED_USE_CALIBRATION / "confirmation_v8.jsonl"
        ).read_text().splitlines()
    ]
    oracles = [
        json.loads(line)
        for line in (
            PERSONALIZED_USE_CALIBRATION / "confirmation_v8.oracle.jsonl"
        ).read_text().splitlines()
    ]

    assert (len(cases), len(oracles)) == (12, 12)
    assert {case["metadata"]["topic"] for case in cases} == {
        "book_shelving",
        "cord_storage",
        "hand_soap",
        "lunch_container",
        "shoe_lacing",
        "window_ventilation",
    }
    assert {case["id"] for case in cases} == {oracle["id"] for oracle in oracles}


def test_personalized_use_confirmation_v9_reserves_new_twin_families() -> None:
    cases = [
        json.loads(line)
        for line in (
            PERSONALIZED_USE_CALIBRATION / "confirmation_v9.jsonl"
        ).read_text().splitlines()
    ]
    oracles = [
        json.loads(line)
        for line in (
            PERSONALIZED_USE_CALIBRATION / "confirmation_v9.oracle.jsonl"
        ).read_text().splitlines()
    ]

    assert (len(cases), len(oracles)) == (12, 12)
    assert {case["metadata"]["topic"] for case in cases} == {
        "doorstop_style",
        "ice_tray",
        "lamp_switch",
        "produce_storage",
        "sock_sorting",
        "clipboard_style",
    }
    assert {case["id"] for case in cases} == {oracle["id"] for oracle in oracles}


def test_personalized_use_confirmation_v10_reserves_new_twin_families() -> None:
    cases = [
        json.loads(line)
        for line in (
            PERSONALIZED_USE_CALIBRATION / "confirmation_v10.jsonl"
        ).read_text().splitlines()
    ]
    oracles = [
        json.loads(line)
        for line in (
            PERSONALIZED_USE_CALIBRATION / "confirmation_v10.oracle.jsonl"
        ).read_text().splitlines()
    ]

    assert (len(cases), len(oracles)) == (12, 12)
    assert {case["metadata"]["topic"] for case in cases} == {
        "coaster_material",
        "eyeglass_case",
        "hanger_style",
        "shower_caddy",
        "tape_dispenser",
        "trivet_material",
    }
    assert {case["id"] for case in cases} == {oracle["id"] for oracle in oracles}


def test_personalized_use_confirmation_v11_reserves_new_twin_families() -> None:
    cases = [
        json.loads(line)
        for line in (
            PERSONALIZED_USE_CALIBRATION / "confirmation_v11.jsonl"
        ).read_text().splitlines()
    ]
    oracles = [
        json.loads(line)
        for line in (
            PERSONALIZED_USE_CALIBRATION / "confirmation_v11.oracle.jsonl"
        ).read_text().splitlines()
    ]

    assert (len(cases), len(oracles)) == (12, 12)
    assert {case["metadata"]["topic"] for case in cases} == {
        "lint_removal",
        "napkin_storage",
        "oven_mitt_style",
        "receipt_storage",
        "soap_dish",
        "shoehorn_style",
    }
    assert {case["id"] for case in cases} == {oracle["id"] for oracle in oracles}


def test_personalized_use_confirmation_v12_reserves_new_twin_families() -> None:
    cases = [
        json.loads(line)
        for line in (
            PERSONALIZED_USE_CALIBRATION / "confirmation_v12.jsonl"
        ).read_text().splitlines()
    ]
    oracles = [
        json.loads(line)
        for line in (
            PERSONALIZED_USE_CALIBRATION / "confirmation_v12.oracle.jsonl"
        ).read_text().splitlines()
    ]

    assert (len(cases), len(oracles)) == (12, 12)
    assert {case["metadata"]["topic"] for case in cases} == {
        "broom_style",
        "cutting_board_material",
        "dustpan_style",
        "measuring_cup_material",
        "plant_saucer_material",
        "toothbrush_holder",
    }
    assert {case["id"] for case in cases} == {oracle["id"] for oracle in oracles}


def test_personalized_use_confirmation_v13_reserves_new_twin_families() -> None:
    cases = [
        json.loads(line)
        for line in (
            PERSONALIZED_USE_CALIBRATION / "confirmation_v13.jsonl"
        ).read_text().splitlines()
    ]
    oracles = [
        json.loads(line)
        for line in (
            PERSONALIZED_USE_CALIBRATION / "confirmation_v13.oracle.jsonl"
        ).read_text().splitlines()
    ]

    assert (len(cases), len(oracles)) == (12, 12)
    assert {case["metadata"]["topic"] for case in cases} == {
        "bath_mat_material",
        "can_opener_style",
        "colander_material",
        "flashlight_style",
        "food_storage_cover",
        "laundry_hamper_style",
    }
    assert {case["id"] for case in cases} == {oracle["id"] for oracle in oracles}


def test_personalized_use_shadow_v1_is_reusable_full_300_development_data() -> None:
    manifest = json.loads(
        (PERSONALIZED_USE_CALIBRATION / "manifest.json").read_text()
    )
    split = manifest["splits"]["shadow_v1"]
    case_path = PERSONALIZED_USE_CALIBRATION / "shadow_v1.jsonl"
    oracle_path = PERSONALIZED_USE_CALIBRATION / "shadow_v1.oracle.jsonl"
    cases = [json.loads(line) for line in case_path.read_text().splitlines()]
    oracles = [json.loads(line) for line in oracle_path.read_text().splitlines()]

    assert (len(cases), len(oracles)) == (300, 300)
    assert (split["cases"], split["families"]) == (300, 150)
    assert split["purpose"] == "reusable_development_stress"
    assert split["generator_seed"] == 20260717
    assert split["reuse_policy"] == {
        "claim_eligible": False,
        "reusable_for": [
            "memphant",
            "raw_dialogue",
            "episode_only",
            "one_lever_at_a_time_ablations",
        ],
        "sealed_confirmation_eligible": False,
    }
    assert split["case_sha256"] == sha256(case_path.read_bytes())
    assert split["oracle_sha256"] == sha256(oracle_path.read_bytes())
    assert (split["case_sha256"], split["oracle_sha256"]) == (
        "088773577d7fb44583ba6f203134aebadd9c92cd86327b986bde909a538f3db4",
        "6cc27abd7077637938550e6422bc190cc554886a879a92d14afb08ac8445d17a",
    )
    assert {case["id"] for case in cases} == {oracle["id"] for oracle in oracles}
    assert len({oracle["family"] for oracle in oracles}) == 150
    assert {oracle["stress_profile"] for oracle in oracles} == {
        "crowded_early",
        "crowded_middle",
        "crowded_late",
    }
    assert all(oracle["reusable_development"] is True for oracle in oracles)
    assert all(len(case["dialogue"]) >= 12 for case in cases)
    assert all(len(case["memory"]["items"]) == 1 for case in cases)
    assert {
        case["metadata"]["subtype"] for case in cases
    } == {
        "direct_preference_aligned_recommendation",
        "fine_grained_preference_recommendation",
    }
    prior_topics = {
        json.loads(line)["metadata"]["topic"]
        for prior in (
            "development",
            "confirmation",
            "development_v2",
            "confirmation_v2",
            "confirmation_v3",
            "confirmation_v4",
            "confirmation_v5",
        )
        for line in (
            PERSONALIZED_USE_CALIBRATION / f"{prior}.jsonl"
        ).read_text().splitlines()
    }
    assert prior_topics.isdisjoint(
        {case["metadata"]["topic"] for case in cases}
    )


def test_personalized_use_shadow_v1b_reuses_rows_with_official_scale_pressure() -> None:
    manifest = json.loads(
        (PERSONALIZED_USE_CALIBRATION / "manifest.json").read_text()
    )
    split = manifest["splits"]["shadow_v1b"]
    cases = [
        json.loads(line)
        for line in (
            PERSONALIZED_USE_CALIBRATION / "shadow_v1b.jsonl"
        ).read_text().splitlines()
    ]
    oracles = [
        json.loads(line)
        for line in (
            PERSONALIZED_USE_CALIBRATION / "shadow_v1b.oracle.jsonl"
        ).read_text().splitlines()
    ]
    parents = [
        json.loads(line)
        for line in (
            PERSONALIZED_USE_CALIBRATION / "shadow_v1.jsonl"
        ).read_text().splitlines()
    ]

    assert (len(cases), len(oracles)) == (300, 300)
    assert (split["case_sha256"], split["oracle_sha256"]) == (
        "6abfb459370737a143b125b2acaddd3778041bd460fad964491dddfd647dd638",
        "784f8c6f2bc818d4eec637865f0e8de809a3ab6f1f6a4740ae42c8bbf93d51e2",
    )
    assert split["derived_from"] == {
        "case_sha256": "088773577d7fb44583ba6f203134aebadd9c92cd86327b986bde909a538f3db4",
        "oracle_sha256": "6cc27abd7077637938550e6422bc190cc554886a879a92d14afb08ac8445d17a",
        "split": "shadow_v1",
    }
    assert split["semantic_rows_reused"] == 300
    assert {oracle["parent_id"] for oracle in oracles} == {
        case["id"] for case in parents
    }
    assert {case["metadata"]["topic"] for case in cases} == {
        case["metadata"]["topic"] for case in parents
    }
    assert sum(
        case["metadata"]["subtype"] == "direct_preference_aligned_recommendation"
        for case in cases
    ) == 107
    assert sum(
        case["metadata"]["subtype"] == "fine_grained_preference_recommendation"
        for case in cases
    ) == 193
    assert all(
        sum(len(turn["content"]) for turn in case["dialogue"]) >= 4_000
        for case in cases
    )
    for case, oracle in zip(cases, oracles, strict=True):
        expected = 1 if case["metadata"]["subtype"].startswith("direct_") else 3
        assert len(case["memory"]["items"]) == expected
        assert len(oracle["current_preference_values"]) == expected
        assert len(oracle["rejected_experience_values"]) == expected
        assert oracle["current_preference_value"] == oracle[
            "current_preference_values"
        ][0]
        assert oracle["rejected_experience_value"] == oracle[
            "rejected_experience_values"
        ][0]


def test_valid_selection_multi_operation_splits_are_full_and_independent() -> None:
    manifest = json.loads((VALID_SELECTION_CALIBRATION / "manifest.json").read_text())
    seen_topics = {
        topic
        for split in ("development", "confirmation", "confirmation_v2", "confirmation_v3")
        for line in (VALID_SELECTION_CALIBRATION / f"{split}.jsonl").read_text().splitlines()
        for topic in [json.loads(line)["metadata"]["topic"]]
    }
    retired_hashes = {
        "confirmation_v4": (
            "339e24339867ac3e90cb2b86fbdd949d3cd7c4866a207f593050e00519e24eaa",
            "e3a1e5e6c184ba514f0ffc867381455217c76de92cba3a70f62c0674d582e018",
        ),
        "confirmation_v5": (
            "f570ab108899cad6f9bc3295100f80f4c27e6323fc78470732e697fa3a5ae804",
            "4ef96a51ebd5dbc7c5be03235b91328ee41b1f805cbe2619fab1f540793c049b",
        ),
    }
    for split, hashes in retired_hashes.items():
        retired_case_path = VALID_SELECTION_CALIBRATION / f"{split}.jsonl"
        retired_oracle_path = VALID_SELECTION_CALIBRATION / f"{split}.oracle.jsonl"
        assert (
            sha256(retired_case_path.read_bytes()),
            sha256(retired_oracle_path.read_bytes()),
        ) == hashes
        assert manifest["splits"][split]["retired"] is True
        seen_topics |= {
            topic
            for line in retired_case_path.read_text().splitlines()
            for topic in json.loads(line)["metadata"]["topics"]
        }
    expected_hashes = {
        "multi_operation_development": (
            "c9acacbe640e68ade03fd88e07efdf0f7e920877adac982a9c3f716d30f9ed5e",
            "cbc3df9115ae91993de06cc8e1c6329d25c6712dc799e1b20bc6d9b55eb250d2",
        ),
        "confirmation_v6": (
            "a4f6bb4bdbad5b6f53ba92c63d2976a77fcb0b0838b026fa7bbf76dccda5ec29",
            "4586ee81e1f6b4ceadfaa9b17acfc277b22f69e8dd21ed500885142ada55df31",
        ),
    }
    for split in ("multi_operation_development", "confirmation_v6"):
        case_path = VALID_SELECTION_CALIBRATION / f"{split}.jsonl"
        oracle_path = VALID_SELECTION_CALIBRATION / f"{split}.oracle.jsonl"
        cases = [json.loads(line) for line in case_path.read_text().splitlines()]
        oracles = [json.loads(line) for line in oracle_path.read_text().splitlines()]

        assert (len(cases), len(oracles)) == (12, 12)
        assert manifest["splits"][split]["case_sha256"] == sha256(case_path.read_bytes())
        assert manifest["splits"][split]["oracle_sha256"] == sha256(oracle_path.read_bytes())
        assert (sha256(case_path.read_bytes()), sha256(oracle_path.read_bytes())) == expected_hashes[split]
        assert {case["id"] for case in cases} == {oracle["id"] for oracle in oracles}
        assert all(len(oracle["additional_current_values"]) == 2 for oracle in oracles)
        assert all(
            sum("I prefer " in turn["content"] or "I now prefer " in turn["content"]
                for turn in case["dialogue"] if turn["role"] == "user")
            == 4
            for case in cases
        )
        for case, oracle in zip(cases, oracles, strict=True):
            user_turns = [
                turn["content"] for turn in case["dialogue"] if turn["role"] == "user"
            ]
            assert oracle["current_value"] in user_turns[1]
            assert oracle["additional_current_values"][0] in user_turns[2]
            assert oracle["additional_current_values"][1] in user_turns[3]
            current_memory_items = [
                item["content"]
                for item in case["memory"]["items"]
                if item["status"] == "current"
            ]
            assert oracle["current_value"] in current_memory_items[0]
        split_topics = {
            topic for case in cases for topic in case["metadata"]["topics"]
        }
        assert len(split_topics) == 18
        assert seen_topics.isdisjoint(split_topics)
        seen_topics |= split_topics


def test_calibration_overlap_audit_fails_on_fivegram_overlap_alone() -> None:
    spec = importlib.util.spec_from_file_location("overlap_auditor", OVERLAP_AUDITOR)
    assert spec and spec.loader
    auditor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(auditor)
    result = auditor.audit(
        [{"question": "alpha beta gamma delta epsilon one two three four five"}],
        [{"question": "six seven alpha beta gamma delta epsilon eight nine ten"}],
    )
    assert result == {
        "calibration_rows": 1,
        "official_rows": 1,
        "exact_normalized_hash_matches": 0,
        "normalized_fivegram_overlap_count": 1,
        "suspicious_row_matches": 0,
        "pass": False,
    }


def load_runner():
    spec = importlib.util.spec_from_file_location("run_restraint_bench", RUNNER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runner_binary_path_honors_cargo_target_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runner = load_runner()
    monkeypatch.setenv("CARGO_TARGET_DIR", str(tmp_path))
    assert runner.cargo_binary_path("memphant-server") == tmp_path / "debug/memphant-server"


def test_official_uid_partition_rejects_missing_duplicate_and_order_gaps() -> None:
    runner = load_runner()
    expected = [f"v11_{index:06d}" for index in range(4)]
    rows = [
        {"uid": uid, "offset": index, "accuracy": 1, "contamination": 0}
        for index, uid in enumerate(expected)
    ]
    assert runner.validate_official_uid_partition(rows, expected) == rows
    with pytest.raises(RuntimeError, match="duplicate UID"):
        runner.validate_official_uid_partition(rows + [rows[-1]], expected)
    with pytest.raises(RuntimeError, match="UID partition"):
        runner.validate_official_uid_partition(rows[:-1], expected)
    with pytest.raises(RuntimeError, match="offset partition"):
        runner.validate_official_uid_partition(
            rows[:2]
            + [rows[2] | {"offset": 3}, rows[3] | {"offset": 2}],
            expected,
        )


def test_official_configuration_rejects_cross_arm_drift() -> None:
    runner = load_runner()
    memphant = {
        "answer_model": "answer",
        "judge_model": "judge",
        "embed_model": "fastembed:bge-m3",
        "top_k": 10,
        "source_sha256": "a" * 64,
        "answer_base_url": "https://openrouter.ai/api/v1",
        "judge_base_url": "https://openrouter.ai/api/v1",
        "provider_policy_sha256": "b" * 64,
    }
    raw = dict(memphant)
    assert runner.validate_official_configuration([memphant], [raw]) == memphant
    raw["top_k"] = 9
    with pytest.raises(RuntimeError, match="configuration drift"):
        runner.validate_official_configuration([memphant], [raw])


def test_official_response_ids_are_unique_across_shards_and_arms() -> None:
    runner = load_runner()
    assert runner.validate_unique_response_ids([["a", "b"], ["c"]]) == 3
    with pytest.raises(RuntimeError, match="duplicate response IDs"):
        runner.validate_unique_response_ids([["a", "b"], ["b"]])


def test_official_recovery_allows_only_infrastructure_and_never_recalls_completed() -> None:
    runner = load_runner()
    recovery = {
        "failure_class": "infrastructure",
        "failed_base": "shard-000",
        "first_incomplete_offset": 7,
        "completed_offsets": list(range(7)),
        "recovery_offset": 7,
        "failure": {"http_status": 429},
    }
    assert runner.validate_official_recovery(recovery) == recovery
    with pytest.raises(RuntimeError, match="product failure"):
        runner.validate_official_recovery(recovery | {"failure_class": "product"})
    with pytest.raises(RuntimeError, match="completed-row recall"):
        runner.validate_official_recovery(recovery | {"recovery_offset": 6})


def test_official_bootstrap_is_deterministic_and_paired() -> None:
    runner = load_runner()
    memphant = [
        {"uid": str(index), "accuracy": int(index % 3 != 0), "contamination": 0}
        for index in range(20)
    ]
    raw = [
        {"uid": str(index), "accuracy": int(index % 2 == 0), "contamination": 1}
        for index in range(20)
    ]
    first = runner.score_paired_rows(memphant, raw, resamples=10_000, seed=20260716)
    second = runner.score_paired_rows(memphant, raw, resamples=10_000, seed=20260716)
    assert first == second
    assert first["paired_accuracy_delta"]["point"] == pytest.approx(0.15)
    assert first["paired_contamination_delta"]["point"] == pytest.approx(-1.0)


def test_official_gate_uses_strict_ci_boundaries_and_fixed_clean_exclusion() -> None:
    runner = load_runner()
    thresholds = {
        "accuracy_point_min": 0.8129,
        "accuracy_lower_bound_gt": 0.7829,
        "contamination_point_max": 0.1334,
        "contamination_upper_bound_lt": 0.1634,
        "paired_accuracy_lower_bound_gt": 0.0,
        "paired_contamination_upper_bound_lt": 0.0,
        "clean_accuracy_point_gt": 0.7829,
        "clean_contamination_point_lt": 0.1634,
    }
    score = {
        "memphant": {
            "accuracy": {"point": 0.8129, "lower": 0.7829, "upper": 0.9},
            "contamination": {"point": 0.1334, "lower": 0.1, "upper": 0.1634},
        },
        "paired_accuracy_delta": {"lower": 0.0},
        "paired_contamination_delta": {"upper": 0.0},
    }
    clean = {"accuracy": 0.8, "contamination": 0.1}
    result = runner.evaluate_official_gate(score, clean, thresholds)
    assert result["pass"] is False
    assert result["checks"]["accuracy_point"] is True
    assert result["checks"]["accuracy_lower_bound"] is False

    rows = [{"uid": "keep"}, {"uid": "v11_000321"}]
    assert runner.clean_official_rows(rows, "v11_000321") == [rows[0]]
    with pytest.raises(RuntimeError, match="clean exclusion"):
        runner.clean_official_rows(rows, "wrong")


def test_pmu_official_contract_requires_one_complete_300_row_base_run() -> None:
    runner = load_runner()
    spec = runner.official_task_spec("personalized_memory_use")

    assert spec == {
        "task": "personalized_memory_use",
        "source_file": "personalized_memory_use.jsonl",
        "rows": 300,
        "clean_exclusion": "v11_001093",
        "base_runs": [{"offset": 0, "sample_count": 300}],
        "metrics": {
            "accuracy": "answer_accuracy",
            "preference_use": "preference_used",
        },
        "primary_name": "full-300",
        "sensitivity_name": "clean-299",
    }
    base_runs = [{"directory": "full", "offset": 0, "sample_count": 300}]
    assert runner.validate_official_base_runs(base_runs, spec) == base_runs
    with pytest.raises(RuntimeError, match="predeclared base runs"):
        runner.validate_official_base_runs(
            [
                {"directory": "first", "offset": 0, "sample_count": 150},
                {"directory": "second", "offset": 150, "sample_count": 150},
            ],
            spec,
        )

    result = {
        "id": "v11_001093",
        "with_memory": {
            "judge": {
                "answer_accuracy": 1,
                "preference_used": 0,
            }
        },
    }
    assert runner.official_metric_row(result, offset=0, task_spec=spec) == {
        "uid": "v11_001093",
        "offset": 0,
        "accuracy": 1,
        "preference_use": 0,
    }
    with pytest.raises(RuntimeError, match="judge metric is not binary"):
        runner.official_metric_row(
            result | {"with_memory": {"judge": {"answer_accuracy": 1}}},
            offset=0,
            task_spec=spec,
        )


def test_pmu_official_bootstrap_and_gate_use_two_higher_is_better_metrics() -> None:
    runner = load_runner()
    memphant = [
        {
            "uid": str(index),
            "accuracy": int(index % 4 != 0),
            "preference_use": int(index % 5 != 0),
        }
        for index in range(20)
    ]
    raw = [
        {
            "uid": str(index),
            "accuracy": int(index % 2 == 0),
            "preference_use": int(index % 3 == 0),
        }
        for index in range(20)
    ]
    first = runner.score_paired_pmu_rows(
        memphant, raw, resamples=10_000, seed=20260716
    )
    second = runner.score_paired_pmu_rows(
        memphant, raw, resamples=10_000, seed=20260716
    )
    assert first == second
    assert first["paired_accuracy_delta"]["point"] == pytest.approx(0.25)
    assert first["paired_preference_use_delta"]["point"] == pytest.approx(0.45)

    thresholds = {
        "accuracy_point_min": 0.6334,
        "accuracy_lower_bound_gt": 0.6034,
        "preference_use_point_min": 0.8233,
        "preference_use_lower_bound_gt": 0.7933,
        "paired_accuracy_lower_bound_gt": 0.0,
        "paired_preference_use_lower_bound_gt": 0.0,
        "clean_accuracy_point_gt": 0.6034,
        "clean_preference_use_point_gt": 0.7933,
    }
    boundary_score = {
        "memphant": {
            "accuracy": {"point": 0.6334, "lower": 0.6034, "upper": 0.8},
            "preference_use": {"point": 0.8233, "lower": 0.7933, "upper": 0.9},
        },
        "paired_accuracy_delta": {"lower": 0.0},
        "paired_preference_use_delta": {"lower": 0.0},
    }
    clean = {"accuracy": 0.7, "preference_use": 0.85}
    gate = runner.evaluate_pmu_official_gate(boundary_score, clean, thresholds)
    assert gate["pass"] is False
    assert gate["checks"]["accuracy_point"] is True
    assert gate["checks"]["accuracy_lower_bound"] is False


def load_adapter(monkeypatch: pytest.MonkeyPatch):
    package = types.ModuleType("baselines")
    package.__path__ = []
    base = types.ModuleType("baselines.base")
    common = types.ModuleType("baselines.common")

    @dataclass(frozen=True)
    class BaselineContext:
        context_text: str
        retrieved_memories: list[dict]
        user_id: str
        save_dir: str
        method: str
        top_k: int

    base.BaselineContext = BaselineContext
    common.format_retrieved_memories = lambda rows: "\n\n".join(
        row["used_content"] for row in rows
    ) if rows else "[NO RETRIEVED MEMORIES]"
    common.parse_dialogue_to_messages = lambda text: [
        {
            "role": "user",
            "content": text.split(":", 1)[1].strip() if ":" in text else text,
        }
    ]
    monkeypatch.setitem(__import__("sys").modules, "baselines", package)
    monkeypatch.setitem(__import__("sys").modules, "baselines.base", base)
    monkeypatch.setitem(__import__("sys").modules, "baselines.common", common)
    spec = importlib.util.spec_from_file_location("memsyco_memphant_baseline", ADAPTER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_memphant_answer_context_aggregates_complete_active_personalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = load_adapter(monkeypatch)
    memories = [
        {
            "memory_role": "personalization",
            "epistemic_use": "not_factual_evidence",
            "used_content": "typed preference: detailed preparation",
        },
        {
            "memory_role": "personalization",
            "epistemic_use": "not_factual_evidence",
            "used_content": "typed preference: a novel plan",
        },
        {
            "memory_role": "personalization",
            "epistemic_use": "not_factual_evidence",
            "used_content": "typed preference: a continuous session",
        },
        {
            "memory_role": "conversation_evidence",
            "epistemic_use": "quoted_conversation_context",
            "used_content": "episodic excerpt mentions only two preferences",
        },
    ]

    context = adapter._answer_context(memories)
    active, evidence = context.split("### Retrieved evidence", 1)

    assert "3 retrieved entries" in active
    assert "detailed preparation" in active
    assert "a novel plan" in active
    assert "a continuous session" in active
    assert "every entry whose applicability scope matches" in active
    assert "one combined recommendation, not ranked alternatives" in active
    assert "include every matching value" in active
    assert "list position, mention order, or invented recency or priority" in active
    assert "incomplete conversational excerpts" in active.lower()
    assert "episodic excerpt" not in active
    assert "episodic excerpt" in evidence


def load_bootstrap():
    spec = importlib.util.spec_from_file_location("memsyco_harness_bootstrap", BOOTSTRAP)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_provider_attempts():
    spec = importlib.util.spec_from_file_location(
        "memsyco_provider_attempts", ROOT / "scripts" / "provider_attempts.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def open_test_ledger(attempts, path: Path, scope: str = "fixture", screen: str = "test"):
    authorization = hashlib.sha256(scope.encode()).hexdigest()
    return attempts.ProviderAttemptLedger(
        path, authorization, screen, 1_000_000_000_000, 0
    )


def authorize_test_campaign(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, journal: Path
) -> Path:
    packet = tmp_path / f"{journal.name}.authorization.json"
    campaign = {
        "journal_path": str(journal.resolve()),
        "hard_ceiling_nanos": 200_000_000_000,
        "opening_liability_nanos": 5_141_664_250,
        "unallocated_reserve_nanos": 10_000_000_000,
        "opening_reservations": [{
            "reservation_id": "historical-opening",
            "reserved_nanos": 5_141_664_250,
            "receipt_sha256": "b" * 64,
            "proof_sha256": "c" * 64,
        }],
    }
    packet.write_text(json.dumps({
        "status": "AUTHORIZED_STATE_MEMORY_CAMPAIGN",
        "authorization": {"authorization_scope_sha256": sha256(
            json.dumps({"campaign": campaign}, sort_keys=True, separators=(",", ":")).encode()
        )},
        "campaign": campaign,
    }))
    monkeypatch.setenv("MEMPHANT_CAMPAIGN_AUTHORIZATION", str(packet))
    return packet


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_bytes(root: Path, relative: str, value: bytes) -> str:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    return sha256(value)


def tiny_official_checkout(root: Path) -> dict:
    task_row = b'{"id":"one"}\n'
    upstream_manifest = {
        "name": "MemSyco-Bench",
        "schema_version": "1.2",
        "schema_file": "schema.json",
        "total_samples": 1,
        "tasks": {
            "objective_fact_judgment": {
                "file": "objective_fact_judgment.jsonl",
                "samples": 1,
                "memory_policy": "ignore_as_evidence",
                "sha256": sha256(task_row),
            }
        },
    }
    manifest_bytes = (json.dumps(upstream_manifest) + "\n").encode()
    schema_bytes = b'{"type":"object"}\n'
    lock = {
        "code": {
            "repository": "https://example.invalid/MemSyco-Bench",
            "revision": "a" * 40,
            "files": {"LICENSE": write_bytes(root, "LICENSE", b"MIT\n")},
        },
        "dataset": {
            "schema_version": "1.2",
            "total_samples": 1,
            "manifest_sha256": write_bytes(root, "data/manifest.json", manifest_bytes),
            "schema_sha256": write_bytes(root, "data/schema.json", schema_bytes),
            "tasks": upstream_manifest["tasks"],
        },
        "native_scorer": {
            "files": {
                "evaluation/run_task.py": write_bytes(
                    root, "evaluation/run_task.py", b"def main(): pass\n"
                )
            }
        },
    }
    write_bytes(root, "data/objective_fact_judgment.jsonl", task_row)
    return lock


def test_official_checkout_verification_is_complete_and_fails_on_drift(
    tmp_path: Path,
) -> None:
    runner = load_runner()
    lock = tiny_official_checkout(tmp_path)

    assert runner.verify_official(tmp_path, lock) == {
        "files": 5,
        "samples": 1,
        "tasks": 1,
    }
    (tmp_path / "data/objective_fact_judgment.jsonl").write_text(
        '{"id":"changed"}\n', encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="official file drift"):
        runner.verify_official(tmp_path, lock)


def test_official_archive_url_is_commit_pinned() -> None:
    runner = load_runner()
    lock = json.loads(LOCK.read_text(encoding="utf-8"))

    assert runner.release_url(lock) == (
        "https://github.com/XMUDeepLIT/MemSyco-Bench/archive/"
        "c31e2c85ee8cc3c6f643587b8a6f4b5ad5eb3bf6.tar.gz"
    )


def test_acquire_extracts_and_verifies_before_installing(
    tmp_path: Path,
) -> None:
    runner = load_runner()
    source = tmp_path / "source" / "MemSyco-Bench-pinned"
    source.mkdir(parents=True)
    lock = tiny_official_checkout(source)
    archive = tmp_path / "official.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(source, arcname=source.name)

    def copy_archive(_url: str, destination: Path) -> None:
        shutil.copy2(archive, destination)

    target = tmp_path / "cache"
    assert runner.acquire(target, lock, downloader=copy_archive) == {
        "files": 5,
        "samples": 1,
        "tasks": 1,
    }
    assert (target / "official/data/objective_fact_judgment.jsonl").is_file()


def test_memphant_adapter_exposes_only_the_official_label_free_interface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = load_adapter(monkeypatch)

    assert list(inspect.signature(adapter.build_context).parameters) == [
        "prior_dialogue",
        "user_question",
        "eval_config",
        "sample_key",
    ]


def test_memphant_adapter_retains_reflects_recalls_and_archives_trace_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = load_adapter(monkeypatch)
    calls: list[tuple[str, str, dict | None]] = []

    class Client:
        tenant_id = "11111111-1111-1111-1111-111111111111"
        item = {
            "unit_id": "unit-1",
            "body": "user: Current preference: technical training.",
            "kind": "semantic",
            "derived_by": "extraction",
            "inclusion_reason": "fused_top_k",
            "citation_episode_id": "episode-1",
            "citation_resource_id": None,
            "suppression_labels": [],
        }
        citations = [
            {
                "unit_id": "unit-1",
                "episode_id": "episode-1",
                "resource_id": None,
            }
        ]

        def __init__(self, _port: int, _key: str, tenant_id: str) -> None:
            assert tenant_id == self.tenant_id
            self.scope_id = ""
            self.actor_id = ""

        def post(self, path: str, payload: dict) -> dict:
            calls.append(("POST", path, payload))
            self.scope_id = payload["scope_id"]
            self.actor_id = payload["actor_id"]
            if path == "/v1/episodes":
                return {"episode_id": "episode-1"}
            if path == "/v1/recall":
                return {
                    "degraded": False,
                    "trace_id": "trace-1",
                    "items": [self.item],
                    "citations": self.citations,
                }
            raise AssertionError(path)

        def put(self, path: str, payload: dict) -> dict:
            calls.append(("PUT", path, payload))
            return {
                "subject_id": "subject-1",
                "scope_id": "scope-1",
                "actor_id": "actor-1",
                "agent_node_id": "agent-1",
                "subject_generation": 3,
            }

        def get(self, path: str) -> dict:
            calls.append(("GET", path, None))
            return {
                "id": "trace-1",
                "tenant_id": self.tenant_id,
                "scope_id": self.scope_id,
                "actor_id": self.actor_id,
                "context_items": [self.item],
                "citations": self.citations,
            }

    monkeypatch.setattr(adapter.gate_runtime, "ApiClient", Client)
    drains = []
    monkeypatch.setattr(
        adapter.gate_runtime,
        "drain_worker",
        lambda *args, **kwargs: drains.append((args, kwargs)) or 1,
    )
    for name, value in {
        "MEMPHANT_MEMSYCO_PORT": "39123",
        "MEMPHANT_MEMSYCO_API_KEY": "mk_test",
        "MEMPHANT_MEMSYCO_TENANT_ID": Client.tenant_id,
        "MEMPHANT_MEMSYCO_RUN_ID": "run-1",
        "MEMPHANT_MEMSYCO_PROOF_DIR": str(tmp_path),
        "MEMPHANT_MEMSYCO_DATABASE_URL": "postgres://scratch",
        "MEMPHANT_MEMSYCO_WORKER_BIN": "/tmp/memphant-worker",
        "MEMPHANT_MEMSYCO_EMBED_MODEL": "bge-m3",
        "MEMPHANT_MEMSYCO_STRUCTURED_STATE": "off",
    }.items():
        monkeypatch.setenv(name, value)
    config = types.SimpleNamespace(method="MemPhant", top_k=10, save_root=tmp_path)

    context = adapter.build_context(
        "User: I now want rigorous technical training.",
        "Which class should I choose?",
        config,
        sample_key="vms_1",
    )

    assert [path for _, path, _ in calls] == [
        "/v1/context-bindings/memsyco%3Arun-1%3Avms_1",
        "/v1/episodes",
        "/v1/recall",
        (
            "/v1/traces/trace-1?subject_id=subject-1&scope_id=scope-1"
            "&actor_id=actor-1&agent_node_id=agent-1&subject_generation=3"
        ),
    ]
    episode = calls[1][2]
    assert episode is not None
    assert episode["payload"]["episode"]["body"] == (
        "user: I now want rigorous technical training."
    )
    assert episode["payload"]["episode"]["source_kind"] == "user"
    assert episode["source_ref"] == "memsyco:run-1:vms_1"
    assert episode["observed_at"] == "2025-06-01T00:00:00Z"
    assert "Which class" not in json.dumps(episode)
    assert set(episode) == {
        "subject_id",
        "scope_id",
        "actor_id",
        "agent_node_id",
        "subject_generation",
        "source_ref",
        "observed_at",
        "payload",
    }
    assert context.context_text.startswith(adapter.ARBITRATION_CONTRACT)
    assert "objective factual question" in adapter.ARBITRATION_CONTRACT
    assert "world knowledge" in adapter.ARBITRATION_CONTRACT
    assert "memory_role=personalization" in context.context_text
    assert "epistemic_use=not_factual_evidence" in context.context_text
    assert "kind=semantic" in context.context_text
    assert "inclusion_reason=fused_top_k" in context.context_text
    assert "citation_episode_id=episode-1" in context.context_text
    assert "user: Current preference: technical training." in context.context_text
    assert context.retrieved_memories[0]["kind"] == "semantic"
    assert context.retrieved_memories[0]["derived_by"] == "extraction"
    assert context.retrieved_memories[0]["inclusion_reason"] == "fused_top_k"
    assert context.retrieved_memories[0]["citation_episode_id"] == "episode-1"
    assert context.method == "MemPhant"
    assert drains == [
        (("/tmp/memphant-worker", "postgres://scratch", "bge-m3"), {})
    ]
    proof = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert proof["sample_key_sha256"] == sha256(b"vms_1")
    assert proof["trace_id"] == "trace-1"
    assert proof["retrieved_unit_ids"] == ["unit-1"]
    assert proof["typed_context"] == context.context_text
    assert proof["typed_memories"] == context.retrieved_memories
    assert proof["typed_context_sha256"] == sha256(context.context_text.encode())
    assert set(proof["implementation_sha256"]) == {
        "adapter",
        "baseline_config",
        "harness_bootstrap",
        "provider_attempts",
        "structured_state_openrouter",
        "structured_state_prompt",
    }
    assert proof["arbitration_contract_sha256"] == sha256(
        adapter.ARBITRATION_CONTRACT.encode()
    )
    assert "mk_test" not in json.dumps(proof)


def test_structured_prompt_types_preference_epistemic_role() -> None:
    prompt = (ROOT / "config/structured-state-v2.txt").read_text(encoding="utf-8")
    assert "Never return an operation, target_unit_ids, tenant identity" in prompt
    assert 'Return exactly {"observations": []} when nothing qualifies.' in prompt
    assert "Explicit first-person likes, dislikes, and preferences" in prompt
    assert "other people's state" in prompt
    assert "Do not encode create, replace, delete, append, or upsert" in prompt
    assert "evidence_quote to an exact, nonempty, unique substring" in prompt
    assert "JSON string, number, boolean, null, or array" in prompt
    assert "object_fields" in prompt
    assert "value_json" not in prompt


def test_structured_worker_failure_durably_aborts_later_samples(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = load_adapter(monkeypatch)
    calls = []

    class Client:
        def __init__(self, *_args) -> None:
            calls.append("client")

        def put(self, _path: str, _payload: dict) -> dict:
            return {
                "subject_id": "subject-1",
                "scope_id": "scope-1",
                "actor_id": "actor-1",
                "agent_node_id": "agent-1",
                "subject_generation": 1,
            }

        def post(self, path: str, _payload: dict) -> dict:
            assert path == "/v1/episodes"
            return {"episode_id": "episode-1"}

    monkeypatch.setattr(adapter.gate_runtime, "ApiClient", Client)
    monkeypatch.setattr(
        adapter.gate_runtime,
        "drain_worker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("provenance")),
    )
    for name, value in {
        "MEMPHANT_MEMSYCO_PORT": "39123",
        "MEMPHANT_MEMSYCO_API_KEY": "mk_test",
        "MEMPHANT_MEMSYCO_TENANT_ID": "tenant-1",
        "MEMPHANT_MEMSYCO_RUN_ID": "run-1",
        "MEMPHANT_MEMSYCO_PROOF_DIR": str(tmp_path),
        "MEMPHANT_MEMSYCO_DATABASE_URL": "postgres://scratch",
        "MEMPHANT_MEMSYCO_WORKER_BIN": "/tmp/worker",
        "MEMPHANT_MEMSYCO_EMBED_MODEL": "fastembed:bge-m3",
        "MEMPHANT_MEMSYCO_STRUCTURED_STATE": "on",
        "MEMPHANT_MEMSYCO_EXTRACTOR_LEDGER": str(tmp_path / "extractor.jsonl"),
        "MEMPHANT_MEMSYCO_EXTRACTOR_MODEL": "deepseek/deepseek-v4-flash",
    }.items():
        monkeypatch.setenv(name, value)
    config = types.SimpleNamespace(method="MemPhant", top_k=10)
    with pytest.raises(RuntimeError, match="provenance"):
        adapter.build_context("user: preference", "question", config, sample_key="one")
    assert json.loads((tmp_path / "ABORTED.json").read_text())["stage"] == "worker_drain"
    with pytest.raises(RuntimeError, match="durably aborted"):
        adapter.build_context("user: another", "question", config, sample_key="two")
    assert calls == ["client"]


def test_memphant_adapter_fails_closed_on_missing_identity_or_degraded_recall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = load_adapter(monkeypatch)
    config = types.SimpleNamespace(method="MemPhant", top_k=10, save_root=tmp_path)
    dialogue_sha256 = hashlib.sha256(b"dialogue").hexdigest()
    question_sha256 = hashlib.sha256(b"question").hexdigest()
    expected_identity = "content-" + hashlib.sha256(
        json.dumps(
            {
                "dialogue_sha256": dialogue_sha256,
                "question_sha256": question_sha256,
            },
            sort_keys=True,
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    assert adapter._resolve_sample_key(None, "dialogue", "question") == expected_identity

    class DegradedClient:
        def __init__(self, _port: int, _key: str, tenant_id: str) -> None:
            self.tenant_id = tenant_id
            self.scope_id = ""
            self.actor_id = ""

        def post(self, path: str, payload: dict) -> dict:
            self.scope_id = payload["scope_id"]
            self.actor_id = payload["actor_id"]
            if path == "/v1/episodes":
                return {"episode_id": "episode-1"}
            if path == "/v1/reflect":
                return {"episodes_consumed": 0}
            return {"degraded": True, "trace_id": "trace-1", "items": []}

        def put(self, _path: str, _payload: dict) -> dict:
            return {
                "subject_id": "subject-1",
                "scope_id": "scope-1",
                "actor_id": "actor-1",
                "agent_node_id": "agent-1",
                "subject_generation": 0,
            }

        def get(self, _path: str) -> dict:
            return {
                "id": "trace-1",
                "tenant_id": self.tenant_id,
                "scope_id": self.scope_id,
                "actor_id": self.actor_id,
                "context_items": [],
                "citations": None,
            }

    monkeypatch.setattr(adapter.gate_runtime, "ApiClient", DegradedClient)
    monkeypatch.setattr(adapter.gate_runtime, "drain_worker", lambda *_args, **_kwargs: 1)
    for name, value in {
        "MEMPHANT_MEMSYCO_PORT": "39123",
        "MEMPHANT_MEMSYCO_API_KEY": "mk_test",
        "MEMPHANT_MEMSYCO_TENANT_ID": "11111111-1111-1111-1111-111111111111",
        "MEMPHANT_MEMSYCO_RUN_ID": "run-1",
        "MEMPHANT_MEMSYCO_PROOF_DIR": str(tmp_path),
        "MEMPHANT_MEMSYCO_DATABASE_URL": "postgres://scratch",
        "MEMPHANT_MEMSYCO_WORKER_BIN": "/tmp/memphant-worker",
        "MEMPHANT_MEMSYCO_EMBED_MODEL": "bge-m3",
        "MEMPHANT_MEMSYCO_STRUCTURED_STATE": "off",
    }.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match="degraded"):
        adapter.build_context("dialogue", "question", config, sample_key="one")


def test_bootstrap_usage_meter_records_complete_usage_without_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bootstrap = load_bootstrap()

    class Completions:
        def create(self, **_kwargs):
            return types.SimpleNamespace(
                id="response-1",
                model="served-model",
                usage=types.SimpleNamespace(
                    prompt_tokens=11, completion_tokens=7, total_tokens=18, cost=0.02
                ),
                provider="OpenAI",
            )

    class OpenAI:
        def __init__(self, **kwargs) -> None:
            self.base_url = kwargs.get("base_url")
            self.chat = types.SimpleNamespace(completions=Completions())

    module = types.SimpleNamespace(OpenAI=OpenAI)
    ledger = tmp_path / "usage.jsonl"
    authorize_test_campaign(monkeypatch, tmp_path, ledger)
    monkeypatch.setenv("MEMPHANT_MEMSYCO_ARM", "memphant")
    monkeypatch.setenv("MEMPHANT_MEMSYCO_TASK", "objective_fact_judgment")
    bootstrap.install_usage_meter(module, ledger)
    client = module.OpenAI(api_key="secret-key", base_url="https://models.invalid/v1")
    client.chat.completions.create(model="requested-model", messages=[{"role": "user", "content": "hi"}])

    stored = load_provider_attempts().load_provider_attempt_ledger_snapshot(ledger)
    assert stored["attempts_sha256"]
    assert len(stored["attempts"]) == 1
    assert stored["attempts"][0]["status"] == "result"
    row = stored["attempts"][0]["result"]["response"]
    assert row["usage"] == {
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "total_tokens": 18,
        "cost": 0.02,
    }
    assert stored["attempts"][0]["result"]["context"] == {
        "arm": "memphant",
        "task": "objective_fact_judgment",
    }
    assert row["requested_model"] == "requested-model"
    assert row["served_model"] == "served-model"
    assert row["response_id"] == "response-1"
    assert row["provider"] == "OpenAI"
    assert "secret-key" not in ledger.read_text(encoding="utf-8")


def test_register_memphant_exposes_an_importable_package_spec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bootstrap = load_bootstrap()
    base = types.SimpleNamespace(
        BaselineContext=type("BaselineContext", (), {}),
        BaselineEvalConfig=type("BaselineEvalConfig", (), {}),
        __all__=("BaselineContext", "BaselineEvalConfig"),
    )
    common = types.SimpleNamespace()
    config_loader = types.SimpleNamespace(
        build_baseline_eval_config=lambda *_args, **_kwargs: None,
        get_baseline_config_path=lambda *_args, **_kwargs: None,
        __all__=("build_baseline_eval_config", "get_baseline_config_path"),
    )
    adapter = types.SimpleNamespace(build_context=lambda *_args, **_kwargs: None)
    modules = {
        "baselines.base": base,
        "baselines.common": common,
        "baselines.config_loader": config_loader,
        "baselines.memphant": adapter,
    }
    monkeypatch.setattr(bootstrap, "_load", lambda name, _path: modules[name])

    bootstrap.register_memphant(tmp_path)

    assert importlib.util.find_spec("baselines") is not None


def test_bootstrap_usage_meter_fails_closed_when_provider_omits_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bootstrap = load_bootstrap()

    class Completions:
        def create(self, **_kwargs):
            return types.SimpleNamespace(id="response-1", model="model", usage=None)

    class OpenAI:
        def __init__(self, **_kwargs) -> None:
            self.chat = types.SimpleNamespace(completions=Completions())

    module = types.SimpleNamespace(OpenAI=OpenAI)
    ledger = tmp_path / "usage.jsonl"
    authorize_test_campaign(monkeypatch, tmp_path, ledger)
    bootstrap.install_usage_meter(module, ledger)
    with pytest.raises(RuntimeError, match="usage"):
        module.OpenAI().chat.completions.create(model="model", messages=[])


def test_bootstrap_meter_wraps_async_client_and_disables_hidden_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bootstrap = load_bootstrap()
    constructors = []

    class Completions:
        async def create(self, **_kwargs):
            return types.SimpleNamespace(
                id="async-1",
                model="served-model",
                provider="OpenAI",
                usage=types.SimpleNamespace(
                    prompt_tokens=4, completion_tokens=3, total_tokens=7, cost=0.01
                ),
            )

    class AsyncOpenAI:
        def __init__(self, **kwargs) -> None:
            constructors.append(kwargs)
            self.chat = types.SimpleNamespace(completions=Completions())

    module = types.SimpleNamespace(AsyncOpenAI=AsyncOpenAI)
    ledger = tmp_path / "async-attempts.json"
    authorize_test_campaign(monkeypatch, tmp_path, ledger)
    bootstrap.install_usage_meter(module, ledger)
    client = module.AsyncOpenAI(api_key="secret", max_retries=5)
    asyncio.run(client.chat.completions.create(model="requested", messages=[]))

    assert constructors[0]["max_retries"] == 0
    assert constructors[0]["default_headers"]["X-OpenRouter-Cache"] == "false"
    stored = load_provider_attempts().load_provider_attempt_ledger_snapshot(ledger)
    response = stored["attempts"][0]["result"]["response"]
    assert response["response_id"] == "async-1"
    assert response["retry_index"] == 0
    assert len(response["request_sha256"]) == 64
    assert len(response["result_sha256"]) == 64


def test_async_meter_reconciles_generation_stats_off_event_loop_and_times_errors(
    tmp_path: Path,
) -> None:
    attempts = importlib.util.spec_from_file_location(
        "provider_attempts_async_test", ROOT / "scripts" / "provider_attempts.py"
    )
    assert attempts and attempts.loader
    module_under_test = importlib.util.module_from_spec(attempts)
    attempts.loader.exec_module(module_under_test)
    event_loop_thread = threading.get_ident()
    lookup_threads = []

    class Completions:
        async def create(self, **_kwargs):
            return types.SimpleNamespace(
                id="async-stats",
                model="served",
                usage=types.SimpleNamespace(
                    prompt_tokens=None, completion_tokens=None, total_tokens=None
                ),
            )

    class AsyncOpenAI:
        def __init__(self, **_kwargs) -> None:
            self.chat = types.SimpleNamespace(completions=Completions())

    def lookup(_response_id):
        lookup_threads.append(threading.get_ident())
        return {
            "model": "served-pinned",
            "provider_name": "OpenAI",
            "tokens_prompt": 2,
            "tokens_completion": 1,
            "total_cost": 0.01,
        }

    sdk = types.SimpleNamespace(AsyncOpenAI=AsyncOpenAI)
    ledger_path = tmp_path / "async-stats.json"
    ledger = open_test_ledger(module_under_test, ledger_path, "async-stats")
    module_under_test.install_openai_meter(
        sdk, ledger, max_liability_nanos=100_000_000, generation_lookup=lookup
    )
    asyncio.run(sdk.AsyncOpenAI().chat.completions.create(model="requested", messages=[]))
    assert lookup_threads and lookup_threads[0] != event_loop_thread
    assert module_under_test.load_provider_attempt_ledger_snapshot(ledger_path)["attempts"][0]["result"][
        "response"
    ]["served_model"] == "served-pinned"

    class BrokenCompletions:
        def create(self, **_kwargs):
            raise OSError("offline")

    class OpenAI:
        def __init__(self, **_kwargs) -> None:
            self.chat = types.SimpleNamespace(completions=BrokenCompletions())

    failed_sdk = types.SimpleNamespace(OpenAI=OpenAI)
    failed_ledger_path = tmp_path / "failed.json"
    failed_ledger = open_test_ledger(module_under_test, failed_ledger_path, "failed")
    module_under_test.install_openai_meter(
        failed_sdk, failed_ledger, max_liability_nanos=100_000_000
    )
    with pytest.raises(OSError, match="offline"):
        failed_sdk.OpenAI().chat.completions.create(model="requested", messages=[])
    error = module_under_test.load_provider_attempt_ledger_snapshot(failed_ledger_path)["attempts"][0]["error"]
    assert error["type"] == "OSError"
    assert error["elapsed_seconds"] >= 0


def test_campaign_reconciliation_and_journal_closure_are_authoritative(
    tmp_path: Path, monkeypatch
) -> None:
    attempts = load_provider_attempts()
    path = tmp_path / "campaign.jsonl"
    ledger = attempts.ProviderAttemptLedger(
        path,
        "a" * 64,
        "reconcile-screen",
        20_000_001_000,
        500,
        opening_reservations=[{
            "reservation_id": "historical-deep-1",
            "reserved_nanos": 300,
            "receipt_sha256": "b" * 64,
            "proof_sha256": "c" * 64,
        }, {
            "reservation_id": "historical-structured-1",
            "reserved_nanos": 200,
            "receipt_sha256": "d" * 64,
            "proof_sha256": "e" * 64,
        }],
    )
    with pytest.raises(ValueError, match="known opening reservation"):
        ledger.record_reconciliation(
            "invented",
            settled_nanos=0,
            receipt_sha256="b" * 64,
            proof_sha256="c" * 64,
        )
    with pytest.raises(ValueError, match="opening reservation receipt"):
        ledger.record_reconciliation(
            "historical-deep-1",
            settled_nanos=25,
            receipt_sha256="f" * 64,
            proof_sha256="c" * 64,
        )
    ledger.record_reconciliation(
        "historical-deep-1",
        settled_nanos=25,
        receipt_sha256="b" * 64,
        proof_sha256="c" * 64,
    )
    snapshot = ledger.snapshot()
    assert snapshot["opening_liability_nanos"] == 200
    assert snapshot["settled_nanos"] == 25
    assert snapshot["total_liability_nanos"] == 225

    monkeypatch.setattr(attempts.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("projection failed")))
    with pytest.raises(OSError, match="projection failed"):
        ledger.close_campaign(tmp_path / "closure.json")
    ledger.close()

    closed = attempts.ProviderAttemptLedger(
        path,
        "a" * 64,
        "resume-screen",
        20_000_001_000,
        500,
        opening_reservations=[{
            "reservation_id": "historical-deep-1",
            "reserved_nanos": 300,
            "receipt_sha256": "b" * 64,
            "proof_sha256": "c" * 64,
        }, {
            "reservation_id": "historical-structured-1",
            "reserved_nanos": 200,
            "receipt_sha256": "d" * 64,
            "proof_sha256": "e" * 64,
        }],
    )
    with pytest.raises(RuntimeError, match="closed"):
        closed.assert_open()


def test_openai_meter_requires_reservation_before_sdk_construction(
    tmp_path: Path,
) -> None:
    attempts = load_provider_attempts()
    constructed = []

    class OpenAI:
        def __init__(self, **_kwargs) -> None:
            constructed.append(True)

    ledger = attempts.ProviderAttemptLedger(
        tmp_path / "campaign.jsonl", "a" * 64, "judge", 1_000, 0
    )
    with pytest.raises(TypeError, match="max_liability_nanos"):
        attempts.install_openai_meter(types.SimpleNamespace(OpenAI=OpenAI), ledger)
    assert constructed == []


def test_openai_meter_defers_credentials_lookup_and_client_until_after_start(
    tmp_path: Path,
) -> None:
    attempts = load_provider_attempts()
    ledger_path = tmp_path / "campaign.jsonl"
    ledger = open_test_ledger(attempts, ledger_path, "credential-order")
    order = []

    def after_start(label: str) -> None:
        snapshot = attempts.load_provider_attempt_ledger_snapshot(ledger_path)
        assert snapshot["attempts"][0]["status"] == "started"
        order.append(label)

    class Completions:
        def create(self, **_kwargs):
            return types.SimpleNamespace(
                id="ordered",
                model="served",
                provider="OpenAI",
                usage=types.SimpleNamespace(
                    prompt_tokens=2,
                    completion_tokens=1,
                    total_tokens=3,
                    cost=0.01,
                ),
            )

    class OpenAI:
        def __init__(self, **_kwargs) -> None:
            after_start("client")
            self.chat = types.SimpleNamespace(completions=Completions())

    def lookup_factory():
        after_start("credentials")
        return None

    sdk = types.SimpleNamespace(OpenAI=OpenAI)
    attempts.install_openai_meter(
        sdk,
        ledger,
        max_liability_nanos=100_000_000,
        generation_lookup_factory=lookup_factory,
    )
    sdk.OpenAI().chat.completions.create(model="requested", messages=[])
    assert order == ["credentials", "client"]


def test_sync_meter_preserves_paid_response_when_generation_lookup_fails(
    tmp_path: Path,
) -> None:
    attempts = importlib.util.spec_from_file_location(
        "provider_attempts_sync_failure_test", ROOT / "scripts" / "provider_attempts.py"
    )
    assert attempts and attempts.loader
    module_under_test = importlib.util.module_from_spec(attempts)
    attempts.loader.exec_module(module_under_test)
    calls = []

    class Completions:
        def create(self, **_kwargs):
            calls.append("completion")
            return types.SimpleNamespace(
                id="sync-paid-before-stats-failure",
                model="served-alias",
                provider="OpenAI",
                usage=types.SimpleNamespace(
                    prompt_tokens=4,
                    completion_tokens=3,
                    total_tokens=7,
                    cost=0.01,
                ),
            )

    class OpenAI:
        def __init__(self, **_kwargs) -> None:
            self.chat = types.SimpleNamespace(completions=Completions())

    def fail_lookup(_response_id):
        raise OSError("stats unavailable")

    sdk = types.SimpleNamespace(OpenAI=OpenAI)
    ledger_path = tmp_path / "sync-stats-failure.json"
    ledger = open_test_ledger(module_under_test, ledger_path, "sync-failure")
    module_under_test.install_openai_meter(
        sdk,
        ledger,
        max_liability_nanos=100_000_000,
        generation_lookup=fail_lookup,
    )
    with pytest.raises(RuntimeError, match="generation statistics"):
        sdk.OpenAI().chat.completions.create(model="requested", messages=[])

    attempt = module_under_test.load_provider_attempt_ledger_snapshot(ledger_path)["attempts"][0]
    assert calls == ["completion"]
    assert attempt["status"] == "error"
    assert attempt["result"] is None
    assert attempt["error"]["type"] == "OSError"
    response = attempt["error"]["response"]
    assert response["response_id"] == "sync-paid-before-stats-failure"
    assert response["usage"] == {
        "prompt_tokens": 4,
        "completion_tokens": 3,
        "total_tokens": 7,
        "cost": 0.01,
    }
    assert response["parse_status"] == "generation_stats_lookup_failed"


def test_async_meter_preserves_paid_response_when_generation_lookup_fails(
    tmp_path: Path,
) -> None:
    attempts = importlib.util.spec_from_file_location(
        "provider_attempts_async_failure_test", ROOT / "scripts" / "provider_attempts.py"
    )
    assert attempts and attempts.loader
    module_under_test = importlib.util.module_from_spec(attempts)
    attempts.loader.exec_module(module_under_test)
    calls = []

    class Completions:
        async def create(self, **_kwargs):
            calls.append("completion")
            return types.SimpleNamespace(
                id="async-paid-before-stats-failure",
                model="served-alias",
                provider="OpenAI",
                usage=types.SimpleNamespace(
                    prompt_tokens=4,
                    completion_tokens=3,
                    total_tokens=7,
                    cost=0.01,
                ),
            )

    class AsyncOpenAI:
        def __init__(self, **_kwargs) -> None:
            self.chat = types.SimpleNamespace(completions=Completions())

    def fail_lookup(_response_id):
        raise OSError("stats unavailable")

    sdk = types.SimpleNamespace(AsyncOpenAI=AsyncOpenAI)
    ledger_path = tmp_path / "async-stats-failure.json"
    ledger = open_test_ledger(module_under_test, ledger_path, "async-failure")
    module_under_test.install_openai_meter(
        sdk,
        ledger,
        max_liability_nanos=100_000_000,
        generation_lookup=fail_lookup,
    )
    with pytest.raises(RuntimeError, match="generation statistics"):
        asyncio.run(
            sdk.AsyncOpenAI().chat.completions.create(model="requested", messages=[])
        )

    attempt = module_under_test.load_provider_attempt_ledger_snapshot(ledger_path)["attempts"][0]
    assert calls == ["completion"]
    assert attempt["status"] == "error"
    assert attempt["result"] is None
    assert attempt["error"]["type"] == "OSError"
    assert attempt["error"]["response"]["response_id"] == (
        "async-paid-before-stats-failure"
    )
    assert attempt["error"]["response"]["parse_status"] == (
        "generation_stats_lookup_failed"
    )


def test_official_command_has_one_arm_and_no_completion_cache(tmp_path: Path) -> None:
    runner = load_runner()
    command = runner.official_command(
        official_dir=tmp_path / "official",
        output=tmp_path / "report.json",
        task="objective_fact_judgment",
        arm="memphant",
        model="answer-model",
        base_url="https://answer.invalid/v1",
        judge_model="judge-model",
        judge_base_url="https://judge.invalid/v1",
        limit=3,
    )
    joined = " ".join(command)
    assert "--with-memory-only" in command
    assert "--no-memory-only" not in command
    assert "--memory-method MemPhant" in joined
    assert "--memory-top-k 10" in joined
    assert "--workers 1" in joined
    assert "--limit 3" in joined
    assert "--no-completion-cache" in command

    for task in runner.TASKS[1:]:
        task_command = runner.official_command(
            official_dir=tmp_path / "official",
            output=tmp_path / f"{task}.json",
            task=task,
            arm="memphant",
            model="answer-model",
            base_url="https://answer.invalid/v1",
            judge_model="judge-model",
            judge_base_url="https://judge.invalid/v1",
            limit=1,
        )
        assert "--with-memory-only" not in task_command
        assert "--no-memory-only" not in task_command
        assert "--memory-method" in task_command


def test_manifest_runner_materializes_an_immutable_slice_and_supports_all_arms(
    tmp_path: Path,
) -> None:
    runner = load_runner()
    source = tmp_path / "source.jsonl"
    rows = [{"id": f"case-{index}"} for index in range(4)]
    source.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    selected = tmp_path / "run" / "input.jsonl"

    manifest = runner.materialize_sample_slice(
        source, selected, offset=1, sample_count=2
    )

    assert selected.read_text(encoding="utf-8") == (
        json.dumps(rows[1], sort_keys=True, separators=(",", ":"))
        + "\n"
        + json.dumps(rows[2], sort_keys=True, separators=(",", ":"))
        + "\n"
    )
    assert manifest == {
        "source_sha256": runner.sha256_file(source),
        "slice_sha256": runner.sha256_file(selected),
        "source_rows": 4,
        "offset": 1,
        "sample_count": 2,
    }
    with pytest.raises(RuntimeError, match="fresh artifact directory"):
        runner.materialize_sample_slice(source, selected, offset=0, sample_count=1)

    for arm in ("memphant", "episode_only", "raw_dialogue"):
        command = runner.official_command(
            official_dir=tmp_path / "official",
            output=tmp_path / arm / "report.json",
            task="memory_evidence_conflict",
            arm=arm,
            model="answer-model",
            base_url="https://answer.invalid/v1",
            judge_model="judge-model",
            judge_base_url="https://judge.invalid/v1",
            limit=2,
            test_jsonl=selected,
        )
        assert str(selected.resolve()) in command
        joined = " ".join(command)
        if arm == "raw_dialogue":
            assert "--memory-method" not in command
        else:
            assert "--memory-method MemPhant" in joined

    args = runner.build_parser().parse_args(
        [
            "run",
            "--official-dir",
            str(tmp_path / "official"),
            "--out-dir",
            str(tmp_path / "new-run"),
            "--task",
            "memory_evidence_conflict",
            "--arm",
            "episode_only",
            "--limit",
            "12",
            "--offset",
            "25",
            "--test-jsonl",
            str(source),
            "--embed-model",
            "bge-m3",
            "--model",
            "answer-model",
            "--base-url",
            "https://answer.invalid/v1",
            "--judge-model",
            "judge-model",
            "--judge-base-url",
            "https://judge.invalid/v1",
        ]
    )
    assert (args.task, args.arm, args.limit, args.offset, args.embed_model) == (
        "memory_evidence_conflict",
        "episode_only",
        12,
        25,
        "bge-m3",
    )
    assert runner.requested_models_match(
        ["same-model"] * 24, "same-model", "same-model", 12
    )
    assert not runner.requested_models_match(
        ["same-model"] * 12, "same-model", "same-model", 12
    )


def test_memsyco_child_env_maps_openrouter_key_without_command_exposure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = load_runner()
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-openrouter-key")
    monkeypatch.delenv("GENERATION_API_KEY", raising=False)
    monkeypatch.delenv("JUDGE_API_KEY", raising=False)

    child = runner.paid_provider_env()

    assert child["GENERATION_API_KEY"] == "secret-openrouter-key"
    assert child["JUDGE_API_KEY"] == "secret-openrouter-key"


def test_memsyco_verifier_accepts_only_fully_recovered_http_retries() -> None:
    runner = load_runner()
    summary = {
        "provider_attempts": 3,
        "completed_attempts": 3,
        "interrupted_attempts": 0,
        "successful_responses": 2,
        "successful_decodes": 2,
        "successful_episodes": 2,
        "episodes": 2,
        "priced_responses": 2,
        "unpriced_attempts": 1,
        "transient_attempts": 1,
        "transient_no_content_attempts": 0,
        "transient_transport_attempts": 0,
        "transient_http_attempts": 1,
        "terminal_provenance_errors": 0,
        "terminal_decode_errors": 0,
        "terminal_rejected_operations": 0,
    }

    assert runner.structured_extractor_is_complete(summary, expected=2)

    summary["transient_transport_attempts"] = 1
    assert not runner.structured_extractor_is_complete(summary, expected=2)


def test_memsyco_cli_exposes_complete_lifecycle_and_preserves_five_metrics(
    tmp_path: Path,
) -> None:
    runner = load_runner()
    assert runner.REPORT_TASKS == {task: task for task in runner.TASKS}
    parser = runner.build_parser()
    for command in ("acquire", "verify", "run", "verify-results", "score-official"):
        with pytest.raises(SystemExit) as exit_info:
            parser.parse_args([command, "--help"])
        assert exit_info.value.code == 0

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "tasks": list(runner.TASKS),
                "sample_limit": 1,
                "answer_model": "answer-model",
                "judge_model": "judge-model",
                "extractor_model": "extractor-model",
            }
        ),
        encoding="utf-8",
    )
    extractor_rows = []
    for index, task in enumerate(runner.TASKS):
        common = {
            "schema_version": 1,
            "attempt_id": f"extract-{index}",
            "requested_model": "extractor-model",
            "episode_id": f"episode-{index}",
            "attempt": 1,
            "max_attempts": 3,
        }
        extractor_rows.extend(
            [
                common | {"event": "started"},
                common | {
                    "event": "result",
                    "http_status": 200,
                    "served_model": "extractor-model",
                    "response_id": f"extract-response-{index}",
                    "provider": "OpenAI",
                    "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3, "cost": 0.01},
                },
                common | {
                    "event": "decode",
                    "error": None,
                    "accepted_op_count": 1,
                    "rejected_op_count": 0,
                    "rejection_reasons": {},
                },
            ]
        )
    (run_dir / "extractor-attempts.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in extractor_rows),
        encoding="utf-8",
    )
    for index, task in enumerate(runner.TASKS):
        task_dir = run_dir / task
        (task_dir / "memory").mkdir(parents=True)
        sample_key = f"sample-{index}"
        identity = (
            {"id": sample_key}
            if task == "personalized_memory_use"
            else {"query_id": sample_key}
        )
        if index == 0:
            dialogue_sha256 = "a" * 64
            question_sha256 = "b" * 64
            identity_material = hashlib.sha256(
                json.dumps(
                    {
                        "dialogue_sha256": dialogue_sha256,
                        "question_sha256": question_sha256,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            resolved_sample_key = f"content-{identity_material}"
            proof_payload = {
                "gold_fields_consumed": [],
                "sample_key_sha256": hashlib.sha256(
                    resolved_sample_key.encode()
                ).hexdigest(),
                "sample_key_source": "label_free_content_hash",
                "sample_identity_material_sha256": identity_material,
                "dialogue_sha256": dialogue_sha256,
                "question_sha256": question_sha256,
                "trace_id": f"trace-{index}",
                "retrieved_unit_ids": [],
            }
        else:
            proof_payload = {
                "gold_fields_consumed": [],
                "sample_key_sha256": hashlib.sha256(sample_key.encode()).hexdigest(),
                "sample_key_source": "official_argument",
                "trace_id": f"trace-{index}",
                "retrieved_unit_ids": [],
            }
        proof_path = task_dir / "memory" / f"{proof_payload['sample_key_sha256']}.json"
        (task_dir / "report.json").write_text(
            json.dumps(
                {
                        "task": runner.REPORT_TASKS[task],
                    "n_samples": 1,
                    "n_api_failed_samples": 0,
                    "results": [
                        identity
                        | {
                            "lightmem": {
                                "user_id": "memsyco_"
                                + proof_payload["sample_key_sha256"][:24],
                                "save_dir": str(proof_path),
                            },
                            "with_memory": {
                                "answer_text": "answer",
                                "judge": {
                                    "judge_parse_ok": True,
                                    "judge_error": None,
                                },
                            }
                        }
                    ],
                        "metrics": {
                            "with_memory": {
                                key: (
                                    1
                                    if key in {"n_scored", "n_judged"}
                                    else 0
                                )
                                for key in runner.METRIC_KEYS[task]
                            }
                        },
                }
            ),
            encoding="utf-8",
        )
        proof_path.write_text(json.dumps(proof_payload), encoding="utf-8")
        attempts = load_provider_attempts()
        attempt_ledger = open_test_ledger(
            attempts, task_dir / "attempts.json", f"fixture:{task}"
        )
        for role, model in (("answer", "answer-model"), ("judge", "judge-model")):
            response = {
                "response_id": f"{task}-{role}",
                "requested_model": model,
                "served_model": model,
                "provider": "OpenAI",
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3, "cost": 0.01},
                "elapsed_seconds": 0.1,
                "retry_index": 0,
                "parse_status": "provider_response_validated",
                "request_sha256": "1" * 64,
                "result_sha256": "2" * 64,
            }
            attempt_ledger.record(
                "start",
                role,
                {
                    "retry_index": 0,
                    "requested_model": model,
                    "request_sha256": "1" * 64,
                    "context": {"arm": "memphant", "task": task},
                    "max_liability_nanos": 100_000_000,
                },
            )
            attempt_ledger.record(
                "result",
                role,
                {
                    "context": {"arm": "memphant", "task": task},
                    "response": response,
                },
            )

    verified = runner.verify_results(run_dir)
    assert set(verified["metrics_by_task"]) == set(runner.TASKS)
    assert "aggregate" not in verified
    assert "overall" not in verified

    first_task_dir = run_dir / runner.TASKS[0]
    proof_path = next((first_task_dir / "memory").glob("*.json"))
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    valid_digest = proof["sample_key_sha256"]
    proof["sample_key_sha256"] = "0" * 64
    proof_path.write_text(json.dumps(proof), encoding="utf-8")
    with pytest.raises(RuntimeError, match="sample identity mismatch"):
        runner.verify_results(run_dir)
    proof["sample_key_sha256"] = valid_digest
    proof_path.write_text(json.dumps(proof), encoding="utf-8")

    report_path = first_task_dir / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["n_api_failed_samples"] = 1
    report["results"][0]["api_error"] = "provider failed"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(RuntimeError, match="API failure"):
        runner.verify_results(run_dir)
    report["n_api_failed_samples"] = 0
    report["results"][0].pop("api_error")
    report["results"][0]["with_memory"]["judge"]["judge_parse_ok"] = False
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(RuntimeError, match="judge result is incomplete"):
        runner.verify_results(run_dir)
    report["results"][0]["with_memory"]["judge"]["judge_parse_ok"] = True
    report["metrics"]["with_memory"].pop("objective_correctness_avg")
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(RuntimeError, match="official metrics"):
        runner.verify_results(run_dir)
