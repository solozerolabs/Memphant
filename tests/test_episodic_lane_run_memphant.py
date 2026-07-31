"""Unit tests for the episodic backfill runner's pure functions.

The runner (``episodic_lane_run_memphant.py``) mirrors the C3 code-lane runner:
re-exec through a scratch DB, start the packaged server/worker, bind context per
tenant (C0 strict contract), retain one episode per corpus row, drain, recall.
These tests pin the pure pieces the live run and Bar 2 depend on, without a
server: the strict-contract retain payload, the recall-visible expected set (the
Conversations-tab surface), and the equivalence assertion.
"""

from __future__ import annotations

import collections
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import episodic_lane_corpus as corpus  # noqa: E402
import episodic_lane_run_memphant as runner  # noqa: E402


def _ctx():
    return {
        "subject_id": "s",
        "scope_id": "sc",
        "actor_id": "a",
        "agent_node_id": "ag",
        "subject_generation": 0,
    }


def test_source_kind_maps_syndai_to_memphant_enum():
    """MemPhant's episode source_kind is a fixed 6-value enum
    (user/agent/tool/web/resource/system). Syndai's episodic kinds must map onto
    it (spec-28 convention: user-origin -> user, agent turns -> agent, system
    -> system). An unmapped kind is a hard error, never silently passed."""
    m = runner.map_source_kind
    assert m("user_correction") == "user"
    assert m("user_message") == "user"
    assert m("assistant_message") == "agent"
    assert m("dialog_turn") == "agent"
    assert m("system_generated") == "system"
    # Real prod data (2026-07-22 extract) has only dialog_turn + rollup; rollup
    # is a system-generated consolidation.
    assert m("rollup") == "system"
    with pytest.raises(KeyError):
        m("not_a_syndai_kind")


def test_normalize_observed_at_to_rfc3339():
    """Postgres exports timestamps as '2026-06-17 11:03:30.693143+00' (space
    separator, '+00' offset); MemPhant's contract requires RFC3339 with a proper
    UTC offset. The adapter normalizes to a 'T'-separated, 'Z'-or-'+00:00' form."""
    n = runner.normalize_observed_at
    assert n("2026-06-17 11:03:30.693143+00").endswith(("Z", "+00:00"))
    assert "T" in n("2026-06-17 11:03:30.693143+00")
    # already-RFC3339 passes through unchanged in shape
    assert n("2026-01-01T00:00:00Z").endswith(("Z", "+00:00"))
    assert "T" in n("2026-01-01T00:00:00Z")


def test_retain_payload_normalizes_observed_at():
    """The retain payload's observed_at is RFC3339, whatever the corpus format."""
    row = {
        "id": "x", "content": "b", "source_kind": "dialog_turn",
        "created_at": "2026-06-17 11:03:30.693143+00",
    }
    payload = runner.retain_payload(_ctx(), row)
    assert "T" in payload["observed_at"]
    assert payload["observed_at"].endswith(("Z", "+00:00"))


def test_retain_payload_is_strict_contract_and_maps_source_kind():
    """No banned tenant_id/allowed_scope_ids; body/source_ref/observed_at map
    straight from the row; source_kind is translated to MemPhant's enum."""
    row = corpus.build_corpus(count=252, seed=20260721)[0]
    payload = runner.retain_payload(_ctx(), row)
    assert "tenant_id" not in payload
    assert "allowed_scope_ids" not in payload
    assert payload["payload"]["episode"]["body"] == row["content"]
    assert payload["payload"]["episode"]["source_kind"] == runner.map_source_kind(
        row["source_kind"]
    )
    assert payload["payload"]["episode"]["source_kind"] in {
        "user", "agent", "tool", "web", "resource", "system"
    }
    assert payload["source_ref"] == f"episodic:{row['id']}"
    assert payload["observed_at"] == row["created_at"]
    # the bound identity is spread in verbatim
    assert payload["subject_id"] == "s"
    assert payload["subject_generation"] == 0


def _row(**overrides):
    """A recall-visible row with all filter fields present; override to test edges."""
    base = {
        "source_kind": "dialog_turn",
        "archived_at": None,
        "rolled_up": False,
    }
    base.update(overrides)
    return base


