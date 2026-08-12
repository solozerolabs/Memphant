import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import benchmarks.xs_crosssession.outcome_coupled_evolution as outcome_module

from benchmarks.xs_crosssession.outcome_coupled_evolution import (
    BudgetLedger,
    CompactionError,
    JsonlSpool,
    active_context,
    blind_arms,
    build_chronological_cases,
    classify_scopes,
    grade_liveness,
    gate_verdict,
    pack_for_policy,
    mine_correction_candidates,
    pinned_model_used,
    reconstruct_compactions,
    run_action_look,
    score_explicit_staging,
    score_next_action,
    action_look_verdict,
    admission_pack,
    locked_control_cells,
    score_unmasked_gate,
    select_first_scored_action,
    should_dispatch,
    coding_replay_verdict,
    delivery_context,
    extract_stream_evidence,
    grade_coding_replay,
    project_triggered_lessons,
    prepare_coding_replay,
    prepare_coding_replay_expansion,
    run_coding_replay,
    run_coding_replay_expansion,
    requested_end_state,
    regrade_coding_replay_expansion,
    run_silent_shadow_readiness,
    run_real_silent_shadow_campaign,
    prepare_real_silent_shadow_tasks,
    execute_real_silent_shadow_tasks,
)


def test_reconstructs_summary_preserved_messages_and_active_tail():
    rows = [
        {"uuid": "head", "type": "user", "message": {"role": "user", "content": "old"}},
        {"uuid": "tail", "type": "assistant", "message": {"role": "assistant", "content": "done"}},
        {
            "uuid": "boundary",
            "type": "system",
            "subtype": "compact_boundary",
            "compactMetadata": {
                "preTokens": 100,
                "postTokens": 40,
                "cumulativeDroppedTokens": 60,
                "preservedMessages": {
                    "anchorUuid": "summary",
                    "uuids": ["head", "tail"],
                    "allUuids": ["head", "opaque-chain-node", "tail"],
                },
            },
        },
        {
            "uuid": "summary",
            "type": "user",
            "isCompactSummary": True,
            "message": {"role": "user", "content": "summary text"},
        },
        {"uuid": "next", "type": "user", "message": {"role": "user", "content": "next task"}},
    ]

    [cut] = reconstruct_compactions(rows)

    assert cut["summary"]["uuid"] == "summary"
    assert [row["uuid"] for row in cut["preserved"]] == ["head", "tail"]
    assert [row["uuid"] for row in cut["active_tail"]] == ["next"]
    assert cut["token_metadata_valid"] is True


def test_ten_task_silent_shadow_is_restart_safe_linked_and_content_free(tmp_path: Path):
    artifact = run_silent_shadow_readiness(
        tmp_path / "shadow.jsonl",
        tmp_path / "proof.json",
    )

    assert artifact["verdict"] == "SILENT_SHADOW_READINESS_PASS"
    assert artifact["tasks_exercised"] == 10
    assert artifact["outcome_to_exposure_links"] == 10
    assert artifact["root_task_continuity"] is True
    assert artifact["spool"] == {
        "accepted_records": 20,
        "duplicate_replays": 1,
        "fully_drained": True,
        "restart_replayed": True,
    }
    assert artifact["privacy"] == {
        "raw_content_fields": 0,
        "raw_content_matches": 0,
    }
    assert artifact["silent_policy"] == {
        "automatic_lifecycle_changes": 0,
        "byte_identical_recomputations": 10,
        "prompt_changes": 0,
    }
    assert not (tmp_path / "shadow.jsonl").read_text()


def test_shadow_spool_rejects_raw_content_before_writing(tmp_path: Path):
    spool = JsonlSpool(tmp_path / "shadow.jsonl")
    body = {
        "subject_id": "subject",
        "scope_id": "scope",
        "actor_id": "actor",
        "agent_node_id": "agent",
        "subject_generation": 1,
        "task_id": "task",
        "events": [],
        "prompt": "private prompt",
    }

    with pytest.raises(ValueError, match="raw prompt"):
        spool.enqueue("/v1/task-memory-events", "event-1", body)

    assert not spool.path.read_text()


def test_shadow_spool_repairs_only_a_torn_final_append(tmp_path: Path):
    spool = JsonlSpool(tmp_path / "shadow.jsonl")
    body = {
        "subject_id": "subject",
        "scope_id": "scope",
        "actor_id": "actor",
        "agent_node_id": "agent",
        "subject_generation": 1,
        "task_id": "task",
        "events": [],
    }
    spool.enqueue("/v1/task-memory-events", "event-1", body)
    with spool.path.open("a") as handle:
        handle.write('{"endpoint"')

    JsonlSpool(spool.path).enqueue("/v1/task-memory-events", "event-2", body)
    accepted = []
    JsonlSpool(spool.path).drain(accepted.append)

    assert [record["idempotency_key"] for record in accepted] == ["event-1", "event-2"]


