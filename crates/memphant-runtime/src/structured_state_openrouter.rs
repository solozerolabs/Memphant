use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

#[cfg(unix)]
use std::os::unix::fs::OpenOptionsExt;

use fs2::FileExt;
use memphant_core::{
    EvidenceSlice, StructuredObservation, StructuredObservationDisposition, StructuredSourceKind,
    StructuredStateProvider, StructuredStateProviderError, StructuredStateProviderIdentity,
    StructuredStateRequest, validate_structured_observation,
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use tokenizers::Tokenizer;
use ureq::Agent;

const URL: &str = "https://openrouter.ai/api/v1/chat/completions";
const GENERATION_URL: &str = "https://openrouter.ai/api/v1/generation";
const DEFAULT_MODEL: &str = "openai/gpt-5.6-luna-pro";
const FLASH_MODEL: &str = "google/gemini-3.5-flash";
const FLASH_PROVIDER: &str = "google-ai-studio";
const DEEPSEEK_MODEL: &str = "deepseek/deepseek-v4-flash";
const DEEPSEEK_PROVIDERS: [&str; 2] = ["deepinfra", "wandb"];
const LME_V2_QWEN_MODEL: &str = "qwen/qwen3.5-9b-20260310";
const LME_V2_QWEN_PROVIDER: &str = "deepinfra";
const CONTRACT_REVISION: &str = "structured-observation.v2";
// A provider call is one campaign-ledger wave attempt. Retrying inside this
// process would bypass the aggregate wave reservation and is therefore
// forbidden. The campaign may authorize up to three separately metered waves.
const MAX_ATTEMPTS: usize = 1;
const MAX_CAMPAIGN_ATTEMPTS: usize = 3;
const MAX_OUTPUT_TOKENS: u64 = 4096;
const MAX_REQUEST_BYTES: usize = 131_072;
const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
const GLOBAL_TIMEOUT: Duration = Duration::from_secs(240);
const RESPONSE_LIMIT: u64 = 4 * 1024 * 1024;
const PROMPT_PATH_ENV: &str = "MEMPHANT_STRUCTURED_STATE_PROMPT_PATH";
const LEDGER_ENV: &str = "MEMPHANT_STRUCTURED_STATE_ATTEMPT_LEDGER";
const DISPATCH_ROOT_ENV: &str = "MEMPHANT_STRUCTURED_STATE_DISPATCH_ROOT";
const INPUT_PRICE_ENV: &str = "MEMPHANT_STRUCTURED_STATE_INPUT_PRICE_NANOS_PER_MILLION";
const OUTPUT_PRICE_ENV: &str = "MEMPHANT_STRUCTURED_STATE_OUTPUT_PRICE_NANOS_PER_MILLION";
const TOKENIZER_PATH_ENV: &str = "MEMPHANT_STRUCTURED_STATE_TOKENIZER_PATH";
const TOKENIZER_CONFIG_PATH_ENV: &str = "MEMPHANT_STRUCTURED_STATE_TOKENIZER_CONFIG_PATH";
const CAMPAIGN_ATTEMPT_ENV: &str = "MEMPHANT_STRUCTURED_STATE_CAMPAIGN_ATTEMPT";
const AGGREGATE_RESERVATION_ENV: &str = "MEMPHANT_STRUCTURED_STATE_AGGREGATE_RESERVATION_NANOS";
const OBSERVATION_CACHE_ENV: &str = "MEMPHANT_STRUCTURED_STATE_OBSERVATION_CACHE";
const CACHE_HITS_ENV: &str = "MEMPHANT_STRUCTURED_STATE_CACHE_HITS";
const AUTHORIZATION_SHA256_ENV: &str = "MEMPHANT_STRUCTURED_STATE_AUTHORIZATION_SHA256";
const CAMPAIGN_SHA256_ENV: &str = "MEMPHANT_STRUCTURED_STATE_CAMPAIGN_SHA256";
const CACHE_NAMESPACE_ENV: &str = "MEMPHANT_STRUCTURED_STATE_CACHE_NAMESPACE";
const CACHE_ONLY_ENV: &str = "MEMPHANT_STRUCTURED_STATE_CACHE_ONLY";
const CACHE_SOURCE_LEDGER_ENV: &str = "MEMPHANT_STRUCTURED_STATE_CACHE_SOURCE_LEDGER";
const CACHE_SOURCE_LEDGER_PREFIX_BYTES_ENV: &str =
    "MEMPHANT_STRUCTURED_STATE_CACHE_SOURCE_LEDGER_PREFIX_BYTES";
const CACHE_SOURCE_LEDGER_PREFIX_SHA256_ENV: &str =
    "MEMPHANT_STRUCTURED_STATE_CACHE_SOURCE_LEDGER_PREFIX_SHA256";
const CACHE_SERVED_MODEL_ENV: &str = "MEMPHANT_STRUCTURED_STATE_CACHE_SERVED_MODEL";
const CACHE_SERVED_PROVIDER_ENV: &str = "MEMPHANT_STRUCTURED_STATE_CACHE_SERVED_PROVIDER";
const QWEN_CHAT_TEMPLATE_OVERHEAD_TOKENS: u64 = 15;
const QWEN_TOKENIZER_SHA256: &str =
    "5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42";
const QWEN_CHAT_TEMPLATE_SHA256: &str =
    "a4aee8afcf2e0711942cf848899be66016f8d14a889ff9ede07bca099c28f715";

#[derive(Clone)]
pub struct StructuredStateTokenizer {
    tokenizer: Tokenizer,
    tokenizer_sha256: String,
    chat_template_sha256: String,
    chat_template_overhead_tokens: u64,
}

pub fn load_structured_state_tokenizer(
    tokenizer_path: &Path,
    tokenizer_config_path: &Path,
) -> Result<StructuredStateTokenizer, String> {
    let tokenizer_bytes =
        fs::read(tokenizer_path).map_err(|error| format!("failed to read tokenizer: {error}"))?;
    let tokenizer_sha256 = sha256(&tokenizer_bytes);
    let config_bytes = fs::read(tokenizer_config_path)
        .map_err(|error| format!("failed to read tokenizer config: {error}"))?;
    let config: Value = serde_json::from_slice(&config_bytes)
        .map_err(|error| format!("tokenizer config is invalid JSON: {error}"))?;
    let chat_template = config
        .get("chat_template")
        .and_then(Value::as_str)
        .ok_or_else(|| "tokenizer config is missing chat_template".to_string())?;
    let chat_template_sha256 = sha256(chat_template.as_bytes());
    if tokenizer_sha256 != QWEN_TOKENIZER_SHA256
        || chat_template_sha256 != QWEN_CHAT_TEMPLATE_SHA256
    {
        return Err("Qwen tokenizer or chat template identity drift".to_string());
    }
    Ok(StructuredStateTokenizer {
        tokenizer: Tokenizer::from_file(tokenizer_path)
            .map_err(|_| "tokenizer.json is invalid".to_string())?,
        tokenizer_sha256,
        chat_template_sha256,
        chat_template_overhead_tokens: QWEN_CHAT_TEMPLATE_OVERHEAD_TOKENS,
    })
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StructuredStateRequestPlan {
    pub serialized_request: Vec<u8>,
    pub request_sha256: String,
    pub extraction_key: String,
    pub input_reservation_units: u64,
    pub per_attempt_reservation_nanos: u64,
    pub maximum_reservation_nanos: u64,
    pub maximum_attempts: usize,
}

pub fn plan_structured_state_request(
    request: &StructuredStateRequest,
    model: &str,
    prompt: &str,
    reasoning_effort: Option<&str>,
    input_price_nanos_per_million: u64,
    output_price_nanos_per_million: u64,
) -> Result<StructuredStateRequestPlan, StructuredStateProviderError> {
    plan_structured_state_request_with_tokenizer(
        request,
        model,
        prompt,
        reasoning_effort,
        input_price_nanos_per_million,
        output_price_nanos_per_million,
        None,
    )
}

pub fn plan_structured_state_request_with_tokenizer(
    request: &StructuredStateRequest,
    model: &str,
    prompt: &str,
    reasoning_effort: Option<&str>,
    input_price_nanos_per_million: u64,
    output_price_nanos_per_million: u64,
    tokenizer: Option<&StructuredStateTokenizer>,
) -> Result<StructuredStateRequestPlan, StructuredStateProviderError> {
    if model.trim().is_empty() || prompt.trim().is_empty() {
        return Err(invalid(
            "structured-state model and prompt must not be empty",
        ));
    }
    if input_price_nanos_per_million == 0 || output_price_nanos_per_million == 0 {
        return Err(invalid("structured-state price ceilings must be positive"));
    }
    let reasoning_effort = if model == LME_V2_QWEN_MODEL {
        Some(reasoning_effort.unwrap_or("none"))
    } else {
        reasoning_effort
    };
    let schema = response_schema();
    let provider_policy = provider_preferences(
        model,
        input_price_nanos_per_million,
        output_price_nanos_per_million,
    );
    let payload = serde_json::to_string(request)
        .map_err(|error| invalid(format!("structured-state request payload: {error}")))?;
    let mut body = json!({
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": payload}
        ],
        "seed": 0,
        "stream": false,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "memphant_structured_observations",
                "strict": true,
                "schema": schema
            }
        },
        "provider": provider_policy,
    });
    if model == FLASH_MODEL {
        body["temperature"] = json!(0);
    }
    if let Some(effort) = reasoning_effort {
        body["reasoning"] = json!({"effort": effort});
    }
    let serialized_request = serde_json::to_vec(&body)
        .map_err(|error| invalid(format!("structured-state request: {error}")))?;
    if serialized_request.len() >= MAX_REQUEST_BYTES {
        return Err(invalid(format!(
            "request reaches or exceeds {MAX_REQUEST_BYTES}-byte limit"
        )));
    }

    let request_sha256 = sha256(&serialized_request);
    let schema_sha256 = sha256(
        serde_json::to_vec(&response_schema())
            .expect("static structured-state schema serializes")
            .as_slice(),
    );
    let extraction_key = sha256(
        serde_json::to_vec(&json!({
            "contract_revision": CONTRACT_REVISION,
            "source_kind": request.source_kind,
            "source_body_sha256": request.source_body_sha256,
            "batch_index": request.batch_index,
            "evidence_slices": request.evidence_slices,
            "requested_model": model,
            "provider_policy": provider_preferences(
                model,
                input_price_nanos_per_million,
                output_price_nanos_per_million,
            ),
            "prompt_sha256": sha256(prompt.as_bytes()),
            "schema_sha256": schema_sha256,
            "seed": 0,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "max_request_bytes": MAX_REQUEST_BYTES,
            "maximum_attempts": MAX_CAMPAIGN_ATTEMPTS,
            "reasoning_effort": reasoning_effort,
            "tokenizer": tokenizer.map(|tokenizer| json!({
                "tokenizer_sha256": tokenizer.tokenizer_sha256,
                "chat_template_sha256": tokenizer.chat_template_sha256,
                "chat_template_overhead_tokens": tokenizer.chat_template_overhead_tokens,
            })),
        }))
        .expect("structured-state extraction identity serializes")
        .as_slice(),
    );
    let input_units = if let Some(tokenizer) = tokenizer {
        let system = tokenizer
            .tokenizer
            .encode(prompt, false)
            .map_err(|_| invalid("structured-state system prompt tokenization failed"))?
            .len() as u64;
        let user = tokenizer
            .tokenizer
            .encode(payload, false)
            .map_err(|_| invalid("structured-state user payload tokenization failed"))?
            .len() as u64;
        system
            .checked_add(user)
            .and_then(|value| value.checked_add(tokenizer.chat_template_overhead_tokens))
            .ok_or_else(|| invalid("structured-state token count overflow"))?
    } else {
        // Non-Qwen providers retain the conservative byte upper bound until
        // their exact pinned chat tokenizer is part of the provider contract.
        serialized_request.len() as u64
    };
    let input = reserve_nanos(input_units, input_price_nanos_per_million)?;
    let output = reserve_nanos(MAX_OUTPUT_TOKENS, output_price_nanos_per_million)?;
    let per_attempt_reservation_nanos = input
        .checked_add(output)
        .ok_or_else(|| invalid("structured-state reservation overflow"))?;
    let maximum_reservation_nanos = per_attempt_reservation_nanos
        .checked_mul(MAX_CAMPAIGN_ATTEMPTS as u64)
        .ok_or_else(|| invalid("structured-state retry reservation overflow"))?;
    Ok(StructuredStateRequestPlan {
        serialized_request,
        request_sha256,
        extraction_key,
        input_reservation_units: input_units,
        per_attempt_reservation_nanos,
        maximum_reservation_nanos,
        maximum_attempts: MAX_CAMPAIGN_ATTEMPTS,
    })
}

#[allow(clippy::too_many_arguments)]
pub fn plan_structured_state_batches(
    source_kind: StructuredSourceKind,
    source_body_sha256: &str,
    evidence_slices: Vec<EvidenceSlice>,
    model: &str,
    prompt: &str,
    reasoning_effort: Option<&str>,
    input_price_nanos_per_million: u64,
    output_price_nanos_per_million: u64,
    tokenizer: Option<&StructuredStateTokenizer>,
) -> Result<Vec<StructuredStateRequest>, StructuredStateProviderError> {
    plan_structured_state_batches_with(
        source_kind,
        source_body_sha256,
        evidence_slices,
        |request| {
            plan_structured_state_request_with_tokenizer(
                request,
                model,
                prompt,
                reasoning_effort,
                input_price_nanos_per_million,
                output_price_nanos_per_million,
                None,
            )
        },
        |request| {
            plan_structured_state_request_with_tokenizer(
                request,
                model,
                prompt,
                reasoning_effort,
                input_price_nanos_per_million,
                output_price_nanos_per_million,
                tokenizer,
            )
        },
    )
}

fn plan_structured_state_batches_with<SizePlan, FinalPlan>(
    source_kind: StructuredSourceKind,
    source_body_sha256: &str,
    evidence_slices: Vec<EvidenceSlice>,
    mut size_plan: SizePlan,
    mut final_plan: FinalPlan,
) -> Result<Vec<StructuredStateRequest>, StructuredStateProviderError>
where
    SizePlan: FnMut(
        &StructuredStateRequest,
    ) -> Result<StructuredStateRequestPlan, StructuredStateProviderError>,
    FinalPlan: FnMut(
        &StructuredStateRequest,
    ) -> Result<StructuredStateRequestPlan, StructuredStateProviderError>,
{
    let mut batches = Vec::new();
    let mut cursor = 0;
    while cursor < evidence_slices.len() {
        let mut lower = cursor + 1;
        let mut upper = evidence_slices.len();
        let mut best = cursor;
        while lower <= upper {
            let end = lower + (upper - lower) / 2;
            let candidate = StructuredStateRequest {
                source_kind,
                source_body_sha256: source_body_sha256.to_string(),
                batch_index: batches.len(),
                evidence_slices: evidence_slices[cursor..end].to_vec(),
            };
            if size_plan(&candidate).is_ok() {
                best = end;
                lower = end + 1;
            } else if end == 0 {
                break;
            } else {
                upper = end - 1;
            }
        }
        if best == cursor {
            let single = StructuredStateRequest {
                source_kind,
                source_body_sha256: source_body_sha256.to_string(),
                batch_index: batches.len(),
                evidence_slices: vec![evidence_slices[cursor].clone()],
            };
            size_plan(&single)?;
            unreachable!("a successful single-slice request must advance the batch cursor");
        }
        let accepted = StructuredStateRequest {
            source_kind,
            source_body_sha256: source_body_sha256.to_string(),
            batch_index: batches.len(),
            evidence_slices: evidence_slices[cursor..best].to_vec(),
        };
        final_plan(&accepted)?;
        batches.push(accepted);
        cursor = best;
    }
    Ok(batches)
}

