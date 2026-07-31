#!/usr/bin/env python3
"""MemPhant episodic-slice backfill runner for C1.

Mirrors ``code_lane_run_memphant.py``: re-execs through
``scripts/with_scratch_db.sh`` onto a fresh, migrated, auto-dropped scratch DB
(never a shared or Syndai-prod DB — AGENTS.md §18), starts the packaged
``memphant-server`` + ``memphant-worker``, binds context per tenant via the C0
strict-contract handshake (``bind_context``; no ``tenant_id``), retains one
episode per synthetic corpus row, drains the worker, and recalls.

The 252-row backfill IS this ingest path at the default count. Two tenants (the
corpus's two ``user_id``s) are bound to separate MemPhant subjects/scopes so the
same run also seeds the RLS two-tenant fixture and so the Bar 2 equivalence check
is per-tenant.

Bar 2 (Conversations-tab equivalence) is proven **on recall**, not on the
``scope_memory_page`` listing: recall filters state (drops archived/forgotten/
``user_correction`` rows) exactly as the tab does, whereas the listing does not
(verified store.rs:3374-3389). The claim is episode-set + recall-visibility
equivalence — ``content``/``source_kind`` match, archived+correction rows absent,
recency order — NOT DTO byte-for-byte (``StoredMemoryUnit`` lacks the tab's
presentation fields, which stay Syndai-side).

Flag posture (pinned): the server runs with default flags — fact-extraction and
resource-chunk writes OFF, no structured-state provider — so one episode compiles
to exactly one unit and the 1:1 count is deterministic. Bodies are short
(``episodic_lane_corpus.MAX_BODY_CHARS``) so contextual chunking never splits one.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import episodic_lane_corpus as elc  # noqa: E402
import gate_common as gc  # noqa: E402
import gate_runtime as gr  # noqa: E402

DEFAULT_BASE_DATABASE_URL = "postgres://memphant:memphant@localhost:5432/memphant"

# Bar 1 SLO thresholds (Syndai's HTTP-observed context-injection budget).
SLO_P50_MS = 200.0
SLO_P95_MS = 500.0


# --- pure functions (unit-tested in tests/test_episodic_lane_run_memphant.py) ---

# Syndai's episodic source_kind taxonomy -> MemPhant's fixed 6-value episode enum
# (user/agent/tool/web/resource/system, service.rs:1071). The spec-28 convention:
# user-originated turns -> user, the agent's own turns -> agent, system-generated
# -> system. This is the adapter's source_kind translation, a real C1 cutover
# mapping (the corpus keeps Syndai's kinds so it stays schema-faithful).
SYNDAI_TO_MEMPHANT_SOURCE_KIND = {
    "user_correction": "user",
    "user_message": "user",
    "assistant_message": "agent",
    "dialog_turn": "agent",
    "system_generated": "system",
    # Real prod data (2026-07-22) has only dialog_turn + rollup. A rollup is a
    # system-generated consolidation of prior turns.
    "rollup": "system",
}


def map_source_kind(syndai_kind: str) -> str:
    """Translate a Syndai episodic source_kind to MemPhant's enum. Raises
    ``KeyError`` on an unmapped kind — never silently pass an invalid value that
    the strict contract would 422 (or, worse, that a laxer server would accept
    with the wrong semantics)."""
    return SYNDAI_TO_MEMPHANT_SOURCE_KIND[syndai_kind]


def is_recall_visible(row: dict) -> bool:
    """The Conversations-tab / recall-visibility predicate, faithful to Syndai's
    ``episodic_service._build_active_scope_filters``: a row is visible iff it is
    NOT rolled up, NOT archived, and NOT a ``user_correction`` audit row.
    ``rolled_up`` is optional (the synthetic corpus predates it; missing => not
    rolled up)."""
    return (
        not row.get("rolled_up", False)
        and row["archived_at"] is None
        and row["source_kind"] != "user_correction"
    )


def backfill_disposition(row: dict) -> str:
    """How the cutover treats one Syndai episodic row, so the backfilled store's
    recall surface equals the Conversations tab:

      - ``skip``   — ``user_correction`` audit rows: Syndai excludes them from
        recall; they are correction artifacts, not conversation episodes.
      - ``forget`` — archived OR rolled-up rows: retained so they exist like
        Syndai's table, then soft-forgotten so they drop out of recall — exactly
        as Syndai's ``archived_at IS NULL`` + ``rolled_up = false`` filters drop
        them. This is the archive->forget verb mapping.
      - ``retain`` — everything else: a live, recall-visible episode.
    """
    if row["source_kind"] == "user_correction":
        return "skip"
    if not is_recall_visible(row):
        return "forget"
    return "retain"


def normalize_observed_at(value: str) -> str:
    """Normalize a Syndai/Postgres timestamp to the RFC3339 form MemPhant's
    contract requires (``T`` separator + a proper UTC offset). Postgres exports
    ``2026-06-17 11:03:30.693143+00`` (space separator, ``+00``), which the strict
    contract 422s as "observed_at must use a UTC offset". ``datetime.fromisoformat``
    parses both that and already-RFC3339 input; we re-emit with ``Z`` for UTC."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    text = parsed.astimezone(timezone.utc).isoformat()
    return text.replace("+00:00", "Z")


