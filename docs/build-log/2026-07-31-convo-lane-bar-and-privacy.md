# Convo lane (coding-agent memory golden bank from the owner's own sessions) — preregistered bar and privacy terms

Date: 2026-07-30 (dated `2026-07-31` to sit in the W-series build-log sequence
alongside `2026-07-31-track-r-paraphrase-bar.md`).
Branch: `af-w6-convo`. Worktree: `/Users/sidsharma/Memphant-af-w6-convo`.
Status: **preregistered** — committed *before* any extraction run, per the
binding requirement the Track U slice already follows
(`docs/build-log/2026-07-30-track-u-privacy-prereg.md`).

Any figure that fails a bar below is a **kill-gate report**, not a reason to
move a threshold. The bar is not revisable downward after extraction.

---

## 1. Why this source

The mined Track R bank leaks. An LLM asked to write "causally identifying"
questions satisfied the instruction by copying rare identifiers out of the
target event: question→target token coverage **0.3960** against a
question→non-target exhaustive floor of **0.1008** — **3.93×** concentration
(4.19× against the seeded single-draw floor), with 105/180 questions narrowing
the corpus to exactly one event. See
`docs/build-log/2026-07-30-coding-lane-first-win.md` §4 and
`docs/build-log/2026-07-31-track-r-paraphrase-bar.md` §1. The paraphrase re-mine
improved it to 0.135 / 0.067 — **2.05×** — but it is still a model writing the
question.

**A human's turn in a real session is leak-free by construction.** The user
asked "why is X failing?" *before* the answer existed, in their own words,
without having seen the target. No prompt engineering produces that property;
only provenance does. This corpus is additionally our actual target domain —
coding agents at work — rather than public synthetic rollouts.

The prediction this bank tests is therefore falsifiable and stated up front:
**a query nobody wrote to be findable should land at or near the non-target
floor.** If it does not, the construct is not what we claim, and §4.1 says so.

---

## 2. Sources — surveyed, and the frame chosen

Everything below was measured on **2026-07-30** by read-only survey before this
document was written. Nothing was extracted.

### 2.1 Claude Code — **the sampling frame**

| measure | value |
|---|---:|
| project directories under `~/.claude/projects/` | **245** |
| `.jsonl` session files | **4,842** |
| total bytes | **2.7 GB** |
| session files containing at least one harness-stamped human turn | **412** |
| harness-stamped human turns in those files | **2,655** |
| ↳ of which `isSidechain: true` | **0** |
| session files with ≥2 human turns | **347** |
| date range / distinct days covered | 2026-06-26 → 2026-07-31 / **33** |
| human-turn length, p10 / p50 / p90 (chars) | 8 / 110 / 2,222 |

Top projects by human-turn count: `Syndai` (1,073), `Yurivan` (323),
`Memphant` (116), `evalrank` (91), then Syndai worktrees.

### 2.2 Codex — surveyed, **deliberately excluded from slice 1**

| measure | value |
|---|---:|
| `~/.codex/sessions/` | 3,288 files / **2.9 GB** |
| `~/.codex/archived_sessions/` | 598 files / **2.3 GB** |
| `~/.codex/history.jsonl` (typed-prompt log) | 5,110 lines |
| `~/.codex/session_index.jsonl` | 2,957 lines |

Codex carries its own human marker (`event_msg` records with
`payload.type == "user_message"` and a UI `client_id`), and `history.jsonl` is a
clean typed-prompt log. It is excluded from slice 1 for one stated reason and
not a resource one: **it would require a second, separately validated human-turn
rule**, and mixing two rules of different validated strength into one bank makes
the yield number uninterpretable. The Claude Code frame already supplies 2,655
positively-stamped turns, which is ~30× the largest bank this document permits.
Codex is a documented slice-2 extension, not a silent omission.

**The reported corpus statistic is therefore: 412 of 4,842 Claude Code sessions
scanned in full, 4,842 of 4,842 cheaply prefiltered, 0 of 3,886 Codex sessions
scanned.** Nothing in this bank may be described as covering the Codex corpus.

### 2.3 Read-only, always

Every source is opened read-only. Nothing is created, edited, or deleted under
`~/.claude/` or `~/.codex/` at any point, by any script in this slice.

---

## 3. The human-turn identification rule (preregistered, mechanical)

This is the load-bearing decision of the whole slice. In one 15 MB session,
1,512 records typed `user` yielded ~72 plausibly-human turns; the rest were
harness-injected. Getting this wrong reintroduces model-authored queries — the
exact defect we are escaping.

A 60-session random survey (seed 11) measured the false-positive classes
directly. Of 2,718 `type: "user"` records:

