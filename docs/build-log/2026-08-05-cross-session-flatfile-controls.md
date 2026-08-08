# XS — cross-session flat-file controls: does MemPhant beat the memory a coding agent already has?

**Date:** 2026-08-05 · **Branch:** `xsession-controls` · **Spend ceiling:** $40
· **Status: PREREGISTRATION. The bank does not exist yet; no cell has been
seen. Part A is committed before bank construction begins and is never edited.**

This lane **gates all MCP/coding-agent integration work**. No MemPhant MCP
server, hook, or client surface for coding agents is built unless this lane
authorizes it under the decision rule in A.5.

---

# PART A — PREREGISTRATION

## A.0 The question

*For cross-session, repo-external knowledge — the corrections, conventions,
environment quirks, and failure patterns a coding agent accumulates across
sessions — does MemPhant recall beat the flat-file memory stack the agent
already has (AGENTS.md + LEARNINGS.md + a memory directory with a curated
index, searched with grep/read)?*

This is the lane `s4-controls` did **not** measure. S4 measured repo-recoverable
facts and grep won 96.67% vs 58.89% (p=1.2e-19); the standing conclusion is
that the niche is what is *not* in the repo. But "not in the repo" knowledge is
not memoryless today — its incumbent is flat files plus the agent's own tools.
That incumbent has never been paired against MemPhant. If MemPhant cannot beat
it, there is no coding-agent product story, and better to know for ≤$40.

**The published prior is against us, again.** MemDelta: agent self-memory 42%
vs 47% basic retrieval. S4's own mechanism finding: on a ~122-item haystack
with enumeration available, an agentic loop brute-forces — and a coding agent's
memory directory (Syndai's: 340 files, ~28 KiB always-loaded AGENTS.md chain,
an 18 KB curated index) is exactly that regime. The realistic corpus is
grep-affordable, and that is not a flaw in the eval — it *is* the deployment
condition. If grep-over-flat-files wins at the scale users actually have, the
MCP story is dead at that scale and the honest claim moves to where flat files
break (no supersession, no decay, curation labor), which must then be
demonstrated at a scale where they actually break, not asserted.

## A.1 Instrument — to be constructed, under the acquisition gate

**Corpus (fixed, real, not synthetic):** the Syndai flat-file memory stack as
of a pinned commit/snapshot date:

- `~/Syndai/AGENTS.md` + `~/Syndai/LEARNINGS.md` (the one-line-per-entry
  long-tail ledger, ~82 lines, `key | confidence | source | insight | refs`)
- the Claude session-memory directory for the Syndai project (~340 files +
  `MEMORY.md` index)

Snapshot is content-addressed (`corpus_sha256` over a sorted file manifest);
every runner re-verifies it. Entries carry real dates, real supersessions
(e.g. `SUPERSEDED 2026-07-25` / `CORRECTED claude-2026-07-29` markers), and
real restatements — the corpus has the structure the product claims to manage,
which no synthetic corpus would.

**Goldens: target n=120, mined agent-in-the-loop, not templated.** A golden is:

```json
{
  "id": "xs_0007",
  "question": "situational query an agent would ask BEFORE acting, e.g. 'I need to poll CI status from a script in this repo — any constraints on how often?'",
  "answer_bearing_ids": ["learnings:github-api-poll-interval"],
  "forbidden_terms": ["120", "5000"],
  "kind_expected": "procedural"
}
```

- Questions are written as *situations*, never as paraphrases of the entry
  headline; the miner writes the question from the scenario, a separate leak
  check raises if any `forbidden_terms` (gold-identifying tokens, numeric
  answers per the P1 leak-guard) appear in the question.
- `answer_bearing_ids` labeled by the retrieval-only oracle procedure
  (`05` §4.0) + human spot-check on a 20% sample.
- **Split frozen at construction:** dev 40 / eval 80, hash-bound to the bank
  file. Per the exposure-guard rule: the split constant is never bumped.

**The acquisition gate (one-plan §5) is applied BEFORE any paid arm:**

1. **Death-from-below check ($0):** two trivial rules run first — (a) BM25 over
   the file corpus, (b) "grep the question's top-3 content words, return files
   by match count". If either scores ≥ 85% hits@10 on the dev split, the bank
   is saturated by a short rule and is **dead on arrival**; stop, record, do
   not spend.
2. **Death-from-above check ($0):** an oracle arm (reader handed exactly the
   gold entries) bounds the ceiling. Addressable headroom between the best
   trivial rule and the oracle must exceed the MDE at n=80 (≈13pp at 80%
   power for McNemar with expected discordance; computed and recorded from the
   dev-split discordance before the eval split is touched). If headroom < MDE,
   the instrument cannot answer the question at this n; stop.
