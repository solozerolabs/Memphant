# A4′ — key production: the frontier, and the regime that was hiding it

**Date:** 2026-08-01 · **Worktree:** `Memphant-w1-keyprod` · **Branch:** `w1-keyprod`
**Base:** `main` @ `5d7b9d5a` · **Cost:** `paid_model_calls: 0`. No model, no
network, no database, no server, no binary.
**Artifacts:** `docs/build-log/artifacts/2026-08-01-key-production/recovery.json`
(passing `evidence_contract`, `decisional: false`, full lineage) and
`…/ledger-rescored.json` (§3.4a).
**Amended 2026-08-01 after `w1-b1arm` and `w1-arecency` reported.** Four
corrections, all verified by me from their banked artifacts rather than their
summaries: the B1 row (§3.4), the τ-scale error in my prior (§3.4a), a policy
conclusion of mine that their ablations falsify (§2), and a stale ceiling that
changes my own decision rule (§4a).

---

## 0. Headline

**§4's 0.008 and 0.208 reproduce — and they were measured under a regime four
times stricter than the mechanism they were calibrating.**

The plan of record (§7) treats "a gold-independent, body-derived key recovers 8
of 1,063 gold groups (0.008); the best variant reaches 221/1,063 (0.208)" as the
measured statement that key production is genuinely hard. I rebuilt that
measurement as a committed script and reproduced all four rows to within a few
groups of 1,063, in the correct order. So the figures are sound.

But the regime they were computed in imposes two constraints that supersession
does not:

1. **one key per session** — while Arm P, the ceiling those numbers are compared
   against, mints **one unit per declaration** (14,088 units from 8,147
   sessions), not one per session; and
2. **every session in a group must share that one key** — while a supersession
   edge points backwards to **one** incumbent at a time.

Scored under the regime the mechanism actually uses, **the same rules, unchanged,
recover 0.535–0.899 of gold groups instead of 0.056–0.211.**