| class | n | human? |
|---|---:|---|
| `content` is a `tool_result` block list | 2,560 | no — tool output |
| `content` is a plain string, no `origin` | 44 | **no** — subagent dispatch prompts ("You are the OPEN-SOURCE REPOS team…"). **Model-authored.** |
| `origin.kind == "task-notification"` | 39 | no — harness notification |
| `origin.kind == "human"` | 38 | **yes** |
| `isMeta: true` text block | 14 | no — skill/system prompt load |
| `promptSource: "sdk"`, no `origin` | 10 | no — programmatic review call |
| `[Request interrupted by user]` | 2 | no — control event |

The plain-string-no-origin class is the trap: it *looks* exactly like a typed
prompt and is a model writing to a model. Any content-shape heuristic admits it.

**The rule is provenance, not shape.** A record is a HUMAN TURN iff all hold:

1. `type == "user"`;
2. `origin.kind == "human"` — the harness's own stamp on an interactively
   submitted turn;
3. `isSidechain == false`;
4. the record carries no `toolUseResult` key and `isMeta` is not true;
5. after stripping harness wrappers that the harness *prepends to* an otherwise
   human turn — `<system-reminder>…</system-reminder>`,
   `<task-notification>…</task-notification>`, `<command-name>…</command-name>`,
   `<command-message>`, `<command-args>`, `<local-command-stdout>…`,
   and `[Pasted text #N …]` / `[Image #N]` markers — the residual is **≥40
   characters**;
6. **paste guard:** the residual contains no run of ≥20 consecutive non-empty
   lines and no fenced code block of ≥20 lines. A pasted article or log is
   human-*submitted* but not human-*authored*, and it can carry the answer.

Rule cost is preregistered as measured, not as hoped: of the 2,655 stamped
turns, **870 are <40 chars** ("1", "y", "continue") and **93 exceed 6,000
chars**; 17 carry a paste/image marker. Steps 5–6 are expected to remove the
bulk of the first group. **The achieved yield after every step is reported in
the lock and in the final report, whatever it is.**

### 3.7 Amendment A1 — three guards added after the first candidate pass, before any gate was run

**Disclosed because the timing matters.** The first candidate pass (155→240
candidates, no verdicts computed, **leakage never measured**) surfaced a failure
mode the §3 rule does not catch, and three guards were added in response. All
three are *strictly tightening*; none relaxes a threshold in §4; no gate result
was known when they were written. Recording them here rather than silently is
the point.

The failure mode: the owner keeps standing "campaign handoff / paste this to
resume" prompts and re-pastes them into session after session. They are
human-typed (once) and human-*pasted* (thereafter), so they pass the §3
provenance rule — but as a *query* they are not a spontaneous turn, and their
nearest prior unit is a near-copy of themselves. Left in, they would have handed
the leakage metric a target coverage near 1.0 that has nothing to do with the
construct, in either direction.

| guard | rule | effect |
|---|---|---|
| **A1.1 oversized prompt** | residual > **2,000 chars** is rejected (`oversized_prompt`) | a memory query, not a pasted spec dump. Cut at the surveyed p90 (2,222). |
| **A1.2 re-pasted boilerplate** | a turn whose token Jaccard against a turn in a **different session** is ≥ **0.80** may not be a query (`boilerplate_repasted`). It stays in the haystack. | kills the standing-handoff class |
| **A1.3 target is not a restatement** | a prior unit with token Jaccard ≥ **0.45** to the question cannot be the target; a unit below **0.06** is too unrelated to be one | the target must be *referred to*, not *copied* |

A fourth change is a selection-*ordering* correction with the same motive: the
first pass ranked candidate targets by raw lexical similarity, which
systematically selects the leakiest available target. Targets are now ranked by
**shared concrete artifacts** (paths, dotted/snake/Camel identifiers) first,
lexical overlap only as a tiebreak.

### 3.8 Amendment A2 — the provenance rule had a false positive, and adjudication found it

**This one matters more than A1, because it is a hole in §3 itself.**

Wave-1 adjudication (155 packets) flagged four turns as "not a human turn at
all". Inspection confirmed it: a message sent from one agent session to another
is delivered into the receiving session as a `type: "user"` record stamped
`origin.kind == "human"`, `promptSource: "sdk"`, `isSidechain: false`, no
`toolUseResult`, not `isMeta`. It passes **every** condition of the §3 rule. The
body is an XML block, `<cross-session-message from="local_…" name="…">`.

So the harness's human stamp is *necessary but not sufficient*: it marks
"submitted through the user channel", and an agent-to-agent message enters
through that channel. This is a machine writing to a machine wearing the human
stamp — exactly the defect the whole slice exists to escape — and it was caught
only because the adjudicator was asked to judge, not to rubber-stamp.

