"""Canonical authority and receipt validation for state-aware construction.

This module is deliberately dependency-free so both the campaign controller and
the official benchmark adapter execute the same validation code.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_CEILING
import hashlib
import json
from pathlib import Path
import re
from typing import Any


SHA256 = re.compile(r"[0-9a-f]{64}")


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    ).encode("utf-8")


def _compact_json_exponents(encoded: str) -> str:
    """Match serde_json's exponent spelling without touching JSON strings."""
    output: list[str] = []
    in_string = False
    escaped = False
    cursor = 0
    while cursor < len(encoded):
        character = encoded[cursor]
        if in_string:
            output.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            cursor += 1
            continue
        if character == '"':
            in_string = True
            output.append(character)
            cursor += 1
            continue
        if character == "e" and cursor + 2 < len(encoded):
            sign = encoded[cursor + 1]
            if sign in "+-":
                digits = cursor + 2
                while digits + 1 < len(encoded) and encoded[digits] == "0":
                    digits += 1
                if encoded[digits].isdigit():
                    output.extend(("e", sign))
                    cursor = digits
                    continue
        output.append(character)
        cursor += 1
    return "".join(output)


def rust_json(value: object) -> bytes:
    encoded = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    # serde_json's finite-number encoder emits compact exponent digits (for
    # example ``1.75e-6``), while CPython emits ``1.75e-06``. Campaign hashes
    # use the exact Rust spelling and reject numbers serde_json cannot encode.
    return _compact_json_exponents(encoded).encode("utf-8")


def sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def sha256_rust_json(value: object) -> str:
    return hashlib.sha256(rust_json(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def valid_sha256(value: object) -> bool:
    return isinstance(value, str) and SHA256.fullmatch(value) is not None


def path_sha256(path: Path) -> str:
    return sha256_file(path) if path.is_file() else hashlib.sha256(b"").hexdigest()


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"canonical {label} is unreadable") from error
    require(isinstance(value, dict), f"canonical {label} is malformed")
    return value


def _authorization_scope(packet: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in packet.items()
        if key not in {"schema_version", "status", "authorization"}
    }


def load_canonical_binding(
    binding_path: Path,
    *,
    authorization_path: Path,
    census_path: Path,
    manifest_path: Path,
    wave_path: Path,
    binding_root: Path,
) -> dict[str, Any]:
    """Load a create-only binding and prove its complete canonical hash chain."""
    paths = {
        "authorization_path": authorization_path.resolve(),
        "census_path": census_path.resolve(),
        "manifest_path": manifest_path.resolve(),
        "wave_path": wave_path.resolve(),
        "binding_root": binding_root.resolve(),
    }
    binding_path = binding_path.resolve()
    value = _load_json(binding_path, "construction binding")
    require(
        set(value)
        == {
            "schema_version",
            "authority",
            "authorization",
            "selection",
            "compiler",
            "provider",
            "cache",
            "ledger",
            "coverage",
            "binding_sha256",
        }
        and value.get("schema_version") == 1,
        "construction binding canonical authority contract drift",
    )
    core = {key: item for key, item in value.items() if key != "binding_sha256"}
    require(
        value.get("binding_sha256") == sha256_json(core),
        "construction binding canonical authority hash drift",
    )
    authority = value.get("authority")
    require(
        isinstance(authority, dict)
        and set(authority)
        == {
            "authorization_path",
            "authorization_file_sha256",
            "authorization_scope_sha256",
            "census_path",
            "census_file_sha256",
            "census_sha256",
            "manifest_path",
            "manifest_sha256",
            "wave_path",
            "wave_file_sha256",
            "wave_sha256",
            "plan_inventory_sha256",
            "plan_subset_sha256",
            "canonical_artifact_paths_sha256",
            "binding_path",
        },
        "construction binding canonical authority is malformed",
    )
    for name in ("authorization_path", "census_path", "manifest_path", "wave_path"):
        require(
            Path(str(authority.get(name))).resolve() == paths[name],
            "construction binding canonical authority path drift",
        )
    coverage = value.get("coverage")
    plans = coverage.get("plans") if isinstance(coverage, dict) else None
    keys = coverage.get("expected_extraction_keys") if isinstance(coverage, dict) else None
    require(
        isinstance(plans, list)
        and plans
        and all(isinstance(plan, dict) for plan in plans)
        and isinstance(keys, list)
        and keys == sorted(keys)
        and len(keys) == len(set(keys))
        and all(valid_sha256(key) for key in keys)
        and keys == sorted(plan.get("extraction_key") for plan in plans)
        and coverage.get("expected_extraction_keys_sha256") == sha256_json(keys)
        and authority.get("plan_subset_sha256") == sha256_json(plans),
        "construction binding canonical authority plan subset drift",
    )
    expected_binding_path = paths["binding_root"] / f"{authority['plan_subset_sha256']}.json"
    require(
        binding_path == expected_binding_path.resolve()
        and Path(str(authority.get("binding_path"))).resolve() == binding_path,
        "construction binding canonical authority path drift",
    )

    packet = _load_json(paths["authorization_path"], "authorization")
    census = _load_json(paths["census_path"], "census")
    manifest = _load_json(paths["manifest_path"], "manifest")
    wave = _load_json(paths["wave_path"], "construction wave")
    scope = _authorization_scope(packet)
    authorization_sha256 = packet.get("authorization", {}).get(
        "authorization_scope_sha256"
    )
    require(
        packet.get("status") == "AUTHORIZED_STATE_MEMORY_CAMPAIGN"
        and valid_sha256(authorization_sha256)
        and authorization_sha256 == sha256_json(scope)
        and authority.get("authorization_scope_sha256") == authorization_sha256
        and authority.get("authorization_file_sha256")
        == sha256_file(paths["authorization_path"]),
        "construction binding canonical authorization drift",
    )
    census_core = {
        key: item for key, item in census.items() if key != "census_sha256"
    }
    require(
        census.get("census_sha256") == sha256_json(census_core)
        and authority.get("census_sha256") == census["census_sha256"]
        and authority.get("census_file_sha256") == sha256_file(paths["census_path"])
        and authority.get("manifest_sha256") == sha256_file(paths["manifest_path"]),
        "construction binding canonical census or manifest drift",
    )
    packet_inputs = packet.get("inputs", {})
    require(
        packet_inputs.get("census_sha256") == census["census_sha256"]
        and packet_inputs.get("census_file_sha256") == sha256_file(paths["census_path"])
        and packet_inputs.get("manifest_sha256") == sha256_file(paths["manifest_path"]),
        "construction binding authorization input chain drift",
    )
    inventory = census.get("construction", {}).get("plan_inventory")
    inventory_sha256 = census.get("construction", {}).get("plan_inventory_sha256")
    require(
        isinstance(inventory, list)
        and inventory_sha256 == sha256_json(inventory)
        and authority.get("plan_inventory_sha256") == inventory_sha256,
        "construction binding canonical inventory drift",
    )
    inventory_by_key = {plan.get("extraction_key"): plan for plan in inventory}
    require(
        len(inventory_by_key) == len(inventory)
        and all(inventory_by_key.get(plan.get("extraction_key")) == plan for plan in plans),
        "construction binding plan subset is not in the frozen inventory",
    )
    wave_core = {
        key: item
        for key, item in wave.items()
        if key not in {"wave_sha256", "ledger_request_key"}
    }
    require(
        wave.get("wave_sha256") == sha256_json(wave_core)
        and authority.get("wave_sha256") == wave["wave_sha256"]
        and authority.get("wave_file_sha256") == sha256_file(paths["wave_path"])
        and wave.get("campaign_census_sha256") == census["census_sha256"]
        and wave.get("ordered_plans_sha256") == inventory_sha256
        and wave.get("plans") == inventory,
        "construction binding canonical wave drift",
    )
    artifacts = packet.get("artifacts")
    require(
        isinstance(artifacts, dict)
        and authority.get("canonical_artifact_paths_sha256") == sha256_json(artifacts),
        "construction binding canonical artifact paths drift",
    )
    cache = value.get("cache")
    ledger = value.get("ledger")
    provider = value.get("provider")
    construction = manifest.get("construction", {})
    selection = value.get("selection")
    compiler = value.get("compiler")
    runtime_path = "crates/memphant-runtime/src/structured_state_openrouter.rs"
    provider_code_sha256 = construction.get("code_sha256s", {}).get(runtime_path)
    schema_authority = {
        "construction_identity_sha256": census.get("construction", {}).get(
            "construction_identity_sha256"
        ),
        "provider_code_sha256": provider_code_sha256,
        "contract": "structured-state-response-schema-v1",
    }
    require(
        isinstance(selection, dict)
        and set(selection)
        == {"selection_sha256", "input_manifest_sha256", "state_mode"}
        and selection.get("selection_sha256") == authority["plan_subset_sha256"]
        and selection.get("input_manifest_sha256")
        == census.get("construction", {}).get("input_manifest_sha256")
        and selection.get("state_mode") == construction.get("state_mode"),
        "construction binding canonical selection drift",
    )
    require(
        isinstance(compiler, dict)
        and set(compiler)
        == {"prompt_sha256", "schema_sha256", "provider_code_sha256"}
        and compiler.get("prompt_sha256") == construction.get("prompt_sha256")
        and valid_sha256(provider_code_sha256)
        and compiler.get("provider_code_sha256") == provider_code_sha256
        and compiler.get("schema_sha256") == sha256_json(schema_authority),
        "construction binding canonical compiler drift",
    )
    require(
        isinstance(cache, dict)
        and Path(str(cache.get("observation_cache_path"))).resolve()
        == Path(artifacts["observation_cache"]).resolve()
        and Path(str(cache.get("source_receipts_path"))).resolve().is_relative_to(
            Path(artifacts["cache_hits"]).resolve()
        )
        and cache.get("namespace") == packet.get("execution", {}).get("cache_namespace"),
        "construction binding canonical cache path drift",
    )
    require(
        isinstance(ledger, dict)
        and Path(str(ledger.get("subledger_path"))).resolve()
        == Path(artifacts["construction_subledger"]).resolve()
        and Path(str(ledger.get("campaign_journal_path"))).resolve()
        == Path(artifacts["journal"]).resolve()
        and type(ledger.get("source_ledger_prefix_bytes")) is int
        and ledger["source_ledger_prefix_bytes"] >= 0
        and valid_sha256(ledger.get("source_ledger_prefix_sha256"))
        and valid_sha256(ledger.get("before_event_sha256"))
        and valid_sha256(ledger.get("campaign_journal_sha256")),
        "construction binding canonical ledger path drift",
    )
    subledger = Path(ledger["subledger_path"])
    body = subledger.read_bytes() if subledger.is_file() else b""
    prefix_bytes = ledger["source_ledger_prefix_bytes"]
    require(
        len(body) >= prefix_bytes
        and hashlib.sha256(body[:prefix_bytes]).hexdigest()
        == ledger["source_ledger_prefix_sha256"]
        and ledger["before_event_sha256"] == ledger["source_ledger_prefix_sha256"]
        and path_sha256(Path(ledger["campaign_journal_path"]))
        == ledger["campaign_journal_sha256"],
        "construction binding canonical pre-worker ledger identity drift",
    )
    require(
        isinstance(provider, dict)
        and set(provider)
        == {
            "requested_model",
            "served_model",
            "requested_provider",
            "served_provider",
            "input_price_nanos_per_million",
            "output_price_nanos_per_million",
            "maximum_output_tokens",
            "maximum_attempts",
        }
        and provider.get("requested_model") == construction.get("model")
        and provider.get("served_model") == construction.get("response_model")
        and str(provider.get("requested_provider", "")).casefold()
        == str(construction.get("provider", "")).casefold()
        and str(provider.get("served_provider", "")).casefold()
        == str(construction.get("provider", "")).casefold()
        and provider.get("input_price_nanos_per_million")
        == construction.get("input_price_nanos_per_million")
        and provider.get("output_price_nanos_per_million")
        == construction.get("output_price_nanos_per_million")
        and provider.get("maximum_output_tokens")
        == construction.get("maximum_output_tokens")
        and provider.get("maximum_attempts") == construction.get("maximum_attempts"),
        "construction binding canonical provider route drift",
    )
    authorization = value.get("authorization")
    require(
        isinstance(authorization, dict)
        and authorization.get("authorization_sha256") == authorization_sha256
        and authorization.get("campaign_sha256") == census["census_sha256"],
        "construction binding proof authorization drift",
    )
    return value


def _ledger_events(binding: dict[str, Any]) -> list[dict[str, Any]]:
    ledger = binding["ledger"]
    body = Path(ledger["subledger_path"]).read_bytes()
    prefix = body[: ledger["source_ledger_prefix_bytes"]]
    try:
        events = [json.loads(line) for line in prefix.decode("utf-8").splitlines() if line]
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("construction source ledger prefix is malformed") from error
    require(all(isinstance(event, dict) for event in events), "construction source ledger event is malformed")
    return events


def _event_hash(event: dict[str, Any]) -> str:
    return sha256_rust_json(event)


def validate_cache_receipt(
    receipt: object,
    *,
    binding: dict[str, Any],
    plan: dict[str, Any],
    source_events: list[dict[str, Any]],
) -> dict[str, Any]:
    core = receipt.get("core") if isinstance(receipt, dict) else None
    provider = binding["provider"]
    authorization = binding["authorization"]
    cache = binding["cache"]
    require(
        isinstance(core, dict)
        and receipt.get("cache_hit_sha256") == sha256_rust_json(core)
        and core.get("schema_version") == 1
        and core.get("authorization_sha256") == authorization["authorization_sha256"]
        and core.get("campaign_sha256") == authorization["campaign_sha256"]
        and core.get("cache_namespace") == cache["namespace"]
        and core.get("reservation_status") == "cache_hit"
        and core.get("settled_nanos") == 0
        and core.get("extraction_key") == plan.get("extraction_key")
        and core.get("request_sha256") == plan.get("request_sha256")
        and core.get("source_kind") == plan.get("source_kind")
        and core.get("source_body_sha256") == plan.get("source_body_sha256")
        and core.get("batch_index") == plan.get("batch_index")
        and core.get("evidence_slices_sha256") == plan.get("evidence_slices_sha256")
        and core.get("requested_model") == provider["requested_model"]
        and core.get("served_model") == provider["served_model"]
        and str(core.get("served_provider", "")).casefold()
        == str(provider["served_provider"]).casefold(),
        "construction cache-hit exact authority drift",
    )
    started = [
        event
        for event in source_events
        if _event_hash(event) == core.get("source_started_event_sha256")
    ]
    results = [
        event
        for event in source_events
        if _event_hash(event) == core.get("source_result_event_sha256")
    ]
    require(
        len(started) == len(results) == 1,
        "construction cache-hit paid source is absent or ambiguous",
    )
    start, result = started[0], results[0]
    shared = {
        "attempt_id": core.get("source_attempt_id"),
        "extraction_key": core.get("extraction_key"),
        "request_sha256": core.get("request_sha256"),
        "source_kind": core.get("source_kind"),
        "source_body_sha256": core.get("source_body_sha256"),
        "batch_index": core.get("batch_index"),
        "requested_model": core.get("requested_model"),
    }
    require(
        all(start.get(key) == value and result.get(key) == value for key, value in shared.items())
        and start.get("event") == "started"
        and result.get("event") == "result"
        and start.get("attempt") == result.get("attempt") == 1
        and start.get("campaign_attempt") == result.get("campaign_attempt")
        and start.get("maximum_attempts") == result.get("maximum_attempts")
        and start.get("per_attempt_reservation_nanos")
        == result.get("per_attempt_reservation_nanos")
        and result.get("reservation_status") == "settled"
        and result.get("parse_status") == "decoded"
        and result.get("error") is None
        and result.get("served_model") == provider["served_model"]
        and str(result.get("served_provider", "")).casefold()
        == str(provider["served_provider"]).casefold()
        and result.get("response_id") == core.get("response_id")
        and result.get("result_sha256") == core.get("provider_result_sha256")
        and result.get("observation_count") == core.get("observation_count")
        and result.get("observation_sha256") == core.get("observation_sha256"),
        "construction cache-hit paid-source provenance drift",
    )
    slices = core.get("evidence_slices")
    observations = core.get("observations")
    slice_bodies = {
        item.get("id"): item.get("body")
        for item in slices or []
        if isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and isinstance(item.get("body"), str)
    }
    require(
        isinstance(slices, list)
        and len(slice_bodies) == len(slices)
        and sha256_rust_json(slices) == core.get("evidence_slices_sha256")
        and isinstance(observations, list)
        and len(observations) == core.get("observation_count")
        and sha256_rust_json(observations) == core.get("observation_sha256")
        and all(
            isinstance(observation, dict)
            and isinstance(observation.get("evidence_quote"), str)
            and bool(observation["evidence_quote"])
            and observation.get("evidence_slice_id") in slice_bodies
            and observation["evidence_quote"]
            in slice_bodies[observation["evidence_slice_id"]]
            for observation in observations
        ),
        "construction cache-hit observation grounding drift",
    )
    return core


def derive_construction_receipts(
    binding: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Derive proof-v2 cache/ledger sections through one exact union validator."""
    plans = binding["coverage"]["plans"]
    planned = {plan["extraction_key"]: plan for plan in plans}
    expected = set(planned)
    events = _ledger_events(binding)
    started: dict[tuple[str, int], dict[str, Any]] = {}
    results: dict[tuple[str, int], dict[str, Any]] = {}
    for event in events:
        key = event.get("extraction_key")
        if key not in expected:
            continue
        identity = (key, event.get("campaign_attempt"))
        require(event.get("event") in {"started", "result"}, "construction subledger event is malformed")
        target = started if event["event"] == "started" else results
        require(identity not in target, "construction subledger duplicates one attempt")
        target[identity] = event
    for identity, result in results.items():
        require(
            identity in started
            and started[identity].get("attempt_id") == result.get("attempt_id")
            and result.get("requested_model") == binding["provider"]["requested_model"],
            "construction subledger start/result identity drift",
        )
    receipt_root = Path(binding["cache"]["source_receipts_path"])
    cache_receipts: dict[str, dict[str, Any]] = {}
    receipt_hashes = []
    for key in sorted(expected):
        path = receipt_root / f"{key}.json"
        if not path.is_file():
            continue
        receipt = _load_json(path, "construction cache-hit receipt")
        cache_receipts[key] = validate_cache_receipt(
            receipt,
            binding=binding,
            plan=planned[key],
            source_events=events,
        )
        receipt_hashes.append({"extraction_key": key, "sha256": sha256_file(path)})
    decoded: dict[str, dict[str, Any]] = {}
    unresolved_nanos = 0
    for (key, _), result in sorted(results.items()):
        if (
            result.get("reservation_status") == "settled"
            and result.get("parse_status") == "decoded"
            and result.get("error") is None
            and result.get("served_model") == binding["provider"]["served_model"]
            and str(result.get("served_provider", "")).casefold()
            == str(binding["provider"]["served_provider"]).casefold()
        ):
            decoded[key] = result
        elif result.get("reservation_status") != "not_charged":
            unresolved_nanos += int(result.get("per_attempt_reservation_nanos", 0))
    require(
        set(decoded) | set(cache_receipts) == expected,
        "construction receipts lack exact extraction-key coverage",
    )
    for key in set(decoded) & set(cache_receipts):
        require(
            cache_receipts[key].get("source_result_event_sha256")
            == sha256_rust_json(decoded[key]),
            "construction cache hit does not bind its paid source result",
        )
    settled_nanos = 0
    for result in decoded.values():
        usage = result.get("usage")
        require(
            isinstance(usage, dict) and usage.get("cost") is not None,
            "construction settled usage is missing",
        )
        settled_nanos += int(
            (Decimal(str(usage["cost"])) * Decimal(1_000_000_000)).to_integral_value(
                rounding=ROUND_CEILING
            )
        )
    cache = {
        "namespace": binding["cache"]["namespace"],
        "source_receipts_sha256": sha256_json(receipt_hashes),
    }
    ledger = {
        "attempt_ids": sorted({result["attempt_id"] for result in results.values()}),
        "before_event_sha256": binding["ledger"]["before_event_sha256"],
        "after_event_sha256": path_sha256(Path(binding["ledger"]["subledger_path"])),
        "campaign_journal_sha256": path_sha256(
            Path(binding["ledger"]["campaign_journal_path"])
        ),
        "settled_nanos": settled_nanos,
        "unresolved_nanos": unresolved_nanos,
    }
    return cache, ledger
