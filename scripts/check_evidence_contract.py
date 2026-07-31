#!/usr/bin/env python3
"""One checker for the evidence contract every promotion-capable artifact carries.

Nine measurement failures were committed to this repo in a single week. Every one
of them was already written down somewhere as a rule. Prose did not stop any of
them, so this file is the enforcement: each guard below fails closed, exits
non-zero, and has a test in ``tests/test_check_evidence_contract.py`` that proves
it rejects the real historical artifact it was built for.

The nine, and where each is enforced:

1. Underpowered runs reported as nulls -- ``_guard_power`` (n_d < 6 => the
   two-sided exact McNemar test has NO rejection region, so the "null" was never
   a measurement).
2. Power asserted, not computed -- ``_guard_power`` recomputes ``mde_at_80``
   through ``scripts/instrument_power.py`` and rejects a mismatch.
3. Leakage collapsed to one number -- ``_guard_leakage`` (five fields required,
   and absolute coverage may not be compared across unit definitions).
4. Bars preregistered below the achievable floor -- ``_guard_bar`` against
   ``benchmarks/manifests/leakage_floor_reference.json``.
5. A probe that ran with the mechanism switched off -- ``_guard_mechanism``.
6. Harness settings not recorded beside scores -- schema-required ``harness``.
7. Corpora that mutate under their own lock -- ``_guard_corpus`` (snapshot
   identity, not a live path; sha recomputed when the corpus is in-repo).
8. Instruments trusted from cards and READMEs -- ``_guard_instrument``.
9. ``--lib`` instead of ``--workspace``, and base-relative attribution --
   ``check_ci_workflow`` and the ``attribution`` block.

Design constraints: stdlib only (CI has no ``jsonschema``), no DB, no model call,
no network. The required/type/enum rules live in
``benchmarks/manifests/evidence_contract.schema.json`` and are read from there --
one schema, not a validator per lane. Only the cross-field rules a JSON Schema
cannot express live in Python.

Usage:
    python3 scripts/check_evidence_contract.py            # registry + scan + CI lint
    python3 scripts/check_evidence_contract.py --file X   # validate one artifact
    python3 scripts/check_evidence_contract.py --report   # rewrite the retrofit report
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import instrument_power as ip  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "benchmarks/manifests/evidence_contract.schema.json"
FLOORS_PATH = ROOT / "benchmarks/manifests/leakage_floor_reference.json"
REGISTRY_PATH = ROOT / "benchmarks/manifests/evidence_contract_registry.json"
REPORT_PATH = ROOT / "docs/build-log/artifacts/evidence-contract-retrofit.json"
CI_WORKFLOW = ROOT / ".github/workflows/ci.yml"
ARTIFACT_ROOT = "docs/build-log/artifacts"

UNVERIFIED = "unverified"

# The structural floor of the two-sided exact (conditional binomial) McNemar
# test. Derived, not chosen: at n_d = 5 the most extreme split 5/0 has two-sided
# p = 2 * 0.5**5 = 0.0625 > 0.05, so no outcome rejects. Asserted here and
# proved against instrument_power.exact_binom_reject in the test suite.
MIN_DECISIONAL_ND = 6

# A JSON artifact under docs/build-log/artifacts/ carrying any of these keys at
# nesting depth <= 4 is treated as promotion-capable: it says something passed,
# failed, differed, or was decided. Deliberately broad and deliberately
# mechanical -- the ratchet in `scan_unregistered` fails on any NEW match that
# is neither contracted nor recorded as retrofit debt.
DECISION_KEYS = frozenset(
    {
        "verdict",
        "bar",
        "bars",
        "bar_passed",
        "bars_passed",
        "decision",
        "gate_passed",
        "p_value",
        "mcnemar",
        "promotion",
        "conclusion",
        "delta",
        "mde",
        "power",
    }
)
SCAN_MAX_DEPTH = 4


class Violation(str):
    """A guard failure. Plain string; the type exists to make intent readable."""


# ---------------------------------------------------------------------------
# minimal schema validation (stdlib only)
# ---------------------------------------------------------------------------

_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "null": type(None),
}


def _type_ok(value, spec) -> bool:
    names = spec if isinstance(spec, list) else [spec]
    for name in names:
        expected = _TYPES[name]
        if name in ("number", "integer") and isinstance(value, bool):
            continue  # bool is an int in Python; it is not a number here
        if isinstance(value, expected):
            return True
    return False


def validate_against_schema(node, schema: dict, path: str = "evidence_contract") -> list[Violation]:
    """Validate the subset of JSON Schema the contract actually uses.

    required / type / enum / const / minLength / minimum / pattern / properties /
    items / additionalProperties=false. Anything richer belongs in a guard, not
    in the schema, because a rule nobody can read is a rule nobody obeys.
    """
    out: list[Violation] = []

    if "const" in schema and node != schema["const"]:
        out.append(Violation(f"{path}: expected const {schema['const']!r}, got {node!r}"))
        return out
    if "type" in schema and not _type_ok(node, schema["type"]):
        out.append(Violation(f"{path}: expected type {schema['type']}, got {type(node).__name__}"))
        return out
    if "enum" in schema and node not in schema["enum"]:
        out.append(Violation(f"{path}: {node!r} is not one of {schema['enum']}"))
        return out
    if isinstance(node, str):
        if "minLength" in schema and len(node) < schema["minLength"]:
            out.append(Violation(f"{path}: string shorter than minLength {schema['minLength']}"))
        pattern = schema.get("pattern")
        if pattern and not re.match(pattern, node):
            out.append(Violation(f"{path}: {node!r} does not match {pattern}"))
    if isinstance(node, (int, float)) and not isinstance(node, bool):
        if "minimum" in schema and node < schema["minimum"]:
            out.append(Violation(f"{path}: {node} below minimum {schema['minimum']}"))

    if isinstance(node, dict):
        for key in schema.get("required", []):
            if key not in node:
                out.append(Violation(f"{path}.{key}: REQUIRED field missing (fail closed)"))
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for key, value in node.items():
            if key in props:
                out.extend(validate_against_schema(value, props[key], f"{path}.{key}"))
            elif extra is False:
                out.append(Violation(f"{path}.{key}: unknown field (schema is closed here)"))
            elif isinstance(extra, dict):
                out.extend(validate_against_schema(value, extra, f"{path}.{key}"))
    elif isinstance(node, list) and "items" in schema:
        for i, value in enumerate(node):
            out.extend(validate_against_schema(value, schema["items"], f"{path}[{i}]"))
    return out


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _is_num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _unverified_paths(node, path: str = "evidence_contract") -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            found.extend(_unverified_paths(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found.extend(_unverified_paths(value, f"{path}[{i}]"))
    elif node == UNVERIFIED:
        found.append(path)
    return found


# ---------------------------------------------------------------------------
# guards
# ---------------------------------------------------------------------------


def _guard_power(contract: dict, decisional: bool) -> list[Violation]:
    """Failures 1 and 2.

    1. Two-sided exact McNemar has no rejection region below n_d = 6: fewer than
       six discordant pairs means zero power at ANY effect size, so a "null" from
       such a run was never a measurement. This is what the n_d = 2 abstention
       screen was, and every b = c = 0 cited as "no difference".
    2. "~80% power at psi~=0.15" was asserted. Recomputed it is 0.728 there and
       0.541 at the psi that lane exhibits. So mde_at_80 is recomputed here from
       the artifact's own cells and a mismatch is a violation.
    """
    out: list[Violation] = []
    power = contract.get("power")
    if not isinstance(power, dict):
        return [Violation("evidence_contract.power: missing (fail closed)")]

    n, b, c, n_d = power.get("n"), power.get("b"), power.get("c"), power.get("n_d")

    if isinstance(n_d, int) and not isinstance(n_d, bool):
        if isinstance(b, int) and isinstance(c, int) and n_d != b + c:
            out.append(Violation(f"power.n_d={n_d} does not equal b+c={b + c}"))
        if n_d < MIN_DECISIONAL_ND:
            if decisional:
                out.append(
                    Violation(
                        f"power.n_d={n_d} < {MIN_DECISIONAL_ND} but decisional=true. "
                        "The two-sided exact McNemar test has NO rejection region below "
                        f"n_d={MIN_DECISIONAL_ND}: zero power at any effect size. This run "
                        "cannot carry a decision, and its 'null' was never a measurement."
                    )
                )
    elif n_d != UNVERIFIED:
        out.append(Violation(f"power.n_d must be an integer or {UNVERIFIED!r}, got {n_d!r}"))

    if power.get("test") == "descriptive-only (no test)" and decisional:
        out.append(Violation("power.test is descriptive-only but decisional=true: no test, no decision"))

    psi = power.get("psi_observed")
    if _is_num(psi) and isinstance(n, int) and isinstance(n_d, int) and n > 0:
        if abs(psi - n_d / n) > 1e-6:
            out.append(Violation(f"power.psi_observed={psi} != n_d/n={n_d / n:.6f}"))

    mde = power.get("mde_at_80")
    if decisional and not _is_num(mde):
        out.append(
            Violation(
                "power.mde_at_80: a decisional artifact must carry a COMPUTED MDE "
                "(scripts/instrument_power.py), not an asserted power note"
            )
        )
    if _is_num(mde):
        if not (isinstance(n, int) and isinstance(n_d, int) and n > 0):
            out.append(Violation("power.mde_at_80 given but n/n_d are not integers, so it cannot be recomputed"))
        else:
            recomputed = ip.min_detectable_effect(n, n_d / n)
            if recomputed is None:
                out.append(
                    Violation(
                        f"power.mde_at_80={mde} asserted, but at n={n}, psi={n_d / n:.4f} even a "
                        "unanimous discordant split misses 80% power: this lane is UNPOWERABLE"
                    )
                )
            elif abs(recomputed - mde) > 1e-3:
                out.append(
                    Violation(
                        f"power.mde_at_80={mde} does not match the recomputed "
                        f"{recomputed:.6f} at n={n}, psi={n_d / n:.6f}"
                    )
                )
    return out


def _guard_mechanism(contract: dict, decisional: bool) -> list[Violation]:
    """Failure 5: a probe that ran with the mechanism switched off.

    The 276/276 "measured-permanent" packing verdict was taken on the
    cross-rerank arm, where ``rank_based_ordering_active`` disables the very
    contest under test. Same family: the abstention gate that made zero model
    calls, so nothing could ever set ``abstained``. This is the failure that most
    looks like a result, so it is a hard failure whenever the artifact reports a
    null or claims to be decisional.
    """
    out: list[Violation] = []
    probe_kind = contract.get("probe_kind")
    enabled = contract.get("mechanism_enabled")
    if probe_kind in (None, ""):
        return out
    if "mechanism_enabled" not in contract:
        out.append(
            Violation(
                f"mechanism_enabled: REQUIRED when probe_kind={probe_kind!r} -- "
                "was the mechanism under test actually ON in the configuration measured?"
            )
        )
        return out
    if enabled is False:
        power = contract.get("power") or {}
        b, c, n_d = power.get("b"), power.get("c"), power.get("n_d")
        reports_null = (isinstance(b, int) and isinstance(c, int) and b == c) or n_d == 0
        if decisional or reports_null:
            out.append(
                Violation(
                    f"mechanism_enabled=false on a {probe_kind} probe that reports a null "
                    "(or claims to be decisional). The mechanism under test was OFF in the "
                    "configuration measured, so no outcome could have been produced by it. "
                    "This is not a result."
                )
            )
    if enabled is True and not contract.get("mechanism_evidence"):
        out.append(
            Violation(
                "mechanism_evidence: REQUIRED when mechanism_enabled=true -- name the "
                "config key and its observed value, do not assert the mechanism was on"
            )
        )
    return out


def _guard_leakage(contract: dict, decisional: bool) -> list[Violation]:
    """Failure 3: leakage collapsed to one number.

    Five fields, always. Absolute coverage is not portable across unit
    definitions -- the same bank reads 0.3367/1.4991x under 'user turn + agent
    reply' and 0.1871/1.3657x under 'user turn only'. And the metric conflates
    contamination (query authored FROM the target: a fact, settled by provenance)
    with lexical tractability (real, but then the bank only measures the lexical
    regime). ONLY contamination disqualifies.
    """
    out: list[Violation] = []
    leak = contract.get("leakage")
    if isinstance(leak, dict):
        if leak.get("provenance_class") == "authored_from_target" and decisional:
            out.append(
                Violation(
                    "leakage.provenance_class=authored_from_target but decisional=true: the "
                    "query could have been written from the target, so the effect is "
                    "unfalsifiable as a memory gain. Contamination disqualifies; lexical "
                    "tractability alone does not."
                )
            )

    comparisons = contract.get("leakage_comparisons") or []
    units = {entry.get("unit_definition") for entry in comparisons if isinstance(entry, dict)}
    if len(units) > 1:
        out.append(
            Violation(
                "leakage_comparisons: absolute target coverage compared across "
                f"{len(units)} DIFFERENT unit definitions {sorted(units)!r}. Absolute "
                "coverage is not portable across unit definitions; this comparison is void."
            )
        )
    return out


def _guard_bar(contract: dict, floors: dict) -> list[Violation]:
    """Failure 4: bars preregistered below the achievable floor -- three times.

    Human coding queries sit at 0.175-0.287 absolute / 1.76-2.03x; our own floor
    probe returned 1.79; we preregistered <=1.50. A published human corpus then
    failed our gate at 2.42x. A bar a real human corpus cannot pass is measuring
    our metric, not our data.
    """
    out: list[Violation] = []
    bar = contract.get("bar")
    if not isinstance(bar, dict):
        return out

    by_id = {entry["id"]: entry for entry in floors["floors"]}
    ref = bar.get("floor_reference")
    if ref not in by_id:
        return [
            Violation(
                f"bar.floor_reference={ref!r} is not a recorded floor in "
                f"benchmarks/manifests/leakage_floor_reference.json (have: {sorted(by_id)}). "
                "A bar with no measured floor behind it is the failure this guard exists for."
            )
        ]
    floor = by_id[ref]

    field = {
        "leakage_concentration": "concentration",
        "leakage_absolute_target_coverage": "absolute_target_coverage",
    }[bar["metric"]]
    floor_value = floor.get(field)
    if not _is_num(floor_value):
        return [Violation(f"bar.floor_reference={ref!r} records no {field} to calibrate against")]

    if bar.get("direction") == "le" and bar["threshold"] < floor_value:
        out.append(
            Violation(
                f"bar {bar['metric']} <= {bar['threshold']} sits BELOW the recorded "
                f"achievable floor {floor_value} ({ref}). A bar below the floor asks the bank "
                "to be less lexically pointed than real human queries are, which no answerable "
                "question set can be."
            )
        )

    # A bar a real human corpus cannot pass is measuring our metric, not our
    # data. swe-prbench -- published, human-authored -- failed our own <= 2.05x
    # gate at 2.42x, and 2.05 clears the 1.76 floor, so the floor check alone
    # would have let it through.
    if bar.get("direction") == "le":
        for row in floor.get("rows", []):
            observed = row.get(field)
            if _is_num(observed) and observed > bar["threshold"]:
                out.append(
                    Violation(
                        f"bar {bar['metric']} <= {bar['threshold']} is failed by a recorded human "
                        f"corpus: {row['instrument']} measures {observed}. A bar a real human "
                        "corpus cannot pass is measuring our metric, not our data."
                    )
                )

    leak = contract.get("leakage")
    if isinstance(leak, dict):
        want, got = floor.get("negative_selection"), leak.get("negative_selection")
        if want and got and want != got:
            out.append(
                Violation(
                    f"bar cites floor {ref!r} measured with negative_selection={want!r} but the "
                    f"run used {got!r}. Concentration moves ~1.8x with negative selection; the "
                    "comparison is not valid."
                )
            )
    return out


def _guard_corpus(contract: dict) -> list[Violation]:
    """Failure 7: corpora that mutate under their own lock.

    Track U broke mid-run when a concurrent session wrote a ``feedback_*`` file
    and the pinned corpus went 90 -> 91. A live path is not an identity; a
    snapshot id plus a content sha is.
    """
    out: list[Violation] = []
    corpus = contract.get("corpus")
    if not isinstance(corpus, dict):
        return [Violation("evidence_contract.corpus: missing (fail closed)")]

    snapshot = corpus.get("snapshot_id", "")
    if isinstance(snapshot, str) and (
        snapshot.startswith("/") or snapshot.startswith("~") or ".." in snapshot
    ):
        out.append(
            Violation(
                f"corpus.snapshot_id={snapshot!r} is a live filesystem path, not a snapshot "
                "identity. A live directory can mutate under its own lock mid-run."
            )
        )

    sha = corpus.get("sha256")
    rel = corpus.get("path")
    if isinstance(rel, str) and isinstance(sha, str) and sha != UNVERIFIED:
        target = ROOT / rel
        if target.is_file():
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
            if actual != sha:
                out.append(
                    Violation(
                        f"corpus.sha256={sha} does not match the current content of {rel} "
                        f"({actual}). The corpus mutated after it was pinned."
                    )
                )
    return out


def _guard_instrument(contract: dict, decisional: bool) -> list[Violation]:
    """Failure 8: instruments trusted from cards and READMEs.

    SWE-Explore ships 0/848 issue texts. ClawArena's MIT is a shields.io badge.
    swe-prbench ships 3.3% bot comments while claiming zero. STATE-Bench's
    adapter 400s on every call and would have billed $211-634 for zero rows.
    """
    out: list[Violation] = []
    iv = contract.get("instrument_verification")
    if not isinstance(iv, dict):
        return out

    if decisional and iv.get("shipped_rows_verified") is not True:
        out.append(
            Violation(
                "instrument_verification.shipped_rows_verified is not true on a decisional "
                "artifact: the rows were never counted on disk"
            )
        )
    fields = iv.get("fields_counted")
    if not isinstance(fields, dict) or not fields:
        out.append(
            Violation(
                "instrument_verification.fields_counted must name at least one field and the "
                "rows on which it is actually populated -- this is what catches 0/848"
            )
        )
    else:
        for name, count in fields.items():
            if count == 0:
                out.append(
                    Violation(
                        f"instrument_verification.fields_counted[{name!r}]=0: the instrument "
                        "ships zero populated rows for this field. There is nothing to measure."
                    )
                )
    if iv.get("license_source") == UNVERIFIED and decisional:
        out.append(Violation("instrument_verification.license_source is unverified on a decisional artifact"))
    return out


# ---------------------------------------------------------------------------
# top-level contract check
# ---------------------------------------------------------------------------


def check_contract(contract, *, schema: dict | None = None, floors: dict | None = None) -> list[Violation]:
    """Validate one ``evidence_contract`` block. Empty list means it passes."""
    schema = schema if schema is not None else load_json(SCHEMA_PATH)
    floors = floors if floors is not None else load_json(FLOORS_PATH)

    if not isinstance(contract, dict):
        return [Violation("evidence_contract: missing or not an object (fail closed)")]

    out = validate_against_schema(contract, schema)
    decisional = contract.get("decisional") is True

    unverified = _unverified_paths(contract)
    if unverified and decisional:
        out.append(
            Violation(
                f"decisional=true with {len(unverified)} unverified field(s) {unverified!r}. "
                "An unverified field is never a pass-by-default; mark the artifact "
                "decisional=false instead of backfilling a number nobody measured."
            )
        )

    out.extend(_guard_power(contract, decisional))
    out.extend(_guard_mechanism(contract, decisional))
    out.extend(_guard_leakage(contract, decisional))
    out.extend(_guard_bar(contract, floors))
    out.extend(_guard_corpus(contract))
    out.extend(_guard_instrument(contract, decisional))
    return out


def check_artifact(path: Path, **kw) -> list[Violation]:
    if not path.is_file():
        return [Violation(f"{path}: registered artifact does not exist (fail closed)")]
    try:
        doc = load_json(path)
    except json.JSONDecodeError as exc:
        return [Violation(f"{path}: not valid JSON ({exc})")]
    if not isinstance(doc, dict) or "evidence_contract" not in doc:
        return [
            Violation(
                f"{path}: no `evidence_contract` block. A promotion-capable artifact without a "
                "contract is the default-pass this checker exists to remove."
            )
        ]
    return check_contract(doc["evidence_contract"], **kw)


# ---------------------------------------------------------------------------
# discovery, ratchet, retrofit report
# ---------------------------------------------------------------------------


def tracked_artifact_json(root: Path = ROOT) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "--", ARTIFACT_ROOT],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\n")
    return sorted(p for p in out if p.endswith(".json"))


def _keys_within(node, depth: int = 0):
    if depth > SCAN_MAX_DEPTH:
        return
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _keys_within(value, depth + 1)
    elif isinstance(node, list):
        for value in node[:5]:
            yield from _keys_within(value, depth + 1)


def decisional_candidates(root: Path = ROOT) -> list[tuple[str, list[str]]]:
    """Artifacts that say something passed, failed, differed, or was decided."""
    found: list[tuple[str, list[str]]] = []
    for rel in tracked_artifact_json(root):
        try:
            doc = json.loads((root / rel).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            continue
        hits = DECISION_KEYS & set(_keys_within(doc))
        if hits:
            found.append((rel, sorted(hits)))
    return found


def build_report(root: Path = ROOT) -> dict:
    schema, floors = load_json(SCHEMA_PATH), load_json(FLOORS_PATH)
    registry = load_json(REGISTRY_PATH)
    contracted = set(registry["contracted"])
    pending = {entry["path"]: entry["reason"] for entry in registry["pending"]}

    rows, unverified_total = [], 0
    for rel, hits in decisional_candidates(root):
        path = root / rel
        doc = json.loads(path.read_text(encoding="utf-8"))
        contract = doc.get("evidence_contract") if isinstance(doc, dict) else None
        if contract is None:
            rows.append(
                {
                    "artifact": rel,
                    "state": "contracted" if rel in contracted else ("pending" if rel in pending else "UNREGISTERED"),
                    "decision_keys": hits,
                    "violations": [
                        "no `evidence_contract` block: power/leakage/harness/mechanism_enabled/"
                        "corpus/instrument_verification are all absent and cannot be inferred"
                    ],
                    "reason": pending.get(rel, ""),
                }
            )
            continue
        violations = check_contract(contract, schema=schema, floors=floors)
        unverified_total += len(_unverified_paths(contract))
        rows.append(
            {
                "artifact": rel,
                "state": "contracted" if rel in contracted else "pending",
                "decision_keys": hits,
                "violations": list(violations),
                "unverified_fields": _unverified_paths(contract),
            }
        )

    failing = [r for r in rows if r["violations"]]
    return {
        "schema_version": 1,
        "generated_by": "scripts/check_evidence_contract.py --report",
        "method": (
            "Every tracked JSON under docs/build-log/artifacts/ carrying a decision key at "
            "depth <= 4 is a promotion-capable candidate. NOTHING here is backfilled: a field "
            "that cannot be verified from a banked artifact is left absent and reported as a "
            "violation rather than invented."
        ),
        "candidates": len(rows),
        "contracted": len(contracted),
        "pending_retrofit": len(pending),
        "failing": len(failing),
        "unverified_fields_total": unverified_total,
        "artifacts": rows,
    }


def scan_unregistered(root: Path = ROOT) -> list[Violation]:
    """The ratchet. A new decisional artifact must be contracted or declared debt."""
    registry = load_json(REGISTRY_PATH)
    known = set(registry["contracted"]) | {entry["path"] for entry in registry["pending"]}
    out = []
    for rel, hits in decisional_candidates(root):
        if rel not in known:
            out.append(
                Violation(
                    f"{rel}: promotion-capable artifact (decision keys {hits}) is neither in "
                    "`contracted` nor recorded as retrofit debt in "
                    "benchmarks/manifests/evidence_contract_registry.json"
                )
            )
    for entry in registry["pending"]:
        path = root / entry["path"]
        if not path.is_file():
            out.append(Violation(f"{entry['path']}: pending retrofit entry names a file that does not exist"))
    return out


# ---------------------------------------------------------------------------
# failure 9: --lib instead of --workspace
# ---------------------------------------------------------------------------


def check_ci_workflow(path: Path = CI_WORKFLOW) -> list[Violation]:
    """`-p memphant-core --lib` runs 137 tests and excludes all 30 files in
    memphant-core/tests/. That is how a packing regression shipped. The Rust
    floor in CI must be workspace-wide, and no CI test invocation may narrow to
    a single crate's lib target.
    """
    out: list[Violation] = []
    if not path.is_file():
        return [Violation(f"{path}: CI workflow missing (fail closed)")]
    text = path.read_text(encoding="utf-8")
    if not re.search(r"cargo test\s+--workspace", text):
        out.append(
            Violation(
                f"{path.name}: no `cargo test --workspace` floor. Without it the Rust gate can "
                "silently narrow, and `--lib` alone excludes every tests/ integration file."
            )
        )
    for line in text.splitlines():
        stripped = line.strip()
        if "cargo test" in stripped and "--lib" in stripped:
            out.append(Violation(f"{path.name}: `--lib` narrows a CI test invocation: {stripped!r}"))
    return out


# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", type=Path, help="validate one artifact and exit")
    parser.add_argument("--report", action="store_true", help="rewrite the retrofit report")
    args = parser.parse_args()

    if args.file:
        violations = check_artifact(args.file)
        for v in violations:
            print(f"FAIL {args.file}: {v}", file=sys.stderr)
        return 1 if violations else 0

    if args.report:
        report = build_report()
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(
            f"wrote={REPORT_PATH} candidates={report['candidates']} "
            f"failing={report['failing']} unverified_fields={report['unverified_fields_total']}"
        )
        return 0

    schema, floors = load_json(SCHEMA_PATH), load_json(FLOORS_PATH)
    registry = load_json(REGISTRY_PATH)
    failures: list[str] = []

    for rel in registry["contracted"]:
        for v in check_artifact(ROOT / rel, schema=schema, floors=floors):
            failures.append(f"{rel}: {v}")
    failures.extend(scan_unregistered())
    failures.extend(check_ci_workflow())

    current = REPORT_PATH.read_text(encoding="utf-8") if REPORT_PATH.exists() else ""
    expected = json.dumps(build_report(), indent=2) + "\n"
    if current != expected:
        failures.append(
            f"{REPORT_PATH.relative_to(ROOT)} is stale -- rerun "
            "`python3 scripts/check_evidence_contract.py --report`"
        )

    for f in failures:
        print(f"FAIL {f}", file=sys.stderr)
    if failures:
        print(f"evidence_contract_failures={len(failures)}", file=sys.stderr)
        return 1
    print(
        f"evidence_contract_ok contracted={len(registry['contracted'])} "
        f"pending_retrofit={len(registry['pending'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