**Rule added (§3, condition 7):** a turn whose raw text contains
`<cross-session-message` or `<agent-message` is rejected outright as
`agent_to_agent_message`. It is *not* unwrapped and kept, because unlike a
`<system-reminder>` prefix the machine message *is* the whole turn.

Measured cost on the frozen snapshot: **34 turns**. That is 34 turns that would
otherwise have been eligible to become model-authored queries in a bank whose
entire claim is that its queries are not model-authored.

Two smaller adjudication-layer findings are recorded rather than mechanised:

- **The regex secret scan is not sufficient on its own.** It caught 12 turns by
  family; the adjudicator additionally flagged pasted browser cookies, an
  account password written in prose, and a serialized session record — none of
  which match a key-shaped pattern. The adjudicator flag now **quarantines every
  unit visible in the flagged packet**: it may not be a query, may not be a
  target, and is removed from every shipped haystack.
- **One project is excluded wholesale** under §5 `content_sensitive_excluded`:
  17 of its 18 adjudicated candidates were flagged, and the Track U prereg
  already excluded the same project's adult-content vocabulary. Cost: 325 turns.

Condition 2 restricts the frame to sessions written since the harness began
stamping origins (2026-06-26). That is accepted, and it is not only a cost: a
33-day window over the owner's four active projects is *denser* in
cross-session continuity than a two-year sparse tail would be, and continuity is
half of what this bank measures.

---

## 4. The bar

### 4.1 Leakage — hard gate, run before the bank is usable

Computed by `scripts/track_r_leakage.py` (unmodified where possible; any
adaptation is limited to input plumbing and is recorded in the lock with the
script sha256). Metric, pinned identically to the Track R paraphrase bar:

```
coverage(question, event) = |T(question) ∩ T(event)| / |T(question)|
T(s) = set(re.findall(r"[a-z0-9_]{3,}", s.lower()))
```

Two floors, both published: the **exhaustive** floor (per golden, the mean
coverage over *every* non-target event in the same scope — no seed, so no lucky
draw) is binding; the **sampled** floor (one uniformly random non-target, seed
7) is reported because it is the form the original figures took.

| metric | bar | Track R original | Track R paraphrase |
|---|---|---:|---:|
| **concentration** = mean(target) / mean(exhaustive floor) | **≤ 1.50** | 3.93 | 2.05 |
| mean question→target coverage, absolute | **≤ 0.25** | 0.3960 | 0.135 |
| max question→target coverage, per golden | **≤ 0.60** | 0.6667 | — |

The **construct-validity prediction**, stated before the number is known: a
human-authored query should land at **concentration ≤ 1.30**, materially below
the ship bar, because it was written in ignorance of the target. A result in
`(1.30, 1.50]` ships but is reported as **weaker than the construct predicts**.
A result **> 1.50 does not ship and is reported loudly** — it would mean the
human-turn construct does not deliver what §1 claims, which is a more important
finding than the bank.

The full per-golden coverage distribution (n, mean, median, p10, p90, min, max
for target and both floors) is published in the lock.

### 4.2 Shapes, and end-behavior scoring

Four shapes, each anchored on a **verbatim, unedited human turn** as the query:

| shape | construct | scored win |
|---|---|---|
| `task_resumption` | the user returns in a later session and refers back to earlier work | the right prior context is retrieved *and used* |
| `correction_retention` | the user corrects the agent; a later turn tempts the same mistake | the mistake is not repeated |
| `state_churn` | a decision is superseded later; the query is answerable only under the latest state | the current state wins, the superseded one is not applied |
| `file_symbol_grounding` | the user asks about a specific artifact discussed earlier | the agent grounds on that artifact, not a plausible sibling |

**Every golden records `observable_correct_behavior` and `forbidden_behavior` as
end behavior, not retrieval@k** — the adherence rule the accuracy-first program
already binds Track U to. A retrieval target is recorded as *provenance*, for
the leakage metric and for diagnosis; it is not the score.

| metric | bar |
|---|---|
| goldens with a non-empty `observable_correct_behavior` | **100%** |
| goldens with a non-empty `forbidden_behavior` | **100%** |
| goldens whose `question` is byte-identical to the source human turn's residual (§3.5) | **100%** |
| goldens whose question was edited, paraphrased, or model-written | **0** |

That last row is the whole bank. Any golden failing it is a hard reject, not a
repair.

### 4.3 Size — and the honest-failure clause

| metric | bar |
|---|---|
| shipped goldens | **40–80** |
| per shape, each of the four | **≥6** |
| distinct source sessions | **≥20** |
| goldens per source session | **≤3** |
| goldens per project | **≤25** |

**Below 24 shipped goldens the bank is reported as insufficient and is not
padded.** Padding with model-authored turns is the specific failure this slice
exists to avoid; a small honest bank beats a large contaminated one. A shortfall
is reported with the yield numbers at every filter step so the cause is legible.

