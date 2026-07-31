# GitHub lane (coding-memory golden bank) — preregistered bar and privacy terms

Date: 2026-07-31
Phase: accuracy-first program, coding golden bank critical path
Status: **preregistered** — written and committed **before** any extraction run,
on the pattern of `docs/build-log/2026-07-30-track-u-privacy-prereg.md` and
`docs/build-log/2026-07-30-track-r-golden-bar.md`.

Every threshold below is binding. A number in
`benchmarks/data/github_lane_golden.lock.json` that fails a bar means the bank
does **not** ship as a bank; the outcome is a kill-gate report with the numbers,
not a relaxed threshold. Bars are not revisable downward after extraction.

## 1. Why this lane exists, and why the obvious design is barred

The mined Track R bank leaked. An LLM asked to write "causally identifying"
questions copied identifiers out of the target: question→target token coverage
**0.396** vs **0.094** against a random non-target in the same attempt — a
**4.19×** concentration — and 105/180 questions narrowed to exactly one event.
Recorded in `docs/build-log/2026-07-30-coding-lane-first-win.md` §4. The
paraphrase re-mine brought it to **0.135 / 0.067 = 2.05×**, which was accepted.

**Commit messages and PR descriptions carry the identical defect.** They are
written by the person who has just made the change, describing that change. A
"commit message → diff" or "PR body → diff" bank reproduces the Track R failure
exactly. It is barred here by preregistration, not by later judgement: no
stratum in this bank may take its **query text** from a commit message, a PR
title, or a PR body authored by the same actor who made the change.

Commit and PR text may still appear on the **target** side (the thing to be
retrieved) and in provenance. The bar is on the query side only.

## 2. Survey of the corpus, verified 2026-07-30/31

Independently recounted against the owner's survey. **All five owner figures
reproduce exactly.**

| repo | PRs | issues | commits (`rev-list --all`) | PR review comments | ↳ `coderabbitai[bot]` | ↳ human |
|---|---:|---:|---:|---:|---:|---:|
| `solozerolabs/Syndai` | 117 | 0 | 4,892 | 194 | 179 | **15** |
| `solozerolabs/Finn` | 194 | 0 | 1,579 | 0 | 0 | 0 |
| `solozerolabs/yurivan` | 39 | 0 | 1,294 | 0 | 0 | 0 |
| `solozerolabs/RecMe` | 7 | **16** | 80 | 0 | 0 | 0 |
| `solozerolabs/eternex` | 1 | 0 | 4 | 0 | 0 | 0 |

Commit counts are `git rev-list --count --all` on the local read-only clones and
so run slightly above the owner's default-branch figures (4,892 vs 4,782 etc.);
PR, issue and review-comment counts match to the unit. Savida is confirmed not a
separate repo — it is a subtree at `/Users/sidsharma/Finn/savida_mobile`.

**The conclusion stands: the human issue→fix and human review→change construct is
nearly dry** — 16 issues and 15 human review comments in total. This bank is not
built on it as a headline; it is mined exhaustively and reported as its own small
stratum.

Additional survey done for this prereg (CI construct, from cached run metadata):

| repo | workflow runs | failures | failure→success transitions on the same (workflow, branch) at a **different** head SHA | ↳ since 2026-05-01 | ↳ on default branch |
|---|---:|---:|---:|---:|---:|
| Syndai | 5,825 | 1,132 | 267 | 230 | 182 |
| Finn | 2,633 | 112 | 57 | 3 | 3 |
| yurivan | 2,369 | 306 | 114 | 76 | 76 |

**Actions log retention is ~90 days and is the binding constraint.** Probed
directly: job logs for runs on 2026-05-01 and earlier return empty; 2026-05-20
onward return full text. Check-run annotations persist longer but carry only a
sentence. The extraction window is therefore pinned to runs created on or after
**2026-05-01**, and every fetched log is snapshotted to the private mirror and
hashed, so the bank does not silently rot when GitHub expires the logs.

Revert / supersession survey on the local clones:

| repo | commits containing `This reverts commit` | revert-worded subjects |
|---|---:|---:|
| Syndai | 5 | 41 |
| yurivan | 2 | 8 |
| Finn / RecMe / eternex | 0 | 0 |

## 2b. Public human-review corpora (additive, verified 2026-07-31)

The private repos are dry for human review turns (15 comments, 16 issues). That
volume is not manufacturable from those repos, and padding it from commit
messages is barred by §1. Public, licensed, **human-authored** review corpora
supply it honestly. Each was verified from the LICENSE / record metadata and
from the **shipped data**, not from a badge or a README claim.

