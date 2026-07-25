#!/usr/bin/env python3
"""Run the authorized SWE-ContextBench Codex kill gate.

The runner exposes only the frozen target fields and, for treatment arms, the
exact resource body previously recalled from MemPhant plus its receipt/trace
identity. It never loads target solution material into the agent prompt.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "benchmarks/manifests/swe_contextbench.kill.n12.json"
DEFAULT_REHEARSAL = (
    ROOT
    / "docs/build-log/artifacts/next-evidence/coding/swe-contextbench-n12-rehearsal.json"
)
DEFAULT_AUTHORIZATION = (
    ROOT
    / "docs/build-log/artifacts/next-evidence/coding/swe-contextbench-authorization.json"
)
ARMS = ("no_memory", "unrelated_memory", "related_memphant_memory")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_adapter() -> Any:
    path = ROOT / "scripts/run_swe_contextbench_memphant.py"
    spec = importlib.util.spec_from_file_location("swe_context_adapter", path)
    require(spec is not None and spec.loader is not None, "adapter import failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_authorization(
    packet: dict[str, object],
    *,
    manifest_sha256: str,
    rehearsal_sha256: str,
    codex_version: str,
) -> None:
    require(packet.get("schema_version") == 1, "authorization schema drift")
    require(packet.get("campaign") == "swe_contextbench_codex_n12", "campaign drift")
    require(packet.get("status") == "AUTHORIZED_FIRST_TRANCHE", "campaign is not authorized")
    authorization = packet.get("authorization")
    require(isinstance(authorization, dict), "authorization is missing")
    require(
        authorization.get("authorized_by") == "repository_owner",
        "repository-owner authorization is missing",
    )
    require(
        authorization.get("scope") == "local benchmark execution only",
        "authorization scope drift",
    )
    inputs = packet.get("inputs")
    require(isinstance(inputs, dict), "authorization inputs are missing")
    require(inputs.get("manifest_sha256") == manifest_sha256, "manifest hash drift")
    require(inputs.get("rehearsal_sha256") == rehearsal_sha256, "rehearsal hash drift")
    require(
        isinstance(inputs.get("official_code_commit"), str)
        and re.fullmatch(r"[0-9a-f]{40}", str(inputs["official_code_commit"])) is not None,
        "official code commit is not pinned",
    )
    agent = packet.get("agent")
    require(isinstance(agent, dict), "agent packet is missing")
    require(agent.get("cli") == "codex", "agent CLI drift")
    require(agent.get("cli_version") == codex_version, "Codex CLI version drift")
    require(agent.get("model") == "gpt-5.6-sol", "model drift")
    require(agent.get("reasoning_effort") == "medium", "reasoning effort drift")
    require(agent.get("sandbox") == "workspace-write", "sandbox drift")
    require(agent.get("timeout_seconds") == 900, "task timeout drift")
    require(agent.get("retries") == 0, "retries must remain zero")
    tranche = packet.get("tranche")
    require(isinstance(tranche, dict), "tranche is missing")
    target_ids = tranche.get("target_ids")
    require(
        isinstance(target_ids, list)
        and len(target_ids) == 4
        and len(set(target_ids)) == 4
        and all(isinstance(value, str) and value for value in target_ids),
        "first tranche must contain exactly four unique targets",
    )
    require(tranche.get("arms") == list(ARMS), "arm order drift")
    require(tranche.get("max_task_runs") == 12, "task-run cap drift")


def build_agent_prompt(
    target: dict[str, str],
    *,
    arm: str,
    memory_body: str | None = None,
    memory_proof: dict[str, object] | None = None,
) -> str:
    require(arm in ARMS, "unknown benchmark arm")
    base = (
        "You are solving one isolated repository-level bug-fix task.\n"
        f"Instance: {target['instance_id']}\n"
        f"Repository: {target['repo']}\n"
        f"Base commit: {target['base_commit']}\n\n"
        f"Problem statement:\n{target['problem_statement']}\n\n"
        "Inspect the repository, identify the root cause, implement the smallest durable fix, "
        "and run focused tests when practical. Do not modify test files. Leave the working tree "
        "with the implementation change; the harness will collect the diff."
    )
    if arm == "no_memory":
        require(memory_body is None and memory_proof is None, "baseline received memory")
        return base
    require(isinstance(memory_body, str) and memory_body, "treatment memory is missing")
    require(isinstance(memory_proof, dict), "treatment proof is missing")
    trace_id = memory_proof.get("trace_id")
    receipt = memory_proof.get("receipt_sha256")
    unit_ids = memory_proof.get("returned_unit_ids")
    require(isinstance(trace_id, str) and trace_id, "memory trace is missing")
    require(isinstance(receipt, str) and len(receipt) == 64, "memory receipt is invalid")
    require(isinstance(unit_ids, list) and unit_ids, "memory units are missing")
    memory = (
        "\n\nMemPhant recalled prior experience (verified):\n"
        f"Trace: {trace_id}\nReceipt SHA-256: {receipt}\n"
        f"Unit IDs: {', '.join(str(value) for value in unit_ids)}\n\n"
        f"{memory_body}\n\n"
        "Use this prior experience critically: transfer only the reusable root-cause or workflow "
        "pattern, verify it against the current code, and ignore details that do not apply."
    )
    return base + memory


def codex_command(
    *,
    codex_bin: str,
    worktree: Path,
    output_message: Path,
    model: str,
    reasoning_effort: str,
) -> list[str]:
    return [
        codex_bin,
        "exec",
        "-",
        "--cd",
        str(worktree),
        "--model",
        model,
        "--sandbox",
        "workspace-write",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--json",
        "--output-last-message",
        str(output_message),
        "-c",
        f'model_reasoning_effort="{reasoning_effort}"',
        "-c",
        'approval_policy="never"',
        "-c",
        'shell_environment_policy.inherit="none"',
    ]


def is_test_path(path: str) -> bool:
    parts = Path(path).parts
    name = Path(path).name.lower()
    test_directories = {"test", "tests", "testing", "spec", "specs", "__tests__"}
    test_configs = {"conftest.py", "pytest.ini", "tox.ini", ".rspec"}
    return (
        any(part.lower() in test_directories for part in parts)
        or name in test_configs
        or bool(re.match(r"^(jest|vitest|karma)\.(config|conf)\.", name))
        or bool(re.search(r"(^|[._-])(test|tests|spec|specs)([._-]|$)", name))
    )


def changed_paths_from_patch(patch: str) -> list[str]:
    paths: list[str] = []
    for line in patch.splitlines():
        if not line.startswith("diff --git "):
            continue
        fields = shlex.split(line)
        require(
            len(fields) == 4
            and fields[0:2] == ["diff", "--git"]
            and fields[2].startswith("a/")
            and fields[3].startswith("b/"),
            "model patch contains an invalid diff path header",
        )
        paths.extend((fields[2][2:], fields[3][2:]))
    return paths


def validate_model_patch(patch: str, *, target_patch_sha256: str) -> None:
    require(sha256_text(patch) != target_patch_sha256, "model patch equals reference solution")
    for path in changed_paths_from_patch(patch):
        require(
            not is_test_path(path),
            f"model patch touched a test path: {path}",
        )


def continuation_verdict(
    results: dict[str, dict[str, bool]],
    *,
    unsafe_reuse: int,
    invalid_receipts: int,
) -> dict[str, object]:
    require(len(results) == 4, "continuation requires exactly four paired targets")
    for target_id, arms in results.items():
        require(set(arms) == set(ARMS), f"incomplete arms for {target_id}")
    baseline = sum(int(arms["no_memory"]) for arms in results.values())
    unrelated = sum(int(arms["unrelated_memory"]) for arms in results.values())
    related = sum(int(arms["related_memphant_memory"]) for arms in results.values())
    related_gain = related - baseline
    unrelated_gain = unrelated - baseline
    return {
        "continue": (
            related_gain >= 2
            and unrelated_gain == 0
            and unsafe_reuse == 0
            and invalid_receipts == 0
        ),
        "resolved": {
            "no_memory": baseline,
            "unrelated_memory": unrelated,
            "related_memphant_memory": related,
        },
        "related_gain_over_no_memory": related_gain,
        "unrelated_gain_over_no_memory": unrelated_gain,
        "unsafe_reuse": unsafe_reuse,
        "invalid_receipts": invalid_receipts,
    }


def baseline_ceiling_verdict(
    baseline_results: dict[str, bool],
    *,
    total_targets: int,
    required_related_gain: int,
) -> dict[str, object]:
    require(0 < len(baseline_results) <= total_targets, "baseline result count is invalid")
    require(required_related_gain > 0, "required related gain must be positive")
    resolved = sum(int(value) for value in baseline_results.values())
    maximum_gain = total_targets - resolved
    return {
        "stop": maximum_gain < required_related_gain,
        "graded_baselines": len(baseline_results),
        "resolved_baselines": resolved,
        "ungraded_baselines": total_targets - len(baseline_results),
        "maximum_possible_related_gain": maximum_gain,
        "required_related_gain": required_related_gain,
    }


def run_checked(argv: list[str], *, cwd: Path) -> str:
    return subprocess.run(
        argv,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def prepare_checkout(worktree: Path, *, repo: str, base_commit: str) -> None:
    if worktree.exists():
        shutil.rmtree(worktree)
    worktree.mkdir(parents=True)
    run_checked(["git", "init", "-q"], cwd=worktree)
    run_checked(
        ["git", "remote", "add", "origin", f"https://github.com/{repo}.git"],
        cwd=worktree,
    )
    run_checked(["git", "fetch", "--depth", "1", "origin", base_commit], cwd=worktree)
    run_checked(["git", "checkout", "-q", "--detach", "FETCH_HEAD"], cwd=worktree)
    run_checked(["git", "remote", "remove", "origin"], cwd=worktree)
    require(run_checked(["git", "rev-parse", "HEAD"], cwd=worktree) == base_commit, "checkout drift")


def collect_patch(worktree: Path) -> str:
    subprocess.run(
        ["git", "add", "-N", "."],
        cwd=worktree,
        check=True,
        capture_output=True,
        text=True,
    )
    return subprocess.run(
        ["git", "diff", "--binary", "--", "."],
        cwd=worktree,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def append_ledger(path: Path, record: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(canonical_json(record) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def execute(args: argparse.Namespace) -> int:
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    rehearsal = json.loads(args.rehearsal.read_text(encoding="utf-8"))
    authorization = json.loads(DEFAULT_AUTHORIZATION.read_text(encoding="utf-8"))
    codex_version = run_checked([args.codex_bin, "--version"], cwd=ROOT)
    validate_authorization(
        authorization,
        manifest_sha256=sha256_file(args.manifest),
        rehearsal_sha256=sha256_file(args.rehearsal),
        codex_version=codex_version,
    )
    adapter = load_adapter()
    experiences, targets = adapter.verify_sources(
        manifest,
        experience_path=args.experience_parquet,
        related_path=args.related_parquet,
        relationship_path=args.relationship_parquet,
    )
    records_by_key = {
        (record["target_id"], record["arm"]): record
        for record in rehearsal["records"]
    }
    packet_targets = authorization["tranche"]["target_ids"]
    require(
        packet_targets == [case["target_id"] for case in manifest["cases"][:4]],
        "authorization target order differs from frozen manifest",
    )
    args.output_root.mkdir(parents=True, exist_ok=True)
    predictions_dir = args.output_root / "predictions"
    raw_dir = args.output_root / "raw"
    worktrees_dir = args.output_root / "worktrees"
    for path in (predictions_dir, raw_dir, worktrees_dir):
        path.mkdir(parents=True, exist_ok=True)
    ledger_path = args.output_root / "attempts.jsonl"
    completed = set()
    if ledger_path.exists():
        for line in ledger_path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record.get("status") in {"COMPLETED", "FAILED_NO_RETRY"}:
                completed.add((record["target_id"], record["arm"]))

    case_by_target = {case["target_id"]: case for case in manifest["cases"]}
    for target_id in packet_targets:
        case = case_by_target[target_id]
        target = adapter.target_agent_input(targets[target_id])
        for arm in ARMS:
            if (target_id, arm) in completed:
                continue
            memory_body = None
            memory_proof = None
            if arm != "no_memory":
                rehearsal_arm = "related" if arm == "related_memphant_memory" else "unrelated"
                source_field = "experience_id" if rehearsal_arm == "related" else "unrelated_experience_id"
                source_id = case[source_field]
                memory_body = adapter.experience_body(experiences[source_id])
                memory_proof = records_by_key[(target_id, rehearsal_arm)]
                require(
                    sha256_text(memory_body) == memory_proof["resource_body_sha256"],
                    "recalled memory body hash drift",
                )
            prompt = build_agent_prompt(
                target,
                arm=arm,
                memory_body=memory_body,
                memory_proof=memory_proof,
            )
            worktree = worktrees_dir / target_id / arm
            prepare_checkout(worktree, repo=target["repo"], base_commit=target["base_commit"])
            raw_path = raw_dir / f"{target_id}.{arm}.jsonl"
            last_path = raw_dir / f"{target_id}.{arm}.last.txt"
            command = codex_command(
                codex_bin=args.codex_bin,
                worktree=worktree,
                output_message=last_path,
                model=authorization["agent"]["model"],
                reasoning_effort=authorization["agent"]["reasoning_effort"],
            )
            started = time.time()
            status = "COMPLETED"
            exit_code = 0
            try:
                result = subprocess.run(
                    command,
                    input=prompt,
                    text=True,
                    capture_output=True,
                    timeout=authorization["agent"]["timeout_seconds"],
                    check=False,
                )
                exit_code = result.returncode
                raw_path.write_text(result.stdout, encoding="utf-8")
                if result.returncode != 0:
                    status = "FAILED_NO_RETRY"
            except subprocess.TimeoutExpired as error:
                status = "FAILED_NO_RETRY"
                exit_code = 124
                stdout = error.stdout.decode() if isinstance(error.stdout, bytes) else (error.stdout or "")
                raw_path.write_text(stdout, encoding="utf-8")
            patch = collect_patch(worktree) if status == "COMPLETED" else ""
            if status == "COMPLETED":
                validate_model_patch(patch, target_patch_sha256=case["target_patch_sha256"])
            prediction = {
                target_id: {
                    "model_name_or_path": authorization["agent"]["model"],
                    "instance_id": target_id,
                    "model_patch": patch,
                }
            }
            prediction_path = predictions_dir / arm / f"{target_id}_preds.json"
            prediction_path.parent.mkdir(parents=True, exist_ok=True)
            prediction_path.write_text(json.dumps(prediction, indent=2, sort_keys=True) + "\n")
            append_ledger(
                ledger_path,
                {
                    "schema_version": 1,
                    "target_id": target_id,
                    "arm": arm,
                    "status": status,
                    "exit_code": exit_code,
                    "started_unix": started,
                    "duration_seconds": round(time.time() - started, 3),
                    "prompt_sha256": sha256_text(prompt),
                    "command_sha256": sha256_text(canonical_json(command)),
                    "raw_output_sha256": sha256_file(raw_path),
                    "model_patch_sha256": sha256_text(patch),
                    "model_patch_bytes": len(patch.encode("utf-8")),
                    "memory_trace_id": memory_proof.get("trace_id") if memory_proof else None,
                    "memory_receipt_sha256": memory_proof.get("receipt_sha256") if memory_proof else None,
                },
            )
            print(canonical_json({"target_id": target_id, "arm": arm, "status": status}))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("execute",))
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--rehearsal", type=Path, default=DEFAULT_REHEARSAL)
    parser.add_argument("--experience-parquet", type=Path, required=True)
    parser.add_argument("--related-parquet", type=Path, required=True)
    parser.add_argument("--relationship-parquet", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--codex-bin", default=shutil.which("codex") or "codex")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return execute(args)


if __name__ == "__main__":
    raise SystemExit(main())