### 4.4 Track U non-duplication

`correction_retention` here is *episodic* — a correction observed in a
transcript — where Track U's is *semantic*, distilled from
`~/.claude/projects/*/memory/feedback_*.md`. The axis is extended, not
duplicated. Mechanically:

| metric | bar |
|---|---|
| convo goldens whose max Jaccard token overlap against any of the 51 Track U goldens/probes is ≥ **0.60** | **0** |
| provenance overlap with Track U's source set (`feedback_*.md`, `LEARNINGS.md`, `AGENTS.md`) | **none** — this bank reads session transcripts only |

Max and mean cross-bank Jaccard are published in the lock.

### 4.5 Templates and diversity

| metric | bar |
|---|---|
| distinct question skeletons / goldens (`track_r_mine.skeleton`) | **≥0.90** |
| max single-skeleton share | **≤3 goldens** |

Set higher than Track R's 0.80 because the questions are not generated: real
human turns that collapse to one skeleton would indicate a selector artifact.

### 4.6 Adjudication, determinism, cost

| metric | bar |
|---|---|
| goldens agent-adjudicated | **100%** |
| paid API spend | **$0** — adjudication runs on subscription-model agent calls, replies cached by `sha256(kind + system + prompt)`; **OpenRouter is not used**, its path requires an authorized spend ledger |
| warm rerun re-emits byte-identical goldens | **required** (`--check` exits non-zero otherwise) |
| fabricated numbers | **0** — every figure in the lock is written by an executed run |
| owner spot-check | sample emitted (gitignored), lock state starts `emitted_pending_owner_review` |

Selection is a seeded, stable-key traversal. The bank is a pure function of the
pinned snapshot, the seed, and the reply cache.

---

## 5. Secrets — scan, exclude, count, never quote

This material contains API keys, tokens, credentials, and personal content.

Every candidate turn and every candidate target passes a secret scan before it
may enter any artifact. Detected families: AWS access keys and secrets, Google
API keys, GitHub PAT/OAuth/App tokens (`ghp_`/`gho_`/`ghu_`/`ghs_`/`ghr_`/
`github_pat_`), Slack tokens (`xox[baprs]-`), Stripe keys (`sk_live_`/`rk_live_`/
`pk_live_`), OpenAI/Anthropic/OpenRouter keys (`sk-`, `sk-ant-`, `sk-or-`),
Doppler tokens (`dp.pt.`/`dp.st.`), JWTs, private-key PEM headers, `postgres://`
and other URIs carrying inline credentials, `.env`-style
`KEY=<high-entropy>` assignments, bearer headers, and long base64/hex blobs
above an entropy threshold.

**A detection excludes the whole candidate.** No redaction, no masking, no
partial retention — a masked secret still tells an attacker where to look, and
a redaction bug is unrecoverable once written. Exclusions are recorded in the
lock as **counts by reason only**:

> `secret_detected:<family>`, `paste_guard`, `too_short`, `harness_wrapper_only`,
> `content_sensitive_excluded`, `no_prior_context`, `adjudication_rejected`,
> `track_u_duplicate`, `leakage_outlier`

**No secret, and no fragment of one, is ever written into the bank, the lock, a
log, a commit message, a build-log document, a progress entry, or a report.**
Counts are the only permitted output. Personal (non-engineering) content is
excluded under `content_sensitive_excluded` on the same terms.

---

## 6. Privacy and custody

Committed:

- `scripts/convo_lane_extract.py` — mechanism only, no content, no rule text.
- `benchmarks/data/convo_lane_golden.lock.json` — sha256, byte size, counts,
  per-shape strata, parameters, accept/reject counts by reason, the leakage
  distribution, and the source-snapshot hash. **Counts and hashes only, never
  content.**
- this document.

Never committed (gitignored under the existing `benchmarks/data/*` rule, with
the lock allow-listed):

- `benchmarks/data/convo_lane_golden.jsonl` — the bank bodies.
- `benchmarks/data/convo_lane_corpus.jsonl` — the scoped memory-unit corpus the
  leakage metric measures against.
- `benchmarks/data/convo_lane_spotcheck.jsonl` — the owner-review sample.

Neither the bodies nor the corpus may be pasted into a commit message, a
build-log document, a STATUS entry, an issue, or a report. Reports quote strata
and statistics only.

**Mirror.** Every gitignored artifact is mirrored to
`~/.memphant-private/convo-lane/` — outside every worktree and every git
repository, never committed, never published — with its sha256 recorded in §9 of
this document on completion. Gitignored-and-single-copy is how this repo already
lost a 64k-event corpus.