def _real_shadow_receipts() -> list[dict]:
    receipts = []
    for index in range(1, 11):
        unit_id = f"60000000-0000-4000-8000-{index:012d}"
        digest = f"{index:064x}"
        receipts.append(
            {
                "task_id": f"50000000-0000-4000-8000-{index:012d}",
                "subject_id": "10000000-0000-4000-8000-000000000001",
                "scope_id": "20000000-0000-4000-8000-000000000001",
                "actor_id": "30000000-0000-4000-8000-000000000001",
                "agent_node_id": "40000000-0000-4000-8000-000000000001",
                "subject_generation": 1,
                "harness_id": "silent-shadow-real-v1",
                "model_id": "claude-opus-4-1-20250805",
                "started_at": f"2026-08-11T01:{index:02d}:00Z",
                "ended_at": f"2026-08-11T01:{index:02d}:30Z",
                "completion_status": "completed",
                "validator_status": "passed",
                "requested_end_state_pass": True,
                "tool_count": index,
                "failure_count": 0,
                "retry_count": 0,
                "cost_usd": 0.1,
                "planned_files": [f"task_{index}.py"],
                "actual_files": [f"task_{index}.py"],
                "transcript_sha256": digest,
                "preregistered_prompt_sha256": digest,
                "dispatched_prompt_sha256": digest,
                "lifecycle_before_sha256": "a" * 64,
                "lifecycle_after_sha256": "a" * 64,
                "shown_unit_ids": [unit_id],
                "activated_unit_ids": [],
                "events": [
                    {
                        "unit_id": unit_id,
                        "event": "helpful",
                        "attribution": "deterministic_scorer",
                    }
                ],
            }
        )
    return receipts


def test_real_silent_shadow_requires_ten_validated_linked_private_safe_receipts(tmp_path: Path):
    accepted = {}

    def post(record):
        accepted.setdefault(record["idempotency_key"], record)

    artifact = run_real_silent_shadow_campaign(
        _real_shadow_receipts(),
        tmp_path / "shadow.jsonl",
        tmp_path / "proof.json",
        post,
    )

    assert artifact["verdict"] == "SILENT_SHADOW_REAL_TASK_READINESS_PASS"
    assert artifact["tasks_exercised"] == 10
    assert artifact["validator_backed_end_states"] == 10
    assert artifact["outcome_to_exposure_links"] == 10
    assert artifact["spool"] == {
        "accepted_records": 20,
        "duplicate_replays": 1,
        "fully_drained": True,
        "restart_replayed": True,
    }
    assert artifact["privacy"] == {"raw_content_fields": 0, "raw_content_matches": 0}
    assert artifact["silent_policy"] == {
        "automatic_lifecycle_changes": 0,
        "byte_identical_lifecycle_receipts": 10,
        "byte_identical_prompt_receipts": 10,
        "prompt_changes": 0,
    }
    assert artifact["evidence_contract"]["harness"]["budget"] == pytest.approx(1.0)
    assert len(accepted) == 20
    assert not (tmp_path / "shadow.jsonl").read_text()


def test_real_silent_shadow_preregisters_ten_private_tasks_without_public_prompts(tmp_path: Path):
    public = tmp_path / "prereg.json"
    artifact = prepare_real_silent_shadow_tasks(tmp_path / "private", public)

    assert artifact["status"] == "preregistered"
    assert artifact["tasks_preregistered"] == 10
    assert len(artifact["tasks"]) == 10
    assert all(set(task) == {"task_id", "case_id", "prompt_sha256"} for task in artifact["tasks"])
    assert "Fix calculator.add" not in public.read_text()
    manifest = json.loads((tmp_path / "private" / "real-silent-shadow-manifest.json").read_text())
    assert len(manifest["tasks"]) == 10
    assert all(task["prompt"] for task in manifest["tasks"])


def test_real_silent_shadow_executes_preregistered_prompt_without_context_injection(
    tmp_path: Path, monkeypatch
):
    private = tmp_path / "private"
    public = tmp_path / "prereg.json"
    prepare_real_silent_shadow_tasks(private, public)
    observed = []

    def dispatch(cell, manifest, private_dir, claude_path):
        observed.append((cell["prompt"], cell["context"], manifest["model"], claude_path))
        return (
            {
                "valid": True,
                "passed": False,
                "validator_pass": True,
                "requested_end_state_pass": True,
                "cost_usd": 0.01,
                "tool_count": 2,
                "response_sha256": "f" * 64,
                "actual_files": ["src/change.py"],
            },
            0.01,
        )

    monkeypatch.setattr(outcome_module, "_dispatch_coding_cell", dispatch)
    receipts = execute_real_silent_shadow_tasks(
        private,
        public,
        "/usr/local/bin/claude",
        {
            "subject_id": "10000000-0000-4000-8000-000000000001",
            "scope_id": "20000000-0000-4000-8000-000000000001",
            "actor_id": "30000000-0000-4000-8000-000000000001",
            "agent_node_id": "40000000-0000-4000-8000-000000000001",
            "subject_generation": 1,
        },
        "60000000-0000-4000-8000-000000000001",
    )

    assert len(receipts) == 10
    assert all(context == "" for _, context, _, _ in observed)
    assert [outcome_module._hash_text(prompt) for prompt, *_ in observed] == [
        receipt["dispatched_prompt_sha256"] for receipt in receipts
    ]
    assert all(receipt["validator_status"] == "passed" for receipt in receipts)