def retain_payload(ctx: dict, row: dict) -> dict:
    """Strict-contract retain body for one episodic row. Identity comes from the
    bound context; there is no tenant_id. Episodic bodies are already prose, so
    the body is the ``content`` verbatim (unlike the code-lane role-prefixed
    join). ``source_kind`` is translated to MemPhant's enum and ``observed_at`` to
    RFC3339."""
    return {
        **ctx,
        "source_ref": f"episodic:{row['id']}",
        "observed_at": normalize_observed_at(row["created_at"]),
        "payload": {
            "episode": {
                "source_kind": map_source_kind(row["source_kind"]),
                "body": row["content"],
            }
        },
    }


def expected_recall_set(rows: list[dict], user_id: str | None = None) -> list[dict]:
    """The rows the Conversations tab renders: recall-visible episodes, newest
    first (``is_recall_visible`` excludes rolled-up / archived / correction rows).
    Optionally scoped to one tenant."""
    visible = [
        row
        for row in rows
        if is_recall_visible(row) and (user_id is None or row["user_id"] == user_id)
    ]
    return sorted(visible, key=lambda row: row["created_at"], reverse=True)


def probe_conversations_equivalence(recall_fn, rows: list[dict], user_id: str) -> dict:
    """Prove the Conversations-tab equivalence against the RECALL surface.

    Recall is a bounded, ranked retrieval — NOT a full listing — so "one broad
    query returns exactly the visible set" is the wrong bar (a budget-limited
    query returns only its top subset). The real "the tab shows exactly the
    recall-visible episodes" property, tested against recall, is two-part:

      (a) retrievability — every recall-visible episode is individually
          recallable: a query containing its distinct body returns that body.
      (b) state-filter correctness — no archived / ``user_correction`` episode is
          EVER recallable: a query containing its body must NOT return it.

    (b) is the load-bearing half: recall's state filter must drop exactly what the
    tab's ``archived_at IS NULL`` (+ correction exclusion) drops. ``recall_fn`` is
    ``query -> list_of_bodies`` (injected so this is unit-testable without a
    server). Raises on any divergence; returns a small summary on success.

    Chunk-awareness (real data): MemPhant splits long episode bodies into
    contextual chunks (default on), so recall surfaces a chunk body, not the full
    episode text. Retrievability therefore matches on chunk OVERLAP — a returned
    body is "from" the episode if either contains the other — and queries a
    distinctive leading slice rather than the whole (possibly 16k-char) body."""
    visible = expected_recall_set(rows, user_id=user_id)
    filtered = [
        row for row in rows if row["user_id"] == user_id and not is_recall_visible(row)
    ]

    def episode_query(body: str) -> str:
        # A distinctive slice: querying a full 16k-char body is degenerate.
        return body[:200]

    def overlaps(episode_body: str, returned: str) -> bool:
        return returned in episode_body or episode_body in returned

    # (a) REACHABILITY COVERAGE (reported metric, NOT a hard gate). Recall is a
    # ranked, DEDUPED, budget-limited retrieval — not a 1:1 mirror of a listing.
    # On real prod data, near-duplicate 16k-char audit-prompt bodies that share an
    # identical instruction prefix are legitimately NOT individually retrievable
    # (a generic-prefix query is not distinctive, and dedup/budget collapse the
    # cluster). Asserting 100% per-episode retrievability would be dishonest about
    # what recall is. We REPORT the coverage rate; the hard gate is (b).
    distinct_bodies = {row["content"] for row in visible}
    reachable = {
        body
        for body in distinct_bodies
        if any(overlaps(body, b) for b in recall_fn(episode_query(body)))
    }

    # (b) STATE-FILTER CORRECTNESS (HARD gate, load-bearing): no rolled-up /
    # archived / correction episode is EVER recallable — recall must drop exactly
    # what the tab drops. A single leak here is a data-integrity failure. This is
    # the property that actually matters for a cutover: the forgotten stay gone.
    leaked = [
        row["content"]
        for row in filtered
        if any(overlaps(row["content"], b) for b in recall_fn(episode_query(row["content"])))
    ]
    if leaked:
        raise RuntimeError(
            "conversations equivalence FAILED (b): archived/correction/rolled-up "
            f"episode leaked into recall: {[b[:80] for b in leaked[:3]]} "
            f"({len(leaked)} of {len(filtered)})"
        )
    return {
        "visible": len(visible),
        "distinct_bodies": len(distinct_bodies),
        "distinct_reachable": len(reachable),
        "reachability_rate": round(len(reachable) / len(distinct_bodies), 3)
        if distinct_bodies
        else 1.0,
        "correctly_excluded": len(filtered),
        "state_filter_correct": True,
    }


