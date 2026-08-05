# Horizon stage-1 verdict — supersession was unreachable, twice over

Date: 2026-08-05
Cost: $0. No provider, reader, or paid call ran. Free ten-row exposed sample
plus re-measurement of already-banked paid artifacts.

## Verdict

The HorizonBench powered confirmation (-15.8pp overall, Fast 36.7% vs full
context 52.5%) measured the substrate with its current-state compilation
mechanism **disabled by configuration**, and that mechanism **cannot fire on
this benchmark even when enabled**. The binding defect is stage 1 of the
evidence funnel — compilation — not retrieval, not packing, not the reader.

The funnel was not needed to establish this. Stages 2-4 cannot be the first
failing stage when no current-state unit is ever produced to retrieve.

## Defect (a) — extraction was off in the paid run

`scripts/run_horizonbench.py` set `os.environ["MEMPHANT_FACT_EXTRACTION"] = "0"`
on the confirmation path. Per `crates/memphant-runtime/src/lib.rs:593-609`, W6
lexical fact extraction is "the only deterministic producer of an explicit
subject/predicate on the served write path", and an explicit subject is what
`has_explicit_subject` (`crates/memphant-core/src/lib.rs:12630`) requires before
the supersedence branch runs at all. Without it every write takes a
`{scope}:auto:{sha256[..16]}` content-hash key, and AUTO-KEYS NEVER SUPERSEDE
(`crates/memphant-core/src/lib.rs:11871`).

Measured over all 1,448 evidence units in the 120 banked paid rows
(`docs/build-log/artifacts/horizonbench-confirmation/fast-evidence.jsonl`):

| signal | value |
|---|---:|
| units with `:auto:` fact key | **1448 / 1448 (100%)** |
| units with `subject_generation > 0` | **0** |
| units with `valid_to` closed (superseded) | **0** |
| units of kind `semantic` | **0** (1448 episodic) |

Zero supersessions occurred in the run that measured evolving belief state.
The runtime default has since flipped to ON; the benchmark script had not.

## Defect (b) — enabling it is necessary but not sufficient

> **Correction (same day).** This section first reported that the
> extraction-on arm "still supersedes nothing". That was wrong: it was measured
> on *served* evidence, and a superseded unit is closed, so recall never serves
> it. Measured against the database instead, extraction-on supersedes 34 times
> on this sample. The corrected finding is narrower and is stated below.

Free re-run of the ten exposed pilot users (`build-fast-evidence`, the same
users the frozen 60-user tranche excludes, so nothing held out was touched),
each arm on its own retained database. Identical corpus, 943 sessions retained
and compiled in every arm:

| arm | units | superseded | edges | preference units | distinct preference keys |
|---|---:|---:|---:|---:|---:|
| `fact_extraction=off` | 943 | **0** | **0** | 0 | 0 |
| `fact_extraction=on` | 1329 | 34 | 102 | 218 | 212 |

Extraction off is total: every one of the 943 units is auto-keyed, nothing
supersedes, and not one edge exists. That is defect (a) confirmed at the
storage layer, not inferred from served rows.

Extraction on does supersede — but inspect what it supersedes. Every one of the
34 baseline supersessions is the same conversational filler closing its own
generation:

```
i like the sound of that      =>  i like the sound of that
hmm i like the sound of that  =>  i like the sound of that
damn, i like the sound of that => i like the sound of that
```

Identical phrases collide, so identical phrases supersede. A restatement in
*different words* still does not, which is the case the evolving-state axis is
made of.

The reason is the key derivation. `ExtractedFact`
(`crates/memphant-core/src/service.rs:6211-6220`) builds the subject key as
`{scope}:{family}:{subject_phrase}`, where `subject_phrase` is the mined
verbatim object phrase — so 218 preference units carry 212 distinct keys.
Extraction mines an honest topic only when the sentence has an explicit topic
slot ("my favorite tea is chamomile" → `preference:favorite tea`, the case
`crates/memphant-core/tests/fact_extraction.rs` pins). An open-ended
preference has no such slot, so `clean_object` takes the whole object phrase:

```
preference:looking at this through like a comparative lens
preference:getting really specific examples like that
preference:having a framework before i dive in
```