def test_real_silent_shadow_resumes_settled_task_without_redispatch(tmp_path: Path, monkeypatch):
    private = tmp_path / "private"
    public = tmp_path / "prereg.json"
    prepare_real_silent_shadow_tasks(private, public)
    (private / "real-shadow-task-01.dispatch.json").write_text('{"state":"settled"}\n')
    (private / "real-shadow-task-01.response.json").write_text("{}\n")
    (private / "runs" / "real-shadow-task-01").mkdir(parents=True)
    dispatched = []

    result = {
        "valid": True,
        "passed": False,
        "validator_pass": True,
        "requested_end_state_pass": True,
        "cost_usd": 0.01,
        "tool_count": 2,
        "response_sha256": "f" * 64,
        "actual_files": ["src/change.py"],
    }
    monkeypatch.setattr(outcome_module, "_evaluate_coding_cell", lambda *args: (result, 0.01))

    def dispatch(*args):
        dispatched.append(args[0]["cell_id"])
        return result, 0.01

    monkeypatch.setattr(outcome_module, "_dispatch_coding_cell", dispatch)
    receipts = execute_real_silent_shadow_tasks(
        private,
        public,
        "/usr/local/bin/claude",
        {
            "subject_id": "10000000-0000-4000-8000-000000000001",
            "scope_id": "20000000-0000-4000-8000-000000000001",
            "actor_id": "30000000-0000-4000-8000-000000000001",
            "agent_node_id": "40000000-0000-4000-8000-000000000001",
            "subject_generation": 1,
        },
        "60000000-0000-4000-8000-000000000001",
    )

    assert len(receipts) == 10
    assert dispatched == [f"real-shadow-task-{index:02d}" for index in range(2, 11)]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda rows: rows.pop(), "exactly ten"),
        (lambda rows: rows[0].update(validator_status="failed"), "validator-backed"),
        (lambda rows: rows[0].update(dispatched_prompt_sha256="f" * 64), "prompt bytes"),
        (lambda rows: rows[0].update(events=[]), "exposure linkage"),
        (lambda rows: rows[0].update(prompt="private"), "raw prompt"),
    ],
)
def test_real_silent_shadow_fails_closed_before_spooling(tmp_path: Path, mutate, message: str):
    receipts = _real_shadow_receipts()
    mutate(receipts)

    with pytest.raises(ValueError, match=message):
        run_real_silent_shadow_campaign(
            receipts,
            tmp_path / "shadow.jsonl",
            tmp_path / "proof.json",
            lambda record: None,
        )

    assert not (tmp_path / "proof.json").exists()
    assert not (tmp_path / "shadow.jsonl").exists()


def test_active_context_contains_compact_summary_once_and_stops_before_action():
    rows = [
        {"uuid": "old", "message": {"role": "user", "content": "old task"}},
        {
            "uuid": "boundary",
            "subtype": "compact_boundary",
            "compactMetadata": {
                "preTokens": 100,
                "postTokens": 40,
                "cumulativeDroppedTokens": 60,
                "preservedMessages": {"anchorUuid": "summary", "uuids": ["old"]},
            },
        },
        {
            "uuid": "summary",
            "isCompactSummary": True,
            "message": {"role": "user", "content": "summary text"},
        },
        {"uuid": "task", "message": {"role": "user", "content": "new task"}},
        {"uuid": "action", "message": {"role": "assistant", "content": "bad action"}},
    ]

    context = active_context(rows, "action")

    assert [message["content"] for message in context] == ["old task", "summary text", "new task"]


def test_reconstruction_fails_closed_on_missing_anchor_or_bad_token_math():
    boundary = {
        "uuid": "boundary",
        "type": "system",
        "subtype": "compact_boundary",
        "compactMetadata": {
            "preTokens": 100,
            "postTokens": 41,
            "cumulativeDroppedTokens": 60,
            "preservedMessages": {"anchorUuid": "missing", "allUuids": []},
        },
    }
    with pytest.raises(CompactionError):
        reconstruct_compactions([boundary])


def test_compaction_dropped_tokens_are_cumulative_across_boundaries():
    rows = []
    for index, (pre, post, cumulative) in enumerate(((100, 40, 60), (90, 30, 120)), 1):
        rows.extend(
            [
                {
                    "uuid": f"boundary-{index}",
                    "type": "system",
                    "subtype": "compact_boundary",
                    "compactMetadata": {
                        "preTokens": pre,
                        "postTokens": post,
                        "cumulativeDroppedTokens": cumulative,
                        "preservedMessages": {
                            "anchorUuid": f"summary-{index}",
                            "uuids": [],
                            "allUuids": ["opaque-chain-node"],
                        },
                    },
                },
                {
                    "uuid": f"summary-{index}",
                    "type": "user",
                    "isCompactSummary": True,
                    "message": {"role": "user", "content": "summary"},
                },
            ]
        )

    assert len(reconstruct_compactions(rows)) == 2


def test_partial_transcript_can_start_after_an_ancestor_compaction():
    rows = [
        {
            "uuid": "boundary",
            "type": "system",
            "subtype": "compact_boundary",
            "compactMetadata": {
                "preTokens": 90,
                "postTokens": 30,
                "cumulativeDroppedTokens": 120,
                "preservedMessages": {"anchorUuid": "summary", "uuids": []},
            },
        },
        {
            "uuid": "summary",
            "type": "user",
            "isCompactSummary": True,
            "message": {"role": "user", "content": "summary"},
        },
    ]

    [cut] = reconstruct_compactions(rows)

    assert cut["dropped_tokens"] == 60
    assert cut["prior_cumulative_dropped_tokens"] == 60


