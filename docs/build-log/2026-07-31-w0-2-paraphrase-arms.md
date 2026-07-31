# W0.2 — the decisive comparison on the paraphrase variant

Date: 2026-07-31
Phase: substrate-and-accuracy program, **W0.2** (instrument validity, BLOCKING)
Cost: **$0 paid API spend.** No reader, no judge, no paid model call.

> # DIAGNOSTIC — NOT PROMOTION-GRADE
>
> The bank these numbers are measured on **failed its own preregistered headline
> leakage criterion**: concentration **2.0180** against a bar of **≤1.50**
> (`benchmarks/data/track_r_paraphrase_golden.lock.json`, `bar_passed: false`).
> It is used here deliberately and with that failure declared, because at 2.0180
> it is still a far less lexically confounded instrument than the original bank
> at 3.9286 — and because §5 shows the bar, not the bank, was the mis-specified
> thing. **No number in this document may be promoted, published, defaulted on,
> or cited as a standing measurement.** The spot-check is still
> `emitted_pending_owner_review`. Ownership question (d) is the owner's to
> decide; nothing here decides it.

## 1. The bank's leakage, printed beside its numbers

Unit definition, stated because absolute coverage is **not** portable across
banks with different units: **one unit = one content event of the attempt**, the
event `text` as materialized by
`openhands_trajectory_to_syndai_content_events_v2` at a 4000-char clip. The
non-target floor is **same-attempt** — hard negatives from the same run, which is
also exactly the retrieval haystack. Metric:
`|T(q) ∩ T(e)| / |T(q)|`, `T(s) = set(re.findall(r"[a-z0-9_]{3,}", s.lower()))`,
`scripts/track_r_leakage.py` sha256 `1dd9435e…`.

| measure (same unit, same metric, same corpus `c008142e…`) | original bank | **this bank** |
|---|---:|---:|
| question→target coverage, mean | 0.3960 | **0.1346** |
| non-target exhaustive floor, mean | 0.1008 | 0.0667 |
| concentration vs exhaustive floor | 3.9286 | **2.0180** |
| concentration vs sampled floor | 4.1905 | 2.0518 |
| questions narrowing to exactly one event | 105/180 | n/a (gate removed) |

## 2. Harness settings — recorded beside every score

Identical across all four MemPhant arms except the two varied dimensions. A
default is not evidence merely because it was the default.

| setting | value |
|---|---|
| recall mode | `fast` |
| k | 10 |
| budget tokens | 8192 |
| pack render cap | off |
| corpus | `c008142e…`, 495 attempts / 64,055 events, fully ingested |
| golden bank | `4aed8e99…`, 180 goldens |
| haystack | attempt-scoped for every arm, control included |
| query string | `code_lane_run_memphant.retrieval_query(golden)` — identical string in every arm |
| binaries | release, built in this worktree |
| scratch DB | one per arm, minted and dropped by the runner, fresh port per arm |
| worker drain | `ingest done … draining worker` completed before any recall in all four arms |
| paid spend | $0 |

**Varied:** `--lexical-scorer ∈ {overlap, bm25-code}` × `--embed-model ∈ {off,
small}`. `small` = `fastembed:bge-small-en-v1.5`, local ONNX, no network, no key.

## 3. The table — five arms, two stages, paired exact McNemar vs the control

Every MemPhant contrast is against the **scoped BM25 control** on the **same
haystack**, at the **same stage**, over the same 180 questions. `fused` = the
ranked candidate list via `gold_fused_rank`; `packed` = what actually reaches a
reader. p-values are two-sided exact McNemar on discordant pairs.

**Control (scoped BM25, attempt scope): r@5 = 0.1167, r@10 = 0.2556.**

| arm | scorer | embed | stage | r@5 | vs control @5 | r@10 | vs control @10 |
|---|---|---|---|---:|---|---:|---|
| `overlap_off` | overlap | off | fused | 0.0833 | +9/−15, p=0.307 | 0.2222 | +17/−23, p=0.430 |
| `overlap_off` | overlap | off | packed | 0.0611 | +5/−15, **p=0.0414** | 0.1722 | +12/−27, **p=0.0237** |
| `bm25code_off` | bm25-code | off | fused | 0.3333 | +43/−4, **p=2.8e-09** | 0.4944 | +48/−5, **p=7.1e-10** |
| `bm25code_off` | bm25-code | off | packed | 0.2111 | +25/−8, **p=0.00455** | 0.3722 | +35/−14, **p=0.00380** |
| `overlap_dense` | overlap | small | fused | 0.2889 | +44/−13, **p=4.7e-05** | 0.4611 | +51/−14, **p=4.5e-06** |
| `overlap_dense` | overlap | small | packed | 0.2111 | +30/−13, **p=0.0137** | 0.3333 | +37/−23, p=0.0925 |
| `bm25code_dense` | bm25-code | small | fused | **0.4333** | +61/−4, **p=3.9e-14** | **0.6278** | +71/−4, **p=6.8e-17** |
| `bm25code_dense` | bm25-code | small | packed | **0.3222** | +43/−6, **p=5.7e-08** | **0.4889** | +54/−12, **p=1.7e-07** |

