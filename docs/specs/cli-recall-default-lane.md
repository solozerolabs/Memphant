# Spec: `memphant recall` default lane

Status: ACCEPTED — implemented on this branch.
North star: coding-agent UX. Priorities: accuracy > cost > speed; KISS/DRY.

## Decision

A bare `memphant recall --query "..."` (no lane flags) must return the coding
agent's own freshly-captured memory AND its own plainly-retained facts, without
the agent having to remember to pass `--compact-only --include-beliefs`.

The CLI default becomes a **union (coding) lane**: it serves the general lane's
live facts (`Active`/`Validated`, any kind subject to `include_beliefs`) **plus**
captured `Candidate` units (labelled `captured_unconfirmed` → rendered
`[unconfirmed]`). The general lane's anti-poison guarantee — non-CLI consumers
never see `Candidate` units — is preserved unchanged.

## Background (verified against the code)

`recallable()` (crates/memphant-core/src/lib.rs) has, until now, keyed two
behaviors off a single `compact_only` bool:

1. **Card restriction** — `compact_only && unit.compact.is_none() &&
   unit.capture.is_none() → hide`. Only typed compact envelopes
   (`remember`/`correct_memory` cards) and captures are eligible; a raw
   episode/resource body copied into an `Active` unit is excluded. Guarded by an
   anti-pattern test (crates/memphant-core/tests/surface_mutations.rs) and relied
   on by the MCP recall tool (crates/memphant-mcp/src/lib.rs, `compact_only:
   true`) and the MEMORY.md projection (plugins/_shared/memphant_projection.py).
2. **Candidate serving** — `captured_candidate = compact_only && capture.is_some()
   && state == Candidate`. Only the coding lane serves an unconfirmed capture; the
   general lane keeps `Candidate` invisible (anti-poison).

The bug: the two behaviors were welded to the same bool, so the only way to see
captures via the CLI was to also opt into the card restriction (`--compact-only`)
— and a bare `memphant recall` (`compact_only=false`) silently used the general
lane and MISSED every freshly-captured Candidate. The AGENTS.snippet told agents
to pass `--compact-only --include-beliefs` to compensate; a dogfood run showed
agents reasoning on their own omit advertised flags. (`include_beliefs` is
already decoupled: captures mint `Semantic`/`Procedural`, never `Belief`.)

## Options considered

**(a) Default the card lane (`compact_only=true`) + `--general` opt-out.**
Rejected. The card restriction hides any unit without a compact/capture marker.
A plain `memphant retain --body "..."` produces an episode that reflects into an
`Active Semantic` unit with **neither** marker, so `retain` → bare `recall` would
return EMPTY: the agent stores a fact and cannot read it back with the same tool.
This is the worst possible surprise for read-your-own-writes, and it would force
rewriting the round-trip integration tests to bless the broken behavior. The
card restriction is the right precision filter for the MCP/projection (curated
MEMORY.md cards), but wrong as the CLI's read-your-own-writes default.

**(b) Union / coding lane as the CLI default. CHOSEN.**
Decouple "serve captured Candidates" from "restrict to typed cards." The union
lane = the general lane's serving (no card restriction) PLUS captured Candidates.
Bare `memphant retain` → bare `memphant recall` returns the retained fact (union
drops the card restriction, so the round-trip tests pass UNCHANGED), and a
freshly-captured Candidate is also served, labelled `[unconfirmed]`. Anti-poison
is preserved because the union is an explicit opt-in signal that non-CLI general
consumers never send.

**(c) Status quo + a better AGENTS.snippet.** Rejected. It leaves the bare command
wrong and leans on the agent following a documented flag pattern — the exact
failure mode dogfooding already exposed. The bare command should Just Work.

## Design

Introduce one orthogonal request signal, `serve_captures` (bool), on
`RecallRequest` (internal) and `RecallHttpRequest` (wire), both `#[serde(default)]`
= false. It is independent of `compact_only`:

| Lane | `compact_only` | `serve_captures` | Card restriction | Serves Candidates | Consumer |
|------|:---:|:---:|:---:|:---:|---|
| General | false | false | no | **no** (anti-poison) | evals, non-CLI general consumers, `memphant recall --general` |
| Union (coding) | false | true | no | **yes** ([unconfirmed]) | **bare `memphant recall`** |
| Card | true | (implied) | yes | yes ([unconfirmed]) | MCP recall, projection, `memphant recall --compact-only` |

Inside `recallable()`: `compact_only` **implies** capture serving
(`let serve_captures = serve_captures || compact_only;`), so the MCP/projection
literals (`compact_only: true`) are unchanged and no caller must set both. The
card restriction stays gated on `compact_only` ALONE. `captured_candidate` and
the procedural `Active`-vs-`Validated` state gate key off the derived
`serve_captures`.

CLI flag mapping (crates/memphant-cli/src/main.rs, `recall`):
- bare (no flags) → `serve_captures=true`, `compact_only=false` → **union**.
- `--compact-only` → `compact_only=true` (card lane; unchanged, still advertised).
- `--general` → `serve_captures=false`, `compact_only=false` → general lane
  (anti-poison parity, testing, non-coding reads).

Capture labelling (`inclusion_reason = captured_unconfirmed`) is already
lane-independent, so the union lane renders `[unconfirmed]` with no extra change.

MCP recall and the projection deliberately STAY on the card lane: they are
curated-card surfaces (a compressed MEMORY.md, a high-precision programmatic
read), not the agent's raw read-your-own-writes tool. This asymmetry is
intentional, not an oversight.

## Acceptance criteria

1. `recallable()` unit coverage: with `serve_captures=true, compact_only=false`, a
   captured `Candidate` (Semantic) IS recallable and a plain `Active` Semantic unit
   IS recallable; with `serve_captures=false, compact_only=false` (general) the
   captured `Candidate` is NOT recallable (anti-poison) while the plain `Active`
   unit still is; with `compact_only=true` a marker-less `Active` unit is NOT
   recallable (card restriction intact).
2. Round-trip (crates/memphant-cli/tests/http_verbs.rs): plain `retain` → `reflect`
   → bare `recall` still returns the retained body (no test rewrite), and bare
   `recall` after `forget` is empty.
3. New integration coverage: a capture episode (`--source-ref capture://summary`)
   → `reflect` → bare `recall` returns it with `inclusion_reason` containing
   `captured_unconfirmed`; the same recall with `--general` returns it NOT.
4. Anti-pattern guard (crates/memphant-core/tests/surface_mutations.rs) stays green:
   `compact_only=true` never serves a raw episode.
5. Projection contract (tests/test_projection.py) stays green: the projection keeps
   `compact_only: true`.
6. OpenAPI snapshot regenerated to include `serve_captures` on `RecallHttpRequest`.
7. Full green: `cargo fmt --check`, `cargo clippy --workspace --all-targets
   --all-features -D warnings`, `cargo test --workspace`, `python3 -m pytest tests/ -q`.
8. AGENTS.snippet / SKILL.md: bare `memphant recall --query "..."` is the documented
   default; `--compact-only`/`--general` are noted as options, not requirements.
