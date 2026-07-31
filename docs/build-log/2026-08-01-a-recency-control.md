# A-recency — the control the bitemporal edifice has never been measured against

**Date:** 2026-08-01 · **Worktree:** `/Users/sidsharma/Memphant-af-arecency` ·
**Branch:** `af-arecency`, based on `accuracy-first` @ `d01affad`
**Cost:** $0. No paid model call on any path. Deterministic regex-derived gold only.

> **§1–§4 are the PREREGISTRATION and were committed before either arm ran.**
> §5 onward is the result. Nothing in §1–§4 was edited after a number existed.

## 1. The question

MemPhant carries a bitemporal edifice: transaction-time vs valid-time,
`tstzrange(valid_from, valid_to, '[)')` under a GiST exclusion constraint,
supersession closing generations, remainder rectangles that tile rather than
overlap, `subject_generation`, `fact_key`, trust-gated promotion. It is the
single largest source of complexity in the substrate and it has already caused
one production-blocking defect (`memphant_memory_unit_subject_valid_excl`
rejecting every served ingest under `MEMPHANT_FACT_EXTRACTION=1`, fixed by
`20260731_007`).

It has never been measured against a trivial control.

**A-recency** is that control: *the most recent assertion about a subject wins.*
No supersession, no generations, no valid time, no trust promotion.

## 2. The control, and exactly what it bypasses

`MEMPHANT_A_RECENCY_CONTROL=1`, read once per process and cached
(`crates/memphant-core/src/lib.rs`, `a_recency_control_enabled`). Two call
sites, deliberately no more. No strategy trait, no config subsystem, no
abstraction layer.

**Write side** — `supersedes_own_kind` returns `None` for every arm. The
condition at the supersede branch is `explicit_subject && (supersedable_kind.is_some()
|| target_unit_ids.is_some())`, so with `None` returned and no caller-directed
targets the branch is never entered and every candidate appends. Consequently
*nothing* is ever written that the edifice would write:

| the edifice writes | under A-recency |
|---|---|
| `UnitState::Superseded` on the prior generation | never set — every row stays `Active` |
| `transaction_to` on the prior generation | never set |
| `valid_from = now` / `valid_to` on the closed generation | never set — every range is `(-inf, inf)` |
| remainder rectangles (`correction_rectangles`) | never minted |
| `Supersedes` / `Contradicts` edges | never minted |
| at-most-one-open-row-per-subject-key | not maintained |

