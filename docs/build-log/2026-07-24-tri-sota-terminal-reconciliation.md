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
| Rung 7 packing | mixed retrieval result; reader external-blocked | Across 178 rows Recall@10 moved 0.6145 to 0.8434, while the binding 12-row exact-abstention slice regressed 7/12 to 5/12. Paid packet frozen, zero calls, maximum 1,020 calls / $116. Default remains off. |
| B5 cleanup | complete/rejected as adjudicated | `2026-07-23-b5-recall-stage-retirement.md`; heuristic, learned wrapper, decomposition and Balanced deleted; cross-encoder seam remains default-off. |
| C0/C1 | correctness complete; parity/live L0 open | Existing C0/C1 proofs; no new production corpus authorization. |
| C2 | rejected/deleted | Free kill-gate remains binding. |
| C3 public volume | volume/isolation complete; adversarial continuity open | `2026-07-24-c3-public-code-lane.md`; 64,055 events, exact action-to-result lexical retrieval R@10 18/40. Reader, paraphrased adversarial continuity, and live production ingestion remain open. |
| WS-F active-read mechanism | local integration complete; dogfood gate open | Private companion evidence passes retain/compile/verified receipt/loader/trace/isolation/auth paths. Feature is not an enabled production cutover. Exact private landing identity stays in the private repository. |
| D2 ForgetEval | instrument complete; measured gaps remain | `2026-07-24-forgeteval-public-api.md`; 133/126/126 on adversarial. |
| D4 SWE-Explore | immutable-input external block | The pinned JSONL omits issue text/base commits. Official code includes a reconstruction helper, but its auxiliary datasets are unpinned, required issue-map/trajectory inputs are absent, and mappings are incomplete for pro/multilingual cells. |
| D4 SWE Context Bench | no-model smoke complete; benchmark unauthorized | Pinned artifacts under `tri-sota-completion/swe-contextbench`; exact task/adapter/model/compute budgets and authorization absent. |
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
13. Final regression-only repairs: `1915dfa8`, `6b1efe86`, `d590b7dc`.
14. Apply the commit containing this corrected manifest last.

The private Syndai branch is separate and depends on the B4 public receipt
contract plus `ee4de593`. Land its exact private manifest only after compatible
public MemPhant commits exist. No private commit identity, path, or secret is
recorded in this repository.

Generated OpenAPI/MCP artifacts travel with their owning commits. The worker
throughput migration travels before any binary that declares it as migration
head. After assembly, run the complete MemPhant gate and the complete Syndai
gate plus the cross-repo scratch integration. Remote CI, push, PR, merge,
deployment, production enablement, paid packing reader work, and D4 model work
all require separate authorization.