| id | source | license, verified from | shipped-data verification | decision |
|---|---|---|---|---|
| P1 | `foundry-ai/swe-prbench` | `cc-by-4.0` in **both** HF card metadata (`license: cc-by-4.0`) and README body §License ("Dataset: CC BY 4.0") | downloaded `dataset/prs.jsonl`: **350 PRs, 3,093 review comments**, every comment carrying `author`/`body`/`path`/`line`/`diffHunk`/`replyTo`; merged **2025-09-24 → 2026-02-17** | **ADMITTED** |
| P2 | Microsoft CodeReviewer, Zenodo record `6900648` | `{"id": "cc-by-4.0"}` in the Zenodo record metadata | record lists `Comment_Generation.zip` 846.6 MB, `Code_Refinement.zip` 1168.6 MB | **DEFERRED — contamination.** The corpus predates 2022 and is near-certainly in the pretraining mix of every model we would evaluate. P1 already supplies human review volume at a merge window that postdates plausible cutoffs. Admitting a 120k-triple corpus we cannot decontaminate would buy volume at the cost of the property the bank exists to measure. |
| P3 | `zhangfw123/CORE-Bench` Level-2 | **`cardData.license` is null** | not fetched | **BLOCKED — license unresolved.** No content is vendored. Were it resolved, the `Rewrite-LEVEL-2/3` configs would still be excluded as LLM-rewritten queries, and Level-3 as unverified provenance. |
| P4 | CodeSearchNet `annotationStore.csv` | repo MIT | not fetched | **DEFERRED — wrong shape.** 4,006 graded judgments over 99 queries is pooled IR qrels, not memory goldens with an observable-correct/forbidden behavior pair. |

**The P1 bot-contamination caveat is confirmed, not taken on trust.** The card
field `ai_comments_removed` is `0` on rows that plainly contain bot comments.
Recounted from the shipped file: **102 of 3,093 comments (3.30%) are
bot-authored** — `gemini-code-assist` 85, `cursor` 17 — spread across **37 of
350 PRs**. Exactly the coordinator's figures. P1 is therefore filtered **by
author**, never by the `ai_comments_removed` field, and the excluded count is
recorded in the lock. Usable human comments: **2,991**.

P1 is **public CC-BY-4.0 material and is NOT subject to the private-repo privacy
terms of §5.** The lock separates the two populations explicitly
(`privacy_class: private_repo` vs `public_cc_by_4_0`), and CC-BY attribution for
P1 is recorded in the lock and in §8. P1 bodies may be committed; they are
nonetheless kept in the same gitignored bank file for one operational reason —
one file, one lock, no chance of a private row leaking through a
misclassification — and that is a convenience, not a licence restriction.

### Calibration band for absolute leakage

Human-authored coding queries measure **0.175–0.287** absolute question→target
token coverage; SWE-PRBench review comments specifically **0.197** at **1.76×**
against same-domain negatives. Our leaked bank was **0.396**. That band is the
reporting target. **A stratum landing far below 0.175 is harder than reality,
not better**, and is flagged as such in the lock rather than celebrated — an
under-specified query measures a retrieval problem no user has.

## 3. Strata, and what each one measures

Six strata. Each golden records the **observable correct behavior** and the
**forbidden behavior**, not only a retrieval target.

| id | stratum | query author | why it is leak-free | scored win |
|---|---|---|---|---|
| S1 | `ci_failure_fix` | **a machine that has not seen the fix** (the CI runner) | the failing test name and error text are emitted before the fix exists | retrieve the commit that turned the check green |
| S2 | `revert_supersession` | **templated by mechanism** from the touched path/symbol — never from either commit's message | the query names only the subject under churn, not the resolution | return the **current** decision, not the reverted one |
| S3 | `fix_of_a_fix` | **templated by mechanism** from the touched path/symbol | the correction is attested by the repo (a later commit repairs the earlier one), not authored by us | apply the correction; do not resurface the superseded fix |
| S4 | `coderabbit_review` | **`coderabbitai[bot]`** — a model | the reviewer had not seen the subsequent change, but the query is model-authored | make the change the review asked for |
| S5 | `human_issue_review` | **a human**, in an issue or a review comment | written by a person who has not seen the fix | resolve the reported problem |
| P1 | `public_human_review` | **a human**, in a merged-PR review comment (`foundry-ai/swe-prbench`, CC-BY-4.0) | the reviewer wrote the comment against the pre-change hunk; the change is what followed | produce the change the reviewer asked for, at the reviewed path |

S5 and P1 are the same construct — a human review turn — differing only in
provenance and privacy class. They are measured with the same checks and the
same bar, and reported as separate rows so the private slice's small n is never
hidden inside the public slice's volume.

