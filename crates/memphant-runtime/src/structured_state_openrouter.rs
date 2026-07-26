use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use memphant_core::{
    StructuredStateOp, StructuredStateOperation, StructuredStateProvider,
    StructuredStateProviderError, StructuredStateProviderIdentity, StructuredStateRequest,
};
use memphant_types::UnitId;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use ureq::Agent;

const URL: &str = "https://openrouter.ai/api/v1/chat/completions";
const GENERATION_URL: &str = "https://openrouter.ai/api/v1/generation";
const DEFAULT_MODEL: &str = "openai/gpt-5.6-luna-pro";
const FLASH_MODEL: &str = "google/gemini-3.5-flash";
const FLASH_PROVIDER: &str = "google-ai-studio";
const DEEPSEEK_MODEL: &str = "deepseek/deepseek-v4-flash";
const DEEPSEEK_PROVIDERS: [&str; 2] = ["deepinfra", "wandb"];
const MAX_ATTEMPTS: usize = 3;
const MAX_OUTPUT_TOKENS: u64 = 4096;
const MAX_REQUEST_BYTES: usize = 128 * 1024;
const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
// Accuracy-first reasoning can legitimately emit >15k hidden reasoning tokens.
// Keep a single attempt inside the 15-minute, three-attempt queue lease while
// avoiding retries caused only by the former two-minute client ceiling.
const GLOBAL_TIMEOUT: Duration = Duration::from_secs(240);
const RESPONSE_LIMIT: u64 = 4 * 1024 * 1024;
const PROMPT_PATH_ENV: &str = "MEMPHANT_STRUCTURED_STATE_PROMPT_PATH";
const LEDGER_ENV: &str = "MEMPHANT_STRUCTURED_STATE_ATTEMPT_LEDGER";

pub(crate) fn provider_from_env() -> Result<Option<Arc<dyn StructuredStateProvider>>, String> {
    if std::env::var("MEMPHANT_STRUCTURED_STATE").as_deref() != Ok("on") {
        return Ok(None);
    }
    let key = std::env::var("OPENROUTER_API_KEY")
        .ok()
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| "OPENROUTER_API_KEY is required".to_string())?;
    let model = std::env::var("MEMPHANT_STRUCTURED_STATE_MODEL")
        .ok()
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| DEFAULT_MODEL.to_string());
    let prompt_path = std::env::var_os(PROMPT_PATH_ENV)
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .ok_or_else(|| format!("{PROMPT_PATH_ENV} is required"))?;
    let prompt = load_prompt(&prompt_path)?;
    let ledger = std::env::var_os(LEDGER_ENV)
        .filter(|value| !value.is_empty())
        .map(PathBuf::from);
    let mut provider = OpenRouterStructuredState::new(
        model,
        prompt,
        Arc::new(UreqTransport::new(key)),
        Duration::from_millis(500),
        ledger,
    );
    if let Some(effort) = std::env::var("MEMPHANT_STRUCTURED_STATE_REASONING_EFFORT")
        .ok()
        .filter(|value| !value.trim().is_empty())
    {
        if !matches!(effort.as_str(), "minimal" | "low" | "medium" | "high") {
            return Err(
                "MEMPHANT_STRUCTURED_STATE_REASONING_EFFORT must be minimal, low, medium, or high"
                    .to_string(),
            );
        }
        provider = provider.with_reasoning_effort(effort);
    }
    Ok(Some(Arc::new(provider)))
}

fn load_prompt(path: &Path) -> Result<String, String> {
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
struct OpenRouterStructuredState {
    model: String,
    prompt: String,
    identity: StructuredStateProviderIdentity,
    transport: Arc<dyn Transport>,
    ledger: Option<PathBuf>,
    ledger_lock: Arc<Mutex<()>>,
    retry_base: Duration,
    reasoning_effort: Option<String>,
}

impl OpenRouterStructuredState {
    fn new(
        model: String,
        prompt: String,
        transport: Arc<dyn Transport>,
        retry_base: Duration,
        ledger: Option<PathBuf>,
    ) -> Self {
        let schema = json!({
            "with_active_state": response_schema(),
            "without_active_state": response_schema_for_request(true, &[]),
        });
        Self {
            identity: StructuredStateProviderIdentity {
                model: compiler_model_identity(&model, None),
                prompt_hash: sha256(prompt.as_bytes()),
                schema_hash: sha256(
                    serde_json::to_vec(&schema)
                        .expect("static structured-state schema serializes")
                        .as_slice(),
                ),
            },
            model,
            prompt,
            transport,
            ledger,
            ledger_lock: Arc::new(Mutex::new(())),
            retry_base,
            reasoning_effort: None,
        }
    }

    fn with_reasoning_effort(mut self, effort: String) -> Self {
        self.identity.model = compiler_model_identity(&self.model, Some(&effort));
        self.reasoning_effort = Some(effort);
        self
    }

    fn request(&self, request: &StructuredStateRequest) -> Value {
        let evidence_quotes = memphant_core::user_evidence_turns(&request.episode_body);
        let payload = json!({
            "active_state": request.active_items,
            "episode": request.episode_body,
        });
        let mut request = json!({
            "model": self.model,
            "messages": [
                {"role": "system", "content": &self.prompt},
                {"role": "user", "content": payload.to_string()}
            ],
            "seed": 0,
            "stream": false,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "memphant_structured_state",
                    "strict": true,
                    "schema": response_schema_for_request(
                        request.active_items.is_empty(),
                        &evidence_quotes,
                    )
                }
            },
            "provider": provider_preferences(&self.model)
        });
        if self.model == FLASH_MODEL {
            request["temperature"] = json!(0);
        }
        if let Some(effort) = &self.reasoning_effort {
            request["reasoning"] = json!({"effort": effort});
        }
        request
    }

    fn extract_sync(
        &self,
        request: &StructuredStateRequest,
    ) -> Result<Vec<StructuredStateOp>, StructuredStateProviderError> {
        let body = self.request(request);
        let request_bytes = serde_json::to_vec(&body).expect("structured-state request serializes");
        if request_bytes.len() > MAX_REQUEST_BYTES {
            return Err(StructuredStateProviderError::InvalidOutput(format!(
                "request exceeds {MAX_REQUEST_BYTES}-byte limit"
            )));
        }
        let request_sha256 = sha256(&request_bytes);
        let episode_id = request.episode_id.as_uuid().to_string();
        for attempt in 1..=MAX_ATTEMPTS {
            let attempt_id = uuid::Uuid::new_v4().to_string();
            let started = Instant::now();
            self.record_attempt(&AttemptEvent::started(
                &attempt_id,
                episode_id.clone(),
                &self.model,
                &request_sha256,
                attempt,
            ))?;
            let response = match self.transport.post(&body) {
                Ok(response) => response,
                Err(_) => {
                    self.record_attempt(&AttemptEvent::transport_error(
                        &attempt_id,
                        episode_id,
                        &self.model,
                        &request_sha256,
                        attempt,
                        started.elapsed(),
                    ))?;
                    return Err(StructuredStateProviderError::Unavailable(
                        "OpenRouter transport failed; completion was not resent".to_string(),
                    ));
                }
            };
            if !(200..300).contains(&response.status) {
                self.record_attempt(&AttemptEvent::http_error(
                    &attempt_id,
                    episode_id.clone(),
                    &self.model,
                    &request_sha256,
                    attempt,
                    &response,
                    started.elapsed(),
                ))?;
                if is_retryable_status(response.status) && attempt < MAX_ATTEMPTS {
                    std::thread::sleep(
                        response
                            .retry_after
                            .unwrap_or_else(|| self.retry_base.saturating_mul(1 << (attempt - 1))),
                    );
                    continue;
                }
                return Err(StructuredStateProviderError::Unavailable(format!(
                    "OpenRouter HTTP {}: {}",
                    response.status,
                    openrouter_error_message(&response.body)
                )));
            }
            let reconciled = match reconcile_generation(self.transport.as_ref(), &response) {
                Ok(reconciled) => reconciled,
                Err(error) => {
                    self.record_attempt(&AttemptEvent::reconciliation_error(
                        &attempt_id,
                        episode_id,
                        &self.model,
                        &request_sha256,
                        attempt,
                        &response,
                        started.elapsed(),
                    ))?;
                    return Err(StructuredStateProviderError::Unavailable(error));
                }
            };
            self.record_attempt(&AttemptEvent::response(
                &attempt_id,
                episode_id.clone(),
                &self.model,
                &request_sha256,
                attempt,
                &reconciled,
                started.elapsed(),
            ))?;
            let decoded = decode_response_with_state(
                reconciled.body,
                &self.model,
                &request.episode_body,
                &request.active_items,
            );
            self.record_attempt(&AttemptEvent::decode(
                &attempt_id,
                episode_id,
                &self.model,
                &request_sha256,
                attempt,
                decoded.as_ref().ok(),
                started.elapsed(),
            ))?;
            let decoded = decoded?;
            return if decoded.rejected.is_empty() {
                Ok(decoded.operations)
            } else {
                Err(invalid("structured response contained rejected operations"))
            };
        }
        unreachable!("bounded structured-state retry loop always returns")
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
        append_json_line(path, event).map_err(|error| {
            StructuredStateProviderError::Unavailable(format!(
                "structured-state attempt ledger write failed: {error}"
            ))
        })
    }
}

fn provider_preferences(model: &str) -> Value {
    if model == FLASH_MODEL {
        json!({
            "require_parameters": true,
            "only": [FLASH_PROVIDER],
            "allow_fallbacks": true
        })
    } else if model == DEEPSEEK_MODEL {
        json!({
            "require_parameters": true,
            "order": DEEPSEEK_PROVIDERS,
            "only": DEEPSEEK_PROVIDERS,
            "allow_fallbacks": true
        })
    } else {
        json!({"require_parameters": true})
    }
}

fn compiler_model_identity(model: &str, reasoning_effort: Option<&str>) -> String {
    let mut identity = model.to_string();
    if model == FLASH_MODEL {
        identity.push_str(";provider=google-ai-studio");
    } else if model == DEEPSEEK_MODEL {
        identity.push_str(";providers=deepinfra,wandb");
    }
    identity.push_str(";seed=0;max_tokens=4096;max_request_bytes=131072");
    if model == FLASH_MODEL {
        identity.push_str(";temperature=0");
    }
    if let Some(effort) = reasoning_effort {
        identity.push_str(";reasoning_effort=");
        identity.push_str(effort);
    }
    identity
}

impl StructuredStateProvider for OpenRouterStructuredState {
    fn identity(&self) -> &StructuredStateProviderIdentity {
        &self.identity
    }

    fn extract<'a>(
        &'a self,
        request: &'a StructuredStateRequest,
    ) -> std::pin::Pin<
        Box<
            dyn std::future::Future<
                    Output = Result<Vec<StructuredStateOp>, StructuredStateProviderError>,
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

struct HttpResponse {
    status: u16,
    body: Value,
    retry_after: Option<Duration>,
}

trait Transport: Send + Sync {
    fn post(&self, body: &Value) -> Result<HttpResponse, String>;
    fn generation(&self, response_id: &str) -> Result<Value, String>;
}

struct UreqTransport {
    agent: Agent,
    key: String,
}

impl UreqTransport {
    fn new(key: String) -> Self {
        let config = Agent::config_builder()
            .timeout_connect(Some(CONNECT_TIMEOUT))
            .timeout_global(Some(GLOBAL_TIMEOUT))
            .http_status_as_error(false)
            .build();
        Self {
            agent: config.into(),
            key,
        }
    }
}

impl Transport for UreqTransport {
    fn post(&self, body: &Value) -> Result<HttpResponse, String> {
        let mut request = self
            .agent
            .post(URL)
            .header("authorization", &format!("Bearer {}", self.key));
        for (name, value) in attribution_headers() {
            request = request.header(name, value);
        }
        let mut response = request
            .send_json(body)
            .map_err(|_| "OpenRouter transport error".to_string())?;
        let status = response.status().as_u16();
        let response_id = response
            .headers()
            .get("x-generation-id")
            .and_then(|value| value.to_str().ok())
            .map(str::to_owned);
        let retry_after = response
            .headers()
            .get("retry-after")
            .and_then(|value| value.to_str().ok())
            .and_then(|value| value.trim().parse::<u64>().ok())
            .map(Duration::from_secs);
        let mut body = response
            .body_mut()
            .with_config()
            .limit(RESPONSE_LIMIT)
            .read_json()
            .map_err(|_| "OpenRouter response decode failed".to_string())?;
        backfill_response_id(&mut body, response_id.as_deref());
        Ok(HttpResponse {
            status,
            body,
            retry_after,
        })
    }

    fn generation(&self, response_id: &str) -> Result<Value, String> {
        // OpenRouter may publish generation metadata shortly after returning
        // the paid completion. Retry only the free, idempotent metadata GET on
        // 404; the completion POST above remains strictly single-shot.
        for (index, delay_seconds) in [1_u64, 2, 4, 8, 16, 0].into_iter().enumerate() {
            let mut request = self
                .agent
                .get(GENERATION_URL)
                .query("id", response_id)
                .header("authorization", &format!("Bearer {}", self.key));
            for (name, value) in attribution_headers() {
                request = request.header(name, value);
            }
            let mut response = request
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
    let Some(response_id) = header.filter(|value| !value.is_empty()) else {
        return;
    };
    if let Some(body) = body.as_object_mut() {
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
    let model = data
        .get("model")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| "OpenRouter generation statistics omitted model".to_string())?;
    let provider = data
        .get("provider_name")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| "OpenRouter generation statistics omitted provider".to_string())?;
    let prompt_tokens = data
        .get("tokens_prompt")
        .and_then(Value::as_u64)
        .filter(|value| *value > 0)
        .ok_or_else(|| "OpenRouter generation statistics omitted prompt tokens".to_string())?;
    let completion_tokens = data
        .get("tokens_completion")
        .and_then(Value::as_u64)
        .filter(|value| *value > 0)
        .ok_or_else(|| "OpenRouter generation statistics omitted completion tokens".to_string())?;
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
        retry_after: response.retry_after,
    })
}

fn is_retryable_status(status: u16) -> bool {
    status == 429 || (500..600).contains(&status)
}

fn openrouter_error_message(body: &Value) -> &str {
    body.pointer("/error/metadata/raw")
        .and_then(Value::as_str)
        .or_else(|| body.pointer("/error/message").and_then(Value::as_str))
        .unwrap_or("unstructured provider error")
}

fn attribution_headers() -> [(&'static str, &'static str); 3] {
    [
        ("http-referer", "https://github.com/memphant"),
        ("x-title", "memphant-structured-state"),
        ("x-openrouter-metadata", "enabled"),
    ]
}

#[derive(Serialize)]
struct AttemptEvent {
    schema_version: u8,
    event: &'static str,
    attempt_id: String,
    episode_id: String,
    attempt: usize,
    max_attempts: usize,
    retry_index: usize,
    requested_model: String,
    request_sha256: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    http_status: Option<u16>,
    #[serde(skip_serializing_if = "Option::is_none")]
    response_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    served_model: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    provider: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    usage: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error_type: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    provider_code: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    retry_after_seconds: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    elapsed_seconds: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    parse_status: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    result_sha256: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    accepted_op_count: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    rejected_op_count: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    rejection_reasons: Option<BTreeMap<&'static str, usize>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    rejection_diagnostics: Option<Vec<Value>>,
}

impl AttemptEvent {
    fn started(
        attempt_id: &str,
        episode_id: String,
        model: &str,
        request_sha256: &str,
        attempt: usize,
    ) -> Self {
        Self::new(
            "started",
            attempt_id,
            episode_id,
            model,
            request_sha256,
            attempt,
        )
    }

    fn response(
        attempt_id: &str,
        episode_id: String,
        model: &str,
        request_sha256: &str,
        attempt: usize,
        response: &HttpResponse,
        elapsed: Duration,
    ) -> Self {
        let mut event = Self::new(
            "result",
            attempt_id,
            episode_id,
            model,
            request_sha256,
            attempt,
        );
        event.http_status = Some(response.status);
        event.response_id = response
            .body
            .get("id")
            .and_then(Value::as_str)
            .map(str::to_owned);
        event.served_model = response
            .body
            .get("model")
            .and_then(Value::as_str)
            .map(str::to_owned);
        event.provider = selected_provider(&response.body);
        event.usage = response.body.get("usage").cloned();
        event.elapsed_seconds = Some(elapsed.as_secs_f64());
        event.parse_status = Some("generation_stats_reconciled");
        event.result_sha256 = Some(sha256(
            serde_json::to_vec(&response.body)
                .expect("provider response serializes")
                .as_slice(),
        ));
        event
    }

    fn http_error(
        attempt_id: &str,
        episode_id: String,
        model: &str,
        request_sha256: &str,
        attempt: usize,
        response: &HttpResponse,
        elapsed: Duration,
    ) -> Self {
        let mut event = Self::new(
            "result",
            attempt_id,
            episode_id,
            model,
            request_sha256,
            attempt,
        );
        event.http_status = Some(response.status);
        event.provider = selected_provider(&response.body);
        event.error = Some("http_error".to_string());
        event.error_type = response
            .body
            .pointer("/error/metadata/error_type")
            .and_then(Value::as_str)
            .map(str::to_owned);
        event.provider_code = response
            .body
            .pointer("/error/metadata/provider_code")
            .and_then(Value::as_str)
            .map(str::to_owned);
        event.retry_after_seconds = response.retry_after.map(|delay| delay.as_secs());
        event.elapsed_seconds = Some(elapsed.as_secs_f64());
        event.parse_status = Some("http_error");
        event
    }

    fn transport_error(
        attempt_id: &str,
        episode_id: String,
        model: &str,
        request_sha256: &str,
        attempt: usize,
        elapsed: Duration,
    ) -> Self {
        let mut event = Self::new(
            "result",
            attempt_id,
            episode_id,
            model,
            request_sha256,
            attempt,
        );
        event.error = Some("transport_error".to_string());
        event.elapsed_seconds = Some(elapsed.as_secs_f64());
        event.parse_status = Some("transport_error");
        event
    }

    fn reconciliation_error(
        attempt_id: &str,
        episode_id: String,
        model: &str,
        request_sha256: &str,
        attempt: usize,
        response: &HttpResponse,
        elapsed: Duration,
    ) -> Self {
        let mut event = Self::response(
            attempt_id,
            episode_id,
            model,
            request_sha256,
            attempt,
            response,
            elapsed,
        );
        event.error = Some("generation_stats_lookup_failed".to_string());
        event.parse_status = Some("generation_stats_lookup_failed");
        event
    }

    fn decode(
        attempt_id: &str,
        episode_id: String,
        model: &str,
        request_sha256: &str,
        attempt: usize,
        decoded: Option<&DecodedResponse>,
        elapsed: Duration,
    ) -> Self {
        let mut event = Self::new(
            "decode",
            attempt_id,
            episode_id,
            model,
            request_sha256,
            attempt,
        );
        event.elapsed_seconds = Some(elapsed.as_secs_f64());
        match decoded {
            Some(decoded) => {
                event.accepted_op_count = Some(decoded.operations.len());
                event.rejected_op_count = Some(decoded.rejected.values().sum());
                event.rejection_reasons = Some(decoded.rejected.clone());
                event.rejection_diagnostics = Some(decoded.rejection_diagnostics.clone());
                event.parse_status = Some("decoded");
            }
            None => {
                event.accepted_op_count = Some(0);
                event.rejected_op_count = Some(0);
                event.rejection_reasons = Some(BTreeMap::new());
                event.error = Some("response_decode_error".to_string());
                event.parse_status = Some("response_decode_error");
            }
        }
        event
    }

    fn new(
        event: &'static str,
        attempt_id: &str,
        episode_id: String,
        model: &str,
        request_sha256: &str,
        attempt: usize,
    ) -> Self {
        Self {
            schema_version: 2,
            event,
            attempt_id: attempt_id.to_string(),
            episode_id,
            attempt,
            max_attempts: MAX_ATTEMPTS,
            retry_index: attempt - 1,
            requested_model: model.to_string(),
            request_sha256: request_sha256.to_string(),
            http_status: None,
            response_id: None,
            served_model: None,
            provider: None,
            usage: None,
            error: None,
            error_type: None,
            provider_code: None,
            retry_after_seconds: None,
            elapsed_seconds: None,
            parse_status: None,
            result_sha256: None,
            accepted_op_count: None,
            rejected_op_count: None,
            rejection_reasons: None,
            rejection_diagnostics: None,
        }
    }
}