fn reserve_nanos(tokens: u64, nanos_per_million: u64) -> Result<u64, StructuredStateProviderError> {
    tokens
        .checked_mul(nanos_per_million)
        .and_then(|value| value.checked_add(999_999))
        .map(|value| value / 1_000_000)
        .ok_or_else(|| invalid("structured-state reservation overflow"))
}

pub fn structured_state_provider_from_env()
-> Result<Option<Arc<dyn StructuredStateProvider>>, String> {
    if std::env::var("MEMPHANT_STRUCTURED_STATE").as_deref() != Ok("on") {
        return Ok(None);
    }
    let model = std::env::var("MEMPHANT_STRUCTURED_STATE_MODEL")
        .ok()
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| DEFAULT_MODEL.to_string());
    let prompt_path = std::env::var_os(PROMPT_PATH_ENV)
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .ok_or_else(|| format!("{PROMPT_PATH_ENV} is required"))?;
    let prompt = load_structured_state_prompt(&prompt_path)?;
    let input_price = parse_positive_u64_env(INPUT_PRICE_ENV)?;
    let output_price = parse_positive_u64_env(OUTPUT_PRICE_ENV)?;
    let tokenizer = if model == LME_V2_QWEN_MODEL {
        let path = PathBuf::from(required_env(TOKENIZER_PATH_ENV)?);
        let config_path = PathBuf::from(required_env(TOKENIZER_CONFIG_PATH_ENV)?);
        Some(load_structured_state_tokenizer(&path, &config_path)?)
    } else {
        None
    };
    let ledger = std::env::var_os(LEDGER_ENV)
        .filter(|value| !value.is_empty())
        .map(PathBuf::from);
    if model == LME_V2_QWEN_MODEL && ledger.is_none() {
        return Err(format!(
            "{LEDGER_ENV} is required for the Qwen campaign route"
        ));
    }
    let dispatch_root = std::env::var_os(DISPATCH_ROOT_ENV)
        .filter(|value| !value.is_empty())
        .map(PathBuf::from);
    if model == LME_V2_QWEN_MODEL && dispatch_root.is_none() {
        return Err(format!(
            "{DISPATCH_ROOT_ENV} is required for the Qwen campaign route"
        ));
    }
    let aggregate_reservation_nanos = if ledger.is_some() {
        Some(parse_positive_u64_env(AGGREGATE_RESERVATION_ENV)?)
    } else {
        None
    };
    let mut provider = OpenRouterStructuredState::new(
        model.clone(),
        prompt,
        input_price,
        output_price,
        Arc::new(UreqTransport::new()),
        ledger,
    );
    if let Some(tokenizer) = tokenizer {
        provider = provider.with_tokenizer(tokenizer);
    }
    let campaign_attempt = if model == LME_V2_QWEN_MODEL {
        parse_positive_u64_env(CAMPAIGN_ATTEMPT_ENV)? as usize
    } else {
        std::env::var(CAMPAIGN_ATTEMPT_ENV)
            .ok()
            .map(|value| value.parse::<usize>())
            .transpose()
            .map_err(|_| format!("{CAMPAIGN_ATTEMPT_ENV} must be an integer"))?
            .unwrap_or(1)
    };
    provider = provider.with_campaign_attempt(campaign_attempt)?;
    provider.aggregate_reservation_nanos = aggregate_reservation_nanos;
    provider.dispatch_root = dispatch_root;
    let cache_values = [
        std::env::var_os(OBSERVATION_CACHE_ENV),
        std::env::var_os(CACHE_HITS_ENV),
        std::env::var_os(AUTHORIZATION_SHA256_ENV),
        std::env::var_os(CAMPAIGN_SHA256_ENV),
        std::env::var_os(CACHE_NAMESPACE_ENV),
        std::env::var_os(CACHE_SOURCE_LEDGER_ENV),
        std::env::var_os(CACHE_SOURCE_LEDGER_PREFIX_BYTES_ENV),
        std::env::var_os(CACHE_SOURCE_LEDGER_PREFIX_SHA256_ENV),
        std::env::var_os(CACHE_SERVED_MODEL_ENV),
        std::env::var_os(CACHE_SERVED_PROVIDER_ENV),
    ];
    if cache_values.iter().any(Option::is_some) {
        provider = provider.with_campaign_cache(CampaignCache {
            root: PathBuf::from(required_env(OBSERVATION_CACHE_ENV)?),
            hit_root: PathBuf::from(required_env(CACHE_HITS_ENV)?),
            authorization_sha256: required_env(AUTHORIZATION_SHA256_ENV)?,
            campaign_sha256: required_env(CAMPAIGN_SHA256_ENV)?,
            namespace: required_env(CACHE_NAMESPACE_ENV)?,
            source_ledger: PathBuf::from(required_env(CACHE_SOURCE_LEDGER_ENV)?),
            source_ledger_prefix_bytes: parse_nonnegative_usize_env(
                CACHE_SOURCE_LEDGER_PREFIX_BYTES_ENV,
            )?,
            source_ledger_prefix_sha256: required_env(CACHE_SOURCE_LEDGER_PREFIX_SHA256_ENV)?,
            served_model: required_env(CACHE_SERVED_MODEL_ENV)?,
            served_provider: required_env(CACHE_SERVED_PROVIDER_ENV)?,
        })?;
    } else if model == LME_V2_QWEN_MODEL {
        return Err(format!(
            "{OBSERVATION_CACHE_ENV}, {CACHE_HITS_ENV}, {AUTHORIZATION_SHA256_ENV}, {CAMPAIGN_SHA256_ENV}, {CACHE_NAMESPACE_ENV}, {CACHE_SOURCE_LEDGER_ENV}, {CACHE_SOURCE_LEDGER_PREFIX_BYTES_ENV}, {CACHE_SOURCE_LEDGER_PREFIX_SHA256_ENV}, {CACHE_SERVED_MODEL_ENV}, and {CACHE_SERVED_PROVIDER_ENV} are required for the Qwen campaign route"
        ));
    }
    provider.cache_only = match std::env::var(CACHE_ONLY_ENV).as_deref() {
        Ok("on") => true,
        Ok("off") | Err(std::env::VarError::NotPresent) => false,
        _ => return Err(format!("{CACHE_ONLY_ENV} must be on or off")),
    };
    if provider.cache_only && provider.campaign_cache.is_none() {
        return Err(format!(
            "{CACHE_ONLY_ENV}=on requires the campaign cache contract"
        ));
    }
    if let Some(effort) = std::env::var("MEMPHANT_STRUCTURED_STATE_REASONING_EFFORT")
        .ok()
        .filter(|value| !value.trim().is_empty())
    {
        if !matches!(
            effort.as_str(),
            "none" | "minimal" | "low" | "medium" | "high"
        ) {
            return Err(
                "MEMPHANT_STRUCTURED_STATE_REASONING_EFFORT must be none, minimal, low, medium, or high"
                    .to_string(),
            );
        }
        provider = provider.with_reasoning_effort(effort);
    }
    Ok(Some(Arc::new(provider)))
}

fn required_env(name: &str) -> Result<String, String> {
    std::env::var(name)
        .ok()
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| format!("{name} is required"))
}

fn parse_positive_u64_env(name: &str) -> Result<u64, String> {
    required_env(name)?
        .parse::<u64>()
        .ok()
        .filter(|value| *value > 0)
        .ok_or_else(|| format!("{name} must be a positive integer"))
}

fn parse_nonnegative_usize_env(name: &str) -> Result<usize, String> {
    required_env(name)?
        .parse::<usize>()
        .map_err(|_| format!("{name} must be a non-negative integer"))
}

pub fn load_structured_state_prompt(path: &Path) -> Result<String, String> {
    let prompt = fs::read_to_string(path).map_err(|error| {
        format!(
            "failed to read {PROMPT_PATH_ENV}={}: {error}",
            path.display()
        )
    })?;
    let prompt = prompt
        .strip_suffix("\r\n")
        .or_else(|| prompt.strip_suffix('\n'))
        .unwrap_or(&prompt)
        .to_string();
    if prompt.trim().is_empty() {
        return Err(format!("{PROMPT_PATH_ENV} must not be empty"));
    }
    Ok(prompt)
}

