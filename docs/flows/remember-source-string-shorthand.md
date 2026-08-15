# Flow: remember source string shorthand

## Intent

The five coding-agent MCP mutation tools (`remember`, `correct_memory`,
`invalidate_memory`) require `source` to be a JSON object
`{kind, ref, observed_at, ...}`. A live Codex dogfood showed an agent's first
natural `remember` call passing `source` as a bare string and failing because an
object was required — real tool-use friction. Accept a **string shorthand** for
`source` in addition to the object form, with no compat shim.

## Spec

`MemorySourceInput` (`crates/memphant-types/src/lib.rs`) accepts EITHER shape on
the wire:

- **Object** — unchanged strict contract: `kind`, `ref`, `observed_at` required,
  optional `episode_id` XOR `resource_id`, `additionalProperties: false`.
- **String** — a bare JSON string is shorthand for `ref`. It maps to:
  - `ref` = the string,
  - `kind` = `"agent"` (the default),
  - `observed_at` = `""` (the empty sentinel; the service stamps the clock),
  - `episode_id`/`resource_id` = `None`.

**Default `kind` = `"agent"`.** Justification: the provenance of a bare-string
`remember` is the coding agent itself. `"agent"` maps through
`actor_kind_trust` to `AgentOutput` — the correct non-elevated trust floor (it
does not claim `user`/`system`/`tool`/`web` authority). It also correctly makes
a shorthand `remember` of a `Preference` fail the existing
`source.kind ∈ {user, correction}` gate, since a standing user constraint must
never be minted from an anonymous string.

**`observed_at` now-default lives at the SERVICE layer, not in `Deserialize`.**
`observed_at` is a required `String` and the deserializer has no clock. The
string form yields an empty-string sentinel; the service replaces a blank
`source.observed_at` with `clock.now_rfc3339()` — matching how the service
already stamps transaction time — before validating/canonicalizing. A non-blank
value must still be canonical UTC RFC3339. This applies uniformly to
`remember`, `correct_memory`, and `invalidate_memory`.

**Schema.** The generated MCP tool schema advertises both forms: the
`MemorySourceInput` definition becomes
`oneOf: [ {type: string, …}, {<the current object schema>} ]`. Regenerate
`mcp/memphant.tools.v1.json` via its owner. `openapi/memphant.v1.json` is
unaffected (it does not reference `MemorySourceInput`).

## Plan

1. **Types (`crates/memphant-types/src/lib.rs`)**
   - Add `pub const MEMORY_SOURCE_DEFAULT_KIND: &str = "agent";`.
   - Keep `MemorySourceInput` as the struct used throughout the code (derive
     `Debug, Clone, PartialEq, Eq, Serialize`), but replace the derived
     `Deserialize`/`JsonSchema` with hand-written impls:
     - A private `MemorySourceObject` shadow struct (`Deserialize, JsonSchema`,
       `deny_unknown_fields`) holds the object shape — the single source of
       truth for the strict object contract, preserving `deny_unknown_fields`.
     - Custom `Deserialize`: read a `serde_json::Value`; a `String` becomes the
       shorthand (default kind, empty `observed_at` sentinel), anything else
       deserializes through `MemorySourceObject` (so unknown fields still
       error). Value-based branch keeps strictness that `#[serde(untagged)]`
       would silently drop.
     - Custom `JsonSchema`: `oneOf: [ string, MemorySourceObject schema ]`.
   - Unit tests: string parses to shorthand (kind `agent`, ref set, empty
     `observed_at`); object parses fully; unknown object field still errors;
     schema exposes the string `oneOf` branch.
2. **Service (`crates/memphant-core/src/service.rs`)**
   - Add `fn resolve_source_observed_at(value, clock) -> Result<String>`: blank
     → `clock.now_rfc3339()`, else `canonical_utc_timestamp`.
   - In `remember`, `correct_memory`, `invalidate_memory`: drop
     `source.observed_at` from the blank-field guards and replace the direct
     `canonical_utc_timestamp(&request.source.observed_at, …)` with
     `resolve_source_observed_at(&request.source.observed_at, self.clock.as_ref())`.
3. **Behavioral test (`crates/memphant-core/tests/compact_remember.rs`)**
   - Deserialize a `RememberRequest` whose `source` is a bare JSON string, call
     `service.remember`, and assert the stored unit has `source_kind ==
     Some("agent")` and `observed_at == clock.now` (FixedClock), proving the
     end-to-end string path + service clock stamp.
   - Assert the object form still stores its explicit `observed_at`.
4. **Artifact regen**: `cargo run -q -p memphant-mcp -- --list-tools-json`
   owns `mcp/memphant.tools.v1.json`; write its output back (never hand-edit).

## Harness

```sh
cd /Users/sidsharma/Memphant/.claude/worktrees/agent-a9bf7a35c7474f7d4 && cargo test -p memphant-types memory_source -- --nocapture
cd /Users/sidsharma/Memphant/.claude/worktrees/agent-a9bf7a35c7474f7d4 && cargo test -p memphant-core --test compact_remember
cd /Users/sidsharma/Memphant/.claude/worktrees/agent-a9bf7a35c7474f7d4 && cargo test -p memphant-mcp --test mcp_schema_contract
cd /Users/sidsharma/Memphant/.claude/worktrees/agent-a9bf7a35c7474f7d4 && cargo fmt --check
cd /Users/sidsharma/Memphant/.claude/worktrees/agent-a9bf7a35c7474f7d4 && cargo clippy --all-targets --all-features -- -D warnings
cd /Users/sidsharma/Memphant/.claude/worktrees/agent-a9bf7a35c7474f7d4 && cargo test --workspace --all-targets
```
