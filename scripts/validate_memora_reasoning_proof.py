#!/usr/bin/env python3
"""Fail closed before paid FAMA judging of the pinned Memora reasoning arm."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import generate_memora_memphant_answers as generator  # noqa: E402
import run_memora_fama  # noqa: E402


GROUP = "weekly/software_engineer"
TASK = "reasoning"
CANONICAL_QUANTITY_NAMESPACE = "quantity_event_v1"
ROLLUP_RE = re.compile(
    r"^quantity rollup ([^/]+)/([^/]+)/([^ ]+) \(([^)]+)\); "
    r"window=\[([^,]+),([^)]*)\); filter=(.*?); total=([^;]+); "
    r"average=([^ ]+) \(rounded to 6 decimal places when needed\); "
    r"count=(\d+); min=([^;]+); max=(.+)$"
)
SESSION_RE = re.compile(r"\[session (\d+)\]")


def decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("Memora oracle contains a non-numeric value")
    return Decimal(str(value))


def derive_oracle(question: dict[str, Any], window: tuple[str, str]) -> dict[str, Any]:
    evidence = question.get("memory_evidence")
    if not isinstance(evidence, dict):
        raise ValueError("official reasoning question omitted memory_evidence")
    goal = None
    if "food_expenses" in evidence:
        rows, value_key, dimension = evidence["food_expenses"], "amount", "expense_type"
        measure, unit, aggregate = "food_spending", "usd", "total"
        official = evidence.get("total_amount")
    elif "step_data" in evidence:
        rows, value_key, dimension = evidence["step_data"], "step_count", "activity_type"
        measure, unit, aggregate = "daily_steps", "steps", "total"
        official = evidence.get("total_steps")
    elif "expense_items" in evidence:
        rows, value_key, dimension = evidence["expense_items"], "amount", "expense_type"
        measure, unit, aggregate = "food_spending", "usd", "total"
        official = evidence.get("category_total")
    elif "activity_sessions" in evidence:
        rows = evidence["activity_sessions"]
        goal_data = evidence.get("goal_data")
        goal_session = evidence.get("goal_session")
        if not isinstance(goal_data, dict) or not isinstance(goal_session, dict):
            raise ValueError("official goal question omitted goal provenance")
        if rows and "amount" in rows[0]:
            value_key, dimension = "amount", "expense_type"
            measure, unit, aggregate = "food_spending", "usd", "total"
        else:
            value_key, dimension = "step_count", "activity_type"
            measure, unit, aggregate = "daily_steps", "steps", "average"
            rows = [dict(row, activity_type=goal_data.get("subcategory")) for row in rows]
        official = evidence.get("actual_value")
        goal = {
            "value": decimal(evidence.get("goal_value")),
            "category": goal_data.get("category"),
            "subcategory": goal_data.get("subcategory"),
            "session_id": goal_session.get("session_id"),
        }
    else:
        raise ValueError(f"unsupported reasoning oracle shape: {question.get('question_id')}")
    if not isinstance(rows, list) or not rows:
        raise ValueError("official reasoning activity set is empty")
    events = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("session_id"), int):
            raise ValueError("official reasoning activity row is malformed")
        category = row.get(dimension)
        if not isinstance(category, str) or not category:
            raise ValueError("official reasoning activity category is malformed")
        events.append((row["session_id"], decimal(row.get(value_key)), ((dimension, category),)))
    total = sum((event[1] for event in events), Decimal(0))
    expected = total if aggregate == "total" else total / len(events)
    tolerance = Decimal("0.01") if unit == "usd" else Decimal("0.000001")
    if abs(decimal(official) - expected) > tolerance:
        raise ValueError("official reasoning aggregate disagrees with its activity rows")
    dimension_values = {event[2] for event in events}
    required_dimensions = (
        dict(next(iter(dimension_values)))
        if measure == "food_spending" and len(dimension_values) == 1
        else {}
    )
    return {
        "question_id": question["question_id"],
        "question": question.get("question", question["question_id"]),
        "question_date": question.get("question_date", window[1][:10]),
        "events": events,
        "measure": measure, "unit": unit, "aggregate": aggregate,
        "total": total, "average": total / len(events), "window": window,
        "goal": goal,
        "required_dimensions": required_dimensions,
    }


def render_decimal(value: Decimal, places: int | None = None) -> str:
    if places is not None:
        return format(value.quantize(Decimal(1).scaleb(-places)), "f")
    rendered = format(value.normalize(), "f")
    return "0" if rendered == "-0" else rendered


def oracle_reader_evidence(oracle: dict[str, Any]) -> list[dict[str, Any]]:
    dimensions = {event[2] for event in oracle["events"]}
    filters = "all"
    if len(dimensions) == 1 and next(iter(dimensions)):
        filters = ",".join(f"{key}={value}" for key, value in next(iter(dimensions)))
    values = [event[1] for event in oracle["events"]]
    start, end = oracle["window"]
    rollup = (
        f"quantity rollup {CANONICAL_QUANTITY_NAMESPACE}/{oracle['measure']}/"
        f"{oracle['measure']} ({oracle['unit']}); window=[{start},{end}); "
        f"filter={filters}; total={render_decimal(oracle['total'])}; "
        f"average={render_decimal(oracle['average'], 6)} "
        f"(rounded to 6 decimal places when needed); count={len(values)}; "
        f"min={render_decimal(min(values))}; max={render_decimal(max(values))}"
    )
    evidence = [{"rank": 1, "unit_id": "oracle-rollup", "body": rollup}]
    goal = oracle["goal"]
    if goal is not None:
        period = "day" if oracle["unit"] == "steps" else "week"
        evidence.append({
            "rank": 2,
            "unit_id": "oracle-goal",
            "body": (
                f"goal {goal['category']}/{goal['subcategory']}: "
                f"target={render_decimal(goal['value'])} {oracle['unit']} per {period}"
            ),
        })
    return evidence


def answer_contains_decimal(answer: str, value: Decimal) -> bool:
    compact = answer.replace(",", "").replace("$", "")
    return value in (
        decimal(candidate)
        for candidate in re.findall(r"-?\d+(?:\.\d+)?", compact)
    )


def validate_oracle_reader_answer(oracle: dict[str, Any], answer: str) -> bool:
    actual = oracle[oracle["aggregate"]]
    numbers = {
        decimal(candidate)
        for candidate in re.findall(r"-?\d+(?:\.\d+)?", answer.replace(",", "").replace("$", ""))
    }
    valid = actual in numbers
    goal = oracle["goal"]
    if goal is not None:
        meeting = actual >= goal["value"] if oracle["unit"] == "steps" else actual <= goal["value"]
        status = re.match(r"\s*(yes|no|not)\b", answer.lower())
        clauses = re.split(r"\b(?:against|versus|and)\b|[;:]", answer.lower())
        actual_labels = (
            "actual", "spent", "spending", "average", "averaged", "averaging"
        )
        goal_labels = ("goal", "budget")
        actual_clauses = {
            index
            for index, clause in enumerate(clauses)
            if answer_contains_decimal(clause, actual)
            and any(label in clause for label in actual_labels)
        }
        goal_clauses = {
            index
            for index, clause in enumerate(clauses)
            if answer_contains_decimal(clause, goal["value"])
            and any(label in clause for label in goal_labels)
        }
        valid = (
            valid
            and answer_contains_decimal(answer, goal["value"])
            and bool(actual_clauses)
            and bool(goal_clauses)
            and bool(actual_clauses - goal_clauses)
            and bool(goal_clauses - actual_clauses)
            and status is not None
            and status.group(1) in (("yes",) if meeting else ("no", "not"))
        )
    else:
        valid = valid and numbers == {actual}
    if not valid:
        raise ValueError(f"reader answer omitted exact values/status: {oracle['question_id']}")
    return True


def run_oracle_reader_screen(
    oracles: list[dict[str, Any]], reader: Any
) -> dict[str, Any]:
    results = []
    total_cost = 0.0
    for oracle in oracles:
        evidence = oracle_reader_evidence(oracle)
        answer, metadata = reader.answer(
            {
                "question": oracle["question"],
                "question_date": oracle["question_date"],
            },
            evidence,
        )
        validate_oracle_reader_answer(oracle, answer)
        cost = metadata.get("cost_usd") if isinstance(metadata, dict) else None
        fresh = (
            metadata.get("provider_attempts") == 1
            and metadata.get("fresh_call") is True
            and metadata.get("cache_hit") is False
        )
        if not isinstance(cost, (int, float)) or not fresh:
            raise ValueError("reader screen call proof is incomplete")
        total_cost += float(cost)
        results.append({
            "question_id": oracle["question_id"],
            "evidence_sha256": generator.sha256_json(evidence),
            "answer": answer,
            "answer_sha256": generator.sha256_json(answer),
            "reader": metadata,
        })
    return {
        "eligible": True,
        "group": GROUP,
        "task": TASK,
        "model": reader.model,
        "questions": len(results),
        "reported_cost_usd": total_cost,
        "results": results,
    }


def official_oracles(repo: Path) -> tuple[list[dict[str, Any]], str]:
    manifest = json.loads(run_memora_fama.DEFAULT_MANIFEST.read_text(encoding="utf-8"))
    run_memora_fama.verify_official_repo(repo, manifest)
    questions, _, _ = run_memora_fama.verify_dataset(repo, manifest)
    questions, _, _ = run_memora_fama.select_group(questions, GROUP)
    questions, _, _ = run_memora_fama.select_task(questions, TASK)
    question_file = repo / "data" / "weekly" / "software_engineer" / "evaluation_questions_software_engineer.json"
    document = json.loads(question_file.read_text(encoding="utf-8"))
    dates = document.get("date_range", {})
    start = date.fromisoformat(dates["start_date"])
    end = date.fromisoformat(dates["end_date"]) + timedelta(days=1)
    window = (f"{start.isoformat()}T00:00:00Z", f"{end.isoformat()}T00:00:00Z")
    return [derive_oracle(question, window) for question in questions], run_memora_fama.sha256_file(question_file)


def parse_quantity_source(source: dict[str, Any]) -> tuple[int, Decimal, str, str, dict[str, Any]]:
    body = source.get("memory_unit_body")
    episode = source.get("source_episode_body")
    if not isinstance(body, str) or not isinstance(episode, str):
        raise ValueError("quantity source proof is incomplete")
    match = SESSION_RE.search(episode)
    identity, separator, encoded = body.partition(": ")
    namespace, item_separator, item_key = identity.partition(" item ")
    if (
        not match or not separator or not item_separator
        or namespace != CANONICAL_QUANTITY_NAMESPACE
    ):
        raise ValueError("quantity source proof is not canonical")
    fields = json.loads(encoded)
    if set(fields) != {"dimensions", "measure", "occurred_at", "type", "unit", "value"} or fields["type"] != "quantity_event.v1":
        raise ValueError("quantity source fields drifted")
    if not isinstance(fields["dimensions"], dict):
        raise ValueError("quantity source dimensions are malformed")
    return int(match.group(1)), decimal(fields["value"]), item_key, fields["measure"], {
        "unit": fields["unit"], "dimensions": fields["dimensions"],
    }


def structured_goal_matches(source: dict[str, Any], goal: dict[str, Any]) -> bool:
    body = source.get("memory_unit_body")
    episode = source.get("source_episode_body")
    if not isinstance(body, str) or not isinstance(episode, str):
        return False
    session = SESSION_RE.search(episode)
    _, separator, encoded = body.partition(": ")
    if not session or int(session.group(1)) != goal["session_id"] or not separator:
        return False
    try:
        fields = json.loads(encoded)
    except json.JSONDecodeError:
        return False
    scalars: list[Any] = []
    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                scalars.append(key)
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
        else:
            scalars.append(value)
    walk(fields)
    text = " ".join(str(value).lower() for value in [body, *scalars])
    numbers = []
    for value in scalars:
        try:
            numbers.append(decimal(value))
        except (ValueError, ArithmeticError):
            pass
    tokens = set(re.findall(r"[a-z0-9]+", text))
    required_tokens = set(
        re.findall(r"[a-z0-9]+", str(goal["subcategory"]).lower())
    )
    return (
        required_tokens <= tokens
        and bool(tokens & {"goal", "goals", "budget", "target", "limit"})
        and goal["value"] in numbers
    )


def validate_record(oracle: dict[str, Any], record: dict[str, Any], answer: dict[str, Any]) -> None:
    generator.validate_retrieval_record(record, answer)
    retrieval = record["retrieval"]
    trace_items = {item["unit_id"]: item for item in retrieval["trace"]["context_items"]}
    derived = {source["unit_id"]: source for source in retrieval["derived_sources"]}
    expected_events = Counter((session, value) for session, value, _ in oracle["events"])
    matched = False
    for item in answer["evidence"]:
        rollup = ROLLUP_RE.match(item["body"])
        trace_item = trace_items.get(item["unit_id"])
        if not rollup or not trace_item or not trace_item["derived_from_unit_ids"]:
            continue
        try:
            sources = [derived[unit_id] for unit_id in trace_item["derived_from_unit_ids"]]
            actual = Counter()
            for source in sources:
                session, value, item_key, measure, details = parse_quantity_source(source)
                if item_key != oracle["measure"] or measure != oracle["measure"] or details["unit"] != oracle["unit"]:
                    raise ValueError("quantity source series drifted")
                if any(
                    details["dimensions"].get(key) != value
                    for key, value in oracle["required_dimensions"].items()
                ):
                    raise ValueError("quantity source dimension drifted")
                actual[(session, value)] += 1
        except (KeyError, ValueError):
            continue
        if actual != expected_events:
            continue
        namespace, item_key, measure, unit, window_from, window_to, filters, total, average, count, _, _ = rollup.groups()
        expected_filter = (
            ",".join(
                f"{key}={value}"
                for key, value in sorted(oracle["required_dimensions"].items())
            )
            or "all"
        )
        if (
            namespace != CANONICAL_QUANTITY_NAMESPACE or item_key != oracle["measure"]
            or measure != oracle["measure"] or unit != oracle["unit"]
            or (window_from, window_to) != oracle["window"]
            or filters != expected_filter
            or decimal(total) != oracle["total"]
            or decimal(average) != oracle["average"].quantize(Decimal("0.000001"))
            or int(count) != len(oracle["events"])
        ):
            raise ValueError(f"reasoning rollup aggregate drifted: {oracle['question_id']}")
        matched = True
        break
    if not matched:
        raise ValueError(f"reasoning rollup lacks exact activity provenance: {oracle['question_id']}")
    if oracle["goal"] is not None and not any(
        structured_goal_matches(source, oracle["goal"])
        for source in retrieval["direct_sources"]
    ):
        raise ValueError(f"reasoning goal lacks exact direct provenance: {oracle['question_id']}")


def validate_run_coverage(answers_doc: dict[str, Any], proof: dict[str, Any]) -> None:
    model = answers_doc.get("summary", {}).get("model")
    reader = proof.get("reader")
    runtime = proof.get("runtime")
    if model not in generator.MODEL_CANDIDATES or not isinstance(reader, dict) or not isinstance(runtime, dict):
        raise ValueError("reasoning model/runtime proof is incomplete")
    behavior = runtime.get("behavior_environment")
    if (
        reader.get("model") != model
        or reader.get("reasoning_effort") != "high"
        or reader.get("fresh_calls") != 5
        or reader.get("cache_hits") != 0
        or reader.get("provider_attempts") != 5
        or reader.get("unpriced_provider_attempts") != 0
        or reader.get("cost_status") != "all_provider_attempts_priced"
        or not isinstance(behavior, dict)
        or behavior.get("MEMPHANT_STRUCTURED_STATE_MODEL") != model
        or behavior.get("MEMPHANT_STRUCTURED_STATE_CONCURRENCY") != "4"
    ):
        raise ValueError("reasoning model arm or reader coverage drifted")
    effort = behavior.get("MEMPHANT_STRUCTURED_STATE_REASONING_EFFORT")
    if (
        model == "google/gemini-3.5-flash" and effort != "high"
        or model == "openai/gpt-5.6-luna-pro"
        and "MEMPHANT_STRUCTURED_STATE_REASONING_EFFORT" in behavior
    ):
        raise ValueError("reasoning structured-extractor effort drifted")
    extractor = runtime.get("structured_extractor")
    provider_attempts = extractor.get("provider_attempts") if isinstance(extractor, dict) else None
    transient_attempts = extractor.get("transient_attempts") if isinstance(extractor, dict) else None
    transient_no_content = (
        extractor.get("transient_no_content_attempts")
        if isinstance(extractor, dict) else None
    )
    semantic_repairs = (
        extractor.get("semantic_repair_attempts") if isinstance(extractor, dict) else None
    )
    rejected_operations = (
        extractor.get("rejected_operations") if isinstance(extractor, dict) else None
    )
    unpriced_attempts = (
        extractor.get("unpriced_attempts") if isinstance(extractor, dict) else None
    )
    rejection_reasons = (
        extractor.get("rejection_reasons") if isinstance(extractor, dict) else None
    )
    if (
        not isinstance(extractor, dict)
        or type(provider_attempts) is not int or not 163 <= provider_attempts <= 489
        or extractor.get("completed_attempts") != provider_attempts
        or extractor.get("episodes") != 163
        or extractor.get("successful_episodes") != 163
        or extractor.get("successful_responses") != 163
        or extractor.get("successful_decodes") != 163
        or extractor.get("interrupted_attempts") != 0
        or extractor.get("terminal_decode_errors") != 0
        or extractor.get("terminal_rejected_operations") != 0
        or type(semantic_repairs) is not int or semantic_repairs < 0
        or type(rejected_operations) is not int or rejected_operations < semantic_repairs
        or type(unpriced_attempts) is not int or unpriced_attempts < 0
        or rejection_reasons != (
            {} if semantic_repairs == 0
            else {"evidence_grounding": rejected_operations}
        )
        or extractor.get("priced_responses") != provider_attempts - unpriced_attempts
        or transient_attempts + semantic_repairs != provider_attempts - 163
        or unpriced_attempts != transient_attempts
        or extractor.get("decode_errors") != transient_no_content
        or extractor.get("decode_outcomes") != 163 + transient_no_content + semantic_repairs
        or extractor.get("cost_status") not in {
            "all_provider_attempts_priced", "reported_cost_is_lower_bound"
        }
        or (
            extractor.get("cost_status") == "all_provider_attempts_priced"
            and transient_attempts != 0
        )
        or extractor.get("requested_model") != model
    ):
        raise ValueError("reasoning structured-extractor coverage is not exact")


def validate_reader_ledger(ledger: Any, model: str) -> None:
    if not isinstance(ledger, dict):
        raise ValueError("reasoning proof omitted the provider-attempt ledger")
    try:
        generator.validate_provider_attempt_ledger(ledger)
    except RuntimeError as error:
        raise ValueError("reasoning reader attempt coverage is not exact") from error
    attempts = ledger.get("attempts", [])
    responses = [attempt["result"]["response"] for attempt in attempts]
    expected_cost = sum(float(response["usage"]["cost"]) for response in responses)
    if (
        ledger.get("provider_attempts") != 5
        or ledger.get("priced_provider_attempts") != 5
        or ledger.get("unpriced_provider_attempts") != 0
        or len(attempts) != 5
        or ledger.get("reported_cost_usd") != expected_cost
        or ledger.get("attempts_sha256") != generator.sha256_json(attempts)
        or any(
            response.get("model") != model
            or not isinstance(response.get("provider"), str)
            or not response["provider"].strip()
            for response in responses
        )
    ):
        raise ValueError("reasoning reader attempt coverage is not exact")


def validate_artifacts(oracles: list[dict[str, Any]], answers_doc: dict[str, Any], proof: dict[str, Any]) -> dict[str, Any]:
    model = answers_doc.get("summary", {}).get("model")
    if answers_doc.get("summary", {}).get("complete") is not True:
        raise ValueError("reasoning answers are not complete")
    answers = answers_doc.get("data")
    records = proof.get("records")
    if not isinstance(answers, list) or not isinstance(records, list) or proof.get("errors") != [] or proof.get("fallback_count") != 0:
        raise ValueError("reasoning proof is incomplete or degraded")
    expected = {("weekly", "software_engineer", oracle["question_id"]) for oracle in oracles}
    run_memora_fama.verify_answers(expected, answers)
    if answers_doc["summary"].get("generation_fingerprint") != proof.get("generation_fingerprint"):
        raise ValueError("reasoning answer/proof fingerprints differ")
    validate_run_coverage(answers_doc, proof)
    validate_reader_ledger(proof.get("provider_attempt_ledger"), model)
    by_answer = {answer["question_id"]: answer for answer in answers}
    by_record = {record.get("identity", [None, None, None])[2]: record for record in records}
    if set(by_answer) != set(by_record) or len(records) != len(by_record):
        raise ValueError("reasoning proof identities do not pair exactly")
    for oracle in oracles:
        answer = by_answer[oracle["question_id"]]
        record = by_record[oracle["question_id"]]
        if (
            record.get("answer_sha256") != generator.sha256_json(answer)
            or record.get("trace_id") != answer["trace"]["trace_id"]
            or record.get("evidence_sha256") != answer["trace"]["evidence_sha256"]
            or record.get("returned_items") != len(answer["evidence"])
            or record.get("reader_metadata_sha256")
            != generator.sha256_json(record.get("reader"))
        ):
            raise ValueError(f"reasoning answer/proof record drifted: {oracle['question_id']}")
        validate_record(oracle, record, answer)
    return {"eligible": True, "group": GROUP, "task": TASK, "questions": len(oracles)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--official-repo", type=Path, required=True)
    parser.add_argument("--answers", type=Path)
    parser.add_argument("--proof", type=Path)
    parser.add_argument("--reader-screen-model", choices=generator.MODEL_CANDIDATES)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--attempt-ledger", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.reader_screen_model and (args.answers or args.proof):
        parser.error("--reader-screen-model cannot be combined with --answers/--proof")
    if args.reader_screen_model and (args.cache_dir is None or args.attempt_ledger is None):
        parser.error("--reader-screen-model requires --cache-dir and --attempt-ledger")
    if not args.reader_screen_model and (args.answers is None or args.proof is None):
        parser.error("artifact validation requires --answers and --proof")
    try:
        oracles, oracle_source_sha256 = official_oracles(args.official_repo)
        if args.reader_screen_model:
            generator.configure_model(args.reader_screen_model)
            fingerprint = generator.sha256_json({
                "model": args.reader_screen_model,
                "oracle_source_sha256": oracle_source_sha256,
                "oracle_packs": [
                    {
                        "question_id": oracle["question_id"],
                        "question": oracle["question"],
                        "question_date": oracle["question_date"],
                        "evidence": oracle_reader_evidence(oracle),
                    }
                    for oracle in oracles
                ],
                "prompt": generator.PROMPT_TEMPLATE,
                "system": generator.SYSTEM_PROMPT,
            })
            args.attempt_ledger.parent.mkdir(parents=True, exist_ok=True)
            if args.attempt_ledger.exists():
                raise ValueError("reader screen attempt ledger already exists")
            ledger = generator.ProviderAttemptLedger(
                args.attempt_ledger,
                generator.sha256_file(generator.GENERATION_LOCK),
                "memora-reasoning-screen",
                200_000_000_000,
                4_258_002_400,
            )
            generator.validate_reader_cache_contract(
                args.cache_dir, ledger, ledger_existed=False
            )
            reader = generator.OpenRouterReader(args.cache_dir, len(oracles))
            reader.set_attempt_ledger(ledger)
            report = run_oracle_reader_screen(oracles, reader)
            snapshot = ledger.snapshot()
            validate_reader_ledger(snapshot, args.reader_screen_model)
            report |= {
                "oracle_source_sha256": oracle_source_sha256,
                "generation_fingerprint": fingerprint,
                "provider_attempt_ledger": snapshot,
                "reported_cost_usd": snapshot["reported_cost_usd"],
            }
        else:
            answers = json.loads(args.answers.read_text(encoding="utf-8"))
            proof = json.loads(args.proof.read_text(encoding="utf-8"))
            report = validate_artifacts(oracles, answers, proof) | {
                "oracle_source_sha256": oracle_source_sha256,
                "answers_sha256": run_memora_fama.sha256_file(args.answers),
                "proof_sha256": run_memora_fama.sha256_file(args.proof),
            }
    except Exception as error:
        report = {"eligible": False, "group": GROUP, "task": TASK, "error": str(error)}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    generator.run_reader.atomic_write_json(args.out, report)
    return 0 if report["eligible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