def test_backfill_disposition_matches_syndai_recall_semantics():
    """Faithful to Syndai's `_build_active_scope_filters` recall predicate
    (rolled_up=false AND archived_at IS NULL AND source_kind != 'user_correction'):
      - user_correction audit rows -> skip (excluded from recall)
      - archived rows -> forget (retain then soft-forget = the archive verb)
      - rolled_up rows -> forget (consolidated; Syndai drops them from recall)
      - the rest -> retain."""
    assert runner.backfill_disposition(_row(source_kind="user_correction")) == "skip"
    assert runner.backfill_disposition(_row(archived_at="2026-01-01T00:00:00Z")) == "forget"
    assert runner.backfill_disposition(_row(rolled_up=True)) == "forget"
    assert runner.backfill_disposition(_row()) == "retain"


def test_disposition_and_expected_set_handle_rows_missing_optional_fields():
    """Robust to corpora that omit rolled_up (the synthetic corpus predates it):
    a missing rolled_up defaults to not-rolled-up."""
    assert runner.backfill_disposition({"source_kind": "dialog_turn", "archived_at": None}) == "retain"
    rows = [{"user_id": "u", "content": "x", "created_at": "t", "source_kind": "dialog_turn", "archived_at": None}]
    assert len(runner.expected_recall_set(rows, user_id="u")) == 1


def test_expected_recall_set_excludes_rolled_up():
    """rolled_up rows are not part of the Conversations tab / recall surface."""
    rows = [
        {"user_id": "u", "content": "live", "created_at": "2026-01-01T00:00:00Z",
         "source_kind": "dialog_turn", "archived_at": None, "rolled_up": False},
        {"user_id": "u", "content": "consolidated", "created_at": "2026-01-01T00:01:00Z",
         "source_kind": "rollup", "archived_at": None, "rolled_up": True},
    ]
    visible = runner.expected_recall_set(rows, user_id="u")
    assert [r["content"] for r in visible] == ["live"]


def test_backfill_dispositions_cover_the_whole_corpus():
    """Every row gets exactly one disposition; the retain+forget set equals the
    non-correction rows, and skip equals the correction rows."""
    rows = corpus.build_corpus(count=252, seed=20260721)
    dispositions = [runner.backfill_disposition(r) for r in rows]
    assert set(dispositions) == {"retain", "forget", "skip"}
    n_skip = sum(1 for d in dispositions if d == "skip")
    n_corrections = sum(1 for r in rows if r["source_kind"] == "user_correction")
    assert n_skip == n_corrections
    n_forget = sum(1 for d in dispositions if d == "forget")
    n_archived = sum(
        1 for r in rows
        if r["archived_at"] is not None and r["source_kind"] != "user_correction"
    )
    assert n_forget == n_archived


def test_expected_recall_set_excludes_archived_and_corrections_and_is_recency_ordered():
    """The Conversations tab shows recall-visible episodes newest-first; archived
    and user_correction audit rows are absent (recall's own state filter)."""
    rows = corpus.build_corpus(count=252, seed=20260721)
    expected = runner.expected_recall_set(rows)
    assert expected, "expected set must be non-empty"
    assert all(r["archived_at"] is None for r in expected)
    assert all(r["source_kind"] != "user_correction" for r in expected)
    created = [r["created_at"] for r in expected]
    assert created == sorted(created, reverse=True), "must be recency DESC"


def test_expected_recall_set_is_single_tenant_scoped():
    """Bar 2 is proven per tenant; the expected set filters to one user_id so a
    cross-tenant row can never inflate the match."""
    rows = corpus.build_corpus(count=252, seed=20260721)
    user_id = rows[0]["user_id"]
    expected = runner.expected_recall_set(rows, user_id=user_id)
    assert expected
    assert all(r["user_id"] == user_id for r in expected)


def _perfect_recall_fn(rows, user_id):
    """A fake recall that behaves like a correct MemPhant: a query for a body
    returns it IFF that episode is recall-visible for this tenant (state filter
    applied). Injected so equivalence is unit-testable without a server."""
    visible = [r["content"] for r in runner.expected_recall_set(rows, user_id=user_id)]

    def recall_fn(query: str) -> list[str]:
        # The probe queries a leading slice; a correct recall returns any visible
        # body whose leading slice matches (models chunk retrieval).
        return [body for body in visible if body.startswith(query)]

    return recall_fn


