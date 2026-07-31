# Phase 3 — does the coding-lane retrieval margin convert to answer accuracy?

**Date:** 2026-08-01 · **Branch:** `af-phase3-reader` (base `af-dense-on`)
**Reader pin:** `claude-opus-5` · **Status:** PREREGISTRATION (§1–§5 committed before
the first paid call; results appended below the marked line)

Everything banked on the coding lane is retrieval. On the decontaminated
paraphrase bank the shipped arm reaches packed r@10 **0.5889** against a scoped
BM25 control at **0.2556**. Nobody outside this program cares about recall@10.
The question that decides ownership (d), and the only externally meaningful
claim available, is whether that margin converts into **answer accuracy**. It
has never been measured. This is that measurement.

---

## 1. The reader pin — read from the doc, not from memory

The standing rule in session memory names `claude-opus-4-8`. It is stale.
`docs/superpowers/plans/2026-07-27-accuracy-first-program.md:285` and `:302`
both pin the Phase 3 reader to **`claude-opus-5`** — "the model Syndai's Claude
Code executor serves as its workhorse default" — and commit `bf2c87c3` re-pinned
the eval reader to it. **`claude-opus-5` is the pin used here.**

The instrument register recorded that **no opus-5 price exists anywhere in this
repo** (§6.1) and that the price must be pinned at authorization time. Fetched
fresh from the OpenRouter model catalogue ($0, unauthenticated):

| model id | context | prompt USD/M | completion USD/M |
|---|---:|---:|---:|
| `anthropic/claude-opus-5` | 1,000,000 | **5.00** | **25.00** |

Both figures are pinned into the authorization packet as
`provider_max_price_usd_per_million`, which OpenRouter enforces provider-side
via `provider.max_price` — so a price change upstream fails the call rather
than silently costing more.

Judge: **`claude-opus-5`**, same model, `--judge-profile rag-supported-v1`.

## 2. Arms, and the stage gate that has to come first

The coding lane owns one permanently VOID number — "MemPhant 0.506 vs BM25
0.806" — created by scoring MemPhant *after packing* against BM25's *plain
top-10*. That asymmetry is not a past mistake to remember; it is **built into
the two runners**:

* `code_lane_run_memphant.py:817` writes the bodies `/v1/recall` returned —
  already budget-packed server-side at `--budget-tokens`;
* `code_lane_run_deterministic.py:125` writes the raw BM25 top-k with **no
  budget applied at all**.

Feeding those two files to a reader unchanged recreates the void comparison one
layer downstream, where it is harder to spot because the number now looks like
accuracy. So before any arm reaches a reader, `scripts/code_lane_reader_prepare.py`
runs **every** arm through **one** `gate_common.pack_evidence` at **one** k and
**one** budget — including the arms already packed, where the pass is a no-op
and is asserted to be one — and records each arm's token profile before and
after. `scripts/code_lane_reader_compare.py` then **refuses to run** unless
every reader report is bound by sha256 to the same equalization manifest and
every arm shares one reader model and one judge model.

| arm | retrieval | reader prompt | role |
|---|---|---|---|
| `memphant` | `bm25code_dense`, the shipped default, attempt-scoped | evidence | treatment |
| `bm25scoped` | deterministic BM25, **same attempt scope**, same query string | evidence | control |
| `nomemory` | none — empty pack | **closed-book** | saturation check |

Common to `memphant` and `bm25scoped`: same 180 goldens, same corpus, same
attempt-scoped haystack, same `retrieval_query(golden)` string, k=10, budget
8192, one packer, one reader, one judge, one prompt.

**The no-memory arm is deliberately not prompt-identical, and that is not a
defect.** Under the evidence prompts it would be *inert*, not neutral: those
prompts order the reader to answer from the evidence only, and the calibrated
abstention line then tells it to abstain whenever no item bears on the question
— which, with an empty pack, is always. It would abstain on every row, score 0
by construction, and say nothing about whether the reader already knew. It
answers a different question ("is this bank saturated?"), so it gets the prompt
that question needs, and `reader_profile` is stamped in every report. It is
**never** the paired comparator for a memory claim.

### 2.1 The endpoint, and why it is not the default one

