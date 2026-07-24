from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "run_forgeteval", ROOT / "scripts/run_forgeteval.py"
)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class FakeClient:
    def __init__(self) -> None:
        self.posts = []
        self.recall_items = [{"unit_id": "unit-1", "body": "old"}]

    def bind_context(self, *_args, **_kwargs):
        return {
            "subject_id": "s",
            "scope_id": "scope",
            "actor_id": "a",
            "agent_node_id": "n",
            "subject_generation": 0,
        }

    def post(self, path, body):
        self.posts.append((path, body))
        if path == "/v1/episodes":
            return {"unit_ids": ["unit-written"]}
        if path == "/v1/recall":
            return {"trace_id": "trace-1", "items": self.recall_items, "degraded": False}
        if path == "/v1/correct":
            return {"superseded": [body["selector"]["memory_unit_id"]]}
        if path == "/v1/forget":
            return {"invalidated_units": [body["selector"]["memory_unit_id"]]}
        raise AssertionError(path)

    def get(self, path):
        self.posts.append((path, None))
        return {"candidates": []}


def adapter():
    client = FakeClient()
    value = module.MemphantForgetEvalAdapter(client, lambda: None)
    value.reset()
    return value, client


def test_public_operation_mapping_is_exact_and_bound() -> None:
    value, client = adapter()
    assert value.inscribe("fact") == "unit-written"
    value.supersede("old", "new")
    assert value.release("new") == 1
    paths = [path for path, _ in client.posts]
    assert paths[:2] == ["/v1/episodes", "/v1/recall"]
    assert paths[2].startswith("/v1/traces/")
    assert paths[3:5] == ["/v1/correct", "/v1/recall"]
    assert paths[5].startswith("/v1/traces/")
    assert paths[6] == "/v1/forget"
    forget = client.posts[-1][1]
    assert forget["selector"] == {"scope_id": "scope", "memory_unit_id": "unit-1"}
    assert "tenant_id" not in forget


def test_release_falls_back_to_rank_one_without_trace_scores_and_purge_is_na() -> None:
    value, client = adapter()
    client.recall_items = [
        {"unit_id": "first", "body": "target"},
        {"unit_id": "second", "body": "unrelated"},
    ]
    assert value.release("target") == 1
    assert client.posts[-1][1]["selector"]["memory_unit_id"] == "first"
    with pytest.raises(NotImplementedError, match="selective hard purge"):
        value.purge("target")


def test_release_uses_adaptive_gap_over_trace_scores() -> None:
    value, client = adapter()
    client.recall_items = [
        {"unit_id": "first", "body": "target one"},
        {"unit_id": "second", "body": "target two"},
        {"unit_id": "third", "body": "distractor"},
    ]

    def trace(_path):
        return {
            "candidates": [
                {"unit_id": "first", "fused_score": 0.90},
                {"unit_id": "second", "fused_score": 0.86},
                {"unit_id": "third", "fused_score": 0.20},
            ]
        }

    client.get = trace
    assert value.release("target") == 2


def test_release_rank_one_never_expands_over_trace_scores() -> None:
    client = FakeClient()
    value = module.MemphantForgetEvalAdapter(
        client, lambda: None, release_selection="rank_one"
    )
    value.reset()
    client.recall_items = [
        {"unit_id": "first", "body": "target one"},
        {"unit_id": "second", "body": "target two"},
    ]
    client.get = lambda _path: {
        "candidates": [
            {"unit_id": "first", "fused_score": 0.90},
            {"unit_id": "second", "fused_score": 0.89},
        ]
    }

    assert value.release("target") == 1
    release = value.case_records[0]["operations"][-1]
    assert release["selection_strategy"] == "rank_one"
    assert release["selected_unit_ids"] == ["first"]


def test_release_selection_rejects_unknown_strategy() -> None:
    with pytest.raises(ValueError, match="release_selection"):
        module.MemphantForgetEvalAdapter(
            FakeClient(), lambda: None, release_selection="unbounded"
        )