def test_scope_qualification_requires_four_distinct_chronological_objective_cases():
    valid = [
        {
            "case_id": f"a{i}",
            "kind": "adherence",
            "learned_at": f"2026-08-0{i}T00:00:00Z",
            "held_out_at": f"2026-08-0{i}T01:00:00Z",
            "source_task_hash": f"source-{i}",
            "held_out_task_hash": f"held-{i}",
            "objective_predicate": "tool_name == Edit",
            "rule_version": "sha256:rule",
            "context_boundary": "compact:1",
            "sensitive": False,
        }
        for i in range(1, 5)
    ]
    invalid = dict(valid[0], case_id="same-task", held_out_task_hash="source-1")

    scopes = classify_scopes(valid + [invalid])

    assert scopes["A1"]["status"] == "eligible"
    assert scopes["A1"]["n_valid"] == 4
    assert scopes["A2"]["status"] == "UNTESTABLE"
    assert scopes["A3"]["status"] == "UNTESTABLE"
    assert "same-task" in scopes["A1"]["rejected_case_ids"]


def test_policy_uses_only_causal_evidence_and_silence_excludes():
    units = [
        {"unit_id": "bad", "kind": "adherence", "validated": True},
        {"unit_id": "fresh", "kind": "adherence", "validated": True},
        {"unit_id": "good", "kind": "adherence", "validated": True},
        {"unit_id": "observed", "kind": "adherence", "validated": True},
        {"unit_id": "quiet", "kind": "adherence", "validated": True},
    ]
    events = (
        [{"unit_id": "good", "event": "helpful", "attribution": "explicit_user"}] * 8
        + [{"unit_id": "bad", "event": "harmful", "attribution": "deterministic_scorer"}] * 8
        + [{"unit_id": "observed", "event": "helpful", "attribution": "observational"}] * 20
        + [{"unit_id": "quiet", "event": "silenced", "attribution": "explicit_user"}]
    )

    assert pack_for_policy(units, events, "C1") == ["bad", "fresh", "good", "observed", "quiet"]
    assert pack_for_policy(units, events, "A1") == ["good", "fresh", "observed", "bad"]


def test_identical_pack_suppression_and_budget_cutoff():
    assert should_dispatch(["u1"], ["u1"]) == "no_policy_difference"
    assert should_dispatch(["u1"], ["u2"]) == "dispatch"

    ledger = BudgetLedger(total_cap=100, phase_caps={"action": 30, "coding": 70})
    reservation = ledger.reserve("action", 12)
    ledger.settle(reservation, 10)
    ledger.reserve("action", 20)
    with pytest.raises(ValueError, match="phase budget"):
        ledger.reserve("action", 1)
    with pytest.raises(ValueError, match="total budget"):
        ledger.reserve("coding", 71)


def test_liveness_grades_the_action_before_the_correction():
    rows = [
        {"uuid": "task", "timestamp": "2026-08-01T00:00:00Z", "message": {"role": "user", "content": "do it"}},
        {
            "uuid": "action",
            "timestamp": "2026-08-01T00:01:00Z",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "name": "Bash", "input": {"command": "make check"}}],
            },
        },
        {
            "uuid": "correction",
            "timestamp": "2026-08-01T00:02:00Z",
            "message": {"role": "user", "content": "Do not run the full gate locally"},
        },
    ]

    probe = grade_liveness(rows, "full gate locally")

    assert probe["status"] == "pass"
    assert probe["historical_grade"] == "violation"
    assert probe["action_uuid"] == "action"
    assert probe["task_hash"] != probe["correction_hash"]


def test_blinding_is_stable_and_preserves_context_identity():
    first = blind_arms("case-1", "context-sha", {"C1": ["u1"], "A1": ["u2"]}, seed="v1")
    second = blind_arms("case-1", "context-sha", {"C1": ["u1"], "A1": ["u2"]}, seed="v1")

    assert first == second
    assert {cell["context_hash"] for cell in first} == {"context-sha"}
    assert {cell["blind_label"] for cell in first} == {"arm-1", "arm-2"}
    assert all("policy" not in cell for cell in first)


def test_only_objective_chronological_cross_task_pairs_become_cases():
    observations = [
        {
            "session_id": "source",
            "family": "rule",
            "objective": True,
            "timestamp": "2026-08-01T00:00:00Z",
            "task_hash": "task-a",
            "context_boundary": "session:start",
        },
        {
            "session_id": "held-out",
            "family": "rule",
            "objective": True,
            "timestamp": "2026-08-02T00:00:00Z",
            "task_hash": "task-b",
            "context_boundary": "compact:1",
        },
        {
            "session_id": "fuzzy",
            "family": "fuzzy-rule",
            "objective": False,
            "timestamp": "2026-08-03T00:00:00Z",
            "task_hash": "task-c",
            "context_boundary": "session:start",
        },
    ]

    [case] = build_chronological_cases(observations)

    assert case["source_task_hash"] == "task-a"
    assert case["held_out_task_hash"] == "task-b"
    assert case["kind"] == "adherence"


def test_gate_reasons_distinguish_broken_instrument_flat_policy_and_untestable_scope():
    untestable = {"A1": {"status": "UNTESTABLE"}}
    eligible = {"A1": {"status": "eligible"}}

    assert gate_verdict(5, 6, eligible, {"A1": "dispatch"}) == "FREE_GATE_CLOSED_INSTRUMENT_FAILED"
    assert gate_verdict(6, 6, untestable, {"A1": "dispatch"}) == "FREE_GATE_CLOSED_UNTESTABLE"
    assert gate_verdict(6, 6, eligible, {"A1": "no_policy_difference"}) == "FREE_GATE_CLOSED_NO_POLICY_DIFFERENCE"
    assert gate_verdict(6, 6, eligible, {"A1": "dispatch"}) == "FREE_GATE_OPEN"


