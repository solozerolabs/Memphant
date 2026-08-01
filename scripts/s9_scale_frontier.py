#!/usr/bin/env python3
"""S9 — the enumeration affordability frontier and the retrieval crossover.

$0. Stdlib only. No server, no database, no port, no model call.

Every constant below is either MEASURED (recomputed here from a banked artifact,
or from a file count taken on this host) or FETCHED (a catalogue price read from
a live public page on 2026-08-01, recorded with its URL). Nothing is assumed.

Inputs
------
  A. ~/.memphant-private/track-r-paraphrase/run-s4/agentic-final-provenance.json
     -> chars per enumerated item, agentic amplification, wall latency, spend.
  B. ~/.memphant-private/track-r-paraphrase/run-fusion/fusion_probe-provenance.json
     -> the banked Coverage(k) curve.
  C. ~/.memphant-private/track-r/artifacts/corpus.jsonl
     -> per-attempt event counts and bytes.
  D. docs/build-log/artifacts/s9-scale/track-r-repo-sizes.json
     -> file / source-file / authored-source counts for Track R's 134 repos,
        taken from the GitHub git/trees API at HEAD on 2026-08-01.

Usage:  python3 scripts/s9_scale_frontier.py [--write]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics as st
import sys

PRIV = os.path.expanduser("~/.memphant-private")
S4 = f"{PRIV}/track-r-paraphrase/run-s4/agentic-final-provenance.json"
FUSION = f"{PRIV}/track-r-paraphrase/run-fusion/fusion_probe-provenance.json"
CORPUS = f"{PRIV}/track-r/artifacts/corpus.jsonl"
GOLDEN = f"{PRIV}/track-r-paraphrase/track_r_paraphrase_golden.jsonl"
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPOS = f"{HERE}/docs/build-log/artifacts/s9-scale/track-r-repo-sizes.json"
OUT = f"{HERE}/docs/build-log/artifacts/s9-scale/frontier.json"

# FETCHED 2026-08-01 from https://platform.claude.com/docs/en/about-claude/pricing
# ("Model pricing" table, row "Claude Opus 5"). Cross-checked against
# https://openrouter.ai/anthropic/claude-opus-5 ($5 in / $25 out, 1M context).
PRICE = {
    "model": "claude-opus-5",
    "input_usd_per_mtok": 5.0,
    "output_usd_per_mtok": 25.0,
    "cache_read_usd_per_mtok": 0.50,
    "cache_write_5m_usd_per_mtok": 6.25,
    "batch_input_usd_per_mtok": 2.50,
    "context_window_tokens": 1_000_000,
    "source": "https://platform.claude.com/docs/en/about-claude/pricing",
    "fetched": "2026-08-01",
}
IN_USD = PRICE["input_usd_per_mtok"] / 1e6
OUT_USD = PRICE["output_usd_per_mtok"] / 1e6


def pct(xs, f):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(f * (len(xs) - 1))))]


# --------------------------------------------------------------------------
# A. the enumeration unit, the amplification factor, and the token rate
# --------------------------------------------------------------------------
def measure_s4():
    d = json.load(open(S4))
    T = d["transcript"]
    per_item, items, idx_chars = [], [], []
    resent_chars = 0
    for q in T:
        n = q["events_available"]
        running = 0
        results = []
        for c in q["tool_call_log"]:
            if c["tool"] == "list_events":
                idx_chars.append(c["result_chars"])
                items.append(n)
                per_item.append(c["result_chars"] / n)
            results.append(c["result_chars"])
        # every API call resends the whole conversation, so tool text sent to
        # the provider is the running prefix sum, not the one-off total.
        for i in range(len(results)):
            running += results[i]
            resent_chars += running
    u = d["usage"]
    nq = len(T)
    tok_per_char = u["prompt_tokens"] / resent_chars
    cost = (u["prompt_tokens"] * IN_USD + u["completion_tokens"] * OUT_USD) / nq
    n_mean = st.mean(items)
    ch_item = st.mean(per_item)
    single_pass = n_mean * ch_item * tok_per_char * IN_USD
    return {
        "n_questions": nq,
        "list_events_calls": len(idx_chars),
        "chars_per_enumerated_item_mean": round(ch_item, 2),
        "chars_per_enumerated_item_p50": round(st.median(per_item), 2),
        "items_enumerated_mean": round(n_mean, 1),
        "items_enumerated_max": max(items),
        "list_events_cap": 200,
        "prompt_tokens": u["prompt_tokens"],
        "completion_tokens": u["completion_tokens"],
        "tool_text_chars_resent": resent_chars,
        "tokens_per_char_upper_bound": round(tok_per_char, 4),
        "usd_per_question": round(cost, 5),
        "reported_spend_usd": d["reported_spend_usd"],
        "mean_tool_calls": round(st.mean(q["tool_calls"] for q in T), 3),
        "amplification_vs_single_pass": round(cost / single_pass, 3),
        "wall_seconds_total_full_run": 486.62,
        "wall_concurrency": 6,
        "wall_seconds_per_question": round(486.62 * 6 / nq, 2),
        "wall_source": "run-s4/agentic-full-provenance.json elapsed_seconds; "
                       "--concurrency default 6 in code_lane_run_agentic_control.py",
    }


# --------------------------------------------------------------------------
# B. the banked Coverage(k) curve
# --------------------------------------------------------------------------
def measure_coverage():
    d = json.load(open(FUSION))
    pq = d["per_question"]
    n = len(pq)
    ranks = [q["gold_fused_rank"] for q in pq
             if q.get("gold_in_pool") and q.get("gold_fused_rank") is not None]
    cov = {str(k): round(sum(1 for r in ranks if r <= k) / n, 4)
           for k in (1, 3, 5, 10, 16, 32, 64, 128, 256)}
    pools = [q["pool_size"] for q in pq]
    return {
        "n": n,
        "in_pool": len(ranks),
        "in_pool_rate": round(len(ranks) / n, 4),
        "coverage_at_k": cov,
        "gold_rank_p50": st.median(ranks),
        "gold_rank_p90": pct(ranks, 0.90),
        "gold_rank_max": max(ranks),
        "pool_size_p50": st.median(pools),
        "pool_size_max": max(pools),
        "recall_seconds_total": d["timings"]["recall_seconds"],
        "recall_seconds_per_question": round(d["timings"]["recall_seconds"] / n, 3),
        "llm_calls_at_recall": 0,
        "marginal_llm_usd_at_recall": 0.0,
    }


# --------------------------------------------------------------------------
# C. the Track R haystack distribution
# --------------------------------------------------------------------------
def measure_corpus():
    ev, by = [], []
    for line in open(CORPUS):
        o = json.loads(line)
        ev.append(len(o["events"]))
        by.append(sum(len(e["text"].encode()) for e in o["events"]))
    att, repos = set(), set()
    for line in open(GOLDEN):
        r = json.loads(line)
        s = r["provenance"] if isinstance(r["provenance"], str) else json.dumps(r["provenance"])
        att.update(re.findall(r"'attempt_id': '([^']+)'", s))
        repos.add(r["repository"])
    return {
        "sampled_attempts": len(ev),
        "emitted_events": sum(ev),
        "bound_attempts": len(att),
        "bound_repositories": len(repos),
        "events_per_attempt": {"min": min(ev), "p50": st.median(ev), "p90": pct(ev, .9),
                               "max": max(ev), "mean": round(st.mean(ev), 1)},
        "bytes_per_event_mean": round(sum(by) / sum(ev), 1),
        "bytes_per_attempt_p50": st.median(by),
    }


# --------------------------------------------------------------------------
# D. repo sizes
# --------------------------------------------------------------------------
def measure_repos():
    res = json.load(open(REPOS))
    out = {"n_repos": len(res)}
    for key in ("files", "source_files", "authored_source"):
        v = [r[key] for r in res]
        out[key] = {"min": min(v), "p10": pct(v, .10), "p25": pct(v, .25),
                    "p50": pct(v, .50), "p75": pct(v, .75), "p90": pct(v, .90),
                    "p95": pct(v, .95), "p99": pct(v, .99), "max": max(v),
                    "mean": round(st.mean(v), 1)}
    return out


# --------------------------------------------------------------------------
# the frontier (MODELLED: arithmetic on the measured inputs above)
# --------------------------------------------------------------------------
def frontier(s4, ch_path=61.9):
    t = s4["tokens_per_char_upper_bound"]
    amp = s4["amplification_vs_single_pass"]
    units = {
        "preview_index": s4["chars_per_enumerated_item_mean"],   # S4's measured regime
        "path_only_index": ch_path,                              # measured blob path length + \n
    }
    rows = {}
    for label, ch in units.items():
        tpi = ch * t
        rows[label] = {
            "chars_per_item": ch,
            "tokens_per_item": round(tpi, 2),
            "usd_per_item_single_pass": tpi * IN_USD,
            "usd_per_item_agentic": tpi * IN_USD * amp,
            "at_N": {f"1e{e}": {
                "tokens": round(10 ** e * tpi),
                "usd_single_pass": round(10 ** e * tpi * IN_USD, 4),
                "usd_agentic": round(10 ** e * tpi * IN_USD * amp, 4),
            } for e in (2, 3, 4, 5, 6)},
            "thresholds": {
                "N_at_200k_context": round(200_000 / tpi),
                "N_at_1M_context": round(1_000_000 / tpi),
                "N_at_1usd_single_pass": round(1.0 / (tpi * IN_USD)),
                "N_at_1usd_agentic": round(1.0 / (tpi * IN_USD * amp)),
            },
        }
    lat = s4["wall_seconds_per_question"]
    rows["latency"] = {
        "measured_seconds_at_N": {"N": s4["items_enumerated_mean"], "seconds": lat},
        "N_at_10s_linear_model": round(s4["items_enumerated_mean"] * 10.0 / lat),
        "note": "linear-in-N is the optimistic model: it ignores that more items "
                "provoke more tool calls, and each call is a serial round trip.",
    }
    return rows


def crossover(cov):
    """Accuracy crossover, parameterised in the UNMEASURED RankAcc.

    A_enum(N)     = RankAcc(N)                 gold is in the haystack by construction
    A_hybrid(k)   = Coverage(k) * RankAcc(k)
    hybrid wins  <=>  RankAcc(N) / RankAcc(k)  <  Coverage(k)
    """
    c = cov["coverage_at_k"]
    return {
        "condition": "RankAcc(N)/RankAcc(k) < Coverage(k)",
        "required_relative_decay_by_k": {
            k: {"coverage": c[k], "max_ratio_RankAcc_N_over_RankAcc_k": c[k],
                "min_relative_decay_pct": round(100 * (1 - c[k]), 1)}
            for k in ("5", "10", "16", "32", "64", "128")},
        "measured_RankAcc_point": {
            "N": 140.6, "value": 0.9667,
            "source": "s4-controls agentic_grep 174/180 at full enumeration",
        },
        "unmeasured": "RankAcc(k) for k != ~140 — owned by lane s8-hybrid. "
                      "No value is invented here.",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    s4 = measure_s4()
    cov = measure_coverage()
    doc = {
        "lane": "s9-scale",
        "date": "2026-08-01",
        "paid_model_calls": 0,
        "price": PRICE,
        "measured": {"s4_enumeration": s4, "coverage": cov,
                     "track_r_corpus": measure_corpus(), "track_r_repos": measure_repos()},
        "modelled": {"frontier": frontier(s4), "crossover": crossover(cov)},
    }
    print(json.dumps(doc, indent=1, default=str))
    if a.write:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        json.dump(doc, open(OUT, "w"), indent=1, default=str)
        print("wrote " + OUT, file=sys.stderr)


if __name__ == "__main__":
    main()
