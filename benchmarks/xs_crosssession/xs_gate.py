#!/usr/bin/env python3
"""XS lane acquisition-gate checks (prereg A.1), all $0.

Runs against a bank JSON + units.jsonl:
  1. leak check    — any forbidden_term present in its question => defect list
  2. death-from-below — BM25 and naive-grep trivial rules, hits@10
  3. recency rule  — keyword match ranked by latest date in unit text, hits@10

hit = any answer_bearing_id in top-10. Exit nonzero on leak defects.
"""

import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

UNITS = Path.home() / ".memphant-private/xs-crosssession/units.jsonl"
K = 10

STOP = set("""a an the is are was were be been being to of in on for with and or
not no as at by from this that these those it its if then else when where how
what which who you your we our they their i my me do does did done can could
should would may might must will shall about into over under after before""".split())


def toks(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9_]+", text.lower()) if t not in STOP]


def load_units() -> list[dict]:
    return [json.loads(l) for l in UNITS.read_text().splitlines()]


def bm25_rank(units: list[dict], question: str) -> list[str]:
    docs = [toks(u["text"]) for u in units]
    n = len(docs)
    avgdl = sum(len(d) for d in docs) / n
    df: Counter = Counter()
    for d in docs:
        df.update(set(d))
    k1, b = 1.5, 0.75
    q = toks(question)
    scores = []
    for u, d in zip(units, docs):
        tf = Counter(d)
        s = 0.0
        for t in q:
            if t not in tf:
                continue
            idf = math.log((n - df[t] + 0.5) / (df[t] + 0.5) + 1)
            s += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(d) / avgdl))
        scores.append((s, u["id"]))
    scores.sort(key=lambda x: (-x[0], x[1]))
    return [i for _, i in scores[:K]]


def grep_rank(units: list[dict], question: str, df: Counter, n: int) -> list[str]:
    """Naive-grep rule: 3 rarest question words, rank by total match count."""
    q = sorted(set(toks(question)), key=lambda t: df.get(t, 0))[:3]
    scores = []
    for u in units:
        low = u["text"].lower()
        c = sum(low.count(t) for t in q)
        scores.append((c, u["id"]))
    scores.sort(key=lambda x: (-x[0], x[1]))
    return [i for _, i in scores[:K]]


DATE_RE = re.compile(r"20\d{2}-\d{2}(?:-\d{2})?")


def recency_rank(units: list[dict], question: str) -> list[str]:
    q = set(toks(question))
    scores = []
    for u in units:
        if not q & set(toks(u["text"])):
            continue
        dates = DATE_RE.findall(u["text"])
        scores.append((max(dates) if dates else "", u["id"]))
    scores.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [i for _, i in scores[:K]]


def main(bank_path: str) -> int:
    raw = json.loads(Path(bank_path).read_text())
    bank = raw["goldens"] if isinstance(raw, dict) else raw
    units = load_units()
    unit_ids = {u["id"] for u in units}
    df: Counter = Counter()
    for u in units:
        df.update(set(toks(u["text"])))

    leaks, bad_ids = [], []
    for g in bank:
        qlow = g["question"].lower()
        for t in g["forbidden_terms"]:
            if t.lower() in qlow:
                leaks.append((g["id"], t))
        for a in g["answer_bearing_ids"]:
            if a not in unit_ids:
                bad_ids.append((g["id"], a))

    results = {}
    for name, fn in [("bm25", lambda g: bm25_rank(units, g["question"])),
                     ("grep", lambda g: grep_rank(units, g["question"], df, len(units))),
                     ("recency", lambda g: recency_rank(units, g["question"]))]:
        per = {}
        hits = 0
        for g in bank:
            top = fn(g)
            h = bool(set(g["answer_bearing_ids"]) & set(top))
            hits += h
            per[g["id"]] = h
        results[name] = {"hits_at_10": hits / len(bank), "n": len(bank), "per": per}

    out = {"leak_defects": leaks, "unknown_answer_ids": bad_ids,
           "rules": {k: {"hits_at_10": v["hits_at_10"], "n": v["n"]}
                     for k, v in results.items()},
           "per_question": {k: v["per"] for k, v in results.items()}}
    print(json.dumps(out, indent=1))
    return 1 if (leaks or bad_ids) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
