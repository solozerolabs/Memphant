# HorizonBench full census and held-out selection

**Result:** The free census passed with explicit exclusions, and a 60-user /
120-item paired tranche is frozen. No provider, reader, generative, or paid call
ran.

The six pinned benchmark Parquets total 1,601,956,117 bytes and contain 4,245
unique rows from 346 benchmark-contributing users: 1,052 Sonnet 4.5 rows, 981
o3 rows, and 2,212 Gemini 3 Flash rows. An identity-only remote projection of
the three mental-state-graph Parquets confirmed all 346 benchmark users within
the official 360-user population and found 14 graph-only users. Graph payloads
were neither acquired nor parsed.

## Integrity findings

- Conversation bytes are question-time prefixes, not one repeated immutable
  timeline. 344/346 users form monotone prefix histories.
- `gemini-3-flash/user_15` and `gemini-3-flash/user_49` are identity collisions:
  each has two incompatible conversation roots. Both are excluded.
- Options are valid ordered A-E prefixes, but ten rows have fewer than five
  choices: one has three and nine have four.
- 992/2,484 evolved rows omit `distractor_letter`. This does not affect exact
  answer accuracy, but it weakens the distractor-error secondary metric and is
  reported separately rather than imputed.

The dataset is therefore usable for a scoped held-out preference/UX result,
not pristine enough to justify an unqualified official-full result. The fixed
selection excludes all ten exposed pilot users and both collision users, then
chooses 20 users per generator with one static and one evolved question each.
It contains 60 static and 60 evolved items; 37/60 evolved items have a released
distractor label and 23/60 do not. Selection uses identity, generator, and the
evolved/static stratum only; answer and distractor values do not affect ranking.

The runtime must retain stable turn episodes incrementally for each user,
recall the earlier question before adding later turns, then add only the delta
before the second recall. Re-ingesting a whole timeline per question would
double work; ingesting the longest timeline up front would leak future events.

Proof: `docs/build-log/artifacts/horizonbench-confirmation/full-census.json`,
`docs/build-log/artifacts/horizonbench-confirmation/selection.json`, and
`benchmarks/manifests/horizonbench.benchmark.v1.json`.