**External-claim rule (binding).** Any external claim derived from this bank —
a published number, a blog figure, a leaderboard submission, a README line —
requires a **paraphrase-scrubbed or synthetic public variant**, re-adjudicated
to this same bar, with the number recomputed on that variant. The private bank's
numbers are internal decision evidence only. A private-bank number is never
published, not even for illustration and not even rounded. Scrubbing bar for the
public variant: no repo names, no project refs, no user handles, no file paths,
no session ids, no incident-tied dates, and no verbatim run longer than a clause
from any source.

**Owner authorization.** The repo owner explicitly authorized use of this
material for this purpose. That authorization covers internal use under these
terms; it does not waive §6's external-claim rule.

---

## 7. Source snapshot pinning — mandatory, and already justified twice

The Track U bank broke this week because it extracted from the live
`~/.claude/projects/` tree and a concurrent session added a file mid-run
(`docs/build-log/2026-07-30-track-u-privacy-prereg.md` §"Source snapshot
pinning"). **The same drift was observed during this survey**: two counts of the
human-turn population taken about six minutes apart returned **2,655** and
**2,659**. The corpus moves while the owner works.

Extraction therefore reads **only** from a frozen snapshot at
`~/.memphant-private/convo-lane/sources/`, a read-only copy of the qualifying
session files, with a `sources.manifest.json` recording each file's sha256 and a
`snapshot_sha256` over the sorted `(relative path, sha256)` list. The lock
carries a `source_snapshot` block; if no snapshot is present it records
`pinned: false` rather than silently reading live.

---

## 8. Reproduction

```
python3 scripts/convo_lane_extract.py --snapshot   # freeze + hash the sources
python3 scripts/convo_lane_extract.py --extract    # candidates + corpus
python3 scripts/convo_lane_extract.py --build      # bank + lock
python3 scripts/convo_lane_extract.py --check      # re-derive, assert lock
```

## 9. Achieved figures — executed 2026-07-30

Every number below is written by an executed run
(`benchmarks/data/convo_lane_golden.lock.json`, sha256
`6a9878d4bc3cd25da6abd5dff4dfb912f4d4f1cd22d37bb5fe2641c4e416c091`). None is
quoted, estimated, or rounded from memory.

### 9.1 Yield of the human-turn rule

| stage | n | note |
|---|---:|---|
| session files cheaply prefiltered | 4,843 | grep for the marker byte string |
| session files scanned in full | **412** | 1.45 GB, the frozen snapshot |
| records stamped `origin.kind == "human"` | **2,655** | all `isSidechain: false` |
| ↳ excluded project (§5 `content_sensitive_excluded`) | −325 | |
| ↳ `agent_to_agent_message` (A2) | −34 | machine turns wearing the human stamp |
| ↳ `too_short` (<40 chars) | −842 | "y", "1", "continue" |
| ↳ `boilerplate_repasted` (A1.2) | −564 | still retained as haystack units |
| ↳ `oversized_prompt` (>2,000 chars, A1.1) | −197 | |
| ↳ `paste_guard` | −45 | |
| ↳ `secret_detected` (regex, §5) | −12 | 7 high-entropy, 4 URI creds, 1 GitHub PAT |
| **admitted as memory units** | **1,200** | 45.2% of stamped |
| eligible as a *query* after A1.2 | 757 | boilerplate stays in the haystack only |
| candidates selected (4 shapes, seeded) | **204** | the cue-driven pool, near-exhausted |
| agent-adjudicated | **204 / 204 (100%)** | 10 subscription-model agents, 2 waves |
| **accepted into the bank** | **43** | 21.1% of candidates |

Adjudication rejects: `question_self_contained` 85, `content_sensitive` 16,
`target_unrelated` 12, `ambiguous` 17, `shape_not_supported` 6. Post-adjudication
caps: `per_project_cap` 19, `per_session_cap` 2, `content_sensitive_quarantine` 4.

**The dominant reject is a real property of the owner, not a bug.** 85 of 204
candidate turns are fully specified briefs — file, line, root cause, remedy,
verification command inline — and a competent engineer needs no recalled context
to act on them. A corpus of self-contained work orders cannot yield
memory-dependent queries, however human-authored it is.

### 9.2 Composition

43 goldens · 32 distinct sessions · 9 projects (Syndai 25 at the cap, Memphant 9,
rest ≤2) · 3,912 corpus events across 43 per-golden scopes · mean haystack 91
prior units. Shapes: `task_resumption` 31, `correction_retention` 9,
`state_churn` 3, **`file_symbol_grounding` 0**.

### 9.3 Leakage — the headline result

`scripts/track_r_leakage.py`, sha256 `1dd9435e…`, **unmodified** (verified in the
lock as `leakage_script_modified: false`), n=43, seed 7.

| measure | this bank | Track R original | Track R paraphrase |
|---|---:|---:|---:|
| question → **target**, mean / median | **0.3367** / 0.3158 | 0.3960 / 0.3880 | 0.135 |
| question → non-target, **exhaustive** floor | **0.2246** / 0.2190 | 0.1008 / 0.0942 | 0.067 |
| question → non-target, **sampled** floor (seed 7) | 0.2177 / 0.2000 | 0.0945 | — |
| **concentration vs exhaustive floor** | **1.4991** | 3.93 | 2.05 |
| concentration vs sampled floor | 1.5466 | 4.19 | — |
| target p10 / p90 / max | 0.1524 / 0.4756 / **0.8750** | — / — / 0.6667 |

By shape: `task_resumption` 0.3355/0.2176 (n=31), `correction_retention`
0.3516/0.2372 (n=9), `state_churn` 0.3038/0.2594 (n=3).

### 9.3b Amendment A3 — the gate is split into two fields, and the calibration band is external

Received from the coordinator after the numbers above were computed, and adopted
because it is correct and because it is backed by measurements this slice does
not have:

**The leakage metric conflates two properties, and only one is disqualifying.**

- **Contamination** — the query was authored *from* the target, so the number is
  fake. Settled by **provenance**, not by a statistic. Track R fails this: an LLM
  read the target and wrote the question.
- **Lexical tractability** — the query naturally shares tokens with its target.
  The number is real; the bank simply measures the lexical regime and cannot
  separate lexical from semantic retrieval quality. This is how Track R made
  dense embeddings look worthless.

**This bank passes provenance by construction.** Every question is byte-identical
to a turn the owner typed before the agent did the work. So the concentration
figure here **is not evidence of contamination and is not reported as such.** The
lock therefore carries two fields that are never collapsed: `provenance`
(`class: human_authored_pre_answer`, 43/43, `contamination_possible: false`,
per-shape) and the full concentration distribution.

**External calibration band** (measured by the sibling GitHub-lane run on
human-authored coding queries): concentration **1.76–2.03**, absolute target
coverage **0.175–0.287**. A *published human corpus*,
`foundry-ai/swe-prbench`, measures **2.42** and **fails** the ≤1.50 bar. When a
human corpus fails a gate, that is evidence about the gate. The ≤1.50 bar in §4.1
— inherited from the Track R paraphrase prereg — sits **below the human floor**.

The §4.1 bars are **not** rewritten. They are reported as they were preregistered,
with their pass/fail as measured, plus this band as context. The lock records
`prereg_bar_pass: false` alongside the provenance class, so nothing downstream can
read a single boolean and lose the distinction.

### 9.3c Position against the human band — and a construction defect found by looking

| measure | this bank | human band | position |
|---|---:|---|---|
| concentration | **1.4991** | 1.76–2.03 | **below band** |
| absolute target coverage | **0.3367** | 0.175–0.287 | **above band** |

Below on the ratio, above on the absolute — which can only happen if the *floor*
is unusually high. The coordinator's rule is that landing above 0.287 means
looking for a construction defect before concluding anything. There is one, and
it is measurable.

**A shipped memory unit is `user turn + agent reply` (reply clipped to 900
chars).** The agent's reply restates the user's vocabulary, so it adds shared
tokens to *both* sides of the metric. Re-running the identical pinned script with
each unit reduced to the user's turn alone (`diagnostics.unit_granularity_sensitivity`
in the lock, reproducible, same seed):