#[derive(Clone)]
struct CampaignCache {
    root: PathBuf,
    hit_root: PathBuf,
    authorization_sha256: String,
    campaign_sha256: String,
    namespace: String,
    source_ledger: PathBuf,
    source_ledger_prefix_bytes: usize,
    source_ledger_prefix_sha256: String,
    served_model: String,
    served_provider: String,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct CacheEntry {
    core: CacheEntryCore,
    cache_entry_sha256: String,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct CacheEntryCore {
    schema_version: u8,
    authorization_sha256: String,
    campaign_sha256: String,
    cache_namespace: String,
    extraction_key: String,
    request_sha256: String,
    source_kind: StructuredSourceKind,
    source_body_sha256: String,
    batch_index: usize,
    evidence_slices_sha256: String,
    requested_model: String,
    served_model: String,
    served_provider: String,
    response_id: String,
    source_attempt_id: String,
    source_started_event_sha256: String,
    source_result_event_sha256: String,
    provider_result_sha256: String,
    observation_count: usize,
    observation_sha256: String,
    observations: Vec<StructuredObservation>,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct CacheHitReceipt {
    core: CacheHitReceiptCore,
    cache_hit_sha256: String,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct CacheHitReceiptCore {
    schema_version: u8,
    authorization_sha256: String,
    campaign_sha256: String,
    cache_namespace: String,
    cache_entry_sha256: String,
    extraction_key: String,
    request_sha256: String,
    source_kind: StructuredSourceKind,
    source_body_sha256: String,
    batch_index: usize,
    evidence_slices_sha256: String,
    evidence_slices: Vec<EvidenceSlice>,
    requested_model: String,
    served_model: String,
    served_provider: String,
    response_id: String,
    source_attempt_id: String,
    source_started_event_sha256: String,
    source_result_event_sha256: String,
    provider_result_sha256: String,
    observation_count: usize,
    observation_sha256: String,
    observations: Vec<StructuredObservation>,
    reservation_status: String,
    settled_nanos: u64,
}

impl CampaignCache {
    fn lock(&self, extraction_key: &str) -> Result<std::fs::File, StructuredStateProviderError> {
        let lock_root = self.root.join(".locks");
        fs::create_dir_all(&lock_root).map_err(cache_error)?;
        let file = OpenOptions::new()
            .create(true)
            .read(true)
            .write(true)
            .truncate(false)
            .open(lock_root.join(format!("{extraction_key}.lock")))
            .map_err(cache_error)?;
        file.lock_exclusive().map_err(cache_error)?;
        Ok(file)
    }

    fn load(
        &self,
        request: &StructuredStateRequest,
        plan: &StructuredStateRequestPlan,
        requested_model: &str,
    ) -> Result<Option<Vec<StructuredObservation>>, StructuredStateProviderError> {
        let path = self.root.join(format!("{}.json", plan.extraction_key));
        if !path.exists() {
            return Ok(None);
        }
        let bytes = fs::read(&path).map_err(cache_error)?;
        let entry: CacheEntry = serde_json::from_slice(&bytes).map_err(cache_error)?;
        let core_sha256 = canonical_sha256(&entry.core);
        let core = &entry.core;
        if entry.cache_entry_sha256 != core_sha256
            || core.schema_version != 1
            || core.authorization_sha256 != self.authorization_sha256
            || core.campaign_sha256 != self.campaign_sha256
            || core.cache_namespace != self.namespace
            || core.extraction_key != plan.extraction_key
            || core.request_sha256 != plan.request_sha256
            || core.source_kind != request.source_kind
            || core.source_body_sha256 != request.source_body_sha256
            || core.batch_index != request.batch_index
            || core.evidence_slices_sha256 != canonical_sha256(&request.evidence_slices)
            || core.requested_model != requested_model
            || core.observation_count != core.observations.len()
            || core.observation_sha256 != canonical_sha256(&core.observations)
            || !is_sha256(&core.source_started_event_sha256)
            || !is_sha256(&core.source_result_event_sha256)
            || !is_sha256(&core.provider_result_sha256)
            || core.served_model != self.served_model
            || core.served_provider != self.served_provider
            || core.response_id.trim().is_empty()
            || core.source_attempt_id.trim().is_empty()
        {
            return Err(invalid("structured-state campaign cache identity drift"));
        }
        self.validate_source_ledger(core)?;
        validate_cached_observations(request, &core.observations)?;
        let receipt_core = CacheHitReceiptCore {
            schema_version: 1,
            authorization_sha256: core.authorization_sha256.clone(),
            campaign_sha256: core.campaign_sha256.clone(),
            cache_namespace: core.cache_namespace.clone(),
            cache_entry_sha256: entry.cache_entry_sha256.clone(),
            extraction_key: core.extraction_key.clone(),
            request_sha256: core.request_sha256.clone(),
            source_kind: core.source_kind,
            source_body_sha256: core.source_body_sha256.clone(),
            batch_index: core.batch_index,
            evidence_slices_sha256: core.evidence_slices_sha256.clone(),
            evidence_slices: request.evidence_slices.clone(),
            requested_model: core.requested_model.clone(),
            served_model: core.served_model.clone(),
            served_provider: core.served_provider.clone(),
            response_id: core.response_id.clone(),
            source_attempt_id: core.source_attempt_id.clone(),
            source_started_event_sha256: core.source_started_event_sha256.clone(),
            source_result_event_sha256: core.source_result_event_sha256.clone(),
            provider_result_sha256: core.provider_result_sha256.clone(),
            observation_count: core.observation_count,
            observation_sha256: core.observation_sha256.clone(),
            observations: core.observations.clone(),
            reservation_status: "cache_hit".to_string(),
            settled_nanos: 0,
        };
        let receipt = CacheHitReceipt {
            cache_hit_sha256: canonical_sha256(&receipt_core),
            core: receipt_core,
        };
        create_or_validate_json(
            &self.hit_root.join(format!("{}.json", plan.extraction_key)),
            &receipt,
        )?;
        Ok(Some(core.observations.clone()))
    }

    fn validate_source_ledger(
        &self,
        core: &CacheEntryCore,
    ) -> Result<(), StructuredStateProviderError> {
        let bytes = fs::read(&self.source_ledger).map_err(cache_error)?;
        if bytes.len() < self.source_ledger_prefix_bytes
            || sha256(&bytes[..self.source_ledger_prefix_bytes]) != self.source_ledger_prefix_sha256
        {
            return Err(invalid(
                "structured-state campaign cache source ledger prefix identity drift",
            ));
        }
        let text =
            std::str::from_utf8(&bytes[..self.source_ledger_prefix_bytes]).map_err(cache_error)?;
        let mut started = None;
        let mut result = None;
        for line in text.lines() {
            let event: Value = serde_json::from_str(line).map_err(cache_error)?;
            let event_sha256 = canonical_sha256(&event);
            if event_sha256 == core.source_started_event_sha256 {
                if started.replace(event).is_some() {
                    return Err(invalid(
                        "structured-state campaign cache source event is duplicated",
                    ));
                }
            } else if event_sha256 == core.source_result_event_sha256
                && result.replace(event).is_some()
            {
                return Err(invalid(
                    "structured-state campaign cache source event is duplicated",
                ));
            }
        }
        let Some(started) = started else {
            return Err(invalid(
                "structured-state campaign cache source start is not in the frozen ledger",
            ));
        };
        let Some(result) = result else {
            return Err(invalid(
                "structured-state campaign cache source result is not in the frozen ledger",
            ));
        };
        let source_kind = serde_json::to_value(core.source_kind).map_err(cache_error)?;
        let shared_identity = |event: &Value| {
            event.get("attempt_id").and_then(Value::as_str) == Some(core.source_attempt_id.as_str())
                && event.get("source_kind") == Some(&source_kind)
                && event.get("extraction_key").and_then(Value::as_str)
                    == Some(core.extraction_key.as_str())
                && event.get("request_sha256").and_then(Value::as_str)
                    == Some(core.request_sha256.as_str())
                && event.get("source_body_sha256").and_then(Value::as_str)
                    == Some(core.source_body_sha256.as_str())
                && event.get("batch_index").and_then(Value::as_u64) == Some(core.batch_index as u64)
                && event.get("requested_model").and_then(Value::as_str)
                    == Some(core.requested_model.as_str())
        };
        if started.get("schema_version").and_then(Value::as_u64) != Some(3)
            || started.get("event").and_then(Value::as_str) != Some("started")
            || started.get("reservation_status").and_then(Value::as_str) != Some("reserved")
            || started.get("attempt").and_then(Value::as_u64) != Some(1)
            || !shared_identity(&started)
            || result.get("schema_version").and_then(Value::as_u64) != Some(3)
            || result.get("event").and_then(Value::as_str) != Some("result")
            || result.get("reservation_status").and_then(Value::as_str) != Some("settled")
            || result.get("attempt").and_then(Value::as_u64) != Some(1)
            || result.get("campaign_attempt") != started.get("campaign_attempt")
            || result.get("maximum_attempts") != started.get("maximum_attempts")
            || result.get("per_attempt_reservation_nanos")
                != started.get("per_attempt_reservation_nanos")
            || result.get("parse_status").and_then(Value::as_str) != Some("decoded")
            || result.get("error").is_some()
            || !shared_identity(&result)
            || result.get("served_model").and_then(Value::as_str)
                != Some(self.served_model.as_str())
            || result.get("served_provider").and_then(Value::as_str)
                != Some(self.served_provider.as_str())
            || result.get("response_id").and_then(Value::as_str) != Some(core.response_id.as_str())
            || result.get("result_sha256").and_then(Value::as_str)
                != Some(core.provider_result_sha256.as_str())
            || result.get("observation_count").and_then(Value::as_u64)
                != Some(core.observation_count as u64)
            || result.get("observation_sha256").and_then(Value::as_str)
                != Some(core.observation_sha256.as_str())
        {
            return Err(invalid(
                "structured-state campaign cache paid-source provenance drift",
            ));
        }
        Ok(())
    }

    #[allow(clippy::too_many_arguments)]
    fn store(
        &self,
        request: &StructuredStateRequest,
        plan: &StructuredStateRequestPlan,
        requested_model: &str,
        source_started_event_sha256: String,
        source_result_event_sha256: String,
        completed: &AttemptEvent,
        observations: &[StructuredObservation],
    ) -> Result<(), StructuredStateProviderError> {
        validate_cached_observations(request, observations)?;
        if completed.served_model.as_deref() != Some(self.served_model.as_str())
            || completed.served_provider.as_deref() != Some(self.served_provider.as_str())
        {
            return Err(invalid(
                "settled cache source differs from the exact authorized route",
            ));
        }
        let core = CacheEntryCore {
            schema_version: 1,
            authorization_sha256: self.authorization_sha256.clone(),
            campaign_sha256: self.campaign_sha256.clone(),
            cache_namespace: self.namespace.clone(),
            extraction_key: plan.extraction_key.clone(),
            request_sha256: plan.request_sha256.clone(),
            source_kind: request.source_kind,
            source_body_sha256: request.source_body_sha256.clone(),
            batch_index: request.batch_index,
            evidence_slices_sha256: canonical_sha256(&request.evidence_slices),
            requested_model: requested_model.to_string(),
            served_model: completed
                .served_model
                .clone()
                .ok_or_else(|| invalid("settled cache source omitted served model"))?,
            served_provider: completed
                .served_provider
                .clone()
                .ok_or_else(|| invalid("settled cache source omitted served provider"))?,
            response_id: completed
                .response_id
                .clone()
                .ok_or_else(|| invalid("settled cache source omitted response id"))?,
            source_attempt_id: completed.attempt_id.clone(),
            source_started_event_sha256,
            source_result_event_sha256,
            provider_result_sha256: completed
                .result_sha256
                .clone()
                .ok_or_else(|| invalid("settled cache source omitted result hash"))?,
            observation_count: observations.len(),
            observation_sha256: canonical_sha256(&observations),
            observations: observations.to_vec(),
        };
        let entry = CacheEntry {
            cache_entry_sha256: canonical_sha256(&core),
            core,
        };
        create_or_validate_json(
            &self.root.join(format!("{}.json", plan.extraction_key)),
            &entry,
        )
    }
}

fn validate_cached_observations(
    request: &StructuredStateRequest,
    observations: &[StructuredObservation],
) -> Result<(), StructuredStateProviderError> {
    let slices = request
        .evidence_slices
        .iter()
        .map(|slice| (slice.id.as_str(), slice.body.as_str()))
        .collect::<BTreeMap<_, _>>();
    for observation in observations {
        validate_structured_observation(observation)?;
        let Some(body) = slices.get(observation.evidence_slice_id.as_str()) else {
            return Err(invalid(
                "cached observation names an unknown evidence slice",
            ));
        };
        if !body.contains(&observation.evidence_quote) {
            return Err(invalid(
                "cached observation quote is not grounded in its evidence slice",
            ));
        }
    }
    Ok(())
}

fn create_or_validate_json<T: Serialize>(
    path: &Path,
    value: &T,
) -> Result<(), StructuredStateProviderError> {
    let bytes = serde_json::to_vec(value).expect("campaign cache artifact serializes");
    if path.exists() {
        let prior = fs::read(path).map_err(cache_error)?;
        if prior != bytes {
            return Err(invalid("structured-state campaign cache artifact drift"));
        }
        return Ok(());
    }
    let parent = path
        .parent()
        .ok_or_else(|| invalid("campaign cache artifact path has no parent"))?;
    fs::create_dir_all(parent).map_err(cache_error)?;
    let temporary = parent.join(format!(
        ".{}.{}.tmp",
        path.file_name().unwrap().to_string_lossy(),
        uuid::Uuid::new_v4()
    ));
    let mut file = OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(&temporary)
        .map_err(cache_error)?;
    file.write_all(&bytes).map_err(cache_error)?;
    file.sync_all().map_err(cache_error)?;
    fs::rename(&temporary, path).map_err(cache_error)?;
    std::fs::File::open(parent)
        .and_then(|directory| directory.sync_all())
        .map_err(cache_error)?;
    Ok(())
}

fn attempt_event_sha256(event: &AttemptEvent) -> String {
    canonical_sha256(event)
}

fn canonical_sha256<T: Serialize + ?Sized>(value: &T) -> String {
    let value = serde_json::to_value(value).expect("canonical campaign value serializes");
    sha256(
        serde_json::to_vec(&value)
            .expect("canonical campaign value serializes")
            .as_slice(),
    )
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
}

fn cache_error(error: impl std::fmt::Display) -> StructuredStateProviderError {
    StructuredStateProviderError::Unavailable(format!(
        "structured-state campaign cache failed: {error}"
    ))
}

#[derive(Clone)]
struct OpenRouterStructuredState {
    model: String,
    prompt: String,
    input_price_nanos_per_million: u64,
    output_price_nanos_per_million: u64,
    identity: StructuredStateProviderIdentity,
    transport: Arc<dyn Transport>,
    ledger: Option<PathBuf>,
    dispatch_root: Option<PathBuf>,
    ledger_lock: Arc<Mutex<()>>,
    reasoning_effort: Option<String>,
    tokenizer: Option<StructuredStateTokenizer>,
    campaign_attempt: usize,
    aggregate_reservation_nanos: Option<u64>,
    campaign_cache: Option<CampaignCache>,
    cache_only: bool,
}

impl OpenRouterStructuredState {
    fn new(
        model: String,
        prompt: String,
        input_price_nanos_per_million: u64,
        output_price_nanos_per_million: u64,
        transport: Arc<dyn Transport>,
        ledger: Option<PathBuf>,
    ) -> Self {
        Self {
            identity: StructuredStateProviderIdentity {
                model: compiler_model_identity(&model, None),
                prompt_hash: sha256(prompt.as_bytes()),
                schema_hash: sha256(
                    serde_json::to_vec(&response_schema())
                        .expect("static structured-state schema serializes")
                        .as_slice(),
                ),
            },
            model,
            prompt,
            input_price_nanos_per_million,
            output_price_nanos_per_million,
            transport,
            ledger,
            dispatch_root: None,
            ledger_lock: Arc::new(Mutex::new(())),
            reasoning_effort: None,
            tokenizer: None,
            campaign_attempt: 1,
            aggregate_reservation_nanos: None,
            campaign_cache: None,
            cache_only: false,
        }
    }

    fn with_campaign_cache(mut self, cache: CampaignCache) -> Result<Self, String> {
        if !is_sha256(&cache.authorization_sha256)
            || !is_sha256(&cache.campaign_sha256)
            || !is_sha256(&cache.source_ledger_prefix_sha256)
            || cache.namespace.trim().is_empty()
            || cache.served_model.trim().is_empty()
            || cache.served_provider.trim().is_empty()
        {
            return Err("structured-state campaign cache identity is malformed".to_string());
        }
        self.campaign_cache = Some(cache);
        Ok(self)
    }

    fn with_campaign_attempt(mut self, campaign_attempt: usize) -> Result<Self, String> {
        if !(1..=MAX_CAMPAIGN_ATTEMPTS).contains(&campaign_attempt) {
            return Err(format!(
                "{CAMPAIGN_ATTEMPT_ENV} must be between 1 and {MAX_CAMPAIGN_ATTEMPTS}"
            ));
        }
        self.campaign_attempt = campaign_attempt;
        Ok(self)
    }

    fn with_tokenizer(mut self, tokenizer: StructuredStateTokenizer) -> Self {
        self.identity.model.push_str(";tokenizer_sha256=");
        self.identity.model.push_str(&tokenizer.tokenizer_sha256);
        self.identity.model.push_str(";chat_template_sha256=");
        self.identity
            .model
            .push_str(&tokenizer.chat_template_sha256);
        self.identity
            .model
            .push_str(";chat_template_overhead_tokens=");
        self.identity
            .model
            .push_str(&tokenizer.chat_template_overhead_tokens.to_string());
        self.tokenizer = Some(tokenizer);
        self
    }

    fn with_reasoning_effort(mut self, effort: String) -> Self {
        self.identity.model = compiler_model_identity(&self.model, Some(&effort));
        if let Some(tokenizer) = &self.tokenizer {
            self.identity.model.push_str(";tokenizer_sha256=");
            self.identity.model.push_str(&tokenizer.tokenizer_sha256);
            self.identity.model.push_str(";chat_template_sha256=");
            self.identity
                .model
                .push_str(&tokenizer.chat_template_sha256);
            self.identity
                .model
                .push_str(";chat_template_overhead_tokens=");
            self.identity
                .model
                .push_str(&tokenizer.chat_template_overhead_tokens.to_string());
        }
        self.reasoning_effort = Some(effort);
        self
    }

    fn plan(
        &self,
        request: &StructuredStateRequest,
    ) -> Result<StructuredStateRequestPlan, StructuredStateProviderError> {
        plan_structured_state_request_with_tokenizer(
            request,
            &self.model,
            &self.prompt,
            self.reasoning_effort.as_deref(),
            self.input_price_nanos_per_million,
            self.output_price_nanos_per_million,
            self.tokenizer.as_ref(),
        )
    }

    fn extract_sync(
        &self,
        request: &StructuredStateRequest,
    ) -> Result<Vec<StructuredObservation>, StructuredStateProviderError> {
        let plan = self.plan(request)?;
        let cache_lock = self
            .campaign_cache
            .as_ref()
            .map(|cache| cache.lock(&plan.extraction_key))
            .transpose()?;
        if let Some(cache) = &self.campaign_cache
            && let Some(observations) = cache.load(request, &plan, &self.model)?
        {
            drop(cache_lock);
            return Ok(observations);
        }
        if self.cache_only {
            return Err(StructuredStateProviderError::Unavailable(
                "structured-state cache-only materialization rejected a cache miss".to_string(),
            ));
        }
        if let Some(outcome) = self.resume_exact_attempt(request, &plan)? {
            drop(cache_lock);
            return outcome;
        }
        let attempt = MAX_ATTEMPTS;
        let attempt_id = uuid::Uuid::new_v4().to_string();
        let started = Instant::now();
        let started_event =
            AttemptEvent::started(&attempt_id, request, &plan, &self.model, attempt)
                .for_campaign_attempt(self.campaign_attempt);
        self.record_attempt(&started_event)?;
        let response = match self.transport.post(&plan.serialized_request) {
            Ok(response) => response,
            Err(_) => {
                self.record_attempt(
                    &AttemptEvent::failed(
                        &attempt_id,
                        request,
                        &plan,
                        &self.model,
                        attempt,
                        "transport_error",
                        started.elapsed(),
                    )
                    .for_campaign_attempt(self.campaign_attempt),
                )?;
                return Err(StructuredStateProviderError::Unavailable(
                    "OpenRouter transport failed; completion was not resent".to_string(),
                ));
            }
        };
        self.persist_dispatch_response(&started_event, &response, "response")?;
        if !(200..300).contains(&response.status) {
            self.record_attempt(
                &AttemptEvent::http_error(
                    &attempt_id,
                    request,
                    &plan,
                    &self.model,
                    attempt,
                    &response,
                    started.elapsed(),
                )
                .for_campaign_attempt(self.campaign_attempt),
            )?;
            return Err(StructuredStateProviderError::Unavailable(format!(
                "OpenRouter HTTP {}: {}",
                response.status,
                openrouter_error_message(&response.body)
            )));
        }
        let reconciled = match reconcile_generation(self.transport.as_ref(), &response) {
            Ok(reconciled) => reconciled,
            Err(error) => return Err(StructuredStateProviderError::Unavailable(error)),
        };
        self.persist_dispatch_response(&started_event, &reconciled, "generation")?;
        let observations = match decode_response(reconciled.body.clone(), request) {
            Ok(observations) => observations,
            Err(error) => {
                self.record_attempt(
                    &AttemptEvent::reconciled(
                        &attempt_id,
                        request,
                        &plan,
                        &self.model,
                        attempt,
                        &reconciled,
                        None,
                        "response_decode_error",
                        Some("response_decode_error"),
                        started.elapsed(),
                    )
                    .for_campaign_attempt(self.campaign_attempt),
                )?;
                return Err(error);
            }
        };
        let completed_event = AttemptEvent::completed(
            &attempt_id,
            request,
            &plan,
            &self.model,
            attempt,
            &reconciled,
            &observations,
            started.elapsed(),
        )
        .for_campaign_attempt(self.campaign_attempt);
        self.record_attempt(&completed_event)?;
        if let Some(cache) = &self.campaign_cache {
            cache.store(
                request,
                &plan,
                &self.model,
                attempt_event_sha256(&started_event),
                attempt_event_sha256(&completed_event),
                &completed_event,
                &observations,
            )?;
        }
        drop(cache_lock);
        Ok(observations)
    }

    fn record_attempt(&self, event: &AttemptEvent) -> Result<(), StructuredStateProviderError> {
        let Some(path) = &self.ledger else {
            return Ok(());
        };
        let _guard = self.ledger_lock.lock().map_err(|_| {
            StructuredStateProviderError::Unavailable(
                "structured-state attempt ledger lock poisoned".to_string(),
            )
        })?;
        append_json_line(path, event, self.aggregate_reservation_nanos).map_err(|error| {
            StructuredStateProviderError::Unavailable(format!(
                "structured-state attempt ledger write failed: {error}"
            ))
        })
    }

    fn dispatch_path(&self, plan: &StructuredStateRequestPlan, stage: &str) -> Option<PathBuf> {
        self.dispatch_root.as_ref().map(|root| {
            root.join(format!(
                "{}-{}-{stage}.json",
                self.campaign_attempt, plan.extraction_key
            ))
        })
    }

    fn persist_dispatch_response(
        &self,
        started: &AttemptEvent,
        response: &HttpResponse,
        stage: &str,
    ) -> Result<(), StructuredStateProviderError> {
        let Some(path) = self.dispatch_path_for_event(started, stage) else {
            return Ok(());
        };
        let core = json!({
            "schema_version": 1,
            "stage": stage,
            "campaign_attempt": started.campaign_attempt,
            "attempt_id": started.attempt_id,
            "extraction_key": started.extraction_key,
            "request_sha256": started.request_sha256,
            "status": response.status,
            "body": response.body,
        });
        let record = json!({
            "core": core,
            "dispatch_sha256": canonical_sha256(&core),
        });
        write_private_create_only(&path, &record).map_err(|error| {
            StructuredStateProviderError::Unavailable(format!(
                "structured-state dispatch journal write failed: {error}"
            ))
        })
    }

    fn dispatch_path_for_event(&self, event: &AttemptEvent, stage: &str) -> Option<PathBuf> {
        self.dispatch_root.as_ref().map(|root| {
            root.join(format!(
                "{}-{}-{stage}.json",
                event.campaign_attempt, event.extraction_key
            ))
        })
    }

    fn resume_exact_attempt(
        &self,
        request: &StructuredStateRequest,
        plan: &StructuredStateRequestPlan,
    ) -> Result<
        Option<Result<Vec<StructuredObservation>, StructuredStateProviderError>>,
        StructuredStateProviderError,
    > {
        let Some(path) = &self.ledger else {
            return Ok(None);
        };
        let state = exact_attempt_state(path, plan, self.campaign_attempt)?;
        let attempt_id = match state {
            ExactAttemptState::Absent => return Ok(None),
            ExactAttemptState::Terminal { decoded: false, .. } => return Ok(Some(Ok(Vec::new()))),
            ExactAttemptState::Started {
                attempt_id,
                started_event_sha256,
            } => (attempt_id, started_event_sha256),
            ExactAttemptState::Terminal {
                attempt_id,
                decoded: true,
                started_event_sha256,
                result_event_sha256,
            } => {
                let generation_path = self.dispatch_path(plan, "generation").ok_or_else(|| {
                    StructuredStateProviderError::Unavailable(
                        "decoded terminal attempt lacks dispatch journal authority".to_string(),
                    )
                })?;
                let reconciled = load_dispatch_response(
                    &generation_path,
                    &attempt_id,
                    plan,
                    self.campaign_attempt,
                    "generation",
                )?;
                let observations = decode_response(reconciled.body.clone(), request)?;
                let completed = AttemptEvent::completed(
                    &attempt_id,
                    request,
                    plan,
                    &self.model,
                    MAX_ATTEMPTS,
                    &reconciled,
                    &observations,
                    Duration::ZERO,
                )
                .for_campaign_attempt(self.campaign_attempt);
                if let Some(cache) = &self.campaign_cache {
                    cache.store(
                        request,
                        plan,
                        &self.model,
                        started_event_sha256,
                        result_event_sha256,
                        &completed,
                        &observations,
                    )?;
                }
                return Ok(Some(Ok(observations)));
            }
        };
        let (attempt_id, started_event_sha256) = attempt_id;
        let response_path = self.dispatch_path(plan, "response").ok_or_else(|| {
            StructuredStateProviderError::Unavailable(
                "structured-state started attempt has no dispatch journal authority".to_string(),
            )
        })?;
        if !response_path.is_file() {
            return Err(StructuredStateProviderError::Unavailable(
                "structured-state started attempt is ambiguous and non-dispatchable".to_string(),
            ));
        }
        let response = load_dispatch_response(
            &response_path,
            &attempt_id,
            plan,
            self.campaign_attempt,
            "response",
        )?;
        let started = AttemptEvent::started(&attempt_id, request, plan, &self.model, MAX_ATTEMPTS)
            .for_campaign_attempt(self.campaign_attempt);
        if !(200..300).contains(&response.status) {
            self.record_attempt(
                &AttemptEvent::http_error(
                    &attempt_id,
                    request,
                    plan,
                    &self.model,
                    MAX_ATTEMPTS,
                    &response,
                    Duration::ZERO,
                )
                .for_campaign_attempt(self.campaign_attempt),
            )?;
            return Ok(Some(Err(StructuredStateProviderError::Unavailable(
                format!("OpenRouter HTTP {}", response.status),
            ))));
        }
        if response.body.get("id").and_then(Value::as_str).is_none() {
            return Err(StructuredStateProviderError::Unavailable(
                "structured-state captured response has no generation id and is non-dispatchable"
                    .to_string(),
            ));
        }
        let generation_path = self
            .dispatch_path(plan, "generation")
            .expect("dispatch root exists");
        let reconciled = if generation_path.is_file() {
            load_dispatch_response(
                &generation_path,
                &attempt_id,
                plan,
                self.campaign_attempt,
                "generation",
            )?
        } else {
            let value = reconcile_generation(self.transport.as_ref(), &response)
                .map_err(StructuredStateProviderError::Unavailable)?;
            self.persist_dispatch_response(&started, &value, "generation")?;
            value
        };
        let observations = decode_response(reconciled.body.clone(), request)?;
        let completed = AttemptEvent::completed(
            &attempt_id,
            request,
            plan,
            &self.model,
            MAX_ATTEMPTS,
            &reconciled,
            &observations,
            Duration::ZERO,
        )
        .for_campaign_attempt(self.campaign_attempt);
        self.record_attempt(&completed)?;
        if let Some(cache) = &self.campaign_cache {
            cache.store(
                request,
                plan,
                &self.model,
                started_event_sha256,
                attempt_event_sha256(&completed),
                &completed,
                &observations,
            )?;
        }
        Ok(Some(Ok(observations)))
    }
}

impl StructuredStateProvider for OpenRouterStructuredState {
    fn identity(&self) -> &StructuredStateProviderIdentity {
        &self.identity
    }

    fn plan_batches(
        &self,
        source_kind: StructuredSourceKind,
        source_body_sha256: &str,
        evidence_slices: Vec<EvidenceSlice>,
    ) -> Result<Vec<StructuredStateRequest>, StructuredStateProviderError> {
        plan_structured_state_batches(
            source_kind,
            source_body_sha256,
            evidence_slices,
            &self.model,
            &self.prompt,
            self.reasoning_effort.as_deref(),
            self.input_price_nanos_per_million,
            self.output_price_nanos_per_million,
            self.tokenizer.as_ref(),
        )
    }

    fn extract<'a>(
        &'a self,
        request: &'a StructuredStateRequest,
    ) -> std::pin::Pin<
        Box<
            dyn std::future::Future<
                    Output = Result<Vec<StructuredObservation>, StructuredStateProviderError>,
                > + Send
                + 'a,
        >,
    > {
        let provider = self.clone();
        let request = request.clone();
        Box::pin(async move {
            tokio::task::spawn_blocking(move || provider.extract_sync(&request))
                .await
                .map_err(|error| {
                    StructuredStateProviderError::Unavailable(format!(
                        "OpenRouter blocking task failed: {error}"
                    ))
                })?
        })
    }
}

fn provider_preferences(model: &str, input_price: u64, output_price: u64) -> Value {
    let mut preferences = if model == FLASH_MODEL {
        json!({
            "require_parameters": true,
            "only": [FLASH_PROVIDER],
            "allow_fallbacks": false,
        })
    } else if model == DEEPSEEK_MODEL {
        json!({
            "require_parameters": true,
            "order": DEEPSEEK_PROVIDERS,
            "only": DEEPSEEK_PROVIDERS,
            "allow_fallbacks": false,
        })
    } else if model == LME_V2_QWEN_MODEL {
        json!({
            "require_parameters": true,
            "only": [LME_V2_QWEN_PROVIDER],
            "allow_fallbacks": false,
        })
    } else {
        json!({"require_parameters": true, "allow_fallbacks": false})
    };
    preferences["max_price"] = json!({
        "prompt": input_price as f64 / 1_000_000_000.0,
        "completion": output_price as f64 / 1_000_000_000.0,
    });
    preferences
}

fn compiler_model_identity(model: &str, reasoning_effort: Option<&str>) -> String {
    let reasoning_effort = if model == LME_V2_QWEN_MODEL {
        Some(reasoning_effort.unwrap_or("none"))
    } else {
        reasoning_effort
    };
    let mut identity = model.to_string();
    if model == FLASH_MODEL {
        identity.push_str(";provider=google-ai-studio");
    } else if model == DEEPSEEK_MODEL {
        identity.push_str(";providers=deepinfra,wandb");
    } else if model == LME_V2_QWEN_MODEL {
        identity.push_str(";provider=deepinfra");
    }
    identity.push_str(";fallbacks=false;seed=0;max_tokens=4096;max_request_bytes=131072");
    if model == FLASH_MODEL {
        identity.push_str(";temperature=0");
    }
    if let Some(effort) = reasoning_effort {
        identity.push_str(";reasoning_effort=");
        identity.push_str(effort);
    }
    identity
}

fn response_schema() -> Value {
    let nullable_timestamp = json!({
        "anyOf": [
            {"type": "string", "format": "date-time"},
            {"type": "null"}
        ]
    });
    json!({
        "type": "object",
        "additionalProperties": false,
        "required": ["observations"],
        "properties": {
            "observations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": false,
                    "required": [
                        "namespace", "item_key", "fields", "disposition",
                        "evidence_slice_id", "evidence_quote", "valid_from", "valid_to"
                    ],
                    "properties": {
                        "namespace": {"type": "string", "minLength": 1},
                        "item_key": {"type": "string", "minLength": 1},
                        "fields": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": false,
                                "required": ["key", "value"],
                                "properties": {
                                    "key": {"type": "string", "minLength": 1},
                                    "value": {}
                                }
                            }
                        },
                        "disposition": {"type": "string", "enum": ["state", "event"]},
                        "evidence_slice_id": {"type": "string", "minLength": 1},
                        "evidence_quote": {"type": "string", "minLength": 1},
                        "valid_from": nullable_timestamp,
                        "valid_to": nullable_timestamp,
                    }
                }
            }
        }
    })
}

