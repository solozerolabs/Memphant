#!/usr/bin/env python3
"""Confirm the abstention-scorer fix on real data: re-judge the committed P-2
reader answers through the LANDED abstention judge (real sol-pro calls) and
recompute the paired McNemar. Judge-only — the reader answers are already
recorded, so this makes ~13 calls (one per abstention row with a non-null
answer, both arms), not a full re-run.

Run under doppler for OPENROUTER_API_KEY. Governance-compliant: judge calls go
through an authorized campaign ledger opened from a freshly minted packet.
"""
import importlib.util, json, sys
from decimal import Decimal
from math import comb
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # …/docs/build-log/artifacts/spec30-p2/<file>
def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
rr = _load("run_reader", "scripts/run_reader.py")

QAD = ROOT / "docs/build-log/artifacts/spec30-p2/reader-qa"
RJ = QAD / "rejudge"

ledger = rr.open_campaign_ledger(
    RJ / "authorization-rejudge.json",
    screen_id="reader-rejudge",
    expected_journal_path=RJ / "attempts-rejudge.jsonl",
)
cli = rr.ReaderCli(
    "openrouter", "openai/gpt-5.6-luna-pro", "openai/gpt-5.6-sol-pro",
    RJ / "cache", 400, reasoning_effort="high",
    max_spend_usd=Decimal("118.63"),
    max_price_per_million={"prompt": Decimal("10"), "completion": Decimal("40")},
    max_output_tokens=1024,
)
cli.set_provider_attempt_ledger(ledger)

def by_id(p): return {r["question_id"]: r for r in json.loads(p.read_text())["per_question"]}
off = by_id(QAD / "reader-mergeoff.json")
on = by_id(QAD / "reader-mergeon.json")

def regrade(row):
    """Apply the landed abstention rule to a committed reader row."""
    if not row["is_abstention"]:
        return bool(row["correct"])          # non-abstention: keep the committed judge grade
    ans = row["answer"]
    if ans is None:
        return True                          # structured/empty decline — free
    verdict = cli.call("judge", rr.JUDGE_SYSTEM_PROMPT,
                       rr.build_abstention_judge_prompt(row["question"], str(row["gold_answer"]), ans))
    return rr.parse_judge_output(verdict, "openrouter") == "yes"

rg_off = {q: regrade(off[q]) for q in off}
rg_on = {q: regrade(on[q]) for q in on}

def mcnemar(go, gn):
    B = sum(1 for q in go if not go[q] and gn[q])
    C = sum(1 for q in go if go[q] and not gn[q])
    nd = B + C; k = min(B, C)
    p = min(1.0, 2 * sum(comb(nd, i) for i in range(k + 1)) / 2**nd) if nd else 1.0
    return B, C, p

eB, eC, ep = mcnemar({q: bool(off[q]["correct"]) for q in off},
                     {q: bool(on[q]["correct"]) for q in on})
sB, sC, sp = mcnemar(rg_off, rg_on)
print(f"EXACT scorer (committed): B={eB} C={eC} net +{eB-eC} p={ep:.4f}")
print(f"LLM re-judge (landed fix): B={sB} C={sC} net +{sB-sC} p={sp:.4f}")
print("\nabstention rows the real judge scored (non-null answers):")
for q in sorted(x for x in off if off[x]["is_abstention"]):
    print(f"  {q:<20} OFF exact/judge={int(bool(off[q]['correct']))}/{int(rg_off[q])}"
          f"  ON exact/judge={int(bool(on[q]['correct']))}/{int(rg_on[q])}")
json.dump({"exact": {"B": eB, "C": eC, "p": ep},
           "llm_rejudge": {"B": sB, "C": sC, "p": sp},
           "abstention_regrade": {q: {"off": rg_off[q], "on": rg_on[q]}
                                  for q in sorted(x for x in off if off[x]["is_abstention"])}},
          open(RJ / "rejudge_result.json", "w"), indent=1)
print(f"\nwrote {RJ / 'rejudge_result.json'}")