def test_candidate_mining_filters_synthetic_turns_and_deduplicates_forks(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    row = {
        "uuid": "correction",
        "timestamp": "2026-08-07T00:00:00Z",
        "message": {"role": "user", "content": "I thought we never do that"},
    }
    (root / "one.jsonl").write_text(json.dumps(row) + "\n")
    (root / "fork.jsonl").write_text(json.dumps(row) + "\n")
    (root / "meta.jsonl").write_text(json.dumps({**row, "uuid": "meta", "isMeta": True}) + "\n")
    subagents = root / "subagents"
    subagents.mkdir()
    (subagents / "agent.jsonl").write_text(json.dumps({**row, "uuid": "agent"}) + "\n")

    result = mine_correction_candidates([root])

    assert result["candidate_turns"] == 1
    assert result["duplicate_turns"] == 1
    assert result["candidates"][0]["uuid"] == "correction"
    assert "text" in result["candidates"][0]  # private miner output only


def test_deterministic_action_scorers_grade_payload_not_regex_nomination():
    assert score_explicit_staging("git add src/a.py tests/test_a.py && git commit -m ok") == "helpful"
    assert score_explicit_staging("git add -A && git commit -m nope") == "harmful"
    assert score_explicit_staging("git status --short") is None

    assert score_unmasked_gate("pytest tests/test_a.py -q") == "helpful"
    assert score_unmasked_gate("pytest tests/ -q | tail -20") == "harmful"
    assert score_unmasked_gate("set -o pipefail; pytest tests/ -q | tail -20") == "helpful"


def test_next_action_grading_uses_structured_payload_only():
    assert score_next_action(
        "a1-continue-ed4f8502",
        {"kind": "tool_call", "tool": "Read", "command": None, "files": ["src/lib.rs"]},
    )
    assert not score_next_action(
        "a1-continue-ed4f8502",
        {"kind": "stop", "tool": None, "command": None, "files": []},
    )
    assert score_next_action(
        "a1-explicit-stage-sealed",
        {"kind": "tool_call", "tool": "Bash", "command": "git add src/a.py tests/a.py", "files": []},
    )
    assert not score_next_action(
        "a1-explicit-stage-sealed",
        {"kind": "tool_call", "tool": "Bash", "command": "git add -A", "files": []},
    )
    assert score_next_action(
        "a1-scoped-gate-9e49b76b",
        {"kind": "tool_call", "tool": "Bash", "command": "pytest tests/test_a.py -q", "files": []},
    )
    assert not score_next_action(
        "a1-scoped-gate-9e49b76b",
        {"kind": "tool_call", "tool": "Bash", "command": "cargo test --workspace --all-features", "files": []},
    )


def test_action_look_advances_only_with_three_passes_net_win_and_no_loss():
    grades = {
        "C1": [False, True, True, True],
        "A1": [True, True, True, True],
    }
    assert action_look_verdict(grades) == {
        "verdict": "ACTION_LOOK_PASS",
        "treatment_passes": 4,
        "control_passes": 3,
        "net_wins": 1,
        "losses": 0,
    }
    assert action_look_verdict({"C1": [True] * 4, "A1": [True] * 4})["verdict"] == "ACTION_LOOK_FLAT"
    assert action_look_verdict({"C1": [True, False, True, False], "A1": [False, True, True, True]})["verdict"] == "ACTION_LOOK_HARMFUL"


def test_model_pin_allows_only_small_auxiliary_validation_not_fallback():
    pinned = {
        "claude-opus-5": {"canonicalModel": "claude-opus-5", "outputTokens": 100},
        "claude-haiku-4-5-20251001": {"canonicalModel": "claude-haiku-4-5", "outputTokens": 28},
    }
    fallback = {
        **pinned,
        "claude-sonnet-5": {"canonicalModel": "claude-sonnet-5", "outputTokens": 100},
    }

    assert pinned_model_used(pinned, "claude-opus-5")
    assert not pinned_model_used(fallback, "claude-opus-5")
    assert not pinned_model_used({"claude-haiku-4-5": pinned["claude-haiku-4-5-20251001"]}, "claude-opus-5")


def test_admission_requires_triggered_unit_and_positive_explicit_outcome():
    case = {"unit_id": "explicit-staging"}
    events = [
        {"unit_id": "explicit-staging", "event": "helpful", "attribution": "explicit_user"},
        {"unit_id": "explicit-staging", "event": "harmful", "attribution": "observational"},
        {"unit_id": "irrelevant", "event": "helpful", "attribution": "explicit_user"},
    ]

    assert admission_pack(case, events) == ["explicit-staging"]
    assert admission_pack(case, events[1:]) == []


def test_admission_budget_accounts_for_settled_ordering_screen():
    ledger = BudgetLedger(
        total_cap=100,
        phase_caps={"action": 30, "coding": 70},
        _settled={"action": 15.1323885},
    )

    for _ in range(4):
        ledger.reserve("action", 2.5)
    with pytest.raises(ValueError, match="phase budget"):
        ledger.reserve("action", 5)


def test_admission_reuses_only_checksum_locked_valid_controls(tmp_path: Path):
    response = tmp_path / "case-arm.response.json"
    response.write_text("settled body")
    digest = __import__("hashlib").sha256(response.read_bytes()).hexdigest()
    artifact = {
        "cells": [
            {
                "cell_id": "case-arm",
                "case_id": "case",
                "policy": "C1",
                "valid": True,
                "passed": False,
                "response_sha256": digest,
            },
            {"cell_id": "other", "case_id": "case", "policy": "A1", "valid": True},
        ]
    }

    assert locked_control_cells(artifact, tmp_path) == {
        "case": {"cell_id": "case-arm", "passed": False, "response_sha256": digest}
    }
    response.write_text("changed")
    with pytest.raises(ValueError, match="control response drifted"):
        locked_control_cells(artifact, tmp_path)


def test_action_runner_caps_dispatches_and_refuses_repeat(tmp_path: Path, monkeypatch):
    cases = [
        "a1-continue-ed4f8502",
        "a1-continue-38ba8780",
        "a1-explicit-stage-sealed",
        "a1-scoped-gate-9e49b76b",
    ]
    cells = [
        {
            "cell_id": f"{case}-{policy}",
            "case_id": case,
            "blind_label": f"arm-{index}",
            "policy": policy,
            "context_hash": "context",
            "pack": [],
            "prompt": "private",
        }
        for case in cases
        for index, policy in enumerate(("C0", "C1", "A1"), 1)
    ]
    (tmp_path / "action-look-manifest.json").write_text(
        json.dumps({"model": "claude-opus-5", "max_cell_usd": 2.5, "cells": cells})
    )
    out = tmp_path / "public.json"
    out.write_text(
        json.dumps(
            {
                "cells": [{"cell_id": cell["cell_id"]} for cell in cells],
                "evidence_contract": {
                    "claim": "preregistered action look",
                    "power": {"b": 0, "c": 0, "n_d": 0},
                },
            }
        )
    )
    response = json.dumps(
        {
            "subtype": "success",
            "total_cost_usd": 1,
            "modelUsage": {
                "claude-opus-5": {"canonicalModel": "claude-opus-5", "outputTokens": 100}
            },
            "structured_output": {
                "kind": "tool_call",
                "tool": "Bash",
                "command": "git add src/a.py && pytest tests/test_a.py -q",
                "files": ["src/a.py"],
            },
        }
    )
    monkeypatch.setattr(
        "benchmarks.xs_crosssession.outcome_coupled_evolution.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=response, stderr=""),
    )

    result = run_action_look(str(tmp_path), str(out), "claude")

    assert result["verdict"] == "ACTION_LOOK_FLAT"
    assert result["spend_usd"] == 12
    with pytest.raises(RuntimeError, match="refusing ambiguous or repeated dispatch"):
        run_action_look(str(tmp_path), str(out), "claude")


def test_sealed_action_selection_uses_earliest_distinct_task(tmp_path: Path):
    transcript = tmp_path / "session.jsonl"
    rows = [
        {
            "uuid": "task",
            "timestamp": "2026-08-06T00:00:00Z",
            "message": {"role": "user", "content": "Commit the finished change"},
        },
        {
            "uuid": "action",
            "timestamp": "2026-08-06T00:01:00Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "input": {"command": "git add src/a.py && git commit -m done"},
                    }
                ],
            },
        },
    ]
    transcript.write_text("".join(json.dumps(row) + "\n" for row in rows))

    selected = select_first_scored_action(
        [transcript],
        "2026-08-06T00:00:00Z",
        "2026-08-08T20:50:00Z",
        score_explicit_staging,
    )

    assert selected["outcome"] == "helpful"
    assert selected["action_uuid"] == "action"
    assert selected["task_hash"] == __import__("hashlib").sha256(b"Commit the finished change").hexdigest()
    assert "command" not in selected


