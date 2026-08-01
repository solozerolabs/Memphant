"""Pin `required_n`'s argument ORDER, because transposing it fails silently.

`required_n(psi, delta)` takes the discordance rate FIRST and the effect
SECOND. Both are small floats in the same range, so a transposed call is
type-correct, returns a plausible integer, and answers a different question —
there is nothing to notice at the call site.

Two things go wrong when they are swapped, and this file pins both:

1. **Wrong answer.** The returned n is the sample size for a different
   (psi, delta) pair entirely.
2. **Apparent hang.** `required_n` returns None immediately when `delta > psi`,
   but walks upward to its 20,000 cap when `delta` is merely small. Measured on
   this tree: `required_n(0.02, 0.0938)` (correct order, delta > psi) returns
   None in 0.000s, while the transposed `required_n(0.0938, 0.02)` grinds for
   **23.5s** and returns 1920. In a sweep that reads as a stalled run.

Provenance: a lane reported this defect in `s4_controls_compare.py`. It was not
there — that script has always called `required_n(psi, PLANNING_MDE)`, in both
`6912e35f` and `aec0c98d`. The claim still cost review time twice, which is
reason enough for a test rather than a comment: an assertion cannot be wrong
about which file it guards.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from instrument_power import required_n  # noqa: E402


def test_signature_is_psi_then_delta():
    """The order is part of the contract, not an implementation detail."""
    import inspect

    names = list(inspect.signature(required_n).parameters)
    assert names[:2] == ["psi", "delta"], (
        f"required_n's first two parameters are {names[:2]}; callers across the "
        "repo pass (psi, delta) positionally, so reordering them silently "
        "changes every existing call site's meaning"
    )


def test_delta_greater_than_psi_returns_none_immediately():
    """An effect larger than the discordance rate is unreachable at any n.

    This is the honest-None case callers must branch on: None here means "no n
    reaches this", NOT "not computed" and NOT "zero".
    """
    assert required_n(0.02, 0.0938) is None


def test_a_reachable_pair_returns_an_int():
    n = required_n(0.40, 0.0938)
    assert isinstance(n, int) and n > 0


def test_transposing_the_arguments_changes_the_answer():
    """The regression this file exists for.

    Correct and transposed both "work" — that is the whole problem. They must
    not agree, or a swapped call would be undetectable.
    """
    correct = required_n(0.40, 0.0938)
    transposed = required_n(0.0938, 0.40)
    assert correct is not None
    assert transposed is None, (
        "with psi=0.0938 < delta=0.40 the transposed call is unreachable; if "
        "this ever returns an int, the two orders have become confusable"
    )


@pytest.mark.parametrize("psi", [0.0, -0.1])
def test_non_positive_psi_returns_none(psi):
    assert required_n(psi, 0.0938) is None


def test_non_positive_delta_returns_none():
    assert required_n(0.40, 0.0) is None
