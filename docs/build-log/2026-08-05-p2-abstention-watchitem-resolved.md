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

## Separable instrument finding — FIXED (NOT a P-2 issue)

`abstention_exact` (`run_reader.py`) under-credited prose refusals: 5 of 12 abstention questions flipped form between two otherwise-comparable arms, injecting symmetric noise into any paired reader-QA abstention comparison. This affected every reader-QA lane, not just P-2.

**Fix landed** in both judge paths (`judge_row` for the longmemeval profile, `judge_rag_row` for rag-supported): an abstention question with **no answer text** (structured null or empty) is a correct decline, scored free; **any answer text** is delegated to the LLM judge (`build_abstention_judge_prompt`, reusing the `judge` kind's model + strict yes/no verdict schema, method `abstention_llm_judge`), which separates a prose refusal ("cannot be determined" → correct) from a fabricated answer (→ wrong). The `abstain` flag is now advisory — the answer TEXT decides — because the reader sets the flag inconsistently for prose refusals. No contract change (reuses the existing judge schema), so no new stub round-trip. `gate_compare.py`'s negative-gate guard was widened to accept `abstention_llm_judge` alongside `abstention_exact` (both are valid abstention evaluations). Tests: 6 added + 1 pre-existing updated in `tests/test_run_reader_contract.py`; full Python suite green (837 passed).

Cost note: abstention questions that refuse in prose now spend one judge call (structured/null abstentions stay free), which the packet minter already budgets — `calls_per_question=2` was always the reader+judge estimate.

**End-to-end confirmation (paid, settled $0.19).** Re-judged the committed P-2 reader answers through the LANDED fix with the real sol-pro judge — judge-only, 13 calls (one per abstention row with a non-null answer, both arms), no reader re-run. The real judge reproduces the deterministic prediction exactly:

| grading | B | C | net | p |
|---|---|---|---|---|
| exact scorer (committed) | 15 | 5 | +10 | 0.0414 |
| **LLM re-judge (landed fix, real sol-pro)** | **13** | **2** | **+11** | **0.0074** |

Every prose refusal ("cannot be determined", "does not mention", "name not provided") scored a correct abstention; the sole fabricated answer (`09ba9854_abs`, the invented "$40–50") scored wrong in both arms. The three exact-scorer "regressions" (`60bf93ed`, `edced276`, `gpt4_70e84552`) all flip to correct-in-both-arms under the real judge. Reproducible: `rejudge_confirm.py` + `reader-qa/rejudge/rejudge_result.json`.

Artifacts: `abstention-diagnosis/regrade.py` (the deterministic re-grade that first demonstrated the +11 semantic result); `rejudge_confirm.py` (the paid confirmation via the landed LLM judge). Both agree: +11, p=0.0074.