| | as shipped (turn + reply) | user turn only |
|---|---:|---:|
| target coverage, mean | 0.3367 | **0.1871** |
| non-target exhaustive floor | 0.2246 | **0.1370** |
| concentration | 1.4991 | **1.3657** |

**The absolute figure moves 1.8× on a unit-granularity choice alone**, and the
user-turn-only variant lands **inside** the human band (0.175–0.287). The bank is
not lexically hotter than real human queries; the *unit definition* is wider.

The shipped corpus is deliberately **left as is**. `user turn + agent reply` is
what an episodic agent memory would actually store, and narrowing it to hit a
number would be bar-fitting. The right conclusion is the general one:
**absolute-coverage bars are not portable between banks with different unit
definitions**, and only the ratio — measured on a fixed granularity — travels.

### 9.4 Bar table as preregistered — 5 rows FAIL

| bar | result |
|---|---|
| concentration ≤ 1.50 | **PASS — 1.4991, by 0.0009.** Not a comfortable pass and is not reported as one. |
| mean question→target ≤ 0.25 | **FAIL — 0.3367** |
| max question→target ≤ 0.60 | **FAIL — 0.8750** (2 goldens over) |
| construct prediction ≤ 1.30 | **FAIL — 1.4991** |
| 40–80 goldens | PASS — 43 |
| ≥6 per shape, all four | **FAIL** — `state_churn` 3, `file_symbol_grounding` 0 |
| ≥20 distinct sessions | PASS — 32 |
| skeleton ratio ≥ 0.90 | **FAIL — 0.8605** (37 distinct / 43) |
| max single skeleton ≤ 3 | PASS — 2 |
| Track U non-duplication (< 0.60 Jaccard) | PASS — max 0.1913, mean 0.0773 |
| `observable_correct_behavior` + `forbidden_behavior` on 100% | PASS |
| question byte-identical to the source residual on 100% | PASS (asserted mechanically) |
| 100% agent-adjudicated | PASS — 204/204 |
| $0 paid spend | PASS |
| warm rerun byte-identical | PASS — `--check` exit 0, sha256 reproduced |

