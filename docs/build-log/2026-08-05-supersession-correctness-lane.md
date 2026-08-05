# SC — supersession-correctness: does the system know when a later statement *replaces* an earlier one?

**Date:** 2026-08-05 · **Branch:** `xsession-controls` · **Status: PART A
PREREGISTRATION, BLOCKED ON A CORPUS.** No cell has been seen. The corpus this
lane was expected to use has been **rejected on measured construct and power
grounds** (§A.1); the lane cannot run until §A.2's corpus requirement is met.

Successor to XS (`2026-08-05-cross-session-flatfile-controls.md`), which died at
its own acquisition gate. XS's B.3 named this lane as the cheaper and more
honest next instrument. **That recommendation was wrong about the corpus, and
this document records why before it proposes anything.**

---

# PART A — PREREGISTRATION

## A.0 The question, and why the obvious version of it is not worth asking

*When a later statement arrives about a subject the store already has, does the
system correctly decide whether it **supersedes** the prior statement or
**coexists** with it — and serve accordingly?*

The naive endpoint — *"return the latest statement about X"* — is *not this
question* and must not be built. It is saturable by a five-line recency rule,
which is precisely how MemoryCode died (gold computable as latest-declaration;
re-cutting as-of changed which short rule won, not whether one did). Any lane
whose gold is "the newer one" is dead before it is mined.

**The discriminating question is the one where recency is sometimes WRONG.** A
later statement about a related topic may:

- **supersede** — same subject, new value, prior generation closes; serve
  only the new one; or
- **coexist** — narrower scope, different subject, an exception, or a
  restatement that adds detail without invalidating; serve both.

A recency-always rule gets *every* supersede case and *no* coexist case, so its
score is **pinned by construction** at the supersede fraction of the bank. Balance
the bank 50/50 and the strongest trivial temporal rule cannot exceed 0.50. That
is the property that makes this instrument constructible at all, and it is the
reason the endpoint is served-set correctness rather than `hits@k`.

This is also the mechanism MemPhant actually owns and currently gets wrong:
subject keys are lexical phrases, so restatements only supersede when they are
near-identical, and the fix sits behind `MEMPHANT_SUBJECT_RESOLUTION_THRESHOLD`
(default **off**) with **no instrument that can score it**. This lane is that
instrument.

## A.1 The Syndai flat-file corpus is REJECTED — two independent measured reasons

