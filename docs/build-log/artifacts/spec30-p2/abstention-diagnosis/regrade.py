#!/usr/bin/env python3
"""Diagnose the spec 30 §7b abstention watch-item: are P-2's abstention
"regressions" real (merge makes the reader answer where it should abstain), or
a scoring-form artifact?

`run_reader.py:1666` scores an abstention question correct ONLY when the reader
sets the structured `abstain=True` AND `answer is None`. A natural-language
refusal ("cannot be determined from the provided evidence") with `abstain=False`
is scored WRONG even though the reader plainly abstained. This re-grades every
abstention question semantically — a refusal (structured null OR prose) is
correct; only a fabricated substantive answer is wrong — and recomputes the
paired McNemar. Run from repo root against the committed reader reports.
"""
import json, re
from math import comb

QAD = "docs/build-log/artifacts/spec30-p2/reader-qa"
def by_id(p): return {r["question_id"]: r for r in json.load(open(p))["per_question"]}
off = by_id(f"{QAD}/reader-mergeoff.json")
on = by_id(f"{QAD}/reader-mergeon.json")

# Deliberately broad — the only questions whose *paired* grade it can change are
# the ones that flip form between arms, and those are all unambiguous refusals
# ("cannot be determined" / null); the borderline substantive answers are
# merge-invariant, so their classification never affects the paired result.
REFUSAL = re.compile(
    r"can[’']?t (?:be )?determine|cannot be determined|"
    r"not (?:provided|mentioned|enough|documented|given|available)|"
    r"does not mention|didn[’']?t mention|no (?:information|mention|evidence)|"
    r"is not provided",
    re.I,
)
def semantic_correct(r):
    if not r["is_abstention"]:
        return bool(r["correct"])          # non-abstention: keep the LLM judge
    a = r["answer"]
    return a is None or bool(REFUSAL.search(a))

def mcnemar(grade):
    B = sum(1 for q in off if not grade(off[q]) and grade(on[q]))
    C = sum(1 for q in off if grade(off[q]) and not grade(on[q]))
    nd = B + C; k = min(B, C)
    p = min(1.0, 2 * sum(comb(nd, i) for i in range(k + 1)) / 2**nd) if nd else 1.0
    return B, C, p

if __name__ == "__main__":
    eB, eC, ep = mcnemar(lambda r: bool(r["correct"]))
    sB, sC, sp = mcnemar(semantic_correct)
    print(f"EXACT scorer     : B={eB} C={eC} net +{eB-eC} p={ep:.4f}")
    print(f"SEMANTIC re-grade: B={sB} C={sC} net +{sB-sC} p={sp:.4f}")
    hallu = [q for q in off if off[q]["is_abstention"]
             and not (semantic_correct(off[q]) and semantic_correct(on[q]))]
    print(f"abstention Qs with a non-refusal in either arm (merge-invariant): {hallu}")