`prereg_bar_pass: false`. Under A3 this is **not** a contamination finding:
`provenance.contamination_possible` is `false` and 43/43 goldens are
`human_authored_pre_answer`. The failing rows are lexical-tractability and
composition rows. What they disqualify is *this bank as a general-purpose
retrieval instrument*, not the corpus and not the construct. No number here may
be promoted, published, or used to move a default until §9.7 is addressed.

### 9.5 What the failure means

**Said plainly, as the brief requires: the human-authored query did NOT land at
the non-target floor.** The construct predicted ≤1.30; it delivered 1.4991.

The diagnosis is not "the human turns leaked" — they cannot have, and A3 makes
that a provenance question rather than a statistical one. Four measurements say
what actually happened:

0. **Unit granularity accounts for most of the absolute excess** (§9.3c):
   0.3367 → 0.1871 on the user-turn-only variant, moving the bank from *above*
   the human band to *inside* it.

1. **The floor moved, not just the target.** The non-target floor here is
   **0.2246** against Track R's 0.1008. Track R's floor was low because its
   questions carried *rare* identifiers copied from the target; nothing in a
   conversational corpus is rare in that way.
2. **The cross-project floor is 0.1986** — statistically indistinguishable from
   the in-project floor of 0.2246 (diagnostic, outside the pinned metric). A
   question drawn from this corpus covers ~20% of *any* unit the owner ever
   wrote, in *any* project. That is a house dialect, not pointing.
3. **The residual concentration is length-driven.** Split by question length:
   shortest third 1.766 (median 86 chars), middle third 1.354, longest third
   1.393. `coverage = |T(q) ∩ T(e)| / |T(q)|` has a small denominator for a short
   anaphoric turn, so a 56-char follow-up whose eight tokens are common words
   scores 0.875 against its referent — the single worst golden in the bank, and
   an artifact of the metric, not evidence of copying.

**So the honest conclusion is about the instrument, not the corpus.** The
leakage metric measures *aboutness*. It cannot separate "this question was
written by copying the target" from "this question is a genuine follow-up to the
target" — which is exactly why A3 splits provenance out and refuses to let one
statistic carry both. Track R's 3.93× was pathological and the metric caught it
correctly. Roughly 1.4–2.0× appears to be what genuine topical aboutness costs on
human coding queries: this bank measures 1.4991 (1.3657 at the narrower unit
granularity), the sibling lane's human corpora 1.76–2.03, and a published human
corpus 2.42.

The absolute bars (≤0.25 mean, ≤0.60 max) were set from Track R's own
distribution and are, at this bank's unit granularity, **unreachable by
construction**: they ask target coverage to fall below this corpus's own 0.2246
non-target floor. They are not relaxed here — they failed, and they are recorded
as failed. What they demonstrate is that **a bar calibrated on one corpus's floor
and one unit definition is not portable to another**, corroborated independently
by `swe-prbench` failing the same gate at 2.42.

**Recommendation to the program, stated as a recommendation and not as an
action taken here:** the ≤1.50 concentration bar in
`docs/build-log/2026-07-31-track-r-paraphrase-bar.md` §4.1 sits below the
measured human floor and should be recalibrated against the human band by
whoever owns that document. This slice does not amend another lane's prereg.

### 9.6 Two findings that outlive the bank

**A2 — the harness's human stamp is necessary but not sufficient.** An
agent-to-agent `<cross-session-message>` satisfies every provenance condition in
§3 and is a machine writing to a machine. 34 turns. Adjudication found it; the
survey did not. Any future work that trusts `origin.kind == "human"` alone
inherits this bug.

**The regex secret scan is not sufficient on its own.** It caught 12 turns by
family. Adjudicators additionally flagged, across 16 packets, material the
regexes do not match: pasted browser cookies and clearance tokens, an account
password written in prose, a serialized session record with a client IP, and
**live API-key material appearing in plain prose in at least four distinct
source sessions**, one of which reached three separate packets.

