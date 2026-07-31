"""Pure-helper tests for the convo-lane extractor.

These cover the three decisions that decide whether the bank is leak-free at
all: the human-turn rule (provenance, not content shape), the wrapper/paste
guards, and the secret scan. Everything else in the extractor is I/O over a
frozen snapshot.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

_spec = importlib.util.spec_from_file_location(
    "convo_lane_extract", ROOT / "scripts" / "convo_lane_extract.py"
)
cle = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cle)


def _record(**overrides):
    base = {
        "type": "user",
        "origin": {"kind": "human"},
        "isSidechain": False,
        "message": {"content": "why is the frozen feed clamping the deadline?"},
    }
    base.update(overrides)
    return base


class TestHumanRule:
    def test_stamped_interactive_turn_is_human(self):
        assert cle._is_human_record(_record())

    def test_subagent_dispatch_prompt_is_not_human(self):
        """The trap class: a plain-string user record with no origin stamp is a
        model writing to a model. Any content-shape heuristic admits it."""
        assert not cle._is_human_record(
            _record(origin=None, message={"content": "You are the OSS REPOS team. Read..."})
        )

    def test_task_notification_is_not_human(self):
        assert not cle._is_human_record(_record(origin={"kind": "task-notification"}))

    def test_sidechain_turn_is_not_human(self):
        assert not cle._is_human_record(_record(isSidechain=True))

    def test_tool_result_carrier_is_not_human(self):
        assert not cle._is_human_record(_record(toolUseResult={"stdout": ""}))

    def test_skill_load_meta_is_not_human(self):
        assert not cle._is_human_record(_record(isMeta=True))


class TestWrappers:
    def test_system_reminder_prefix_is_stripped_and_turn_survives(self):
        text = (
            "<system-reminder>\nThe git worktree was recycled.\n</system-reminder>\n\n"
            "Is it fixed now by another session?"
        )
        assert cle.strip_wrappers(text) == "Is it fixed now by another session?"

    def test_wrapper_only_turn_reduces_to_nothing(self):
        assert cle.strip_wrappers("<task-notification>x</task-notification>") == ""

    def test_paste_marker_is_removed(self):
        assert "Pasted text" not in cle.strip_wrappers("[Pasted text #3 +412 lines] look at this")

    def test_long_pasted_block_trips_the_paste_guard(self):
        assert cle.has_paste_run("\n".join(f"line {i}" for i in range(25)))

    def test_ordinary_multiline_turn_does_not(self):
        assert not cle.has_paste_run("first thought\n\nsecond thought\n\nthird thought")


class TestSecretScan:
    def test_detects_by_family_and_never_returns_content(self):
        found = cle.secret_reasons("export GITHUB_TOKEN=ghp_" + "a1B2c3D4e5" * 4)
        assert "github_token" in found
        assert all(isinstance(name, str) and " " not in name for name in found)

    def test_detects_credentials_in_a_uri(self):
        assert "uri_credentials" in cle.secret_reasons("postgres://user:hunter2xyz@host/db")

    def test_git_sha_is_not_a_secret(self):
        assert cle.secret_reasons("commit 9937bf9581ac253853904832f1eb3cec923d6b4d0011223344") == []

    def test_ordinary_engineering_prose_is_clean(self):
        assert cle.secret_reasons("the BM25 b parameter is 0.75 in memphant-core/src/lib.rs") == []


class TestSelectionHelpers:
    def test_artifacts_finds_paths_and_identifiers(self):
        found = cle.artifacts("check pack_render_cap in scripts/track_r_mine.py")
        assert "scripts/track_r_mine.py" in found
        assert "pack_render_cap" in found

    def test_skeleton_erases_identifiers_so_templates_collapse(self):
        one = cle.skeleton("why does pack_render_cap drop chunks in packed_render?")
        two = cle.skeleton("why does admit_or_drop drop chunks in sibling_gather?")
        assert one == two

    def test_jaccard_is_symmetric_and_bounded(self):
        a, b = {"x", "y"}, {"y", "z"}
        assert cle.jaccard(a, b) == cle.jaccard(b, a)
        assert 0.0 <= cle.jaccard(a, b) <= 1.0
        assert cle.jaccard(set(), b) == 0.0