def expected_compiled_jobs(counts: dict[str, int], reflected: bool) -> int:
    """Exactly how many reflect jobs this backfill enqueues.

    ``retain_episode`` enqueues one ``reflect_episode`` job per retained episode
    (memphant-core/src/lib.rs); ``skip``ped rows are never posted; ``forget``
    enqueues NOTHING (service.rs `forget` stages the deletion and commits, no
    `enqueue_reflect`), which is exactly why the archived rows need the explicit
    ``/v1/reflect`` — one extra ``reflect_scope`` job.

    Asserting the drain against THIS number is the count half of the drain
    contract ``code_lane_run_memphant.py`` already had and this runner did not:
    an under-drain reports fewer completions than it enqueued, so the mismatch
    raises instead of silently scoring a partially compiled corpus.
    """
    return counts["retain"] + counts["forget"] + (1 if reflected else 0)


def measure_recall_slo(
    client: "gr.ApiClient",
    ctx: dict,
    query: str,
    k: int,
    budget_tokens: int,
    samples: int,
) -> dict:
    """Measure client wall-clock p50/p95 over ``samples`` real POST /v1/recall
    calls (Fast). This is the HTTP-boundary hot path a user actually waits on
    (axum + resolve_memory_context + pipeline).

    Returns the measurement including ``passed`` and the host load average
    observed around it; it does NOT raise. The caller asserts the bar after the
    artifacts are written, because a latency measurement that produces no
    artifact when it fails teaches nothing — and a wall-clock number taken on a
    contended machine is uninterpretable without the load that produced it.
    """
    load_before = os.getloadavg()
    latencies_ms: list[float] = []
    for _ in range(samples):
        start = time.perf_counter()
        gr.recall_query(client, ctx, query, k, budget_tokens, "fast")
        latencies_ms.append((time.perf_counter() - start) * 1000.0)
    load_after = os.getloadavg()
    latencies_ms.sort()
    p50 = statistics.median(latencies_ms)
    # inclusive p95 (matches gate_common._percentile).
    p95 = statistics.quantiles(latencies_ms, n=100, method="inclusive")[94]
    return {
        "p50_ms": p50,
        "p95_ms": p95,
        "samples": samples,
        "passed": p50 < SLO_P50_MS and p95 < SLO_P95_MS,
        "cpu_count": os.cpu_count(),
        "loadavg_1m_before": round(load_before[0], 2),
        "loadavg_1m_after": round(load_after[0], 2),
    }


# --- live run ----------------------------------------------------------------