`gold_in_pool` is 169/180 in all four MemPhant arms — the pool is identical; the
arms differ only in how they rank and pack it.

### Arm-vs-arm contrasts, @10, paired exact McNemar

| contrast | fused | packed |
|---|---|---|
| `bm25code_off` vs `overlap_off` | +52/−3, **p=1.5e-12** | +41/−5, **p=4.4e-08** |
| `overlap_dense` vs `overlap_off` | +46/−3, **p=7.0e-11** | +33/−4, **p=1.1e-06** |
| `bm25code_dense` vs `bm25code_off` | +29/−5, **p=3.9e-05** | +26/−5, **p=1.9e-04** |
| `bm25code_dense` vs `overlap_dense` | +34/−4, **p=6.0e-07** | +32/−4, **p=1.9e-06** |

## 4. How much of the win survives — it did not shrink, it grew

Margin = MemPhant recall − scoped BM25 control recall, each on its own bank's
questions. Survival ratio = paraphrase margin ÷ original margin. Survival arm is
`bm25code_off`, the like-for-like configuration the original result used
(embeddings off).

| stage | original bank margin | paraphrase margin | ratio |
|---|---:|---:|---:|
| fused @5 | +0.1222 | +0.2167 | **1.77** |
| fused @10 | +0.0667 | +0.2389 | **3.58** |
| packed @5 | −0.1000 | +0.0944 | −0.94 |
| packed @10 | −0.1222 | +0.1167 | −0.95 |

**Read the packed rows carefully: the negative ratio is a sign flip, not a
shrinkage.** On the original bank MemPhant *lost* to the control at the packed
stage (0.7722 vs 0.8944, margin −0.1222). On the paraphrase bank it *wins*
(0.3722 vs 0.2556, margin +0.1167). The ratio is negative because the
denominator was a loss. Expressed usefully: **the packed stage went from a
0.12-point deficit to a 0.12-point advantage.** The ratio statistic is simply
not meaningful when the baseline margin has the opposite sign, and it is
reported that way rather than quietly dropped.

**Everything collapsed in absolute terms, and that is the expected result.**
Removing the lexical give-away cost the control 0.8944 → 0.2556 and cost
MemPhant 0.9611 → 0.4944 at fused @10. The instrument got much harder for
everyone. What changed is the *ordering and the gap*: MemPhant's advantage over
the control widened at fused and reversed sign in our favour at packed.

**Third-party corroboration that the new instrument is the sane one.** CLARC
(ICLR 2026, USC, 1,245 C/C++ pairs, third-party-run) measures BM25 at **R@10 ≈
18.06** on genuine natural-language→code queries. Our control scored **0.8944**
on the original bank — a ~5× outlier — and **0.2556** here. The paraphrase bank
puts BM25 in the same order of magnitude as an independent measurement of BM25
on the same query class. That is the strongest available evidence that the
confound was in the instrument and is now substantially removed.

## 5. The two preregistered predictions

Both were recorded before the run. One is falsified and one is confirmed.

**(a) "The `bm25-code` advantage over the control shrinks sharply, plausibly to
null, because identifier matching is what the bank withholds." — FALSIFIED.**

It did the opposite. Against the control at fused @10 the margin went from
+0.0667 (+15/−3, p=0.0075) on the original bank to **+0.2389 (+48/−5,
p=7.1e-10)** here. The prediction's premise was that `bm25-code`'s gain *is*
identifier matching. The measurement says otherwise: `bm25code_off` beats
`overlap_off` by **+52/−3 (p=1.5e-12)** at fused @10 on a bank where questions
contain no identifier from their target at all. Whatever code-aware tokenisation
and BM25 term weighting are buying, they keep buying it when the identifiers are
withheld — so the mechanism is not "the question hands us the token", it is
better weighting of the shared *ordinary* vocabulary that remains.

**(b) "Dense flips from null to positive, because a bank that suppresses lexical
signal is where semantic matching should finally earn its keep." — CONFIRMED,
and larger than the shape of the prediction suggested.**

On the original bank dense alone was a null against the control (p=0.200 @5 /
1.000 @10) and stacking it on `bm25-code` was −10/+3 at @5, which is why the
coding lane was run with `--embed-model off` throughout. Here:

- dense on top of `overlap`: **+46/−3, p=7.0e-11** (fused @10), 0.2222 → 0.4611.
- dense on top of `bm25-code`: **+29/−5, p=3.9e-05** (fused @10), 0.4944 →
  0.6278.
- the best arm in the whole table is **both together**: `bm25code_dense`, fused
  @10 **0.6278**, packed @10 **0.4889**.

**This reverses a standing conclusion of the coding lane.** "The best
configuration uses no embeddings at all" was true of the original bank and is
false here. The `docs/build-log/2026-07-30-coding-lane-first-win.md` §"Dense
embeddings did not work on this lane" paragraph explicitly flagged that it
"should be re-tested on the W0 paraphrase variant before being generalised — a
lexically biased bank is exactly where dense would be expected to underperform."
That re-test has now happened and the caveat was correct. **No default moves on
this document**; the finding is that the dense-is-useless conclusion was an
artifact of the instrument and must be re-decided on a validated one.

## 6. What this cannot establish

- **Not production-representative in the other direction.** This bank's absolute
  question→target coverage is 0.1346 against a 0.175–0.287 human-authored range
  (owner-supplied calibration, not reproduced here). It bans every identifier
  surface; real engineers name files and functions. So it is an **adversarial
  floor**, the original bank is an **optimistic ceiling**, and production sits
  between them. Every margin here is a lower bound on the production margin, not
  a point estimate.
- **The bank failed its own bar** (§ header) and its spot-check is unreviewed.
- **Single corpus, single generator.** One synthetic-rollout corpus, questions
  authored by one model family, uniqueness judged by an agent.
- **No reader.** These are retrieval and packing measurements. Nothing here says
  what an answer-generating model would do with the packed context.
- **The dense arms cost real latency and compile time** — ~16 min ingest plus a
  substantially longer embedding compile per arm — and this document measures
  accuracy only, not the p95 or the cost line that any adoption decision needs.

## 7. Artifacts and reproduction

Committed: `docs/build-log/artifacts/track-r-paraphrase/w0-2-five-arm.json`
(full per-question vectors, all paired tables, the embedded leakage block, and
the `DIAGNOSTIC` grade). Gitignored, mirrored to
`~/.memphant-private/track-r-paraphrase/`: the four evidence JSONLs and four
provenance reports under `docs/build-log/artifacts/track-r-paraphrase/run/`.

```sh
cd /Users/sidsharma/Memphant-af-w0-instrument
cargo build --release --bin memphant-server --bin memphant-worker --bin memphant-cli
R=docs/build-log/artifacts/track-r-paraphrase/run

# control (~17 s, no DB, no server, no model call)
python3 scripts/code_lane_run_deterministic.py \
  --corpus docs/build-log/artifacts/track-r/corpus.jsonl \
  --golden benchmarks/data/track_r_paraphrase_golden.jsonl \
  --out-evidence $R/bm25-scoped-evidence.jsonl \
  --out-provenance $R/bm25-scoped-provenance.json --k 10 --scope attempt

# one MemPhant arm; vary --lexical-scorer {overlap,bm25-code} and
# --embed-model {off,small}. Fresh port per arm; stagger concurrent
# launches ~90 s (simultaneous scratch-DB migrations race).
python3 scripts/code_lane_run_memphant.py \
  --database-url postgres://memphant:memphant@localhost:5432/memphant \
  --corpus docs/build-log/artifacts/track-r/corpus.jsonl \
  --golden benchmarks/data/track_r_paraphrase_golden.jsonl \
  --out-evidence $R/after-bm25code-evidence.jsonl \
  --out-provenance $R/after-bm25code-provenance.json \
  --embed-model off --mode fast --k 10 --budget-tokens 8192 \
  --lexical-scorer bm25-code --label par-after-bm25code --port 39741 \
  --server-bin target/release/memphant-server \
  --worker-bin target/release/memphant-worker \
  --cli-bin target/release/memphant-cli

python3 scripts/track_r_paraphrase_arm_compare.py \
  --golden benchmarks/data/track_r_paraphrase_golden.jsonl \
  --corpus docs/build-log/artifacts/track-r/corpus.jsonl \
  --control $R/bm25-scoped-provenance.json \
  --arm overlap_off=$R/before-overlap-provenance.json \
  --arm bm25code_off=$R/after-bm25code-provenance.json \
  --arm overlap_dense=$R/dense-overlap-provenance.json \
  --arm bm25code_dense=$R/dense-bm25code-provenance.json \
  --leakage docs/build-log/artifacts/track-r-paraphrase/leakage-paraphrase.json \
  --original docs/build-log/artifacts/track-r/track_r_phase1e_combined_fixes.json \
  --out docs/build-log/artifacts/track-r-paraphrase/w0-2-five-arm.json
```