**S4 is weaker evidence and is quarantined by preregistration.** Its goldens
carry `query_author: model`, its leakage figures are published separately, and
**no headline number in this bank or any downstream report may blend S4 with
S1/S2/S3/S5.** Any aggregate that includes S4 must be labelled as such and be
accompanied by the S4-excluded figure.

S2 and S3 are the state-churn shapes: the forbidden behavior is returning the
superseded state, so a bank that "retrieves the right file" but the wrong
generation scores zero.

## 4. The bar (all thresholds binding)

### 4.1 Leakage — hard gate

Measured by `scripts/github_lane_leakage.py`, a direct adaptation of
`scripts/track_r_leakage.py` (`/Users/sidsharma/Memphant-af-w0-instrument`,
commit `e5fda0de`) with the identical tokenizer and coverage definition:

```
coverage(query, doc) = |T(query) ∩ T(doc)| / |T(query)|
T(s) = set(re.findall(r"[a-z0-9_]{3,}", s.lower()))
```

Track R's scoping unit was "other events of the same attempt". Here it is
**other target documents of the same repository in this bank's own corpus** —
the retrieval haystack is repo-scoped. Both the seed-free exhaustive mean and a
seeded single-draw figure are reported, as in the reference script.

| metric, **per stratum** | bar |
|---|---|
| `target_mean / non_target_exhaustive_mean` (concentration) | **≤ 2.05×** for S1, S2, S3, S5, P1 |
| same, for S4 | **reported, not gated** — S4 may not enter a headline either way |
| absolute `target_mean` | reported, no bar; flagged `below_human_band` if < 0.175 and `above_human_band` if > 0.287 (§2b) |

2.05× is the concentration of the Track R paraphrase re-mine, the variant that
was accepted. The gate is therefore "at least as leak-free as the artifact we
already accepted", stated in advance against a reproduced number. A stratum
above 2.05× is **dropped from the bank**, not renegotiated.

### 4.2 Size and composition

| metric | bar |
|---|---|
| **Private** goldens excluding S4 (S1+S2+S3+S5) | **≥ 40** — below this, the private slice does not ship as a bank |
| S5 (private human-authored queries) | mine **all** available candidates; no minimum, count reported |
| P1 goldens | **≥ 100**, `≤ 1` per source PR, human-authored comments only |
| Goldens per single source commit / PR / issue | **≤ 2** (private), **≤ 1** (P1) |
| Goldens per repository | **≤ 60%** of the private non-S4 total |
| Distinct source commits across the private slice | **≥ 30** |

If the private non-S4 yield lands below 40, the deliverable is the numbers and
the small high-quality slice, explicitly **not** presented as a private bank.
Padding a shortfall with S4 or with commit-message-derived goldens is barred,
**and so is papering over it with P1** — P1 is public third-party material and
cannot stand in for evidence about the owner's own repositories. The two counts
are always reported separately.

### 4.3 Attribution — hard reject

An S1 golden is admitted only if the fix is unambiguously attributable:

| check | bar |
|---|---|
| commits strictly between the failing head SHA and the green head SHA | **≤ 3** |
| the failing job produced retrievable log text | required (else `log_unavailable`) |
| the extracted failure excerpt contains a concrete file path, test name, or symbol | required |
| the failing check is a **test/lint/typecheck/schema** check, not a deploy or infra check | required |

The last row matters: "Deploy" failures dominate the raw transition count (116 of
230 on Syndai) and are frequently infra flakes retried on unchanged content, or —
worse for this bank — emit **commit subject lines** into the log, which would
smuggle commit-message text into the query side and reproduce the exact defect
§1 bars. Deploy-family workflows are excluded by name at extraction and the
exclusion is counted.

### 4.4 Determinism

`scripts/github_lane_extract.py --check` re-runs the extraction against the
pinned snapshot and asserts byte-identical output against the committed lock.
Seeded where a draw is needed. No clock read enters the bank. Every GitHub API
response and every job log is cached to disk under
`~/.memphant-private/github-lane/cache/`, so a re-run makes **zero** network
calls and the bank survives GitHub's 90-day log expiry.

## 5. Privacy terms (binding)

**All five repositories are private.** Every one of them contains, or may
contain, credentials, keys, internal endpoints, customer identifiers, and
business-confidential text.

This section governs the **private** strata S1–S5 only. P1 is public CC-BY-4.0
material (§2b) and is exempt from §5.2, §5.3 and §5.4; it is nonetheless carried
in the same gitignored bank file so that no misclassification can leak a private
row, and every golden carries an explicit `privacy_class`.

### 5.1 What is committed and what is not

Committed:

- `scripts/github_lane_extract.py`, `scripts/github_lane_leakage.py` — mechanism
  only. No query text, no failure text, no diff text, no repo-internal strings.