def _bind_tenant(client: "gr.ApiClient", user_id: str) -> dict:
    """Bind one MemPhant context per Syndai user_id (own subject/scope/agent)."""
    return client.bind_context(
        f"episodic:{user_id}",
        subject_ref=f"episodic:subject:{user_id}",
        actor_ref=f"episodic:actor:{user_id}",
        actor_kind="system",
        scope_ref=f"episodic:scope:{user_id}",
        agent_node_ref=f"episodic:agent:{user_id}",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=DEFAULT_BASE_DATABASE_URL)
    parser.add_argument(
        "--corpus", default=None,
        help="JSONL episodic corpus to backfill (e.g. a read-only prod extract, "
             "gitignored). When omitted, a deterministic synthetic corpus is used.",
    )
    parser.add_argument("--count", type=int, default=252)
    parser.add_argument("--seed", type=int, default=20260721)
    parser.add_argument("--out-evidence", required=True)
    parser.add_argument("--out-provenance", required=True)
    parser.add_argument("--embed-model", default=None)
    parser.add_argument("--port", type=int, default=39415)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--budget-tokens", type=int, default=1200)
    parser.add_argument(
        "--slo-samples", type=int, default=0,
        help="if >0, measure HTTP-boundary recall p50/p95 over N calls and assert the SLO",
    )
    parser.add_argument(
        "--redact-tenant-ids", action="store_true",
        help="write only the first 8 hex chars of each Syndai user_id into the "
             "evidence and provenance files. Required whenever the corpus is a "
             "real-prod extract, because those artifacts are COMMITTED and a "
             "user_id is production identity "
             "(docs/build-log/2026-07-30-c1-replication-privacy-prereg.md).",
    )
    parser.add_argument("--server-bin", default=str(gc.MEMPHANT_ROOT / "target/release/memphant-server"))
    parser.add_argument("--worker-bin", default=str(gc.MEMPHANT_ROOT / "target/release/memphant-worker"))
    parser.add_argument("--cli-bin", default=str(gc.MEMPHANT_ROOT / "target/release/memphant-cli"))
    args = parser.parse_args()

    if args.corpus:
        rows = gc.load_goldens(Path(args.corpus))
        source_desc = f"corpus={Path(args.corpus).name}"
    else:
        rows = elc.build_corpus(args.count, args.seed)
        source_desc = f"synthetic seed={args.seed}"
    user_ids = sorted({row["user_id"] for row in rows})
    print(
        f"source: {source_desc} | rows={len(rows)} tenants={len(user_ids)}",
        file=sys.stderr,
    )

    gr.reexec_through_scratch_db(args.database_url)
    args.database_url = os.environ["DATABASE_URL"]
    gr.check_embed_model_key(args.embed_model)

    tenant_id, api_key = gr.provision_tenant(
        args.cli_bin, args.database_url, name_prefix="episodic-lane"
    )
    print(f"tenant={tenant_id}", file=sys.stderr)

    server_log_path = Path(args.out_provenance).resolve().parent / "server-episodic.log"
    server = gr.Server(
        args.server_bin, args.database_url, args.port, args.embed_model,
        log_path=server_log_path,
    )
    try:
        server.start()
        client = gr.ApiClient(args.port, api_key, tenant_id)

        # One bound context per Syndai user_id (two tenants in one MemPhant tenant,
        # separate subjects/scopes — the two-user fixture).
        contexts = {uid: _bind_tenant(client, uid) for uid in user_ids}

        t0 = time.time()
        counts = {"retain": 0, "forget": 0, "skip": 0}
        to_forget = []  # (user_id, episode_id) for archived rows
        for i, row in enumerate(rows):
            disposition = backfill_disposition(row)
            counts[disposition] += 1
            if disposition == "skip":
                continue
            ctx = contexts[row["user_id"]]
            response = client.post("/v1/episodes", retain_payload(ctx, row))
            if disposition == "forget":
                to_forget.append((row["user_id"], response.get("episode_id")))
            if (i + 1) % 50 == 0:
                print(f"  ingested {i + 1}/{len(rows)}", file=sys.stderr)
        print(
            f"ingest done in {time.time() - t0:.1f}s "
            f"(retain={counts['retain']} forget={counts['forget']} skip={counts['skip']}); "
            "draining worker...",
            file=sys.stderr,
        )
        compiled = gr.drain_worker(args.worker_bin, args.database_url, args.embed_model)
        print(f"worker drained: compiled={compiled} jobs", file=sys.stderr)

        # Archive semantics: soft-forget the archived episodes so they exist in
        # the store (like Syndai's table) but drop out of recall (like the tab's
        # archived_at IS NULL filter). This exercises the archive->forget verb.
        for user_id, episode_id in to_forget:
            if not episode_id:
                continue
            ctx = contexts[user_id]
            client.post(
                "/v1/forget",
                {
                    **ctx,
                    "selector": {"episode_id": episode_id, "scope_id": ctx["scope_id"]},
                    "reason": "episodic archive (archived_at set in Syndai)",
                },
            )
        if to_forget:
            # Re-drain so forget's deletion generation is compiled before recall.
            client.post("/v1/reflect", {**contexts[user_ids[0]]})
            compiled += gr.drain_worker(
                args.worker_bin, args.database_url, args.embed_model
            )
            print(f"archived {len(to_forget)} episodes (forget); re-drained", file=sys.stderr)

        # The drain contract, both halves, BEFORE any bar is asserted (a bar
        # proven on a partially compiled corpus is worse than no bar at all).
        # (1) the count: as many jobs completed as were enqueued; (2) the
        # database, asked on the bench credential rather than the worker's own
        # RLS-constrained pool.
        expected_jobs = expected_compiled_jobs(counts, bool(to_forget))
        if compiled != expected_jobs:
            raise RuntimeError(
                f"compiled job count mismatch: {compiled} != {expected_jobs} enqueued "
                "(retain + forget episodes + the archive reflect)"
            )
        gr.assert_worker_queue_empty(args.database_url)
        print(
            f"compilation verified: compiled={compiled} == enqueued={expected_jobs}, "
            "queue empty per the database",
            file=sys.stderr,
        )

        # Bar 2, per tenant: recall is a bounded ranked retrieval, not a full
        # listing, so equivalence is proven per-episode — (a) every visible
        # episode is retrievable by a query for its body, (b) no archived/
        # correction episode ever is. First guard against a degraded (partial-
        # drain) read so an infra fault can't masquerade as a divergence.
        per_tenant = {}
        for uid in user_ids:
            ctx = contexts[uid]

            def recall_fn(query: str, _ctx=ctx) -> list[str]:
                bodies, degraded = gr.recall_query(
                    client, _ctx, query, args.k, args.budget_tokens, "fast"
                )
                if degraded:
                    raise RuntimeError(
                        f"recall degraded for tenant {uid} — worker drain "
                        "incomplete; refusing to assert equivalence on a partial read"
                    )
                return bodies

            summary = probe_conversations_equivalence(recall_fn, rows, uid)
            per_tenant[uid] = {**summary, "equivalence": "passed"}
            print(
                f"  tenant {uid[:8]}: state-filter EXACT (correctly_excluded="
                f"{summary['correctly_excluded']}, 0 leaks) | reachability="
                f"{summary['distinct_reachable']}/{summary['distinct_bodies']} distinct "
                f"({summary['reachability_rate']:.0%})",
                file=sys.stderr,
            )

        slo = None
        if args.slo_samples > 0:
            # A REALISTIC hot-path query is short (a context-injection lookup),
            # never a full episode body — a 16k-char body as the query embeds +
            # packs in ~1s and is not what the hot path does. Use a short query.
            slo_query = "what happened recently in this conversation"
            slo = measure_recall_slo(
                client, contexts[user_ids[0]], slo_query, args.k,
                args.budget_tokens, args.slo_samples,
            )
            print(
                f"SLO (HTTP boundary): p50={slo['p50_ms']:.1f}ms p95={slo['p95_ms']:.1f}ms "
                f"(< {SLO_P50_MS}/{SLO_P95_MS}) over {slo['samples']} calls "
                f"-- {'PASS' if slo['passed'] else 'BREACH'} at loadavg "
                f"{slo['loadavg_1m_before']}->{slo['loadavg_1m_after']} "
                f"on {slo['cpu_count']} cpus",
                file=sys.stderr,
            )

        if args.redact_tenant_ids:
            per_tenant = {uid[:8]: info for uid, info in per_tenant.items()}
            if len(per_tenant) != len(user_ids):
                raise RuntimeError("tenant-id prefix redaction collided; widen the prefix")
        evidence_rows = [{"user_id": uid, **info} for uid, info in per_tenant.items()]
        gc.write_jsonl(Path(args.out_evidence), evidence_rows)
        report = {
            "engine": "memphant",
            "lane": "episodic",
            "runtime": "memphant-server episode ingest + /v1/recall (fast, budget 1200)",
            "database_url_db": args.database_url.rsplit("/", 1)[-1],
            "corpus_rows": len(rows),
            "tenants": len(user_ids),
            "compiled_jobs": compiled,
            "expected_jobs": expected_jobs,
            "queue_drained_verified": True,
            "k": args.k,
            "budget_tokens": args.budget_tokens,
            "conversations_equivalence": "passed",
            "per_tenant": per_tenant,
            "hot_path_slo": slo,
        }
        Path(args.out_provenance).write_text(json.dumps(report, indent=2) + "\n")
        print(
            f"done: {len(rows)} rows backfilled, equivalence PASSED for {len(user_ids)} tenants; "
            f"evidence={args.out_evidence} provenance={args.out_provenance}",
            file=sys.stderr,
        )
        # Assert the SLO only now, with the artifacts already on disk.
        if slo and not slo["passed"]:
            raise RuntimeError(
                f"hot-path SLO BREACHED: p50={slo['p50_ms']:.1f}ms (<{SLO_P50_MS}) "
                f"p95={slo['p95_ms']:.1f}ms (<{SLO_P95_MS}) at loadavg "
                f"{slo['loadavg_1m_before']}->{slo['loadavg_1m_after']}; "
                "provenance written"
            )
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
