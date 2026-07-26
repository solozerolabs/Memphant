use std::collections::{BTreeMap, BTreeSet};
use std::fs::File;
use std::io::{BufRead, BufReader};
use std::path::Path;
use std::process::ExitCode;
use std::sync::{Arc, Mutex};

use memphant_core::StructuredSourceKind;
use memphant_core::service::structured_state_slices_for_resource;
use memphant_runtime::{
    load_structured_state_prompt, load_structured_state_tokenizer, plan_structured_state_batches,
    plan_structured_state_request_with_tokenizer, structured_state_provider_from_env,
};
use serde::{Deserialize, Serialize};
use serde_json::json;
use sha2::{Digest, Sha256};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CensusResource {
    source_body: String,
    #[serde(default = "one")]
    uses: u64,
}

#[derive(Clone, Serialize)]
struct CensusPlan {
    extraction_key: String,
    request_sha256: String,
    per_attempt_reservation_nanos: u64,
    requested_model: String,
    maximum_attempts: usize,
    source_kind: StructuredSourceKind,
    source_body_sha256: String,
    batch_index: usize,
    evidence_slices_sha256: String,
}

#[derive(Clone, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct AuthorizedPlan {
    extraction_key: String,
    request_sha256: String,
    per_attempt_reservation_nanos: u64,
    requested_model: String,
    maximum_attempts: usize,
    source_kind: StructuredSourceKind,
    source_body_sha256: String,
    batch_index: usize,
    evidence_slices_sha256: String,
}

fn one() -> u64 {
    1
}

pub fn run(args: &[String]) -> ExitCode {
    let result = if args.first().map(String::as_str) == Some("execute") {
        execute(args)
    } else {
        census(args)
    };
    match result {
        Ok(value) => {
            println!(
                "{}",
                serde_json::to_string_pretty(&value).expect("census JSON")
            );
            ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("structured_state_census=error");
            eprintln!("{error}");
            ExitCode::from(2)
        }
    }
}

