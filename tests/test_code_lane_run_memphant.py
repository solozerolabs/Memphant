"""Unit tests for the R0-T6 code-lane runner's pure functions
(``scripts/code_lane_run_memphant.py``): the episode-body turn-formatting
convention, the gold-coverage-preserving attempt selection for
``--limit-attempts`` smoke runs, and the coverage assertion. No DB, no
server process — these run under plain ``pytest tests/``.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def clr():
    return _load("code_lane_run_memphant", "scripts/code_lane_run_memphant.py")


# --- build_episode_body -------------------------------------------------


def test_build_episode_body_role_prefixes_each_event(clr):
    events = [
        {"sequence": 0, "role": "user", "text": "please fix the bug"},
        {"sequence": 1, "role": "assistant", "text": "looking into it"},
    ]
    body = clr.build_episode_body(events)
    assert body == "user: please fix the bug\nassistant: looking into it"


def test_build_episode_body_preserves_sequence_order_as_given(clr):
    """The runner is expected to pass already sequence-sorted events (the
    corpus already stores them sorted); this function itself does not
    re-sort, it just formats in the given order."""
    events = [
        {"sequence": 2, "role": "toolResult", "text": "b"},
        {"sequence": 0, "role": "user", "text": "a"},
    ]
    body = clr.build_episode_body(events)
    assert body == "toolResult: b\nuser: a"


def test_build_episode_body_empty_events_is_empty_string(clr):
    assert clr.build_episode_body([]) == ""


# --- select_ingest_attempts / assert_gold_coverage --------------------------


def _row(attempt_id: str) -> dict:
    return {"attempt_id": attempt_id, "run_id": "r", "started_at": "t", "events": []}


def _golden(attempt_id: str, question_id: str = "q1") -> dict:
    return {
        "question_id": question_id,
        "provenance": [{"role": "answer", "attempt_id": attempt_id, "span": "x"}],
    }


def test_select_ingest_attempts_full_corpus_when_no_limit(clr):
    corpus = [_row("a1"), _row("a2"), _row("a3")]
    out = clr.select_ingest_attempts(corpus, [_golden("a1")], limit_attempts=0)
    assert out == corpus


def test_select_ingest_attempts_keeps_gold_attempts_under_limit(clr):
    corpus = [_row("a1"), _row("a2"), _row("a3"), _row("a4")]
    goldens = [_golden("a3", "q1")]
    out = clr.select_ingest_attempts(corpus, goldens, limit_attempts=2)
    ids = {row["attempt_id"] for row in out}
    assert "a3" in ids
    assert len(out) == 2


def test_select_ingest_attempts_fills_deterministically(clr):
    corpus = [_row("a1"), _row("a2"), _row("a3"), _row("a4")]
    goldens = [_golden("a3", "q1")]
    out1 = clr.select_ingest_attempts(corpus, goldens, limit_attempts=2)
    out2 = clr.select_ingest_attempts(corpus, goldens, limit_attempts=2)
    assert [r["attempt_id"] for r in out1] == [r["attempt_id"] for r in out2]


def test_select_ingest_attempts_never_drops_gold_even_if_limit_smaller(clr):
    corpus = [_row("a1"), _row("a2"), _row("a3")]
    goldens = [_golden("a1", "q1"), _golden("a2", "q2"), _golden("a3", "q3")]
    out = clr.select_ingest_attempts(corpus, goldens, limit_attempts=1)
    ids = {row["attempt_id"] for row in out}
    assert ids == {"a1", "a2", "a3"}


def test_assert_gold_coverage_passes_when_all_present(clr):
    ingested = [_row("a1"), _row("a2")]
    goldens = [_golden("a1")]
    clr.assert_gold_coverage(ingested, goldens)  # must not raise


def test_assert_gold_coverage_raises_when_missing(clr):
    ingested = [_row("a1")]
    goldens = [_golden("a2")]
    with pytest.raises(RuntimeError, match="a2"):
        clr.assert_gold_coverage(ingested, goldens)


def test_verify_input_contract_rejects_corpus_drift(clr, tmp_path):
    corpus = tmp_path / "corpus.jsonl"
    golden = tmp_path / "golden.jsonl"
    corpus.write_text(json.dumps(_row("a1")) + "\n")
    golden.write_text(json.dumps(_golden("a1")) + "\n")
    import hashlib

    lock = {
        "sha256": hashlib.sha256(golden.read_bytes()).hexdigest(),
        "bytes": golden.stat().st_size,
        "count": 1,
        "extraction": {
            "corpus_sha256": "0" * 64,
            "corpus_bytes": corpus.stat().st_size,
            "sampled_attempts": 1,
        },
    }

    with pytest.raises(RuntimeError, match="corpus sha256 mismatch"):
        clr.verify_input_contract(corpus, golden, lock)


def test_verify_input_contract_requires_exact_counts_and_pairing(clr, tmp_path):
    import hashlib

    corpus = tmp_path / "corpus.jsonl"
    golden = tmp_path / "golden.jsonl"
    corpus_row = _row("a1")
    corpus_row["events"] = [
        {"sequence": 7, "event_id": "event-7", "role": "assistant", "text": "exact span"}
    ]
    golden_row = _golden("a1")
    golden_row["provenance"][0].update(
        {"event_sequence": 7, "event_id": "event-7", "char_start": 0, "char_end": 10}
    )
    golden_row["provenance"][0]["span"] = "exact span"
    corpus.write_text(json.dumps(corpus_row) + "\n")
    golden.write_text(json.dumps(golden_row) + "\n")
    lock = {
        "sha256": hashlib.sha256(golden.read_bytes()).hexdigest(),
        "bytes": golden.stat().st_size,
        "count": 1,
        "extraction": {
            "corpus_sha256": hashlib.sha256(corpus.read_bytes()).hexdigest(),
            "corpus_bytes": corpus.stat().st_size,
            "sampled_attempts": 1,
        },
    }

    corpus_rows, goldens = clr.verify_input_contract(corpus, golden, lock)

    assert corpus_rows == [corpus_row]
    assert goldens == [golden_row]


def test_outcome_marked_arm_fails_closed_without_explicit_typed_labels(clr):
    readiness = clr.control_readiness(
        [{"attempt_id": "a1", "run_id": "r1", "started_at": "2026-01-01", "events": []}],
        [_golden("a1")],
    )

    assert readiness["verbatim_memphant"] is True
    assert readiness["outcome_marked_memphant"] is False
    assert readiness["validator_backed_held_out"] is False
    assert "explicit_outcome" in readiness["missing_fields"]
    with pytest.raises(RuntimeError, match="outcome-marked MemPhant is not paired"):
        clr.require_outcome_mark_ready(readiness)


# --- ingest payload conforms to the strict v1 contract ----------------------


class _CaptureClient:
    """Fake ApiClient that records the posted payload instead of sending it."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, dict]] = []

    def post(self, path: str, payload: dict) -> dict:
        self.posts.append((path, payload))
        return {"episode_id": "ep_test"}