Under `rag-supported-v1` the report's `correct` field is
`answer_correct AND fully_supported`, and `fully_supported` **cannot** be true
against an empty pack — the strict parser rejects a true flag with no cited
evidence ranks. Verified live in the dry run below: the no-memory arm returns
`answer_correct: true, fully_supported: false, correct: false`. Scoring on
`correct` would make the no-memory arm 0 by construction and turn every
comparison against it into a tautology.

**Primary endpoint: `answer_correct`** — the judge's verdict that the reader's
answer is correct against the gold answer. It is the only field that means the
same thing for an arm with a pack and an arm without one. **Secondary
endpoint: `correct`** (grounded correctness), reported for the two arms that
both have packs.

Neither is retrieval@k. Both grade end behaviour.

## 3. The $0 stub round trip — done, before any authorization

The instrument register's single highest-leverage governance recommendation
(§4.5) is: *no paid authorization for any lane whose adapter has not completed
a $0 stub round trip against the current strict contract since the last
contract change.* Three external instruments failed at first contact on our own
side, two of them after money was authorized.

`scripts/openrouter_stub_server.py` serves a loopback OpenRouter that honours
the `response_format` json_schema the caller sends, so `run_reader.py`'s strict
parsers are genuinely exercised rather than trivially satisfied. It is
loopback-only and receives **no real credential** — a stub URL that could be
any host, carrying a live bearer token, is an exfiltration primitive.

Result, 6 questions × 3 arms, full paid code path (manifest validation →
campaign ledger → pre-call reservation → request → strict parse → judge →
settlement → report), **$0.000036 of stub-reported cost, zero real dollars**:

| observed at the stub | value |
|---|---|
| calls | **36** = 18 reader + 18 judge (6 × 3 arms × 2) |
| model on every call | `anthropic/claude-opus-5` |
| `response_format` | present, `strict: true`, on every call |
| `provider` block | `{"require_parameters": true, "max_price": {"prompt": 5.0, "completion": 25.0}}` |
| `max_tokens` / `temperature` | 1024 / 0 |
| Authorization header | `Bearer stub-no-credential` — no real key left the process |
| distinct system prompts | **3** (evidence reader, closed-book reader, rag judge) |
| reader prompt size | **259 – 23,263 chars** |
| reader calls with zero evidence ranks | **6** — exactly the no-memory arm |
| generation-stats fallback | exercised 36× against the stub, not the live host |
| judge fire rate | **1.00**, `judge_parse_status: strict_valid` on every row |

Three defects were found and fixed *at $0*, which is the whole point:

1. `code_lane_reader_packet.py` created a zero-byte ledger; `_replay_journal`
   treats that as truncated and refused to open the campaign. An absent path is
   the valid empty state.
2. The stub's `usage.cost: 0` sent settlement to the generation-stats fallback,
   which was **hardwired to openrouter.ai** — a "stub" run would have reached
   the live host with a fake key. `openrouter_generation_lookup` now takes a
   `base_url`.
3. The no-memory arm has no retrieval report, but `--engine openrouter`
   requires one; it is now bound to the stage-equalization manifest, which is
   the correct provenance document for a minted arm.

Artifacts: `docs/build-log/artifacts/track-r-paraphrase/reader/stub/`
(gitignored — the reports carry gold answers verbatim).

## 4. Power, computed before spending

Two-sided exact (conditional binomial) McNemar at α=0.05, power integrated
unconditionally over `N_d ~ Binomial(n, ψ)`, via `scripts/instrument_power.py`.
**ψ for the reader endpoint has never been measured on any lane**, so the table
is the decision rule, not a prediction:

| n | ψ=0.15 | ψ=0.20 | ψ=0.25 | ψ=0.30 | ψ=0.40 |
|---:|---:|---:|---:|---:|---:|
| **180** — MDE@80% | 0.0836 | 0.0964 | 0.1071 | 0.1172 | 0.1354 |
| **360** — MDE@80% | 0.0587 | 0.0678 | 0.0758 | 0.0829 | 0.0954 |

Required n: (ψ=0.20, δ=0.10) → 168 · (ψ=0.30, δ=0.10) → 250 ·
(ψ=0.30, δ=0.15) → 112 · (ψ=0.40, δ=0.20) → 84 · **(ψ=0.30, δ=0.05) → 975**.