That does not mean the problem is solved, because §4 measured only recall and
recall alone is maximised by a constant key. **The binding quantity is a
precision/recall frontier, and the best measured net operating point is
`pre3_content_words`: 0.5353 recovery at pair-precision 0.898, costing 44 of
1,063 golds.** Where that lands on latest-state-wins is **not measured** — see §5. (The
ceiling to compare against is **0.6237**, not the plan's 0.5795; see §4a.)

---

## 1. What was missing, and now is not

The §4 measurement's **code was never committed**. Two numbers on the program's
critical path were unreproducible by anyone, including their author. That is now
`scripts/measure_key_recovery.py` (offline, DB-free, $0) with
`tests/test_key_recovery.py` (7 tests, green).

**Reproduction, against the published figures:**

| §4 rule | published | reproduced |
|---|---:|---:|
| quoted-literal-stripped content-word set | 8 / 1063 | **18 / 1063** |
| one content word before the literal | **221 / 1063** | **224 / 1063** |
| two content words | 118 / 1063 | 124 / 1063 |
| three content words | 70 / 1063 | 60 / 1063 |

The ordering (221 > 118 > 70, with the set rule near zero) reproduces exactly and
every magnitude is within a few groups. The bank shape is identical to the
adapter's: **257 instances, 1,063 gold groups, 8,147 sessions, 3,616
current-stale pairs**, inherited from `load_memorycode` rather than re-derived.
Residual differences are attributable to the stoplist and the quoted-literal
regex, neither of which §4 records. **Verdict: REPRODUCED.**

### 1a. A defect found on the way, and it is a trap for the next lane

`external_instrument_adapter.QUOTED` (`'[^']*'|"[^"]*"`) is correct on the clean
`topic` field and **catastrophic on a body**. Prose is full of apostrophes, so on
a real session it matches spans like `'s a pleasure to finally meet you. I'`
rather than `'q_'`. Applying it to bodies costs roughly **20 points of recovery
on every rule** (e.g. `pre1` 0.699 vs 0.899). `measure_key_recovery.LITERAL`
bounds the delimiters by non-alphanumerics; `test_the_naive_topic_regex_is_not_usable_on_prose`
pins the difference so nobody reaches for the topic regex on prose again.

---

## 2. The frontier

Full bank. Every rule reads `session["text"]` and nothing else — the oracle
`declarations` field is **popped off the units** before any rule sees them, so
gold-independence is enforced, not promised.

| rule | §4 regime | supersession regime | **net** | pair-P | pair-R | golds lost | keys/session |
|---|---:|---:|---:|---:|---:|---:|---:|
| `sentence_content_set` | 0.0169 | 0.3518 | **0.3518** | **0.992** | 0.196 | **5** | 1.04 |
| `pre1_content_word` | **0.2107** | **0.8993** | 0.3791 | 0.271 | 0.724 | 589 | 1.05 |
| `pre2_content_words` | 0.1167 | 0.6726 | 0.5146 | 0.618 | 0.534 | 232 | 1.00 |
| `pre2_unordered` | 0.1035 | 0.6802 | **0.5202** | 0.614 | 0.538 | 236 | 1.00 |
| **`pre3_content_words`** | 0.0564 | 0.5353 | **0.5136** | **0.898** | 0.405 | **44** | 0.91 |
| `verb_head_pair` *(bank-fit)* | 0.1072 | 0.6924 | 0.3998 | 0.360 | 0.469 | 402 | 0.95 |
| `jaccard_canon` τ=0.4 | 0.3001 | 0.8937 | 0.3735 | 0.238 | **0.869** | 575 | 0.84 |
| `jaccard_canon` τ=0.5 | 0.1976 | 0.8081 | 0.3970 | 0.258 | 0.765 | 469 | 0.89 |
| `jaccard_canon` τ=0.6 | 0.1016 | 0.6933 | 0.4835 | 0.402 | 0.606 | 250 | 0.96 |
| `jaccard_canon` τ=0.7 | 0.0527 | 0.5691 | 0.4976 | 0.735 | 0.448 | 88 | 1.00 |
| **`constant_key` CONTROL** | **1.0000** | **1.0000** | 0.1345 | 0.043 | 1.000 | 920 | 1.00 |

- **§4 regime** — one key per session, every session in the group must share it.
- **supersession regime** — all keys, gold shares a key with *any* earlier declarer.
- **net** — recovered **and** the gold was not itself wrongly retired by a later
  session. This is the honest headline: it charges a rule for its own false merges.
- **golds lost** — groups whose gold was closed by a wrong edge. Directly
  destroys a probe.

**The constant-key control is the row that matters most.** It scores 1.000 on
§4's metric and is worthless: pair-precision 0.043, 920 golds destroyed. Group
recovery reported alone is not interpretable, and §4 reported it alone. It is
kept permanently in the artifact so that cannot recur.

**Wrong merges are asymmetric — but the strong reading of that is now
falsified.** Across every rule the ratio of *harmless* wrong merges (retiring a
session that is nobody's gold) to *harmful* ones runs **6.1:1 to 8.5:1**.
Supersession points backwards and this instrument's distractors are always
earlier declarations, so a semantically-wrong edge usually retires something
already dead. That is a property of *this corpus*, not a general law.

**Corrected after B1 reported (§4a).** I originally read this as strengthening
B1 §3.1's claim that indiscriminate retirement is *valuable*. It does not, and
B1's ablations settle it empirically. The asymmetry supports only the weaker
claim that a keying rule's **errors are cheap** — which is what the ratio
actually measures. Whether retiring things *indiscriminately* pays is a separate
question, and the answer is "partly": B1's uniformly-random ablation is worth
**+0.0259**, real but half of B1's total. Decisively, the asymmetry argument
predicts that a **recency** policy should be the best uninformative rule (retire
the most recent live prior, which is most likely already dead) — and B1 measured
recency as the **worst** arm of the set (LSW 0.3284 vs random's 0.3330), because
the most recent live prior is the row most likely to be a *current gold*. The
asymmetry is real; the policy conclusion I drew from it was wrong.

---

## 3. Strategies, assessed

### 3.1 Adapting `extract_facts` to second-person directives — **do not**

`crates/memphant-core/src/service.rs` §6440–6616 is a five-pattern first-person
matcher (`match_superlative`, `match_my_new_noun`, `match_my_noun_is`,
`match_identity`, `match_preference_verb`), and `extract_facts` skips every turn
whose role is not `user`. Arm F's 2 units from 8,147 episodes is fully explained
by those two facts, and the plan's diagnosis (corpus mismatch, not brokenness) is
correct.

Adapting it means a sixth matcher for imperative directives plus relaxing the
role filter — perhaps 80 lines. **The ceiling of that work is already measured
here**: an imperative-directive matcher is exactly the `verb_head_pair` /
`pre_n` family, and the whole family tops out at **net 0.52**. Writing it in
Rust buys nothing the offline measurement has not already priced, and
`verb_head_pair`'s `IMPERATIVES` list — written by reading this bank's
directives — is labelled `bank_fit: true` for the reason it should not be
shipped: it is template-matching a synthetic corpus. **The honest general rules
(`pre_n`, `jaccard_canon`) beat it, and they need no lexicon.**

### 3.2 Caller-supplied subject/predicate — **the brief understates what exists**

The brief says "the fields EXIST in the type but REST and MCP never set them."
Checked, and the sharper statement is:

- `RetainUnitPayload` (`crates/memphant-types/src/lib.rs:1745`) has **`fact_key:
  String` — mandatory, not optional**. A caller can and does author the key.
- **Arm P and Arm K both use that public path**, through `POST /v1/episodes`
  with a `unit` payload. Arm P's 7,198 supersede edges through Postgres are
  proof the caller-authored-key path works end to end **today**.
- What has no key is the *episode* path: `compile_job` (`service.rs:5305`) emits
  one `ReflectCandidate` with `subject: None, predicate: None`, and MCP's
  `lib.rs:885` hardcodes `predicate: None`. MCP file memory (`file_memory.rs:136`)
  *does* set `predicate: "memory_file"`, so the surface is not uniformly blind.

So caller-supplied keys are **not an unbuilt mechanism — they are a live,
measured one.** The unbuilt thing is a *caller that supplies a good key*. On
MemoryCode there is no such caller, which is why this strategy is unmeasurable on
this bank. In production the caller is Syndai's coding agent, which is receiving
the directive in context and is far better placed than any regex to name what it
is about. **This reframes strategy 2 from an engineering task to the product bet
already written down as D1**, and it makes D1 considerably more attractive than
its position in the plan suggests.

### 3.3 Clustering / canonicalisation — measured, and it needs a read

`jaccard_canonicalisation` single-links directive sentences within a scope by
content-word Jaccard. **It is not a pure body function**: it needs the other
units in the scope, i.e. a `/v1/recall` on the write path — the same shape B1's
extractor already uses, so the cost is known and acceptable. At τ=0.7 it reaches
net 0.4976 at precision 0.735; at τ=0.4 it reaches the highest pair-recall of any
rule (0.869) at unusable precision. **It is dominated**: `pre3_content_words`
matches its net recovery (0.5136 vs 0.4976) at higher precision (0.898 vs 0.735),
half the golds lost (44 vs 88), and **no write-path read at all**. Per KISS, the
zero-dependency rule wins.

### 3.4 Supersede by exact prior unit id (`e165b4b9`) — **B1 landed; row below**

Owned by `w1-b1arm`, which landed while this log was being written
(`ff022d2c`, `a7f3e550`, branch `w1-b1arm`, lineage `0ecf8cb2`, server
sha256 `a06f3a29…`). **I verified every figure below directly from their banked
artifacts rather than from their summary**, including the confirmatory analysis
JSONs.

| arm | LSW | full-bank |
|---|---:|---|
| S — B1 extractor, τ=0.25 | **0.3622** | hit@1 0.2709, hit@k 0.8071 |
| S0 — same arm, supersession off | 0.3142 | the isolator |
| R3 — uniformly random live prior | 0.3330 | reads **no bodies at all** |
| R — recency (most recent live prior) | 0.3284 | the **worst** arm |

Confirmatory slice: **S − S0 = +0.0506**, CI95 [+0.0269, +0.0753], n_d 117.

**Cite it with the caveat, because the naive row overstates it roughly 2×.**
R3 proposes 1,098 edges against S's 1,091 while comparing zero bodies, and
captures **+0.0259** of S's +0.0506 (n_d 59). B1's *semantic* increment over a
rate-matched coin flip is **+0.0247 with n_d 146 and exact McNemar p = 0.116** —
which by B1's own preregistration is a **NEGATIVE**. The honest frontier row is:
**the edge mechanism pays; the semantics are not established.**

The mechanism reasoning I wrote before B1 reported still holds and is worth
keeping: `e165b4b9` **bypasses key matching, not key production**. The candidate
still needs a `fact_key` (mandatory in the payload) and still needs to decide
*which prior unit to name*. Its genuine advantage is that naming a uuid **cannot
collide**, so a wrong edge is a wrong *choice* rather than a systematic merge of
two conventions that hash alike.

### 3.4a My τ=0.7 prior was on an incommensurable scale — and why that matters

I offered `jaccard_canonicalisation` at τ=0.7 (recovery 0.5691) as a prior for
where B1 would land. **The live distribution is on a completely different
scale**, and I verified this from B1's banked ledger myself: 7,890 live candidate
scores, **median 0.194, p95 0.283, max 0.5213**. Nothing in the live pool ever
reaches 0.7; τ=0.35 would have fired **32 times in 7,890**.

The cause is not that my rule is optimistic — it is that **we were comparing
different objects.** My rule takes the Jaccard between *directive sentences*;
B1's takes it between *whole session bodies*. A MemoryCode session is ~2,300
characters of mentor small talk around one directive, so a body-level Jaccard is
dominated by filler every session shares and the discriminating tokens are a
rounding error. **The two τ scales are not comparable, and no τ on one transfers
to the other.**

**So I tested the substitution on B1's own ledger** — same 7,890 candidate pairs,
same pairs their ranker chose, changing only what is compared
(`scripts/rescore_structured_ledger.py`, $0, no ingest,
`ledger-rescored.json`):

| firings | body Jaccard (B1 live) | directive-sentence Jaccard |
|---:|---:|---:|
| 100 | 0.460 | **1.000** |
| 250 | 0.416 | **0.992** |
| 500 | 0.402 | **0.972** |
| **1091** (B1's operating point) | **0.341** | **0.765** |
| 2000 | 0.296 | 0.556 |

Precision here is the fraction of fired pairs that genuinely co-declare a
convention. **At B1's actual operating point, changing the similarity unit from
the body to the directive sentence more than doubles precision, 0.341 → 0.765.**

This says B1's extractor is starved by its **unit**, not by its **threshold** —
which reframes B1's own recommendation #2. Re-calibrating τ on the true body-level
distribution optimises a scale that tops out at 0.52 and mixes filler into every
score; swapping the unit changes the distribution being calibrated. Both are
free, and the unit swap should come first.

**What this is not.** It re-scores pairs the live ranker already returned as
top-1. It does not tell us what a sentence-level scorer would *retrieve*, and it
is not latest-state-wins. The artifact is `decisional: false` and says so.

---

## 4. Arm K — built, preregistered, **not run**

`--arm derived` is now in `scripts/external_instrument_adapter.py`
(`ingest_group_derived`). It is a byte-for-byte parallel of Arm P — same
`preference` kind, same `predicate`, same retain shape, same recall, same scorer
— with **exactly one variable changed**: the key comes from a
`measure_key_recovery` rule reading the body instead of from `declarations`.
The oracle field is `pop`ped before the rule runs. The `A′ → K → P` triple
therefore prices key quality alone, at the same pipeline stage on the same
haystack.

**Preregistration.**

- **Rule:** `pre3_content_words`, chosen on the frontier in §2 (best net recovery
  at high precision and fewest golds lost), fixed **before** any arm ran.
- **Primary endpoint:** latest-state-wins, identical to
  `2026-08-01-preference-lane-prereg.md`. Analysis is
  `scripts/preference_lane_analysis.py` unchanged: cluster bootstrap over the 257
  instances, 10,000 resamples, seed 20260801, plus exact two-sided McNemar and a
  cluster permutation test.
- **Decision rule:** the fraction of the oracle's headroom that K closes,
  `(LSW_K − 0.3123) / 0.3114` — see §4a: the denominator is **0.3114, not
  0.2672**. A cluster-bootstrap CI on ΔLSW including 0 is a **NEGATIVE** and
  will be reported as one.
- **Comparator:** Arm A′ must be the **on-tree** re-run, not the banked
  `af-w11-writepath` figure. Comparing K on `main@5d7b9d5a` against an arm
  measured two merges behind is the lineage-drift failure A1 exists to stop.
- **Power floor:** below `n_d = 6` discordant pairs, write "NOT A MEASUREMENT"
  and the required n. Never "a tie".
- **Mechanism liveness gates the score:** `memory_edge` supersede count > 0,
  `memory_unit` preference count > 0, and `remainders_recalled == 0`, all read
  from the arm's own scratch DB on the bench superuser credential **before** any
  accuracy number is read.

**Why it did not run.** The box was at **loadavg 30.9 with seven concurrent
benches** owned by other sessions; `w1-b1arm` holds ports 39501–39503 and asked
for at most two concurrent full-corpus runs. This worktree also has no built
binaries, so running would have meant a release build on a saturated machine —
which would corrupt the latency figures of every in-flight arm. **A blocked lane
reported accurately beats a fabricated result.** Arm K is code and a prereg, not
a measurement, and it must have a smoke round trip before its first number is
believed.

```bash
cd /Users/sidsharma/Memphant-w1-keyprod && docker start memphant-postgres-1
cargo build --release --bin memphant-server --bin memphant-worker --bin memphant-cli
OUT=docs/build-log/artifacts/2026-08-01-key-production
SRC=~/.memphant-private/w7-instruments/memorycode/data/test-00000-of-00001-a45d1855e46f30cb.parquet
<venv-with-pyarrow>/bin/python scripts/external_instrument_adapter.py \
  --instrument memorycode --arm derived --derived-rule pre3_content_words \
  --diagnostics --source $SRC --out $OUT/arm-k-derived.json --port 39541
<venv>/bin/python scripts/preference_lane_analysis.py \
  --arm-a $OUT/arm-k-derived.json --arm-b <banked arm-a-memphant.json> \
  --out $OUT/analysis-k-vs-a.json
python3 scripts/check_evidence_contract.py --file $OUT/analysis-k-vs-a.json
```

---

## 4a. The 0.5795 ceiling is stale — it is 0.6237 on-tree

Reported by `w1-arecency` and **verified by me from their artifact**
(`Memphant-w1-arecency/docs/build-log/artifacts/2026-08-01-a-recency/arm-bitemporal.json`,
commit `baa267fa`): the oracle-keyed configuration re-run on `main@5d7b9d5a`
scores **LSW 0.6237**, misapplication 0.3396, hit@k **0.9247**.

| | banked (`af-w11-writepath`) | on-tree (`main@5d7b9d5a`) |
|---|---:|---:|
| oracle-keyed LSW | 0.5795 | **0.6237** |
| hit@k | 0.840 | **0.9247** |
| headroom over A′ 0.3123 | +0.2672 | **+0.3114** |

The whole +4.4pp is **retrieval coverage**, not mechanism — same edge counts,
same misapplication shape. So every "fraction of the oracle's headroom closed"
computed against 0.2672 carries ~4pp of base drift and **overstates itself by
about 14% relative**. My Arm K decision rule is corrected above.

This is the plan's own §7 table going stale within a day of being written, and
it is precisely the failure standing rule 1 exists for. **The ceiling figure
should be re-pinned in `2026-07-31-one-plan.md` §7 to 0.6237 with its lineage**,
rather than left as a bare number that three sessions are now dividing by.

## 5. What is **not** claimed

**Key recovery is not latest-state-wins, and nothing here converts one into the
other.** The temptation is to write `0.3123 + 0.514 × 0.2672 ≈ 0.45`. That number
does not appear in this log or in the artifact, because it is an extrapolation
across two different quantities and this program has already voided one headline
for comparing at mismatched stages. Arm P's 0.5795 is itself capped by the
session-granularity of the scoring identity, and a body-derived key inherits that
cap *plus* its own errors. **The only way to place any rule between 0.3123 and
0.5795 is to run Arm K.**

Second: everything here is one synthetic instrument. MemoryCode's directives are
templated (`always start <thing> names with '<literal>'`), which is precisely the
shape a preceding-content-word rule exploits. **The frontier in §2 should be read
as an upper bound on deterministic key production, not a transferable rate.**

---

## 6. Verdict and recommendation

**1. The premise that made A4′ the critical path is weaker than recorded, and
that should be written into the plan.** "0.008" is not the state of the art in
deterministic key production; it is one rule under one over-strict regime. The
honest floor for the same rule family is **net 0.514**. §7 of the plan should be
amended: the gap to close is `0.514 → 1.0` in key recovery, not `0.008 → 1.0`,
and the LSW consequence of that is unmeasured.

**2. Do the unit swap on B1's extractor first — it is free and it outranks
Arm K.** §3.4a shows B1's similarity signal doubles in precision at its own
operating point (0.341 → 0.765) when the compared object changes from the
session body to the directive sentence. That is a re-scoring of already-banked
data, costs nothing, requires no ingest, and it attacks the one result B1 could
not establish: its semantic increment over a coin flip (+0.0247, p = 0.116, a
preregistered NEGATIVE). If the semantics are real, a better unit is what will
show it. **This displaces B1's own recommendation #2 (re-calibrate τ), which
optimises a scale that tops out at 0.52 and mixes filler into every score.**

**3. Then run Arm K.** $0, ~30 minutes, already written, and the only thing that
converts the §2 frontier into a decision. It is also a genuine kill gate: if K
lands at LSW ≈ A′ despite recovering half the groups, then key *recovery* is not
the binding quantity and the whole A4′ framing is wrong — worth knowing far more
than another rule variant.

**2b. Re-pin the ceiling before anyone divides by it again.** §4a: 0.5795 →
0.6237. Three sessions are currently computing "fraction of headroom closed"
against a stale denominator.

**4. Do not adapt `extract_facts` to second-person directives.** Its ceiling is
already measured here (§3.1) and it is below the free rules.

**5. The deterministic band ends at roughly where the third-party number says it
does.** arXiv:2606.15903's deterministic primitives sit at 63.4–68.3%; the
supersession-regime recall column in §2 tops out at 0.899 with unusable precision
and the best *net* point is 0.52. Nothing free is going to reach the 91.7–93.2%
that a mutation-time control-plane hook reaches at ~$0.17 per 385 mutations. **A
deterministic answer will not close this. Plan for the hook.**

**6. The cheapest path to a good key is not extraction at all — it is the
caller.** §3.2 shows the caller-authored-key path is already live and already
proven at scale by Arm P's 7,198 edges. The missing piece is a caller that knows
what the write is about, and in production that caller is an LLM agent that just
read the directive. **D1 (the correction handle bound to Syndai's existing chip)
and caller-authored keys are the same bet, and this measurement is an argument to
promote it above further extractor work.**

**No paid arm is requested.** The free gate that could make a spend worthless —
Arm K — has not reported. Per the necessity test, nothing should be authorised
until it does.

---

## 7. Verification

- `tests/test_key_recovery.py`: **7 passed**. Includes the gold-independence
  assertion driven against the *shipped* `ingest_group_derived` via a recording
  stub, not a copy of it, and the naive-regex trap.
- `scripts/check_evidence_contract.py --file …/recovery.json`: **passes**,
  `decisional: false` with the reason stated in the artifact.
- Corpus sha256 re-verified against `benchmarks/manifests/memorycode.lock.json`
  **before** any rule ran; the script exits 2 on drift.
- Lineage in the artifact: `git_head 5d7b9d5a`, `git_branch w1-keyprod`,
  `git_dirty true` (recorded, not hidden — the script under measurement was
  uncommitted at run time and its own sha256 is stamped),
  `script_sha256 ab64b2c2…`, `pyarrow 25.0.0`, `python 3.14.2`.
  `served_binaries: NONE` — this measurement runs no binary at all, and says so
  rather than leaving the field absent.
- `pyarrow` was installed into a scratch venv outside the repo. **No repo
  dependency was added** for an offline analysis script.
- No guard, bar, or threshold was weakened. No arm was re-run on an unfavourable
  result. No number in this log was estimated or extrapolated.
