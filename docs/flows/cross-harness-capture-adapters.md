# Cross-harness memory CAPTURE — write seam + adapters

Flow lane: `flow`. Feature: cross-harness capture adapters. Base commit `bc800e29`
(`feat(capture): anti-poisoning trust-ladder cross-check engine`, fast-forwarded to
`main`). Plan of record: `docs/superpowers/plans/2026-08-15-cross-harness-capture-plan.md`.

Write-side counterpart to the shipped INJECTION adapters (`plugins/`, one shared recall
core). Stage A built the ENGINE (`run_capture_crosscheck`, `compute_capture_crosscheck`)
that reads `payload.capture` markers and ladders captured units on reflect. This stage
supplies (1) the SERVICE WRITE SEAM that actually SETS the marker from an HTTP caller and
(2) the per-harness capture ADAPTERS that feed it — mirroring the injection surface's
shared-core + thin-adapter shape.

## Spec

### Outcome

1. **Write seam (Rust, zero new schema/verb).** An HTTP caller can write a captured unit
   tagged `source=mirror|summary` that lands as a `Belief` `candidate` carrying
   `payload.capture = {source, ladder:Captured, witnesses:[]}` at `AgentOutput` trust, so
   the existing `run_capture_crosscheck` processes it on reflect.
2. **Adapters (Python + TS).** One shared capture core (parse last turn → exclusion
   filters → cheap-model summarize → POST through the seam) behind thin per-harness
   adapters: session-summarize on all four harnesses; file-mirror on the three where it is
   native (Claude Code, opencode, pi). Codex is summarize-only.

### Write-seam decision — ride `retain`, not `remember` (option b)

Two options were on the table. **Chosen: a capture-provenance convention on a `retain`
episode that the reflect nominator detects and tags** (option b). Rejected: an optional
`capture_source` on `RememberRequest` (option a).

Justification:
- **Keeps `remember`'s pure-Active contract completely untouched.** Option (a) would branch
  `remember` to mint a `candidate` `Belief` instead of its Active compact — exactly the
  contract muddying the decision guidance warns against. Option (b) leaves `remember`
  byte-for-byte unchanged.
- **Only option (b) flows `service verb → compile_job → run_capture_crosscheck`
  end-to-end.** `retain` (Episode) enqueues a reflect job; the worker's `compile_job`
  compiles it and calls `run_capture_crosscheck` at its tail. `remember` stages units
  synchronously and enqueues NO reflect job — a capture minted via `remember` would sit at
  `candidate` forever unless an unrelated scope reflect happened to run (a liveness gap).
  The plan's architecture diagram ("async reflect job (existing worker) → cross-check")
  is the `retain` path.
- **Reuses the `compact` threading template.** `ReflectCandidate` already carries an
  optional typed `payload` marker (`compact: Option<CompactEnvelope>`) that `minted_unit`
  copies onto the staged unit. Adding `capture: Option<CaptureMarker>` mirrors it exactly:
  one serde-default field + one line in `minted_unit`.
- Zero new MCP/service verb, zero migration.

**The convention.** A capture episode is a `retain` Episode with
`source_kind = "agent"` (AgentOutput trust floor) and `source_ref` of the reserved form
`capture://mirror` or `capture://summary`. Its `subject` is the captured subject key (so a
mirror and a summary for one subject share a `fact_key` and the cross-check can pair them);
its `body` is the captured content. In `compile_job`'s `ReflectEpisode` arm, a
`capture_episode_source` detector parses that `source_ref`; when present the arm builds ONE
`Belief` candidate (`kind=Belief`, `fact_key=subject`, `trust=AgentOutput`,
`confidence=1.0`, `capture=Some(CaptureMarker::captured(source))`) and skips the normal
episodic / fact-extraction / structured candidates. The low-trust append arm then lands it
as `Belief`/`Candidate`/`AgentOutput` with `payload.capture` — identical to how the Stage A
tests seed via `stage_memory_unit`.

### Adapter design (mirrors the injection surface)

- **One shared capture core** parallels `plugins/_shared/memphant_recall.py` as
  `plugins/_shared/memphant_capture.py`: parse the transcript's LAST TURN only (strip
  tool-call / tool-result / thinking / system), apply mandatory exclusion filters
  (**secret regex-redact BEFORE anything else**, drop assistant echoes of the user, drop
  phatic/filler, length-gate trivial turns, skip subagent sessions, skip repo-recoverable
  facts), summarize via a cheap-model shell-out (`MEMPHANT_CAPTURE_SUMMARIZER_CMD`,
  injectable/stubbed for tests), then POST as a `retain` capture episode tagged
  `source=summary`. A `build_capture` CLI entrypoint lets the TS adapters shell out (same
  DRY split as recall: Python hooks import; TS shell out).
- **Session-summarize adapters (spine, all 4):** Codex `Stop`, Claude Code `Stop`/
  `SessionEnd`, opencode `session.idle`/`session.completed`, pi `turn_end`/`agent_end`.
