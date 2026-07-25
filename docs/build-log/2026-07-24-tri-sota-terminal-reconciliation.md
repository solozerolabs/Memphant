# Tri-SOTA terminal reconciliation and clean-landing manifest

Date: 2026-07-24. Program base:
`a21479e3f558fe83188bbf06c2144c11326d19b5`. This document records terminal
states; it does not claim overall SOTA, production cutover, deployment, CI, or
launch.

## Requirement matrix

| Area | Terminal state | Named evidence or remaining predicate |
|---|---|---|
| A0 | proof-missing, non-blocking | Real-binary Deep E2E leg absent; Deep is diagnostic. |
| A1 | complete, kill-switch fired | `2026-07-21-a1-fast-miss-classification.md`; zero depth-bound Fast misses. |
| A2/A3/A4, D1, D3, Deep | deferred | A1 binds; reopening needs a new frozen depth-bound bank and explicit paid authorization. |
| B1 | rejected/deleted | `2026-07-22-b1-observation-block.md`. |
| B2/B3/B6 | locally complete | Existing B2/B3 proofs and local CI-honesty legs; remote CI unproven. |
| B4 receipts/status/policy | complete locally | `2026-07-23-b4-verified-receipts.md`; verified receipts fail closed and calibrated policy is deterministic. |
| Rung 7 packing | all tested techniques rejected | Cap 1200 plus local rerank stayed at 8/8 scored retrieval and 1/4 abstention. A decision-aware screen stopped at 5/12 calls when 8/8 supported became impossible; $0.114075625 settled. Default remains off. |
| B5 cleanup | complete/rejected as adjudicated | `2026-07-23-b5-recall-stage-retirement.md`; heuristic, learned wrapper, decomposition and Balanced deleted; cross-encoder seam remains default-off. |
| C0/C1 | correctness complete; parity/live L0 open | Existing C0/C1 proofs; no new production corpus authorization. |
| C2 | rejected/deleted | Free kill-gate remains binding. |
| C3 public volume | volume/isolation complete; candidate stress bank rejected; valid adversarial continuity blocked | `2026-07-24-c3-public-code-lane.md`; 64,055 events, action-repetition lexical R@10 18/40. A clean issue-text to auto-selected late-diagnostic probe scored 0/40, but its targets were not causally identified and its non-target events were not adjudicated distractors. The probe rejects the bank, not MemPhant continuity. A valid causal-paraphrase bank, reader, validator-backed tasks, and live production ingestion remain authorization-blocked/open. |
| WS-F active-read mechanism | local integration complete; dogfood gate open | Private companion evidence passes retain/compile/verified receipt/loader/trace/isolation/auth paths. Feature is not an enabled production cutover. Exact private landing identity stays in the private repository. |
| D2 ForgetEval | deterministic lineage evidence complete; not SOTA | Sleep-inspired exact-hash offline consolidation completed all 321 supported transitions and scored 244 pass / 15 fail / 126 N/A, with 111 gains and zero baseline regressions. Thirteen residuals are official substring conflicts and two are genuine multi-unit conflicts. |
| D4 SWE-Explore | immutable-input external block | The pinned JSONL omits issue text/base commits. Official code includes a reconstruction helper, but its auxiliary datasets are unpinned, required issue-map/trajectory inputs are absent, and mappings are incomplete for pro/multilingual cells. |
| D4 SWE Context Bench | first-tranche gate rejected | Twelve authorized Codex calls completed. The official evaluator resolved the first 3/3 no-memory baselines, making the required +2 related-arm gain impossible (maximum +1); nine patches were intentionally ungraded and the remaining 24 calls did not run. |
| WS-G/public launch/restraint/GateMem | open | Local mechanisms and synthetic fixtures do not close reopened launch predicates. |
| Rungs 5/6/8/10/11/13/15 | open/rejected exactly as STATUS records | No new real promotion evidence in this program. Rung 9 remains rejected/deleted. |
| Public/private spec mirror | complete in the authorized worktrees | `check_spec_drift.py` passes when `MEMPHANT_PRIVATE_SPEC_DIR` names the Syndai mirror. Its default sibling lookup skips in this linked-worktree topology, so the final gate must set that boundary explicitly. |

## Landing dependency graph

Never merge the program branch wholesale: it inherits substantial P1 history.
Build a new branch from the actual reviewed landing base and cherry-pick logical
groups in this order, resolving each group against that base and rerunning its
narrow tests before continuing:

1. Initial terminal audit scaffold: `2351fa6f`.
2. B4 proof spine: `814f8d7f`.
3. Current Python/public contracts: `2c2358d6`.
4. Recall retirement plus proof: `2cd157a4`, `68192ee9`.
5. Durable paid-run authorization/ledger: `197f0ddf`, `84f7445a`.
6. ForgetEval adapter: `e9755cd4`.
7. Free-gate and throughput/migration fixes: `cc5c6c7c` (includes migration
   `20260724_003_worker_claim_throughput.sql`).
8. C3 identity/adapter chain: `1471ac82`, `8617fa93`, `a9d7e73a`, `c4a6c97b`,
   `d8933695`, `c3f610bc`, `e1d019c0`, `9c37fff3`.
9. Unchunked canonical citation repair: `ee4de593`.
10. Final C3/ForgetEval evidence: `8ccdc407` only after every implementation
   dependency above.
11. Initial terminal reconciliation documents: `8e6a2d1c`.
12. Post-review proof-integrity implementation and evidence: `aae4b97a`,
    `847a5059`.
13. Final regression-only repairs: `1915dfa8`, `6b1efe86`, `d590b7dc`,
    `c5d58ec5`, `6185c5b5`, `54e50a84`.
14. Terminal-manifest repair chain: `8bf9ab44`, `40e5e6cf`, `8c023803`.
15. Source-derived C3 rejected stress bank: `025f350c`.
16. Rejected-bank adjudication, runner honesty repair, sealed v3 provenance,
    and corrected manifest: `965ad632`.
17. Hash-bound rejection receipt plus regression: `ad9b217a`.
18. Initial next-evidence handoff: `11f957f0`, `665a1be3`, `fc873e75`,
    `adbf55ef`, `7dfae997`, `5ebd868e`, `ece26197`, `702097ee`, `f25a6805`,
    `5a99a0e3`, `a1f89b18`, `b3d98795`, `23ef9a10`.
19. Authorized forgetting proposal control plane and bounded evidence:
    `8ef48afd`, `8a39e940`, `bea8e309`, `e6304ad1`, `779141f5`, `ad18c7f3`,
    `470cf9e0`, `e8f087e1`, `52e67bdd`.
20. Forgetting lineage correctness and terminal evidence: `10b075fe`,
    `8a786689`, `e0a44371`, `ee69e9aa`.
21. Packing sufficiency rejection: `017078c5`.
22. Coding gate implementation and baseline-ceiling rejection: `c70feead`.
23. Apply the final fail-closed coding-evidence and reconciliation commit last;
    it consumes the child authorization, preserves the bounded evidence bundle,
    hardens the test-path policy, and updates this manifest and clean handoff.

The private Syndai branch is separate and depends on the B4 public receipt
contract plus `ee4de593`. Land its exact private manifest only after compatible
public MemPhant commits exist. No private commit identity, path, or secret is
recorded in this repository.

Generated OpenAPI/MCP artifacts travel with their owning commits. The worker
throughput migration travels before any binary that declares it as migration
head. After assembly, run the complete MemPhant gate and the complete Syndai
gate plus the cross-repo scratch integration. Remote CI, push, PR, merge,
deployment, production enablement, a materially different packing treatment,
or a materially harder coding-memory gate all require separate authorization.