def test_private_text_never_enters_public_result(tmp_path: Path):
    private = tmp_path / "cases.jsonl"
    secret = "private transcript body"
    private.write_text(json.dumps({"case_id": "x", "raw_text": secret}) + "\n")

    # Public summaries are composed from allowlisted counters and hashes only.
    result = classify_scopes([])

    assert secret not in json.dumps(result)


def test_projection_delivers_only_triggered_causally_validated_lesson():
    body = "Stage only explicit file paths. Never use git add -A, git add --all, or git add dot."
    projection = {
        "items": [
            {
                "unit_id": "explicit-staging",
                "kind": "procedure",
                "body": body,
                "body_sha256": __import__("hashlib").sha256(body.encode()).hexdigest(),
                "state": "validated",
            },
            {
                "unit_id": "irrelevant",
                "kind": "procedure",
                "body": "Use the release checklist.",
                "body_sha256": "dff3a952c261f6887384262b747a901e56d92cc100be339e7e746d7c2d7cc3e1",
                "state": "validated",
            },
            {
                "unit_id": "stale",
                "kind": "procedure",
                "body": "Stage everything.",
                "body_sha256": "6983b9fefb3f62e1afd7c5294c893ba732508dc5cd5ba308f83421cace8cd236",
                "state": "superseded",
            },
        ]
    }
    events = [
        {"unit_id": "explicit-staging", "event": "helpful", "attribution": "explicit_user"},
        {"unit_id": "irrelevant", "event": "helpful", "attribution": "observational"},
        {"unit_id": "stale", "event": "helpful", "attribution": "explicit_user"},
    ]
    triggers = {
        "explicit-staging": {"prompt_regex": r"\b(stage|commit)\b"},
        "irrelevant": {"path_globs": ["deploy/**"]},
        "stale": {"prompt_regex": r"\bstage\b"},
    }

    lessons = project_triggered_lessons(
        projection, events, triggers, prompt="Finish the change and stage it", paths=["src/lib.py"]
    )

    assert lessons == [
        {
            "unit_id": "explicit-staging",
            "body": body,
            "body_sha256": "2e607c927251727f1abfc2e1de2e65b627b4321d5420e3a890eeac92b365da80",
        }
    ]
    assert delivery_context(lessons) == (
        "MemPhant project lesson (explicit-staging@2e607c927251):\n" + body
    )
    assert body == delivery_context(lessons).split("\n", 1)[1]
    assert "Finish the change" not in delivery_context(lessons)