fn execute(args: &[String]) -> Result<serde_json::Value, String> {
    let flags = flags(&args[1..])?;
    let input_path = required(&flags, "input-jsonl")?;
    let plans_path = required(&flags, "allowed-plans-json")?;
    let workers = positive_u64(required(&flags, "max-workers")?)? as usize;
    if workers > 32 {
        return Err("--max-workers must be between 1 and 32".to_string());
    }
    let allowed: Vec<AuthorizedPlan> = serde_json::from_reader(
        File::open(plans_path).map_err(|error| format!("--allowed-plans-json: {error}"))?,
    )
    .map_err(|error| format!("--allowed-plans-json: {error}"))?;
    let allowed = allowed
        .into_iter()
        .map(|plan| (plan.extraction_key.clone(), plan))
        .collect::<BTreeMap<_, _>>();
    if allowed.is_empty() {
        return Err("allowed plan subset is empty".to_string());
    }
    let model = std::env::var("MEMPHANT_STRUCTURED_STATE_MODEL")
        .map_err(|_| "MEMPHANT_STRUCTURED_STATE_MODEL is required".to_string())?;
    let prompt = load_structured_state_prompt(Path::new(
        &std::env::var("MEMPHANT_STRUCTURED_STATE_PROMPT_PATH")
            .map_err(|_| "MEMPHANT_STRUCTURED_STATE_PROMPT_PATH is required".to_string())?,
    ))?;
    let input_price = positive_u64(
        &std::env::var("MEMPHANT_STRUCTURED_STATE_INPUT_PRICE_NANOS_PER_MILLION")
            .map_err(|_| "structured-state input price is required".to_string())?,
    )?;
    let output_price = positive_u64(
        &std::env::var("MEMPHANT_STRUCTURED_STATE_OUTPUT_PRICE_NANOS_PER_MILLION")
            .map_err(|_| "structured-state output price is required".to_string())?,
    )?;
    let reasoning_mode = std::env::var("MEMPHANT_STRUCTURED_STATE_REASONING_MODE").ok();
    let tokenizer = match (
        std::env::var("MEMPHANT_STRUCTURED_STATE_TOKENIZER_PATH").ok(),
        std::env::var("MEMPHANT_STRUCTURED_STATE_TOKENIZER_CONFIG_PATH").ok(),
    ) {
        (Some(path), Some(config)) => Some(load_structured_state_tokenizer(
            Path::new(&path),
            Path::new(&config),
        )?),
        (None, None) => None,
        _ => return Err("structured-state tokenizer paths must be supplied together".to_string()),
    };
    let mut selected = BTreeMap::new();
    let input = File::open(input_path).map_err(|error| format!("--input-jsonl: {error}"))?;
    for (index, line) in BufReader::new(input).lines().enumerate() {
        let line = line.map_err(|error| format!("input line {}: {error}", index + 1))?;
        if line.trim().is_empty() {
            continue;
        }
        let row: CensusResource = serde_json::from_str(&line)
            .map_err(|error| format!("input line {}: {error}", index + 1))?;
        let source_body_sha256 = sha256(row.source_body.as_bytes());
        let requests = plan_structured_state_batches(
            StructuredSourceKind::Resource,
            &source_body_sha256,
            structured_state_slices_for_resource(&row.source_body)
                .map_err(|error| format!("input line {}: {error}", index + 1))?,
            &model,
            &prompt,
            reasoning_mode.as_deref(),
            input_price,
            output_price,
            tokenizer.as_ref(),
        )
        .map_err(|error| format!("input line {}: {error}", index + 1))?;
        for request in requests {
            let plan = plan_structured_state_request_with_tokenizer(
                &request,
                &model,
                &prompt,
                reasoning_mode.as_deref(),
                input_price,
                output_price,
                tokenizer.as_ref(),
            )
            .map_err(|error| format!("input line {}: {error}", index + 1))?;
            let Some(authority) = allowed.get(&plan.extraction_key) else {
                continue;
            };
            let actual = AuthorizedPlan {
                extraction_key: plan.extraction_key.clone(),
                request_sha256: plan.request_sha256,
                per_attempt_reservation_nanos: plan.per_attempt_reservation_nanos,
                requested_model: model.clone(),
                maximum_attempts: plan.maximum_attempts,
                source_kind: request.source_kind,
                source_body_sha256: request.source_body_sha256.clone(),
                batch_index: request.batch_index,
                evidence_slices_sha256: sha256(
                    serde_json::to_vec(
                        &serde_json::to_value(&request.evidence_slices)
                            .expect("evidence slices serialize"),
                    )
                    .expect("evidence slices serialize")
                    .as_slice(),
                ),
            };
            if &actual != authority {
                return Err("emitted construction plan differs from frozen authority".to_string());
            }
            selected.insert(plan.extraction_key, request);
        }
    }
    if selected.len() != allowed.len() {
        return Err(
            "allowed construction subset is not exactly present in census input".to_string(),
        );
    }
    let provider = structured_state_provider_from_env()?
        .ok_or_else(|| "MEMPHANT_STRUCTURED_STATE=on is required".to_string())?;
    let queue = Arc::new(Mutex::new(selected.into_values().collect::<Vec<_>>()));
    let failures = Arc::new(Mutex::new(Vec::new()));
    std::thread::scope(|scope| {
        for _ in 0..workers.min(allowed.len()) {
            let queue = Arc::clone(&queue);
            let failures = Arc::clone(&failures);
            let provider = Arc::clone(&provider);
            scope.spawn(move || {
                let runtime = tokio::runtime::Builder::new_current_thread()
                    .enable_all()
                    .build()
                    .expect("structured-state execute runtime");
                loop {
                    let request = queue.lock().unwrap().pop();
                    let Some(request) = request else { break };
                    if let Err(error) = runtime.block_on(provider.extract(&request)) {
                        failures.lock().unwrap().push(error.to_string());
                    }
                }
            });
        }
    });
    let failures = failures.lock().unwrap();
    if !failures.is_empty() {
        return Err(format!(
            "structured-state execute failed {} plans: {}",
            failures.len(),
            failures[0]
        ));
    }
    Ok(json!({
        "schema_version": 1,
        "executed_plan_count": allowed.len(),
        "allowed_plans_sha256": sha256(
            serde_json::to_vec(&allowed.values().collect::<Vec<_>>())
                .expect("allowed plans serialize")
                .as_slice()
        ),
        "maximum_workers": workers,
        "hidden_retries": 0,
    }))
}

