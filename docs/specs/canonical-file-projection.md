# Canonical file projection — usage map and boundary

Status: adopted. Verdict: the two "file projection" implementations are **not**
duplicates and are **not** a bug. They are two different products that happen to
emit a file named `MEMORY.md`. This spec pins the boundary so a future editor
cannot mistake them for the store-divergence anti-pattern and "unify" them —
which would silently destroy either the measured coding wins or the write-back
contract.

## The two implementations

### (A) Recall delivery — `plugins/_shared/memphant_projection.py`

- **Purpose:** always-in-context DELIVERY. Render prior-session memory into a
  file every coding agent reads before it derives. This is the `file` delivery
  surface that produced the measured coding wins in this project.
- **Source:** `POST /v1/recall` — a *ranked, bounded* retrieval on the **coding
  lane** (`compact_only=true` + `include_beliefs=true`, `limit=20`,
  `budget_tokens=4096`, query `"<repo_slug> gotchas conventions contracts
  procedures"`).
- **Kinds accepted:** `procedural` → **Procedures**; `semantic | belief |
  episodic` → **Facts**; `preference` → **Preferences** (see `_GROUPS`).
- **Candidate/unconfirmed units:** SERVED, labelled `[unconfirmed]` (the coding
  lane's whole point — a just-captured memory must be visible the same session).
  Confirmed units are labelled `[confirmed]`.
- **Output:** `<cwd>/.memphant/MEMORY.md` (grouped, one headed block per unit
  carrying its FULL body, bounded to 8 KB by dropping WHOLE tail items) plus a
  STABLE one-line pointer block in `AGENTS.md`/`CLAUDE.md`/`GEMINI.md`.
- **Direction:** READ-ONLY. Never writes back to canonical memory.
- **Live callers:**
  - `scripts/battery/run_battery.sh` — the `file` arm (invokes the CLI before the run).
  - `plugins/codex-memphant/hooks/session_capture.py` — `project()`.
  - `plugins/claude-code-memphant/hooks/capture_session.py` — `project()`.
  - `plugins/_shared/memphant_capture.py` — post-capture projection refresh.
  - `plugins/install.py` — imports the stable-block helpers.

### (B) Canonical projection — `crates/memphant-cli/src/file_plane.rs` (`memphant compile` / `memphant sync`) and `crates/memphant-mcp/src/file_memory.rs` (Anthropic `memory_20250818` tool)

- **Purpose:** a read-WRITE editing round-trip over the *full canonical scope*.
  `compile` renders a deterministic editable tree; the human/agent edits it;
  `sync --apply` writes corrections/forgets/retains back to canonical memory.
  The MCP memory tool is the same backend behind a `/memories` filesystem
  (`crates/memphant-mcp/src/lib.rs` documents it as "the same canonical
  projection and atomic file-sync path as `memphant compile`").
- **Source:** `GET /v1/scopes/{id}/projection` — the `canonical_projection`, the
  complete deterministic fingerprinted scope, gated at the STORE to
  `(Semantic, Active|Validated) | (Procedural, Validated)`
  (`in_memory_canonical_projection_units` in `crates/memphant-core/src/lib.rs`,
  and the SQL twin). **No Candidates. No Belief / Episodic / Preference.**
- **Kinds accepted:** `Semantic | Procedural` only. `file_plane.rs`
  re-asserts this defensively (`render_projection`, the
  `!matches!(item.kind, Semantic | Procedural)` guard) so a non-conforming
  server response fails loudly instead of mis-rendering.
- **Output:** `--out <DIR>` containing `MEMORY.md` (a table), `units/*.md` (one
  editable body + provenance footer each), `inbox/`, and
  `memphant-export.json` (a digest-bound manifest).
- **Direction:** READ-WRITE. `sync --apply` POSTs `/v1/file-sync`.
- **Live callers:** `crates/memphant-cli/src/main.rs` (`compile`/`sync` verbs);
  `crates/memphant-mcp/src/lib.rs` (`handle_memory_command`).

## Why the kind-acceptance differs (this is correct, not drift)

The difference is a direct, necessary consequence of the two SOURCES and two
DIRECTIONS — not a setting that drifted:

- (A) is a **read-only display** of *recalled* memory. A belief, an episode, a
  preference, or an unconfirmed Candidate can all be usefully *shown* to the
  agent. Showing them is the value.
- (B) is an **editable canonical round-trip**. A `file-sync` operation
  (`Correct` / `Forget` / `Retain`) acts on a `fact_key`'d, editable canonical
  unit. Only `Semantic`/`Procedural` `Active`/`Validated` units are editable
  canonical state; you cannot "correct" an observed episode or an unconfirmed
  Candidate through a unit file. Admitting those kinds into (B) would be the
  bug.

So aligning the two on kinds would break one of them. They must NOT be aligned.

### Not the filter the earlier audit cited

The `!matches!(unit.kind, Semantic | Procedural)` at
`crates/memphant-core/src/structured_state.rs` ~L797 is **unrelated** to file
projection. It lives in `active_structured_state`, a structured-state key/value
helper. The real canonical-projection kind gate is in
`in_memory_canonical_projection_units` (+ SQL twin) in
`crates/memphant-core/src/lib.rs`, with a defense-in-depth re-check in
`file_plane.rs`.

## Decision

**Keep both. Do not delete or merge either.** Each backs a distinct live path;
deleting (A) breaks the coding delivery surface, deleting (B) breaks the
`compile`/`sync` CLI and the MCP memory tool. There is no shared logic to DRY —
the renderers produce different formats for different consumers from different
sources.

Reconciliation is **documentation + guardrails**, not code consolidation:
each implementation carries a short cross-reference to this spec at its
kind-selection site, so the boundary cannot be silently erased by an editor who
assumes the two `MEMORY.md` renderers are redundant.

## Acceptance criteria

1. **(A) keeps serving the wide, unconfirmed set.** `procedural`,
   `semantic`, `belief`, `preference` all render, and a `captured_unconfirmed`
   unit renders labelled `[unconfirmed]`. Anchored by
   `tests/test_projection.py::test_render_groups_labels_and_is_byte_stable`
   (fixture exercises all four kinds + an unconfirmed unit).
2. **(B) keeps excluding everything but editable canonical units.** The
   canonical projection admits only `(Semantic, Active|Validated) |
   (Procedural, Validated)`. Anchored by
   `crates/memphant-core/src/service.rs::canonical_projection_store_excludes_historical_and_disallowed_units`
   and `..._respects_resolved_kind_policy`.
3. **The boundary is discoverable from the code.** (A)'s `_GROUPS` site and
   (B)'s canonical-projection kind gate + `file_plane.rs` guard each reference
   this spec.
4. **All green:** `cargo fmt --check`, `cargo clippy --workspace --all-targets
   --all-features -D warnings`, `cargo test --workspace`, `python3 -m pytest
   tests/ -q`.
