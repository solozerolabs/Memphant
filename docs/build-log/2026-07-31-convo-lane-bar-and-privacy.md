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

## 9. Achieved figures and custody hashes

*(Filled in on completion, from executed runs only. Empty until then.)*
