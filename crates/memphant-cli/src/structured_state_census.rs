use std::collections::{BTreeMap, BTreeSet};
use std::fs::File;
use std::io::{BufRead, BufReader};
use std::path::Path;
use std::process::ExitCode;

use memphant_core::StructuredSourceKind;
use memphant_core::service::structured_state_slices_for_resource;
use memphant_runtime::{
    load_structured_state_prompt, load_structured_state_tokenizer, plan_structured_state_batches,
    plan_structured_state_request_with_tokenizer,
};
use serde::Deserialize;
use serde_json::json;
use sha2::{Digest, Sha256};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CensusResource {
    source_body: String,
    #[serde(default = "one")]
    uses: u64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ReaderShape {
    question_id: String,
    system_prompt: String,
    question_text: String,
    has_image: bool,
}

struct ReaderShapeProof {
    fixture_sha256: String,
    rows: u64,
    tokenizer_sha256: String,
    chat_template_sha256: String,
    maximum_nonmemory_chat_tokens: u64,
}

fn one() -> u64 {
    1
}

pub fn run(args: &[String]) -> ExitCode {
    match census(args) {
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

fn census(args: &[String]) -> Result<serde_json::Value, String> {
    if args.first().map(String::as_str) != Some("census") {
        return Err("usage: memphant structured-state census --input-jsonl <PATH> --model <ID> --prompt-file <PATH> --input-price-nanos-per-million <N> --output-price-nanos-per-million <N> [--reasoning-effort <LEVEL>] [--tokenizer-file <PATH> --tokenizer-config-file <PATH>] [--reader-input-jsonl <PATH>]".to_string());
    }
    let flags = flags(&args[1..])?;
    let input_path = required(&flags, "input-jsonl")?;
    let model = required(&flags, "model")?;
    let prompt_path = required(&flags, "prompt-file")?;
    let input_price = positive_u64(required(&flags, "input-price-nanos-per-million")?)?;
    let output_price = positive_u64(required(&flags, "output-price-nanos-per-million")?)?;
    let reasoning_effort = flags.get("reasoning-effort").map(String::as_str);
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
    let reader_shape_proof = match flags.get("reader-input-jsonl") {
        Some(path) => Some(census_reader_shapes(
            Path::new(path),
            tokenizer
                .as_ref()
                .ok_or("reader shape census requires the pinned Qwen tokenizer")?,
        )?),
        None => None,
    };
    let input = File::open(input_path).map_err(|error| format!("--input-jsonl: {error}"))?;
    let mut input_hasher = Sha256::new();
    let mut source_hashes = BTreeSet::new();
    let mut extraction_keys = BTreeSet::new();
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
            reasoning_effort,
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
                reasoning_effort,
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
            if extraction_keys.insert(plan.extraction_key) {
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
    let mut output = json!({
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
        "tokenizer_bound": tokenizer.is_some(),
    });
    if let Some(proof) = reader_shape_proof {
        let object = output
            .as_object_mut()
            .expect("static census output is an object");
        object.insert(
            "reader_shape_fixture_sha256".to_string(),
            json!(proof.fixture_sha256),
        );
        object.insert("reader_shape_rows".to_string(), json!(proof.rows));
        object.insert(
            "reader_tokenizer_sha256".to_string(),
            json!(proof.tokenizer_sha256),
        );
        object.insert(
            "reader_chat_template_sha256".to_string(),
            json!(proof.chat_template_sha256),
        );
        object.insert(
            "reader_maximum_nonmemory_chat_tokens".to_string(),
            json!(proof.maximum_nonmemory_chat_tokens),
        );
    }
    Ok(output)
}

fn census_reader_shapes(
    path: &Path,
    tokenizer: &memphant_runtime::StructuredStateTokenizer,
) -> Result<ReaderShapeProof, String> {
    let input = File::open(path).map_err(|error| format!("--reader-input-jsonl: {error}"))?;
    let mut input_hasher = Sha256::new();
    let mut question_ids = BTreeSet::new();
    let mut rows = 0_u64;
    let mut maximum_nonmemory_chat_tokens = 0_u64;
    for (index, line) in BufReader::new(input).lines().enumerate() {
        let line = line.map_err(|error| format!("reader input line {}: {error}", index + 1))?;
        if line.trim().is_empty() {
            continue;
        }
        input_hasher.update(line.as_bytes());
        input_hasher.update(b"\n");
        let row: ReaderShape = serde_json::from_str(&line)
            .map_err(|error| format!("reader input line {}: {error}", index + 1))?;
        if row.question_id.trim().is_empty()
            || row.system_prompt.trim().is_empty()
            || row.question_text.trim().is_empty()
            || !question_ids.insert(row.question_id)
        {
            return Err(format!(
                "reader input line {} has blank content or duplicate question id",
                index + 1
            ));
        }
        rows = rows.checked_add(1).ok_or("reader shape count overflow")?;
        maximum_nonmemory_chat_tokens =
            maximum_nonmemory_chat_tokens.max(tokenizer.count_qwen_reader_chat_tokens(
                &row.system_prompt,
                &row.question_text,
                row.has_image,
            )?);
    }
    if rows == 0 {
        return Err("reader shape input contains no questions".to_string());
    }
    let (tokenizer_sha256, chat_template_sha256) = tokenizer.reader_identity();
    Ok(ReaderShapeProof {
        fixture_sha256: format!("{:x}", input_hasher.finalize()),
        rows,
        tokenizer_sha256: tokenizer_sha256.to_string(),
        chat_template_sha256: chat_template_sha256.to_string(),
        maximum_nonmemory_chat_tokens,
    })
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
