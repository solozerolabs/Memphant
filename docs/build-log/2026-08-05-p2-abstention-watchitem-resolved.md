# P-2 abstention watch-item — RESOLVED (false alarm), 2026-08-05

Spec 30 §7b flagged that promoting `merge_chunk_blocks` default-ON produced "2 stable abstention regressions of 12 abstention questions" on the paid n=178 reader-QA run — apparently the merge reshuffling the pack so the reader answered where it should abstain. Diagnosed here from the committed reader reports; **$0, no new model calls.**

## Finding: the merge causes ZERO hallucinations on abstention questions

`scripts/run_reader.py:1666` scores an abstention question correct **only** when the reader emits the structured pair `abstain=True` AND `answer=None`. A natural-language refusal with `abstain=False` — e.g. *"It cannot be determined from the provided evidence."* — is scored WRONG despite being a plain abstention.

Reading all 24 abstention answers (12 questions × 2 arms), every "regression" is a prose refusal, not a fabricated answer:

| question | OFF answer | ON answer | exact | semantic |
|---|---|---|---|---|
| 60bf93ed_abs | `<null>` | "I can't determine how many days it took…" | 1→0 | 1→1 |
| edced276_abs | `<null>` | "The total cannot be determined…" | 1→0 | 1→1 |
| gpt4_70e84552_abs | `<null>` | "It cannot be determined from the evidence." | 1→0 | 1→1 |

The reader **abstained in both arms**; under merge it phrased the abstention in prose (with `abstain=False`) instead of the structured null. The form flips **both** directions — 2 questions also went prose→structured under merge (`0862e8bf_abs`, `88432d0a_abs`, "regressions" in OFF that ON fixed) — so the exact scorer's abstention noise is roughly symmetric reader nondeterminism in *how* abstention is expressed, not a merge quality effect. The only genuinely fabricated answer (`09ba9854_abs`, a made-up dollar figure) is **merge-invariant** (both arms).

**Conclusion: the watch-item is a false alarm. P-2 does not make the reader answer where it should abstain. No P-2 guard is warranted.**

## Bonus: the exact scorer slightly UNDERSTATED P-2

Re-grading every abstention question semantically (refusal — structured null or prose — is correct; only a fabricated substantive answer is wrong) and recomputing the paired McNemar (`abstention-diagnosis/regrade.py`):

| grading | B (improved) | C (regressed) | net | exact McNemar p |
|---|---|---|---|---|
| exact (`abstention_exact`) | 15 | 5 | +10 | 0.0414 |
| **semantic re-grade** | **13** | **2** | **+11** | **0.0074** |

The exact scorer's abstention-form noise leaned net −1 against P-2 (3 form-regressions vs 2 form-improvements), so it *understated* the effect. Under semantic grading P-2's reader-QA win is **stronger and more significant** — the promotion is robust to the scoring defect.

## Separable instrument finding (NOT a P-2 issue)

`abstention_exact` (`run_reader.py:1666`) under-credits prose refusals: 5 of 12 abstention questions flipped form between two otherwise-comparable arms, injecting symmetric noise into any paired reader-QA abstention comparison. This affects every reader-QA lane, not just P-2. Filed as a follow-up (credit a reader response that refuses in prose, e.g. `abstain OR answer is a refusal`, ideally via the LLM judge rather than a regex). Fixing it would tighten the abstention endpoint for all future paid runs.

Artifacts: `abstention-diagnosis/regrade.py` (reproducible from the committed reader reports).