XS proposed reusing its own corpus snapshot here, on the argument that it
"genuinely carries these arcs (`SUPERSEDED 2026-07-25`, `CORRECTED
claude-2026-07-29`)." Both halves of that argument were checked before drafting
and both fail.

**Reason 1 — the arcs are INTRA-unit, so there is nothing to adjudicate.**
Marker census over the 410-unit snapshot: `SUPERSEDED` 9 units, `CORRECTED` 8,
`RESOLVED` 18. Inspecting them shows the correction living **inside the same
unit as the claim it corrects** — `learnings:github-actions-doppler-pin` carries
its own `SUPERSEDED 2026-07-25` clause in the same bullet; `mem:project_make_
check_does_not_run_tests_unit` opens with `**SUPERSEDED 2026-07-30**` above the
text it retires. At least one marker hit (`mem:feedback_gh_polling`) is not a
supersession at all — it is the word used as domain vocabulary about git pushes,
so the true count is below the census.

A human curator resolves supersession **by editing in place**. Retrieve the
unit and you get the resolution for free. There is no (retired, live) pair for a
memory system to order, and a lane scored on this corpus would score the
curator's work, not the system's.

**This refutes a claim XS's own Part B made.** XS B.2 asserted flat files fail
by "a stale entry retrieved next to its correction, both rank-1." Measured:
they do not fail that way. In-place editing is a *correct and cheap*
supersession mechanism. Flat-file memory's real costs are elsewhere — the
curation labor that performs those edits, and unbounded growth (one entry here
accreted 2,520 → 14,517 chars across four revisions). Any future product framing
must drop the "stale entry served beside its correction" story for this class of
corpus; it is not what happens.

**Reason 2 — mining git history for cross-unit arcs is UNDERPOWERED.** The
in-place edits are themselves a legitimate arc source: pre-edit text is the
retired statement, post-edit the live one, commit date the transaction time, and
the label comes from version history — *evidence outside the statement set*,
which satisfies the instrument-acquisition requirement. So it was measured.

Walking all 84 commits touching `LEARNINGS.md`: **64 new-entry additions** (no
arc — nothing is replaced), **29 modification events over only 16 distinct
keys**, and inspection shows most of those are **accretion, not replacement**
(`preview-webserver-owns-the-build…` 2,520 → 4,422 → 8,851 → 11,593 → 14,517;
`integration-local-lane-db` four growth steps). Genuine claim-replacement is a
small subset of 16. `AGENTS.md` adds 86 commits of the same character. The
session-memory directory is **not a git repository** (0 commits), so its 340
files contribute no history at all.

Ceiling: **≤16 arcs, realistically ~10 after dropping accretions.** A paired
test needs n_d ≥ 6 discordant pairs to report anything; at n≈10–16 the MDE
exceeds 30pp. Even a perfect instrument at this n could only detect an effect
larger than any plausible one. **Rejected on power, before construct.**

## A.2 Corpus requirement — the blocking prerequisite

This lane does not run until a corpus exists with **all four** properties:

1. **Append-only arrival.** Statements enter as a time-ordered stream that
   nobody edits in place. (This is what disqualifies curated flat files.)
2. **Genuine same-subject restatement.** ≥ 60 supersede arcs where a later
   statement replaces an earlier value for the same subject.
3. **Genuine coexistence pairs.** ≥ 60 later-statement pairs that are
   topically adjacent but must **not** supersede — narrower scope, an
   exception, an added detail. Without these the bank is a recency bank.
4. **Labels from outside the statements.** Supersede/coexist decided by
   execution, version history, or an author who was there — never by a rule
   over the statement text.

Two candidate sources, neither yet adapted, each needing its own $0 census
before mining (the census this lane just ran on the flat files is the template):

- **Syndai prod episodic data** (C1 landed: real rows, read-only extract,
  gitignored). Append-only by construction, real subjects, real restatement.
  Requires a labeling pass; the arcs are real but unlabeled.
- **C3 public trajectories** (`nebius/SWE-rebench` et al. via the schema
  adapter). Large enough for power; needs a census proving properties 2 and 3
  actually occur at rate, which is *not* established and would be the first
  thing to measure.

**Do not mine a bank before the census.** XS's lesson was not "the bar was too
low" — it was that a corpus's decisive structural property was knowable for $0
and was not checked first.

## A.3 Arms (fixed now, so the corpus choice cannot be tuned to them)

| | arm | mechanism |
|---|---|---|
| **R** | recency-always | serve the later statement, always. **Score is pinned at the supersede fraction by construction** — a live check that the bank is balanced, not a real competitor |
| **J** | subject-Jaccard rule (~20 lines) | supersede iff token-Jaccard(subject spans) ≥ τ, τ tuned on dev. **This is the trivial rule that can kill the bank** — it approximates what MemPhant's own subject resolution does, and if it wins there is no product here |
| **F** | flat-file agent | agent with grep/read over the same statements, asked to serve the live set |
| **T1** | MemPhant, flag OFF | shipped default (`MEMPHANT_SUBJECT_RESOLUTION_THRESHOLD` unset) |
| **T2** | MemPhant, flag ON | the same store with semantic subject identity enabled |

**T1 vs T2 is the co-primary pair and the reason to build this at all.** It is
the first measurement the subject-resolution work could ever have had, and it is
paired on identical inputs. Per the standing Horizon rule, supersessions are
**counted in the DB, never inferred from served evidence**; a T2 run showing
zero DB supersessions is inert and does not report.

## A.4 Endpoint

**Served-set correctness, exact match, per query:** the arm returns a set of
statement ids; correct iff it equals the gold live set. Partial credit is not
scored — "served the retired rule alongside the live one" is the failure this
lane exists to catch, and set-F1 would hide it.

Reported as a 2×2 breakdown, never as a single rate:

| | gold: supersede | gold: coexist |
|---|---|---|
| arm says supersede | correct | **over-supersession** (data loss — the worse error) |
| arm says coexist | **under-supersession** (stale served) | correct |

Over-supersession is destructive and under-supersession is noisy; a lane that
collapses them into one accuracy number cannot tell a system that forgets too
much from one that forgets too little. Primary statistic: exact-match rate,
paired exact McNemar, α=0.025 Bonferroni across the two co-primary pairs
(T1 vs J, T2 vs T1).

## A.5 Acquisition gate — $0, runs before any mining spend

1. **Census first** (§A.2) — properties 1–4 proven on the candidate corpus
   before a single golden is written.
2. **Death-from-below:** arms R and J run on the dev split. **R > 0.55 means
   the bank is unbalanced** (rebuild the balance, not the bar). **J ≥ 0.85 kills
   the lane** — a 20-line lexical rule doing the product's job means there is no
   product, and that verdict is accepted, not re-litigated.
3. **Death-from-above:** oracle arm (handed gold subject identity) bounds the
   ceiling; addressable headroom between J and oracle must exceed the MDE at the
   planned n, computed and recorded before the eval split is touched.
4. **Lexical-overlap flatness check** — the diagnostic that made XS's verdict
   decisive. If J's score is flat across question↔statement overlap strata,
   re-mining cannot rescue the bank and the lane stops immediately.

## A.6 Decision rule

- **Ship the flag:** T2 beats T1 significantly at α=0.025 **and** does not
  increase over-supersession. A quality win bought with data loss is not a win.
- **Delete the flag:** T2 does not beat T1, or wins only by over-superseding.
  The mechanism is measured-dead and `MEMPHANT_SUBJECT_RESOLUTION_THRESHOLD`
  plus its machinery comes out, per the evidence-reset convention.
- **No product here:** J ≥ 0.85, or J beats both T arms.
- **MCP gate:** unchanged and still unmet. This lane does **not** authorize an
  MCP surface — it scores a write-path mechanism, not an integration.

## A.7 Spend

$0 until §A.2 is satisfied. Census and both trivial rules are $0. No paid arm is
authorized by this document; a mining budget is requested only after a census
passes, and the $100 currently authorized remains **unspent**.

---

# CENSUS LOG (appended per candidate corpus; Part A above is unedited)

## Census 1 — C1 Syndai prod episodic: **REJECTED**, $0

**Corpus.** `~/.memphant-private/c1/c1_prod_episodic.jsonl`, 321 rows from
`syndai.episodic_memories`, `snapshot_sha256`
`ddc0bc77d273c2da0ba4a7f95da4487a577e61fef861c7700fdfbc41dccf85c2`, read-only
extract. 292 `dialog_turn` + 29 `rollup`; 5 users, 5 agents, 6 projects;
2026-05-31 → 2026-07-31 over 31 distinct days.

| §A.2 property | verdict | evidence |
|---|---|---|
| 1. Append-only arrival | **PASS** | table has `created_at`, no `updated_at`; rows are appended, never edited |
| 2. ≥60 supersede arcs | **FAIL** | ceiling ≤13, and 0 genuine on inspection |
| 3. ≥60 coexist pairs | **FAIL** | same structural reason |
| 4. Labels outside statements | **N/A** | no arcs to label |

**The structural reason, which generalizes.** 292 of 321 rows begin `USER: ` and
are **task requests — imperatives, not assertions**. "Fix the character-gathering
step in the forge flow" has no subject-value pair, so there is nothing for a
later statement to replace. **An imperative has no truth value to supersede.**
The 29 `rollup` rows are LLM aggregations over request clusters ("The user
repeatedly requested completing localized-description tasks…") — summaries, not
restatements. Duplication is heavy: 207 distinct normalized content prefixes of
321, 30 duplicate clusters.

**The keyword probe fired and every hit was a false positive.** Searching for
restatement language returned 26 change-of-mind, 46 preference, and 10
correction hits — apparently promising. Inspection kills all sampled hits: the
matches are inside *technical prose describing code*, not a user restating a
convention — "use the localized synopsis **instead of** the hard-coded text",
"**prefer** it over older fallbacks", "**Prefer** synopsis translation for the
selected locale". This is exactly the unprobed-instrument trap: a keyword
counter formatted a clean-looking result, and only reading the matches showed it
was measuring vocabulary, not structure.

**Generous upper bound.** Clustering the 292 dialog turns by content Jaccard
≥0.5 gives 117 clusters, 37 multi-row, of which only **13 span more than one
day**. Even counting every cross-day cluster as a supersede arc — which none of
the inspected ones are, being repeated requests against the same feature — the
ceiling is **13 against a requirement of 60**. Rejected on power *and* construct.

**What this rules in.** The failure is specific to the *episodic dialog-turn*
slice, not to Syndai prod data as a whole. Restatement of durable
subject-value claims is what Syndai stores in its **`user_facts` / persona /
behavioral** layers (the L0 layers `context_loader` budgets separately at 400 /
300 / 700 tokens) — a different table, not in the C1 extract, and the only
remaining prod candidate whose rows are assertions rather than imperatives.
**Censusing it requires a fresh read-only extract under the C1 privacy
preregistration** (`docs/build-log/2026-07-30-c1-replication-privacy-prereg.md`)
and owner authorization; it is not covered by the existing C1 extract's scope.
Recorded as the next candidate, **not** started.

**Standing rule earned here:** *census the statement SHAPE before the arc count.*
Both rejected corpora failed on shape — flat files because the curator resolves
supersession by in-place edit, C1 because imperatives carry no value to
supersede. Counting markers or clusters first would have missed both.

## Census 2 — Syndai prod `user_facts`: **REJECTED**, $0, no PII read

**Probe.** Aggregates only, read-only, under
`2026-08-05-user-facts-census-privacy-prereg.md`; SQL committed at
`scripts/user_facts_census_probe.sql`. No `label`, `value`, `user_id`, or row id
was selected, printed, or written.

This was the most promising candidate on **shape**, and it remains so. The table
is exactly what the lane needs: `label` is the subject (uniquely indexed per user
over active rows), `value` is the value, `valid_to` closes a generation, and
`supersedes_fact_id` is an **explicit supersession edge** — a label from outside
the statement set, satisfying §A.2 property 4 by construction rather than by
annotation. Property 1 (append-only) holds: supersession inserts a new row and
closes the old via `valid_to`, never editing in place.

**It fails on data, and completely:**

| quantity | value | §A.2 requirement |
|---|---:|---|
| rows total | **5** | — |
| distinct users | 2 | — |
| **supersede arcs** (`supersedes_fact_id` not null) | **0** | ≥60 |
| closed generations (`valid_to` not null) | **0** | — |
| subjects with >1 generation | **0** (all 5 subjects have exactly 1) | ≥60 |
| review_status active / proposed | 2 / 3 | — |
| category mix | context 3, preferences 2 | — |
| span | 2026-05-06 → 2026-08-04 | — |

Ceiling for supersede arcs is **0**, against a requirement of 60. Rejected.

**The finding worth keeping, stated at its actual strength.** Syndai's user-fact
supersession machinery — the proposal/confirm review lifecycle, the partial
unique indexes that let a proposal share a label with its active target, the
`supersedes_fact_id` edge — is **fully built and has never fired in production**
across three months. Three of the five rows are still sitting in `proposed`,
never resolved.

This is **not** evidence that users do not restate their preferences. It is
evidence that a **human-confirmation-gated** supersession surface captured
essentially no restatement in this deployment. The distinction matters: MemPhant
proposes to supersede *automatically* on semantic subject identity, and the one
production system in reach that implements the *gated* alternative produced n=5.
Two readings remain open (nobody used the feature; or restatement happens but
never reaches this surface) and this probe cannot separate them.

## Census 3 — C3 public trajectories: **SHAPE PASSES, arc rate UNMEASURED**, $0

**Corpus.** `nebius/SWE-rebench-openhands-trajectories`, CC-BY-4.0, the source
already adapted in `2026-07-24-c3-public-code-lane.md`. **67,074 trajectories**
(the prior lane used 495). Sampled 14 across three offsets via the public
datasets-server; 1,896 messages, median 137 per trajectory; roles 941 assistant
/ 927 tool / 14 system / 14 user. No paid call.

| §A.2 property | verdict | evidence |
|---|---|---|
| 1. Append-only arrival | **PASS** | trajectories are immutable event streams; nothing is edited in place |
| 2. ≥60 supersede arcs | **PLAUSIBLE, UNMEASURED** | see the rate caveat below |
| 3. ≥60 coexist pairs | **PLAUSIBLE** | 7 of 14 `model_patch` values touch >1 file, so multi-item gold sets exist |
| 4. Labels outside statements | **PASS — and this is the strong result** | `model_patch` (files actually changed) and `resolved` (tests passed) are **execution-grounded**, exactly the "gold that depends on evidence outside the statement set" the instrument-acquisition gate demands |

**Volume is not the constraint, for the first time in this lane.** 67,074
trajectories at ~137 messages each is ~9.2M messages. Even a low arc rate yields
arcs in the thousands.

**But the lane MUTATES if it uses this corpus, and that must be explicit.** There
are no user preferences here. The construct becomes **belief revision during bug
localization**: the *subject* is "where is the defect", the *value* is the
agent's current file set, a later claim either **supersedes** (revised
localization) or **coexists** (an additional file that also needs changing), and
the gold live set is the file set in `model_patch`, graded by `resolved`. That is
a different instrument than Part A describes — arguably a better one, since it is
SWE-bench-shaped and execution-graded — and it **requires its own Part A**, not
an amendment to this one.

**The rate caveat, stated as the blocker it is.** The keyword probe found 28
"reconsider", 35 "revise-plan", and 6 "explicit-wrong" hits across 941 assistant
messages — and inspection shows them **dominated by navigation**, not
restatement: "Actually, let me fix the import issue", "Let me check if there are
other places". This is the **same false-positive trap C1 died on**, and the hits
are therefore **not counted as arcs**. The load-bearing number is different: only
**45 of 941 assistant messages (4.8%) name a source file at all** — roughly 3
localization statements per trajectory, of which genuine *revisions* are a
subset. **The arc rate is unmeasured and this census does not claim it.**
Establishing it needs a hand-labeled sample (~40 trajectories) separating genuine
localization revision from navigation chatter.

**Run this death-from-below check BEFORE the labeled sample.** Agents converge,
so *"serve the most recently named file"* is likely a strong trivial rule — the
recency saturation that killed MemoryCode, in new clothing. It costs nothing
against the sampled trajectories and could kill the whole direction before any
labeling spend. **Do that first.**

## Census status: three corpora rejected, one conditional — the lane is BLOCKED

| candidate | verdict | binding reason |
|---|---|---|
| Syndai flat files (XS snapshot) | rejected | arcs are intra-unit; curator edits in place |
| C1 prod episodic | rejected | imperatives carry no truth value to supersede |
| Syndai prod `user_facts` | rejected | right shape, **0 arcs** — feature unused |
| C3 public trajectories | **conditional** | shape + execution labels PASS, volume abundant; **arc rate unmeasured**, construct mutates to belief-revision, needs its own Part A |

**The consequence, stated plainly:
`MEMPHANT_SUBJECT_RESOLUTION_THRESHOLD` cannot currently be validated by any
data we can reach.** It stays default-off and unshipped — not because it was
measured and lost, but because no instrument exists to measure it. Building more
supersession machinery before an instrument exists would repeat the error this
lane was created to avoid.

**That prior was half right.** C3's assistant/tool messages are indeed actions
and observations, not preference assertions — so the lane as written in Part A
cannot run on it. What the shape check surfaced instead is a *different* subject
the corpus does carry (defect localization) with an execution-grounded label the
other three corpora entirely lacked. The next step is therefore **not** this
lane's Part B; it is the recency death-from-below check above, then a Part A for
a belief-revision lane if that check survives.

---

# PART B — RESULTS

*(empty — blocked on §A.2; three candidate corpora censused and rejected)*