#[derive(Deserialize)]
struct ChatResponse {
    model: String,
    choices: Vec<Choice>,
}

#[derive(Deserialize)]
struct Choice {
    message: Message,
    #[serde(default)]
    finish_reason: Option<String>,
}

#[derive(Deserialize)]
struct Message {
    content: Option<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct WireResponse {
    observations: Vec<WireObservation>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct WireObservation {
    namespace: String,
    item_key: String,
    fields: Vec<WireField>,
    disposition: StructuredObservationDisposition,
    evidence_slice_id: String,
    evidence_quote: String,
    valid_from: Option<String>,
    valid_to: Option<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct WireField {
    key: String,
    value: Value,
}

fn decode_response(
    body: Value,
    request: &StructuredStateRequest,
) -> Result<Vec<StructuredObservation>, StructuredStateProviderError> {
    let response: ChatResponse = serde_json::from_value(body).map_err(invalid)?;
    if response.model.trim().is_empty() || response.choices.len() != 1 {
        return Err(invalid(
            "structured response has invalid model or choice count",
        ));
    }
    if response.choices[0].finish_reason.as_deref() == Some("length") {
        return Err(invalid(
            "structured response exhausted its output token budget",
        ));
    }
    let content = response.choices[0]
        .message
        .content
        .as_deref()
        .ok_or_else(|| invalid("structured response content is null"))?;
    let wire: WireResponse = serde_json::from_str(content).map_err(invalid)?;
    let slice_ids = request
        .evidence_slices
        .iter()
        .map(|slice| slice.id.as_str())
        .collect::<BTreeSet<_>>();
    wire.observations
        .into_iter()
        .map(|observation| {
            if !slice_ids.contains(observation.evidence_slice_id.as_str()) {
                return Err(invalid("observation names an unknown evidence slice"));
            }
            let mut fields = BTreeMap::new();
            for field in observation.fields {
                if field.key.is_empty() || fields.contains_key(&field.key) {
                    return Err(invalid(
                        "observation field keys must be nonempty and unique",
                    ));
                }
                fields.insert(field.key, field.value);
            }
            Ok(StructuredObservation {
                namespace: observation.namespace,
                item_key: observation.item_key,
                fields,
                disposition: observation.disposition,
                evidence_slice_id: observation.evidence_slice_id,
                evidence_quote: observation.evidence_quote,
                valid_from: observation.valid_from,
                valid_to: observation.valid_to,
            })
            .and_then(|observation| {
                validate_structured_observation(&observation)?;
                Ok(observation)
            })
        })
        .collect()
}

#[derive(Clone, Serialize, Deserialize)]
struct HttpResponse {
    status: u16,
    body: Value,
}

enum ExactAttemptState {
    Absent,
    Started {
        attempt_id: String,
        started_event_sha256: String,
    },
    Terminal {
        attempt_id: String,
        decoded: bool,
        started_event_sha256: String,
        result_event_sha256: String,
    },
}

fn exact_attempt_state(
    path: &Path,
    plan: &StructuredStateRequestPlan,
    campaign_attempt: usize,
) -> Result<ExactAttemptState, StructuredStateProviderError> {
    if !path.is_file() {
        return Ok(ExactAttemptState::Absent);
    }
    let mut file = File::open(path).map_err(|error| {
        StructuredStateProviderError::Unavailable(format!(
            "structured-state attempt ledger read failed: {error}"
        ))
    })?;
    file.lock_shared().map_err(|error| {
        StructuredStateProviderError::Unavailable(format!(
            "structured-state attempt ledger lock failed: {error}"
        ))
    })?;
    let mut body = String::new();
    file.read_to_string(&mut body).map_err(|error| {
        StructuredStateProviderError::Unavailable(format!(
            "structured-state attempt ledger read failed: {error}"
        ))
    })?;
    file.unlock().map_err(|error| {
        StructuredStateProviderError::Unavailable(format!(
            "structured-state attempt ledger unlock failed: {error}"
        ))
    })?;
    let mut started: Option<(String, String)> = None;
    let mut terminal: Option<(bool, String)> = None;
    for line in body.lines() {
        let event: Value = serde_json::from_str(line).map_err(|error| {
            StructuredStateProviderError::Unavailable(format!(
                "structured-state attempt ledger is malformed: {error}"
            ))
        })?;
        if event.get("extraction_key").and_then(Value::as_str) != Some(plan.extraction_key.as_str())
            || event.get("request_sha256").and_then(Value::as_str)
                != Some(plan.request_sha256.as_str())
            || event.get("campaign_attempt").and_then(Value::as_u64)
                != Some(campaign_attempt as u64)
        {
            continue;
        }
        match event.get("event").and_then(Value::as_str) {
            Some("started") if started.is_none() && terminal.is_none() => {
                let attempt_id = event
                    .get("attempt_id")
                    .and_then(Value::as_str)
                    .map(str::to_owned);
                let Some(attempt_id) = attempt_id else {
                    return Err(StructuredStateProviderError::Unavailable(
                        "structured-state started attempt id is missing".to_string(),
                    ));
                };
                started = Some((attempt_id, canonical_sha256(&event)));
            }
            Some("result") if started.is_some() && terminal.is_none() => {
                if event.get("attempt_id").and_then(Value::as_str)
                    != started.as_ref().map(|(attempt_id, _)| attempt_id.as_str())
                {
                    return Err(StructuredStateProviderError::Unavailable(
                        "structured-state terminal attempt identity drift".to_string(),
                    ));
                }
                terminal = Some((
                    event.get("parse_status").and_then(Value::as_str) == Some("decoded"),
                    canonical_sha256(&event),
                ));
            }
            _ => {
                return Err(StructuredStateProviderError::Unavailable(
                    "structured-state exact attempt history is forked".to_string(),
                ));
            }
        }
    }
    if let Some((decoded, result_event_sha256)) = terminal {
        let (attempt_id, started_event_sha256) = started.expect("terminal event has a start");
        Ok(ExactAttemptState::Terminal {
            attempt_id,
            decoded,
            started_event_sha256,
            result_event_sha256,
        })
    } else if let Some((attempt_id, started_event_sha256)) = started {
        Ok(ExactAttemptState::Started {
            attempt_id,
            started_event_sha256,
        })
    } else {
        Ok(ExactAttemptState::Absent)
    }
}

fn write_private_create_only(path: &Path, value: &Value) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut options = OpenOptions::new();
    options.create_new(true).write(true);
    #[cfg(unix)]
    options.mode(0o600);
    let mut file = options.open(path)?;
    serde_json::to_writer(&mut file, value)?;
    file.write_all(b"\n")?;
    file.sync_all()?;
    if let Some(parent) = path.parent() {
        File::open(parent)?.sync_all()?;
    }
    Ok(())
}

fn load_dispatch_response(
    path: &Path,
    attempt_id: &str,
    plan: &StructuredStateRequestPlan,
    campaign_attempt: usize,
    stage: &str,
) -> Result<HttpResponse, StructuredStateProviderError> {
    let record: Value = serde_json::from_slice(&fs::read(path).map_err(|error| {
        StructuredStateProviderError::Unavailable(format!(
            "structured-state dispatch journal read failed: {error}"
        ))
    })?)
    .map_err(|error| {
        StructuredStateProviderError::Unavailable(format!(
            "structured-state dispatch journal is malformed: {error}"
        ))
    })?;
    let value = record.get("core").ok_or_else(|| {
        StructuredStateProviderError::Unavailable(
            "structured-state dispatch journal core is missing".to_string(),
        )
    })?;
    let core_sha256 = canonical_sha256(value);
    if record.get("dispatch_sha256").and_then(Value::as_str) != Some(core_sha256.as_str())
        || value.get("schema_version").and_then(Value::as_u64) != Some(1)
        || value.get("stage").and_then(Value::as_str) != Some(stage)
        || value.get("campaign_attempt").and_then(Value::as_u64) != Some(campaign_attempt as u64)
        || value.get("attempt_id").and_then(Value::as_str) != Some(attempt_id)
        || value.get("extraction_key").and_then(Value::as_str) != Some(plan.extraction_key.as_str())
        || value.get("request_sha256").and_then(Value::as_str) != Some(plan.request_sha256.as_str())
    {
        return Err(StructuredStateProviderError::Unavailable(
            "structured-state dispatch journal authority drift".to_string(),
        ));
    }
    Ok(HttpResponse {
        status: value
            .get("status")
            .and_then(Value::as_u64)
            .and_then(|value| u16::try_from(value).ok())
            .ok_or_else(|| {
                StructuredStateProviderError::Unavailable(
                    "structured-state dispatch status is invalid".to_string(),
                )
            })?,
        body: value.get("body").cloned().ok_or_else(|| {
            StructuredStateProviderError::Unavailable(
                "structured-state dispatch body is missing".to_string(),
            )
        })?,
    })
}

trait Transport: Send + Sync {
    fn post(&self, body: &[u8]) -> Result<HttpResponse, String>;
    fn generation(&self, response_id: &str) -> Result<Value, String>;
}

struct UreqTransport {
    agent: Agent,
}

impl UreqTransport {
    fn new() -> Self {
        let config = Agent::config_builder()
            .timeout_connect(Some(CONNECT_TIMEOUT))
            .timeout_global(Some(GLOBAL_TIMEOUT))
            .http_status_as_error(false)
            .build();
        Self {
            agent: config.into(),
        }
    }
}

impl Transport for UreqTransport {
    fn post(&self, body: &[u8]) -> Result<HttpResponse, String> {
        let key = required_env("OPENROUTER_API_KEY")?;
        let mut response = self
            .agent
            .post(URL)
            .header("authorization", &format!("Bearer {key}"))
            .header("content-type", "application/json")
            .header("http-referer", "https://github.com/memphant")
            .header("x-title", "memphant-structured-state")
            .header("x-openrouter-metadata", "enabled")
            .send(body)
            .map_err(|_| "OpenRouter transport error".to_string())?;
        let status = response.status().as_u16();
        let response_id = response
            .headers()
            .get("x-generation-id")
            .and_then(|value| value.to_str().ok())
            .map(str::to_owned);
        let mut body = response
            .body_mut()
            .with_config()
            .limit(RESPONSE_LIMIT)
            .read_json()
            .map_err(|_| "OpenRouter response decode failed".to_string())?;
        backfill_response_id(&mut body, response_id.as_deref());
        Ok(HttpResponse { status, body })
    }

    fn generation(&self, response_id: &str) -> Result<Value, String> {
        let key = required_env("OPENROUTER_API_KEY")?;
        for (index, delay_seconds) in [1_u64, 2, 4, 8, 16, 0].into_iter().enumerate() {
            let mut response = self
                .agent
                .get(GENERATION_URL)
                .query("id", response_id)
                .header("authorization", &format!("Bearer {key}"))
                .header("http-referer", "https://github.com/memphant")
                .header("x-title", "memphant-structured-state")
                .header("x-openrouter-metadata", "enabled")
                .call()
                .map_err(|_| "OpenRouter generation statistics transport error".to_string())?;
            let status = response.status().as_u16();
            if (200..300).contains(&status) {
                return response
                    .body_mut()
                    .with_config()
                    .limit(RESPONSE_LIMIT)
                    .read_json()
                    .map_err(|_| "OpenRouter generation statistics decode failed".to_string());
            }
            if status != 404 || index == 5 {
                return Err("OpenRouter generation statistics HTTP error".to_string());
            }
            std::thread::sleep(Duration::from_secs(delay_seconds));
        }
        unreachable!("bounded generation lookup loop always returns")
    }
}

fn backfill_response_id(body: &mut Value, header: Option<&str>) {
    if body.get("id").and_then(Value::as_str).is_some() {
        return;
    }
    if let Some(response_id) = header.filter(|value| !value.is_empty())
        && let Some(body) = body.as_object_mut()
    {
        body.insert("id".to_string(), json!(response_id));
    }
}

fn reconcile_generation(
    transport: &dyn Transport,
    response: &HttpResponse,
) -> Result<HttpResponse, String> {
    let response_id = response
        .body
        .get("id")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| "OpenRouter paid response omitted its response id".to_string())?;
    let statistics = transport.generation(response_id)?;
    let data = statistics
        .get("data")
        .and_then(Value::as_object)
        .ok_or_else(|| "OpenRouter generation statistics omitted data".to_string())?;
    if data.get("id").and_then(Value::as_str) != Some(response_id) {
        return Err("OpenRouter generation statistics response id mismatch".to_string());
    }
    let model = required_string(data, "model")?;
    let provider = required_string(data, "provider_name")?;
    let prompt_tokens = required_positive_u64(data, "tokens_prompt")?;
    let completion_tokens = required_positive_u64(data, "tokens_completion")?;
    let cost = data
        .get("total_cost")
        .and_then(Value::as_f64)
        .filter(|value| value.is_finite() && *value > 0.0)
        .ok_or_else(|| "OpenRouter generation statistics omitted positive cost".to_string())?;
    let mut body = response.body.clone();
    body["model"] = json!(model);
    body["provider"] = json!(provider);
    body["usage"] = json!({
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "cost": cost,
    });
    Ok(HttpResponse {
        status: response.status,
        body,
    })
}

fn required_string<'a>(
    data: &'a serde_json::Map<String, Value>,
    key: &str,
) -> Result<&'a str, String> {
    data.get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| format!("OpenRouter generation statistics omitted {key}"))
}

