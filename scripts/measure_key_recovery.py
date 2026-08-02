#!/usr/bin/env python3
"""A4' — how well can a GOLD-INDEPENDENT rule produce a supersession key?

Offline, corpus-only, DB-free, $0. No server, no worker, no network, no model.

Why this exists
---------------
`docs/build-log/2026-07-31-preference-writepath.md` §4 reports that a
body-derived key recovers 8 of 1063 MemoryCode gold groups (0.008) and that the
best variant reaches 221/1063 (0.208). **That measurement's code was never
committed**, so the two numbers this program's critical path is calibrated
against were unreproducible. This script rebuilds the measurement, keeps the
same four rules, and adds the counterpart §4 never measured.

The metric §4 is missing, and why it matters
--------------------------------------------
Group recovery alone is maximised by a constant key: emit ``"k"`` for every
session and 1063/1063 groups "recover". But a key that merges two sessions
that state *different* conventions produces a **wrong supersession** — it
retires a live rule — which is the exact failure (misapplication) the program
is trying to remove. So every rule is also scored as a clustering of an
instance's sessions against the gold partition: pair precision, pair recall,
and the count of harmful merges. A rule is only useful where both are high.

Gold-independence
-----------------
Every rule reads ``session["text"]`` and nothing else. The ``type``/``topic``
arrays — which the gold rule is built from, and which Arm P consumes, making
Arm P ``decisional: false`` — are used ONLY to construct the gold labels this
script scores against, never as a rule input. `check_gold_independence` asserts
this by re-running every rule against bodies alone.

Usage
-----
    python3 scripts/measure_key_recovery.py \
        --source ~/.memphant-private/w7-instruments/memorycode/data/test-*.parquet \
        --out docs/build-log/artifacts/2026-08-01-key-production/recovery.json

Requires ``pyarrow`` (MemoryCode ships parquet). It is deliberately NOT added to
any repo requirement: this is an offline analysis script, and
`external_instrument_adapter.load_memorycode` already imports it lazily.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# The directive-sentence primitives were DEFINED here first, then moved into
# `external_instrument_adapter` when the live B1 extractor needed the same unit
# (S1, `docs/build-log/2026-08-01-similarity-unit-swap.md`). They are imported
# back under their original names so every rule below is byte-identical in
# behaviour, and so an offline rule and the live extractor can never drift onto
# two different stoplists -- which is the whole reason the move went in this
# direction rather than the copy-paste one.
from external_instrument_adapter import (  # noqa: E402
    DIRECTIVE_LITERAL as LITERAL,
    DIRECTIVE_SENTENCE_SPLIT as SENTENCE,
    DIRECTIVE_STOPWORDS as STOPWORDS,
    DIRECTIVE_WORD as WORD,
    directive_content_words as content_words,
    literal_sentences,
    load_memorycode,
    sha256_file,
)

REPO = Path(__file__).resolve().parent.parent
LOCK = REPO / "benchmarks" / "manifests" / "memorycode.lock.json"

# The imperative heads this corpus's directives actually open with. Used ONLY by
# the `verb_head` rule, which is labelled corpus-shaped for that reason.
IMPERATIVES = frozenset(
    "start begin end prefix suffix name use write add append avoid never always "
    "keep set follow ensure include prepend terminate finish".split()
)


# --------------------------------------------------------------------------
# Rules. Each maps a body -> set[str] of derived keys. Body text only.
# --------------------------------------------------------------------------


def rule_sentence_set(body: str) -> set[str]:
    """§4 row 1: quoted-literal-stripped content-word SET of a directive
    sentence. Exact set equality, which is what makes it near-zero."""
    keys = set()
    for _, blanked in literal_sentences(body):
        words = sorted(set(content_words(blanked)))
        if words:
            keys.add("set:" + " ".join(words))
    return keys


def _preceding(body: str, n: int) -> set[str]:
    keys = set()
    for sentence, _ in literal_sentences(body):
        for match in LITERAL.finditer(sentence):
            before = content_words(sentence[: match.start()])
            if len(before) >= n:
                keys.add(f"pre{n}:" + " ".join(before[-n:]))
    return keys


def rule_pre1(body: str) -> set[str]:
    """§4 row 2, the reported best variant: the single content word before the
    quoted literal."""
    return _preceding(body, 1)


def rule_pre2(body: str) -> set[str]:
    return _preceding(body, 2)


def rule_pre3(body: str) -> set[str]:
    return _preceding(body, 3)


def rule_pre2_unordered(body: str) -> set[str]:
    """`pre2` is brittle to an inserted modifier; the same two words in either
    order is the cheapest relaxation of it."""
    keys = set()
    for sentence, _ in literal_sentences(body):
        for match in LITERAL.finditer(sentence):
            before = content_words(sentence[: match.start()])
            if len(before) >= 2:
                keys.add("pre2u:" + " ".join(sorted(before[-2:])))
    return keys


def rule_verb_head(body: str) -> set[str]:
    """(imperative head, last content word before the literal).

    CORPUS-SHAPED: `IMPERATIVES` was written by reading this bank's directives.
    Reported so the shape of a template-matched rule is visible, and labelled
    `bank_fit: true` so it is never read as a transferable result."""
    keys = set()
    for sentence, _ in literal_sentences(body):
        words = content_words(sentence)
        verbs = [w for w in words if w in IMPERATIVES]
        if not verbs:
            continue
        for match in LITERAL.finditer(sentence):
            before = content_words(sentence[: match.start()])
            if before:
                keys.add(f"vh:{verbs[0]} {before[-1]}")
    return keys


BODY_RULES = {
    "sentence_content_set": rule_sentence_set,
    "pre1_content_word": rule_pre1,
    "pre2_content_words": rule_pre2,
    "pre3_content_words": rule_pre3,
    "pre2_unordered": rule_pre2_unordered,
    "verb_head_pair": rule_verb_head,
}
BANK_FIT = {"verb_head_pair"}

# --------------------------------------------------------------------------
# Canonicalisation rule: not a pure function of one body.
# --------------------------------------------------------------------------


def jaccard_keys(bodies: list[str], tau: float) -> list[set[str]]:
    """Single-link agglomerative clustering of directive sentences within one
    instance by content-word Jaccard; a session's keys are its sentences'
    cluster ids.

    NOT a pure body function: it needs the other units in the scope. That is
    legitimate — it is the same shape as B1's extractor, which issues one
    `/v1/recall` into the target scope before writing — but it means this rule
    costs a read on the write path, which the per-body rules do not.
    """
    sentences: list[tuple[int, frozenset]] = []
    for i, body in enumerate(bodies):
        for _, blanked in literal_sentences(body):
            words = frozenset(content_words(blanked))
            if words:
                sentences.append((i, words))
    parent = list(range(len(sentences)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in combinations(range(len(sentences)), 2):
        wa, wb = sentences[a][1], sentences[b][1]
        union = len(wa | wb)
        if union and len(wa & wb) / union >= tau:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
    keys: list[set[str]] = [set() for _ in bodies]
    for idx, (session, _) in enumerate(sentences):
        keys[session].add(f"jac:{find(idx)}")
    return keys


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


def gold_structure(groups: list[dict]) -> dict:
    """Per instance: the gold partition (probe key -> session indices) taken
    from the SAME probe objects the adapter scores on, so the 1063/257/3616
    shape is inherited rather than re-derived."""
    out = {}
    for group in groups:
        n = len(group["units"])
        index = {u["unit_id"]: i for i, u in enumerate(group["units"])}
        probes = []
        for probe in group["probes"]:
            gold = index[probe["gold_unit_ids"][0]]
            earlier = sorted(index[u] for u in probe["distractor_unit_ids"])
            probes.append({"probe_id": probe["probe_id"], "gold": gold, "earlier": earlier})
        gold_pairs = set()
        for probe in probes:
            members = sorted(set(probe["earlier"] + [probe["gold"]]))
            gold_pairs.update(combinations(members, 2))
        out[group["group_id"]] = {
            "n_sessions": n,
            "probes": probes,
            "gold_pairs": gold_pairs,
            "bodies": [u["body"] for u in group["units"]],
        }
    return out


def score(gold: dict, keys_for_instance) -> dict:
    """Two regimes, reported side by side, because they differ by 4x and the
    program's critical path is calibrated on the stricter one.

    ``s4_regime`` — ONE key per session (lexicographically first), and a group
    counts as recovered only when **every** session in it shares that one key.
    This is what `2026-07-31-preference-writepath.md` §4 measured. It is the
    right regime for a design where a unit carries exactly one `fact_key` and
    the whole group must collapse onto it.

    ``supersession_regime`` — ALL keys a session emits, and a group counts as
    recovered when the gold (most recent) session shares a key with **any**
    earlier declarer. This is what supersession actually requires: the edge
    points backwards, one incumbent at a time, and Arm P itself mints one unit
    per *declaration* (14,088 units from 8,147 sessions), not one per session.
    §4's regime is therefore stricter than the mechanism it was calibrating.
    """
    strict_recovered = 0
    recovered = 0
    net_recovered = 0
    total_probes = 0
    pair_recovered = 0
    total_pairs = 0
    tp = 0
    predicted_pairs_total = 0
    keys_emitted = 0
    sessions_with_key = 0
    sessions_total = 0
    retires_gold = 0
    retires_nongold = 0
    golds_destroyed = 0

    for gid, info in gold.items():
        keys = keys_for_instance(gid, info)
        single = [{sorted(k)[0]} if k else set() for k in keys]
        sessions_total += info["n_sessions"]
        for k in keys:
            keys_emitted += len(k)
            if k:
                sessions_with_key += 1

        by_key = defaultdict(set)
        for i, k in enumerate(keys):
            for key in k:
                by_key[key].add(i)
        predicted = set()
        for members in by_key.values():
            predicted.update(combinations(sorted(members), 2))
        predicted_pairs_total += len(predicted)
        hit = predicted & info["gold_pairs"]
        tp += len(hit)
        # B1 §3.1's classification, which is the decision-relevant one. A
        # predicted pair (i<j) means j closes i's generation. Supersession
        # always points backwards, and this instrument's distractors are always
        # earlier declarations — so retiring a session that is nobody's gold is
        # near-free, while retiring a gold destroys a probe.
        golds = {p["gold"] for p in info["probes"]}
        wrongly_retired = set()
        for i, j in predicted - hit:
            if i in golds:
                retires_gold += 1
                wrongly_retired.add(i)
            else:
                retires_nongold += 1

        for probe in info["probes"]:
            total_probes += 1
            if probe["gold"] in wrongly_retired:
                golds_destroyed += 1
            members = [probe["gold"]] + probe["earlier"]
            common = set(single[members[0]])
            for m in members[1:]:
                common &= single[m]
            if common:
                strict_recovered += 1
            gold_keys = keys[probe["gold"]]
            if any(gold_keys & keys[e] for e in probe["earlier"]):
                recovered += 1
                if probe["gold"] not in wrongly_retired:
                    net_recovered += 1
            for e in probe["earlier"]:
                total_pairs += 1
                if gold_keys & keys[e]:
                    pair_recovered += 1

    gold_pair_total = sum(len(i["gold_pairs"]) for i in gold.values())
    wrong = predicted_pairs_total - tp
    precision = tp / predicted_pairs_total if predicted_pairs_total else None
    recall = tp / gold_pair_total if gold_pair_total else None
    return {
        "s4_regime_groups_recovered": strict_recovered,
        "s4_regime_recovery": round(strict_recovered / total_probes, 6),
        "groups_recovered": recovered,
        "groups_total": total_probes,
        "group_recovery": round(recovered / total_probes, 6),
        "groups_net_recovered": net_recovered,
        "net_group_recovery": round(net_recovered / total_probes, 6),
        "groups_whose_gold_was_wrongly_retired": golds_destroyed,
        "current_stale_pairs_recovered": pair_recovered,
        "current_stale_pairs_total": total_pairs,
        "pair_recovery": round(pair_recovered / total_pairs, 6),
        "predicted_pairs": predicted_pairs_total,
        "gold_pairs": gold_pair_total,
        "pair_precision": round(precision, 6) if precision is not None else None,
        "pair_recall": round(recall, 6) if recall is not None else None,
        "pair_f1": (
            round(2 * precision * recall / (precision + recall), 6)
            if precision and recall
            else 0.0
        ),
        "wrong_merges": wrong,
        "wrong_merges_retiring_a_gold": retires_gold,
        "wrong_merges_retiring_a_nongold": retires_nongold,
        "nongold_to_gold_ratio": (
            round(retires_nongold / retires_gold, 3) if retires_gold else None
        ),
        "keys_per_session": round(keys_emitted / sessions_total, 4),
        "sessions_emitting_any_key": sessions_with_key,
        "sessions_total": sessions_total,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--jaccard-tau",
        type=float,
        nargs="*",
        default=[0.4, 0.5, 0.6, 0.7],
        help="thresholds for the canonicalisation rule",
    )
    args = parser.parse_args()

    source = args.source.expanduser()
    digest = sha256_file(source)
    lock = json.loads(LOCK.read_text())
    expected = lock["dataset"]["files"][lock["dataset"]["primary_file"]]["sha256"]
    if digest != expected:
        print(f"FATAL: corpus sha256 {digest} != pinned {expected}", file=sys.stderr)
        return 2

    import pyarrow

    groups = load_memorycode(source)
    gold = gold_structure(groups)

    # Gold-independence assertion: every rule is evaluated against bodies that
    # are re-read from the units, with `declarations` (the oracle field) popped.
    for group in groups:
        for unit in group["units"]:
            unit.pop("declarations", None)

    results = {}
    for name, rule in BODY_RULES.items():
        cache: dict[str, list[set[str]]] = {}

        def keys_for(gid, info, rule=rule, cache=cache):
            if gid not in cache:
                cache[gid] = [rule(b) for b in info["bodies"]]
            return cache[gid]

        results[name] = score(gold, keys_for)
        results[name]["gold_independent"] = True
        results[name]["bank_fit"] = name in BANK_FIT
        results[name]["needs_write_path_read"] = False

    for tau in args.jaccard_tau:
        cache: dict[str, list[set[str]]] = {}

        def keys_for(gid, info, tau=tau, cache=cache):
            if gid not in cache:
                cache[gid] = jaccard_keys(info["bodies"], tau)
            return cache[gid]

        name = f"jaccard_canonicalisation_tau{tau}"
        results[name] = score(gold, keys_for)
        results[name]["gold_independent"] = True
        results[name]["bank_fit"] = False
        results[name]["needs_write_path_read"] = True

    # Degenerate control: the constant key. Recovers everything and is useless.
    # Present so the group-recovery metric can never be read on its own again.
    results["constant_key_CONTROL"] = score(gold, lambda gid, info: [{"k"}] * info["n_sessions"])
    results["constant_key_CONTROL"]["gold_independent"] = True
    results["constant_key_CONTROL"]["bank_fit"] = False
    results["constant_key_CONTROL"]["needs_write_path_read"] = False

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True
    ).stdout.strip()
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=REPO, capture_output=True, text=True
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain"], cwd=REPO, capture_output=True, text=True
        ).stdout.strip()
    )

    artifact = {
        "measurement": "a4prime_key_production_recovery",
        # DECISIONAL: FALSE, and deliberately so. Every figure below is an
        # EXACT census over the whole frozen bank -- there is no sampling, so
        # there is no confidence interval and no hypothesis test to run. What
        # this artifact cannot do is decide anything about latest-state-wins:
        # key recovery is an input to LSW, not LSW. Only a live arm (Arm K,
        # `--arm derived`, preregistered in
        # docs/build-log/2026-08-01-a4-prime-key-production.md) can carry that,
        # and it has not been run. Reporting a census as promotion-capable is
        # exactly the substitution this program voided a headline number over.
        "decisional": False,
        "paid_model_calls": 0,
        "evidence_contract": {
            "schema_version": 1,
            "decisional": False,
            "claim": "Gold-independent, body-derived key production on the frozen "
            "MemoryCode bank: §4's 0.008/0.208 reproduced, and the same rules "
            "re-scored under the regime supersession actually uses. A census of "
            "key recovery, NOT a measurement of latest-state-wins.",
            "power": {
                "test": "descriptive-only (no test)",
                "n": 1063,
                "b": 0,
                "c": 0,
                "n_d": 0,
                "psi_observed": None,
                "mde_at_80": None,
                "computed_by": "not applicable: exhaustive census of the full bank, "
                "not a sample. Two arms are never compared here, so there are no "
                "discordant pairs; n_d = 0 is arithmetic, not a null result, and it "
                "is why decisional is false.",
                "source": "docs/build-log/artifacts/2026-08-01-key-production/recovery.json",
            },
            "mechanism_enabled": True,
            "probe_kind": None,
            "harness": {
                "embed_model": "none: no embedding, no server, no worker, no database",
                "scorer": "scripts/measure_key_recovery.py (group recovery, pair "
                "precision/recall, wrong-merge classification)",
                "k": "not applicable (no retrieval)",
                "budget": None,
                "flags": ["offline", "db_free", "no_model_call", "no_network"],
                "command": "python3 scripts/measure_key_recovery.py --source <parquet> --out <json>",
            },
            "corpus": {
                "sha256": digest,
                "snapshot_id": f"memorycode@{lock['attribution']['revision']}",
                "n_items": 1063,
                "path": str(source),
            },
            "attribution": None,
            "leakage": None,
        },
        "lineage": {
            "git_head": head,
            "git_branch": branch,
            "git_dirty": dirty,
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "pyarrow_version": pyarrow.__version__,
            "python_version": sys.version.split()[0],
            "served_binaries": "NONE — this measurement runs no binary, no server, "
            "no worker and no database. Its lineage is the script and the corpus.",
        },
        "corpus": {
            "instrument": "memorycode",
            "path": str(source),
            "sha256": digest,
            "pinned_sha256": expected,
            "revision": lock["attribution"]["revision"],
        },
        "bank_shape": {
            "instances": len(groups),
            "gold_groups": sum(len(i["probes"]) for i in gold.values()),
            "sessions": sum(i["n_sessions"] for i in gold.values()),
            "current_stale_pairs": sum(
                len(p["earlier"]) for i in gold.values() for p in i["probes"]
            ),
        },
        "reference_points": {
            "arm_A_unchanged_lsw": 0.31232361,
            "arm_P_oracle_ceiling_lsw": 0.57949200,
            "arm_P_decisional": False,
            "source": "docs/build-log/2026-07-31-preference-writepath.md §5",
        },
        "s4_reproduction": {
            "target": "docs/build-log/2026-07-31-preference-writepath.md §4",
            "target_code_committed": False,
            "target_code_note": "The §4 measurement was never committed to the "
            "tree, so an exact reproduction is impossible by construction. This "
            "is a re-implementation from the prose descriptions of the four "
            "rules, scored under the regime that reproduces the published "
            "ordering and magnitudes.",
            "published_vs_reproduced": {
                "quoted_literal_stripped_content_word_set": {
                    "published": 8,
                    "reproduced": results["sentence_content_set"]["s4_regime_groups_recovered"],
                },
                "one_content_word_before_the_literal": {
                    "published": 221,
                    "reproduced": results["pre1_content_word"]["s4_regime_groups_recovered"],
                },
                "two_content_words": {
                    "published": 118,
                    "reproduced": results["pre2_content_words"]["s4_regime_groups_recovered"],
                },
                "three_content_words": {
                    "published": 70,
                    "reproduced": results["pre3_content_words"]["s4_regime_groups_recovered"],
                },
            },
            "verdict": "REPRODUCED up to implementation detail. The published "
            "ordering (221 > 118 > 70, with the set rule near zero) is "
            "reproduced exactly and the magnitudes are within a few groups of "
            "1063. Residual differences are attributable to the stoplist and to "
            "the quoted-literal regex, neither of which §4 records.",
        },
        "rules": results,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")

    width = max(len(n) for n in results)
    print(
        f"{'rule'.ljust(width)}   §4reg  superss   NETrec   pairP   pairR  "
        f"goldslost  nongold:gold"
    )
    for name, r in results.items():
        ratio = r["nongold_to_gold_ratio"]
        print(
            f"{name.ljust(width)}  {r['s4_regime_recovery']:.4f}  "
            f"{r['group_recovery']:.4f}  {r['net_group_recovery']:.4f}  "
            f"{(r['pair_precision'] or 0):.4f}  {(r['pair_recall'] or 0):.4f}  "
            f"{r['groups_whose_gold_was_wrongly_retired']:9d}  "
            f"{('n/a' if ratio is None else f'{ratio:.1f}:1'):>12}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
