from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/analyze_longmemeval_v2_packing_campaign.py"


def _load():
    spec = importlib.util.spec_from_file_location("lme_campaign_analyzer", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_analyzer_reports_separate_metrics_and_kill_predicates(tmp_path):
    module = _load()
    manifest = tmp_path / "manifest.json"
    cases = [
        {"id": f"{domain}-{index}", "domain": domain}
        for domain in module.DOMAINS
        for index in range(6)
    ]
    manifest.write_text(json.dumps({"cases": cases}))
    attempts = []
    for arm in module.ARMS:
        for domain in module.DOMAINS:
            run_dir = tmp_path / "runs" / arm / domain
            run_dir.mkdir(parents=True)
            rows = []
            for index in range(6):
                is_abstention = index >= 4
                score = arm != "memphant_current" or index != 0
                rows.append(
                    {
                        "question_id": f"{domain}-{index}",
                        "is_abstention_problem": is_abstention,
                        "score_bool": score,
                        "is_unknown": False,
                        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
                        "memory_query_duration_seconds": 0.01,
                    }
                )
            (run_dir / "per_question.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows)
            )
            for index in range(6):
                attempts.append(
                    {
                        "status": "result",
                        "start": {"arm": arm, "domain": domain, "requested_model": "qwen/qwen3.5-9b"},
                    }
                )
            for _ in range(3):
                attempts.append(
                    {
                        "status": "result",
                        "start": {"arm": arm, "domain": domain, "requested_model": "openai/gpt-5.2"},
                    }
                )
            if arm != "no_retrieval":
                proof_dir = tmp_path / "proof" / arm / domain
                proof_dir.mkdir(parents=True)
                for index in range(6):
                    core = {
                        "contract": {"packing_arm": module.PACKING_ARM[arm]},
                        "packing": {
                            "disposition": "supported",
                            "items": [{"status": "supported", "verification_sha256": "a" * 64}],
                        },
                    }
                    (proof_dir / f"{index}.packing.json").write_text(
                        json.dumps({**core, "packing_proof_sha256": module.sha256_json(core)})
                    )
    snapshot = {
        "attempts": attempts,
        "reported_cost_usd": 1.25,
        "attempts_sha256": "b" * 64,
    }
    module.validate_provider_attempt_ledger = lambda value: None
    result = module.analyze(
        tmp_path,
        manifest,
        tmp_path / "attempts.jsonl",
        snapshot_loader=lambda path: snapshot,
    )
    assert result["provider"]["attempt_count"] == 90
    assert result["cells"]["memphant_cap1200"]["answer_correct"] == 12
    assert result["cells"]["memphant_current"]["answer_correct"] == 10
    assert result["kill_predicates"]["primary_cap1200"] is True
    assert result["retrieval_and_packed_gold"]["longmemeval_v2"].startswith("NOT_AVAILABLE")
