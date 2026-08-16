"""One-command installer: idempotent repo + harness wiring."""
import importlib.util
import json
import os

_SPEC = importlib.util.spec_from_file_location(
    "memphant_install", os.path.join(os.path.dirname(__file__), "..", "plugins", "install.py")
)
inst = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(inst)


def test_agents_block_is_stable_and_idempotent(tmp_path):
    repo = str(tmp_path)
    assert inst.write_agents_block(repo) == "agents:written"
    first = (tmp_path / "AGENTS.md").read_text()
    assert inst.BEGIN if False else "memphant:begin" in first
    assert ".memphant/MEMORY.md" in first
    # Re-run changes nothing (byte-identical, status unchanged).
    assert inst.write_agents_block(repo) == "agents:unchanged"
    assert (tmp_path / "AGENTS.md").read_text() == first


def test_agents_block_preserves_user_text(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# Rules\n\n- stdlib only\n")
    inst.write_agents_block(str(tmp_path))
    text = (tmp_path / "AGENTS.md").read_text()
    assert text.startswith("# Rules\n\n- stdlib only\n")
    assert "<!-- memphant:begin -->" in text


def test_gitignore_added_once(tmp_path):
    repo = str(tmp_path)
    assert inst.gitignore_memphant(repo) == "gitignore:added"
    assert ".memphant/" in (tmp_path / ".gitignore").read_text()
    assert inst.gitignore_memphant(repo) == "gitignore:present"
    # Present in any form (with/without trailing slash) is respected.
    assert (tmp_path / ".gitignore").read_text().count(".memphant") == 1


def test_merge_hooks_registers_both_events_without_duplicates():
    merged = inst.merge_hooks({}, "/plug", inst._CODEX_HOOKS)
    assert set(merged["hooks"]) == {"UserPromptSubmit", "Stop"}
    # Idempotent: merging again does not duplicate.
    again = inst.merge_hooks(merged, "/plug", inst._CODEX_HOOKS)
    assert again == merged
    cmds = [h["command"] for g in again["hooks"]["Stop"] for h in g["hooks"]]
    assert cmds == ['python3 "/plug/codex-memphant/hooks/session_capture.py"']


def test_merge_hooks_preserves_a_users_own_hook():
    user = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}}
    merged = inst.merge_hooks(user, "/plug", inst._CODEX_HOOKS)
    stop_cmds = [h["command"] for g in merged["hooks"]["Stop"] for h in g["hooks"]]
    assert "echo mine" in stop_cmds and any("session_capture" in c for c in stop_cmds)


def test_register_hooks_writes_and_is_idempotent(tmp_path):
    cfg = tmp_path / "codex"
    cfg.mkdir()
    assert inst.register_hooks(str(cfg), "/plug", inst._CODEX_HOOKS) == "hooks:registered"
    data = json.load(open(cfg / "hooks.json"))
    assert set(data["hooks"]) == {"UserPromptSubmit", "Stop"}
    assert inst.register_hooks(str(cfg), "/plug", inst._CODEX_HOOKS) == "hooks:unchanged"


def test_register_hooks_no_config_dir(tmp_path):
    assert inst.register_hooks(str(tmp_path / "missing"), "/plug", inst._CODEX_HOOKS) == "hooks:no_config_dir"


def test_install_repo_only_with_harness_none(tmp_path):
    steps = inst.install(str(tmp_path), harness="none")
    assert steps == ["agents:written", "gitignore:added"]
    assert (tmp_path / "AGENTS.md").exists() and (tmp_path / ".gitignore").exists()


def test_install_explicit_harness_registers_when_present(tmp_path, monkeypatch):
    codex = tmp_path / ".codex"
    codex.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex))
    repo = tmp_path / "repo"
    repo.mkdir()
    steps = inst.install(str(repo), harness="codex")
    assert "codex:hooks:registered" in steps
    assert (codex / "hooks.json").exists()