- **File-mirror adapters (augment, 3/4):** Claude Code `PreToolUse` (`Write|Edit|
  MultiEdit`), opencode `tool.execute.before`, pi `tool_call` — detect writes to memory
  files (`MEMORY.md`, `AGENTS.md`, a configurable set) and POST the content tagged
  `source=mirror`. **ALLOW-AND-COPY** — never block the host write. Codex gets NO
  file-mirror (its hooks cannot see `apply_patch`); documented.

### Non-goals

No new migration/column, no new MCP/service verb (rides `retain`/`reflect` + the Stage A
engine), no blocking of host file writes, no live LLM in any test path, no second
extractor, no byte-offset tailer (last-turn read at session end), no Codex file-mirror.
KISS/DRY: one shared capture core; TS shells out to its CLI.

### Trade-off priority

Accuracy (never store a secret; never resurrect a forgotten identity; respect every trust
gate) > cost (cheap single-pass summarizer, free file-mirror) > speed (async, invisible).

## Plan

1. **Types** (`crates/memphant-types/src/lib.rs`): add serde-default
   `capture: Option<CaptureMarker>` to `ReflectCandidate` (mirrors `compact`).
2. **Admission threading** (`crates/memphant-core/src/lib.rs`): in `minted_unit`, carry
   `capture: candidate.capture.clone()` (was hard-coded `None`).
3. **Capture nominator** (`crates/memphant-core/src/service.rs`): a
   `capture_episode_source(source_ref) -> Option<CaptureSource>` parser; in `compile_job`'s
   `ReflectEpisode` arm, when it matches, replace the candidate set with the single captured
   `Belief` candidate and skip episodic/fact/structured nomination.
4. **Integration test** (`crates/memphant-core/tests/capture_write_seam.rs`): drive
   `retain` (mirror) + `retain` (summary) for one subject through `run_worker_tick_scoped`
   (real `compile_job` → `run_capture_crosscheck`); AGREE → promoted to
   `corroborated`/Active and recallable; DIVERGE → both quarantined and recall-excluded.
   Plus gate regressions: trust floor, idempotency (identical re-POST dedups), preference
   source-kind gate, no-resurrection (forgotten source not re-derived), capture stays below
   the high-risk trust floor.
5. **Shared capture core** (`plugins/_shared/memphant_capture.py`, stdlib only): last-turn
   parse, exclusion filters, injectable summarizer + injectable poster, `build_capture`
   library fn + CLI. Never logs secrets/bodies; always exits 0.
6. **Python adapters:** `plugins/codex-memphant/hooks/session_capture.py` (Stop),
   `plugins/claude-code-memphant/hooks/capture_session.py` (Stop/SessionEnd) +
   `.../capture_file_mirror.py` (PreToolUse), each a thin envelope over the core; register
   in the respective `hooks.json`.
7. **TS adapters:** extend `plugins/opencode-memphant/index.ts` (session.idle +
   tool.execute.before) and `plugins/pi-memphant/index.ts` (turn_end/agent_end + tool_call)
   to shell out to `build_capture`.
8. **Tests:** shared-core stdlib tests (`tests/test_shared_capture.py`); Python adapter
   stdlib tests (`tests/test_codex_capture.py`, `tests/test_claude_code_capture.py`); TS
   node-stdlib smoke tests (`plugins/opencode-memphant/capture.test.ts`,
   `plugins/pi-memphant/capture.test.ts`) with a stubbed capture CLI.
9. Regenerate `openapi/memphant.v1.json` + `mcp/memphant.tools.v1.json` if the added
   `ReflectCandidate.capture` field changes them (via the server/mcp binaries, never
   hand-edited).

## Harness

```sh
cd /Users/sidsharma/Memphant/.claude/worktrees/agent-a7fcf8c5db415c49a && cargo fmt --check
cd /Users/sidsharma/Memphant/.claude/worktrees/agent-a7fcf8c5db415c49a && cargo clippy --workspace --all-targets --all-features -- -D warnings
cd /Users/sidsharma/Memphant/.claude/worktrees/agent-a7fcf8c5db415c49a && cargo test --workspace --all-targets --all-features
cd /Users/sidsharma/Memphant/.claude/worktrees/agent-a7fcf8c5db415c49a && cargo test -p memphant-core --test capture_write_seam --all-features
cd /Users/sidsharma/Memphant/.claude/worktrees/agent-a7fcf8c5db415c49a && cargo test -p memphant-core --test capture_crosscheck --all-features
cd /Users/sidsharma/Memphant/.claude/worktrees/agent-a7fcf8c5db415c49a && python3 -m pytest tests/test_shared_capture.py tests/test_codex_capture.py tests/test_claude_code_capture.py -q
cd /Users/sidsharma/Memphant/.claude/worktrees/agent-a7fcf8c5db415c49a && node --test plugins/opencode-memphant/capture.test.ts
cd /Users/sidsharma/Memphant/.claude/worktrees/agent-a7fcf8c5db415c49a && node --test plugins/pi-memphant/capture.test.ts
```