def test_cross_rerank_requires_success_provenance() -> None:
    client = FakeClient()
    value = module.MemphantForgetEvalAdapter(
        client, lambda: None, cross_rerank=True
    )
    value.reset()
    client.get = lambda _path: {
        "candidates": [],
        "cross_rerank": {
            "provider": "fastembed",
            "model": "BAAI/bge-reranker-base",
            "failure": "none",
        },
    }

    value.recall_texts("target")
    recall = value.case_records[0]["operations"][-1]
    assert recall["cross_rerank"]["provider"] == "fastembed"

    client.get = lambda _path: {"candidates": []}
    with pytest.raises(RuntimeError, match="omitted provenance"):
        value.recall_texts("target")


def test_proposal_input_is_stable_and_confirmation_selects_by_body_hash() -> None:
    query = "user employer"
    new_text = "User now works at Anthropic."
    bodies = [
        {"unit_id": "wrong", "body": "User plays cello."},
        {"unit_id": "right", "body": "User works at Stripe."},
    ]
    unsigned = {
        "case_id": "case-a",
        "mutation_index": 1,
        "operation": "supersede",
        "query": query,
        "new_text": new_text,
        "candidates": [
            {
                "index": index,
                "body": row["body"],
                "body_sha256": module.hashlib.sha256(row["body"].encode()).hexdigest(),
            }
            for index, row in enumerate(bodies)
        ],
    }
    input_sha256 = module.sha256_json(unsigned)
    right_sha256 = unsigned["candidates"][1]["body_sha256"]
    confirmation = {
        "input_sha256": input_sha256,
        "case_id": "case-a",
        "operation": "supersede",
        "confirmed": True,
        "confirmed_by": "fixture-reviewer",
        "selected_body_sha256": [right_sha256],
        "replacement_text": new_text,
    }
    client = FakeClient()
    client.recall_items = bodies
    value = module.MemphantForgetEvalAdapter(
        client,
        lambda: None,
        case_ids=["case-a"],
        confirmations={input_sha256: confirmation},
    )
    value.reset()

    value.supersede(query, new_text)

    correct = next(body for path, body in client.posts if path == "/v1/correct")
    assert correct["selector"]["memory_unit_id"] == "right"
    assert value.proposal_inputs[0]["input_sha256"] == input_sha256


def test_missing_or_ambiguous_confirmation_fails_closed() -> None:
    client = FakeClient()
    client.recall_items = [
        {"unit_id": "one", "body": "duplicate"},
        {"unit_id": "two", "body": "duplicate"},
    ]
    value = module.MemphantForgetEvalAdapter(
        client,
        lambda: None,
        case_ids=["case-a"],
        confirmations={"unused": {}},
    )
    value.reset()
    with pytest.raises(RuntimeError, match="missing explicit confirmation"):
        value.release("target")


def test_confirmation_ledger_rejects_unconfirmed_or_duplicate_rows(tmp_path) -> None:
    path = tmp_path / "confirmations.json"
    digest = "a" * 64
    path.write_text(
        module.json.dumps(
            {
                "schema_version": 1,
                "confirmations": [
                    {
                        "input_sha256": digest,
                        "confirmed": False,
                        "confirmed_by": None,
                        "selected_body_sha256": [],
                    }
                ],
            }
        )
    )
    with pytest.raises(ValueError, match="unconfirmed proposal"):
        module.load_confirmation_ledger(path)


def test_recall_degradation_fails_closed() -> None:
    value, client = adapter()

    def degraded(path, body):
        assert path == "/v1/recall"
        return {"items": [], "degraded": True}

    client.post = degraded
    with pytest.raises(RuntimeError, match="degraded"):
        value.recall_texts("query")