def _retain_episode_schema() -> tuple[dict, dict]:
    spec = json.loads((ROOT / "openapi" / "memphant.v1.json").read_text())
    return spec, spec["components"]["schemas"]["RetainEpisodeHttpRequest"]


def _assert_object_conforms(spec: dict, name: str, schema: dict, body: dict) -> None:
    if "$ref" in schema:
        schema = spec["components"]["schemas"][schema["$ref"].split("/")[-1]]
    if "oneOf" in schema:
        errors = []
        for i, variant in enumerate(schema["oneOf"]):
            try:
                _assert_object_conforms(spec, f"{name}#{i}", variant, body)
                return
            except AssertionError as exc:
                errors.append(str(exc))
        raise AssertionError(f"{name}: no oneOf variant matched:\n" + "\n".join(errors))
    props = schema.get("properties", {})
    extra = set(body) - set(props)
    assert not extra, f"{name}: keys not in contract (would 422): {sorted(extra)}"
    missing = set(schema.get("required", [])) - set(body)
    assert not missing, f"{name}: missing required keys: {sorted(missing)}"
    for key, value in body.items():
        if isinstance(value, dict):
            _assert_object_conforms(spec, f"{name}.{key}", props[key], value)


def test_ingest_attempt_payload_conforms_to_strict_contract(clr):
    ctx = {
        "subject_id": "00000000-0000-0000-0000-0000000000a1",
        "scope_id": "00000000-0000-0000-0000-0000000000a2",
        "actor_id": "00000000-0000-0000-0000-0000000000a3",
        "agent_node_id": "00000000-0000-0000-0000-0000000000a4",
        "subject_generation": 0,
    }
    client = _CaptureClient()
    clr.ingest_attempt(
        client,
        ctx,
        {
            "attempt_id": "attempt-1",
            "started_at": "2026-01-01T00:00:00Z",
            "events": [
                {"sequence": 0, "event_id": "event-0", "role": "assistant", "text": "hi"}
            ],
        },
    )
    path, payload = client.posts[-1]
    assert path == "/v1/episodes"
    # The banned shape must be gone.
    assert "tenant_id" not in payload
    assert "subject_hint" not in payload
    assert "source_kind" not in payload  # now lives inside payload.episode
    spec, schema = _retain_episode_schema()
    _assert_object_conforms(spec, "RetainEpisodeHttpRequest", schema, payload)


