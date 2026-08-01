# Trunk integration — 219 commits, 32 worktrees, one conflict

Date: 2026-08-01
Status: **LANDED.** `main` bf2c87c3 → 5d7b9d5a.
Spend: $0.

---

## 1. What was wrong

`main` held **zero of 219 commits.** Thirty-two worktrees, thirty-five branches,
every one ahead of trunk, none merged. Six weeks of measurement lived only on
branches.

The plan of record already named the consequence and did not connect it to the
cause. `2026-07-31-one-plan.md` §A1:

> the program's flagship result was measured two fixes behind its own trunk and
> nobody noticed. The failure mode is not instrument bias — it is **lineage
> drift across nineteen worktrees**.

Nineteen had become thirty-two. The standing rule — *"Lineage or it did not
happen"* — was being enforced on artifacts while the thing generating the drift
went unaddressed.

## 2. The cost, measured

The integration produced **exactly one substantive conflict across all five
leaf branches**: `scripts/gate_runtime.py`, the `drain_worker` line parser.

`memphant-worker/src/main.rs:124` prints `completed/failed/retried/deferred`.
The shared parser accepted only the bare `completed=N` form, so every harness on
the drain path raised "malformed" against the current worker.

**That fix was written four times, independently, on four branches** —
`af-rerank`, `af-phase3-reader`, `af-arecency`, `af-b1-structured` — and a fifth
harness (the docs runner) lost a **53-minute ingest** to the same defect before
patching its own second copy. All four authors converged on a byte-identical
regex. Only the comments differed.

That is the integration debt, denominated in work: one four-line fix, discovered
five times, at a cost including one destroyed ingest — and it blocked the
A-recency arm from running at all (*"Both arms of this measurement died on
[the drain parser defect] on the first attempt."*).

The resolved comment in `gate_runtime.py` records this, because the next agent to
touch shared harness plumbing on a branch should know what it costs.

## 3. What landed

Merge order, chosen so the deepest shared spine resolves once:
`accuracy-first` (ff) → `af-r6-mine` → `af-arecency` → `af-rerank` →
`af-phase3-reader` → `af-b1-structured`.

Three resolutions were judgement calls, recorded here rather than buried:

**`gate_runtime.py`** — comment-only conflict, code byte-identical in three of
four variants (`af-b1-structured` used `(\d+)` where the others used
`(0|[1-9]\d*)`; semantically equivalent given `re.fullmatch`). Took the strictest
regex and consolidated the four comments into one.

**`code_lane_run_memphant.py`** — the only SEMANTIC conflict. `af-rerank` added
four rerank-liveness fields; `af-phase3-reader` flipped the recorded default from
`overlap` to `bm25-code`. Both were kept. The `or "overlap"` fallback was not
merely stale, it was **the four-week default gap bug in miniature**: a harness
default that does not track the shipped default. The comment now says so.

**`evidence_contract_registry.json`** — `contracted` unioned by path, 6 + 7 → 8
distinct, nothing dropped. The ratchet requires clearing debt by writing a
contract, never by deleting an entry, so union is the only correct resolution.
`candidates` 55 → 56 follows; the retrofit report was regenerated, not hand-edited.

## 4. Verification

- `cargo build --workspace` — clean, nine crates.
- `cargo test --workspace` — **0 failed** across the whole suite.
- `python3 -m pytest tests/` — 2 failed / 1112 passed / 15 skipped.

Both remaining failures are pre-existing evidence-contract debt (three
unregistered promotion-capable artifacts), present on the leaf branches before
this merge and untouched by it. They are being closed separately on `w1-evidence`.

Two `test_syndai_gate_contract.py` failures appeared **only** in the scratch
integration worktree and vanished at the canonical checkout path: that test
resolves the Syndai corpus as a sibling of the repo root, and under
`/private/tmp` it selected a stale non-repo `/private/tmp/Syndai` and raised
instead of skipping. A locator-robustness fix is folded into `w1-evidence`.

## 5. The hot-path SLO flake — shipped

`fast_mode_recall_holds_release_hot_path_slo` asserts p50 < 200 ms and its name
says *release*, but nothing enforced it, so `cargo test --workspace` — the
documented pre-commit command and CI's own Rust step — ran it in DEBUG, where the
same work takes 12–17 s against 2.56 s and p50 lands on top of the bar. Measured
2026-08-01, same binary, three consecutive runs at loadavg ~34: pass, pass,
**FAILED at 206.409 ms**; an earlier run under heavier load failed at 269 ms.

A latency assertion with a 3% margin on a shared developer machine is not
measuring the code, and a suite that goes red at random is how a real regression
gets waved through.

Fixed on `af-rerank` (`1610d8f7`) and now on trunk: `#[cfg_attr(debug_assertions,
ignore = "...")]` — **visibly skipped, not silently compiled out** — plus a
`Hot-path SLO (release)` CI step so the bar still runs where it means something.
Verified both ways at the merge: debug reports `1 ignored` with the reason
printed; release passes in 0.27 s.

## 6. Standing rule added

**Shared harness plumbing lands on trunk before a lane branches off it.** A lane
branch may add fields to provenance and may add arms; when it fixes something
every lane uses, that fix goes to trunk first. Five independent rediscoveries of
a four-line parser is the measured cost of the alternative.

Corollary: **integrate leaves on completion, not at the end of a program.** The
merge above was mechanically trivial — one real conflict in 219 commits. It was
expensive only because it was deferred six weeks, during which one flagship
result was measured two fixes behind trunk and had to be re-run.

## 7. State after

One worktree, one branch, one lineage. Nothing pushed to `origin/main`.

Uncommitted artifacts rescued before pruning (26 MB, 19 files) to
`~/.memphant-private/rescued-2026-08-01/`, including the B1 recency-ablation run
(245 KB, ~34 min of unreproducible ingest) that existed only as an untracked file
in a worktree about to be deleted. That is the second near-miss of this class,
after the ~64k-event code-lane corpus.