def test_summary_separates_failures_from_not_applicable() -> None:
    summary = {
        "by_family": {
            "purge": [("a", False, "N/A (capability not supported)")],
            "drift": [("b", True, None), ("c", False, None)],
        },
        "wall_seconds": 1.25,
    }
    result = module.summarize(summary)
    assert result == {
        "families": {
            "purge": {"passed": 0, "failed": 0, "not_applicable": 1, "total": 1},
            "drift": {"passed": 1, "failed": 1, "not_applicable": 0, "total": 2},
        },
        "passed": 1,
        "failed": 1,
        "not_applicable": 1,
        "total": 3,
        "wall_seconds": 1.25,
    }


def test_case_rows_are_ordered_sanitized_and_explain_assertion_failures() -> None:
    cases = [
        SimpleNamespace(
            id="adv_prefix_collision_01",
            family="purge",
            mutations=[("purge", "secret")],
            must_contain=["survivor"],
            must_not_contain=["secret"],
        ),
        SimpleNamespace(
            id="adv_temporal_qualifier_01",
            family="drift",
            mutations=[("supersede", "old", "new")],
            must_contain=["new"],
            must_not_contain=["old"],
        ),
    ]
    summary = {
        "by_family": {
            "purge": [(cases[0].id, False, "N/A (private absolute path)")],
            "drift": [(cases[1].id, False, None)],
        },
        "wall_seconds": 0.1,
    }
    records = [
        {"operations": [{"operation": "purge"}], "_final_texts": []},
        {"operations": [{"operation": "supersede"}], "_final_texts": ["old"]},
    ]

    rows = module.case_rows(summary, cases, records)

    assert [row["case_id"] for row in rows] == [case.id for case in cases]
    assert rows[0]["outcome"] == "not_applicable"
    assert rows[0]["error_kind"] == "not_supported"
    assert rows[1]["outcome"] == "fail"
    assert rows[1]["missing_must_contain_indexes"] == [0]
    assert rows[1]["present_must_not_contain_indexes"] == [0]
    assert "private absolute path" not in str(rows)


def test_decision_trace_hashes_bodies_and_captures_forget_receipt() -> None:
    value, client = adapter()
    client.recall_items = [{"unit_id": "unit-1", "body": "sensitive body"}]
    client.get = lambda _path: {
        "candidates": [{"unit_id": "unit-1", "fused_score": 0.9}]
    }

    assert value.release("remove target") == 1

    operations = value.case_records[0]["operations"]
    recall = next(row for row in operations if row["operation"] == "recall")
    release = next(row for row in operations if row["operation"] == "release")
    assert recall["returned"] == [
        {
            "unit_id": "unit-1",
            "body_sha256": module.hashlib.sha256(b"sensitive body").hexdigest(),
            "fused_score": 0.9,
        }
    ]
    assert "sensitive body" not in str(operations)
    assert release["selected_unit_ids"] == ["unit-1"]
    assert release["receipts"][0]["invalidated_units"] == ["unit-1"]


def test_case_selection_is_exact_ordered_and_fail_closed() -> None:
    cases = [SimpleNamespace(id="a"), SimpleNamespace(id="b")]
    assert [case.id for case in module.select_cases(cases, ["b", "a"])] == ["b", "a"]
    with pytest.raises(ValueError, match="unique"):
        module.select_cases(cases, ["a", "a"])
    with pytest.raises(ValueError, match="unknown"):
        module.select_cases(cases, ["missing"])


def test_runtime_provenance_binds_tracked_tree_and_migrations() -> None:
    repository = module.repository_identity(ROOT)
    migrations = module.migration_identity(ROOT)

    assert len(repository["git_head"]) == 40
    assert repository["tracked_file_count"] > 0
    assert len(repository["tracked_worktree_sha256"]) == 64
    assert len(repository["tracked_diff_sha256"]) == 64
    assert migrations["files"]
    assert all(row["path"].endswith(".sql") for row in migrations["files"])
    assert len(migrations["aggregate_sha256"]) == 64
