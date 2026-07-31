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

Run date 2026-07-31. Preregistration committed at `a7cdb876` **before** the
first extraction. Nothing below was written before that commit.

### 8.1 Verdict, stated first

**Three preregistered bars FAIL. No bank ships as a certified bank.**

| bar | § | observed | verdict |
|---|---|---:|---|
| S1 leakage concentration ≤ 2.05× | 4.1 | **3.31×** | **FAIL** |
| P1 leakage concentration ≤ 2.05× | 4.1 | **2.42×** | **FAIL** |
| max single repo ≤ 60% of private non-S4 | 4.2 | **90.4%** (Syndai 47/52) | **FAIL** |
| S2 leakage concentration ≤ 2.05× | 4.1 | 1.78× | PASS |
| S3 leakage concentration ≤ 2.05× | 4.1 | 1.61× | PASS |
| private non-S4 goldens ≥ 40 | 4.2 | 52 | PASS |
| P1 goldens ≥ 100 | 4.2 | 325 | PASS |
| distinct private source commits ≥ 30 | 4.2 | 50 | PASS |
| `--check` byte-identical re-cut | 4.4 | reproduces | PASS |

§4.1 says a stratum above the bar is **dropped from the bank, not
renegotiated**. Applying that literally, the bar-clearing bank is **S2 + S3 = 13
goldens**, which is below the ≥ 40 private floor. So the honest disposition is:

> **The GitHub lane does not yield a certified coding-memory bank at the
> preregistered bar.** What ships is the measurement, the 13 bar-clearing
> state-churn goldens, and the full 416-golden artifact with every stratum
> carrying its own verdict. Nothing here may be used as a headline number.

The bars are not moved. §8.4 records a *mis-specification* found in the bar
itself; it is recorded as a finding for a future, separately-preregistered
instrument, and is **not** applied retroactively to rescue this run.

### 8.2 Yield and leakage per stratum

Primary scoping is the preregistered one (§4.1): non-targets are the other
target documents of the **same repository**. Full distribution, including
median/p10/p90 and the seeded single-draw floor, is in
`docs/build-log/artifacts/github-lane/leakage.json`
(sha256 `db2fbb4aab2f5ac82f05d03ba57bf7b85d13c2b7b37f2231f93e0706da2eedf4`).

| stratum | query author | shipped | scored | target mean | target median | non-target (exhaustive) | ratio | gate | absolute band |
|---|---|---:|---:|---:|---:|---:|---:|---|---|
| S1 `ci_failure_fix` | machine | 39 | 39 | 0.4315 | 0.4348 | 0.1305 | **3.31×** | **FAIL** | above |
| S2 `revert_supersession` | template | 6 | 6 | 0.3523 | 0.3585 | 0.1982 | 1.78× | PASS | above |
| S3 `fix_of_a_fix` | template | 7 | 7 | 0.3732 | 0.3571 | 0.2318 | 1.61× | PASS | above |
| S4 `coderabbit_review` | **model** | 39 | 39 | 0.2518 | 0.2246 | 0.1194 | 2.11× | not gated | within |
| S5 `human_issue_review` | human | **0** | — | — | — | — | — | — | — |
| P1 `public_human_review` | human | 325 | 305 | 0.3030 | 0.2500 | 0.1251 | **2.42×** | **FAIL** | above |

*scored* < *shipped* for P1 because 20 goldens are the only golden from their
source repository and so have no in-scope negative. They are excluded from the
distribution rather than scored against a zero floor, which would have flattered
every ratio they entered.

Reference points, same arithmetic: Track R original **4.19×** (failed), Track R
paraphrase **2.05×** (accepted, and the bar). Human-authored band **0.175–0.287**.

Secondary scoping, published so the choice of scope is auditable and cannot be
mistaken for after-the-fact bar-shopping: with P1 non-targets scoped by
**language** instead of repository, P1 reads 0.2986 / 0.0990 = **3.02×**. The
preregistered repository scoping is the *stricter* denominator and the one the
gate used; the looser one fails harder. Neither passes.

### 8.3 S5 is empty, and that is the finding

The private human stratum yields **zero** goldens. Both sub-sources are
disqualified by measurement, not by taste:

- **All 15 Syndai "human" review comments are the repo owner replying to
  CodeRabbit.** 11 of 15 open with `Addressed in <sha>: …` / `Fixed in <sha>: …`
  — the person who made the change, describing that change. That is precisely
  the Track R defect §1 bars, arriving through a different door. The remaining 4
  are rebuttals (`Not applicable`, `False positive`, `Intentional and within
  policy`) with no following change and therefore no target.
- **All 16 RecMe issues are open, zero-comment, never-closed feature-planning
  tickets** authored by the same owner, in a repo with 0 CI runs and no fix to
  attribute. A backlog, not a bug report.

The owner's survey said this well was nearly dry. Measured, it is **completely**
dry. This is the single strongest argument for the public corpora of §2b.

### 8.4 A mis-specification in the bar, recorded not applied

The concentration metric detects **copying**: it asks how much of the query's
vocabulary is literally present in the target. Copying requires that the query
could have been written by someone looking at the target. For S1 that is
impossible by construction — the CI runner emitted the failure text before the
fix existed, and no human touched it. For P1 the reviewer wrote the comment
against the pre-change hunk. In both strata a high ratio therefore measures
**causal specificity**, not contamination: a real user pasting a stack trace
names the failing file too.

Gating those strata on a copying metric was a mis-specification made at
preregistration time. The disciplined response is to report the FAIL, record the
mis-specification, and change nothing in this run — overriding a bar because the
number came back inconvenient is the exact failure the preregistration exists to
prevent. A leakage instrument that distinguishes *copied* from *causally
specific* is a separate deliverable requiring its own prereg.

