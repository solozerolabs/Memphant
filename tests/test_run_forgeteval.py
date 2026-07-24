from __future__ import annotations

import importlib.util
from pathlib import Path

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
    assert paths[0:4] == ["/v1/episodes", "/v1/recall", "/v1/correct", "/v1/recall"]
    assert paths[4].startswith("/v1/traces/")
    assert paths[5] == "/v1/forget"
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