**Sizing argument.** The retrieval margin this run exists to convert is
+0.3333 packed@10 (b=66/c=6 on the trunk arms). A reader that converts even 40%
of it yields δ≈0.13, which n=180 resolves at every ψ in the table. n=180 is
therefore adequate **for the effect the retrieval margin implies**, and
inadequate only if conversion is weak (δ≤0.05), where the required n is 820–975
— unreachable, because the bank is 180 goldens and there is no more of it.

That is a real limit and it is why **both brackets are run** rather than one
bank at a bigger n: 180 more goldens of the *same* bank do not exist, and the
second bank is a second instrument rather than more of the first.

**Preregistered decision rule, fixed before the first paid call:**

1. Primary contrast: `memphant` vs `bm25scoped`, endpoint `answer_correct`,
   paraphrase bank, n=180.
2. `n_d < 6` ⇒ **NOT A MEASUREMENT**, whatever p says. The two-sided exact test
   has no rejection region below six discordant pairs.
3. `p ≤ 0.05` ⇒ the margin converts, by δ, at the realized ψ.
4. `p > 0.05` with `|δ| < MDE` ⇒ **underpowered null**, reported as such with
   the required n, never as "no effect".
5. `p > 0.05` with `|δ| ≥ MDE` ⇒ an adequately-powered null: the margin does
   **not** convert. This is a first-class finding and is stated first.
6. If the no-memory arm is saturated (matching or beating the control), the
   bank cannot express a memory effect and the comparison is void — the
   SWE-ContextBench tranche-1 pathology. Checked on a slice **before** the full
   spend.

## 5. The two brackets

The W0.2 adjudication and its second-pass correction disagree about which bank
bounds which direction, and the disagreement is not resolved:

* the adjudication says paraphrase margins are **lower** bounds;
* the CORRECTION says the bracketing argument is inverted — as coverage falls
  0.396 → 0.1346 the measured fused margin *rises* +0.0667 → +0.2389, so under
  the only relation actually measured the paraphrase margin is the **upper**
  bound;
* and it notes the frame is internally inconsistent, because the bracket needs
  the original bank to be a valid endpoint while the instrument-bias thesis
  needs it to be invalid.

This run does not adjudicate that. It **measures both banks at the same reader,
judge, prompt, packer and lineage**, so the reader-QA margin is reported at
coverage 0.1346 and at coverage 0.396 and the direction of the relation becomes
an observation rather than an argument.

| bank | q→target coverage | control r@10 (reproduced at this HEAD) |
|---|---:|---:|
| paraphrase (`4aed8e99…`) | 0.1346 | **0.2556** ✓ |
| original (`6f549daa…`) | 0.3960 | **0.8944** ✓ |

Human-authored coding queries sit at 0.175–0.287, between them. Both control
figures reproduce their banked values **to the digit** at this worktree's HEAD,
which is the correctness gate that licenses reading anything else.

## 6. Lineage — why the banked arms could not be reused

The trunk W0.2 arms (`~/.memphant-private/track-r-paraphrase/run-trunk/`) ran at
`4a39ce5f`. That commit is an ancestor of this HEAD, but **four
retrieval/packing commits sit between them**:

| commit | subject |
|---|---|
| `409538ea` | complete a partial chunk render to full coverage, headers intact |
| `2552d4c1` | delete the sibling-gather packing lever |
| `91437a23` | align the subject-key exclusion with the arms that own supersession |
| `c02ca460` | default dense + bm25-code — ship the measured `bm25code_dense` arm |

Reusing `run-trunk`'s evidence would have been free and would have been exactly
the lineage drift this program names as its dominant failure mode. **Every arm
here was re-run at this HEAD**, and `91437a23` is also why these runs use the
shipped `MEMPHANT_FACT_EXTRACTION` default instead of the `=0` the trunk arms
needed: that commit fixed the exclusion-constraint drain failure, so the
measured configuration is now the shipped one.

Stamped into `stage-equalization.json` and carried into the comparison
artifact: git head, git-dirty flag, worktree path, corpus sha256, golden sha256,
binary sha256s, harness env, packer sha256, and each arm's evidence sha256
before and after equalization.

---
<!-- RESULTS APPENDED BELOW THIS LINE AFTER THE PAID RUN -->