3. **Recency-rule check ($0):** per the MemoryCode lesson, a "return the most
   recently dated entries matching any keyword" rule runs with the other
   trivial rules. Goldens answerable by recency alone are rebalanced or
   dropped *during dev-split construction only*.

**Gold is outside the statement set in the required sense:** the endpoint is
provenance (which entries), not an answer computable from the statements; the
mapping situation→entry requires reading the corpus, not a rule over it. The
death-from-below check is still run because that claim is exactly the kind the
gate exists to test rather than trust.

## A.2 Arms

Same-lattice throughout: identical corpus bytes, identical questions, identical
endpoint stage. Reader/agent model pinned to one model for all agentic arms,
via OpenRouter (Doppler `syndai/dev`), provider pinned, fallbacks off,
max_price pinned at run time.

| | arm | mechanism | what it measures |
|---|---|---|---|
| **F1** | **flat-file incumbent** | agent with `grep`/`read_file`/`list_files` over the corpus **including** `MEMORY.md` + `AGENTS.md` always in context (as the harness actually loads them), bounded loop | the real competitor: what Claude Code does today |
| **F2** | flat-file, no curation | same loop, `MEMORY.md` index and `AGENTS.md` **excluded** from context (files still present on disk) | how much of F1 is human curation labor — the labor MemPhant claims to replace |
| **T1** | MemPhant shipped default | corpus ingested as episodes via the C3 schema adapter → `reflect` → recall at shipped defaults, top-10 packed evidence, **zero LLM calls at recall** | the product's $0/low-latency claim |
| **T2** | MemPhant + agentic loop | same agent loop as F1 but its only search tool is MemPhant `recall` (bounded, same caps as F1) | the actual proposed MCP integration shape |
| **C0** | no-memory | reader answers from the question alone, no corpus access | contamination floor (ONCU-style), 20 questions |

**Flag state pinned:** `MEMPHANT_SUBJECT_RESOLUTION_THRESHOLD` at shipped
default (**off**) for T1/T2. One preregistered ablation cell — T1 with the flag
on — runs only if T1 completes, because this corpus's real restatements are the
first natural test of that mechanism.

**Known weakness stated up front, not discovered later:** the corpus is
majority procedural/feedback knowledge, and MemPhant's procedural write-side
promoter is UNBUILT (04 §13.6, W3.5) — these units will land as
episodic/semantic at shipped default. This eval measures **the shipped
default**, not the specced system. If T1 loses and the per-kind breakdown shows
the loss concentrated where the promoter is missing, that is the finding, and
it prices W3.5.

**Stage identity:** every arm terminates in ten bodies through
`gate_common.evidence_row` / `gate_common.provenance_hit` equivalents for this
bank; the comparison script refuses to run unless every arm's report declares
the same endpoint contract string.

**Budget symmetry (S4 caps inherited):** F1/F2/T2 hard caps — 12 tool calls,
16 turns, 24k completion tokens per question; grep ≤ 25 matches × 300 chars;
read ≤ 6,000 chars; list ≤ 200 rows. Arms select ≤ 10 items ranked. T1 spends
zero LLM tokens; any F-arm win is labeled as bought with inference T1 does not
spend.

## A.3 Endpoints — the three axes, ranked

1. **Primary (golden/quality): hits@10, paired, eval split n=80.** Exact
   two-sided McNemar. The verdict-bearing pair is **T1 vs F1**; T2 vs F1 is
   co-primary for the MCP decision (Bonferroni ×2, α=0.025 each).
2. **Secondary (cost): realized $/question per arm** (LLM tokens at pinned
   prices + amortized embedding cost for T1), reported always, verdict-bearing
   only inside the non-inferiority rule below.
3. **Secondary (speed): wall-clock p50/p95 per question per arm**, measured on
   the same host, reported always, verdict-bearing only inside the
   non-inferiority rule below.

Reader answer accuracy is tertiary, run on ≤30 questions only if budget
remains; it changes no verdict.

## A.4 Mechanism-liveness gates

- **T1/T2** — counted **in the DB, never in served evidence** (the Horizon
  rule): >0 derived units per expected kind actually served across the run;
  ingestion event count == corpus manifest count; supersession count reported
  from the DB. A run that served only raw-episode fallback is inert and does
  not report.
- **F1/F2/T2** — per row: ≥1 tool call executed with non-empty result; rows
  with zero executed calls are errors, not data; `reader_errors == 0` before
  reporting.
- **F2** — the report must prove `MEMORY.md`/`AGENTS.md` absent from context
  (context-hash check), else it silently measures F1 twice.

## A.5 The decision rule — fixed here, before the bank exists

Let Δ = rate(T) − rate(F1) on the eval split, exact McNemar, n_d ≥ 6 required
(else "NOT A MEASUREMENT" + required n).