**Read side** — `retain_most_recent_per_subject` collapses the fused recall pool
to one candidate per `fact_key`, chosen by `observed_at` (ties on body, matching
the fusion sort's own tie-break; rows with no `fact_key` are untouched). It
consults neither `state`, nor `valid_from`/`valid_to`, nor `transaction_to`, nor
`subject_generation`. `observed_at` is carried on every `StoredMemoryUnit`
independently of the bitemporal columns, so the control needs nothing the
edifice provides.

**The database also drops `memphant_memory_unit_subject_valid_excl`** in the
control arm. This is not a loosening — that constraint asserts the invariant
*supersession itself maintains*, so with supersession off the second assertion
on a key would be rejected at insert. The constraint is part of the machinery
under test. Scratch DB only.

*This is itself a finding worth recording before any score: **you cannot simply
not use the edifice.** The schema refuses. The complexity is load-bearing at the
DDL level, not merely at the code level.*

## 3. Instrument choice, and its limits

**Chosen: the MemoryCode preference lane, oracle-keyed (Arm P), 1063 probes over
257 instances.** `CohereLabsCommunity/memorycode` rev `32d888b1…`, Apache-2.0,
sha256 `1edb1238…`. Primary endpoint **latest-state-wins** (`appropriate_application`):
when a coding convention has been superseded, does the *current* session outrank
the retired one?

**Why this one.** The comparison has to run on a lane where supersession is the
point *and where the edifice can actually express itself.* Three candidates were
considered:

- **Arm A′ (default ingest), rejected.** Measured 2026-07-31: `memory_edge` empty,
  8147/8147 units `episodic` and `active`, zero predicates. The edifice never
  fires because nothing produces a subject key. Comparing an inert edifice to a
  control measures nothing — and a lane where the edifice cannot express itself
  is a rigged test in its disfavour, which is exactly as worthless as the reverse.
- **LME-S knowledge-update / temporal-reasoning, rejected.** `temporal_score`
  requires the literal token `current`/`latest`/`now` *and* a Semantic+Active
  unit; the lane has no subject-keyed write path at all. Same inertness problem,
  with a weaker gold.
- **Arm P (oracle-keyed), chosen.** Measured 2026-07-31: **7198 `supersedes` +
  3599 `contradicts` edges, 3599 superseded units, 0 with an open transaction, 0
  remainders ever recalled, LSW 0.5795.** This is the only regime in the repo
  where the full bitemporal machinery demonstrably runs end-to-end through
  Postgres at scale. If the edifice is worth anything, it is worth something
  here.

**What this instrument can decide.** Given a *correct* subject key handed
identically to both arms, whether the bitemporal state machine beats
`ORDER BY observed_at DESC` at telling a live rule from a retired one, at
n=1063 probes / 257 instances.

**What it cannot decide.** (a) Nothing about MemPhant vs a lexical baseline —
the key is derived from the same `topic` field the gold labels are, so both arms
are handed the grouping and neither number is comparable to Arm B. (b) Nothing
about lanes where valid-time is genuinely *bounded* — MemoryCode assertions are
unbounded "current convention" statements, so the valid axis is degenerate here
and `correction_rectangles`' tiling has nothing to tile. A win for A-recency on
this instrument therefore does **not** prove the edifice is worthless for
bounded-interval facts; it proves it is not earning its keep on the
unbounded-supersession shape, which is the shape our #1 user pain actually has.
(c) Retrieval-level only. No reader, no judge — which is what makes it $0.

## 4. Preregistered analysis and decision rule

Verbatim `scripts/preference_lane_analysis.py`, the same script and seed
(`20260801`, 10,000 resamples) the lane's prior three arms used. Arm A = full
bitemporal, Arm B = A-recency.

- Primary: **paired exact two-sided McNemar** on `appropriate_application`, with
  b, c and the per-item vectors preserved in the artifact.
- Cluster bootstrap over the 257 instances is reported alongside, because probes
  nest (mean 4.1/instance) and the McNemar is anti-conservative here.
- **Realized** psi from this run's own cells; MDE at 80% via
  `scripts/instrument_power.py`. No assumed psi.
- **The n_d >= 6 structural floor.** Exact two-sided McNemar has no rejection
  region below six discordant pairs, so `p = 1.0` there is arithmetic, not
  evidence. If this run lands below it the verdict is written as **"no
  measurement"** plus the n required — not as a tie.

**Mechanism liveness is a gate, not a footnote.** Both arms must prove which one
they are, from the database, before any score is read:

| check | bitemporal arm | A-recency arm |
|---|---|---|
| `supersedes` edges | > 0 | **0** |
| `superseded` units | > 0 | **0** |
| valid-closed rows | > 0 | **0** |
| **open rows sharing a subject key with OVERLAPPING valid ranges** (self-join) | **0** | **> 0** |
| subject keys carrying more than one live row | 0 | **> 0** |

The overlap check is a self-join, deliberately **not** an open-row count. The
bitemporal model correctly leaves the historical remainder open beside the
current generation — remainders *tile*, they do not overlap — so an
`open_semantic <= 1` assertion is the wrong test and would pass for the wrong
reason on a broken tree. The self-join computes exactly the predicate of
`memphant_memory_unit_subject_valid_excl` instead of trusting it.

Lineage (git head, branch, dirty flag, sha256 of the served server and worker
binaries) is stamped into every arm report, because lineage drift across ~19
worktrees is this program's dominant failure mode.