Supersession is therefore keyed on **lexical phrase identity**, not semantic
subject identity. HorizonBench evolves a preference by restating it in
different words, so the later statement mints a new key and the obsolete belief
survives beside it. Both reach the reader; the reader picks the stale one. This
is visible in the paid result as evolved distractor selections rising from 10
to 17.

## The fix — semantic subject identity, and what it is worth

`MemoryService::with_subject_resolution_threshold` (env
`MEMPHANT_SUBJECT_RESOLUTION_THRESHOLD`, **default off**): before admission, a
mined candidate whose subject phrase is at least this cosine similar to an open
unit's adopts that unit's `fact_key`, and the existing subject-key supersedence
machinery closes the generation. Matching is confined to the candidate's own
subject family, and the candidate adopts the stored key rather than
re-deriving one, so the keys are equal by construction. It reads the whole open
scope, like the write compiler it feeds.

Threshold sweep on the same ten users:

| threshold | superseded | edges | distinct preference keys |
|---|---:|---:|---:|
| off | 34 | 102 | 212 |
| 0.95 | 36 | 108 | 210 |
| 0.90 | 37 | 111 | 210 |
| 0.85 | **42** | 126 | **206** |

Monotone and small — no runaway merging at any threshold tried. The new merges
at 0.85 are the intended shape:

```
and just throw ideas at me fast, i like the rapid-fire style => i love rapid-fire ideas
ngl i usually hate confronting people but this is getting old => i really hate confrontations …
```

and at least one is arguable (`i love the cumbia comparison` → `u know how much
i love cumbia` merges liking a comparison with liking the music). On a
three-pair eyeball that is two clean and one questionable.

**This is a mechanism that now works, not a measured accuracy win.** Eight extra
supersessions across ten users is not evidence for the axis, and the burned
tranche cannot supply that evidence. It ships default-off with the threshold as
a calibration knob; promotion needs a fresh Horizon tranche.

## Consequence for the program

- The -15.8pp is not evidence that the substrate loses on evolving state. It is
  evidence about a build with state compilation off. It remains decisional
  negative evidence for that configuration and the tranche stays burned.
- Stage 2/3 levers (`pack_render_cap`, pool depth, thresholds) cannot address
  this and should not be spent against it.
- The same default contaminates the local lane: `memphant-eval` defaults
  `fact_extraction = false` (`crates/memphant-eval/src/main.rs:468`), and the
  banked LME-S dev runs record `fact_extraction false`. Most local accuracy
  evidence measures the no-supersession path.

## Note on the cheap LME-S funnel join

Joining `rung7-packing/dev-drop-cause.jsonl` to
`unified-sota-20260713/reader-solpro-memphant.json` on `question_id` is invalid
as-is. Both declare identical config and the same `dataset_sha256`, but the
bucket run reads r@10 0.6145 and the reader run 0.7771 — they differ by two
ingestion fixes the headers do not capture, so the reader answered against a
different packed context. A valid four-stage funnel needs one run at current
HEAD emitting `--emit-trace-classification` and `--emit-qa` together, and its
stage-4 leg costs reader calls.

## Reproduction

```sh
MEMPHANT_SCRATCH_ACTIVE=1 DATABASE_URL="$URL" MEMPHANT_FACT_EXTRACTION=1 \
MEMPHANT_SUBJECT_RESOLUTION_THRESHOLD=0.85 \
python3 scripts/run_horizonbench.py build-fast-evidence \
  --source ~/.cache/memphant-bench/horizonbench/<rev>/sample.jsonl \
  --database-url "$URL" --out <dir>/evidence.jsonl --report-out <dir>/gate.json
```

`MEMPHANT_FACT_EXTRACTION` now selects the arm on the free sample path and the
recorded harness flag reports which arm ran; the sealed confirmation path is
unchanged and still hardcodes the off arm. Setting `MEMPHANT_SCRATCH_ACTIVE=1`
with your own migrated `DATABASE_URL` keeps the units after the run — without
it the scratch database is dropped and only served evidence survives, which is
exactly how the corrected claim above went wrong the first time.

## Verification

- `python3 -m pytest tests/test_horizonbench_contract.py -q` — 26 passed.
- `cargo test -p memphant-core --test subject_resolution` — 3 passed: a
  restatement supersedes, an unrelated preference does not merge, and the
  threshold-off path still mints two keys.
- Every arm: 10/10 non-degraded evidence rows, 0 degraded, 943/943 compiled.