fn census(args: &[String]) -> Result<serde_json::Value, String> {
    if args.first().map(String::as_str) != Some("census") {
        return Err("usage: memphant structured-state census --input-jsonl <PATH> --model <ID> --prompt-file <PATH> --input-price-nanos-per-million <N> --output-price-nanos-per-million <N> [--reasoning-mode <MODE>] [--tokenizer-file <PATH> --tokenizer-config-file <PATH>]".to_string());
    }
    let flags = flags(&args[1..])?;
    let input_path = required(&flags, "input-jsonl")?;
    let model = required(&flags, "model")?;
    let prompt_path = required(&flags, "prompt-file")?;
    let input_price = positive_u64(required(&flags, "input-price-nanos-per-million")?)?;
    let output_price = positive_u64(required(&flags, "output-price-nanos-per-million")?)?;
    let reasoning_mode = flags.get("reasoning-mode").map(String::as_str);
    let prompt = load_structured_state_prompt(Path::new(prompt_path))?;
    let tokenizer = match (
        flags.get("tokenizer-file"),
        flags.get("tokenizer-config-file"),
    ) {
        (Some(tokenizer), Some(config)) => Some(load_structured_state_tokenizer(
            Path::new(tokenizer),
            Path::new(config),
        )?),
        (None, None) => None,
        _ => {
            return Err(
                "tokenizer file and tokenizer config file must be supplied together".to_string(),
            );
        }
    };
    let input = File::open(input_path).map_err(|error| format!("--input-jsonl: {error}"))?;
    let mut input_hasher = Sha256::new();
    let mut source_hashes = BTreeSet::new();
    let mut extraction_keys = BTreeSet::new();
    let mut plan_inventory = BTreeMap::new();
    let mut resource_uses = 0_u64;
    let mut planned_requests = 0_u64;
    let mut maximum_request_bytes = 0_u64;
    let mut maximum_input_reservation_units = 0_u64;
    let mut maximum_per_attempt_reservation_nanos = 0_u64;
    let mut maximum_retry_reservation_nanos = 0_u64;
    let mut first_attempt_liability_nanos = 0_u64;
    let mut construction_liability_nanos = 0_u64;
    let mut maximum_attempts = 0_usize;
    let mut processed_plans = 0_u64;

    for (index, line) in BufReader::new(input).lines().enumerate() {
        let line = line.map_err(|error| format!("input line {}: {error}", index + 1))?;
        if line.trim().is_empty() {
            continue;
        }
        input_hasher.update(line.as_bytes());
        input_hasher.update(b"\n");
        let row: CensusResource = serde_json::from_str(&line)
            .map_err(|error| format!("input line {}: {error}", index + 1))?;
        if row.source_body.is_empty() {
            return Err(format!("input line {}: source_body is empty", index + 1));
        }
        if row.uses == 0 {
            return Err(format!("input line {}: uses must be positive", index + 1));
        }
        resource_uses = resource_uses
            .checked_add(row.uses)
            .ok_or("resource use count overflow")?;
        let source_body_sha256 = sha256(row.source_body.as_bytes());
        source_hashes.insert(source_body_sha256.clone());
        let batches = plan_structured_state_batches(
            StructuredSourceKind::Resource,
            &source_body_sha256,
            structured_state_slices_for_resource(&row.source_body)
                .map_err(|error| format!("input line {}: {error}", index + 1))?,
            model,
            &prompt,
            reasoning_mode,
            input_price,
            output_price,
            tokenizer.as_ref(),
        )
        .map_err(|error| format!("input line {}: {error}", index + 1))?;
        for request in batches {
            planned_requests = planned_requests
                .checked_add(row.uses)
                .ok_or("planned request count overflow")?;
            let plan = plan_structured_state_request_with_tokenizer(
                &request,
                model,
                &prompt,
                reasoning_mode,
                input_price,
                output_price,
                tokenizer.as_ref(),
            )
            .map_err(|error| {
                format!(
                    "input line {} batch {}: {error}",
                    index + 1,
                    request.batch_index
                )
            })?;
            processed_plans = processed_plans
                .checked_add(1)
                .ok_or("processed plan count overflow")?;
            if processed_plans.is_multiple_of(10_000) {
                eprintln!(
                    "structured_state_census_progress plans={processed_plans} input_rows={}",
                    index + 1
                );
            }
            maximum_request_bytes = maximum_request_bytes.max(plan.serialized_request.len() as u64);
            maximum_input_reservation_units =
                maximum_input_reservation_units.max(plan.input_reservation_units);
            maximum_per_attempt_reservation_nanos =
                maximum_per_attempt_reservation_nanos.max(plan.per_attempt_reservation_nanos);
            maximum_retry_reservation_nanos =
                maximum_retry_reservation_nanos.max(plan.maximum_reservation_nanos);
            maximum_attempts = maximum_attempts.max(plan.maximum_attempts);
            if extraction_keys.insert(plan.extraction_key.clone()) {
                plan_inventory.insert(
                    plan.extraction_key.clone(),
                    CensusPlan {
                        extraction_key: plan.extraction_key,
                        request_sha256: plan.request_sha256,
                        per_attempt_reservation_nanos: plan.per_attempt_reservation_nanos,
                        requested_model: model.to_string(),
                        maximum_attempts: plan.maximum_attempts,
                        source_kind: request.source_kind,
                        source_body_sha256: request.source_body_sha256.clone(),
                        batch_index: request.batch_index,
                        evidence_slices_sha256: sha256(
                            serde_json::to_vec(
                                &serde_json::to_value(&request.evidence_slices)
                                    .expect("census evidence slices serialize"),
                            )
                            .expect("census evidence slices serialize")
                            .as_slice(),
                        ),
                    },
                );
                first_attempt_liability_nanos = first_attempt_liability_nanos
                    .checked_add(plan.per_attempt_reservation_nanos)
                    .ok_or("first-attempt construction liability overflow")?;
                construction_liability_nanos = construction_liability_nanos
                    .checked_add(plan.maximum_reservation_nanos)
                    .ok_or("construction liability overflow")?;
            }
        }
    }
    if resource_uses == 0 {
        return Err("census input contains no resources".to_string());
    }
    let plan_inventory = plan_inventory.into_values().collect::<Vec<_>>();
    let plan_inventory_json =
        serde_json::to_value(&plan_inventory).expect("census plan inventory serializes");
    let plan_inventory_sha256 = sha256(
        serde_json::to_vec(&plan_inventory_json)
            .expect("census plan inventory serializes")
            .as_slice(),
    );
    Ok(json!({
        "schema_version": 1,
        "input_manifest_sha256": format!("{:x}", input_hasher.finalize()),
        "resource_uses": resource_uses,
        "unique_source_bodies": source_hashes.len(),
        "planned_requests": planned_requests,
        "unique_extraction_keys": extraction_keys.len(),
        "processed_plans": processed_plans,
        "maximum_request_bytes": maximum_request_bytes,
        "maximum_input_reservation_units": maximum_input_reservation_units,
        "maximum_per_attempt_reservation_nanos": maximum_per_attempt_reservation_nanos,
        "maximum_retry_reservation_nanos": maximum_retry_reservation_nanos,
        "maximum_attempts": maximum_attempts,
        "first_attempt_liability_nanos": first_attempt_liability_nanos,
        "full_three_wave_liability_nanos": construction_liability_nanos,
        "construction_liability_nanos": construction_liability_nanos,
        "plan_inventory_sha256": plan_inventory_sha256,
        "plan_inventory": plan_inventory_json,
        "tokenizer_bound": tokenizer.is_some(),
    }))
}

fn flags(args: &[String]) -> Result<BTreeMap<String, String>, String> {
    let mut result = BTreeMap::new();
    let mut chunks = args.chunks_exact(2);
    for pair in &mut chunks {
        let Some(name) = pair[0].strip_prefix("--") else {
            return Err(format!("unexpected argument: {}", pair[0]));
        };
        if name.is_empty()
            || pair[1].starts_with("--")
            || result.insert(name.to_string(), pair[1].clone()).is_some()
        {
            return Err(format!("invalid or duplicate flag: {}", pair[0]));
        }
    }
    if !chunks.remainder().is_empty() {
        return Err("every census flag requires a value".to_string());
    }
    Ok(result)
}

fn required<'a>(flags: &'a BTreeMap<String, String>, name: &str) -> Result<&'a str, String> {
    flags
        .get(name)
        .map(String::as_str)
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| format!("missing required flag --{name}"))
}

fn positive_u64(value: &str) -> Result<u64, String> {
    value
        .parse::<u64>()
        .ok()
        .filter(|value| *value > 0)
        .ok_or_else(|| format!("expected a positive integer, got {value:?}"))
}

fn sha256(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