> **Action for the owner, outside this lane:** live credential material is
> present in plaintext in the Claude Code session transcripts under
> `~/.claude/projects/`. Those keys should be rotated. No value was written into
> any artifact, log, lock, or report by this slice, and the affected units are
> quarantined out of the bank, the corpus, and every shipped haystack — but the
> transcripts themselves still contain them.

### 9.7 What would make a valid instrument

Recorded now, before anyone is tempted to re-cut this bank to hit the bar:

0. **The corpus itself is now strategically load-bearing.** The sibling
   GitHub-lane run came back with an **empty** human stratum: all 15 "human"
   review comments in the private repos are the owner replying to CodeRabbit, 11
   of them `Addressed in <sha>` — the actor describing his own change. The
   private repos cannot supply human-authored queries at any scale. **This
   conversation corpus is the only in-house source of genuinely human-authored
   coding queries there is.** That raises the cost of a wrong human-turn filter
   and justifies the aggressive-reject posture in §3 and A2: 43 honest goldens
   from a source that exists beats a larger bank from a source that does not.
1. **Do not re-cut against the same metric.** The failing lexical rows are not
   fixable by better selection; they are properties of conversational vocabulary
   and of the unit granularity (§9.3c). The *composition* rows (per-shape,
   skeleton ratio) are fixable and are the ones worth another pass.
2. The discriminating comparison the metric *can* make is **question → target vs
   question → the top-k non-targets a retrieval system actually returns** (the
   Track R paraphrase construct's adversarial distractor set), not the mean over
   the whole scope. That set is buildable here.
3. The `file_symbol_grounding` shape yielded **0** and `state_churn` **3**. On
   this owner's corpus, naming an artifact and supplying the diagnosis are the
   same act. Those two shapes need a different source, not more mining.
4. The size and per-shape bars should be set from a measured pilot yield, not
   from a target bank size. 43 from 2,655 stamped turns is the real rate.

## 10. Custody hashes

Frozen source snapshot — `~/.memphant-private/convo-lane/sources/`, 412 files,
1,448,986,878 bytes, read-only copy of the qualifying live sessions:

| artifact | sha256 |
|---|---|
| `snapshot_sha256` (sorted path+hash list) | `a95351adb957865b3c787bb770edf2185ecbd37afaa4f23000f1deaf64fd7b9f` |
| `sources.manifest.json` | `e56f9c1004d3197577dea3c1bc18e6d4d47d58cd01995a972a37a0f9a1c576a9` |

Gitignored artifacts, mirrored to `~/.memphant-private/convo-lane/` (outside
every worktree and every git repository):

| file | sha256 |
|---|---|
| `convo_lane_golden.jsonl` (43 goldens, 79,189 B) | `bd08c93fa262bb548be82db0808807119463b0dda1c91ebc354fef028be14389` |
| `convo_lane_corpus.jsonl` (43 scopes / 3,912 events, 4,594,245 B) | `d0bca83452acf42229de913714901d3dc2eff186cf7de68ca70e1f123eeefc51` |
| `convo_lane_spotcheck.jsonl` (15) | `3d6375f96c5a7850eecf2e8a453a8b7caf51c7fd6f6f91a8ed772224ccbcebcd` |
| `convo_lane_leakage.json` | `f815787cdb2c79f8881715580eb3f4aa82eee563c75b079bdac523b130a739cf` |
| `adjudication_verdicts.jsonl` (204, the reply cache) | `89f7597c518b023ecc1228d01c2b0a0d4d3764c79fd015dba24f563ac55a1142` |

Committed: `benchmarks/data/convo_lane_golden.lock.json`, sha256
`6a9878d4bc3cd25da6abd5dff4dfb912f4d4f1cd22d37bb5fe2641c4e416c091` — counts and
hashes only. All three committed artifacts (lock, this document, the extractor)
were re-scanned by the §5 detector after writing: clean.

The external-claim rule in §6 is unchanged and, given `ships: false`, moot: there
is no number here eligible for publication in any form.

## 11. Reproduce

```
cd /Users/sidsharma/Memphant-af-w6-convo          # branch af-w6-convo
python3 scripts/convo_lane_extract.py --snapshot  # re-freeze (live tree drifts)
python3 scripts/convo_lane_extract.py --extract   # 204 candidates + packets
python3 scripts/convo_lane_extract.py --build     # bank + leakage + lock
python3 scripts/convo_lane_extract.py --check     # exit 0, sha256 reproduced
python3 -m pytest tests/test_convo_lane_extract.py -q
```

`--build` and `--check` need the mirrored verdict ledger; adjudication itself is
cached by packet `content_sha256`, so a rerun re-adjudicates nothing and costs
$0. `--snapshot` is the only stage that reads the live tree, and it only reads.