- **Verdict A — MCP authorized:** T1 *or* T2 beats F1 at its Bonferroni α with
  Δ ≥ +MDE (computed at bank freeze; recorded in the bank file before the eval
  split runs).
- **Verdict N — MCP authorized on dominance:** neither beats F1, but the 95%
  CI lower bound on Δ(T1−F1) ≥ **−5pp** AND T1 is ≥ **10×** cheaper per
  question AND ≥ **10×** faster at p50. Same memory quality at an order of
  magnitude less cost/latency is a product; this margin is set now so a null
  can't be spun either way later.
- **Verdict B — F1 wins** (Δ ≤ −MDE, significant): **no coding-agent MCP work.**
  The story at this corpus scale is dead; any revival requires first
  demonstrating a real corpus scale at which F1 measurably breaks.
- **Verdict D** — anything else: MCP is **not** authorized; the instrument's
  band is reported and the next move is a decision about power, not code.

**F2 is not verdict-bearing** but is the headline for positioning: F1−F2 is
the measured value of human curation, the labor cost MemPhant's pitch amortizes.

## A.6 Spend plan — $0 first

| stage | what | ceiling |
|---|---|---:|
| 0 | corpus snapshot + manifest + C3 adapter ingest round-trip (contract check, no paid calls) | $0 |
| 1 | bank mining, dev split first (local/cheap model where possible) | ≤ $8 |
| 2 | trivial-rule + oracle + recency checks on dev (acquisition gate) | ≤ $2 |
| 3 | **stub round-trip of every agentic arm** against a loopback stub | $0 |
| 4 | F1/F2/T2 pilot, 15 rows each | ≤ $8 |
| 5 | full eval split, all arms | ≤ $18 |
| — | reserve | rest of $40 |

T1 and the trivial rules are $0 and run before any paid arm. **No paid arm runs
until the acquisition-gate stages pass** — the checks that could kill the bank
cost nothing and run first.

## A.7 Lineage

Every artifact: git HEAD, branch, dirty flag, sha256 of bank + corpus manifest
+ served MemPhant binaries. Ephemeral DBs via `with_scratch_db` throughout. An
artifact without lineage did not happen.

---

# AMENDMENTS (appended, dated; Part A above is unedited)

- **2026-08-05, before bank construction began, no cell seen:** owner authorized
  spend to **$100** (chat). Stage ceilings in A.6 unchanged; the delta is
  reserve. The $40 figure in the header is historical.

---

# PART B — RESULTS (appended after the runs; Part A is unedited)

## B.0 The answer: the bank is DEAD ON ARRIVAL. No money was spent.

**A.1 acquisition gate, death-from-below check, dev split n=55, $0 of the $100
authorized.** Trivial-rule scores, endpoint `hits@10` over 410 units:

| rule | hits@10 |
|---|---:|
| **BM25 (trivial, ~40 lines)** | **0.9091** |
| naive-grep (3 rarest question words by match count) | 0.3091 |
| recency (keyword match ranked by latest date in unit) | 0.0182 |

The preregistered rule in A.1 is unconditional: *"If either scores ≥ 85% hits@10
on the dev split, the bank is saturated by a short rule and is **dead on
arrival**; stop, record, do not spend."* BM25 scores **90.91%**. Stopped. No
paid arm ran. **XS reports no verdict on T1/T2 vs F1** — the instrument was
never able to ask the question.

BM25 takes **rank 1** on 35/55 goldens (63.6%) and rank ≤3 on 82%. Only five
goldens miss the top 10 (`xs_002`, `xs_014`, `xs_015`, `xs_029`, `xs_033`).

## B.1 The escape hatch is measured shut, not argued shut

The obvious rescue is *"the miner wrote lexically-close questions; re-mine with
lower question↔gold vocabulary overlap."* That is refuted by the data. Median
question-gold content-token overlap is 0.50 (min 0.13, max 0.78), and BM25's
hits@10 is **flat across the whole overlap range**:

| question↔gold token overlap | n | BM25 hits@10 |
|---|---:|---:|
| [0.00, 0.35) | 11 | **0.9091** |
| [0.35, 0.50) | 15 | 0.9333 |
| [0.50, 1.01] | 29 | 0.8966 |

Goldens whose wording shares almost nothing with the gold entry are retrieved
**just as well**. Whatever BM25 is keying on is not phrasing similarity, so
re-mining for harder phrasing cannot produce a live instrument. Rebalancing the
dev split (which A.1 permits) is therefore not attempted: there is no direction
to rebalance toward.

## B.2 What this actually says — and the two claims it does NOT license

**The finding.** At the corpus scale a real coding agent has — **410 units, 2.0
MB, topically well-separated entries** — the retrieval problem is *already
solved by lexical matching*. Each entry is about a distinct thing (CI polling,
warm-link symlinks, codex stdin, biome pinning), so any topical signal in the
query lands the right unit. There is no ranking problem for a memory system to
be better at.

