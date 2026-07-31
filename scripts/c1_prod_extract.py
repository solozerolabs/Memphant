#!/usr/bin/env python3
"""C1 episodic slice: read-only extract from Syndai production.

Preregistered privacy terms:
``docs/build-log/2026-07-30-c1-replication-privacy-prereg.md`` — read that first.
Nothing here may be relaxed without amending that document.

Why this exists: the 2026-07-22 real-prod C1 corpus (commit ``6d01789b``) was a
one-time gitignored extract and is no longer on disk, so C1's real-data arm is
unreproducible. This is the mechanism that makes it reproducible: a snapshot that
is frozen and hashed, a derive step that is deterministic from that snapshot, and
a committed lock of counts and hashes only.

Access posture (enforced, not merely intended):

* every statement runs with ``default_transaction_read_only = on`` via
  ``PGOPTIONS``, and the only verb issued is ``SELECT``;
* the connection string is consumed from the environment (supplied by
  ``doppler run --project syndai --config prod``) and never printed or written;
* ``embedding``, ``metadata`` and ``summary`` are refused outright (prereg
  "Fields extracted, and fields refused").

Stages::

    doppler run --project syndai --config prod -- \
        python3 scripts/c1_prod_extract.py --snapshot   # freeze + hash source
    python3 scripts/c1_prod_extract.py --extract        # scan, map, corpus, lock
    python3 scripts/c1_prod_extract.py --check          # re-derive, assert lock

``--extract`` and ``--check`` never open a network socket: they read the frozen
snapshot. Same snapshot -> byte-identical corpus.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate_common as gc  # noqa: E402
import github_lane_secrets as secrets  # noqa: E402

PRIVATE_ROOT = Path.home() / ".memphant-private" / "c1"
SNAPSHOT_PATH = PRIVATE_ROOT / "sources" / "episodic_memories.json"
SNAPSHOT_MANIFEST = PRIVATE_ROOT / "sources.manifest.json"
MIRROR_CORPUS = PRIVATE_ROOT / "c1_prod_episodic.jsonl"

CORPUS_PATH = gc.MEMPHANT_ROOT / "benchmarks" / "data" / "private" / "c1_prod_episodic.jsonl"
LOCK_PATH = gc.MEMPHANT_ROOT / "benchmarks" / "data" / "c1_prod_episodic.lock.json"

# Exactly the columns the prereg admits. `embedding`, `metadata` and `summary`
# are absent on purpose; adding one here is a prereg amendment, not a tweak.
COLUMNS = [
    "id",
    "user_id",
    "l0_agent_id",
    "project_id",
    "mission_id",
    "content",
    "source_kind",
    "importance_score",
    "trust_level",
    "tainted",
    "rolled_up",
    "archived_at",
    "created_at",
    "idempotency_key",
]

SELECT_SQL = (
    "select coalesce(json_agg(t order by t.created_at, t.id), '[]'::json) from ("
    "select " + ", ".join(COLUMNS) + " from syndai.episodic_memories"
    ") t"
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def snapshot() -> int:
    """Freeze the production table into a hashed local snapshot. READ ONLY."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        print(
            "DATABASE_URL is unset. Run under: doppler run --project syndai "
            "--config prod -- python3 scripts/c1_prod_extract.py --snapshot",
            file=sys.stderr,
        )
        return 2
    env = dict(os.environ)
    # The read-only guard is applied by the server to EVERY statement in the
    # session, so it cannot be bypassed by anything this script does later.
    env["PGOPTIONS"] = "-c default_transaction_read_only=on"
    proc = subprocess.run(
        ["psql", url, "-v", "ON_ERROR_STOP=1", "-At", "-c", SELECT_SQL],
        capture_output=True,
        text=True,
        env=env,
    )
    if proc.returncode != 0:
        # stderr from psql can echo the connection string; report the code only.
        print(f"psql failed with exit code {proc.returncode}", file=sys.stderr)
        return proc.returncode
    rows = json.loads(proc.stdout)
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, indent=None).encode()
    SNAPSHOT_PATH.write_bytes(payload)
    digest = _sha256_bytes(payload)
    SNAPSHOT_MANIFEST.write_text(
        json.dumps(
            {
                "source": "syndai.episodic_memories",
                "access": "read-only (default_transaction_read_only=on), SELECT only",
                "columns": COLUMNS,
                "rows": len(rows),
                "bytes": len(payload),
                "snapshot_sha256": digest,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    print(f"snapshot: {len(rows)} rows, sha256={digest}", file=sys.stderr)
    return 0


def _derive(rows: list[dict]) -> tuple[list[dict], Counter]:
    """Secret-scan and shape the snapshot into corpus rows. Pure and deterministic.

    A row whose ``content`` trips any pinned pattern is dropped WHOLE (prereg
    "Secrets"); only the pattern name is retained, never the value.
    """
    kept: list[dict] = []
    drops: Counter = Counter()
    for row in rows:
        content = row.get("content")
        if not content or not content.strip():
            drops["empty_content"] += 1
            continue
        hit = secrets.scan(content)
        if hit:
            drops[hit] += 1
            continue
        kept.append({key: row.get(key) for key in COLUMNS})
    return kept, drops


def _strata(rows: list[dict]) -> dict:
    """Counts only — the shape of the corpus, never its content."""
    import episodic_lane_run_memphant as runner

    by_tenant: dict[str, Counter] = {}
    for row in rows:
        prefix = str(row["user_id"])[:8]
        bucket = by_tenant.setdefault(prefix, Counter())
        bucket["rows"] += 1
        bucket[runner.backfill_disposition(row)] += 1
        if runner.is_recall_visible(row):
            bucket["recall_visible"] += 1
    return {
        "rows": len(rows),
        "tenants": len(by_tenant),
        "by_source_kind": dict(sorted(Counter(r["source_kind"] for r in rows).items())),
        "by_disposition": dict(
            sorted(Counter(runner.backfill_disposition(r) for r in rows).items())
        ),
        "rolled_up": sum(1 for r in rows if r.get("rolled_up")),
        "archived": sum(1 for r in rows if r.get("archived_at")),
        "recall_visible": sum(1 for r in rows if runner.is_recall_visible(r)),
        "by_tenant_prefix": {k: dict(sorted(v.items())) for k, v in sorted(by_tenant.items())},
    }


def _corpus_bytes(rows: list[dict]) -> bytes:
    return "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
    ).encode()


def _load_snapshot() -> tuple[list[dict], str]:
    if not SNAPSHOT_PATH.exists():
        raise SystemExit(f"no snapshot at {SNAPSHOT_PATH}; run --snapshot first")
    data = SNAPSHOT_PATH.read_bytes()
    return json.loads(data), _sha256_bytes(data)


def extract() -> int:
    rows, snap_sha = _load_snapshot()
    kept, drops = _derive(rows)
    blob = _corpus_bytes(kept)
    CORPUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CORPUS_PATH.write_bytes(blob)
    MIRROR_CORPUS.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(CORPUS_PATH, MIRROR_CORPUS)
    lock = {
        "lane": "c1-episodic",
        "source": "syndai.episodic_memories (production, read-only SELECT)",
        "prereg": "docs/build-log/2026-07-30-c1-replication-privacy-prereg.md",
        "columns": COLUMNS,
        "snapshot_rows": len(rows),
        "snapshot_sha256": snap_sha,
        "corpus_sha256": _sha256_bytes(blob),
        "corpus_bytes": len(blob),
        "secret_scan_drops": dict(sorted(drops.items())),
        "strata": _strata(kept),
    }
    LOCK_PATH.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")
    print(
        f"corpus: {len(kept)} rows (dropped {sum(drops.values())}) -> {CORPUS_PATH}",
        file=sys.stderr,
    )
    return 0


def check() -> int:
    rows, snap_sha = _load_snapshot()
    kept, drops = _derive(rows)
    lock = json.loads(LOCK_PATH.read_text())
    expected = {
        "snapshot_sha256": snap_sha,
        "corpus_sha256": _sha256_bytes(_corpus_bytes(kept)),
        "secret_scan_drops": dict(sorted(drops.items())),
        "strata": _strata(kept),
    }
    bad = [k for k, v in expected.items() if lock.get(k) != v]
    if bad:
        print(f"LOCK MISMATCH on: {', '.join(bad)}", file=sys.stderr)
        return 1
    if CORPUS_PATH.exists() and _sha256_file(CORPUS_PATH) != lock["corpus_sha256"]:
        print("LOCK MISMATCH: on-disk corpus differs from the lock", file=sys.stderr)
        return 1
    print("lock verified", file=sys.stderr)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--snapshot", action="store_true")
    group.add_argument("--extract", action="store_true")
    group.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.snapshot:
        return snapshot()
    if args.extract:
        return extract()
    return check()


if __name__ == "__main__":
    raise SystemExit(main())