- `benchmarks/data/github_lane_golden.lock.json` — sha256, byte size, counts,
  per-stratum / per-repo / per-workflow strata, parameters, accept/reject stats
  by reason, secret-exclusion counts by reason, and the pinned source SHAs and
  PR/issue numbers. **Counts, hashes and identifiers only, never content.**
- `docs/build-log/artifacts/github-lane/leakage-*.json` — the leakage
  distribution. Statistics only; `per_question` rows carry ids and floats, never
  query or document text.
- this document.

Never committed (gitignored):

- `benchmarks/data/github_lane_golden.jsonl` — the bank bodies: query text,
  target document text, observable-correct and forbidden behaviors.
- `benchmarks/data/github_lane_corpus.jsonl` — the repo-scoped haystack.

Neither file, nor any excerpt of either, may be pasted into a commit message, a
build-log document, a STATUS entry, an issue, a report, or a terminal transcript
that is later quoted. Reports quote strata and statistics only.

### 5.2 Secret handling

Before any candidate text is written anywhere — bank, corpus, cache index, lock,
log line, or console output — it is scanned by `scripts/github_lane_extract.py`
against a pinned pattern set: AWS access keys and secret keys, GitHub `gh[pousr]_`
tokens, OpenAI / Anthropic / OpenRouter / Google / Stripe / Supabase / Slack /
SendGrid / Twilio key formats, PEM private-key blocks, JWTs, `postgres://` and
other credentialed connection URLs, `Authorization:` headers, and generic
high-entropy `KEY|SECRET|TOKEN|PASSWORD=<value>` assignments.

**A candidate with any match is dropped whole.** It is not redacted and kept —
redaction leaves the surrounding context, and a partially-scrubbed secret is
still a leak vector. The drop is recorded in the lock as a count under
`secret_excluded` keyed by pattern name. **The matched value is never written
anywhere**, including this document, the lock, the cache index, and stdout.

Raw API responses and raw job logs are cached under
`~/.memphant-private/github-lane/cache/` — outside every git repository, never
committed. The cache is the only place raw repository text lands.

### 5.3 External-claim rule

Any external claim derived from this bank — a published number, a blog figure, a
leaderboard entry, a README line — requires a **scrubbed or synthetic
public-reproducible variant**, re-adjudicated to this same bar, with the number
recomputed on that variant. The private bank's own numbers are internal decision
evidence only. A private-bank number is never published, not even for
illustration, not even rounded. Scrubbing bar for that variant, when it is built:
no repo names, no internal service names, no file paths, no user handles, no
customer identifiers, no run ids, no verbatim span longer than a clause.

### 5.4 Read-only guarantee on the source repositories

The extractor and every step of this work is **read-only** on all five
repositories and on the local clones:

- GitHub access is `gh api` **GET only**. No POST, PATCH, PUT or DELETE is
  issued. Nothing is pushed, no PR is opened, no issue or comment is created or
  edited, no label, workflow or setting is touched.
- Local clones at `/Users/sidsharma/{Syndai,Finn,yurivan,RecMe,eternex}` are read
  with `git log` / `git show` / `git diff` only. **No `fetch`, no `checkout`, no
  `pull`, no ref write** — a fetch would mutate the owner's working repositories
  and would also un-pin the corpus mid-run.
- Because there is no fetch, the corpus is pinned to each clone's HEAD as
  observed at extraction time; the SHAs are recorded in the lock.

### 5.5 Durability

Gitignored-and-single-copy is how this repo previously lost the ~64k-event local
code-lane corpus. The bank, corpus and lock are mirrored to
`~/.memphant-private/github-lane/` (outside any git repository, never committed,
never published), with sha256s recorded in the completion section of this
document so the committed record can detect drift without exposing content.

## 6. Spend

**$0 paid spend.** No OpenRouter call, no paid provider call. The extractor is
pure local parsing plus cached GitHub reads and makes no model call at all.
Where adjudication is needed it runs on subscription-model agent calls through a
content-hash cache, so a re-run is free.

GitHub API usage stays inside the authenticated 5,000/hour limit and every
response is cached, so a re-run costs zero requests.

## 7. Reproduce

```
python3 scripts/github_lane_extract.py --out benchmarks/data/github_lane_golden.jsonl
python3 scripts/github_lane_extract.py --check
python3 scripts/github_lane_leakage.py \
  --golden benchmarks/data/github_lane_golden.jsonl \
  --corpus benchmarks/data/github_lane_corpus.jsonl \
  --out docs/build-log/artifacts/github-lane/leakage.json
```

---

## 8. Completion record

*(filled in after the extraction run; empty at preregistration time)*
