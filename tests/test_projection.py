"""Behavioral tests for the AGENTS.md projection renderer + CLI (stdlib, no sockets)."""

import importlib.util
import sys
from pathlib import Path

_SHARED = Path(__file__).resolve().parents[1] / "plugins" / "_shared"
sys.path.insert(0, str(_SHARED))
_spec = importlib.util.spec_from_file_location("memphant_projection", _SHARED / "memphant_projection.py")
proj = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(proj)


def _recall(n_extra=0):
    items = [
        {"unit_id": "u-proc", "kind": "procedural", "inclusion_reason": "fused_top_k", "body": "When cron drifts, set TZ=UTC in the container."},
        {"unit_id": "u-fact", "kind": "semantic", "inclusion_reason": "captured_unconfirmed:coding_lane", "body": "The Finn DB is a shared co-tenant; scope changes to memphant.*"},
        {"unit_id": "u-pref", "kind": "preference", "inclusion_reason": "fused_top_k", "body": "Sid prefers accuracy over cost over speed."},
        {"unit_id": "u-bel", "kind": "belief", "inclusion_reason": "fused_top_k", "body": "Voyage rerank-2.5 is still the latest hosted reranker."},
    ]
    for i in range(n_extra):
        items.append({"unit_id": f"u-x{i:03d}", "kind": "semantic", "inclusion_reason": "fused_top_k", "body": f"Extra fact {i} " + ("lorem ipsum dolor sit amet " * 12)})
    return {"items": items, "trace_id": "t"}


def test_render_groups_labels_and_is_byte_stable(tmp_path):
    cwd = str(tmp_path)
    proj.render_projection(cwd, _recall())
    memory = (tmp_path / ".memphant" / "MEMORY.md").read_text()
    agents = (tmp_path / "AGENTS.md").read_text()
    assert memory.index("## Procedures") < memory.index("## Facts") < memory.index("## Preferences")
    assert "- [unconfirmed]" in memory and "Finn DB" in memory
    assert "- [confirmed]" in memory and "TZ=UTC" in memory
    assert agents.startswith(proj.BEGIN_MARKER) and agents.rstrip().endswith(proj.END_MARKER)
    assert "prefer retrieval-led reasoning over pre-training-led reasoning" in agents
    assert "| procedural | yes | .memphant/MEMORY.md#" in agents
    assert "| semantic | no | .memphant/MEMORY.md#" in agents
    # Rerender with equal (but reordered) inputs ⇒ byte-identical.
    shuffled = _recall()
    shuffled["items"].reverse()
    proj.render_projection(cwd, shuffled)
    assert (tmp_path / ".memphant" / "MEMORY.md").read_text() == memory
    assert (tmp_path / "AGENTS.md").read_text() == agents


def test_managed_block_preserves_text_outside_markers(tmp_path):
    agents = tmp_path / "AGENTS.md"
    agents.write_text("# Working in gateway\n\n- keep it stdlib only\n\n<!-- memphant:begin -->\nstale\n<!-- memphant:end -->\n\n## After\ntrailing text\n")
    proj.render_projection(str(tmp_path), _recall())
    text = agents.read_text()
    assert text.startswith("# Working in gateway\n\n- keep it stdlib only\n\n<!-- memphant:begin -->")
    assert text.endswith("<!-- memphant:end -->\n\n## After\ntrailing text\n")
    assert "stale" not in text
    assert text.count(proj.BEGIN_MARKER) == 1
    # Absent AGENTS.md without markers but with content ⇒ appended block.
    other = tmp_path / "other"
    other.mkdir()
    (other / "AGENTS.md").write_text("# Rules\n")
    proj.render_projection(str(other), _recall())
    assert (other / "AGENTS.md").read_text().startswith("# Rules\n\n<!-- memphant:begin -->")


def test_size_caps_hold(tmp_path):
    proj.render_projection(str(tmp_path), _recall(n_extra=80))
    memory = (tmp_path / ".memphant" / "MEMORY.md").read_bytes()
    agents = (tmp_path / "AGENTS.md").read_text()
    assert len(memory) <= proj.MAX_MEMORY_BYTES
    block = agents[agents.index(proj.BEGIN_MARKER): agents.index(proj.END_MARKER) + len(proj.END_MARKER)]
    assert len(block.encode()) <= proj.MAX_INDEX_BYTES
    # Cap dropping never breaks a line mid-way: every item line is whole.
    for line in memory.decode().splitlines():
        if line.startswith("- "):
            assert line.startswith("- [confirmed]") or line.startswith("- [unconfirmed]")


def test_empty_recall_still_renders_valid_files(tmp_path):
    proj.render_projection(str(tmp_path), {"items": []})
    assert (tmp_path / ".memphant" / "MEMORY.md").exists()
    agents = (tmp_path / "AGENTS.md").read_text()
    assert proj.BEGIN_MARKER in agents and "MEMORY.md" in agents


def test_project_uses_injected_fetch_with_repo_query(tmp_path, monkeypatch):
    monkeypatch.setattr(proj, "repo_slug", lambda cwd: "myrepo")
    seen = []

    def fetch(query):
        seen.append(query)
        return _recall()

    assert proj.project(str(tmp_path), fetch=fetch) == "projected"
    assert seen == ["myrepo gotchas conventions contracts procedures"]
    assert "TZ=UTC" in (tmp_path / ".memphant" / "MEMORY.md").read_text()


def test_project_is_fail_safe(tmp_path, monkeypatch):
    def boom(_q):
        raise RuntimeError("socket")

    assert proj.project(str(tmp_path), fetch=boom) == "projection_error"
    for env in ("MEMPHANT_CAPTURE_URL", "MEMPHANT_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    assert proj.project(str(tmp_path)) == "unconfigured"
    assert proj.recall_url_from_capture_url("http://h/v1/episodes") == "http://h/v1/recall"


def test_http_recall_fetch_request_shape(monkeypatch):
    import json
    seen = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"items": []}'

    def fake_urlopen(request, timeout=None):
        seen["body"] = json.loads(request.data.decode())
        seen["headers"] = dict(request.header_items())
        return _Resp()

    monkeypatch.setattr(proj.urllib.request, "urlopen", fake_urlopen)
    identity = {"subject_id": "s", "scope_id": "sc", "actor_id": "a", "agent_node_id": "n", "subject_generation": 1}
    out = proj.http_recall_fetch("http://h/v1/recall", "k", identity)("q")
    assert out == {"items": []}
    body = seen["body"]
    assert body["query"] == "q" and body["limit"] == 20 and body["budget_tokens"] == 4096 and body["include_beliefs"] is True
    assert "compact_only" not in body  # general lane
    assert seen["headers"]["Authorization"] == "Bearer k"