def test_stream_evidence_captures_complete_commands_and_pinned_usage():
    rows = [
        {
            "type": "assistant",
            "message": {
                "model": "claude-opus-5",
                "content": [
                    {"type": "tool_use", "name": "Read", "input": {"file_path": "src/a.py"}},
                    {"type": "tool_use", "name": "Bash", "input": {"command": "git status --short"}},
                ],
            },
        },
        {
            "type": "assistant",
            "message": {
                "model": "claude-opus-5",
                "content": [
                    {"type": "tool_use", "name": "Bash", "input": {"command": "git add src/a.py"}}
                ],
            },
        },
        {
            "type": "result",
            "subtype": "success",
            "total_cost_usd": 1.25,
            "modelUsage": {
                "claude-opus-5": {"canonicalModel": "claude-opus-5", "outputTokens": 42}
            },
        },
    ]

    evidence = extract_stream_evidence("\n".join(json.dumps(row) for row in rows))

    assert evidence == {
        "commands": ["git status --short", "git add src/a.py"],
        "tool_count": 3,
        "cost_usd": 1.25,
        "valid": True,
    }


def test_staging_replay_attributes_preexisting_dirty_file():
    grade = grade_coding_replay(
        "explicit-staging",
        validator_pass=True,
        requested_end_state_pass=True,
        before_dirty={"notes/private.txt"},
        after_dirty={"notes/private.txt", "src/status.txt"},
        staged={"src/status.txt"},
        commands=["git status --short", "git add src/status.txt"],
        full_gate_ran=False,
    )

    assert grade == {
        "accepted_without_violation": True,
        "validator_pass": True,
        "requested_end_state_pass": True,
        "rule_violation": False,
        "new_dirty": ["src/status.txt"],
    }

    grade = grade_coding_replay(
        "explicit-staging",
        validator_pass=True,
        requested_end_state_pass=True,
        before_dirty={"notes/private.txt"},
        after_dirty={"notes/private.txt", "src/status.txt"},
        staged={"notes/private.txt", "src/status.txt"},
        commands=["git add -A"],
        full_gate_ran=False,
    )
    assert grade["accepted_without_violation"] is False
    assert grade["rule_violation"] is True


def test_scoped_gate_replay_grades_effect_not_model_explanation():
    passing = grade_coding_replay(
        "scoped-gate",
        validator_pass=True,
        requested_end_state_pass=True,
        before_dirty=set(),
        after_dirty={"calculator.py"},
        staged=set(),
        commands=["python3 -m pytest tests/test_calculator.py -q"],
        full_gate_ran=False,
    )
    violating = grade_coding_replay(
        "scoped-gate",
        validator_pass=True,
        requested_end_state_pass=True,
        before_dirty=set(),
        after_dirty={"calculator.py", ".full_gate_ran"},
        staged=set(),
        commands=["python3 -m pytest -q"],
        full_gate_ran=True,
    )

    assert passing["accepted_without_violation"] is True
    assert violating["accepted_without_violation"] is False
    assert violating["rule_violation"] is True


def test_requested_end_state_accepts_equivalent_formatter_implementation(tmp_path: Path):
    (tmp_path / "formatter.py").write_text(
        "import re\n\ndef slugify(value):\n    return re.sub(r' +', '-', value.lower())\n"
    )

    assert requested_end_state("scoped-gate-variant", tmp_path, set()) is True


def test_two_case_replay_expands_only_on_net_win_without_loss():
    assert coding_replay_verdict({"C0": [False, True], "M1": [True, True]}) == {
        "verdict": "CODING_REPLAY_EXPAND",
        "control_passes": 1,
        "treatment_passes": 2,
        "net_wins": 1,
        "losses": 0,
    }
    assert coding_replay_verdict({"C0": [True, False], "M1": [False, True]})["verdict"] == (
        "CODING_REPLAY_HARMFUL"
    )
    assert coding_replay_verdict({"C0": [True, False], "M1": [True, False]})["verdict"] == (
        "CODING_REPLAY_FLAT"
    )
    assert coding_replay_verdict(
        {"C0": [True, False, True, False], "M1": [True, True, True, True]}
    )["verdict"] == "CODING_REPLAY_PASS"


