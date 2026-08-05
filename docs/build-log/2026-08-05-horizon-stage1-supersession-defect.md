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

Free two-arm re-run of the ten exposed pilot users (`build-fast-evidence`, the
same users the frozen 60-user tranche excludes, so nothing held out was
touched). Identical corpus, 943 sessions retained and compiled in both arms:

| arm | evidence units | explicit keys | semantic units | generation > 0 | closed `valid_to` |
|---|---:|---:|---:|---:|---:|
| `fact_extraction=off` | 97 | 0 | 0 | 0 | 0 |
| `fact_extraction=on` | 147 | 56 | 56 | **0** | **0** |

Extraction restores the supersession-capable channel — 56 semantic units with
explicit subject keys appear — and **still nothing supersedes**.

The reason is the key derivation. `ExtractedFact`
(`crates/memphant-core/src/service.rs:6211-6220`) builds the subject key as
`{scope}:{family}:{subject_phrase}`, where `subject_phrase` is the mined
verbatim object phrase. All 56 explicit keys in the on-arm are distinct; not one
recurs:

```
preference:looking at this through like a comparative lens
preference:getting really specific examples like that
preference:having a framework before i dive in
```

Supersession is keyed on **lexical phrase identity**, not semantic subject
identity. HorizonBench evolves a preference by restating it in different words,
so the later statement mints a new key and the obsolete belief survives beside
it. Both reach the reader; the reader picks the stale one. This is visible in
the paid result as evolved distractor selections rising from 10 to 17.

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
python3 scripts/run_horizonbench.py build-fast-evidence \
  --source ~/.cache/memphant-bench/horizonbench/<rev>/sample.jsonl \
  --out <dir>/on-evidence.jsonl --report-out <dir>/on-gate.json
```

`MEMPHANT_FACT_EXTRACTION` now selects the arm on the free sample path; the
recorded harness flag reports which arm ran. The sealed confirmation path is
unchanged and still hardcodes the off arm.

## Verification

- `python3 -m pytest tests/test_horizonbench_contract.py -q` — 26 passed.
- Both arms: 10/10 non-degraded evidence rows, 0 degraded, 943/943 compiled.
