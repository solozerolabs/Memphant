# Packing decision-record amendment — 2026-07-30

Status: AMENDMENT to `docs/build-log/2026-07-24-packing-technique-screen.md`.
Cost: $0. No model calls. No checkbox, default, cutover, deployment, or SOTA
claim moves in this document.
Authority: Phase 0 of `docs/superpowers/plans/2026-07-27-accuracy-first-program.md`
(APPROVED FOR EXECUTION).

This amendment corrects a **measurement instrument**, not a result. Every prior
number in the amended record stands as recorded. What changes is which of those
numbers is allowed to gate a decision.

## 1. The free exact-abstention sentinel is RESCINDED as a decision gate for reader-visible levers

The 2026-07-24 screen rejected `pack_render_cap=1200` on this line:

| arm | scored hit@10 | exact abstention | recorded decision |
|---|---:|---:|---|
| current | 2/8 | 3/4 | control |
| render cap 1200 | 8/8 | **1/4** | reject: exact-abstention non-regression failed |

That 3/4 → 1/4 flip is **not an abstention regression.** It is a retrieval-only
proxy, and the proxy is mis-specified for this class of lever. Verified in
source, `score_question` in `crates/memphant-eval/src/bench_lme.rs:624`:

```rust
let correct = abstained || first_answer_rank.is_none();
```

For an `_abs` case the screen scores "correct" when recall abstained **or when
no answer-bearing session was returned at all**. No model was in the loop on
this screen — zero model calls, recorded — so nothing ever set `abstained`. The
whole 3/4 was `first_answer_rank.is_none()`: credit for *not retrieving*.

Cap-1200 packs 7–9 items where the control packs 4–5, so the near-miss trap
sessions surface at rank 1–2 and the proxy flips to "incorrect". But the
LongMemEval gold answers for those sentinels **require the trap content in
context** — the gold answer form is "You mentioned your cat Luna but not your
hamster". A reader cannot abstain correctly without being shown the trap. The
gate therefore punished the arm for admitting exactly the evidence the gold
answer needs.

Power: 2 discordant pairs, McNemar exact p = 0.5. The gate had **zero** ability
to detect the effect it was asserted to have detected.

Artifacts (unchanged, re-read for this amendment):
`docs/build-log/artifacts/next-evidence/packing/lme-s-pilot-current.json`,
`lme-s-pilot-cap1200.json`, `lme-s-pilot-n12.json`.

**Amended rule.** A no-model retrieval proxy may not gate a lever whose effect
is on what the reader sees. Abstention is a **reader-judged** endpoint
(`abstain=true ∧ answer=null`) or it is not an endpoint. The retrieval proxy
survives as a tripwire only, under §2.

Scope of the rescission: it applies to reader-visible packing/ordering/admission
levers. It does not rehabilitate any arm rejected on its own retrieval result —
naive utility density (1/8), submodular order (2/8, no gain), and the
cap-1200+utility mixture (3/8) remain rejected on the scored leg, which this
amendment does not touch.

Consequence: `pack_render_cap` returns to the queue as an **undecided** lever
with a real recorded retrieval win (Δr@10 +0.2349, two seeds; the rung-7
packaged rehearsal recorded r@10 0.6145 → 0.8434 at k=10, budget 8192). It is
not promoted here. It is decided by a powered paired reader-QA run, per lane —
Phase 2 (chat) and Phase 3 (coding).

## 2. n ≤ 12 frozen screens are reclassified as non-decisional tripwires

Any frozen screen with n ≤ 12 is hereby a **tripwire**: it may stop a lane
early on a gross failure, and it may not certify, reject, or promote. At n=12
with 2–4 sentinel rows the achievable resolution is far coarser than any effect
we are hunting, and §1 is the recorded instance of a 4-row leg reversing a
lane's direction.

This reclassifies, in place and without re-running them, every n=12 screen in
the amended record and in
`docs/build-log/2026-07-24-tri-domain-next-evidence.md`. Their retrieval legs
remain as recorded diagnostics.

Standing rule going forward: a screen small enough to be free is small enough
to be non-decisional. Free screens choose what to measure next; they do not
decide.

## 3. SWE-ContextBench first tranche: baseline saturation recorded as TERMINAL for the tranche

The first tranche is not a usable gate and will not be re-run as designed. The
three resolved no-memory baselines (Astropy 15082, Django 34176, Django 35356;
aggregate 10/10 fail-to-pass, 315/315 pass-to-pass) leave a maximum possible
gain of +1 against a preregistered +2. The instrument cannot express the effect
size it was authorized to detect.

Recorded as terminal **for the current tranche**, not for the benchmark: a
future tranche selected for non-saturated baselines would need its own
preregistration and its own authorization packet. No such tranche is scheduled
(`docs/build-log/2026-07-24-swe-contextbench-next-evidence.md`).

## 4. Reader-QA packet re-issued at schema_version 3

The v2 packet at
`docs/build-log/artifacts/rung7-packing-reader-gate/authorization-request.json`
is tombstoned (`authorization: null`,
`status: SUPERSEDED_REJECTED_BY_2026_07_24_FREE_EXACT_ABSTENTION_GATE`). Its
rejection reason is the gate rescinded in §1, so the tombstone is now a
tombstone of a withdrawn instrument.

The v2 file is left byte-unchanged as the historical record. The replacement is
a new file, `authorization-request.v3.json`, carrying the Phase 2 design: the
238-question pool with separate frozen-178 and full-238 analyses, baseline vs
cap-1200 only, McNemar on the 221 scored rows at d_min 7pt, trap-preserving
`_abs` variants as a powered secondary endpoint, and the production-
representative `claude-opus-5` robustness arm.

The packet is **derived, not transcribed**:
`scripts/derive_phase2_packet.py` measures the frozen evidence on disk and
computes the call budget and ceiling from it; `--check` fails on drift, which is
this phase's runnable check. Derived figures:

| quantity | value | basis |
|---|---:|---|
| widest measured frozen evidence row | 25,909 bytes | measured, both arms |
| prompt-token bound per call | 26,000 | one-byte-per-token, rounded up |
| combined max logical calls | 1,610 | 596 reader + 596 judge + 298 worst-case paired re-judge + 120 minting |
| max USD per call | $0.08839600 | provider maxima $2.75/$16.50 per M + 1,024 completion tokens |
| **combined max spend ceiling** | **$142.31756000** | derived; the plan's ~$175 estimate was loose |
| realistic expected spend | $30–60 | observed $0.02–0.03/call on this lattice |
| scored (non-`_abs`) rows | 221 | 238 pool − 17 natural `_abs` |

Frozen-input hashes were re-verified against v2 and are byte-identical
(`baseline-evidence.jsonl` `a3ff8362…`, `rendercap1200-evidence.jsonl`
`143a2455…`). The 12 natural `_abs` rows in the frozen 178 are a measured
count, not an assumption.

**v3 is issued, not authorized.** `authorization` is `null` until the owner
signs it, and it is additionally gated on Phase 1's kill gates. Re-issuing a
packet does not release a dollar.

## What this amendment does not claim

- It does not promote `pack_render_cap`, on any lane. It restores it to
  undecided.
- It does not produce accuracy evidence. Zero model calls; $0 settled.
- It does not move a checkbox. The companion STATUS.md entry is a dated note
  only.
- It does not assert the amended screen's retrieval numbers are wrong. They are
  as recorded; only their decisional authority is withdrawn.