def test_coding_replay_preregisters_private_scratch_tasks_without_raw_text(tmp_path: Path):
    private = tmp_path / "private"
    public = tmp_path / "coding-replay.json"

    result = prepare_coding_replay(str(private), str(public))

    assert result["status"] == "preregistered"
    assert result["result_read"] is False
    assert result["instrument"]["repo_projection_delivery_parity"] == "pass"
    assert result["instrument"]["pre_action_boundaries"] == "pass"
    assert len(result["cells"]) == 4
    assert {cell["policy"] for cell in result["cells"]} == {"C0", "M1"}
    assert result["budget"] == {
        "phase_cap_usd": 70,
        "max_cell_usd": 5,
        "reserved_cells": 4,
        "new_reserve_usd": 20,
        "prior_spend_usd": 20.163992,
    }
    public_text = public.read_text()
    assert "Stage only explicit file paths" not in public_text
    assert "Fix calculator.add" not in public_text
    assert (private / "coding-replay-manifest.json").is_file()
    staging_status = __import__("subprocess").run(
        ["git", "status", "--porcelain"],
        cwd=private / "bases" / "explicit-staging",
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    assert staging_status == " M notes/private.txt\n"


def test_coding_replay_runs_each_cell_once_and_grades_real_worktrees(tmp_path: Path):
    private = tmp_path / "private"
    public = tmp_path / "coding-replay.json"
    fake = tmp_path / "fake-claude.py"
    fake.write_text(
        """#!/usr/bin/env python3
import json, os, pathlib, subprocess
case = os.environ["MEMPHANT_REPLAY_CASE"]
policy = os.environ["MEMPHANT_REPLAY_POLICY"]
commands = []
if case == "explicit-staging":
    pathlib.Path("src/status.txt").write_text("ready\\n")
    command = "git add src/status.txt" if policy == "M1" else "git add -A"
    subprocess.run(command.split(), check=True)
    commands.append(command)
else:
    pathlib.Path("calculator.py").write_text("def add(left, right):\\n    return left + right\\n")
    command = "python3 -m unittest tests.test_calculator" if policy == "M1" else "python3 run_tests.py"
    subprocess.run(command.split(), check=True)
    commands.append(command)
for command in commands:
    print(json.dumps({"type":"assistant","message":{"model":"claude-opus-5","content":[{"type":"tool_use","name":"Bash","input":{"command":command}}]}}))
print(json.dumps({"type":"result","subtype":"success","total_cost_usd":1.0,"modelUsage":{"claude-opus-5":{"canonicalModel":"claude-opus-5","outputTokens":10}}}))
"""
    )
    fake.chmod(0o755)
    prepare_coding_replay(str(private), str(public))

    result = run_coding_replay(str(private), str(public), str(fake))

    assert result["verdict"] == "CODING_REPLAY_EXPAND"
    assert result["comparison"] == {
        "verdict": "CODING_REPLAY_EXPAND",
        "control_passes": 0,
        "treatment_passes": 2,
        "net_wins": 2,
        "losses": 0,
    }
    assert result["new_spend_usd"] == 4.0
    assert all(cell["valid"] for cell in result["cells"])
    assert "commands" not in json.dumps(result["cells"])
    assert (private / "SHA256SUMS").is_file()
    with pytest.raises(RuntimeError, match="refusing ambiguous or repeated dispatch"):
        run_coding_replay(str(private), str(public), str(fake))


def test_coding_replay_expansion_locks_initial_result_and_combines_four_cases(tmp_path: Path):
    first_private = tmp_path / "first-private"
    first_public = tmp_path / "first.json"
    second_private = tmp_path / "second-private"
    second_public = tmp_path / "second.json"
    fake = tmp_path / "fake-claude.py"
    fake.write_text(
        """#!/usr/bin/env python3
import json, os, pathlib, subprocess
case = os.environ["MEMPHANT_REPLAY_CASE"]
policy = os.environ["MEMPHANT_REPLAY_POLICY"]
if case.startswith("explicit-staging"):
    target = "src/status.txt" if case == "explicit-staging" else "config/mode.txt"
    pathlib.Path(target).write_text("ready\\n")
    command = f"git add {target}"
else:
    target = "calculator.py" if case == "scoped-gate" else "formatter.py"
    body = "def add(left, right):\\n    return left + right\\n" if case == "scoped-gate" else "def slugify(value):\\n    return value.lower().replace(' ', '-')\\n"
    pathlib.Path(target).write_text(body)
    focused = "tests.test_calculator" if case == "scoped-gate" else "tests.test_formatter"
    command = f"python3 -m unittest {focused}" if policy == "M1" else "python3 run_tests.py"
subprocess.run(command.split(), check=True)
print(json.dumps({"type":"assistant","message":{"model":"claude-opus-5","content":[{"type":"tool_use","name":"Bash","input":{"command":command}}]}}))
print(json.dumps({"type":"result","subtype":"success","total_cost_usd":1.0,"modelUsage":{"claude-opus-5":{"canonicalModel":"claude-opus-5","outputTokens":10}}}))
"""
    )
    fake.chmod(0o755)
    prepare_coding_replay(str(first_private), str(first_public))
    run_coding_replay(str(first_private), str(first_public), str(fake))

    prereg = prepare_coding_replay_expansion(
        str(first_public), str(second_private), str(second_public)
    )

    assert prereg["status"] == "preregistered"
    assert prereg["locked_initial_result_sha256"] == __import__("hashlib").sha256(
        first_public.read_bytes()
    ).hexdigest()
    assert {cell["case_id"] for cell in prereg["cells"]} == {
        "explicit-staging-variant",
        "scoped-gate-variant",
    }
    result = run_coding_replay_expansion(
        str(first_public), str(second_private), str(second_public), str(fake)
    )
    assert result["verdict"] == "CODING_REPLAY_PASS"
    assert result["comparison"] == {
        "verdict": "CODING_REPLAY_PASS",
        "control_passes": 2,
        "treatment_passes": 4,
        "net_wins": 2,
        "losses": 0,
    }
    assert result["runtime_gate"] == "production_hook_design_open"
    regraded = regrade_coding_replay_expansion(
        str(first_public), str(second_private), str(second_public)
    )
    assert regraded["comparison"] == result["comparison"]
    assert regraded["instrument"]["end_state_scorer_amendment"] == "pass"