This is the **same shape as S4** and it is now measured twice: S4 found an
agentic loop brute-forcing a ~122-item haystack; XS finds a ~40-line lexical
rule saturating a 410-unit one. The consistent lesson is about **haystack
size**, not about `grep` or BM25 specifically: *below some corpus scale,
retrieval sophistication has nothing to buy.* MemPhant's coding-agent story
cannot live at this scale, and no write-side or ranking work changes that.

**NOT licensed by this result:**

1. **"MemPhant loses to BM25 on cross-session memory."** Unmeasured. T1 never
   ran. A dead instrument produces no comparison, and quoting the 0.9091 beside
   any MemPhant number would be exactly the stage-mismatch this program voided a
   headline for.
2. **"Flat files are sufficient, the product is dead."** Also unmeasured, and
   the endpoint is the reason: `hits@10` scores **retrieval**, and retrieval is
   not what flat files are bad at. Their weaknesses are supersession (a stale
   entry retrieved next to its correction, both rank-1), curation labor (the
   `MEMORY.md` index is hand-maintained), and unbounded growth. This bank scores
   none of those.

## B.3 What would make a live instrument — and what it costs

Two directions, both requiring construction, neither authorized here:

- **Scale up the haystack.** The saturating variable is corpus size. A live
  instrument needs a corpus where lexical rules break — plausibly 10⁴–10⁵ units,
  which is the C3 public-trajectory source (`nebius/SWE-rebench` et al.), not a
  hand-curated memory dir. **But run the ceiling check first:** at 10⁵ units
  BM25 falls, and so does everything else — the question is whether the
  *addressable* gap exceeds the MDE, which is what killed SWE-ContextBench.
- **Change the endpoint to what flat files actually fail at.** Score
  supersession-correctness (does the arm return the live rule or the retired
  one?) rather than `hits@10`. The Syndai corpus genuinely carries these arcs
  (`SUPERSEDED 2026-07-25`, `CORRECTED claude-2026-07-29`), a BM25 rule cannot
  order them (recency scored **0.0182** here, so date-in-text is not a shortcut),
  and it is the mechanism MemPhant actually owns. This is the cheaper and more
  honest of the two, and it is a **different lane**, requiring its own Part A.

  **AMENDED 2026-08-05, same day — the corpus half of this recommendation is
  WRONG.** See `2026-08-05-supersession-correctness-lane.md` §A.1. The arcs in
  this corpus are **intra-unit**: the curator resolves supersession by editing
  in place, so retrieving the unit returns the resolution for free and there is
  no (retired, live) pair to adjudicate. Mining git history instead yields
  **≤16 arcs over 84 commits** (64 of which only add new entries; most
  modifications are accretion, not replacement) — underpowered by an order of
  magnitude. **B.2's claim that flat files fail by serving "a stale entry
  retrieved next to its correction" is refuted**: they do not fail that way.
  Their real costs are the curation labor performing those edits and unbounded
  growth (one entry accreted 2,520 → 14,517 chars across four revisions). The
  endpoint idea survives; the corpus does not.

**The MCP gate stands unchanged and unmet.** A.5 authorizes MCP work only under
Verdict A or N. Neither was reached. **No coding-agent MCP surface is built.**

## B.4 Defects recorded

- **One leak defect:** `xs_005` contains its own forbidden term `staged`. It did
  not affect the verdict (BM25 hits it either way) and the bank is dead, so it
  is recorded, not fixed.
- The gate script initially assumed a bare-list bank and crashed on the frozen
  dict; fixed before the reported run. The reported numbers come from the fixed
  script against the frozen bank.

## B.5 Lineage

- Corpus snapshot `~/.memphant-private/xs-crosssession/corpus-snapshot-2026-08-05`,
  342 files, `MANIFEST.sha256` = `14bee671b4ab868a8fe9b25a323637b572ea078f1fa625be6e426e23ff0a71cf`
- Unit universe 410 units (340 session-memory files, 61 LEARNINGS entries,
  9 AGENTS sections), `units_sha256` = `1e6e665e0e93698911243b53285c00796f4937d3a3942c88de238f6d4ceaca16`
- Frozen dev bank n=55 (40 procedural / 8 semantic / 4 preference / 3 episodic),
  `bank_sha256` = `f678d2a29ef9f96515461500e52eb9c741819444dcb0c38c8d498b590b089328`
- Gate artifact `docs/build-log/artifacts/xs-crosssession/gate-dev-2026-08-05.json`
- Commit `7256f823`, branch `xsession-controls`
- **Spend: $0.00 of $100 authorized.** Mining ran on session subagents; no
  OpenRouter call was made.