fn required_positive_u64(data: &serde_json::Map<String, Value>, key: &str) -> Result<u64, String> {
    data.get(key)
        .and_then(Value::as_u64)
        .filter(|value| *value > 0)
        .ok_or_else(|| format!("OpenRouter generation statistics omitted {key}"))
}

fn openrouter_error_message(body: &Value) -> &str {
    body.pointer("/error/metadata/raw")
        .and_then(Value::as_str)
        .or_else(|| body.pointer("/error/message").and_then(Value::as_str))
        .unwrap_or("unstructured provider error")
}

#[derive(Serialize)]
struct AttemptEvent {
    schema_version: u8,
    event: &'static str,
    attempt_id: String,
    source_kind: memphant_core::StructuredSourceKind,
    source_body_sha256: String,
    batch_index: usize,
    extraction_key: String,
    request_sha256: String,
    requested_model: String,
    attempt: usize,
    campaign_attempt: usize,
    maximum_attempts: usize,
    per_attempt_reservation_nanos: u64,
    maximum_reservation_nanos: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    http_status: Option<u16>,
    #[serde(skip_serializing_if = "Option::is_none")]
    served_model: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    served_provider: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    response_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    usage: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    observation_count: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    parse_status: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    result_sha256: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    observation_sha256: Option<String>,
    reservation_status: &'static str,
    elapsed_seconds: f64,
}

impl AttemptEvent {
    fn base(
        event: &'static str,
        attempt_id: &str,
        request: &StructuredStateRequest,
        plan: &StructuredStateRequestPlan,
        model: &str,
        attempt: usize,
        elapsed: Duration,
    ) -> Self {
        Self {
            schema_version: 3,
            event,
            attempt_id: attempt_id.to_string(),
            source_kind: request.source_kind,
            source_body_sha256: request.source_body_sha256.clone(),
            batch_index: request.batch_index,
            extraction_key: plan.extraction_key.clone(),
            request_sha256: plan.request_sha256.clone(),
            requested_model: model.to_string(),
            attempt,
            campaign_attempt: 1,
            maximum_attempts: plan.maximum_attempts,
            per_attempt_reservation_nanos: plan.per_attempt_reservation_nanos,
            maximum_reservation_nanos: plan.maximum_reservation_nanos,
            http_status: None,
            served_model: None,
            served_provider: None,
            response_id: None,
            usage: None,
            observation_count: None,
            error: None,
            parse_status: None,
            result_sha256: None,
            observation_sha256: None,
            reservation_status: "reserved",
            elapsed_seconds: elapsed.as_secs_f64(),
        }
    }

    fn for_campaign_attempt(mut self, campaign_attempt: usize) -> Self {
        self.campaign_attempt = campaign_attempt;
        self
    }

    fn started(
        attempt_id: &str,
        request: &StructuredStateRequest,
        plan: &StructuredStateRequestPlan,
        model: &str,
        attempt: usize,
    ) -> Self {
        Self::base(
            "started",
            attempt_id,
            request,
            plan,
            model,
            attempt,
            Duration::ZERO,
        )
    }

    fn failed(
        attempt_id: &str,
        request: &StructuredStateRequest,
        plan: &StructuredStateRequestPlan,
        model: &str,
        attempt: usize,
        error: &str,
        elapsed: Duration,
    ) -> Self {
        let mut event = Self::base("result", attempt_id, request, plan, model, attempt, elapsed);
        event.error = Some(error.to_string());
        event.parse_status = Some(match error {
            "http_error" => "http_error",
            "generation_stats_lookup_failed" => "generation_stats_lookup_failed",
            _ => "provider_error",
        });
        event.reservation_status = "unresolved";
        event
    }

    fn http_error(
        attempt_id: &str,
        request: &StructuredStateRequest,
        plan: &StructuredStateRequestPlan,
        model: &str,
        attempt: usize,
        response: &HttpResponse,
        elapsed: Duration,
    ) -> Self {
        let mut event = Self::failed(
            attempt_id,
            request,
            plan,
            model,
            attempt,
            "http_error",
            elapsed,
        );
        event.http_status = Some(response.status);
        event.result_sha256 = Some(sha256(
            serde_json::to_vec(&response.body)
                .expect("provider error response serializes")
                .as_slice(),
        ));
        if is_typed_not_charged_pre_generation(response) {
            event.reservation_status = "not_charged";
        }
        event
    }

    #[allow(clippy::too_many_arguments)]
    fn reconciled(
        attempt_id: &str,
        request: &StructuredStateRequest,
        plan: &StructuredStateRequestPlan,
        model: &str,
        attempt: usize,
        response: &HttpResponse,
        observations: Option<&[StructuredObservation]>,
        parse_status: &'static str,
        error: Option<&str>,
        elapsed: Duration,
    ) -> Self {
        let mut event = Self::base("result", attempt_id, request, plan, model, attempt, elapsed);
        event.http_status = Some(response.status);
        event.served_model = response
            .body
            .get("model")
            .and_then(Value::as_str)
            .map(str::to_owned);
        event.served_provider = response
            .body
            .get("provider")
            .and_then(Value::as_str)
            .map(str::to_owned);
        event.response_id = response
            .body
            .get("id")
            .and_then(Value::as_str)
            .map(str::to_owned);
        event.usage = response.body.get("usage").cloned();
        event.result_sha256 = Some(sha256(
            serde_json::to_vec(&response.body)
                .expect("reconciled provider response serializes")
                .as_slice(),
        ));
        event.parse_status = Some(parse_status);
        event.reservation_status = "settled";
        event.error = error.map(str::to_owned);
        if let Some(observations) = observations {
            event.observation_count = Some(observations.len());
            event.observation_sha256 = Some(canonical_sha256(observations));
        }
        event
    }

    #[allow(clippy::too_many_arguments)]
    fn completed(
        attempt_id: &str,
        request: &StructuredStateRequest,
        plan: &StructuredStateRequestPlan,
        model: &str,
        attempt: usize,
        response: &HttpResponse,
        observations: &[StructuredObservation],
        elapsed: Duration,
    ) -> Self {
        Self::reconciled(
            attempt_id,
            request,
            plan,
            model,
            attempt,
            response,
            Some(observations),
            "decoded",
            None,
            elapsed,
        )
    }
}

