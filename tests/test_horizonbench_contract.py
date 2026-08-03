from __future__ import annotations

import importlib.util
import json
from datetime import datetime
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_horizonbench.py"


def load_module():
    spec = importlib.util.spec_from_file_location("run_horizonbench", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def sample_row(index: int) -> dict:
    options = [
        {"letter": letter, "value": f"value-{letter}", "option": f"response {letter}"}
        for letter in "ABCDE"
    ]
    return {
        "id": f"item-{index}",
        "generator": "fixture-generator",
        "user_id": f"user-{index}",
        "conversation": (
            "Conversation History:\n"
            "Date: 2026-01-01T01:02:03\n"
            "Scenario: initial preference\n"
            "User: I prefer careful answers.\n"
            "continued thought\n"
            "Assistant: Understood.\n"
            "Date: 2026-02-01T01:02:03\n"
            "Scenario: later event\n"
            "User: My situation changed.\n"
            "Assistant: Tell me more."
        ),
        "options": json.dumps(options),
        "correct_letter": "gold-correct-sentinel",
        "distractor_letter": "gold-distractor-sentinel",
        "has_evolved": "gold-evolved-sentinel",
        "preference_domain": "gold-domain-sentinel",
        "preference_evolution": "gold-evolution-sentinel",
    }


def test_options_and_conversations_follow_official_shape() -> None:
    module = load_module()
    row = sample_row(0)

    options = module.parse_options(row["options"])
    sessions = module.parse_sessions(row["conversation"])

    assert [option["letter"] for option in options] == list("ABCDE")
    assert len(sessions) == 2
    assert sessions[0]["date"] == "2026-01-01T01:02:03"
    assert sessions[0]["scenario"] == "initial preference"
    assert sessions[0]["turns"][0] == {
        "role": "user",
        "content": "I prefer careful answers.\ncontinued thought",
    }

    four_options = json.loads(row["options"])[:-1]
    assert [option["letter"] for option in module.parse_options(four_options)] == list(
        "ABCD"
    )


def test_prompt_item_quarantines_every_scoring_field_and_value() -> None:
    module = load_module()
    row = sample_row(0)

    prompt = module.prompt_item(row)
    encoded = json.dumps(prompt, sort_keys=True)

    assert set(prompt) == {"id", "user_id", "generator", "conversation", "options"}
    for field in module.SCORING_ONLY_FIELDS:
        assert field not in encoded
        assert str(row[field]) not in encoded


def test_sample_census_requires_ten_unique_rows_and_users() -> None:
    module = load_module()
    rows = [sample_row(index) for index in range(10)]

    census = module.validate_sample_rows(rows)

    assert census["row_count"] == 10
    assert census["user_count"] == 10
    assert census["expected_ids"] == [f"item-{index}" for index in range(10)]

    with pytest.raises(ValueError, match="exactly 10"):
        module.validate_sample_rows(rows[:-1])
    duplicate = [*rows[:-1], dict(rows[0])]
    with pytest.raises(ValueError, match="duplicate id"):
        module.validate_sample_rows(duplicate)


def benchmark_fixture_rows() -> list[dict]:
    rows = []
    index = 0
    for generator in ("generator-a", "generator-b", "generator-c"):
        for user_index in range(2):
            user_id = f"{generator}-user-{user_index}"
            conversation = sample_row(index)["conversation"] + f"\nUser: {user_id}"
            for evolved in (False, True):
                row = sample_row(index)
                row.update(
                    id=f"benchmark-{index}",
                    generator=generator,
                    user_id=user_id,
                    conversation=conversation,
                    correct_letter="A",
                    distractor_letter="B" if evolved else "",
                    has_evolved=evolved,
                    preference_domain="domain",
                    preference_evolution="changed" if evolved else "",
                )
                rows.append(row)
                index += 1
    return rows


def test_full_census_requires_monotone_user_timelines_and_expected_strata() -> None:
    module = load_module()
    rows = benchmark_fixture_rows()

    census = module.validate_benchmark_rows(
        rows,
        expected_rows=12,
        expected_users=6,
        expected_generator_counts={
            "generator-a": 4,
            "generator-b": 4,
            "generator-c": 4,
        },
    )

    assert census["row_count"] == 12
    assert census["user_count"] == 6
    assert census["eligible_user_counts"] == {
        "generator-a": 2,
        "generator-b": 2,
        "generator-c": 2,
    }
    assert census["option_cardinality_counts"] == {"5": 12}
    assert census["evolved_rows_without_distractor"] == 0
    monotone = [dict(row) for row in rows]
    monotone[1]["conversation"] += "\nAssistant: A later turn.\n"
    monotone[1]["distractor_letter"] = ""
    monotone_census = module.validate_benchmark_rows(
        monotone,
        expected_rows=12,
        expected_users=6,
        expected_generator_counts={
            "generator-a": 4,
            "generator-b": 4,
            "generator-c": 4,
        },
    )
    assert monotone_census["evolved_rows_without_distractor"] == 1
    drifted = [dict(row) for row in rows]
    drifted[1]["conversation"] = drifted[1]["conversation"].replace(
        "careful answers", "careless answers"
    )
    with pytest.raises(ValueError, match="timeline drift"):
        module.validate_benchmark_rows(
            drifted,
            expected_rows=12,
            expected_users=6,
            expected_generator_counts={
                "generator-a": 4,
                "generator-b": 4,
                "generator-c": 4,
            },
        )
    assert module.inconsistent_timeline_users(drifted) == ["generator-a-user-0"]


def test_confirmation_selection_is_balanced_deterministic_and_gold_blind() -> None:
    module = load_module()
    rows = benchmark_fixture_rows()
    excluded = {"generator-a-user-0", "generator-b-user-0", "generator-c-user-0"}

    selected = module.select_confirmation_rows(
        rows,
        excluded_user_ids=excluded,
        seed="confirmation-v1",
        users_per_generator=1,
    )
    mutated_gold = [
        {**row, "correct_letter": "E", "distractor_letter": "D"} for row in rows
    ]
    selected_after_gold_change = module.select_confirmation_rows(
        mutated_gold,
        excluded_user_ids=excluded,
        seed="confirmation-v1",
        users_per_generator=1,
    )

    assert [row["id"] for row in selected] == [
        row["id"] for row in selected_after_gold_change
    ]
    assert len(selected) == 6
    assert len({row["user_id"] for row in selected}) == 3
    assert all(row["user_id"] not in excluded for row in selected)
    for generator in ("generator-a", "generator-b", "generator-c"):
        stratum = [
            row["has_evolved"] for row in selected if row["generator"] == generator
        ]
        assert stratum == [False, True]


def test_graph_population_reconciliation_uses_only_identity_columns() -> None:
    module = load_module()
    rows = benchmark_fixture_rows()
    graph_rows = [
        {"user_id": row["user_id"], "generator": row["generator"]} for row in rows[::2]
    ]
    graph_rows.append({"user_id": "graph-only-user", "generator": "generator-a"})

    report = module.reconcile_graph_population(rows, graph_rows, expected_graph_users=7)

    assert report == {
        "benchmark_users": 6,
        "graph_users": 7,
        "benchmark_users_missing_from_graph": [],
        "graph_only_users": ["graph-only-user"],
    }
    graph_rows[0]["secret_graph"] = "must not be read"
    with pytest.raises(ValueError, match="identity columns"):
        module.reconcile_graph_population(rows, graph_rows, expected_graph_users=7)


def test_locked_source_file_requires_exact_size_and_sha256(tmp_path: Path) -> None:
    module = load_module()
    source = tmp_path / "part.parquet"
    source.write_bytes(b"locked parquet bytes")

    assert module.verify_locked_file(
        source,
        expected_size=20,
        expected_sha256="26b7e954d5ad3aaaaaf16d6a17c10cd7fa62b87cb086142d6675dfec1a5d64fb",
    ) == {
        "path": str(source),
        "size": 20,
        "sha256": "26b7e954d5ad3aaaaaf16d6a17c10cd7fa62b87cb086142d6675dfec1a5d64fb",
    }
    with pytest.raises(ValueError, match="size drift"):
        module.verify_locked_file(source, expected_size=19, expected_sha256="a" * 64)
    with pytest.raises(ValueError, match="sha256 drift"):
        module.verify_locked_file(source, expected_size=20, expected_sha256="a" * 64)


def test_locked_confirmation_requires_exact_ids_users_and_source_hash(
    tmp_path: Path,
) -> None:
    module = load_module()
    rows = benchmark_fixture_rows()
    source = tmp_path / "confirmation.jsonl"
    source.write_bytes(module.canonical_jsonl_bytes(rows))
    selection = tmp_path / "selection.json"
    selection.write_text(
        json.dumps(
            {
                "status": "frozen",
                "dataset_revision": module.DATASET_REVISION,
                "source_jsonl_sha256": module.gr.sha256_file(source),
                "expected_ids": [row["id"] for row in rows],
                "expected_user_ids": sorted({row["user_id"] for row in rows}),
                "rows": 12,
                "users": 6,
            }
        )
    )

    loaded, report = module.load_locked_confirmation(
        source, selection, expected_rows=12, expected_users=6
    )

    assert loaded == rows
    assert report["expected_ids"] == [row["id"] for row in rows]
    source.write_bytes(source.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="source hash drift"):
        module.load_locked_confirmation(
            source, selection, expected_rows=12, expected_users=6
        )


def test_canonical_jsonl_is_stable_and_ends_with_newline() -> None:
    module = load_module()
    rows = [sample_row(index) for index in range(10)]

    first = module.canonical_jsonl_bytes(rows)
    second = module.canonical_jsonl_bytes(list(rows))

    assert first == second
    assert first.endswith(b"\n")
    assert len([line for line in first.split(b"\n") if line]) == 10


def test_source_revision_must_match_pin() -> None:
    module = load_module()

    module.validate_source_revision(module.DATASET_REVISION)
    with pytest.raises(ValueError, match="revision drift"):
        module.validate_source_revision("0" * 40)


def test_runtime_inputs_are_item_isolated_chronological_and_gold_blind() -> None:
    module = load_module()
    row = sample_row(3)

    runtime = module.runtime_item(row)

    assert runtime["context_ref"] == "horizon-item-3"
    assert len(runtime["episodes"]) == 2
    assert [episode["source_ref"] for episode in runtime["episodes"]] == [
        "horizon:item-3:session:0",
        "horizon:item-3:session:1",
    ]
    observed = [
        datetime.fromisoformat(episode["observed_at"].replace("Z", "+00:00"))
        for episode in runtime["episodes"]
    ]
    assert observed == sorted(observed)
    assert all(
        len(episode["body"].encode()) <= module.MAX_EPISODE_BYTES
        for episode in runtime["episodes"]
    )
    encoded = json.dumps(runtime, sort_keys=True)
    for field in module.SCORING_ONLY_FIELDS:
        assert field not in encoded
        assert str(row[field]) not in encoded
    for letter in "ABCDE":
        assert f"response {letter}" in runtime["question"]


def test_evidence_completion_rejects_missing_duplicate_degraded_and_empty() -> None:
    module = load_module()
    expected = ["one", "two"]
    rows = [
        {"id": "one", "arm": "fast", "degraded": False, "evidence": [{"body": "a"}]},
        {"id": "two", "arm": "fast", "degraded": False, "evidence": [{"body": "b"}]},
    ]

    module.validate_evidence_rows(rows, expected, "fast")
    with pytest.raises(ValueError, match="expected IDs"):
        module.validate_evidence_rows(rows[:-1], expected, "fast")
    with pytest.raises(ValueError, match="duplicate"):
        module.validate_evidence_rows([rows[0], rows[0]], expected, "fast")
    with pytest.raises(ValueError, match="degraded"):
        module.validate_evidence_rows(
            [{**rows[0], "degraded": True}, rows[1]], expected, "fast"
        )
    with pytest.raises(ValueError, match="empty evidence"):
        module.validate_evidence_rows(
            [{**rows[0], "evidence": []}, rows[1]], expected, "fast"
        )


def test_runtime_calls_public_retain_then_gold_blind_recall() -> None:
    module = load_module()
    row = sample_row(0)

    class Client:
        def __init__(self):
            self.posts = []

        def bind_context(self, client_ref, **kwargs):
            assert client_ref == "horizon-item-0"
            assert kwargs["subject_ref"] == "horizon-item-0"
            return {
                "subject_id": "subject",
                "scope_id": "scope",
                "actor_id": "actor",
                "agent_node_id": "agent",
                "subject_generation": 1,
            }

        def post(self, path, payload):
            self.posts.append((path, payload))
            if path == "/v1/episodes":
                return {"episode_id": f"episode-{len(self.posts)}", "unit_ids": []}
            assert path == "/v1/recall"
            return {
                "items": [
                    {"unit_id": "unit", "body": "relevant memory", "kind": "episodic"}
                ],
                "degraded": False,
                "trace_id": "trace",
            }

    client = Client()
    items = [module.runtime_item(row)]
    bound, retained = module.retain_runtime_items(client, items)
    evidence = module.recall_runtime_items(client, items, bound, "fast", 20, 16384)

    assert retained == 2
    assert [path for path, _ in client.posts] == [
        "/v1/episodes",
        "/v1/episodes",
        "/v1/recall",
    ]
    assert evidence[0]["trace_id"] == "trace"
    assert evidence[0]["evidence"][0]["body"] == "relevant memory"
    serialized_calls = json.dumps(client.posts, sort_keys=True)
    for field in module.SCORING_ONLY_FIELDS:
        assert field not in serialized_calls
        assert str(row[field]) not in serialized_calls


def test_confirmation_reuses_one_retained_timeline_per_user() -> None:
    module = load_module()
    rows = benchmark_fixture_rows()[:2]
    rows[0] = {**rows[0], "conversation": rows[0]["conversation"] + "\n\n"}
    rows[1] = {
        **rows[1],
        "conversation": rows[1]["conversation"]
        + "\nDate: 2026-03-01T01:02:03\nScenario: newest event\n"
        + "User: One more update.\nAssistant: Noted.",
    }
    items = module.confirmation_runtime_items(rows)

    class Client:
        def __init__(self):
            self.binds = []
            self.posts = []

        def bind_context(self, client_ref, **kwargs):
            self.binds.append((client_ref, kwargs))
            return {
                "subject_id": "subject",
                "scope_id": "scope",
                "actor_id": "actor",
                "agent_node_id": "agent",
                "subject_generation": 1,
            }

        def post(self, path, payload):
            self.posts.append((path, payload))
            if path == "/v1/recall":
                return {
                    "items": [
                        {"unit_id": "unit", "body": "memory", "kind": "episodic"}
                    ],
                    "degraded": False,
                    "trace_id": "trace",
                }
            return {"episode_id": f"episode-{len(self.posts)}", "unit_ids": []}

    client = Client()
    drained = 0

    def drain():
        nonlocal drained
        episode_posts = sum(path == "/v1/episodes" for path, _ in client.posts)
        compiled = episode_posts - drained
        drained = episode_posts
        return compiled

    evidence, retained, compiled = module.build_incremental_confirmation_evidence(
        client, items, drain, k=20, budget_tokens=16384
    )

    assert items[0]["context_ref"] == items[1]["context_ref"]
    assert items[1]["episodes"][: len(items[0]["episodes"])] == items[0]["episodes"]
    assert len(client.binds) == 1
    assert retained == compiled == 7
    assert sum(path == "/v1/episodes" for path, _ in client.posts) == 7
    assert sum(path == "/v1/recall" for path, _ in client.posts) == 2
    assert [row["id"] for row in evidence] == [rows[0]["id"], rows[1]["id"]]


def test_control_character_normalization_is_lossless_and_postgres_safe() -> None:
    module = load_module()
    source = "Clock\x0027s\x07 ticking\nnext\tline"

    normalized = module.normalize_source_text(source)

    assert normalized == "Clock\\u000027s\\u0007 ticking\nnext\tline"
    assert "\x00" not in normalized
    assert "\x07" not in normalized
    assert module.restore_source_text(normalized) == source


def test_fast_gate_carries_nondecisional_evidence_contract() -> None:
    module = load_module()

    contract = module.fast_gate_evidence_contract(
        source_sha="a" * 64,
        k=20,
        budget_tokens=16384,
    )

    assert contract["schema_version"] == 1
    assert contract["decisional"] is False
    assert contract["power"]["test"] == "descriptive-only (no test)"
    assert contract["power"]["n"] == 10
    assert contract["harness"]["k"] == 20
    assert contract["corpus"]["sha256"] == "a" * 64
    assert contract["instrument_verification"]["rows_counted"] == 10


def test_paid_authorization_is_hash_bound_and_capped_at_25_dollars() -> None:
    module = load_module()
    frozen = {
        "source_jsonl_sha256": "a" * 64,
        "lock_sha256": "b" * 64,
        "fast_evidence_sha256": "c" * 64,
        "fast_gate_sha256": "d" * 64,
        "runner_sha256": "e" * 64,
        "provider_attempts_sha256": "f" * 64,
    }
    packet = module.authorization_packet(
        frozen, authorized_by="owner", authorized_at="2026-08-03T00:00:00Z"
    )

    module.validate_pilot_authorization(packet, frozen)
    assert packet["hard_limits"]["reader"]["max_spend_usd"] == "22"
    assert packet["hard_limits"]["deep"]["max_spend_usd"] == "3"
    assert packet["hard_limits"]["combined_max_spend_usd"] == "25"

    packet["frozen_inputs"]["source_jsonl_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="authorization scope"):
        module.validate_pilot_authorization(packet, frozen)


def test_confirmation_authorization_is_two_arm_hash_bound_and_capped_at_92() -> None:
    module = load_module()
    frozen = {
        "source_jsonl_sha256": "a" * 64,
        "selection_sha256": "b" * 64,
        "fast_evidence_sha256": "c" * 64,
        "fast_gate_sha256": "d" * 64,
        "runner_sha256": "e" * 64,
        "provider_attempts_sha256": "f" * 64,
    }
    packet = module.confirmation_authorization_packet(
        frozen, authorized_by="owner", authorized_at="2026-08-03T00:00:00Z"
    )

    module.validate_confirmation_authorization(packet, frozen)
    assert packet["hard_limits"] == {
        "max_logical_calls": 240,
        "max_provider_attempts": 480,
        "max_spend_usd": "92",
    }
    assert packet["arms"] == ["full_context", "fast"]
    assert "deep" not in json.dumps(packet).lower()

    packet["hard_limits"]["max_spend_usd"] = "93"
    with pytest.raises(ValueError, match="authorization scope"):
        module.validate_confirmation_authorization(packet, frozen)


def test_selective_routing_uses_fast_answer_or_requires_completed_deep() -> None:
    module = load_module()

    assert (
        module.selective_route({"status": "completed", "answer": "B", "abstain": False})
        == "fast"
    )
    assert (
        module.selective_route({"status": "completed", "answer": None, "abstain": True})
        == "deep"
    )
    module.validate_deep_completion(
        {
            "degraded": False,
            "evidence": [{"body": "current preference"}],
            "deep": {
                "status": "completed",
                "settled_micros": 10,
                "unsettled_micros_upper_bound": 0,
            },
        }
    )
    with pytest.raises(ValueError, match="completed"):
        module.validate_deep_completion(
            {
                "degraded": False,
                "evidence": [{"body": "partial"}],
                "deep": {
                    "status": "partial",
                    "settled_micros": 10,
                    "unsettled_micros_upper_bound": 0,
                },
            }
        )


def test_terminal_rows_reject_duplicates_and_missing_arms() -> None:
    module = load_module()
    rows = [
        {"id": item_id, "arm": arm, "status": "completed"}
        for item_id in ("one", "two")
        for arm in module.PAID_ARMS
    ]

    module.validate_terminal_rows(rows, ["one", "two"])
    with pytest.raises(ValueError, match="duplicate"):
        module.validate_terminal_rows([*rows, rows[0]], ["one", "two"])
    with pytest.raises(ValueError, match="terminal rows"):
        module.validate_terminal_rows(rows[:-1], ["one", "two"])


def test_analysis_joins_gold_last_and_applies_preregistered_verdict() -> None:
    module = load_module()
    source = [sample_row(0), sample_row(1)]
    source[0].update(correct_letter="A", distractor_letter="B", has_evolved=True)
    source[1].update(correct_letter="C", distractor_letter="", has_evolved=False)
    predictions = []
    for item, full, fast in zip(source, ("B", "C"), ("A", "D"), strict=True):
        predictions.extend(
            [
                {
                    "id": item["id"],
                    "arm": "full_context",
                    "status": "completed",
                    "answer": full,
                    "abstain": False,
                },
                {
                    "id": item["id"],
                    "arm": "fast",
                    "status": "completed",
                    "answer": fast,
                    "abstain": False,
                },
                {
                    "id": item["id"],
                    "arm": "selective_deep",
                    "status": "completed",
                    "answer": fast,
                    "abstain": False,
                    "route": "fast",
                },
            ]
        )

    first = module.analyze_paid_rows(
        source, predictions, bootstrap_seed=7, bootstrap_samples=1000
    )
    second = module.analyze_paid_rows(
        source, predictions, bootstrap_seed=7, bootstrap_samples=1000
    )

    assert first == second
    assert first["arms"]["full_context"]["correct"] == 1
    assert first["arms"]["selective_deep"]["correct"] == 1
    assert first["arms"]["full_context"]["evolved_distractor_selections"] == 1
    assert first["arms"]["selective_deep"]["evolved_distractor_selections"] == 0
    assert first["paired_selective_vs_full"] == {
        **first["paired_selective_vs_full"],
        "gains": 1,
        "losses": 1,
        "discordant": 2,
    }
    assert first["verdict"]["outcome"] == "advance_to_powered_plan"

    with pytest.raises(ValueError, match="terminal rows"):
        module.analyze_paid_rows(source, predictions[:-1])


def test_pilot_contract_stays_nondecisional_below_mcnemar_floor() -> None:
    module = load_module()
    analysis = {"paired_selective_vs_full": {"gains": 1, "losses": 1, "discordant": 2}}

    contract = module.pilot_evidence_contract("a" * 64, analysis)

    assert contract["decisional"] is False
    assert contract["power"]["n_d"] == 2
    assert contract["power"]["test"] == "two-sided exact (conditional binomial) McNemar"


def test_confirmation_analysis_applies_all_preregistered_gates_by_user() -> None:
    module = load_module()
    source = benchmark_fixture_rows()
    predictions = []
    for row in source:
        full_answer = "B" if row["has_evolved"] else "A"
        predictions.extend(
            [
                {
                    "id": row["id"],
                    "arm": "full_context",
                    "status": "completed",
                    "answer": full_answer,
                    "abstain": False,
                },
                {
                    "id": row["id"],
                    "arm": "fast",
                    "status": "completed",
                    "answer": "A",
                    "abstain": False,
                },
            ]
        )

    result = module.analyze_confirmation_rows(
        source, predictions, bootstrap_seed=11, bootstrap_samples=1000
    )

    assert result["paired_fast_vs_full"]["gains"] == 6
    assert result["paired_fast_vs_full"]["losses"] == 0
    assert result["paired_fast_vs_full"]["discordant"] == 6
    assert result["deltas"]["overall"] == 0.5
    assert result["deltas"]["evolved"] == 1.0
    assert result["evolved_distractor_selections"] == {"full_context": 6, "fast": 0}
    assert result["verdict"] == {
        "complete": True,
        "overall_noninferior": True,
        "evolved_positive": True,
        "evolved_distractors_not_increased": True,
        "discordance_sufficient": True,
        "outcome": "pass",
    }