def test_probe_equivalence_passes_and_reports_full_reachability_on_clean_data():
    """On the synthetic corpus (short, distinct bodies) a correct recall reaches
    every distinct visible body — reachability_rate == 1.0 — and the state filter
    is exact."""
    rows = corpus.build_corpus(count=252, seed=20260721)
    user_id = rows[0]["user_id"]
    summary = runner.probe_conversations_equivalence(
        _perfect_recall_fn(rows, user_id), rows, user_id
    )
    assert summary["reachability_rate"] == 1.0
    assert summary["distinct_reachable"] == summary["distinct_bodies"] > 0
    assert summary["state_filter_correct"] is True
    assert summary["correctly_excluded"] > 0


def test_reachability_is_reported_not_gated_when_recall_drops_a_body():
    """(a) reachability is a REPORTED metric, not a hard gate: a recall that drops
    a visible body lowers reachability_rate below 1.0 but does NOT raise (recall
    is ranked/deduped/budget-limited — dropping a near-duplicate is legitimate).
    The hard gate is the state filter (b), tested separately."""
    rows = corpus.build_corpus(count=252, seed=20260721)
    user_id = rows[0]["user_id"]
    base = _perfect_recall_fn(rows, user_id)
    dropped = runner.expected_recall_set(rows, user_id=user_id)[0]["content"]

    def dropping_recall(query: str) -> list[str]:
        return [] if dropped.startswith(query) else base(query)

    summary = runner.probe_conversations_equivalence(dropping_recall, rows, user_id)
    assert summary["reachability_rate"] < 1.0  # metric moved
    assert summary["state_filter_correct"] is True  # but the hard gate still holds


def test_probe_equivalence_fails_when_archived_episode_leaks_into_recall():
    """(b) state-filter correctness: an archived/correction episode surfacing in
    recall is the load-bearing failure the tab's archived filter must prevent."""
    rows = corpus.build_corpus(count=252, seed=20260721)
    user_id = rows[0]["user_id"]
    base = _perfect_recall_fn(rows, user_id)
    filtered_body = next(
        r["content"]
        for r in rows
        if r["user_id"] == user_id and not runner.is_recall_visible(r)
    )

    def leaking_recall(query: str) -> list[str]:
        # The filtered episode surfaces for its own prefix query — a leak.
        return [filtered_body] if filtered_body.startswith(query) else base(query)

    with pytest.raises(RuntimeError):
        runner.probe_conversations_equivalence(leaking_recall, rows, user_id)


def test_expected_compiled_jobs_counts_retains_forgets_and_the_archive_reflect():
    """The count half of the drain contract this runner was missing.

    One `reflect_episode` job per POSTed episode (retain + forget dispositions;
    `skip` rows are never posted), plus exactly one `reflect_scope` job for the
    explicit `/v1/reflect` the archive path issues — `forget` itself enqueues
    nothing. An under-drain reports fewer completions than this, so comparing
    against it is what turns a silent partial corpus into a raised error.
    """
    counts = {"retain": 227, "forget": 10, "skip": 15}
    assert runner.expected_compiled_jobs(counts, True) == 238
    assert runner.expected_compiled_jobs(counts, False) == 237
    assert runner.expected_compiled_jobs({"retain": 0, "forget": 0, "skip": 3}, False) == 0


def test_expected_compiled_jobs_matches_the_banked_c1_provenance():
    """The banked C1 artifacts record `compiled_jobs` 238 (synthetic 252-row) and
    271 (real prod, 270 rows / 0 corrections). Both equal the number of jobs the
    backfill enqueues EXACTLY, which is only possible if the queue fully drained:
    an under-drain reports a short count. This pins that arithmetic so the audit
    verdict cannot silently rot.
    """
    synthetic = collections.Counter(
        runner.backfill_disposition(row) for row in corpus.build_corpus(252, 20260721)
    )
    assert runner.expected_compiled_jobs(synthetic, True) == 238

    # Real prod (2026-07-22): 270 rows, no `user_correction`, so nothing skipped.
    real_prod = {"retain": 35, "forget": 235, "skip": 0}
    assert sum(real_prod.values()) == 270
    assert runner.expected_compiled_jobs(real_prod, True) == 271