fn is_typed_not_charged_pre_generation(response: &HttpResponse) -> bool {
    let expected_type = match response.status {
        429 => "rate_limit_exceeded",
        502 => "provider_unavailable",
        503 => "provider_overloaded",
        _ => return false,
    };
    let error = match response.body.get("error").and_then(Value::as_object) {
        Some(error) => error,
        None => return false,
    };
    error.get("code").and_then(Value::as_u64) == Some(response.status.into())
        && error
            .get("message")
            .and_then(Value::as_str)
            .is_some_and(|message| !message.is_empty())
        && error
            .get("metadata")
            .and_then(Value::as_object)
            .and_then(|metadata| metadata.get("error_type"))
            .and_then(Value::as_str)
            == Some(expected_type)
        && response.body.get("id").is_none()
        && response.body.get("usage").is_none()
        && response.body.get("choices").is_none()
}

fn append_json_line(
    path: &Path,
    event: &AttemptEvent,
    aggregate_reservation_nanos: Option<u64>,
) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut file = OpenOptions::new()
        .create(true)
        .read(true)
        .append(true)
        .open(path)?;
    file.lock_exclusive()?;
    let mut prior = String::new();
    file.read_to_string(&mut prior)?;
    let mut reserved = 0_u64;
    for line in prior.lines() {
        let value: Value = serde_json::from_str(line).map_err(|error| {
            std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                format!("malformed structured-state attempt ledger: {error}"),
            )
        })?;
        if value.get("schema_version").and_then(Value::as_u64) != Some(3)
            || !matches!(
                value.get("event").and_then(Value::as_str),
                Some("started" | "result")
            )
        {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                "malformed structured-state attempt ledger event",
            ));
        }
        if value.get("event").and_then(Value::as_str) == Some("started") {
            reserved = reserved
                .checked_add(
                    value
                        .get("per_attempt_reservation_nanos")
                        .and_then(Value::as_u64)
                        .filter(|value| *value > 0)
                        .ok_or_else(|| {
                            std::io::Error::new(
                                std::io::ErrorKind::InvalidData,
                                "malformed structured-state started reservation",
                            )
                        })?,
                )
                .ok_or_else(|| {
                    std::io::Error::new(
                        std::io::ErrorKind::InvalidData,
                        "structured-state attempt reservation overflow",
                    )
                })?;
        }
    }
    if event.event == "started" {
        let cap = aggregate_reservation_nanos.ok_or_else(|| {
            std::io::Error::new(
                std::io::ErrorKind::PermissionDenied,
                "structured-state aggregate reservation is required before provider access",
            )
        })?;
        if reserved
            .checked_add(event.per_attempt_reservation_nanos)
            .is_none_or(|next| next > cap)
        {
            return Err(std::io::Error::new(
                std::io::ErrorKind::PermissionDenied,
                "structured-state aggregate reservation exhausted",
            ));
        }
    }
    serde_json::to_writer(&mut file, event)?;
    file.write_all(b"\n")?;
    file.sync_all()
}