fn selected_provider(body: &Value) -> Option<String> {
    body.get("provider")
        .and_then(Value::as_str)
        .map(str::to_owned)
        .or_else(|| {
            body.pointer("/openrouter_metadata/endpoints/available")
                .and_then(Value::as_array)?
                .iter()
                .find(|endpoint| endpoint.get("selected") == Some(&Value::Bool(true)))?
                .get("provider")?
                .as_str()
                .map(str::to_owned)
        })
}

fn append_json_line(path: &Path, event: &AttemptEvent) -> std::io::Result<()> {
    let mut line = serde_json::to_vec(event).expect("attempt event serializes");
    line.push(b'\n');
    let mut file = OpenOptions::new().create(true).append(true).open(path)?;
    file.write_all(&line)?;
    file.sync_data()
}

#[derive(Deserialize)]
struct ChatResponse {
    model: String,
    choices: Vec<Choice>,
    #[serde(default)]
    _usage: Option<Usage>,
}

#[derive(Deserialize)]
struct Usage {
    #[serde(default)]
    _prompt_tokens: Option<u64>,
    #[serde(default)]
    _completion_tokens: Option<u64>,
    #[serde(default)]
    _total_tokens: Option<u64>,
}

#[derive(Deserialize)]
struct Choice {
    message: Message,
}