def test_attempt_context_preserves_run_scope_and_agent_identity(clr):
    class Client:
        def bind_context(self, client_ref, **kwargs):
            return {"client_ref": client_ref, **kwargs}

    context = clr.bind_attempt_context(
        Client(),
        {
            "attempt_id": "attempt-1",
            "run_id": "issue-1",
            "repository": "org/repo",
        },
    )

    assert context == {
        "client_ref": "code-lane:attempt:attempt-1",
        "subject_ref": "code-lane:run:issue-1",
        "actor_ref": "code-lane:actor:attempt-1",
        "actor_kind": "agent",
        "scope_ref": "code-lane:scope:attempt-1",
        "agent_node_ref": "code-lane:agent:attempt-1",
    }


def test_runtime_provenance_binds_repository_and_migrations(clr):
    repository = clr.repository_identity(ROOT)
    migrations = clr.migration_identity(ROOT)

    assert len(repository["git_head"]) == 40
    assert repository["tracked_file_count"] > 0
    assert len(repository["tracked_worktree_sha256"]) == 64
    assert len(repository["tracked_diff_sha256"]) == 64
    assert migrations["files"]
    assert len(migrations["aggregate_sha256"]) == 64


def test_isolation_sentinel_reuses_source_ref_but_not_evaluation_body(clr):
    row = {
        "attempt_id": "attempt-1",
        "started_at": "2026-01-01T00:00:00Z",
        "events": [
            {"sequence": 0, "event_id": "event-0", "role": "assistant", "text": "gold"}
        ],
    }
    client = _CaptureClient()
    ctx = {
        "subject_id": "s", "scope_id": "scope", "actor_id": "a",
        "agent_node_id": "n", "subject_generation": 0,
    }

    source_ref, body = clr.ingest_isolation_sentinel(client, ctx, row)

    assert source_ref == clr.event_source_ref(row, row["events"][0])
    assert client.posts[0][1]["source_ref"] == source_ref
    assert "gold" not in body
    assert clr.ISOLATION_SENTINEL_TEXT in body


def test_tool_result_episode_includes_nearest_preceding_action(clr):
    events = [
        {"role": "assistant", "text": "first action"},
        {"role": "user", "text": "interruption"},
        {"role": "assistant", "text": "diagnostic command"},
        {"role": "toolResult", "text": "ERROR exact failure"},
    ]

    assert clr.contextual_event_body(events, 3) == (
        "assistant: diagnostic command\ntoolResult: ERROR exact failure"
    )
    assert clr.contextual_event_body(events, 1) == "user: interruption"


def test_ingest_attempt_rejects_unmapped_event_role(clr):
    client = _CaptureClient()
    with pytest.raises(RuntimeError, match="unmapped code event role"):
        clr.ingest_attempt(
            client,
            {
                "subject_id": "s",
                "scope_id": "scope",
                "actor_id": "a",
                "agent_node_id": "n",
                "subject_generation": 0,
            },
            {
                "attempt_id": "a",
                "started_at": "2026-01-01T00:00:00Z",
                "events": [
                    {"sequence": 0, "event_id": "e", "role": "system", "text": "x"}
                ],
            },
        )


def test_deterministic_file_search_ranks_raw_matching_event_first():
    search = _load("code_lane_run_deterministic", "scripts/code_lane_run_deterministic.py")
    documents = search.event_documents(
        [
            {
                "attempt_id": "a1",
                "events": [
                    {"sequence": 0, "text": "generic build output"},
                    {"sequence": 1, "text": "compiler error E0425 missing value"},
                ],
            }
        ]
    )

    assert search.bm25_search(documents, "Which compiler error E0425 occurred?", 1) == [
        "compiler error E0425 missing value"
    ]