fn sha256(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn invalid(error: impl std::fmt::Display) -> StructuredStateProviderError {
    StructuredStateProviderError::InvalidOutput(error.to_string())
}

#[cfg(test)]
mod tests {
    use std::collections::VecDeque;

    use memphant_core::{
        StructuredSourceKind, evidence_slices_for_episode, evidence_slices_for_resource,
    };

    use super::*;

    #[test]
    fn serde_json_number_encoding_matches_campaign_python_contract() {
        for (value, expected) in [
            (1.75e-6, "{\"value\":1.75e-6}"),
            (1e-7, "{\"value\":1e-7}"),
            (-1e-7, "{\"value\":-1e-7}"),
            (1e20, "{\"value\":1e+20}"),
            (-0.0, "{\"value\":-0.0}"),
        ] {
            assert_eq!(
                serde_json::to_string(&json!({"value": value})).unwrap(),
                expected
            );
        }
    }

    const INPUT_PRICE: u64 = 2_000_000_000;
    const OUTPUT_PRICE: u64 = 10_000_000_000;

    struct FakeTransport {
        responses: Mutex<VecDeque<Result<HttpResponse, String>>>,
        generation_responses: Mutex<VecDeque<Result<Value, String>>>,
        posted: Mutex<Vec<Vec<u8>>>,
    }

    impl FakeTransport {
        fn new(responses: Vec<Result<HttpResponse, String>>) -> Arc<Self> {
            Arc::new(Self {
                responses: Mutex::new(responses.into()),
                generation_responses: Mutex::new(VecDeque::new()),
                posted: Mutex::new(Vec::new()),
            })
        }

        fn with_generation_response(
            responses: Vec<Result<HttpResponse, String>>,
            generation: Result<Value, String>,
        ) -> Arc<Self> {
            Arc::new(Self {
                responses: Mutex::new(responses.into()),
                generation_responses: Mutex::new(VecDeque::from([generation])),
                posted: Mutex::new(Vec::new()),
            })
        }
    }

    impl Transport for FakeTransport {
        fn post(&self, body: &[u8]) -> Result<HttpResponse, String> {
            self.posted.lock().unwrap().push(body.to_vec());
            self.responses.lock().unwrap().pop_front().unwrap()
        }

        fn generation(&self, response_id: &str) -> Result<Value, String> {
            if let Some(response) = self.generation_responses.lock().unwrap().pop_front() {
                return response;
            }
            Ok(json!({"data": {
                "id": response_id,
                "model": "served/model",
                "provider_name": "served-provider",
                "tokens_prompt": 10,
                "tokens_completion": 5,
                "total_cost": 0.001
            }}))
        }
    }

    fn prompt_fixture() -> String {
        fs::read_to_string(
            Path::new(env!("CARGO_MANIFEST_DIR")).join("../../config/structured-state-v2.txt"),
        )
        .unwrap()
    }

    fn request(body: &str) -> StructuredStateRequest {
        StructuredStateRequest {
            source_kind: StructuredSourceKind::Episode,
            source_body_sha256: sha256(body.as_bytes()),
            batch_index: 0,
            evidence_slices: evidence_slices_for_episode(body).unwrap(),
        }
    }

    fn provider(transport: Arc<dyn Transport>) -> OpenRouterStructuredState {
        OpenRouterStructuredState::new(
            DEFAULT_MODEL.to_string(),
            prompt_fixture(),
            INPUT_PRICE,
            OUTPUT_PRICE,
            transport,
            None,
        )
    }

    fn response(content: Value) -> HttpResponse {
        HttpResponse {
            status: 200,
            body: json!({
                "id": "generation-1",
                "model": DEFAULT_MODEL,
                "choices": [{"message": {"content": content.to_string()}}]
            }),
        }
    }

    fn reconciled_body(content: Value) -> Value {
        json!({
            "id": "generation-1",
            "model": "served/model",
            "provider": "served-provider",
            "choices": [{"message": {"content": content.to_string()}}],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "cost": 0.001
            }
        })
    }

    fn provider_with_ledger(transport: Arc<dyn Transport>) -> (OpenRouterStructuredState, PathBuf) {
        let ledger = std::env::temp_dir().join(format!(
            "memphant-structured-observation-{}.jsonl",
            uuid::Uuid::new_v4()
        ));
        let mut provider = OpenRouterStructuredState::new(
            DEFAULT_MODEL.to_string(),
            "prompt".to_string(),
            INPUT_PRICE,
            OUTPUT_PRICE,
            transport,
            Some(ledger.clone()),
        );
        provider.dispatch_root = Some(ledger.with_extension("dispatch"));
        provider.aggregate_reservation_nanos = Some(u64::MAX);
        (provider, ledger)
    }

    fn restarted_provider(
        transport: Arc<dyn Transport>,
        ledger: &Path,
        dispatch_root: &Path,
    ) -> OpenRouterStructuredState {
        let mut provider = OpenRouterStructuredState::new(
            DEFAULT_MODEL.to_string(),
            "prompt".to_string(),
            INPUT_PRICE,
            OUTPUT_PRICE,
            transport,
            Some(ledger.to_path_buf()),
        );
        provider.dispatch_root = Some(dispatch_root.to_path_buf());
        provider.aggregate_reservation_nanos = Some(u64::MAX);
        provider
    }

    fn read_events(ledger: &Path) -> Vec<Value> {
        let events = fs::read_to_string(ledger)
            .unwrap()
            .lines()
            .map(|line| serde_json::from_str::<Value>(line).unwrap())
            .collect();
        fs::remove_file(ledger).unwrap();
        events
    }

    fn ledger_prefix(ledger: &Path) -> (usize, String) {
        let bytes = fs::read(ledger).unwrap_or_default();
        (bytes.len(), sha256(&bytes))
    }

    fn wire_observation(slice_id: &str) -> Value {
        json!({
            "namespace": "profile",
            "item_key": "city",
            "fields": [{"key": "value", "value": "Oslo"}],
            "disposition": "state",
            "evidence_slice_id": slice_id,
            "evidence_quote": "I live in Oslo.",
            "valid_from": null,
            "valid_to": null
        })
    }

    #[test]
    fn request_uses_only_source_neutral_strict_observation_fields() {
        let request = request("user: I live in Oslo.");
        let plan = plan_structured_state_request(
            &request,
            DEFAULT_MODEL,
            &prompt_fixture(),
            None,
            INPUT_PRICE,
            OUTPUT_PRICE,
        )
        .unwrap();
        let value: Value = serde_json::from_slice(&plan.serialized_request).unwrap();
        let content = value["messages"][1]["content"].as_str().unwrap();
        assert!(content.contains("evidence_slices"));
        assert!(content.contains("source_body_sha256"));
        for forbidden in [
            "episode_id",
            "resource_id",
            "active_state",
            "target_unit_ids",
        ] {
            assert!(!content.contains(forbidden));
        }
        let item = &value["response_format"]["json_schema"]["schema"]["properties"]["observations"]
            ["items"];
        assert_eq!(item["additionalProperties"], false);
        assert!(item["properties"].get("operation").is_none());
        assert!(item["properties"].get("target_unit_ids").is_none());
    }

    #[test]
    fn strict_decode_accepts_known_slice_and_rejects_unknown_or_extra_fields() {
        let request = request("user: I live in Oslo.");
        let known = wire_observation(&request.evidence_slices[0].id);
        let decoded = decode_response(
            response(json!({"observations": [known.clone()]})).body,
            &request,
        )
        .unwrap();
        assert_eq!(decoded[0].fields["value"], "Oslo");

        let mut direct_values = wire_observation(&request.evidence_slices[0].id);
        direct_values["fields"] = json!([
            {"key": "string", "value": "Oslo"},
            {"key": "number", "value": 42.5},
            {"key": "boolean", "value": true},
            {"key": "object", "value": {"nested": "value"}},
            {"key": "array", "value": [1, "two"]},
            {"key": "null", "value": null}
        ]);
        let decoded = decode_response(
            response(json!({"observations": [direct_values]})).body,
            &request,
        )
        .unwrap();
        assert_eq!(decoded[0].fields["string"], json!("Oslo"));
        assert_eq!(decoded[0].fields["number"], json!(42.5));
        assert_eq!(decoded[0].fields["boolean"], json!(true));
        assert_eq!(decoded[0].fields["object"], json!({"nested": "value"}));
        assert_eq!(decoded[0].fields["array"], json!([1, "two"]));
        assert_eq!(decoded[0].fields["null"], Value::Null);

        let mut observed_invalid_v1 = wire_observation(&request.evidence_slices[0].id);
        observed_invalid_v1["fields"] = json!([{"key": "value", "value_json": "Oslo"}]);
        assert!(
            decode_response(
                response(json!({"observations": [observed_invalid_v1]})).body,
                &request,
            )
            .is_err()
        );

        let mut unknown = known.clone();
        unknown["evidence_slice_id"] = json!("slice-unknown");
        assert!(
            decode_response(response(json!({"observations": [unknown]})).body, &request).is_err()
        );
        let mut extra = known;
        extra["operation"] = json!("replace");
        assert!(
            decode_response(response(json!({"observations": [extra]})).body, &request).is_err()
        );

        for key in [
            "operation",
            "target_unit_ids",
            "source_span",
            " ",
            "CamelCase",
        ] {
            let mut nested = wire_observation(&request.evidence_slices[0].id);
            nested["fields"] = json!([{"key": key, "value": true}]);
            assert!(
                decode_response(response(json!({"observations": [nested]})).body, &request)
                    .is_err(),
                "nested field key {key:?} must fail closed"
            );
        }
    }

    #[test]
    fn decode_rejects_observed_length_finish_before_content_decode() {
        let request = request("user: I live in Oslo.");
        let body = json!({
            "model": DEFAULT_MODEL,
            "choices": [{
                "finish_reason": "length",
                "message": {"content": null}
            }]
        });
        let error = decode_response(body, &request).unwrap_err().to_string();
        assert!(error.contains("output token budget"));
    }

    #[test]
    fn planner_identity_drifts_for_every_extraction_input() {
        let request = request("user: I live in Oslo.");
        let plan = |request: &StructuredStateRequest, model: &str, prompt: &str| {
            plan_structured_state_request(request, model, prompt, None, INPUT_PRICE, OUTPUT_PRICE)
                .unwrap()
        };
        let base = plan(&request, DEFAULT_MODEL, "prompt-a");
        let mut next_batch = request.clone();
        next_batch.batch_index = 1;
        assert_ne!(
            base.extraction_key,
            plan(&next_batch, DEFAULT_MODEL, "prompt-a").extraction_key
        );
        assert_ne!(
            base.extraction_key,
            plan(&request, "other/model", "prompt-a").extraction_key
        );
        assert_ne!(
            base.extraction_key,
            plan(&request, DEFAULT_MODEL, "prompt-b").extraction_key
        );
        assert_eq!(base.request_sha256, sha256(&base.serialized_request));
    }

    #[test]
    fn reservation_rounds_up_and_covers_every_retry() {
        let plan = plan_structured_state_request(
            &request("user: hello"),
            DEFAULT_MODEL,
            "prompt",
            None,
            1,
            1,
        )
        .unwrap();
        assert!(plan.per_attempt_reservation_nanos > 0);
        assert_eq!(
            plan.maximum_reservation_nanos,
            plan.per_attempt_reservation_nanos * 3
        );
    }

    #[test]
    fn provider_never_hides_a_retry_inside_one_campaign_wave_attempt() {
        let request = request("user: hello");
        let transport = FakeTransport::new(vec![
            Ok(HttpResponse {
                status: 503,
                body: json!({"error": {"message": "retry later"}}),
            }),
            Ok(response(json!({"observations": []}))),
        ]);

        assert!(provider(transport.clone()).extract_sync(&request).is_err());
        assert_eq!(transport.posted.lock().unwrap().len(), 1);
    }

    #[test]
    fn only_typed_pre_generation_capacity_errors_are_not_charged() {
        let request = request("user: hello");
        let typed = FakeTransport::new(vec![Ok(HttpResponse {
            status: 503,
            body: json!({
                "error": {
                    "code": 503,
                    "message": "provider overloaded",
                    "metadata": {"error_type": "provider_overloaded"}
                }
            }),
        })]);
        let (provider, ledger) = provider_with_ledger(typed);
        assert!(provider.extract_sync(&request).is_err());
        let events = read_events(&ledger);
        assert_eq!(events[1]["reservation_status"], "not_charged");
        assert_eq!(events[1]["http_status"], 503);
        assert!(events[1]["result_sha256"].as_str().unwrap().len() == 64);
        assert!(events[1].get("response_id").is_none());
        assert!(events[1].get("usage").is_none());

        let ambiguous = HttpResponse {
            status: 503,
            body: json!({
                "id": "generation-maybe-started",
                "error": {
                    "code": 503,
                    "message": "provider overloaded",
                    "metadata": {"error_type": "provider_overloaded"}
                }
            }),
        };
        assert!(!is_typed_not_charged_pre_generation(&ambiguous));
    }

    #[test]
    fn aggregate_cap_survives_restart_and_malformed_ledger_fails_before_transport() {
        let request = request("user: cap me");
        let first_transport = FakeTransport::new(vec![Ok(response(json!({
            "observations": [wire_observation(&request.evidence_slices[0].id)]
        })))]);
        let (mut first, ledger) = provider_with_ledger(first_transport);
        let dispatch_root = first.dispatch_root.clone().unwrap();
        let one_attempt = first.plan(&request).unwrap().per_attempt_reservation_nanos;
        first.aggregate_reservation_nanos = Some(one_attempt);
        first.extract_sync(&request).unwrap();

        let duplicate_transport = FakeTransport::new(vec![Ok(response(json!({
            "observations": [wire_observation(&request.evidence_slices[0].id)]
        })))]);
        let mut restarted =
            restarted_provider(duplicate_transport.clone(), &ledger, &dispatch_root);
        restarted.aggregate_reservation_nanos = Some(one_attempt);
        restarted.extract_sync(&request).unwrap();
        assert!(duplicate_transport.posted.lock().unwrap().is_empty());
        fs::remove_file(&ledger).unwrap();
        fs::remove_dir_all(&dispatch_root).unwrap();

        fs::write(&ledger, b"{malformed\n").unwrap();
        let malformed_transport = FakeTransport::new(vec![Ok(response(json!({
            "observations": []
        })))]);
        let mut malformed = OpenRouterStructuredState::new(
            DEFAULT_MODEL.to_string(),
            "prompt".to_string(),
            INPUT_PRICE,
            OUTPUT_PRICE,
            malformed_transport.clone(),
            Some(ledger.clone()),
        );
        malformed.aggregate_reservation_nanos = Some(u64::MAX);
        assert!(malformed.extract_sync(&request).is_err());
        assert!(malformed_transport.posted.lock().unwrap().is_empty());
        fs::remove_file(ledger).unwrap();
    }

    #[test]
    fn oversized_request_is_rejected_before_transport() {
        let body = format!("user: {}", "\\\"".repeat(MAX_REQUEST_BYTES));
        let request = request(&body);
        let transport = FakeTransport::new(vec![]);
        let error = provider(transport.clone())
            .extract_sync(&request)
            .unwrap_err();
        assert!(error.to_string().contains("131072-byte limit"));
        assert!(transport.posted.lock().unwrap().is_empty());
    }

    #[test]
    fn request_at_exactly_128_kib_is_rejected() {
        let mut request = StructuredStateRequest {
            source_kind: StructuredSourceKind::Resource,
            source_body_sha256: "0".repeat(64),
            batch_index: 0,
            evidence_slices: vec![memphant_core::EvidenceSlice {
                id: "slice-test".to_string(),
                body: String::new(),
                source_span: "0-1".to_string(),
            }],
        };
        let base = plan_structured_state_request(
            &request,
            DEFAULT_MODEL,
            "prompt",
            None,
            INPUT_PRICE,
            OUTPUT_PRICE,
        )
        .unwrap()
        .serialized_request
        .len();
        request.evidence_slices[0].body = "a".repeat(MAX_REQUEST_BYTES - base - 1);
        let below = plan_structured_state_request(
            &request,
            DEFAULT_MODEL,
            "prompt",
            None,
            INPUT_PRICE,
            OUTPUT_PRICE,
        )
        .unwrap();
        assert_eq!(below.serialized_request.len(), MAX_REQUEST_BYTES - 1);
        request.evidence_slices[0].body.push('a');
        assert!(
            plan_structured_state_request(
                &request,
                DEFAULT_MODEL,
                "prompt",
                None,
                INPUT_PRICE,
                OUTPUT_PRICE,
            )
            .unwrap_err()
            .to_string()
            .contains("131072-byte limit")
        );
    }

    #[test]
    fn dispatch_consumes_the_exact_planned_bytes() {
        let request = request("user: I live in Oslo.");
        let transport = FakeTransport::new(vec![Ok(response(json!({
            "observations": [wire_observation(&request.evidence_slices[0].id)]
        })))]);
        let provider = provider(transport.clone());
        let plan = provider.plan(&request).unwrap();
        provider.extract_sync(&request).unwrap();
        assert_eq!(
            transport.posted.lock().unwrap().as_slice(),
            &[plan.serialized_request]
        );
    }

    #[test]
    fn resource_request_never_exposes_its_local_id() {
        let body = "deploy notes";
        let request = StructuredStateRequest {
            source_kind: StructuredSourceKind::Resource,
            source_body_sha256: sha256(body.as_bytes()),
            batch_index: 4,
            evidence_slices: evidence_slices_for_resource(body, &[]).unwrap(),
        };
        let local_id = "00000000-0000-0000-0000-000000000007";
        let plan = plan_structured_state_request(
            &request,
            DEFAULT_MODEL,
            "prompt",
            None,
            INPUT_PRICE,
            OUTPUT_PRICE,
        )
        .unwrap();
        assert!(
            !String::from_utf8(plan.serialized_request)
                .unwrap()
                .contains(local_id)
        );
    }

    #[test]
    fn official_qwen_census_pins_one_provider_without_fallback() {
        let request = StructuredStateRequest {
            source_kind: StructuredSourceKind::Resource,
            source_body_sha256: sha256(b"state"),
            batch_index: 0,
            evidence_slices: evidence_slices_for_resource("state", &[]).unwrap(),
        };
        let plan = plan_structured_state_request(
            &request,
            "qwen/qwen3.5-9b-20260310",
            "prompt",
            None,
            100_000_000,
            150_000_000,
        )
        .unwrap();
        let body: Value = serde_json::from_slice(&plan.serialized_request).unwrap();
        assert_eq!(body["provider"]["only"], json!(["deepinfra"]));
        assert_eq!(body["provider"]["allow_fallbacks"], false);
        assert_eq!(body["reasoning"], json!({"effort": "none"}));
        let field_schema = &body["response_format"]["json_schema"]["schema"]["properties"]["observations"]
            ["items"]["properties"]["fields"]["items"];
        assert_eq!(field_schema["required"], json!(["key", "value"]));
        assert!(field_schema["properties"].get("value_json").is_none());
    }

    #[test]
    fn greedy_batches_are_ordered_and_maximal_under_the_exact_request_planner() {
        use std::cell::Cell;

        let slices = (0..300)
            .map(|index| EvidenceSlice {
                id: format!("slice-{index:04}"),
                body: format!("{}-{index:04}", "x".repeat(1_000)),
                source_span: format!("{}-{}", index * 1_001, (index + 1) * 1_001),
            })
            .collect::<Vec<_>>();
        let batches = plan_structured_state_batches(
            StructuredSourceKind::Resource,
            &"a".repeat(64),
            slices.clone(),
            LME_V2_QWEN_MODEL,
            "prompt",
            None,
            100_000_000,
            150_000_000,
            None,
        )
        .unwrap();
        assert!(batches.len() < slices.len());
        assert_eq!(
            batches
                .iter()
                .flat_map(|batch| batch.evidence_slices.clone())
                .collect::<Vec<_>>(),
            slices
        );
        assert!(batches.iter().all(|batch| {
            plan_structured_state_request(
                batch,
                LME_V2_QWEN_MODEL,
                "prompt",
                None,
                100_000_000,
                150_000_000,
            )
            .is_ok()
        }));
        for pair in batches.windows(2) {
            let mut extended = pair[0].clone();
            extended
                .evidence_slices
                .push(pair[1].evidence_slices[0].clone());
            assert!(
                plan_structured_state_request(
                    &extended,
                    LME_V2_QWEN_MODEL,
                    "prompt",
                    None,
                    100_000_000,
                    150_000_000,
                )
                .is_err()
            );
        }
        let size_calls = Cell::new(0_usize);
        let final_calls = Cell::new(0_usize);
        let counted = plan_structured_state_batches_with(
            StructuredSourceKind::Resource,
            &"a".repeat(64),
            slices.clone(),
            |request| {
                size_calls.set(size_calls.get() + 1);
                plan_structured_state_request(
                    request,
                    LME_V2_QWEN_MODEL,
                    "prompt",
                    None,
                    100_000_000,
                    150_000_000,
                )
            },
            |request| {
                final_calls.set(final_calls.get() + 1);
                plan_structured_state_request(
                    request,
                    LME_V2_QWEN_MODEL,
                    "prompt",
                    None,
                    100_000_000,
                    150_000_000,
                )
            },
        )
        .unwrap();
        assert_eq!(counted, batches);
        assert_eq!(final_calls.get(), batches.len());
        assert!(size_calls.get() < slices.len());
    }

    #[test]
    fn attempt_receipt_binds_source_key_requested_and_served_identity() {
        let body = "user: I live in Oslo.";
        let request = request(body);
        let transport = FakeTransport::new(vec![Ok(response(json!({
            "observations": [wire_observation(&request.evidence_slices[0].id)]
        })))]);
        let (provider, ledger) = provider_with_ledger(transport);
        let provider = provider.with_campaign_attempt(2).unwrap();
        provider.extract_sync(&request).unwrap();
        let events = read_events(&ledger);
        assert_eq!(events[0]["source_kind"], "episode");
        assert_eq!(events[0]["source_body_sha256"], request.source_body_sha256);
        assert!(events[0]["extraction_key"].as_str().unwrap().len() == 64);
        assert!(events[0].get("episode_id").is_none());
        assert_eq!(events[0]["requested_model"], DEFAULT_MODEL);
        assert_eq!(events[0]["campaign_attempt"], 2);
        assert_eq!(events[1]["campaign_attempt"], 2);
        assert_eq!(events[1]["served_model"], "served/model");
        assert_eq!(events[1]["served_provider"], "served-provider");
        assert_eq!(events[1]["response_id"], "generation-1");
        assert_eq!(events[1]["parse_status"], "decoded");
        assert_eq!(events[1]["reservation_status"], "settled");
        assert_eq!(events[1]["usage"]["cost"], 0.001);
        let content = json!({
            "observations": [wire_observation(&request.evidence_slices[0].id)]
        });
        assert_eq!(
            events[1]["result_sha256"],
            sha256(&serde_json::to_vec(&reconciled_body(content)).unwrap())
        );
        let expected_observations = vec![StructuredObservation {
            namespace: "profile".to_string(),
            item_key: "city".to_string(),
            fields: BTreeMap::from([("value".to_string(), json!("Oslo"))]),
            disposition: StructuredObservationDisposition::State,
            evidence_slice_id: request.evidence_slices[0].id.clone(),
            evidence_quote: "I live in Oslo.".to_string(),
            valid_from: None,
            valid_to: None,
        }];
        assert_eq!(
            events[1]["observation_sha256"],
            canonical_sha256(&expected_observations)
        );
    }

    #[test]
    fn malformed_paid_response_keeps_reconciled_identity_usage_and_result_hash() {
        let request = request("user: I live in Oslo.");
        let malformed = json!({"observations": [{"operation": "replace"}]});
        let transport = FakeTransport::new(vec![Ok(response(malformed.clone()))]);
        let (provider, ledger) = provider_with_ledger(transport);
        assert!(provider.extract_sync(&request).is_err());
        let events = read_events(&ledger);
        let result = &events[1];
        assert_eq!(result["response_id"], "generation-1");
        assert_eq!(result["served_model"], "served/model");
        assert_eq!(result["served_provider"], "served-provider");
        assert_eq!(result["usage"]["cost"], 0.001);
        assert_eq!(result["parse_status"], "response_decode_error");
        assert_eq!(result["reservation_status"], "settled");
        assert_eq!(
            result["result_sha256"],
            sha256(&serde_json::to_vec(&reconciled_body(malformed)).unwrap())
        );
        assert!(result.get("observation_sha256").is_none());
        assert!(result.get("choices").is_none());
    }

    #[test]
    fn response_before_terminal_resumes_generation_without_redispatch() {
        let request = request("user: I live in Oslo.");
        let first_transport = FakeTransport::with_generation_response(
            vec![Ok(response(json!({"observations": []})))],
            Err("generation unavailable".to_string()),
        );
        let (provider, ledger) = provider_with_ledger(first_transport.clone());
        let dispatch_root = provider.dispatch_root.clone().unwrap();
        assert!(provider.extract_sync(&request).is_err());
        let events = fs::read_to_string(&ledger).unwrap();
        assert_eq!(events.lines().count(), 1);
        assert_eq!(first_transport.posted.lock().unwrap().len(), 1);

        let resumed_transport = FakeTransport::with_generation_response(
            vec![],
            Ok(json!({"data": {
                "id": "generation-1",
                "model": "served/model",
                "provider_name": "served-provider",
                "tokens_prompt": 10,
                "tokens_completion": 5,
                "total_cost": 0.001
            }})),
        );
        let resumed = restarted_provider(resumed_transport.clone(), &ledger, &dispatch_root);
        assert!(resumed.extract_sync(&request).unwrap().is_empty());
        assert!(resumed_transport.posted.lock().unwrap().is_empty());
        let events = read_events(&ledger);
        assert_eq!(events.len(), 2);
        assert_eq!(events[1]["parse_status"], "decoded");
        fs::remove_dir_all(dispatch_root).unwrap();
    }

    #[test]
    fn terminal_before_cache_progress_reuses_the_exact_ledger_event_hashes() {
        let request = request("user: I live in Oslo.");
        let content = json!({
            "observations": [wire_observation(&request.evidence_slices[0].id)]
        });
        let first_transport = FakeTransport::new(vec![Ok(response(content))]);
        let (first, ledger) = provider_with_ledger(first_transport);
        let dispatch_root = first.dispatch_root.clone().unwrap();
        first.extract_sync(&request).unwrap();
        let (prefix_bytes, prefix_sha256) = ledger_prefix(&ledger);
        let temporary = tempfile::tempdir().unwrap();
        let cache = CampaignCache {
            root: temporary.path().join("cache"),
            hit_root: temporary.path().join("hits"),
            authorization_sha256: "a".repeat(64),
            campaign_sha256: "b".repeat(64),
            namespace: "terminal-resume".to_string(),
            source_ledger: ledger.clone(),
            source_ledger_prefix_bytes: prefix_bytes,
            source_ledger_prefix_sha256: prefix_sha256,
            served_model: "served/model".to_string(),
            served_provider: "served-provider".to_string(),
        };
        let resume_transport = FakeTransport::new(vec![]);
        let resumed = restarted_provider(resume_transport.clone(), &ledger, &dispatch_root)
            .with_campaign_cache(cache.clone())
            .unwrap();
        resumed.extract_sync(&request).unwrap();
        assert!(resume_transport.posted.lock().unwrap().is_empty());

        let cache_transport = FakeTransport::new(vec![]);
        let cached = restarted_provider(cache_transport.clone(), &ledger, &dispatch_root)
            .with_campaign_cache(cache)
            .unwrap();
        cached.extract_sync(&request).unwrap();
        assert!(cache_transport.posted.lock().unwrap().is_empty());
        fs::remove_file(ledger).unwrap();
        fs::remove_dir_all(dispatch_root).unwrap();
    }

    #[test]
    fn started_without_captured_response_is_non_dispatchable() {
        let request = request("user: crash after start");
        let transport = FakeTransport::new(vec![Ok(response(json!({"observations": []})))]);
        let (provider, ledger) = provider_with_ledger(transport.clone());
        let dispatch_root = provider.dispatch_root.clone().unwrap();
        let plan = provider.plan(&request).unwrap();
        let started = AttemptEvent::started(
            "crash-after-start",
            &request,
            &plan,
            DEFAULT_MODEL,
            MAX_ATTEMPTS,
        );
        append_json_line(&ledger, &started, Some(u64::MAX)).unwrap();

        let error = provider.extract_sync(&request).unwrap_err().to_string();
        assert!(error.contains("ambiguous and non-dispatchable"));
        assert!(transport.posted.lock().unwrap().is_empty());
        assert_eq!(fs::read_to_string(&ledger).unwrap().lines().count(), 1);
        fs::remove_file(ledger).unwrap();
        if dispatch_root.exists() {
            fs::remove_dir_all(dispatch_root).unwrap();
        }
    }

    #[test]
    fn campaign_cache_hit_is_source_neutral_zero_cost_and_authorization_bound() {
        let temporary = tempfile::tempdir().unwrap();
        let cache_root = temporary.path().join("cache");
        let hit_root = temporary.path().join("hits");
        let authorization = "a".repeat(64);
        let campaign = "b".repeat(64);
        let request = request("user: I live in Oslo.");
        let content = json!({
            "observations": [wire_observation(&request.evidence_slices[0].id)]
        });
        let paid_transport = FakeTransport::new(vec![Ok(response(content))]);
        let (paid, paid_ledger) = provider_with_ledger(paid_transport.clone());
        let (empty_prefix_bytes, empty_prefix_sha256) = ledger_prefix(&paid_ledger);
        let paid = paid
            .with_campaign_cache(CampaignCache {
                root: cache_root.clone(),
                hit_root: hit_root.clone(),
                authorization_sha256: authorization.clone(),
                campaign_sha256: campaign.clone(),
                namespace: "longmemeval-v2-construction-v1".to_string(),
                source_ledger: paid_ledger.clone(),
                source_ledger_prefix_bytes: empty_prefix_bytes,
                source_ledger_prefix_sha256: empty_prefix_sha256,
                served_model: "served/model".to_string(),
                served_provider: "served-provider".to_string(),
            })
            .unwrap();
        let expected = paid.extract_sync(&request).unwrap();
        assert_eq!(paid_transport.posted.lock().unwrap().len(), 1);
        let (paid_prefix_bytes, paid_prefix_sha256) = ledger_prefix(&paid_ledger);
        assert_eq!(fs::read_to_string(&paid_ledger).unwrap().lines().count(), 2);

        let plan = paid.plan(&request).unwrap();
        let cache_path = cache_root.join(format!("{}.json", plan.extraction_key));
        let original_entry: Value =
            serde_json::from_slice(&fs::read(&cache_path).unwrap()).unwrap();

        let mut foreign_route = original_entry.clone();
        foreign_route["core"]["served_model"] = json!("foreign/model");
        foreign_route["core"]["served_provider"] = json!("ForeignProvider");
        foreign_route["cache_entry_sha256"] = json!(canonical_sha256(&foreign_route["core"]));
        fs::write(&cache_path, serde_json::to_vec(&foreign_route).unwrap()).unwrap();
        let foreign_transport = FakeTransport::new(vec![]);
        let (foreign, foreign_ledger) = provider_with_ledger(foreign_transport.clone());
        let foreign = foreign
            .with_campaign_cache(CampaignCache {
                root: cache_root.clone(),
                hit_root: temporary.path().join("foreign-hits"),
                authorization_sha256: authorization.clone(),
                campaign_sha256: campaign.clone(),
                namespace: "longmemeval-v2-construction-v1".to_string(),
                source_ledger: paid_ledger.clone(),
                source_ledger_prefix_bytes: paid_prefix_bytes,
                source_ledger_prefix_sha256: paid_prefix_sha256.clone(),
                served_model: "served/model".to_string(),
                served_provider: "served-provider".to_string(),
            })
            .unwrap();
        assert!(foreign.extract_sync(&request).is_err());
        assert!(foreign_transport.posted.lock().unwrap().is_empty());
        assert!(!foreign_ledger.exists());

        let mut invented_source = original_entry.clone();
        invented_source["core"]["source_result_event_sha256"] = json!("f".repeat(64));
        invented_source["cache_entry_sha256"] = json!(canonical_sha256(&invented_source["core"]));
        fs::write(&cache_path, serde_json::to_vec(&invented_source).unwrap()).unwrap();
        let invented_transport = FakeTransport::new(vec![]);
        let (invented, invented_ledger) = provider_with_ledger(invented_transport.clone());
        let invented = invented
            .with_campaign_cache(CampaignCache {
                root: cache_root.clone(),
                hit_root: temporary.path().join("invented-hits"),
                authorization_sha256: authorization.clone(),
                campaign_sha256: campaign.clone(),
                namespace: "longmemeval-v2-construction-v1".to_string(),
                source_ledger: paid_ledger.clone(),
                source_ledger_prefix_bytes: paid_prefix_bytes,
                source_ledger_prefix_sha256: paid_prefix_sha256.clone(),
                served_model: "served/model".to_string(),
                served_provider: "served-provider".to_string(),
            })
            .unwrap();
        assert!(invented.extract_sync(&request).is_err());
        assert!(invented_transport.posted.lock().unwrap().is_empty());
        assert!(!invented_ledger.exists());

        fs::write(&cache_path, serde_json::to_vec(&original_entry).unwrap()).unwrap();

        let hit_transport = FakeTransport::new(vec![]);
        let (hit, hit_ledger) = provider_with_ledger(hit_transport.clone());
        let hit = hit
            .with_campaign_cache(CampaignCache {
                root: cache_root.clone(),
                hit_root: hit_root.clone(),
                authorization_sha256: authorization,
                campaign_sha256: campaign,
                namespace: "longmemeval-v2-construction-v1".to_string(),
                source_ledger: paid_ledger.clone(),
                source_ledger_prefix_bytes: paid_prefix_bytes,
                source_ledger_prefix_sha256: paid_prefix_sha256.clone(),
                served_model: "served/model".to_string(),
                served_provider: "served-provider".to_string(),
            })
            .unwrap();
        OpenOptions::new()
            .append(true)
            .open(&paid_ledger)
            .unwrap()
            .write_all(
                b"{\"schema_version\":3,\"event\":\"started\",\"attempt_id\":\"later-suffix\"}\n",
            )
            .unwrap();
        assert_eq!(hit.extract_sync(&request).unwrap(), expected);
        assert!(hit_transport.posted.lock().unwrap().is_empty());
        assert!(
            !hit_ledger.exists(),
            "a cache hit must not create a paid attempt"
        );
        let plan = hit.plan(&request).unwrap();
        let receipt: Value = serde_json::from_slice(
            &fs::read(hit_root.join(format!("{}.json", plan.extraction_key))).unwrap(),
        )
        .unwrap();
        assert_eq!(receipt["core"]["reservation_status"], "cache_hit");
        assert_eq!(receipt["core"]["settled_nanos"], 0);
        assert!(
            receipt["core"]["source_started_event_sha256"]
                .as_str()
                .is_some_and(is_sha256)
        );
        assert!(
            receipt["core"]["source_result_event_sha256"]
                .as_str()
                .is_some_and(is_sha256)
        );

        let drift_transport = FakeTransport::new(vec![]);
        let (drift, drift_ledger) = provider_with_ledger(drift_transport.clone());
        let drift = drift
            .with_campaign_cache(CampaignCache {
                root: cache_root,
                hit_root: temporary.path().join("drift-hits"),
                authorization_sha256: "c".repeat(64),
                campaign_sha256: "b".repeat(64),
                namespace: "longmemeval-v2-construction-v1".to_string(),
                source_ledger: paid_ledger,
                source_ledger_prefix_bytes: paid_prefix_bytes,
                source_ledger_prefix_sha256: paid_prefix_sha256,
                served_model: "served/model".to_string(),
                served_provider: "served-provider".to_string(),
            })
            .unwrap();
        assert!(drift.extract_sync(&request).is_err());
        assert!(drift_transport.posted.lock().unwrap().is_empty());
        assert!(!drift_ledger.exists());

        let miss_transport = FakeTransport::new(vec![]);
        let (mut miss, miss_ledger) = provider_with_ledger(miss_transport.clone());
        let (miss_prefix_bytes, miss_prefix_sha256) = ledger_prefix(&miss_ledger);
        miss = miss
            .with_campaign_cache(CampaignCache {
                root: temporary.path().join("empty-cache"),
                hit_root: temporary.path().join("empty-hits"),
                authorization_sha256: "a".repeat(64),
                campaign_sha256: "b".repeat(64),
                namespace: "longmemeval-v2-construction-v1".to_string(),
                source_ledger: miss_ledger.clone(),
                source_ledger_prefix_bytes: miss_prefix_bytes,
                source_ledger_prefix_sha256: miss_prefix_sha256,
                served_model: "served/model".to_string(),
                served_provider: "served-provider".to_string(),
            })
            .unwrap();
        miss.cache_only = true;
        assert!(miss.extract_sync(&request).is_err());
        assert!(miss_transport.posted.lock().unwrap().is_empty());
        assert!(!miss_ledger.exists());
    }

    /// Emit a cryptographically valid paid-source cache fixture for the
    /// scratch-Postgres campaign smoke. The transport is in-process and fake:
    /// this test cannot read credentials or reach a provider. It is ignored so
    /// ordinary unit gates never create cross-test artifacts.
    #[test]
    #[ignore = "external scratch-Postgres orchestration fixture"]
    fn emit_cache_only_resource_fixture_for_scratch_campaign() {
        let root = PathBuf::from(
            std::env::var("MEMPHANT_TEST_CACHE_FIXTURE_ROOT")
                .expect("MEMPHANT_TEST_CACHE_FIXTURE_ROOT"),
        );
        let prompt_path = std::env::var("MEMPHANT_TEST_CACHE_FIXTURE_PROMPT")
            .expect("MEMPHANT_TEST_CACHE_FIXTURE_PROMPT");
        // Use the production loader so trailing-newline normalization is part
        // of the fixture identity. A raw read silently produces a cache miss.
        let prompt = load_structured_state_prompt(Path::new(&prompt_path)).unwrap();
        let tokenizer = load_structured_state_tokenizer(
            Path::new(
                &std::env::var("MEMPHANT_TEST_CACHE_FIXTURE_TOKENIZER")
                    .expect("MEMPHANT_TEST_CACHE_FIXTURE_TOKENIZER"),
            ),
            Path::new(
                &std::env::var("MEMPHANT_TEST_CACHE_FIXTURE_TOKENIZER_CONFIG")
                    .expect("MEMPHANT_TEST_CACHE_FIXTURE_TOKENIZER_CONFIG"),
            ),
        )
        .unwrap();
        fs::create_dir_all(&root).unwrap();
        let cache_root = root.join("observation-cache");
        let hit_root = root.join("cache-hits");
        fs::create_dir_all(&cache_root).unwrap();
        fs::create_dir_all(&hit_root).unwrap();
        let ledger = root.join("CONSTRUCTION-ATTEMPTS.jsonl");
        let body = std::env::var("MEMPHANT_TEST_CACHE_FIXTURE_BODY")
            .unwrap_or_else(|_| "I live in Oslo.".to_string());
        let evidence_quote = std::env::var("MEMPHANT_TEST_CACHE_FIXTURE_QUOTE")
            .unwrap_or_else(|_| "I live in Oslo.".to_string());
        let request = StructuredStateRequest {
            source_kind: StructuredSourceKind::Resource,
            source_body_sha256: sha256(body.as_bytes()),
            batch_index: 0,
            evidence_slices: memphant_core::service::structured_state_slices_for_resource(&body)
                .unwrap(),
        };
        let content = json!({
            "observations": [{
                "namespace": "profile",
                "item_key": "city",
                "fields": [{"key": "value", "value": "Oslo"}],
                "disposition": "state",
                "evidence_slice_id": request.evidence_slices[0].id,
                "evidence_quote": evidence_quote,
                "valid_from": null,
                "valid_to": null
            }]
        });
        let response_id = "fixture-generation-1";
        let initial = HttpResponse {
            status: 200,
            body: json!({
                "id": response_id,
                "model": LME_V2_QWEN_MODEL,
                "choices": [{"message": {"content": content.to_string()}}]
            }),
        };
        let transport = FakeTransport::with_generation_response(
            vec![Ok(initial)],
            Ok(json!({"data": {
                "id": response_id,
                "model": "qwen/qwen3.5-9b",
                "provider_name": "DeepInfra",
                "tokens_prompt": 10,
                "tokens_completion": 5,
                "total_cost": 0.00000175
            }})),
        );
        let authorization = std::env::var("MEMPHANT_TEST_CACHE_AUTHORIZATION_SHA256")
            .unwrap_or_else(|_| "a".repeat(64));
        let campaign =
            std::env::var("MEMPHANT_TEST_CACHE_CAMPAIGN_SHA256").unwrap_or_else(|_| "b".repeat(64));
        let namespace = std::env::var("MEMPHANT_TEST_CACHE_NAMESPACE")
            .unwrap_or_else(|_| "scratch-cache-fixture-v1".to_string());
        let empty_prefix_sha256 = sha256(b"");
        let mut provider = OpenRouterStructuredState::new(
            LME_V2_QWEN_MODEL.to_string(),
            prompt,
            100_000_000,
            150_000_000,
            transport.clone(),
            Some(ledger.clone()),
        )
        .with_tokenizer(tokenizer)
        .with_campaign_cache(CampaignCache {
            root: cache_root.clone(),
            hit_root,
            authorization_sha256: authorization.clone(),
            campaign_sha256: campaign.clone(),
            namespace: namespace.clone(),
            source_ledger: ledger.clone(),
            source_ledger_prefix_bytes: 0,
            source_ledger_prefix_sha256: empty_prefix_sha256,
            served_model: "qwen/qwen3.5-9b".to_string(),
            served_provider: "DeepInfra".to_string(),
        })
        .unwrap();
        provider.aggregate_reservation_nanos = Some(u64::MAX);
        let observations = provider.extract_sync(&request).unwrap();
        assert_eq!(transport.posted.lock().unwrap().len(), 1);
        let plan = provider.plan(&request).unwrap();
        let ledger_bytes = fs::read(&ledger).unwrap();
        let metadata = json!({
            "schema_version": 1,
            "transport": "in-process-fake-no-network",
            "provider_credentials_read": false,
            "source_body": body,
            "source_body_sha256": request.source_body_sha256,
            "extraction_key": plan.extraction_key,
            "request_sha256": plan.request_sha256,
            "per_attempt_reservation_nanos": plan.per_attempt_reservation_nanos,
            "maximum_attempts": plan.maximum_attempts,
            "source_kind": "resource",
            "batch_index": 0,
            "evidence_slices_sha256": canonical_sha256(&request.evidence_slices),
            "authorization_sha256": authorization,
            "campaign_sha256": campaign,
            "cache_namespace": namespace,
            "served_model": "qwen/qwen3.5-9b",
            "served_provider": "DeepInfra",
            "source_ledger_prefix_bytes": ledger_bytes.len(),
            "source_ledger_prefix_sha256": sha256(&ledger_bytes),
            "observation_count": observations.len()
        });
        fs::write(
            root.join("fixture.json"),
            serde_json::to_vec_pretty(&metadata).unwrap(),
        )
        .unwrap();
    }
}