#[derive(Deserialize)]
struct Message {
    content: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct WireResponse {
    state_operations: Vec<WireStateOperation>,
    #[serde(default)]
    preference_operations: Vec<WirePreferenceOperation>,
    quantity_events: Vec<WireQuantityEvent>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct WireStateOperation {
    operation: StructuredStateOperation,
    namespace: Option<String>,
    item_key: Option<String>,
    target_unit_ids: Vec<String>,
    #[serde(default)]
    preference_value: String,
    #[serde(default)]
    memory_role: String,
    #[serde(default)]
    epistemic_use: String,
    #[serde(default)]
    applicability_scope: String,
    fields: Vec<WireField>,
    evidence_quote: String,
    valid_from: Option<String>,
    valid_to: Option<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct WirePreferenceOperation {
    operation: StructuredStateOperation,
    namespace: Option<String>,
    item_key: Option<String>,
    target_unit_ids: Vec<String>,
    preference_value: String,
    memory_role: String,
    epistemic_use: String,
    applicability_scope: String,
    evidence_quote: String,
    valid_from: Option<String>,
    valid_to: Option<String>,
}

impl From<WirePreferenceOperation> for WireStateOperation {
    fn from(operation: WirePreferenceOperation) -> Self {
        Self {
            operation: operation.operation,
            namespace: operation.namespace,
            item_key: operation.item_key,
            target_unit_ids: operation.target_unit_ids,
            preference_value: operation.preference_value,
            memory_role: operation.memory_role,
            epistemic_use: operation.epistemic_use,
            applicability_scope: operation.applicability_scope,
            fields: Vec::new(),
            evidence_quote: operation.evidence_quote,
            valid_from: operation.valid_from,
            valid_to: operation.valid_to,
        }
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct WireQuantityEvent {
    value: String,
    measure: String,
    unit: String,
    occurred_at: String,
    dimensions: Vec<WireDimension>,
    evidence_quote: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct WireField {
    key: String,
    value_type: WireValueType,
    value: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
enum WireValueType {
    String,
    Decimal,
    Boolean,
    Timestamp,
    Null,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct WireDimension {
    key: String,
    value: String,
}

#[derive(Debug)]
struct DecodedResponse {
    operations: Vec<StructuredStateOp>,
    rejected: BTreeMap<&'static str, usize>,
    rejection_diagnostics: Vec<Value>,
}

#[cfg(test)]
fn decode_response(
    body: Value,
    requested_model: &str,
    episode_body: &str,
) -> Result<DecodedResponse, StructuredStateProviderError> {
    decode_response_with_state(body, requested_model, episode_body, &[])
}

fn decode_response_with_state(
    body: Value,
    requested_model: &str,
    episode_body: &str,
    active_items: &[memphant_core::ActiveStructuredState],
) -> Result<DecodedResponse, StructuredStateProviderError> {
    let response: ChatResponse = serde_json::from_value(body).map_err(invalid)?;
    if response.model != requested_model
        && !response
            .model
            .strip_prefix(requested_model)
            .is_some_and(|suffix| suffix.starts_with('-'))
    {
        return Err(invalid(format!(
            "served model {:?} did not match requested model {requested_model:?}",
            response.model
        )));
    }
    let content = response
        .choices
        .first()
        .map(|choice| choice.message.content.as_str())
        .ok_or_else(|| invalid("missing choice"))?;
    let wire: WireResponse = serde_json::from_str(content).map_err(invalid)?;
    let mut operations = Vec::new();
    let mut rejected = BTreeMap::new();
    let mut rejection_diagnostics = Vec::new();
    let mut state_identities = BTreeSet::new();
    let grounded_preference_scopes = memphant_core::user_evidence_turns(episode_body)
        .into_iter()
        .filter_map(|quote| {
            grounded_preference_value(&quote).ok().flatten().map(|_| {
                grounded_applicability_scope(&quote)
                    .unwrap_or_default()
                    .trim()
                    .to_owned()
            })
        })
        .collect::<BTreeSet<_>>();
    for (source_channel, operation) in wire
        .state_operations
        .into_iter()
        .map(|operation| ("state_operations", operation))
        .chain(
            wire.preference_operations
                .into_iter()
                .map(|operation| ("preference_operations", operation.into())),
        )
    {
        let discard_if_superseded_by_grounded_preference =
            grounded_preference_value(&operation.evidence_quote)
                .ok()
                .flatten()
                .is_none()
                && grounded_preference_scopes.contains(operation.applicability_scope.trim());
        let discard_rejected_experience_inference = !grounded_preference_scopes.is_empty()
            && evidence_is_rejected_experience(&operation.evidence_quote)
            && wire_operation_looks_like_preference(&operation);
        let diagnostic = json!({
            "source_channel": source_channel,
            "operation": match operation.operation {
                StructuredStateOperation::Create => "create",
                StructuredStateOperation::Replace => "replace",
                StructuredStateOperation::Delete => "delete",
                StructuredStateOperation::Append => "append",
            },
            "has_namespace": operation.namespace.as_deref().is_some_and(|value| !value.trim().is_empty()),
            "has_item_key": operation.item_key.as_deref().is_some_and(|value| !value.trim().is_empty()),
            "target_count": operation.target_unit_ids.len(),
            "field_count": operation.fields.len(),
            "active_state_count": active_items.len(),
        });
        if discard_rejected_experience_inference {
            continue;
        }
        match transform_state(operation, episode_body, active_items) {
            Ok(transformed) => {
                let identities = transformed
                    .iter()
                    .map(|operation| match operation.operation {
                        StructuredStateOperation::Create => {
                            format!("create:{}/{}", operation.namespace, operation.item_key)
                        }
                        StructuredStateOperation::Replace | StructuredStateOperation::Delete => {
                            format!("target:{}", operation.target_unit_ids[0].as_uuid())
                        }
                        StructuredStateOperation::Append => {
                            unreachable!("state append is rejected")
                        }
                    })
                    .collect::<Vec<_>>();
                let has_reserved_personalization_role = |operation: &StructuredStateOp| {
                    operation.fields.get("memory_role") == Some(&json!("personalization"))
                        && operation.fields.get("epistemic_use")
                            == Some(&json!("not_factual_evidence"))
                };
                if identities
                    .iter()
                    .all(|identity| !state_identities.contains(identity))
                {
                    state_identities.extend(identities);
                    operations.extend(transformed);
                } else if transformed
                    .iter()
                    .all(|operation| operations.contains(operation))
                {
                    // Exact duplicate model output is idempotent. Conflicting
                    // operations for one identity still fail closed below.
                } else if transformed.len() == 1
                    && transformed[0].operation == StructuredStateOperation::Create
                    && let Some(existing_index) = operations.iter().position(|existing| {
                        existing.operation == StructuredStateOperation::Create
                            && existing.namespace == transformed[0].namespace
                            && existing.item_key == transformed[0].item_key
                    })
                    && (has_reserved_personalization_role(&operations[existing_index])
                        || has_reserved_personalization_role(&transformed[0]))
                {
                    match (
                        has_reserved_personalization_role(&operations[existing_index]),
                        has_reserved_personalization_role(&transformed[0]),
                    ) {
                        (true, true) => {
                            let existing_position = episode_body
                                .find(&operations[existing_index].evidence_quote)
                                .expect("grounded existing preference quote");
                            let new_position = episode_body
                                .find(&transformed[0].evidence_quote)
                                .expect("grounded new preference quote");
                            if new_position > existing_position {
                                operations[existing_index] = transformed[0].clone();
                            }
                        }
                        (false, true) => operations[existing_index] = transformed[0].clone(),
                        (true, false) => {}
                        (false, false) => unreachable!("guarded reserved-role arbitration"),
                    }
                } else {
                    *rejected.entry("duplicate_state_identity").or_default() += 1;
                    let incoming = transformed.first();
                    let existing = incoming.and_then(|incoming| {
                        operations.iter().find(|existing| {
                            existing.operation == incoming.operation
                                && existing.namespace == incoming.namespace
                                && existing.item_key == incoming.item_key
                                && existing.target_unit_ids == incoming.target_unit_ids
                        })
                    });
                    let incoming_reserved_role =
                        incoming.is_some_and(has_reserved_personalization_role);
                    let existing_reserved_role =
                        existing.is_some_and(has_reserved_personalization_role);
                    let quote_order = match (existing, incoming) {
                        (Some(existing), Some(incoming)) => match (
                            episode_body.find(&existing.evidence_quote),
                            episode_body.find(&incoming.evidence_quote),
                        ) {
                            (Some(existing), Some(incoming)) if incoming > existing => {
                                "incoming_later"
                            }
                            (Some(existing), Some(incoming)) if incoming < existing => {
                                "incoming_earlier"
                            }
                            (Some(_), Some(_)) => "same",
                            _ => "unavailable",
                        },
                        _ => "unavailable",
                    };
                    let mut failed_predicates = Vec::new();
                    if transformed.len() != 1 {
                        failed_predicates.push("not_single_transform");
                    }
                    if !incoming.is_some_and(|operation| {
                        operation.operation == StructuredStateOperation::Create
                    }) {
                        failed_predicates.push("incoming_not_create");
                    }
                    if !incoming_reserved_role {
                        failed_predicates.push("incoming_not_personalization");
                    }
                    if !existing_reserved_role {
                        failed_predicates.push("existing_not_personalization");
                    }
                    if quote_order != "incoming_later" {
                        failed_predicates.push("incoming_not_later");
                    }
                    let mut duplicate_diagnostic = diagnostic;
                    duplicate_diagnostic["transformed_count"] = json!(transformed.len());
                    duplicate_diagnostic["identity_collision"] = json!(true);
                    duplicate_diagnostic["all_transforms_equal"] = json!(false);
                    duplicate_diagnostic["namespace_equal"] = json!(matches!(
                        (existing, incoming),
                        (Some(existing), Some(incoming))
                            if existing.namespace == incoming.namespace
                    ));
                    duplicate_diagnostic["item_key_equal"] = json!(matches!(
                        (existing, incoming),
                        (Some(existing), Some(incoming))
                            if existing.item_key == incoming.item_key
                    ));
                    duplicate_diagnostic["existing_reserved_role"] = json!(existing_reserved_role);
                    duplicate_diagnostic["incoming_reserved_role"] = json!(incoming_reserved_role);
                    duplicate_diagnostic["quote_order"] = json!(quote_order);
                    duplicate_diagnostic["failed_predicates"] = json!(failed_predicates);
                    rejection_diagnostics.push(duplicate_diagnostic);
                }
            }
            Err(error) => {
                let error_message = error.to_string();
                if discard_if_superseded_by_grounded_preference
                    && (error_message.contains(
                        "preference value must be preserved exactly from the evidence quote",
                    ) || error_message.contains(
                        "preference state requires value and reserved epistemic role fields",
                    ))
                {
                    // Strict provider schemas cannot express the dependency
                    // between evidence_quote and preference_value. A model can
                    // therefore emit an inferred preference candidate in either
                    // channel with a value from a different quote or without the
                    // reserved role fields. The exact explicit preference is
                    // deterministically rebuilt below and is authoritative for
                    // its scope; never let the inferred candidate preempt that
                    // reconciliation or enter active state.
                    continue;
                }
                *rejected.entry(rejection_class(&error)).or_default() += 1;
                let mut diagnostic = diagnostic;
                diagnostic["reason"] = json!(error.to_string());
                rejection_diagnostics.push(diagnostic);
            }
        }
    }
    if rejected.is_empty()
        && reconcile_explicit_preferences(&mut operations, episode_body, active_items).is_err()
    {
        *rejected.entry("evidence_grounding").or_default() += 1;
    }
    if rejected.is_empty()
        && reconcile_repeatable_success_preferences(&mut operations, episode_body, active_items)
            .is_err()
    {
        *rejected.entry("evidence_grounding").or_default() += 1;
    }
    for event in wire.quantity_events {
        let is_admitted_personalization_evidence = operations.iter().any(|operation| {
            operation.evidence_quote == event.evidence_quote
                && operation.fields.get("memory_role") == Some(&json!("personalization"))
                && operation.fields.get("epistemic_use") == Some(&json!("not_factual_evidence"))
        });
        match transform_quantity(event, episode_body) {
            Ok(operation) => operations.push(operation),
            Err(error)
                if is_admitted_personalization_evidence
                    && error.to_string().contains(
                        "quantity value must be preserved exactly from grounded evidence",
                    ) => {}
            Err(error) => *rejected.entry(rejection_class(&error)).or_default() += 1,
        }
    }
    if evidence_requires_preference(episode_body)
        && !operations.iter().any(|operation| {
            operation.fields.get("memory_role") == Some(&json!("personalization"))
                && operation.fields.get("epistemic_use") == Some(&json!("not_factual_evidence"))
        })
    {
        *rejected.entry("missing_preference_operation").or_default() += 1;
    }
    Ok(DecodedResponse {
        operations,
        rejected,
        rejection_diagnostics,
    })
}

fn reconcile_explicit_preferences(
    operations: &mut Vec<StructuredStateOp>,
    episode_body: &str,
    active_items: &[memphant_core::ActiveStructuredState],
) -> Result<(), ()> {
    let mut latest_by_scope = BTreeMap::new();
    let mut unscoped = Vec::new();
    for (position, quote) in memphant_core::user_evidence_turns(episode_body)
        .into_iter()
        .enumerate()
    {
        let Some(value) = explicit_preference_value(&quote)? else {
            continue;
        };
        let value = value.to_string();
        let source_span =
            memphant_core::ground_user_evidence_quote(episode_body, &quote).ok_or(())?;
        let Some(scope) = explicit_applicability_scope(&quote) else {
            if quote.to_ascii_lowercase().starts_with("i prefer ") {
                unscoped.push((position, quote, value, source_span));
            }
            continue;
        };
        latest_by_scope.insert(scope.to_string(), (position, quote, value, source_span));
    }
    if latest_by_scope.is_empty() && unscoped.len() != 1 {
        return Ok(());
    }

    let explicit_scopes = latest_by_scope.keys().cloned().collect::<BTreeSet<_>>();
    operations.retain(|operation| {
        operation.fields.get("memory_role") != Some(&json!("personalization"))
            || operation
                .fields
                .get("applicability_scope")
                .and_then(Value::as_str)
                .is_none_or(|scope| !explicit_scopes.contains(scope))
    });

    let mut candidates = latest_by_scope.into_iter().collect::<Vec<_>>();
    candidates.sort_by_key(|(_, (position, _, _, _))| *position);
    for (scope, (_, quote, value, source_span)) in candidates {
        let fields = BTreeMap::from([
            ("value".to_string(), json!(value)),
            ("memory_role".to_string(), json!("personalization")),
            ("epistemic_use".to_string(), json!("not_factual_evidence")),
            ("applicability_scope".to_string(), json!(scope)),
        ]);
        let mut active = active_items
            .iter()
            .filter(|item| {
                item.fields.get("memory_role") == Some(&json!("personalization"))
                    && item
                        .fields
                        .get("applicability_scope")
                        .and_then(Value::as_str)
                        == Some(scope.as_str())
            })
            .collect::<Vec<_>>();
        active.sort_by_key(|item| item.unit_id.as_uuid());
        if let Some((current, superseded)) = active.split_first() {
            operations.push(StructuredStateOp {
                operation: StructuredStateOperation::Replace,
                namespace: current.namespace.clone(),
                item_key: current.item_key.clone(),
                target_unit_ids: vec![current.unit_id],
                fields,
                evidence_quote: quote.clone(),
                source_span: source_span.clone(),
                valid_from: None,
                valid_to: None,
            });
            operations.extend(superseded.iter().map(|item| StructuredStateOp {
                operation: StructuredStateOperation::Delete,
                namespace: item.namespace.clone(),
                item_key: item.item_key.clone(),
                target_unit_ids: vec![item.unit_id],
                fields: BTreeMap::new(),
                evidence_quote: quote.clone(),
                source_span: source_span.clone(),
                valid_from: None,
                valid_to: None,
            }));
        } else {
            operations.push(StructuredStateOp {
                operation: StructuredStateOperation::Create,
                namespace: "user_preferences".to_string(),
                item_key: format!("scope_{}", &sha256(scope.as_bytes())[..16]),
                target_unit_ids: Vec::new(),
                fields,
                evidence_quote: quote,
                source_span,
                valid_from: None,
                valid_to: None,
            });
        }
    }

    if let [(.., quote, value, source_span)] = unscoped.as_slice() {
        // The quote grounds a value but no domain identity, so discard model-inferred
        // identities before rebuilding the single unambiguous candidate.
        operations.retain(|operation| operation.evidence_quote != *quote);
        let fields = BTreeMap::from([
            ("value".to_string(), json!(value)),
            ("memory_role".to_string(), json!("personalization")),
            ("epistemic_use".to_string(), json!("not_factual_evidence")),
            ("applicability_scope".to_string(), json!("")),
        ]);
        if !active_items.iter().any(|item| item.fields == fields) {
            operations.push(StructuredStateOp {
                operation: StructuredStateOperation::Create,
                namespace: "user_preferences".to_string(),
                item_key: format!("unscoped_{}", &sha256(value.as_bytes())[..16]),
                target_unit_ids: Vec::new(),
                fields,
                evidence_quote: quote.clone(),
                source_span: source_span.clone(),
                valid_from: None,
                valid_to: None,
            });
        }
    }
    Ok(())
}

fn reconcile_repeatable_success_preferences(
    operations: &mut Vec<StructuredStateOp>,
    episode_body: &str,
    active_items: &[memphant_core::ActiveStructuredState],
) -> Result<(), ()> {
    let mut candidates = Vec::new();
    let mut identities = BTreeSet::new();
    for quote in memphant_core::user_evidence_turns(episode_body) {
        let Some(value) = repeatable_success_preference_value(&quote)? else {
            continue;
        };
        let Some(scope) = repeatable_success_applicability_scope(&quote) else {
            return Err(());
        };
        let source_span =
            memphant_core::ground_user_evidence_quote(episode_body, &quote).ok_or(())?;
        let identity = (scope.to_string(), value.to_string());
        if identities.insert(identity.clone()) {
            candidates.push((identity.0, identity.1, quote, source_span));
        }
    }
    if candidates.is_empty() {
        return Ok(());
    }

    operations.retain(|operation| {
        operation.fields.get("memory_role") != Some(&json!("personalization"))
            || repeatable_success_preference_value(&operation.evidence_quote)
                .ok()
                .flatten()
                .is_none()
    });

    for (scope, value, quote, source_span) in candidates {
        let fields = BTreeMap::from([
            ("value".to_string(), json!(value)),
            ("memory_role".to_string(), json!("personalization")),
            ("epistemic_use".to_string(), json!("not_factual_evidence")),
            ("applicability_scope".to_string(), json!(scope)),
        ]);
        if active_items.iter().any(|item| item.fields == fields) {
            continue;
        }
        let identity_material = format!("{scope}\0{value}");
        operations.push(StructuredStateOp {
            operation: StructuredStateOperation::Create,
            namespace: "user_preferences".to_string(),
            item_key: format!("experience_{}", &sha256(identity_material.as_bytes())[..16]),
            target_unit_ids: Vec::new(),
            fields,
            evidence_quote: quote,
            source_span,
            valid_from: None,
            valid_to: None,
        });
    }
    Ok(())
}

fn rejection_class(error: &StructuredStateProviderError) -> &'static str {
    let message = error.to_string();
    if message.contains("evidence quote") {
        "evidence_grounding"
    } else if message.contains("occurred_at") {
        "quantity_occurred_at"
    } else if message.contains("dimension") {
        "dimension_shape"
    } else if message.contains("quantity") {
        "quantity_shape"
    } else if message.contains("field") || message.contains("boolean") || message.contains("null") {
        "field_shape"
    } else {
        "operation_shape"
    }
}

fn transform_state(
    operation: WireStateOperation,
    episode_body: &str,
    active_items: &[memphant_core::ActiveStructuredState],
) -> Result<Vec<StructuredStateOp>, StructuredStateProviderError> {
    let mut target_ids = BTreeSet::new();
    let target_unit_ids = operation
        .target_unit_ids
        .iter()
        .map(|raw| {
            let id = uuid::Uuid::parse_str(raw).map_err(|_| invalid("invalid target unit id"))?;
            if !target_ids.insert(id) {
                return Err(invalid("duplicate target unit id"));
            }
            Ok(UnitId::from_u128(id.as_u128()))
        })
        .collect::<Result<Vec<_>, StructuredStateProviderError>>()?;
    let has_identity = operation
        .namespace
        .as_deref()
        .is_some_and(|value| !value.trim().is_empty())
        && operation
            .item_key
            .as_deref()
            .is_some_and(|value| !value.trim().is_empty());
    let has_dedicated_preference_payload = !operation.preference_value.trim().is_empty()
        || !operation.memory_role.trim().is_empty()
        || !operation.epistemic_use.trim().is_empty()
        || !operation.applicability_scope.trim().is_empty();
    let shape_is_valid = match operation.operation {
        StructuredStateOperation::Create => {
            has_identity
                && target_unit_ids.is_empty()
                && (!operation.fields.is_empty() || has_dedicated_preference_payload)
        }
        StructuredStateOperation::Replace => {
            target_unit_ids.len() == 1
                && (!operation.fields.is_empty() || has_dedicated_preference_payload)
        }
        StructuredStateOperation::Delete => {
            !target_unit_ids.is_empty()
                && operation.fields.is_empty()
                && !has_dedicated_preference_payload
        }
        StructuredStateOperation::Append => false,
    };
    if !shape_is_valid {
        return Err(invalid("state operation shape is invalid"));
    }
    let mut fields = BTreeMap::new();
    for field in operation.fields {
        if matches!(
            field.key.as_str(),
            "operation" | "namespace" | "item_key" | "target_unit_ids" | "valid_from" | "valid_to"
        ) {
            return Err(invalid("operation envelope key is forbidden inside fields"));
        }
        let value = match field.value_type {
            WireValueType::String | WireValueType::Decimal | WireValueType::Timestamp => {
                Value::String(field.value)
            }
            WireValueType::Boolean => Value::Bool(
                field
                    .value
                    .parse()
                    .map_err(|_| invalid("invalid boolean field"))?,
            ),
            WireValueType::Null if field.value.is_empty() => Value::Null,
            WireValueType::Null => return Err(invalid("null field value must be empty")),
        };
        if field.key.trim().is_empty() || fields.insert(field.key, value).is_some() {
            return Err(invalid("blank or duplicate field key"));
        }
    }
    for (key, raw) in [
        ("value", operation.preference_value.as_str()),
        ("memory_role", operation.memory_role.as_str()),
        ("epistemic_use", operation.epistemic_use.as_str()),
        (
            "applicability_scope",
            operation.applicability_scope.as_str(),
        ),
    ] {
        if !raw.trim().is_empty()
            && fields
                .insert(key.to_string(), Value::String(raw.to_string()))
                .is_some()
        {
            return Err(invalid("dedicated preference field was duplicated"));
        }
    }
    let source_span =
        memphant_core::ground_user_evidence_quote(episode_body, &operation.evidence_quote)
            .ok_or_else(|| invalid("evidence quote must uniquely match a user turn"))?;
    let identities = match operation.operation {
        StructuredStateOperation::Create => vec![(
            operation.namespace.expect("validated create namespace"),
            operation.item_key.expect("validated create item key"),
            Vec::new(),
        )],
        StructuredStateOperation::Replace | StructuredStateOperation::Delete => target_unit_ids
            .into_iter()
            .map(|target_id| {
                let target = active_items
                    .iter()
                    .find(|item| item.unit_id == target_id)
                    .ok_or_else(|| invalid("target unit id is not active in this scope"))?;
                Ok((
                    target.namespace.clone(),
                    target.item_key.clone(),
                    vec![target_id],
                ))
            })
            .collect::<Result<Vec<_>, StructuredStateProviderError>>()?,
        StructuredStateOperation::Append => unreachable!("validated state operation"),
    };
    let is_preference_identity = |namespace: &str, item_key: &str| {
        namespace.contains("preference") || item_key.contains("preference")
    };
    let grounded_preference_value = match operation.operation {
        StructuredStateOperation::Create | StructuredStateOperation::Replace => {
            grounded_preference_value(&operation.evidence_quote).map_err(|_| {
                invalid("preference evidence quote contains multiple candidate values")
            })?
        }
        StructuredStateOperation::Delete | StructuredStateOperation::Append => None,
    };
    let is_preference_state = has_dedicated_preference_payload
        || grounded_preference_value.is_some()
        || identities
            .iter()
            .any(|(namespace, item_key, _)| is_preference_identity(namespace, item_key));
    if let Some(explicit) = grounded_preference_value {
        fields.insert("value".to_string(), json!(explicit));
        fields.insert("memory_role".to_string(), json!("personalization"));
        fields.insert("epistemic_use".to_string(), json!("not_factual_evidence"));
    }
    if operation.operation != StructuredStateOperation::Delete
        && is_preference_state
        && (!fields.contains_key("value")
            || fields.get("memory_role") != Some(&json!("personalization"))
            || fields.get("epistemic_use") != Some(&json!("not_factual_evidence")))
    {
        return Err(invalid(
            "preference state requires value and reserved epistemic role fields",
        ));
    }
    if is_preference_state {
        if let Some(explicit) = grounded_preference_value {
            fields.insert("value".to_string(), json!(explicit));
        } else if let Some(Value::String(preference_value)) = fields.get("value")
            && !operation.evidence_quote.contains(preference_value)
        {
            return Err(invalid(
                "preference value must be preserved exactly from the evidence quote",
            ));
        }
    }
    if operation.operation != StructuredStateOperation::Delete {
        let grounded_scopes =
            grounded_applicability_scopes(std::slice::from_ref(&operation.evidence_quote));
        if let Some(required_scope) = grounded_scopes.iter().find(|scope| !scope.is_empty()) {
            fields.insert("applicability_scope".to_string(), json!(required_scope));
        } else if let Some(Value::String(scope)) = fields.get("applicability_scope")
            && !scope.is_empty()
        {
            return Err(invalid(
                "preference applicability scope must be copied from the evidence quote",
            ));
        }
    }
    Ok(identities
        .into_iter()
        .map(|(namespace, item_key, target_unit_ids)| StructuredStateOp {
            operation: operation.operation,
            namespace,
            item_key,
            target_unit_ids,
            fields: fields.clone(),
            evidence_quote: operation.evidence_quote.clone(),
            source_span: source_span.clone(),
            valid_from: operation.valid_from.clone(),
            valid_to: operation.valid_to.clone(),
        })
        .collect())
}

fn transform_quantity(
    event: WireQuantityEvent,
    episode_body: &str,
) -> Result<StructuredStateOp, StructuredStateProviderError> {
    let mut dimensions = serde_json::Map::new();
    for dimension in event.dimensions {
        if dimensions
            .insert(dimension.key, Value::String(dimension.value))
            .is_some()
        {
            return Err(invalid("blank or duplicate dimension key"));
        }
    }
    if !quantity_value_is_grounded(&event.value, &event.evidence_quote) {
        return Err(invalid(
            "quantity value must be preserved exactly from grounded evidence",
        ));
    }
    let occurred_at = if event.occurred_at.is_empty() {
        content_date(episode_body)
            .ok_or_else(|| invalid("quantity occurred_at requires a trusted [date] header"))?
    } else {
        event.occurred_at
    };
    occurred_at
        .parse::<jiff::Timestamp>()
        .map_err(|_| invalid("quantity occurred_at must be RFC3339"))?;
    let fields = BTreeMap::from([
        ("dimensions".to_string(), Value::Object(dimensions)),
        ("measure".to_string(), Value::String(event.measure.clone())),
        ("occurred_at".to_string(), Value::String(occurred_at)),
        (
            "type".to_string(),
            Value::String("quantity_event.v1".to_string()),
        ),
        ("unit".to_string(), Value::String(event.unit)),
        ("value".to_string(), Value::String(event.value)),
    ]);
    if memphant_core::quantity_event_from_fields(&fields).is_none() {
        return Err(invalid(
            "quantity event fields violate the canonical contract",
        ));
    }
    let source_span =
        memphant_core::ground_user_evidence_quote(episode_body, &event.evidence_quote)
            .ok_or_else(|| invalid("evidence quote must uniquely match a user turn"))?;
    Ok(StructuredStateOp {
        operation: StructuredStateOperation::Append,
        namespace: "quantity_event.v1".to_string(),
        item_key: event.measure,
        target_unit_ids: vec![],
        fields,
        evidence_quote: event.evidence_quote,
        source_span,
        valid_from: None,
        valid_to: None,
    })
}

fn quantity_value_is_grounded(value: &str, evidence: &str) -> bool {
    let bytes = evidence.as_bytes();
    let mut index = 0;
    while index < bytes.len() {
        let starts_number = bytes[index].is_ascii_digit()
            || (bytes[index] == b'-' && bytes.get(index + 1).is_some_and(u8::is_ascii_digit));
        if !starts_number {
            index += 1;
            continue;
        }
        let start = index;
        index += 1;
        while bytes.get(index).is_some_and(u8::is_ascii_digit)
            || (bytes
                .get(index)
                .is_some_and(|byte| matches!(*byte, b'.' | b','))
                && bytes.get(index + 1).is_some_and(u8::is_ascii_digit))
        {
            index += 1;
        }
        let bounded = bytes
            .get(start.wrapping_sub(1))
            .is_none_or(|byte| !byte.is_ascii_alphanumeric() && *byte != b'_')
            && bytes
                .get(index)
                .is_none_or(|byte| !byte.is_ascii_alphanumeric() && *byte != b'_');
        if bounded && evidence[start..index].replace(',', "") == value {
            return true;
        }
    }
    false
}

fn content_date(body: &str) -> Option<String> {
    let header = body.lines().next()?.trim();
    if !header.starts_with('[') {
        return None;
    }
    let marker = "[date ";
    let start = header.find(marker)? + marker.len();
    let date = header.get(start..start + 10)?;
    if header.as_bytes().get(start + 10) != Some(&b']') {
        return None;
    }
    date.parse::<jiff::civil::Date>()
        .ok()
        .filter(|parsed| parsed.to_string() == date)
        .map(|_| format!("{date}T00:00:00Z"))
}

fn response_schema() -> Value {
    json!({
        "type": "object",
        "additionalProperties": false,
        "properties": {
            "state_operations": {
                "type": "array",
                "description": "Durable non-event state. Numeric plans, budgets, goals, targets, thresholds, durations, ranges, capacities, percentages, forecasts, and proposal metrics belong here, not in quantity_events.",
                "items": {
                    "type": "object",
                    "additionalProperties": false,
                    "properties": {
                        "operation": {"type": "string", "enum": ["create", "replace", "delete"]},
                        "namespace": {"type": ["string", "null"], "pattern": "^[a-z0-9]+(?:_[a-z0-9]+)*$"},
                        "item_key": {"type": ["string", "null"], "pattern": "^[a-z0-9]+(?:_[a-z0-9]+)*$"},
                        "target_unit_ids": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "pattern": "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
                            }
                        },
                        "fields": {"type": "array", "description": "Generic non-preference state payload only. Never put user preferences here, and never copy operation, namespace, item_key, target_unit_ids, valid_from, or valid_to into fields.", "items": {
                            "type": "object", "additionalProperties": false,
                            "properties": {
                                "key": {"type": "string"},
                                "value_type": {"type": "string", "enum": ["string", "decimal", "boolean", "timestamp", "null"]},
                                "value": {"type": "string"}
                            },
                            "required": ["key", "value_type", "value"]
                        }},
                        "evidence_quote": {"type": "string"},
                        "valid_from": {
                            "type": ["string", "null"],
                            "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})$"
                        },
                        "valid_to": {
                            "type": ["string", "null"],
                            "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})$"
                        }
                    },
                    "required": ["operation", "namespace", "item_key", "target_unit_ids", "fields", "evidence_quote", "valid_from", "valid_to"]
                }
            },
            "preference_operations": {
                "type": "array",
                "description": "Only explicit durable first-person user preferences. Never place preferences in state_operations.",
                "items": {
                    "type": "object",
                    "additionalProperties": false,
                    "properties": {
                        "operation": {"type": "string", "enum": ["create", "replace", "delete"]},
                        "namespace": {"type": ["string", "null"], "pattern": "^[a-z0-9]+(?:_[a-z0-9]+)*$"},
                        "item_key": {"type": ["string", "null"], "pattern": "^[a-z0-9]+(?:_[a-z0-9]+)*$"},
                        "target_unit_ids": {"type": "array", "items": {"type": "string", "pattern": "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"}},
                        "preference_value": {"type": "string", "minLength": 1, "description": "Exact preferred or disliked subject copied from the user evidence."},
                        "memory_role": {"type": "string", "enum": ["personalization"]},
                        "epistemic_use": {"type": "string", "enum": ["not_factual_evidence"]},
                        "applicability_scope": {"type": "string", "description": "Exact scope phrase copied from the evidence quote, or empty when no explicit scope exists."},
                        "evidence_quote": {"type": "string"},
                        "valid_from": {"type": ["string", "null"], "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})$"},
                        "valid_to": {"type": ["string", "null"], "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})$"}
                    },
                    "required": ["operation", "namespace", "item_key", "target_unit_ids", "preference_value", "memory_role", "epistemic_use", "applicability_scope", "evidence_quote", "valid_from", "valid_to"]
                }
            },
            "quantity_events": {
                "type": "array",
                "description": "Only observed, completed, aggregatable real-world occurrences explicitly reported by the user.",
                "items": {
                    "type": "object",
                    "additionalProperties": false,
                    "properties": {
                        "value": {
                            "type": "string",
                            "pattern": "^-?(?:0|[1-9][0-9]*)(?:\\.[0-9]{1,18})?$",
                            "description": "Canonical observed decimal from the evidence. Remove only standard comma thousands separators; never calculate, round, infer, or convert."
                        },
                        "measure": {
                            "type": "string",
                            "pattern": "^[a-z0-9]+(?:_[a-z0-9]+)*$",
                            "description": "Stable aggregate series, such as food_spending or daily_steps; put subcategories in dimensions."
                        },
                        "unit": {"type": "string", "pattern": "^[a-z0-9]+(?:_[a-z0-9]+)*$"},
                        "occurred_at": {
                            "type": "string",
                            "pattern": "^(?:|[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2}))$",
                            "description": "Explicit RFC3339 occurrence time, or an empty string to use the trusted episode date."
                        },
                        "dimensions": {"type": "array", "description": "Optional dimensions with each key used at most once.", "items": {
                            "type": "object", "additionalProperties": false,
                            "properties": {
                                "key": {"type": "string", "pattern": "^[a-z0-9]+(?:_[a-z0-9]+)*$"},
                                "value": {"type": "string"}
                            },
                            "required": ["key", "value"]
                        }},
                        "evidence_quote": {"type": "string"}
                    },
                    "required": ["value", "measure", "unit", "occurred_at", "dimensions", "evidence_quote"]
                }
            }
        },
        "required": ["state_operations", "preference_operations", "quantity_events"]
    })
}

fn response_schema_for_request(active_state_is_empty: bool, evidence_quotes: &[String]) -> Value {
    let mut schema = response_schema();
    if active_state_is_empty {
        let state = &mut schema["properties"]["state_operations"]["items"];
        state["properties"]["operation"]["enum"] = json!(["create"]);
        state["properties"]["namespace"]["type"] = json!("string");
        state["properties"]["item_key"]["type"] = json!("string");
        state["properties"]["target_unit_ids"]["maxItems"] = json!(0);
        state["properties"]["fields"]["minItems"] = json!(1);
        let preference = &mut schema["properties"]["preference_operations"]["items"];
        preference["properties"]["operation"]["enum"] = json!(["create"]);
        preference["properties"]["namespace"]["type"] = json!("string");
        preference["properties"]["item_key"]["type"] = json!("string");
        preference["properties"]["target_unit_ids"]["maxItems"] = json!(0);
    }
    if !evidence_quotes.is_empty() {
        let allowed = json!(evidence_quotes);
        schema["properties"]["state_operations"]["items"]["properties"]["evidence_quote"]["enum"] =
            allowed.clone();
        schema["properties"]["preference_operations"]["items"]["properties"]["evidence_quote"]["enum"] =
            allowed.clone();
        schema["properties"]["quantity_events"]["items"]["properties"]["evidence_quote"]["enum"] =
            allowed;
    }
    let preference_scopes = evidence_quotes
        .iter()
        .filter(|quote| evidence_requires_preference(quote))
        .map(|quote| {
            grounded_applicability_scopes(std::slice::from_ref(quote))
                .into_iter()
                .find(|scope| !scope.is_empty())
        })
        .collect::<Vec<_>>();
    let allowed_scopes =
        if !preference_scopes.is_empty() && preference_scopes.iter().all(Option::is_some) {
            preference_scopes
                .into_iter()
                .flatten()
                .collect::<BTreeSet<_>>()
                .into_iter()
                .collect::<Vec<_>>()
        } else {
            grounded_applicability_scopes(evidence_quotes)
        };
    schema["properties"]["preference_operations"]["items"]["properties"]["applicability_scope"]["enum"] =
        json!(allowed_scopes);
    let preference_values = evidence_quotes
        .iter()
        .filter_map(|quote| grounded_preference_value(quote).ok().flatten())
        .collect::<Vec<_>>();
    if !preference_values.is_empty() {
        schema["properties"]["preference_operations"]["maxItems"] = json!(preference_values.len());
        schema["properties"]["preference_operations"]["items"]["properties"]["preference_value"]
            ["enum"] = json!(preference_values);
    }
    if evidence_quotes
        .iter()
        .any(|quote| evidence_requires_preference(quote))
    {
        schema["properties"]["preference_operations"]["minItems"] = json!(1);
    }
    schema
}

fn evidence_requires_preference(evidence: &str) -> bool {
    let normalized = evidence.to_ascii_lowercase();
    let explicit_cue = [
        "i prefer ",
        "i usually prefer ",
        "i now prefer ",
        "i like ",
        "i dislike ",
        "i love ",
        "i hate ",
        "my favorite ",
        "my favourite ",
    ]
    .iter()
    .any(|cue| normalized.contains(cue));
    explicit_cue
        || repeatable_success_preference_value(evidence)
            .ok()
            .flatten()
            .is_some()
}

fn evidence_is_rejected_experience(evidence: &str) -> bool {
    let normalized = evidence.to_ascii_lowercase();
    normalized.contains("did not work for me")
        && (normalized.contains("would not choose ") || normalized.contains("wouldn't choose "))
        && normalized.contains(" again")
}

fn wire_operation_looks_like_preference(operation: &WireStateOperation) -> bool {
    operation
        .namespace
        .as_deref()
        .is_some_and(|value| value.contains("preference"))
        || operation
            .item_key
            .as_deref()
            .is_some_and(|value| value.contains("preference"))
        || !operation.preference_value.trim().is_empty()
        || !operation.memory_role.trim().is_empty()
        || !operation.epistemic_use.trim().is_empty()
        || operation.fields.iter().any(|field| {
            matches!(
                field.key.as_str(),
                "memory_role" | "epistemic_use" | "applicability_scope"
            )
        })
}

fn explicit_preference_value(evidence: &str) -> Result<Option<&str>, ()> {
    let statement = evidence.trim();
    let normalized = statement.to_ascii_lowercase();
    let mut starts = Vec::new();
    for cue in [
        "I usually prefer ",
        "I now prefer ",
        "I prefer ",
        "I dislike ",
        "I like ",
        "I love ",
        "I hate ",
    ] {
        let cue = cue.to_ascii_lowercase();
        let mut offset = 0;
        while let Some(found) = normalized[offset..].find(&cue) {
            let start = offset + found + cue.len();
            starts.push(start);
            offset = start;
        }
    }
    starts.sort_unstable();
    starts.dedup();
    let [start] = starts.as_slice() else {
        return if starts.is_empty() { Ok(None) } else { Err(()) };
    };
    let candidate = &statement[*start..];
    let end = candidate.find(['.', '!', '?']).unwrap_or(candidate.len());
    let value = candidate[..end].trim();
    let value = value.strip_suffix(" instead").unwrap_or(value).trim();
    Ok((!value.is_empty()).then_some(value))
}

fn repeatable_success_preference_value(evidence: &str) -> Result<Option<&str>, ()> {
    const CUE: &str = "the part that consistently worked for me was ";
    let statement = evidence.trim();
    let normalized = statement.to_ascii_lowercase();
    if !normalized.contains("i would choose it again")
        || !normalized.contains("successful outcome was repeatable")
    {
        return Ok(None);
    }
    let starts = normalized
        .match_indices(CUE)
        .map(|(start, _)| start + CUE.len())
        .collect::<Vec<_>>();
    let [start] = starts.as_slice() else {
        return if starts.is_empty() { Ok(None) } else { Err(()) };
    };
    let candidate = &statement[*start..];
    let end = candidate.find(['.', '!', '?']).unwrap_or(candidate.len());
    let value = candidate[..end].trim();
    Ok((!value.is_empty()).then_some(value))
}

fn grounded_preference_value(evidence: &str) -> Result<Option<&str>, ()> {
    if let Some(value) = explicit_preference_value(evidence)? {
        return Ok(Some(value));
    }
    repeatable_success_preference_value(evidence)
}

fn grounded_applicability_scopes(evidence_quotes: &[String]) -> Vec<String> {
    let mut scopes = vec![String::new()];
    for quote in evidence_quotes {
        if let Some(scope) = grounded_applicability_scope(quote)
            && !scopes.iter().any(|existing| existing == scope)
        {
            scopes.push(scope.to_string());
        }
    }
    scopes
}

fn grounded_applicability_scope(evidence: &str) -> Option<&str> {
    explicit_applicability_scope(evidence)
        .or_else(|| repeatable_success_applicability_scope(evidence))
}

fn repeatable_success_applicability_scope(evidence: &str) -> Option<&str> {
    repeatable_success_preference_value(evidence)
        .ok()
        .flatten()?;
    let evidence = evidence.strip_prefix("Update: ").unwrap_or(evidence);
    let rest = evidence.strip_prefix("During the latest ")?;
    let (scope, _) = rest.split_once(',')?;
    let scope = scope.trim();
    (!scope.is_empty()).then_some(scope)
}

fn explicit_applicability_scope(evidence: &str) -> Option<&str> {
    let evidence = evidence.strip_prefix("Update: ").unwrap_or(evidence);
    for prefix in ["For ", "When ", "In "] {
        let Some(rest) = evidence.strip_prefix(prefix) else {
            continue;
        };
        let (scope, _) = rest.split_once(',')?;
        let scope = scope.trim();
        return (!scope.is_empty()).then_some(scope);
    }
    None
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
    use std::sync::Mutex;

    use super::*;
    use memphant_core::ActiveStructuredState;
    use memphant_types::{EpisodeId, UnitId};

    struct FakeTransport {
        responses: Mutex<VecDeque<Result<HttpResponse, String>>>,
        generation_responses: Mutex<VecDeque<Result<Value, String>>>,
        calls: Mutex<usize>,
        generation_calls: Mutex<usize>,
    }

    struct SlowTransport;

    impl Transport for SlowTransport {
        fn post(&self, _: &Value) -> Result<HttpResponse, String> {
            std::thread::sleep(Duration::from_millis(150));
            Ok(success(
                json!({"state_operations": [], "quantity_events": []}),
            ))
        }

        fn generation(&self, response_id: &str) -> Result<Value, String> {
            Ok(generation_success(response_id))
        }
    }

    impl FakeTransport {
        fn new(responses: Vec<Result<HttpResponse, String>>) -> Arc<Self> {
            Arc::new(Self {
                responses: Mutex::new(responses.into()),
                generation_responses: Mutex::new(VecDeque::new()),
                calls: Mutex::new(0),
                generation_calls: Mutex::new(0),
            })
        }

        fn with_generations(
            responses: Vec<Result<HttpResponse, String>>,
            generation_responses: Vec<Result<Value, String>>,
        ) -> Arc<Self> {
            Arc::new(Self {
                responses: Mutex::new(responses.into()),
                generation_responses: Mutex::new(generation_responses.into()),
                calls: Mutex::new(0),
                generation_calls: Mutex::new(0),
            })
        }
    }

    impl Transport for FakeTransport {
        fn post(&self, _: &Value) -> Result<HttpResponse, String> {
            *self.calls.lock().unwrap() += 1;
            self.responses.lock().unwrap().pop_front().unwrap()
        }

        fn generation(&self, response_id: &str) -> Result<Value, String> {
            *self.generation_calls.lock().unwrap() += 1;
            self.generation_responses
                .lock()
                .unwrap()
                .pop_front()
                .unwrap_or_else(|| Ok(generation_success(response_id)))
        }
    }

    fn prompt_fixture() -> String {
        fs::read_to_string(
            Path::new(env!("CARGO_MANIFEST_DIR")).join("../../config/structured-state-v1.txt"),
        )
        .expect("checked-in structured-state prompt")
    }

    fn provider(transport: Arc<dyn Transport>) -> OpenRouterStructuredState {
        OpenRouterStructuredState::new(
            DEFAULT_MODEL.to_string(),
            prompt_fixture(),
            transport,
            Duration::ZERO,
            None,
        )
    }

    fn request(body: &str) -> StructuredStateRequest {
        StructuredStateRequest {
            episode_id: EpisodeId::from_u128(1),
            episode_body: body.to_string(),
            active_items: vec![],
        }
    }

    fn success(content: Value) -> HttpResponse {
        HttpResponse {
            status: 200,
            body: json!({"id": "gen-test", "model": DEFAULT_MODEL, "provider": "OpenAI", "choices": [{"message": {"content": content.to_string()}}], "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3, "cost": 0.001}}),
            retry_after: None,
        }
    }

    fn generation_success(response_id: &str) -> Value {
        json!({"data": {
            "id": response_id,
            "model": DEFAULT_MODEL,
            "provider_name": "OpenAI",
            "tokens_prompt": 1,
            "tokens_completion": 2,
            "total_cost": 0.001
        }})
    }

    #[test]
    fn request_uses_strict_supported_openrouter_parameters() {
        let value = provider(FakeTransport::new(vec![])).request(&request("user: hello"));
        assert_eq!(value["model"], DEFAULT_MODEL);
        assert!(value.get("temperature").is_none());
        assert_eq!(value["seed"], 0);
        assert_eq!(value["stream"], false);
        assert_eq!(value["max_tokens"], 4096);
        assert_eq!(value["provider"]["require_parameters"], true);
        assert_eq!(value["response_format"]["json_schema"]["strict"], true);
        let schema = &value["response_format"]["json_schema"]["schema"];
        assert_eq!(schema["additionalProperties"], false);
        assert_eq!(
            schema["properties"]["state_operations"]["items"]["additionalProperties"],
            false
        );
        assert_eq!(
            schema["properties"]["quantity_events"]["items"]["additionalProperties"],
            false
        );
        let state = &schema["properties"]["state_operations"]["items"];
        assert_eq!(state["properties"]["operation"]["enum"], json!(["create"]));
        assert_eq!(state["properties"]["namespace"]["type"], json!("string"));
        assert_eq!(state["properties"]["item_key"]["type"], json!("string"));
        assert_eq!(state["properties"]["target_unit_ids"]["maxItems"], 0);
        assert_eq!(state["properties"]["fields"]["minItems"], 1);
        let preference = &schema["properties"]["preference_operations"]["items"];
        for reserved in [
            "preference_value",
            "memory_role",
            "epistemic_use",
            "applicability_scope",
        ] {
            assert!(
                preference["required"]
                    .as_array()
                    .unwrap()
                    .contains(&json!(reserved))
            );
        }
        assert_eq!(
            preference["properties"]["operation"]["enum"],
            json!(["create"])
        );
        assert_eq!(
            preference["properties"]["memory_role"]["enum"],
            json!(["personalization"])
        );
        assert_eq!(
            preference["properties"]["applicability_scope"]["enum"],
            json!([""])
        );
        for collection in [
            "state_operations",
            "preference_operations",
            "quantity_events",
        ] {
            assert_eq!(
                schema["properties"][collection]["items"]["properties"]["evidence_quote"]["enum"],
                json!(["hello"])
            );
        }
        assert!(
            state["properties"]["target_unit_ids"]
                .get("uniqueItems")
                .is_none(),
            "OpenAI strict schemas reject uniqueItems; duplicate IDs are rejected by decode"
        );
        assert!(
            state["required"]
                .as_array()
                .unwrap()
                .contains(&json!("target_unit_ids"))
        );
        let payload: Value =
            serde_json::from_str(value["messages"][1]["content"].as_str().unwrap()).unwrap();
        assert_eq!(payload["active_state"], json!([]));
        assert_eq!(payload["episode"], "user: hello");
        assert!(
            schema["properties"]["quantity_events"]["items"]["properties"]
                .get("operation")
                .is_none()
        );
        assert!(
            value["messages"][0]["content"]
                .as_str()
                .unwrap()
                .contains("canonical decimal string")
        );
        assert!(
            schema["properties"]["quantity_events"]["items"]["properties"]["occurred_at"]
                ["pattern"]
                .as_str()
                .unwrap()
                .contains("[0-9]{4}")
        );
        let prompt = value["messages"][0]["content"].as_str().unwrap();
        assert!(prompt.contains("Budgets, goals, targets, thresholds"));
        assert!(prompt.contains("observed, completed, aggregatable"));
        assert!(prompt.contains("used to X, but now/lately Y"));
        assert!(prompt.contains("opinions about AI or other people"));
        assert!(prompt.contains("valid_from and valid_to only to explicit RFC3339"));
        assert!(
            schema["properties"]["state_operations"]["items"]["properties"]["valid_from"]
                ["pattern"]
                .as_str()
                .unwrap()
                .contains("T[0-9]{2}")
        );
    }

    #[test]
    fn oversized_request_is_rejected_before_transport() {
        let transport = FakeTransport::new(vec![]);
        let error = provider(transport.clone())
            .extract_sync(&request(&"x".repeat(MAX_REQUEST_BYTES)))
            .unwrap_err();

        assert_eq!(*transport.calls.lock().unwrap(), 0);
        assert_eq!(
            error,
            StructuredStateProviderError::InvalidOutput(format!(
                "request exceeds {MAX_REQUEST_BYTES}-byte limit"
            ))
        );
    }

    #[test]
    fn applicability_scope_schema_uses_only_exact_user_phrases() {
        let value = provider(FakeTransport::new(vec![])).request(&request(
            "user: For subjective choices, I usually prefer QuietTile.\n\nassistant: noted",
        ));
        let schema = &value["response_format"]["json_schema"]["schema"];
        assert_eq!(
            schema["properties"]["preference_operations"]["items"]["properties"]["applicability_scope"]
                ["enum"],
            json!(["subjective choices"])
        );
        assert_eq!(
            schema["properties"]["preference_operations"]["items"]["properties"]["preference_value"]
                ["enum"],
            json!(["QuietTile"])
        );
        assert_eq!(schema["properties"]["preference_operations"]["minItems"], 1);
    }

    #[test]
    fn updated_preference_schema_preserves_current_value_and_scope() {
        let value = provider(FakeTransport::new(vec![])).request(&request(
            "user: Update: For my breakfast suggestion, I now prefer savory breakfasts instead.",
        ));
        let schema = &value["response_format"]["json_schema"]["schema"];
        assert_eq!(
            schema["properties"]["preference_operations"]["items"]["properties"]["preference_value"]
                ["enum"],
            json!(["savory breakfasts"])
        );
        assert_eq!(
            schema["properties"]["preference_operations"]["items"]["properties"]["applicability_scope"]
                ["enum"],
            json!(["my breakfast suggestion"])
        );
    }

    #[test]
    fn explicit_user_preference_is_admitted_when_model_returns_empty_state() {
        let decoded = decode_response(
            success(json!({
                "state_operations": [],
                "preference_operations": [],
                "quantity_events": []
            }))
            .body,
            DEFAULT_MODEL,
            "user: For subjective choices, I usually prefer Vantage.",
        )
        .unwrap();
        assert_eq!(decoded.operations.len(), 1);
        assert_eq!(decoded.operations[0].fields["value"], json!("Vantage"));
        assert_eq!(
            decoded.operations[0].fields["applicability_scope"],
            json!("subjective choices")
        );
        assert!(decoded.rejected.is_empty());
    }

    #[test]
    fn explicit_preference_scope_is_derived_from_grounded_quote() {
        let quote = "For solo planning, I prefer sunrise starts.";
        let decoded = decode_response(
            success(json!({
                "state_operations": [],
                "preference_operations": [{
                    "operation": "create",
                    "namespace": "planning_preferences",
                    "item_key": "start_time",
                    "target_unit_ids": [],
                    "preference_value": "sunrise starts",
                    "memory_role": "personalization",
                    "epistemic_use": "not_factual_evidence",
                    "applicability_scope": "",
                    "evidence_quote": quote,
                    "valid_from": null,
                    "valid_to": null
                }],
                "quantity_events": []
            }))
            .body,
            DEFAULT_MODEL,
            &format!("user: {quote}"),
        )
        .unwrap();
        assert_eq!(
            decoded.operations[0].fields["applicability_scope"],
            json!("solo planning")
        );
        assert!(decoded.rejected.is_empty());
    }

    #[test]
    fn preference_value_is_derived_from_grounded_quote() {
        let quote = "For solo holidays, I prefer a relaxed itinerary.";
        let decoded = decode_response(
            success(json!({
                "state_operations": [],
                "preference_operations": [{
                    "operation": "create",
                    "namespace": "travel_preferences",
                    "item_key": "itinerary_pace",
                    "target_unit_ids": [],
                    "preference_value": "relaxed itinerary",
                    "memory_role": "personalization",
                    "epistemic_use": "not_factual_evidence",
                    "applicability_scope": "solo holidays",
                    "evidence_quote": quote,
                    "valid_from": null,
                    "valid_to": null
                }],
                "quantity_events": []
            }))
            .body,
            DEFAULT_MODEL,
            &format!("user: {quote}"),
        )
        .unwrap();
        assert_eq!(
            decoded.operations[0].fields["value"],
            json!("a relaxed itinerary")
        );
        assert!(decoded.rejected.is_empty());
    }

    #[test]
    fn repeatable_success_preference_is_derived_from_grounded_quote() {
        let quote = "During the latest astronomy meetup, the part that consistently worked for me was a familiar plan. I would choose it again because the successful outcome was repeatable.";
        let decoded = decode_response(
            success(json!({
                "state_operations": [],
                "preference_operations": [{
                    "operation": "create",
                    "namespace": "activity_preferences",
                    "item_key": "astronomy_meetup_plan",
                    "target_unit_ids": [],
                    "preference_value": "the familiar approach",
                    "memory_role": "personalization",
                    "epistemic_use": "not_factual_evidence",
                    "applicability_scope": "",
                    "evidence_quote": quote,
                    "valid_from": null,
                    "valid_to": null
                }],
                "quantity_events": []
            }))
            .body,
            DEFAULT_MODEL,
            &format!("user: {quote}"),
        )
        .unwrap();
        assert!(decoded.rejected.is_empty());
        assert_eq!(decoded.operations.len(), 1);
        assert_eq!(
            decoded.operations[0].fields["value"],
            json!("a familiar plan")
        );
        assert_eq!(
            decoded.operations[0].fields["applicability_scope"],
            json!("astronomy meetup")
        );
    }

    #[test]
    fn repeatable_success_preferences_for_one_activity_remain_additive() {
        let first = "During the latest poetry reading, the part that consistently worked for me was an outdoor setting. I would choose it again because the successful outcome was repeatable.";
        let second = "During the latest poetry reading, the part that consistently worked for me was a lively atmosphere. I would choose it again because the successful outcome was repeatable.";
        let decoded = decode_response(
            success(json!({
                "state_operations": [],
                "preference_operations": [],
                "quantity_events": []
            }))
            .body,
            DEFAULT_MODEL,
            &format!("user: {first}\nuser: {second}"),
        )
        .unwrap();
        assert!(decoded.rejected.is_empty());
        assert_eq!(decoded.operations.len(), 2);
        let values = decoded
            .operations
            .iter()
            .map(|operation| operation.fields["value"].as_str().unwrap())
            .collect::<BTreeSet<_>>();
        assert_eq!(
            values,
            BTreeSet::from(["an outdoor setting", "a lively atmosphere"])
        );
        assert!(decoded.operations.iter().all(|operation| {
            operation.fields["applicability_scope"] == json!("poetry reading")
        }));
    }

    #[test]
    fn ungrounded_quantity_candidates_do_not_suppress_grounded_preferences() {
        let first = "During the latest photography walk, the part that consistently worked for me was collaborative focus. I would choose it again because the successful outcome was repeatable rather than a one-time novelty.";
        let second = "During the latest photography walk, the part that consistently worked for me was a just-in-time arrival. I would choose it again because the successful outcome was repeatable rather than a one-time novelty.";
        let third = "During the latest photography walk, the part that consistently worked for me was an evening session. I would choose it again because the successful outcome was repeatable rather than a one-time novelty.";
        let inferred_quantity = |quote: &str| {
            json!({
                "value": "1",
                "measure": "successful_outcomes",
                "unit": "occurrences",
                "occurred_at": "",
                "dimensions": [],
                "evidence_quote": quote
            })
        };
        let decoded = decode_response(
            success(json!({
                "state_operations": [],
                "preference_operations": [],
                "quantity_events": [
                    inferred_quantity(first),
                    inferred_quantity(second),
                    inferred_quantity(third)
                ]
            }))
            .body,
            DEFAULT_MODEL,
            &format!("user: {first}\nuser: {second}\nuser: {third}"),
        )
        .unwrap();

        assert!(decoded.rejected.is_empty());
        assert_eq!(decoded.operations.len(), 3);
        assert!(decoded.operations.iter().all(|operation| {
            operation.fields["memory_role"] == json!("personalization")
                && operation.operation == StructuredStateOperation::Create
        }));
    }

    #[test]
    fn one_off_success_without_repeat_choice_is_not_promoted() {
        let quote = "During the latest astronomy meetup, the part that worked for me once was a familiar plan.";
        let decoded = decode_response(
            success(json!({
                "state_operations": [],
                "preference_operations": [],
                "quantity_events": []
            }))
            .body,
            DEFAULT_MODEL,
            &format!("user: {quote}"),
        )
        .unwrap();
        assert!(decoded.rejected.is_empty());
        assert!(decoded.operations.is_empty());
    }

    #[test]
    fn versioned_prompt_file_drives_request_and_compiler_identity() {
        let path = std::env::temp_dir().join(format!(
            "memphant-structured-prompt-{}.txt",
            uuid::Uuid::new_v4()
        ));
        let configured = "versioned extraction policy";
        fs::write(&path, format!("{configured}\n")).unwrap();
        let loaded = load_prompt(&path).expect("runtime prompt loads");
        fs::remove_file(path).unwrap();

        let provider = OpenRouterStructuredState::new(
            DEFAULT_MODEL.to_string(),
            loaded,
            FakeTransport::new(vec![]),
            Duration::ZERO,
            None,
        );
        let value = provider.request(&request("user: hello"));

        assert_eq!(value["messages"][0]["content"], configured);
        assert_eq!(provider.identity.prompt_hash, sha256(configured.as_bytes()));
    }

    #[test]
    fn versioned_prompt_file_fails_closed_when_missing_or_empty() {
        let path = std::env::temp_dir().join(format!(
            "memphant-missing-structured-prompt-{}.txt",
            uuid::Uuid::new_v4()
        ));
        assert!(load_prompt(&path).unwrap_err().contains("failed to read"));

        fs::write(&path, " \n").unwrap();
        assert_eq!(
            load_prompt(&path).unwrap_err(),
            format!("{PROMPT_PATH_ENV} must not be empty")
        );
        fs::remove_file(path).unwrap();
    }

    #[test]
    fn completion_prompt_retires_outstanding_state_and_preserves_observed_events() {
        let value = provider(FakeTransport::new(vec![])).request(&request("user: done"));
        let prompt = value["messages"][0]["content"].as_str().unwrap();

        assert!(prompt.contains("completed, finished, fixed, done"));
        assert!(prompt.contains("delete every exact active item"));
        assert!(prompt.contains("Never keep a current Semantic status=completed copy"));
        assert!(prompt.contains("in addition to retiring the outstanding state"));
    }

    #[test]
    fn prompt_keeps_recommendation_guiding_first_person_preferences() {
        let value = provider(FakeTransport::new(vec![])).request(&request("user: I dislike it"));
        let prompt = value["messages"][0]["content"].as_str().unwrap();

        assert!(prompt.contains("first-person likes, dislikes, and preferences"));
        assert!(prompt.contains("topics, genres, creators, or activities"));
        assert!(prompt.contains("guide future recommendations"));
        assert!(prompt.contains("even when mentioned in ordinary conversation"));
        assert!(prompt.contains("opinions about AI or other people"));
        assert!(
            prompt.contains("When active_state is empty, every state operation must be create")
        );
        assert!(prompt.contains("The word current in episode does not imply replace"));
        assert!(prompt.contains(
            "combine every applicable field for one namespace/item_key into one operation"
        ));
    }

    #[test]
    fn request_includes_the_active_snapshot_as_data_not_evidence() {
        let active = ActiveStructuredState {
            unit_id: UnitId::from_u128(7),
            namespace: "profile".to_string(),
            item_key: "city".to_string(),
            fields: BTreeMap::from([("value".to_string(), json!("Oslo"))]),
            valid_from: None,
            valid_to: None,
        };
        let value = provider(FakeTransport::new(vec![])).request(&StructuredStateRequest {
            episode_id: EpisodeId::from_u128(1),
            episode_body: "user: I moved to Bergen.".to_string(),
            active_items: vec![active.clone()],
        });
        let payload: Value =
            serde_json::from_str(value["messages"][1]["content"].as_str().unwrap()).unwrap();
        assert_eq!(payload["active_state"], json!([active]));
        assert_eq!(payload["episode"], "user: I moved to Bergen.");
        assert!(
            value["messages"][0]["content"]
                .as_str()
                .unwrap()
                .contains("Never use active_state")
        );
    }

    #[test]
    fn state_wire_uses_exact_unit_bound_create_replace_delete_shapes() {
        const FIRST: &str = "01890f47-e8b8-7cc3-98c4-dc0c0c07398f";
        const SECOND: &str = "01890f47-e8b8-7cc3-a8c4-dc0c0c07398f";
        let active = vec![
            memphant_core::ActiveStructuredState {
                unit_id: UnitId::from_u128(uuid::Uuid::parse_str(FIRST).unwrap().as_u128()),
                namespace: "profile".to_string(),
                item_key: "city".to_string(),
                fields: BTreeMap::new(),
                valid_from: None,
                valid_to: None,
            },
            memphant_core::ActiveStructuredState {
                unit_id: UnitId::from_u128(uuid::Uuid::parse_str(SECOND).unwrap().as_u128()),
                namespace: "profile".to_string(),
                item_key: "timezone".to_string(),
                fields: BTreeMap::new(),
                valid_from: None,
                valid_to: None,
            },
        ];
        let operation = |operation, namespace, item_key, target_unit_ids, fields| {
            transform_state(
                WireStateOperation {
                    operation,
                    namespace,
                    item_key,
                    target_unit_ids,
                    preference_value: String::new(),
                    memory_role: String::new(),
                    epistemic_use: String::new(),
                    applicability_scope: String::new(),
                    fields,
                    evidence_quote: "change".to_string(),
                    valid_from: None,
                    valid_to: None,
                },
                "user: change",
                &active,
            )
        };
        let field = || {
            vec![WireField {
                key: "value".to_string(),
                value_type: WireValueType::String,
                value: "current".to_string(),
            }]
        };

        let create = operation(
            StructuredStateOperation::Create,
            Some("profile".to_string()),
            Some("city".to_string()),
            vec![],
            field(),
        )
        .unwrap();
        assert!(create[0].target_unit_ids.is_empty());

        let replace = operation(
            StructuredStateOperation::Replace,
            None,
            None,
            vec![FIRST.to_string()],
            field(),
        )
        .unwrap();
        assert_eq!(replace[0].target_unit_ids[0].as_uuid().to_string(), FIRST);

        let replace_with_untrusted_identity = operation(
            StructuredStateOperation::Replace,
            Some("model_guess".to_string()),
            Some("wrong_key".to_string()),
            vec![FIRST.to_string()],
            field(),
        )
        .expect("exact targets make model-authored identity irrelevant");
        assert_eq!(replace_with_untrusted_identity[0].namespace, "profile");
        assert_eq!(replace_with_untrusted_identity[0].item_key, "city");

        let delete = operation(
            StructuredStateOperation::Delete,
            None,
            None,
            vec![FIRST.to_string(), SECOND.to_string()],
            vec![],
        )
        .unwrap();
        assert_eq!(delete.len(), 2);

        for invalid in [
            operation(
                StructuredStateOperation::Create,
                Some("profile".to_string()),
                Some("city".to_string()),
                vec![FIRST.to_string()],
                field(),
            ),
            operation(
                StructuredStateOperation::Replace,
                None,
                None,
                vec![],
                field(),
            ),
            operation(
                StructuredStateOperation::Delete,
                None,
                None,
                vec![FIRST.to_string()],
                field(),
            ),
            operation(
                StructuredStateOperation::Delete,
                None,
                None,
                vec![FIRST.to_string(), FIRST.to_string()],
                vec![],
            ),
        ] {
            assert!(invalid.is_err());
        }
    }

    #[test]
    fn completion_decodes_as_delete_plus_quantity_never_completed_replacement() {
        const TASK: &str = "01890f47-e8b8-7cc3-98c4-dc0c0c07398f";
        const DURATION: &str = "01890f47-e8b8-7cc3-a8c4-dc0c0c07398f";
        let active = [
            (TASK, "planned_activity", json!("walk")),
            (DURATION, "target_duration", json!("30 minutes")),
        ]
        .into_iter()
        .map(|(id, item_key, value)| ActiveStructuredState {
            unit_id: UnitId::from_u128(uuid::Uuid::parse_str(id).unwrap().as_u128()),
            namespace: "exercise_plan".to_string(),
            item_key: item_key.to_string(),
            fields: BTreeMap::from([("value".to_string(), value)]),
            valid_from: None,
            valid_to: None,
        })
        .collect::<Vec<_>>();
        let quote = "I finished my 30 minute walk.";
        let body = format!("[date 2025-06-07]\nuser_agent: {quote}");
        let response = success(json!({
            "state_operations": [{
                "operation": "delete",
                "namespace": null,
                "item_key": null,
                "target_unit_ids": [TASK, DURATION],
                "fields": [],
                "evidence_quote": quote,
                "valid_from": null,
                "valid_to": null
            }],
            "quantity_events": [{
                "value": "30",
                "measure": "exercise_duration",
                "unit": "minutes",
                "occurred_at": "",
                "dimensions": [],
                "evidence_quote": quote
            }]
        }));

        let decoded = decode_response_with_state(response.body, DEFAULT_MODEL, &body, &active)
            .expect("delete plus observed quantity decodes");

        assert!(decoded.rejected.is_empty());
        assert_eq!(
            decoded
                .operations
                .iter()
                .filter(|operation| operation.operation == StructuredStateOperation::Delete)
                .count(),
            2
        );
        assert_eq!(
            decoded
                .operations
                .iter()
                .filter(|operation| operation.operation == StructuredStateOperation::Append)
                .count(),
            1
        );
        assert!(decoded.operations.iter().all(|operation| {
            operation.operation != StructuredStateOperation::Replace
                && (operation.operation != StructuredStateOperation::Delete
                    || operation.fields.is_empty())
        }));
    }

    #[test]
    fn first_person_topic_dislike_decodes_as_durable_preference_create() {
        let quote = "some sci-fi books tackle genetic engineering, a topic I really dislike";
        let body = format!("user_agent: {quote}");
        let response = success(json!({
            "state_operations": [],
            "preference_operations": [{
                "operation": "create",
                "namespace": "book_preferences",
                "item_key": "genetic_engineering_topic",
                "target_unit_ids": [],
                "preference_value": "genetic engineering",
                "memory_role": "personalization",
                "epistemic_use": "not_factual_evidence",
                "applicability_scope": "",
                "evidence_quote": quote,
                "valid_from": null,
                "valid_to": null
            }],
            "quantity_events": []
        }));

        let decoded = decode_response(response.body, DEFAULT_MODEL, &body)
            .expect("first-person recommendation preference decodes");

        assert!(decoded.rejected.is_empty());
        assert_eq!(decoded.operations.len(), 1);
        let preference = &decoded.operations[0];
        assert_eq!(preference.operation, StructuredStateOperation::Create);
        assert_eq!(preference.namespace, "book_preferences");
        assert_eq!(preference.item_key, "genetic_engineering_topic");
        assert!(preference.target_unit_ids.is_empty());
        assert_eq!(preference.evidence_quote, quote);
        assert_eq!(
            preference.fields.get("value"),
            Some(&json!("genetic engineering"))
        );
        assert_eq!(
            preference.fields.get("memory_role"),
            Some(&json!("personalization"))
        );
        assert_eq!(
            preference.fields.get("epistemic_use"),
            Some(&json!("not_factual_evidence"))
        );
    }

    #[test]
    fn grounded_preference_in_generic_channel_gets_reserved_role_fields() {
        let content = json!({
            "state_operations": [{
                "operation": "create", "namespace": "preferences", "item_key": "quiet_tile_default",
                "target_unit_ids": [],
                "fields": [{"key": "preferred_brand", "value_type": "string", "value": "QuietTile"}],
                "evidence_quote": "For subjective choices, I usually prefer QuietTile.",
                "valid_from": null, "valid_to": null
            }],
            "quantity_events": []
        });
        let decoded = decode_response(
            success(content).body,
            DEFAULT_MODEL,
            "user: For subjective choices, I usually prefer QuietTile.",
        )
        .unwrap();
        assert_eq!(decoded.operations[0].fields["value"], json!("QuietTile"));
        assert_eq!(
            decoded.operations[0].fields["memory_role"],
            json!("personalization")
        );
        assert!(decoded.rejected.is_empty());
    }

    #[test]
    fn operation_envelope_keys_are_rejected_inside_state_fields() {
        let content = json!({
            "state_operations": [{
                "operation": "create", "namespace": "profile", "item_key": "city",
                "target_unit_ids": [],
                "fields": [{"key": "item_key", "value_type": "string", "value": "city"}],
                "evidence_quote": "My city is Oslo.",
                "valid_from": null, "valid_to": null
            }],
            "quantity_events": []
        });
        let decoded = decode_response(
            success(content).body,
            DEFAULT_MODEL,
            "user: My city is Oslo.",
        )
        .unwrap();
        assert!(decoded.operations.is_empty());
        assert_eq!(decoded.rejected.get("field_shape"), Some(&1));
    }

    #[test]
    fn flash_is_pinned_to_the_more_reliable_ai_studio_provider_family() {
        let provider = OpenRouterStructuredState::new(
            FLASH_MODEL.to_string(),
            prompt_fixture(),
            FakeTransport::new(vec![]),
            Duration::ZERO,
            None,
        )
        .with_reasoning_effort("high".to_string());
        let value = provider.request(&request("user: hello"));
        assert_eq!(value["provider"]["only"], json!([FLASH_PROVIDER]));
        assert_eq!(value["provider"]["allow_fallbacks"], true);
        assert_eq!(value["provider"]["require_parameters"], true);
        assert_eq!(
            provider.identity().model,
            "google/gemini-3.5-flash;provider=google-ai-studio;seed=0;max_tokens=4096;max_request_bytes=131072;temperature=0;reasoning_effort=high"
        );
        assert_eq!(value["seed"], 0);
        assert_eq!(value["temperature"], 0);
    }

    #[test]
    fn deepseek_extraction_orders_exact_contract_provider_pool() {
        let provider = OpenRouterStructuredState::new(
            DEEPSEEK_MODEL.to_string(),
            prompt_fixture(),
            FakeTransport::new(vec![]),
            Duration::ZERO,
            None,
        );
        let value = provider.request(&request("user: hello"));
        assert_eq!(value["provider"]["order"], json!(["deepinfra", "wandb"]));
        assert_eq!(value["provider"]["only"], json!(["deepinfra", "wandb"]));
        assert_eq!(value["provider"]["allow_fallbacks"], true);
        assert_eq!(value["provider"]["require_parameters"], true);
        assert_eq!(
            provider.identity().model,
            "deepseek/deepseek-v4-flash;providers=deepinfra,wandb;seed=0;max_tokens=4096;max_request_bytes=131072"
        );
    }

    #[test]
    fn accuracy_first_reasoning_is_explicit_and_part_of_compiler_identity() {
        let provider =
            provider(FakeTransport::new(vec![])).with_reasoning_effort("high".to_string());
        let value = provider.request(&request("user: hello"));
        assert_eq!(value["reasoning"]["effort"], "high");
        assert_eq!(
            provider.identity().model,
            format!(
                "{DEFAULT_MODEL};seed=0;max_tokens=4096;max_request_bytes=131072;reasoning_effort=high"
            )
        );
    }

    async fn assert_live_state_mutation(provider: &dyn StructuredStateProvider) {
        let quote = "Remove Head of Engineering from recipients but keep Embedded Software Team. Delete buy groceries and the entire legacy refactor todo, including its due date.";
        let body = format!("[date 2025-06-05]\nuser: {quote}");
        let active_items = [
            (
                "architecture_email",
                "recipients",
                json!("Embedded Software Team; Head of Engineering"),
            ),
            ("todos", "buy_groceries", json!("pending")),
            (
                "todos",
                "legacy_refactor_task",
                json!("Refactor legacy code"),
            ),
            ("todos", "legacy_refactor_due", json!("today")),
        ]
        .into_iter()
        .map(|(namespace, item_key, value)| ActiveStructuredState {
            // Live requests use the same UUIDv7 IDs produced by the store. Synthetic
            // from_u128 IDs are parseable but violate the strict wire UUID pattern.
            unit_id: UnitId::new(),
            namespace: namespace.to_string(),
            item_key: item_key.to_string(),
            fields: BTreeMap::from([("value".to_string(), value)]),
            valid_from: None,
            valid_to: None,
        })
        .collect::<Vec<_>>();
        let operations = provider
            .extract(&StructuredStateRequest {
                episode_id: memphant_types::EpisodeId::new(),
                episode_body: body.clone(),
                active_items,
            })
            .await
            .expect("state mutation extraction");
        let replacement = operations
            .iter()
            .find(|operation| operation.operation == StructuredStateOperation::Replace)
            .expect("partial aggregate removal becomes exact replacement");
        let replacement_json = serde_json::to_string(&replacement.fields).unwrap();
        assert!(replacement_json.contains("Embedded Software Team"));
        assert!(!replacement_json.contains("Head of Engineering"));
        assert_eq!(
            operations
                .iter()
                .filter(|operation| operation.operation == StructuredStateOperation::Delete)
                .count(),
            3,
            "split logical todo deletes every exact current unit"
        );
        assert!(operations.iter().all(|operation| {
            memphant_core::ground_user_evidence_quote(&body, &operation.evidence_quote).is_some()
        }));
        memphant_core::project_structured_state(
            memphant_types::EpisodeId::new(),
            &body,
            &operations,
        )
        .expect("state mutations project exactly");
    }

    #[tokio::test]
    #[ignore = "makes one paid OpenRouter request; requires OPENROUTER_API_KEY and attempt ledger"]
    async fn live_state_mutation_smoke() {
        assert!(
            std::env::var_os(LEDGER_ENV).is_some(),
            "{LEDGER_ENV} is required"
        );
        let provider = provider_from_env()
            .expect("live provider config")
            .expect("MEMPHANT_STRUCTURED_STATE=on");
        assert_live_state_mutation(provider.as_ref()).await;
    }

    #[tokio::test]
    #[ignore = "makes five paid OpenRouter requests; requires OPENROUTER_API_KEY and attempt ledger"]
    async fn live_structured_state_smoke() {
        assert!(
            std::env::var_os(LEDGER_ENV).is_some(),
            "{LEDGER_ENV} is required"
        );
        let provider = provider_from_env()
            .expect("live provider config")
            .expect("MEMPHANT_STRUCTURED_STATE=on");
        let spending = provider
            .extract(&StructuredStateRequest {
                episode_id: memphant_types::EpisodeId::new(),
                episode_body: "[date 2026-07-13]\nuser: I spent $6.80 on breakfast this morning."
                    .to_string(),
                active_items: vec![],
            })
            .await
            .expect("live structured extraction");
        let event = spending
            .iter()
            .find(|operation| operation.operation == StructuredStateOperation::Append)
            .expect("observed spending becomes a quantity event");
        assert_eq!(event.namespace, "quantity_event.v1");
        assert_eq!(event.fields["value"], "6.80");
        assert_eq!(event.fields["measure"], "food_spending");
        assert_eq!(event.fields["unit"], "usd");
        let steps = provider
            .extract(&StructuredStateRequest {
                episode_id: memphant_types::EpisodeId::new(),
                episode_body: "[period weekly] [persona software_engineer] [session 0049] [date 2025-06-03]\nuser_agent: I actually got 7,640 steps in today, which I was pretty happy about."
                    .to_string(),
                active_items: vec![],
            })
            .await
            .expect("formatted step count extraction");
        let steps = steps
            .iter()
            .find(|operation| operation.operation == StructuredStateOperation::Append)
            .expect("observed steps become a quantity event");
        assert_eq!(steps.fields["value"], "7640");
        assert_eq!(steps.fields["measure"], "daily_steps");
        assert_eq!(steps.fields["unit"], "steps");
        let goal_quote = "My current goal is to walk 10,000 steps every day.";
        let goal_body = format!("[date 2025-06-04]\nuser_agent: {goal_quote}");
        let goal = provider
            .extract(&StructuredStateRequest {
                episode_id: memphant_types::EpisodeId::new(),
                episode_body: goal_body.clone(),
                active_items: vec![],
            })
            .await
            .expect("goal extraction");
        assert!(
            goal.iter().any(|operation| {
                operation.operation == StructuredStateOperation::Create
                    && operation.evidence_quote == goal_quote
                    && !operation.fields.is_empty()
            }),
            "an explicit current goal must become cited state"
        );
        memphant_core::project_structured_state(
            memphant_types::EpisodeId::new(),
            &goal_body,
            &goal,
        )
        .expect("goal operations project exactly");

        assert_live_state_mutation(provider.as_ref()).await;

        let long_quote = "I spent $12.40 on dinner tonight.";
        let filler = "assistant: We discussed architecture, deployment, monitoring, testing, documentation, and several hypothetical alternatives that are not user state.\n".repeat(80);
        let long_body = format!("[date 2025-06-06]\n{filler}user_agent: {long_quote}\n{filler}");
        let long = provider
            .extract(&StructuredStateRequest {
                episode_id: memphant_types::EpisodeId::new(),
                episode_body: long_body.clone(),
                active_items: vec![],
            })
            .await
            .expect("long-context extraction");
        let long_event = long
            .iter()
            .find(|operation| operation.operation == StructuredStateOperation::Append)
            .expect("long-context quantity is retained");
        assert_eq!(long_event.evidence_quote, long_quote);
        assert_eq!(long_event.fields["value"], "12.40");
        assert_eq!(long_event.fields["measure"], "food_spending");
        memphant_core::project_structured_state(
            memphant_types::EpisodeId::new(),
            &long_body,
            &long,
        )
        .expect("long-context operations project exactly");
    }

    #[test]
    fn strict_wire_response_transforms_typed_fields_and_dimensions() {
        let transport = FakeTransport::new(vec![Ok(success(json!({
            "state_operations": [],
            "quantity_events": [{
            "value": "8432", "measure": "steps", "unit": "steps",
            "occurred_at": "2025-06-01T08:00:00Z",
            "dimensions": [{"key": "device", "value": "watch"}],
            "evidence_quote": "I walked 8432 steps."
        }]})))]);
        let operations = provider(transport)
            .extract_sync(&StructuredStateRequest {
                episode_id: EpisodeId::from_u128(1),
                episode_body: "user: I walked 8432 steps.".to_string(),
                active_items: vec![],
            })
            .unwrap();
        assert_eq!(operations[0].fields["value"], "8432");
        assert_eq!(operations[0].fields["dimensions"]["device"], "watch");
        assert_eq!(operations[0].fields["type"], "quantity_event.v1");
        assert_eq!(operations[0].source_span, "6-26");
    }

    #[test]
    fn response_schema_leaves_deterministic_source_spans_to_the_runtime() {
        let schema = response_schema();
        for collection in ["state_operations", "quantity_events"] {
            let item = &schema["properties"][collection]["items"];
            assert!(item["properties"].get("source_span").is_none());
            assert!(
                !item["required"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .any(|value| value == "source_span")
            );
        }
    }

    #[test]
    fn quantity_shape_rejects_float_coercion_and_missing_canonical_fields() {
        let event = WireQuantityEvent {
            value: "8.4e3".to_string(),
            measure: "steps".to_string(),
            unit: "steps".to_string(),
            occurred_at: "2025-06-01T00:00:00Z".to_string(),
            dimensions: vec![],
            evidence_quote: "8432 steps".to_string(),
        };
        assert!(transform_quantity(event, "user: 8432 steps").is_err());
    }

    #[test]
    fn quantity_value_must_be_a_numeric_lexeme_in_grounded_evidence() {
        let event = WireQuantityEvent {
            value: "1".to_string(),
            measure: "successful_outcomes".to_string(),
            unit: "occurrences".to_string(),
            occurred_at: "2025-06-01T00:00:00Z".to_string(),
            dimensions: vec![],
            evidence_quote: "The outcome was repeatable rather than a one-time novelty."
                .to_string(),
        };
        let error = transform_quantity(
            event,
            "user: The outcome was repeatable rather than a one-time novelty.",
        )
        .unwrap_err();
        assert!(
            error
                .to_string()
                .contains("value must be preserved exactly")
        );
    }

    #[test]
    fn provider_and_core_share_the_exact_quantity_contract() {
        let event = |value: &str, unit: &str| WireQuantityEvent {
            value: value.to_string(),
            measure: "daily_steps".to_string(),
            unit: unit.to_string(),
            occurred_at: "2025-06-01T00:00:00Z".to_string(),
            dimensions: vec![],
            evidence_quote: format!("Observed {value}."),
        };
        for (value, unit) in [
            ("01", "steps"),
            ("1.1234567890123456789", "steps"),
            ("1", "US Dollars"),
            ("1", "usd/month"),
        ] {
            let event = event(value, unit);
            let body = format!("user: {}", event.evidence_quote);
            assert!(
                transform_quantity(event, &body).is_err(),
                "provider accepted non-canonical {value:?} {unit:?}"
            );
        }
        for (value, unit) in [
            ("0", "usd"),
            ("6.80", "usd"),
            ("1.123456789012345678", "milliseconds"),
            ("40", "percent"),
            ("500000", "orders"),
        ] {
            let event = event(value, unit);
            let body = format!("user: {}", event.evidence_quote);
            let transformed = transform_quantity(event, &body).unwrap();
            assert_eq!(transformed.fields["value"], value);
            assert!(memphant_core::quantity_event_from_fields(&transformed.fields).is_some());
        }
    }

    #[test]
    fn numeric_proposal_is_state_not_an_observed_quantity_event() {
        let proposal = json!({
            "state_operations": [{
                "operation": "create",
                "namespace": "project_proposal",
                "item_key": "order_orchestration_engine",
                "target_unit_ids": [],
                "fields": [
                    {"key": "budget", "value_type": "string", "value": "$575,000"},
                    {"key": "duration", "value_type": "string", "value": "14 months"},
                    {"key": "latency_target", "value_type": "string", "value": "under 150ms at the 95th percentile"}
                ],
                "evidence_quote": "The proposed budget is $575,000 for 14 months.",
                "valid_from": null,
                "valid_to": null
            }],
            "quantity_events": []
        });
        let decoded = decode_response(
            json!({"model": DEFAULT_MODEL, "choices": [{"message": {"content": proposal.to_string()}}]}),
            DEFAULT_MODEL,
            "user: The proposed budget is $575,000 for 14 months.",
        )
        .unwrap();
        assert_eq!(decoded.operations.len(), 1);
        assert!(decoded.rejected.is_empty());
        assert_eq!(decoded.operations[0].namespace, "project_proposal");
    }

    #[test]
    fn semantic_rejection_fails_the_whole_provider_response() {
        let content = json!({
            "quantity_events": [{
                "value": "8.4e3", "measure": "steps", "unit": "steps",
                "occurred_at": "2025-06-01T00:00:00Z", "dimensions": [],
                "evidence_quote": "bad"
            }],
            "state_operations": [
            {
                "operation": "create", "namespace": "profile", "item_key": "city", "target_unit_ids": [],
                "fields": [{"key": "value", "value_type": "string", "value": "Oslo"}],
                "evidence_quote": "My city is Oslo.",
                "valid_from": null, "valid_to": null
            }
        ]});
        let transport = FakeTransport::new(vec![Ok(success(content))]);
        let concrete = transport.clone();
        let error = provider(transport)
            .extract_sync(&StructuredStateRequest {
                episode_id: EpisodeId::from_u128(1),
                episode_body: "user: My city is Oslo.".to_string(),
                active_items: vec![],
            })
            .unwrap_err();
        assert!(error.to_string().contains("rejected operations"));
        assert_eq!(*concrete.calls.lock().unwrap(), 1);
    }

    #[test]
    fn duplicate_state_identity_fails_before_projection_or_persistence() {
        let operation = |field: &str, quote: &str| {
            json!({
                "operation": "create",
                "namespace": "meeting_notes_accessibility",
                "item_key": "key_decisions",
                "target_unit_ids": [],
                "fields": [{"key": field, "value_type": "string", "value": quote}],
                "evidence_quote": quote,
                "valid_from": null,
                "valid_to": null
            })
        };
        let content = json!({
            "state_operations": [
                operation("first", "decision one"),
                operation("second", "decision two")
            ],
            "quantity_events": []
        });
        let transport = FakeTransport::new(vec![Ok(success(content))]);
        let concrete = transport.clone();
        let error = provider(transport)
            .extract_sync(&StructuredStateRequest {
                episode_id: EpisodeId::from_u128(1),
                episode_body: "user: decision one\nuser: decision two".to_string(),
                active_items: vec![],
            })
            .unwrap_err();
        assert!(error.to_string().contains("rejected operations"));
        assert_eq!(*concrete.calls.lock().unwrap(), 1);
    }

    #[test]
    fn duplicate_state_identity_emits_only_nonsecret_shape_diagnostics() {
        let operation = |field: &str, quote: &str| {
            json!({
                "operation": "create",
                "namespace": "private_namespace",
                "item_key": "private_item",
                "target_unit_ids": [],
                "fields": [{"key": field, "value_type": "string", "value": quote}],
                "evidence_quote": quote,
                "valid_from": null,
                "valid_to": null
            })
        };
        let decoded = decode_response(
            success(json!({
                "state_operations": [
                    operation("first", "private first quote"),
                    operation("second", "private second quote")
                ],
                "quantity_events": []
            }))
            .body,
            DEFAULT_MODEL,
            "user: private first quote\nuser: private second quote",
        )
        .unwrap();

        assert_eq!(decoded.rejected["duplicate_state_identity"], 1);
        assert_eq!(decoded.rejection_diagnostics.len(), 1);
        let diagnostic = &decoded.rejection_diagnostics[0];
        assert_eq!(diagnostic["source_channel"], "state_operations");
        assert_eq!(diagnostic["operation"], "create");
        assert_eq!(diagnostic["transformed_count"], 1);
        assert_eq!(diagnostic["identity_collision"], true);
        assert_eq!(diagnostic["namespace_equal"], true);
        assert_eq!(diagnostic["item_key_equal"], true);
        assert_eq!(diagnostic["existing_reserved_role"], false);
        assert_eq!(diagnostic["incoming_reserved_role"], false);
        assert_eq!(diagnostic["quote_order"], "incoming_later");
        assert!(diagnostic["failed_predicates"].is_array());
        let serialized = diagnostic.to_string();
        for secret in [
            "private_namespace",
            "private_item",
            "private first quote",
            "private second quote",
            "first",
            "second",
        ] {
            assert!(!serialized.contains(secret));
        }
    }

    #[test]
    fn generic_and_dedicated_preference_channels_choose_the_later_grounded_value() {
        let generic = |value: &str, quote: &str| {
            json!({
                "operation": "create",
                "namespace": "breakfast",
                "item_key": "suggestion",
                "target_unit_ids": [],
                "fields": [{"key": "value", "value_type": "string", "value": value}],
                "evidence_quote": quote,
                "valid_from": null,
                "valid_to": null
            })
        };
        let dedicated = |value: &str, quote: &str| {
            json!({
                "operation": "create",
                "namespace": "breakfast",
                "item_key": "suggestion",
                "target_unit_ids": [],
                "preference_value": value,
                "memory_role": "personalization",
                "epistemic_use": "not_factual_evidence",
                "applicability_scope": "my breakfast suggestion",
                "evidence_quote": quote,
                "valid_from": null,
                "valid_to": null
            })
        };
        let old = "For my breakfast suggestion, I prefer sweet breakfasts.";
        let current =
            "Update: For my breakfast suggestion, I now prefer savory breakfasts instead.";
        for (state, preference) in [
            (
                generic("sweet breakfasts", old),
                dedicated("savory breakfasts", current),
            ),
            (
                generic("savory breakfasts", current),
                dedicated("sweet breakfasts", old),
            ),
        ] {
            let decoded = decode_response(
                success(json!({
                    "state_operations": [state],
                    "preference_operations": [preference],
                    "quantity_events": []
                }))
                .body,
                DEFAULT_MODEL,
                &format!("user: {old}\nuser: {current}"),
            )
            .unwrap();
            assert_eq!(decoded.operations.len(), 1);
            assert_eq!(
                decoded.operations[0].fields["value"],
                json!("savory breakfasts")
            );
            assert_eq!(
                decoded.operations[0].fields["memory_role"],
                json!("personalization")
            );
            assert!(decoded.rejected.is_empty());
        }
    }

    #[test]
    fn mixed_role_identity_collision_keeps_grounded_personalization_in_either_order() {
        let preference_quote = "For my breakfast suggestion, I prefer savory breakfasts.";
        let neutral_quote = "The organizer sent registration instructions.";
        let preference = json!({
            "operation": "create",
            "namespace": "breakfast",
            "item_key": "suggestion",
            "target_unit_ids": [],
            "fields": [{"key": "value", "value_type": "string", "value": "savory breakfasts"}],
            "evidence_quote": preference_quote,
            "valid_from": null,
            "valid_to": null
        });
        let neutral = json!({
            "operation": "create",
            "namespace": "breakfast",
            "item_key": "suggestion",
            "target_unit_ids": [],
            "fields": [{"key": "note", "value_type": "string", "value": "registration instructions"}],
            "evidence_quote": neutral_quote,
            "valid_from": null,
            "valid_to": null
        });
        for operations in [
            vec![preference.clone(), neutral.clone()],
            vec![neutral.clone(), preference.clone()],
        ] {
            let decoded = decode_response(
                success(json!({
                    "state_operations": operations,
                    "preference_operations": [],
                    "quantity_events": []
                }))
                .body,
                DEFAULT_MODEL,
                &format!("user: {preference_quote}\nuser: {neutral_quote}"),
            )
            .unwrap();

            assert!(decoded.rejected.is_empty());
            assert_eq!(decoded.operations.len(), 1);
            assert_eq!(
                decoded.operations[0].fields["value"],
                json!("savory breakfasts")
            );
            assert_eq!(
                decoded.operations[0].fields["memory_role"],
                json!("personalization")
            );
            assert_eq!(
                decoded.operations[0].fields["epistemic_use"],
                json!("not_factual_evidence")
            );
        }
    }

    #[test]
    fn multiple_preferences_project_exact_values_from_unique_grounded_quotes() {
        let preference = |item_key: &str, value: &str, quote: &str| {
            json!({
                "operation": "create",
                "namespace": "user_preferences",
                "item_key": item_key,
                "target_unit_ids": [],
                "preference_value": value,
                "memory_role": "personalization",
                "epistemic_use": "not_factual_evidence",
                "applicability_scope": "",
                "evidence_quote": quote,
                "valid_from": null,
                "valid_to": null
            })
        };
        let meal = "For the conference dinner, after checking the menu, I now prefer vegetarian plates instead.";
        let seat = "Actually, I prefer window seats for the train.";
        let study = "For study sessions, when time is short, I prefer flashcards.";
        let decoded = decode_response(
            success(json!({
                "state_operations": [],
                "preference_operations": [
                    preference("conference_dinner", "vegetarian food", meal),
                    preference("train_seat", "a seat by the window", seat),
                    preference("study_method", "study cards", study)
                ],
                "quantity_events": []
            }))
            .body,
            DEFAULT_MODEL,
            &format!("user: {meal}\nuser: {seat}\nuser: {study}"),
        )
        .unwrap();

        assert!(decoded.rejected.is_empty());
        assert_eq!(decoded.operations.len(), 3);
        let values = decoded
            .operations
            .iter()
            .map(|operation| operation.fields["value"].as_str().unwrap())
            .collect::<BTreeSet<_>>();
        assert_eq!(
            values,
            BTreeSet::from([
                "flashcards",
                "vegetarian plates",
                "window seats for the train",
            ])
        );
    }

    #[test]
    fn explicit_preference_inventory_fills_omitted_scopes_and_selects_latest_value() {
        let old = "For my conference dinner, I prefer buffet stations.";
        let current = "Update: For my conference dinner, I now prefer plated service instead.";
        let train = "For my train cabin, I prefer quiet-car seating.";
        let report = "For my report delivery, I prefer linked documents.";
        let decoded = decode_response(
            success(json!({
                "state_operations": [],
                "preference_operations": [{
                    "operation": "create",
                    "namespace": "user_preferences",
                    "item_key": "conference_dinner",
                    "target_unit_ids": [],
                    "preference_value": "buffet stations",
                    "memory_role": "personalization",
                    "epistemic_use": "not_factual_evidence",
                    "applicability_scope": "my conference dinner",
                    "evidence_quote": old,
                    "valid_from": null,
                    "valid_to": null
                }],
                "quantity_events": []
            }))
            .body,
            DEFAULT_MODEL,
            &format!("user: {old}\nuser: {current}\nuser: {train}\nuser: {report}"),
        )
        .unwrap();

        assert!(decoded.rejected.is_empty());
        assert_eq!(decoded.operations.len(), 3);
        let values = decoded
            .operations
            .iter()
            .map(|operation| operation.fields["value"].as_str().unwrap())
            .collect::<BTreeSet<_>>();
        assert_eq!(
            values,
            BTreeSet::from(["linked documents", "plated service", "quiet-car seating"])
        );
        assert!(
            decoded
                .operations
                .iter()
                .all(|operation| operation.fields["value"] != json!("buffet stations"))
        );
    }

    #[test]
    fn omitted_explicit_update_replaces_matching_active_scope() {
        let old = ActiveStructuredState {
            unit_id: UnitId::from_u128(41),
            namespace: "meal_preferences".to_string(),
            item_key: "conference_dinner".to_string(),
            fields: BTreeMap::from([
                ("value".to_string(), json!("buffet stations")),
                ("memory_role".to_string(), json!("personalization")),
                ("epistemic_use".to_string(), json!("not_factual_evidence")),
                (
                    "applicability_scope".to_string(),
                    json!("my conference dinner"),
                ),
            ]),
            valid_from: None,
            valid_to: None,
        };
        let update = "Update: For my conference dinner, I now prefer plated service instead.";
        let train = "For my train cabin, I prefer quiet-car seating.";
        let decoded = decode_response_with_state(
            success(json!({
                "state_operations": [],
                "preference_operations": [{
                    "operation": "create",
                    "namespace": "travel_preferences",
                    "item_key": "train_cabin",
                    "target_unit_ids": [],
                    "preference_value": "quiet-car seating",
                    "memory_role": "personalization",
                    "epistemic_use": "not_factual_evidence",
                    "applicability_scope": "my train cabin",
                    "evidence_quote": train,
                    "valid_from": null,
                    "valid_to": null
                }],
                "quantity_events": []
            }))
            .body,
            DEFAULT_MODEL,
            &format!("user: {update}\nuser: {train}"),
            &[old],
        )
        .unwrap();

        let replacement = decoded
            .operations
            .iter()
            .find(|operation| {
                operation.fields["applicability_scope"] == json!("my conference dinner")
            })
            .expect("omitted explicit update is deterministically admitted");
        assert_eq!(replacement.operation, StructuredStateOperation::Replace);
        assert_eq!(replacement.target_unit_ids, vec![UnitId::from_u128(41)]);
        assert_eq!(replacement.fields["value"], json!("plated service"));
    }

    #[test]
    fn one_quote_with_multiple_preference_values_remains_rejected() {
        let quote = "I prefer aisle seats, but for overnight trips I now prefer window seats.";
        let decoded = decode_response(
            success(json!({
                "state_operations": [],
                "preference_operations": [{
                    "operation": "create",
                    "namespace": "user_preferences",
                    "item_key": "train_seat",
                    "target_unit_ids": [],
                    "preference_value": "window seats",
                    "memory_role": "personalization",
                    "epistemic_use": "not_factual_evidence",
                    "applicability_scope": "overnight trips",
                    "evidence_quote": quote,
                    "valid_from": null,
                    "valid_to": null
                }],
                "quantity_events": []
            }))
            .body,
            DEFAULT_MODEL,
            &format!("user: {quote}"),
        )
        .unwrap();

        assert!(decoded.operations.is_empty());
        assert_eq!(decoded.rejected.get("evidence_grounding"), Some(&1));
    }

    #[test]
    fn explicit_preference_arbitration_discards_ungrounded_inferred_candidate() {
        let explicit = "For my next shared meal, I prefer simple takeout.";
        let failed_experience = "Trying multi-course cooking sounded exciting and the presentation was inviting, but it did not work for me: I stopped early and would not choose multi-course cooking again.";
        let preference = |quote: &str| {
            json!({
                "operation": "create",
                "namespace": "meal_preferences",
                "item_key": "shared_meal",
                "target_unit_ids": [],
                "preference_value": "simple takeout",
                "memory_role": "personalization",
                "epistemic_use": "not_factual_evidence",
                "applicability_scope": "my next shared meal",
                "evidence_quote": quote,
                "valid_from": null,
                "valid_to": null
            })
        };
        let decoded = decode_response(
            success(json!({
                "state_operations": [],
                "preference_operations": [
                    preference(explicit),
                    preference(failed_experience)
                ],
                "quantity_events": []
            }))
            .body,
            DEFAULT_MODEL,
            &format!("user: {explicit}\nassistant: noted\nuser: {failed_experience}"),
        )
        .unwrap();

        assert!(decoded.rejected.is_empty());
        assert_eq!(decoded.operations.len(), 1);
        assert_eq!(decoded.operations[0].evidence_quote, explicit);
        assert_eq!(
            decoded.operations[0].fields["value"],
            json!("simple takeout")
        );
    }

    #[test]
    fn unscoped_explicit_preference_arbitrates_ungrounded_inferred_candidate() {
        let explicit = "I prefer simple takeout.";
        let failed_experience = "Multi-course cooking looked polished, but it did not work for me.";
        let preference = |quote: &str| {
            json!({
                "operation": "create",
                "namespace": "meal_preferences",
                "item_key": "shared_meal",
                "target_unit_ids": [],
                "preference_value": "simple takeout",
                "memory_role": "personalization",
                "epistemic_use": "not_factual_evidence",
                "applicability_scope": "",
                "evidence_quote": quote,
                "valid_from": null,
                "valid_to": null
            })
        };
        let decoded = decode_response(
            success(json!({
                "state_operations": [],
                "preference_operations": [
                    preference(explicit),
                    preference(failed_experience)
                ],
                "quantity_events": []
            }))
            .body,
            DEFAULT_MODEL,
            &format!("user: {explicit}\nassistant: noted\nuser: {failed_experience}"),
        )
        .unwrap();

        assert!(decoded.rejected.is_empty());
        assert_eq!(decoded.operations.len(), 1);
        assert_eq!(decoded.operations[0].evidence_quote, explicit);
        assert_eq!(
            decoded.operations[0].fields["value"],
            json!("simple takeout")
        );
    }

    #[test]
    fn single_unscoped_explicit_preference_survives_model_omission() {
        let explicit = "I prefer a rigid metal tray.";
        let failed_experience = "Trying a flexible silicone tray sounded exciting and the presentation was inviting, but it did not work for me: I stopped early and would not choose a flexible silicone tray again.";
        let decoded = decode_response(
            success(json!({
                "state_operations": [],
                "preference_operations": [{
                    "operation": "create",
                    "namespace": "user_preferences",
                    "item_key": "ice_tray",
                    "target_unit_ids": [],
                    "preference_value": "a flexible silicone tray",
                    "memory_role": "personalization",
                    "epistemic_use": "not_factual_evidence",
                    "applicability_scope": "",
                    "evidence_quote": failed_experience,
                    "valid_from": null,
                    "valid_to": null
                }],
                "quantity_events": []
            }))
            .body,
            DEFAULT_MODEL,
            &format!("user: {explicit}\nassistant: noted\nuser: {failed_experience}"),
        )
        .unwrap();

        assert!(decoded.rejected.is_empty());
        assert_eq!(decoded.operations.len(), 1);
        assert_eq!(decoded.operations[0].evidence_quote, explicit);
        assert_eq!(
            decoded.operations[0].fields["value"],
            json!("a rigid metal tray")
        );
        assert_eq!(
            decoded.operations[0].fields["applicability_scope"],
            json!("")
        );
    }

    #[test]
    fn single_unscoped_explicit_preference_rejects_model_invented_identity() {
        let explicit = "I prefer a soft sleeve.";
        let failed_experience = "Trying a hard-shell case sounded exciting and the presentation was inviting, but it did not work for me: I stopped early and would not choose a hard-shell case again.";
        let decoded = decode_response(
            success(json!({
                "state_operations": [{
                    "operation": "create",
                    "namespace": "case_preferences",
                    "item_key": "phone_case_preference",
                    "target_unit_ids": [],
                    "fields": [{
                        "key": "value",
                        "value_type": "string",
                        "value": "a soft sleeve"
                    }],
                    "evidence_quote": explicit,
                    "valid_from": null,
                    "valid_to": null
                }],
                "preference_operations": [],
                "quantity_events": []
            }))
            .body,
            DEFAULT_MODEL,
            &format!(
                "user: {explicit}\nassistant: I will remember that explicit preference.\nuser: {failed_experience}\nassistant: Understood; the positive presentation did not make the experience successful."
            ),
        )
        .unwrap();

        assert!(decoded.rejected.is_empty());
        assert_eq!(decoded.operations.len(), 1);
        assert_eq!(decoded.operations[0].namespace, "user_preferences");
        assert!(decoded.operations[0].item_key.starts_with("unscoped_"));
        assert_eq!(decoded.operations[0].evidence_quote, explicit);
        assert_eq!(
            decoded.operations[0].fields["value"],
            json!("a soft sleeve")
        );
        assert_eq!(
            decoded.operations[0].fields["applicability_scope"],
            json!("")
        );
    }

    #[test]
    fn multiple_unscoped_explicit_preferences_are_not_canonicalized() {
        let first = "I prefer a soft sleeve.";
        let second = "I prefer green tea.";
        let decoded = decode_response(
            success(json!({
                "state_operations": [],
                "preference_operations": [
                    {
                        "operation": "create",
                        "namespace": "case_preferences",
                        "item_key": "case_style",
                        "target_unit_ids": [],
                        "preference_value": "a soft sleeve",
                        "memory_role": "personalization",
                        "epistemic_use": "not_factual_evidence",
                        "applicability_scope": "",
                        "evidence_quote": first,
                        "valid_from": null,
                        "valid_to": null
                    },
                    {
                        "operation": "create",
                        "namespace": "drink_preferences",
                        "item_key": "tea_style",
                        "target_unit_ids": [],
                        "preference_value": "green tea",
                        "memory_role": "personalization",
                        "epistemic_use": "not_factual_evidence",
                        "applicability_scope": "",
                        "evidence_quote": second,
                        "valid_from": null,
                        "valid_to": null
                    }
                ],
                "quantity_events": []
            }))
            .body,
            DEFAULT_MODEL,
            &format!("user: {first}\nassistant: noted\nuser: {second}"),
        )
        .unwrap();

        assert!(decoded.rejected.is_empty());
        assert_eq!(decoded.operations.len(), 2);
        assert_eq!(decoded.operations[0].namespace, "case_preferences");
        assert_eq!(decoded.operations[0].item_key, "case_style");
        assert_eq!(decoded.operations[1].namespace, "drink_preferences");
        assert_eq!(decoded.operations[1].item_key, "tea_style");
        assert!(
            decoded
                .operations
                .iter()
                .all(|operation| !operation.item_key.starts_with("unscoped_"))
        );
    }

    #[test]
    fn unscoped_explicit_preference_discards_malformed_generic_inference() {
        let explicit = "I prefer pruning shrubs.";
        let failed_experience = "Trying planting herbs sounded exciting and the presentation was inviting, but it did not work for me: I stopped early and would not choose planting herbs again.";
        let decoded = decode_response(
            success(json!({
                "state_operations": [{
                    "operation": "create",
                    "namespace": "garden_preferences",
                    "item_key": "garden_activity",
                    "target_unit_ids": [],
                    "fields": [{
                        "key": "value",
                        "value_type": "string",
                        "value": "planting herbs"
                    }],
                    "evidence_quote": failed_experience,
                    "valid_from": null,
                    "valid_to": null
                }],
                "preference_operations": [{
                    "operation": "create",
                    "namespace": "user_preferences",
                    "item_key": "preference_1",
                    "target_unit_ids": [],
                    "preference_value": "pruning shrubs",
                    "memory_role": "personalization",
                    "epistemic_use": "not_factual_evidence",
                    "applicability_scope": "",
                    "evidence_quote": explicit,
                    "valid_from": null,
                    "valid_to": null
                }],
                "quantity_events": []
            }))
            .body,
            DEFAULT_MODEL,
            &format!("user: {explicit}\nassistant: noted\nuser: {failed_experience}"),
        )
        .unwrap();

        assert!(decoded.rejected.is_empty());
        assert_eq!(decoded.operations.len(), 1);
        assert_eq!(decoded.operations[0].evidence_quote, explicit);
        assert_eq!(
            decoded.operations[0].fields["value"],
            json!("pruning shrubs")
        );
    }

    #[test]
    fn scoped_explicit_preference_discards_unscoped_failed_experience_inference() {
        let explicit = "For my next study session, I prefer long video lectures.";
        let failed_experience = "Trying practice drills sounded exciting and the presentation was inviting, but it did not work for me: I stopped early and would not choose practice drills again.";
        let decoded = decode_response(
            success(json!({
                "state_operations": [{
                    "operation": "create",
                    "namespace": "study_preferences",
                    "item_key": "study_method",
                    "target_unit_ids": [],
                    "fields": [{
                        "key": "value",
                        "value_type": "string",
                        "value": "practice drills"
                    }],
                    "evidence_quote": failed_experience,
                    "valid_from": null,
                    "valid_to": null
                }],
                "preference_operations": [{
                    "operation": "create",
                    "namespace": "user_preferences",
                    "item_key": "study_session",
                    "target_unit_ids": [],
                    "preference_value": "long video lectures",
                    "memory_role": "personalization",
                    "epistemic_use": "not_factual_evidence",
                    "applicability_scope": "my next study session",
                    "evidence_quote": explicit,
                    "valid_from": null,
                    "valid_to": null
                }],
                "quantity_events": []
            }))
            .body,
            DEFAULT_MODEL,
            &format!("user: {explicit}\nassistant: noted\nuser: {failed_experience}"),
        )
        .unwrap();

        assert!(decoded.rejected.is_empty());
        assert_eq!(decoded.operations.len(), 1);
        assert_eq!(decoded.operations[0].evidence_quote, explicit);
        assert_eq!(
            decoded.operations[0].fields["value"],
            json!("long video lectures")
        );
    }

    #[test]
    fn ungrounded_inferred_preference_without_explicit_authority_remains_rejected() {
        let quote = "Trying multi-course cooking sounded exciting, but it did not work for me.";
        let decoded = decode_response(
            success(json!({
                "state_operations": [],
                "preference_operations": [{
                    "operation": "create",
                    "namespace": "meal_preferences",
                    "item_key": "shared_meal",
                    "target_unit_ids": [],
                    "preference_value": "simple takeout",
                    "memory_role": "personalization",
                    "epistemic_use": "not_factual_evidence",
                    "applicability_scope": "",
                    "evidence_quote": quote,
                    "valid_from": null,
                    "valid_to": null
                }],
                "quantity_events": []
            }))
            .body,
            DEFAULT_MODEL,
            &format!("user: {quote}"),
        )
        .unwrap();

        assert!(decoded.operations.is_empty());
        assert_eq!(decoded.rejected.get("evidence_grounding"), Some(&1));
    }

    #[test]
    fn preference_delete_never_synthesizes_fields_from_its_quote() {
        let target = UnitId::from_u128(7);
        let active = ActiveStructuredState {
            unit_id: target,
            namespace: "breakfast_preferences".to_string(),
            item_key: "suggestion".to_string(),
            fields: BTreeMap::from([
                ("value".to_string(), json!("sweet breakfasts")),
                ("memory_role".to_string(), json!("personalization")),
                ("epistemic_use".to_string(), json!("not_factual_evidence")),
            ]),
            valid_from: None,
            valid_to: None,
        };
        let quote = "For my breakfast suggestion, I prefer savory breakfasts.";
        let operations = transform_state(
            WireStateOperation {
                operation: StructuredStateOperation::Delete,
                namespace: None,
                item_key: None,
                target_unit_ids: vec![target.as_uuid().to_string()],
                preference_value: String::new(),
                memory_role: String::new(),
                epistemic_use: String::new(),
                applicability_scope: String::new(),
                fields: vec![],
                evidence_quote: quote.to_string(),
                valid_from: None,
                valid_to: None,
            },
            &format!("user: {quote}"),
            &[active],
        )
        .unwrap();

        assert_eq!(operations.len(), 1);
        assert!(operations[0].fields.is_empty());
    }

    #[test]
    fn exact_duplicate_state_operation_is_coalesced() {
        let operation = json!({
            "operation": "create",
            "namespace": "meeting_notes_accessibility",
            "item_key": "key_decision",
            "target_unit_ids": [],
            "fields": [{"key": "decision", "value_type": "string", "value": "use captions"}],
            "evidence_quote": "use captions",
            "valid_from": null,
            "valid_to": null
        });
        let decoded = decode_response(
            success(json!({
                "state_operations": [operation.clone(), operation],
                "quantity_events": []
            }))
            .body,
            DEFAULT_MODEL,
            "user: use captions",
        )
        .unwrap();
        assert_eq!(decoded.operations.len(), 1);
        assert!(decoded.rejected.is_empty());
    }

    #[test]
    fn later_grounded_preference_replaces_earlier_same_episode_value() {
        let preference = |value: &str, quote: &str| {
            json!({
                "operation": "create", "namespace": "breakfast_preferences", "item_key": "suggestion",
                "target_unit_ids": [], "preference_value": value, "memory_role": "personalization",
                "epistemic_use": "not_factual_evidence", "applicability_scope": "my breakfast suggestion",
                "evidence_quote": quote, "valid_from": null, "valid_to": null
            })
        };
        let old = "For my breakfast suggestion, I prefer sweet breakfasts.";
        let current =
            "Update: For my breakfast suggestion, I now prefer savory breakfasts instead.";
        let decoded = decode_response(
            success(json!({"state_operations": [], "preference_operations": [
                preference("sweet breakfasts", old), preference("savory breakfasts", current)
            ], "quantity_events": []}))
            .body,
            DEFAULT_MODEL,
            &format!("user: {old}\nuser: {current}"),
        )
        .unwrap();
        assert_eq!(decoded.operations.len(), 1);
        assert_eq!(
            decoded.operations[0].fields["value"],
            json!("savory breakfasts")
        );
        assert!(decoded.rejected.is_empty());
    }

    #[test]
    fn single_preference_operation_completes_to_unique_later_same_scope_quote() {
        let old = "For my reading recommendation, I prefer audiobooks.";
        let current = "Update: For my reading recommendation, I now prefer paper books instead.";
        let operation = |quote: &str, value: &str| {
            json!({
                "operation": "create", "namespace": "user_preferences", "item_key": "preference_1",
                "target_unit_ids": [], "preference_value": value, "memory_role": "personalization",
                "epistemic_use": "not_factual_evidence", "applicability_scope": "my reading recommendation",
                "evidence_quote": quote, "valid_from": null, "valid_to": null
            })
        };
        for (quote, value) in [(old, "audiobooks"), (current, "paper books")] {
            let decoded = decode_response(
                success(json!({"state_operations": [], "preference_operations": [
                    operation(quote, value)
                ], "quantity_events": []}))
                .body,
                DEFAULT_MODEL,
                &format!("user: {old}\nassistant: noted\nuser: {current}"),
            )
            .unwrap();
            assert_eq!(decoded.operations.len(), 1);
            assert_eq!(decoded.operations[0].evidence_quote, current);
            assert_eq!(decoded.operations[0].fields["value"], json!("paper books"));
            assert!(decoded.rejected.is_empty());
        }
    }

    #[test]
    fn exact_duplicate_earlier_preferences_complete_after_coalescing() {
        let old = "For my reading recommendation, I prefer audiobooks.";
        let current = "Update: For my reading recommendation, I now prefer paper books instead.";
        let operation = json!({
            "operation": "create", "namespace": "user_preferences", "item_key": "preference_1",
            "target_unit_ids": [], "preference_value": "audiobooks", "memory_role": "personalization",
            "epistemic_use": "not_factual_evidence", "applicability_scope": "my reading recommendation",
            "evidence_quote": old, "valid_from": null, "valid_to": null
        });
        let decoded = decode_response(
            success(json!({"state_operations": [], "preference_operations": [
                operation.clone(), operation
            ], "quantity_events": []}))
            .body,
            DEFAULT_MODEL,
            &format!("user: {old}\nassistant: noted\nuser: {current}"),
        )
        .unwrap();
        assert_eq!(decoded.operations.len(), 1);
        assert_eq!(decoded.operations[0].evidence_quote, current);
        assert_eq!(decoded.operations[0].fields["value"], json!("paper books"));
        assert!(decoded.rejected.is_empty());
    }

    #[test]
    fn rejected_state_response_never_runs_preference_completion() {
        let old = "For my reading recommendation, I prefer audiobooks.";
        let current = "Update: For my reading recommendation, I now prefer paper books instead.";
        let preference = json!({
            "operation": "create", "namespace": "user_preferences", "item_key": "preference_1",
            "target_unit_ids": [], "preference_value": "audiobooks", "memory_role": "personalization",
            "epistemic_use": "not_factual_evidence", "applicability_scope": "my reading recommendation",
            "evidence_quote": old, "valid_from": null, "valid_to": null
        });
        let collision = json!({
            "operation": "create", "namespace": "invalid", "item_key": "invalid",
            "target_unit_ids": [],
            "fields": [{"key": "item_key", "value_type": "string", "value": "forbidden"}],
            "evidence_quote": old, "valid_from": null, "valid_to": null
        });
        let decoded = decode_response(
            success(
                json!({"state_operations": [collision], "preference_operations": [
                preference
            ], "quantity_events": []}),
            )
            .body,
            DEFAULT_MODEL,
            &format!("user: {old}\nassistant: noted\nuser: {current}"),
        )
        .unwrap();
        assert_eq!(decoded.operations.len(), 1);
        assert_eq!(decoded.operations[0].evidence_quote, old);
        assert_eq!(decoded.operations[0].fields["value"], json!("audiobooks"));
        assert_eq!(decoded.rejected.get("field_shape"), Some(&1));
    }

    #[test]
    fn preference_inventory_selects_latest_per_scope_without_cross_scope_confusion() {
        let old = "For my reading recommendation, I prefer audiobooks.";
        let different_scope = "Update: For my commute plan, I now prefer cycling instead.";
        let third_same_scope =
            "Update: For my reading recommendation, I now prefer ebooks instead.";
        let operation = json!({
            "operation": "create", "namespace": "user_preferences", "item_key": "preference_1",
            "target_unit_ids": [], "preference_value": "audiobooks", "memory_role": "personalization",
            "epistemic_use": "not_factual_evidence", "applicability_scope": "my reading recommendation",
            "evidence_quote": old, "valid_from": null, "valid_to": null
        });
        let decoded = decode_response(
            success(json!({"state_operations": [], "preference_operations": [
                operation.clone()
            ], "quantity_events": []}))
            .body,
            DEFAULT_MODEL,
            &format!("user: {old}\nuser: {different_scope}"),
        )
        .unwrap();
        assert_eq!(decoded.operations.len(), 2);
        assert_eq!(
            decoded
                .operations
                .iter()
                .map(|operation| operation.fields["value"].as_str().unwrap())
                .collect::<BTreeSet<_>>(),
            BTreeSet::from(["audiobooks", "cycling"])
        );
        assert!(decoded.rejected.is_empty());

        let decoded = decode_response(
            success(json!({"state_operations": [], "preference_operations": [
                operation
            ], "quantity_events": []}))
            .body,
            DEFAULT_MODEL,
            &format!(
                "user: {old}\nuser: Update: For my reading recommendation, I now prefer paper books instead.\nuser: {third_same_scope}"
            ),
        )
        .unwrap();
        assert_eq!(decoded.operations.len(), 1);
        assert_eq!(decoded.operations[0].evidence_quote, third_same_scope);
        assert_eq!(decoded.operations[0].fields["value"], json!("ebooks"));
        assert!(decoded.rejected.is_empty());
    }

    #[test]
    fn ledger_proves_mixed_semantic_decode_without_episode_content() {
        let path = std::env::temp_dir().join(format!(
            "memphant-structured-decode-{}.jsonl",
            uuid::Uuid::new_v4()
        ));
        let transport = FakeTransport::new(vec![Ok(HttpResponse {
            status: 200,
            body: json!({
                "id": "gen-mixed", "model": DEFAULT_MODEL, "provider": "OpenAI",
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost": 0.001},
                "choices": [{"message": {"content": json!({
                    "quantity_events": [{
                        "value": "8e3", "measure": "steps", "unit": "steps",
                        "occurred_at": "2025-06-01T00:00:00Z", "dimensions": [],
                        "evidence_quote": "bad"
                    }],
                    "state_operations": [
                    {
                        "operation": "create", "namespace": "profile", "item_key": "city", "target_unit_ids": [],
                        "fields": [{"key": "value", "value_type": "string", "value": "Oslo"}],
                        "evidence_quote": "My city is Oslo.",
                        "valid_from": null, "valid_to": null
                    }
                ]}).to_string()}}]
            }),
            retry_after: None,
        })]);
        let provider = OpenRouterStructuredState::new(
            DEFAULT_MODEL.to_string(),
            prompt_fixture(),
            transport,
            Duration::ZERO,
            Some(path.clone()),
        );
        let error = provider
            .extract_sync(&StructuredStateRequest {
                episode_id: EpisodeId::from_u128(1),
                episode_body: "user: My city is Oslo.".to_string(),
                active_items: vec![],
            })
            .unwrap_err();
        assert!(error.to_string().contains("rejected operations"));
        let lines = std::fs::read_to_string(&path).unwrap();
        std::fs::remove_file(path).unwrap();
        let decode: Value = serde_json::from_str(lines.lines().last().unwrap()).unwrap();
        assert_eq!(decode["event"], "decode");
        assert_eq!(decode["accepted_op_count"], 1);
        assert_eq!(decode["rejected_op_count"], 1);
        assert_eq!(decode["rejection_reasons"]["quantity_shape"], 1);
        assert!(!lines.contains("My city is Oslo"));
    }

    #[test]
    fn transformed_quantity_round_trips_through_core_rollup_parser() {
        let body = "[date 2025-06-01]\nuser: I walked 8432 steps.";
        let quote = "I walked 8432 steps.";
        let event = WireQuantityEvent {
            value: "8432".to_string(),
            measure: "steps".to_string(),
            unit: "steps".to_string(),
            occurred_at: "".to_string(),
            dimensions: vec![],
            evidence_quote: quote.to_string(),
        };
        let operation = transform_quantity(event, body).unwrap();
        let projected =
            memphant_core::project_structured_state(EpisodeId::from_u128(7), body, &[operation])
                .unwrap();
        let event = memphant_core::quantity_event_from_body(&projected[0].body).unwrap();
        assert_eq!(event.value, "8432");
        assert_eq!(event.occurred_at, "2025-06-01T00:00:00Z");
    }

    #[test]
    fn explicit_preference_quotes_bound_dedicated_operation_count() {
        let provider = provider(FakeTransport::new(vec![]));
        let one = provider.request(&request("user: I prefer tea."));
        assert_eq!(
            one["response_format"]["json_schema"]["schema"]["properties"]["preference_operations"]
                ["maxItems"],
            1
        );

        let two = provider.request(&request(
            "user: I prefer tea.\nassistant: noted\nuser: I dislike coffee.",
        ));
        assert_eq!(
            two["response_format"]["json_schema"]["schema"]["properties"]["preference_operations"]
                ["maxItems"],
            2
        );

        let delete_only = provider.request(&request("user: Forget my saved preference."));
        assert!(
            delete_only["response_format"]["json_schema"]["schema"]["properties"]
                ["preference_operations"]
                .get("maxItems")
                .is_none()
        );
    }

    #[test]
    fn retryable_http_failure_is_retried_with_attributed_attempts() {
        let path = std::env::temp_dir().join(format!(
            "memphant-structured-retry-{}.jsonl",
            uuid::Uuid::new_v4()
        ));
        let transport = FakeTransport::new(vec![
            Ok(HttpResponse {
                status: 429,
                body: json!({"error": {"message": "rate limited"}}),
                retry_after: Some(Duration::ZERO),
            }),
            Ok(success(
                json!({"state_operations": [], "quantity_events": []}),
            )),
        ]);
        let concrete = transport.clone();
        let provider = OpenRouterStructuredState::new(
            DEFAULT_MODEL.to_string(),
            prompt_fixture(),
            transport,
            Duration::ZERO,
            Some(path.clone()),
        );

        provider.extract_sync(&request("user: hello")).unwrap();

        assert_eq!(*concrete.calls.lock().unwrap(), 2);
        let lines = fs::read_to_string(&path).unwrap();
        fs::remove_file(path).unwrap();
        let events = lines
            .lines()
            .map(|line| serde_json::from_str::<Value>(line).unwrap())
            .collect::<Vec<_>>();
        assert_eq!(events.len(), 5);
        assert_eq!(events[0]["event"], "started");
        assert_eq!(events[0]["attempt"], 1);
        assert_eq!(events[1]["event"], "result");
        assert_eq!(events[1]["http_status"], 429);
        assert_eq!(events[1]["retry_after_seconds"], 0);
        assert_eq!(events[2]["event"], "started");
        assert_eq!(events[2]["attempt"], 2);
        assert_eq!(events[2]["retry_index"], 1);
        assert_eq!(events[3]["event"], "result");
        assert_eq!(events[4]["event"], "decode");
        assert_ne!(events[0]["attempt_id"], events[2]["attempt_id"]);
    }

    #[test]
    fn terminal_http_failures_are_never_resent() {
        let transport = FakeTransport::new(vec![Ok(HttpResponse {
            status: 400,
            body: json!({"error": {"message": "response schema rejected"}}),
            retry_after: None,
        })]);
        let concrete = transport.clone();
        let error = provider(transport)
            .extract_sync(&StructuredStateRequest {
                episode_id: EpisodeId::from_u128(1),
                episode_body: "user: hello".to_string(),
                active_items: vec![],
            })
            .unwrap_err();
        assert!(error.to_string().contains("response schema rejected"));
        assert_eq!(*concrete.calls.lock().unwrap(), 1);
    }

    #[test]
    fn zero_content_and_nonrepairable_semantic_output_are_never_resent() {
        let transport = FakeTransport::new(vec![Ok(HttpResponse {
            status: 200,
            body: json!({
                "id": "gen-empty", "model": DEFAULT_MODEL, "provider": "OpenAI",
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost": 0},
                "choices": [{"message": {"content": ""}}]
            }),
            retry_after: None,
        })]);
        let concrete = transport.clone();
        provider(transport)
            .extract_sync(&StructuredStateRequest {
                episode_id: EpisodeId::from_u128(1),
                episode_body: "user: hello".to_string(),
                active_items: vec![],
            })
            .unwrap_err();
        assert_eq!(*concrete.calls.lock().unwrap(), 1);

        let transport = FakeTransport::new(vec![Ok(success(json!({
            "state_operations": [],
            "quantity_events": [{
                "value": "8.4e3", "measure": "steps", "unit": "steps",
                "occurred_at": "2025-06-01T00:00:00Z", "dimensions": [],
                "evidence_quote": "first"
            }]
        })))]);
        let concrete = transport.clone();
        assert!(
            provider(transport)
                .extract_sync(&StructuredStateRequest {
                    episode_id: EpisodeId::from_u128(1),
                    episode_body: "user: first second".to_string(),
                    active_items: vec![],
                })
                .is_err()
        );
        assert_eq!(*concrete.calls.lock().unwrap(), 1);
    }

    #[test]
    fn repairable_evidence_grounding_output_is_not_resent() {
        let operation = |quote: &str| {
            json!({
                "operation": "create", "namespace": "profile", "item_key": "city", "target_unit_ids": [],
                "fields": [{"key": "value", "value_type": "string", "value": "Oslo"}],
                "evidence_quote": quote, "valid_from": null, "valid_to": null
            })
        };
        let transport = FakeTransport::new(vec![Ok(success(json!({
            "state_operations": [operation("I live in Oslo")],
            "quantity_events": []
        })))]);
        let concrete = transport.clone();
        let error = provider(transport)
            .extract_sync(&StructuredStateRequest {
                episode_id: EpisodeId::from_u128(1),
                episode_body: "user: My city is Oslo.".to_string(),
                active_items: vec![],
            })
            .unwrap_err();
        assert!(error.to_string().contains("rejected operations"));
        assert_eq!(*concrete.calls.lock().unwrap(), 1);
    }

    #[test]
    fn duplicate_state_identity_is_not_resent() {
        let operation = |item: &str, field: &str, quote: &str| {
            json!({
                "operation": "create", "namespace": "social_post", "item_key": item,
                "target_unit_ids": [],
                "fields": [{"key": field, "value_type": "string", "value": quote}],
                "evidence_quote": quote, "valid_from": null, "valid_to": null
            })
        };
        let transport = FakeTransport::new(vec![Ok(success(json!({
            "state_operations": [
                operation("linkedin", "message", "technical achievement"),
                operation("linkedin", "goal", "attract new talent")
            ],
            "quantity_events": []
        })))]);
        let concrete = transport.clone();
        let error = provider(transport)
            .extract_sync(&StructuredStateRequest {
                episode_id: EpisodeId::from_u128(1),
                episode_body: "user: technical achievement\nuser: attract new talent".to_string(),
                active_items: vec![],
            })
            .unwrap_err();
        assert!(error.to_string().contains("rejected operations"));
        assert_eq!(*concrete.calls.lock().unwrap(), 1);
    }

    #[test]
    fn attempt_ledger_pairs_every_transport_call_with_nonsecret_cost_proof() {
        let path = std::env::temp_dir().join(format!(
            "memphant-structured-attempts-{}.jsonl",
            uuid::Uuid::new_v4()
        ));
        let transport = FakeTransport::with_generations(
            vec![Ok(HttpResponse {
                status: 200,
                body: json!({
                    "id": "gen-123",
                    "model": DEFAULT_MODEL,
                    "choices": [{"message": {"content": "{\"state_operations\":[],\"quantity_events\":[]}"}}]
                }),
                retry_after: None,
            })],
            vec![Ok(json!({"data": {
                "id": "gen-123",
                "model": format!("{DEFAULT_MODEL}-20260709"),
                "provider_name": "OpenAI",
                "tokens_prompt": 11,
                "tokens_completion": 3,
                "total_cost": 0.0042
            }}))],
        );
        let concrete = transport.clone();
        let provider = OpenRouterStructuredState::new(
            DEFAULT_MODEL.to_string(),
            prompt_fixture(),
            transport,
            Duration::ZERO,
            Some(path.clone()),
        );
        provider
            .extract_sync(&StructuredStateRequest {
                episode_id: EpisodeId::from_u128(9),
                episode_body: "user: hello".to_string(),
                active_items: vec![],
            })
            .unwrap();

        let lines = std::fs::read_to_string(&path).unwrap();
        let events = lines
            .lines()
            .map(|line| serde_json::from_str::<Value>(line).unwrap())
            .collect::<Vec<_>>();
        std::fs::remove_file(path).unwrap();
        assert_eq!(events.len(), 3);
        assert_eq!(events[0]["event"], "started");
        assert_eq!(events[0]["schema_version"], 2);
        assert_eq!(events[0]["retry_index"], 0);
        assert_eq!(events[0]["max_attempts"], 3);
        assert_eq!(events[1]["response_id"], "gen-123");
        assert_eq!(
            events[1]["served_model"],
            format!("{DEFAULT_MODEL}-20260709")
        );
        assert_eq!(events[1]["provider"], "OpenAI");
        assert_eq!(events[1]["usage"]["cost"], 0.0042);
        assert_eq!(events[1]["parse_status"], "generation_stats_reconciled");
        assert_eq!(events[2]["event"], "decode");
        assert_eq!(events[2]["accepted_op_count"], 0);
        assert_eq!(events[2]["rejected_op_count"], 0);
        assert_eq!(events[2]["rejection_reasons"], json!({}));
        assert_eq!(*concrete.calls.lock().unwrap(), 1);
        assert_eq!(*concrete.generation_calls.lock().unwrap(), 1);
        for event in &events {
            assert_eq!(event["retry_index"], 0);
            assert_eq!(event["request_sha256"].as_str().unwrap().len(), 64);
        }
        assert_eq!(events[1]["result_sha256"].as_str().unwrap().len(), 64);
        assert!(!lines.contains("user: hello"));
        assert_eq!(events[0]["attempt_id"], events[1]["attempt_id"]);
        assert_eq!(events[1]["attempt_id"], events[2]["attempt_id"]);
    }

    #[test]
    fn generation_reconciliation_failure_is_terminal_without_paid_resend() {
        let path = std::env::temp_dir().join(format!(
            "memphant-structured-reconcile-failure-{}.jsonl",
            uuid::Uuid::new_v4()
        ));
        let transport = FakeTransport::with_generations(
            vec![Ok(HttpResponse {
                status: 200,
                body: json!({
                    "id": "gen-paid",
                    "model": DEFAULT_MODEL,
                    "provider": "OpenAI",
                    "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6, "cost": 0.002},
                    "choices": [{"message": {"content": "{\"state_operations\":[],\"quantity_events\":[]}"}}]
                }),
                retry_after: None,
            })],
            vec![Err("secret generation error".to_string())],
        );
        let concrete = transport.clone();
        let provider = OpenRouterStructuredState::new(
            DEFAULT_MODEL.to_string(),
            prompt_fixture(),
            transport,
            Duration::ZERO,
            Some(path.clone()),
        );
        let error = provider.extract_sync(&request("user: hello")).unwrap_err();
        assert!(error.to_string().contains("generation"));
        assert_eq!(*concrete.calls.lock().unwrap(), 1);
        assert_eq!(*concrete.generation_calls.lock().unwrap(), 1);
        let lines = fs::read_to_string(&path).unwrap();
        fs::remove_file(path).unwrap();
        let events = lines
            .lines()
            .map(|line| serde_json::from_str::<Value>(line).unwrap())
            .collect::<Vec<_>>();
        assert_eq!(events.len(), 2);
        assert_eq!(events[1]["event"], "result");
        assert_eq!(events[1]["response_id"], "gen-paid");
        assert_eq!(events[1]["error"], "generation_stats_lookup_failed");
        assert_eq!(events[1]["parse_status"], "generation_stats_lookup_failed");
        assert!(!lines.contains("secret generation error"));
    }

    #[test]
    fn generation_header_recovers_a_missing_body_response_id() {
        let mut body = json!({"model": DEFAULT_MODEL, "choices": []});
        backfill_response_id(&mut body, Some("gen-from-header"));
        assert_eq!(body["id"], "gen-from-header");

        body["id"] = json!("gen-from-body");
        backfill_response_id(&mut body, Some("gen-different-header"));
        assert_eq!(body["id"], "gen-from-body");
    }

    #[test]
    fn first_transport_failure_is_terminal_without_hidden_retry() {
        let path = std::env::temp_dir().join(format!(
            "memphant-structured-transport-failure-{}.jsonl",
            uuid::Uuid::new_v4()
        ));
        let transport = FakeTransport::new(vec![Err("secret transport detail".to_string())]);
        let concrete = transport.clone();
        let provider = OpenRouterStructuredState::new(
            DEFAULT_MODEL.to_string(),
            prompt_fixture(),
            transport,
            Duration::ZERO,
            Some(path.clone()),
        );
        provider.extract_sync(&request("user: hello")).unwrap_err();
        assert_eq!(*concrete.calls.lock().unwrap(), 1);
        assert_eq!(*concrete.generation_calls.lock().unwrap(), 0);
        let lines = fs::read_to_string(&path).unwrap();
        fs::remove_file(path).unwrap();
        let events = lines
            .lines()
            .map(|line| serde_json::from_str::<Value>(line).unwrap())
            .collect::<Vec<_>>();
        assert_eq!(events.len(), 2);
        assert_eq!(events[1]["error"], "transport_error");
        assert_eq!(events[1]["retry_index"], 0);
        assert!(!lines.contains("secret transport detail"));
    }

    #[test]
    fn http_error_ledger_keeps_typed_attribution_without_raw_content() {
        let path = std::env::temp_dir().join(format!(
            "memphant-structured-http-failure-{}.jsonl",
            uuid::Uuid::new_v4()
        ));
        let transport = FakeTransport::new(vec![Ok(HttpResponse {
            status: 400,
            body: json!({
                "error": {
                    "message": "secret provider detail",
                    "metadata": {
                        "error_type": "invalid_request",
                        "provider_code": "invalid_schema"
                    }
                },
                "openrouter_metadata": {
                    "endpoints": {
                        "available": [{"provider": "Alibaba", "selected": true}]
                    }
                }
            }),
            retry_after: None,
        })]);
        let provider = OpenRouterStructuredState::new(
            DEFAULT_MODEL.to_string(),
            prompt_fixture(),
            transport,
            Duration::ZERO,
            Some(path.clone()),
        );
        provider.extract_sync(&request("user: hello")).unwrap_err();
        let lines = fs::read_to_string(&path).unwrap();
        fs::remove_file(path).unwrap();
        let event: Value = serde_json::from_str(lines.lines().last().unwrap()).unwrap();
        assert_eq!(event["provider"], "Alibaba");
        assert_eq!(event["error_type"], "invalid_request");
        assert_eq!(event["provider_code"], "invalid_schema");
        assert!(!lines.contains("secret provider detail"));
    }

    #[test]
    fn openrouter_requests_enable_provider_metadata_and_attribution() {
        assert_eq!(
            attribution_headers(),
            [
                ("http-referer", "https://github.com/memphant"),
                ("x-title", "memphant-structured-state"),
                ("x-openrouter-metadata", "enabled"),
            ]
        );
    }

    #[tokio::test(flavor = "current_thread")]
    async fn blocking_http_does_not_stall_the_async_executor() {
        let provider = provider(Arc::new(SlowTransport));
        let extraction = tokio::spawn(async move {
            provider
                .extract(&StructuredStateRequest {
                    episode_id: EpisodeId::from_u128(1),
                    episode_body: "user: hello".to_string(),
                    active_items: vec![],
                })
                .await
        });
        let started = std::time::Instant::now();
        tokio::time::sleep(Duration::from_millis(10)).await;
        assert!(
            started.elapsed() < Duration::from_millis(75),
            "blocking provider call stalled the single-thread executor"
        );
        extraction.await.unwrap().unwrap();
    }

    #[test]
    fn malformed_content_fails_closed() {
        let error = decode_response(
            json!({"model": DEFAULT_MODEL, "choices": [{"message": {"content": "not json"}}]}),
            DEFAULT_MODEL,
            "user: hello",
        )
        .unwrap_err();
        assert!(matches!(
            error,
            StructuredStateProviderError::InvalidOutput(_)
        ));
        let error = decode_response(
            json!({"model": "other/model", "choices": [{"message": {"content": "{\"state_operations\":[],\"quantity_events\":[]}"}}]}),
            DEFAULT_MODEL,
            "user: hello",
        )
        .unwrap_err();
        assert!(error.to_string().contains("served model"));
    }

    #[test]
    fn malformed_paid_response_records_failed_decode() {
        let path = std::env::temp_dir().join(format!(
            "memphant-structured-malformed-{}.jsonl",
            uuid::Uuid::new_v4()
        ));
        let transport = FakeTransport::new(vec![Ok(HttpResponse {
            status: 200,
            body: json!({
                "id": "gen-bad", "model": DEFAULT_MODEL, "provider": "OpenAI",
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost": 0.001},
                "choices": [{"message": {"content": "not json"}}]
            }),
            retry_after: None,
        })]);
        let provider = OpenRouterStructuredState::new(
            DEFAULT_MODEL.to_string(),
            prompt_fixture(),
            transport,
            Duration::ZERO,
            Some(path.clone()),
        );
        let error = provider
            .extract_sync(&StructuredStateRequest {
                episode_id: EpisodeId::from_u128(1),
                episode_body: "user: hello".to_string(),
                active_items: vec![],
            })
            .unwrap_err();
        assert!(matches!(
            error,
            StructuredStateProviderError::InvalidOutput(_)
        ));
        let lines = std::fs::read_to_string(&path).unwrap();
        std::fs::remove_file(path).unwrap();
        let decode: Value = serde_json::from_str(lines.lines().last().unwrap()).unwrap();
        assert_eq!(decode["event"], "decode");
        assert_eq!(decode["error"], "response_decode_error");
        assert_eq!(decode["accepted_op_count"], 0);
        assert!(!lines.contains("not json"));
    }

    #[test]
    fn env_config_is_explicit_and_defaults_the_model() {
        fn config<'a>(
            flag: Option<&str>,
            key: Option<&'a str>,
            model: Option<&'a str>,
        ) -> Result<Option<(&'a str, &'a str)>, String> {
            if flag != Some("on") {
                return Ok(None);
            }
            Ok(Some((
                key.filter(|value| !value.trim().is_empty())
                    .ok_or("OPENROUTER_API_KEY is required")?,
                model
                    .filter(|value| !value.trim().is_empty())
                    .unwrap_or(DEFAULT_MODEL),
            )))
        }
        assert_eq!(config(None, None, None).unwrap(), None);
        assert_eq!(config(Some("ON"), None, None).unwrap(), None);
        assert!(config(Some("on"), None, None).is_err());
        assert_eq!(
            config(Some("on"), Some("secret"), None).unwrap(),
            Some(("secret", DEFAULT_MODEL))
        );
        assert_eq!(
            config(Some("on"), Some("secret"), Some("vendor/model")).unwrap(),
            Some(("secret", "vendor/model"))
        );
    }
}