Two contributing construction choices are noted for that future instrument, and
deliberately **not** changed here: the S1 target document repeats every touched
filename three times (file list, `--stat`, and diff header), and the P1 target is
a whole file-level diff section up to 3,500 characters rather than the reviewed
hunk. Both inflate coverage. Trimming them after seeing 3.31× would be
bar-shopping.

### 8.5 Repo concentration

Private non-S4 goldens: **Syndai 47, yurivan 5, Finn 0, RecMe 0, eternex 0** —
90.4% in one repository against a ≤ 60% bar. This is a property of the corpus,
not of the extractor: Syndai holds 80 of the 90 viable CI failure→fix
transitions. Enforcing the cap would leave ≤ 12.5 private goldens, far under the
40 floor. Recorded as **FAIL**; the private slice must never be described as
evidence spanning the owner's repositories. It is a Syndai slice with a yurivan
tail.

### 8.6 Extraction yield, by construct

| construct | candidates | admitted | principal loss |
|---|---:|---:|---|
| CI failure→green transitions (post-2026-05-01, non-deploy) | 122 | — | 17 unresolvable range, 13 range > 3 commits, 1 no failed job, 1 log expired |
| ↳ with retrievable logs | 90 | **39** | 51 `no_identifying_failure_text` |
| `This reverts commit` chains | 7 | **6** | 1 revert did not name its target |
| fix-of-a-fix (both ends CI-attested, ≤ 30 days, path overlap) | 7 | **7** | — |
| CodeRabbit review comments | 179 | **39** | 24 no merge commit/path, 114 over the ≤ 2-per-PR cap |
| private human review comments | 15 | **0** | 11 self-describing, 4 rebuttals |
| private issues | 16 | **0** | 16 open backlog, no fix |
| P1 swe-prbench PRs | 350 | **325** | 25 no usable human comment |

P1 comment accounting: 3,093 total → **102 bot comments excluded by author**
(`gemini-code-assist` 85, `cursor` 17) across **37 of 350 PRs**, despite
`ai_comments_removed: 0` on those rows → 2,991 human comments available, one
golden per PR.

### 8.7 Secrets and exclusions

Scanned every query and every target before it was written anywhere.

| reason | count |
|---|---:|
| `secret_excluded:anthropic_key` | 1 |
| `secret_excluded:generic_secret_assignment` | 1 |
| **total dropped for secrets** | **2** |

Both candidates were **dropped whole**, not redacted. No matched value was
written to the bank, the corpus, the lock, this document, the cache index, or
stdout — `scripts/github_lane_secrets.py` returns only the pattern name.

Full reject accounting is in `reject_by_reason` in the committed lock.

### 8.8 Artifacts and hashes

Committed:

| file | sha256 |
|---|---|
| `benchmarks/data/github_lane_golden.lock.json` | `be9965cc4868303ceabee2167259471aad564016549515c0d15a6551e1d6e584` |
| `docs/build-log/artifacts/github-lane/leakage.json` | `db2fbb4aab2f5ac82f05d03ba57bf7b85d13c2b7b37f2231f93e0706da2eedf4` |

Gitignored bodies, mirrored to `~/.memphant-private/github-lane/` (§5.5):

| file | sha256 | bytes |
|---|---|---:|
| `github_lane_golden.jsonl` | `d3387a4128f5a23b87c764c53e3e43812f0b437b01b1cae79f1b72e907ab3550` | 764,019 |
| `github_lane_corpus.jsonl` | `2a869b8ab270f3b732a7006867b634a730bc89122c5b0fab81b72176e3aef7be` | 1,427,533 |

416 goldens, 492 corpus documents. The mirror hashes match the committed lock,
so drift is detectable from the repository without exposing any content.

Source pins — clone HEADs at extraction time, since §5.4 forbids fetching:

| repo | HEAD |
|---|---|
| `solozerolabs/Syndai` | `c9c1cf908424dbca208d8a6d02af5627c5bddbce` |
| `solozerolabs/Finn` | `296411da094ced26195e1bec4eb70212dc4c9b00` |
| `solozerolabs/yurivan` | `2de0284d4d25c4b95d88f8b53cae2e84d131b7b2` |
| `solozerolabs/RecMe` | `93fe0c3040e9ad2bc1c973d165fe3a6255948855` |
| `solozerolabs/eternex` | `dea0d54111b90fb9a660927883c92ef78543b521` |

Every per-golden provenance record pins its own run ids, failing and green head
SHAs, fix SHA, PR number, review-comment id, or `swe-prbench` task id, base and
head commit. Public source pin: `foundry-ai/swe-prbench` `dataset/prs.jsonl`
sha256 `a58e1f713533f6bc260a93f6e234b85acd16a77f55a756893694b96495eb43cd`,
CC BY 4.0, attributed in the lock and on every P1 golden.

### 8.9 Compliance

- **$0 paid spend.** No OpenRouter call, no paid provider call, no model call of
  any kind. The extractor is local parsing over a disk cache.
- **Read-only.** Only `gh api -X GET` and `git log`/`git show`/`git rev-parse`.
  No push, no PR, no comment, no fetch, no checkout, no ref write. GitHub rate
  limit was never approached and every response is cached, so a re-run costs
  zero requests.
- **Determinism.** `--check` re-cuts and reproduces the lock byte-for-byte.
- **No fabricated numbers.** Every figure in this section is emitted by
  `github_lane_extract.py` or `github_lane_leakage.py` and is reproducible with
  the §7 commands.
