"""A4' key-production: the rules, and the arm that would serve them.

These tests exist because `docs/build-log/2026-07-31-preference-writepath.md`
§4's measurement was never committed, so the 0.008 / 0.208 figures the program's
critical path is calibrated on could not be re-derived by anyone. The rules now
live in `scripts/measure_key_recovery.py`; these pin their behaviour and, above
all, pin the property that makes the whole exercise decisional: **no rule may
read a gold-derived field.**
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import measure_key_recovery as kr  # noqa: E402
import external_instrument_adapter as adapter  # noqa: E402

DECLARATION = "Luis: Going forward, always start attribute names with 'j_'."
RESTATED = "Luis: I need you to start attribute names with 'q_' from now on."
UNRELATED = "Luis: Please terminate every function name with 'x_' instead."


def test_a_restated_convention_shares_a_derived_key() -> None:
    """The whole mechanism in one assertion: two sessions stating the same
    convention with different literals must collide on the derived key, or
    supersession can never fire between them."""
    for rule in (kr.rule_pre1, kr.rule_pre2, kr.rule_pre3):
        assert rule(DECLARATION) & rule(RESTATED), rule.__name__


def test_a_different_convention_does_not_share_a_derived_key() -> None:
    """The counterpart §4 never measured. A key that merges two different
    conventions retires a live rule, which is the failure being fixed."""
    assert not kr.rule_pre3(DECLARATION) & kr.rule_pre3(UNRELATED)


def test_the_naive_topic_regex_is_not_usable_on_prose() -> None:
    """`external_instrument_adapter.QUOTED` is correct on the clean `topic`
    field and catastrophic on a body: apostrophes make every contraction pair
    look like a quoted literal. This is why `measure_key_recovery.LITERAL`
    exists, and any future body-side quote extraction must use the bounded one."""
    prose = "Kiyotaka: I'm glad. Luis: Start attribute names with 'q_'."
    naive = adapter.QUOTED.findall(prose)
    bounded = [m.group(1) for m in kr.LITERAL.finditer(prose)]
    assert bounded == ["q_"]
    assert naive != ["q_"]


def test_every_rule_is_a_pure_function_of_the_body() -> None:
    """Gold-independence, asserted rather than promised. A rule is handed a
    string; there is no path from it to `type`/`topic`, which is exactly what
    makes Arm P `decisional: false` and these rules decisional."""
    for name, rule in kr.BODY_RULES.items():
        first = rule(DECLARATION)
        second = rule(DECLARATION)
        assert first == second, name
        assert all(isinstance(key, str) for key in first), name


def test_a_body_with_no_directive_yields_no_key() -> None:
    """A rule must abstain rather than invent. A session that states no
    convention has nothing to supersede and must fall through to the plain
    episode path in `ingest_group_derived`."""
    filler = "Luis: How was your weekend? Kiyotaka: Restful, thank you."
    for name, rule in kr.BODY_RULES.items():
        assert rule(filler) == set(), name


def test_the_derived_arm_deletes_the_oracle_field_before_keying() -> None:
    """Arm K is Arm P with one variable changed. If it could see
    `declarations` it would be Arm P with extra steps, and `decisional: true`
    would be a lie. This drives the real `ingest_group_derived` against a
    recording stub, so the assertion is on shipped code, not a copy of it."""

    class StubClient:
        def __init__(self) -> None:
            self.posted: list[dict] = []
            self.seen_declarations = False

        def bind_context(self, *_args, **_kwargs) -> dict:
            # Exactly the five server-bound fields `gate_runtime`'s strict
            # public retain helper requires; anything else fails closed there.
            return {
                "subject_id": "s",
                "scope_id": "sc",
                "actor_id": "a",
                "agent_node_id": "an",
                "subject_generation": 1,
            }

        def post(self, _path: str, payload: dict) -> dict:
            self.posted.append(payload)
            return {"episode_id": "e1", "unit_ids": ["u1"], "dedup": {"matched": False}}

    group = {
        "group_id": "mc-stub",
        "units": [
            {
                "unit_id": "mc-stub-s0",
                "source_kind": "user",
                "body": DECLARATION,
                "declarations": ["always start attribute names with <X>"],
            },
            {
                "unit_id": "mc-stub-s1",
                "source_kind": "user",
                "body": "Luis: Nothing to report today.",
                "declarations": [],
            },
        ],
        "probes": [],
    }
    args = type("Args", (), {"derived_rule": "pre3_content_words"})()
    client = StubClient()
    _context, identity, episodes, _deduped = adapter.ingest_group_derived(
        client, group, args
    )

    # The oracle field is gone from the units the arm touched.
    assert all("declarations" not in unit for unit in group["units"])
    # The directive-bearing session became a keyed preference unit...
    assert identity["mc-stub-s0"] == {"u1"}
    unit_payload = client.posted[0]["payload"]["unit"]
    assert unit_payload["kind"] == "preference"
    assert unit_payload["fact_key"].startswith("preference:")
    # ...and no part of the oracle key leaked into it.
    assert "attribute names" not in unit_payload["fact_key"]
    # The filler session fell through to the plain episode path.
    assert episodes == 1


def test_the_derived_arm_refuses_an_unknown_rule() -> None:
    args = type("Args", (), {"derived_rule": "no_such_rule"})()
    with pytest.raises(KeyError):
        adapter.ingest_group_derived(None, {"group_id": "x", "units": []}, args)
