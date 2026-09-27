//! CLI memory verbs contract (Task 8): the `memphant` binary drives the real
//! axum app (in-process, in-memory store, dev-mode tenant binding) over HTTP:
//! retain → reflect → recall returns the body; forget → recall is empty.

use std::process::Command;

use memphant_core::MemoryStore;
use memphant_server::AppState;
use memphant_types::{
    ContextBindingAgentRef, ContextBindingEntityRef, ContextBindingRequest, ContextBindingScopeRef,
    TenantId,
};
use serde_json::Value;
use std::io::{Read, Write};
use std::net::TcpListener;
use std::sync::Arc;
use std::sync::Mutex;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::{Duration, Instant};

const TENANT: &str = "00000000-0000-0000-0000-00000000c11a";

async fn spawn_server() -> (
    String,
    memphant_types::ContextBindingResponse,
    AppState<memphant_core::InMemoryStore>,
) {
    let tenant = TenantId::from_u128(uuid::Uuid::parse_str(TENANT).unwrap().as_u128());
    let state = AppState::new_in_memory().with_dev_tenant(tenant);
    let binding = state
        .store()
        .resolve_context_binding(
            tenant,
            "cli-contract".to_string(),
            ContextBindingRequest {
                subject: ContextBindingEntityRef {
                    external_ref: "cli-user".to_string(),
                    kind: "user".to_string(),
                },
                actor: ContextBindingEntityRef {
                    external_ref: "cli-user".to_string(),
                    kind: "user".to_string(),
                },
                scope: ContextBindingScopeRef {
                    external_ref: "cli-root".to_string(),
                    kind: "user_root".to_string(),
                    parent_external_ref: None,
                },
                agent_node: ContextBindingAgentRef {
                    external_ref: "cli-l0".to_string(),
                    parent_external_ref: None,
                },
                access_policies: Vec::new(),
            },
        )
        .await
        .expect("bind CLI context");
    let app = memphant_server::app(state.clone());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind ephemeral port");
    let addr = listener.local_addr().expect("local addr");
    tokio::spawn(async move {
        axum::serve(listener, app).await.expect("server runs");
    });
    (format!("http://{addr}"), binding, state)
}

fn cli(url: &str, args: &[&str]) -> (Value, bool) {
    let output = Command::new(env!("CARGO_BIN_EXE_memphant-cli"))
        .args(args)
        .env("MEMPHANT_URL", url)
        .env_remove("MEMPHANT_API_KEY")
        .output()
        .expect("cli runs");
    let stdout = String::from_utf8_lossy(&output.stdout);
    let value: Value = serde_json::from_str(stdout.trim()).unwrap_or_else(|error| {
        panic!(
            "cli {args:?} must print JSON, got error {error}\nstdout: {stdout}\nstderr: {}",
            String::from_utf8_lossy(&output.stderr)
        )
    });
    (value, output.status.success())
}

fn scripted_openrouter() -> (String, Arc<AtomicUsize>, std::thread::JoinHandle<()>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    listener.set_nonblocking(true).unwrap();
    let address = listener.local_addr().unwrap();
    let calls = Arc::new(AtomicUsize::new(0));
    let observed_calls = calls.clone();
    let server = std::thread::spawn(move || {
        let deadline = Instant::now() + Duration::from_secs(3);
        for turn in 1..=2 {
            let (mut socket, _) = loop {
                match listener.accept() {
                    Ok(connection) => break connection,
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                        if Instant::now() >= deadline {
                            return;
                        }
                        std::thread::sleep(Duration::from_millis(5));
                    }
                    Err(error) => panic!("provider accept failed: {error}"),
                }
            };
            socket.set_nonblocking(false).unwrap();
            socket
                .set_read_timeout(Some(Duration::from_secs(2)))
                .unwrap();
            let mut request = Vec::new();
            let mut buffer = [0u8; 8192];
            loop {
                let read = socket.read(&mut buffer).unwrap();
                request.extend_from_slice(&buffer[..read]);
                let Some(header_end) = request.windows(4).position(|window| window == b"\r\n\r\n")
                else {
                    continue;
                };
                let headers = String::from_utf8_lossy(&request[..header_end + 4]);
                let length = headers
                    .lines()
                    .find_map(|line| {
                        line.to_ascii_lowercase()
                            .strip_prefix("content-length:")
                            .map(str::trim)
                            .and_then(|value| value.parse::<usize>().ok())
                    })
                    .unwrap();
                if request.len() >= header_end + 4 + length {
                    break;
                }
            }
            observed_calls.fetch_add(1, Ordering::SeqCst);
            let header_end = request
                .windows(4)
                .position(|window| window == b"\r\n\r\n")
                .unwrap();
            let body: Value = serde_json::from_slice(&request[header_end + 4..]).unwrap();
            assert_eq!(body["provider"]["only"], serde_json::json!(["azure"]));
            let (name, arguments) = if turn == 1 {
                ("list_files", "{\"prefix\":\"episodes/\"}".to_string())
            } else {
                let tool_content = body["messages"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .rev()
                    .find(|message| message["role"] == "tool")
                    .unwrap()["content"]
                    .as_str()
                    .unwrap();
                let listed: Value = serde_json::from_str(tool_content).unwrap();
                let path = listed["files"][0]["path"].as_str().unwrap();
                let source_id = path.trim_start_matches("episodes/").trim_end_matches(".md");
                (
                    "finish",
                    format!(
                        "{{\"source_ids\":[\"{source_id}\"],\"evidence_status\":\"insufficient\",\"reason\":\"The episode has no canonical byte-span citation.\"}}"
                    ),
                )
            };
            let event = serde_json::json!({
                "model":"anthropic/claude-sonnet-5","provider":"Azure",
                "choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":format!("call-{turn}"),"function":{"name":name,"arguments":arguments}}]}}],
                "usage":{"prompt_tokens":10,"completion_tokens":1,"cost":0.00001}
            });
            let response_body = format!("data: {event}\n\ndata: [DONE]\n\n");
            write!(socket, "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nX-Generation-Id: gen-cli-{turn}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}", response_body.len(), response_body).unwrap();
        }
    });
    (format!("http://{address}/api/v1"), calls, server)
}

/// Run the CLI and return its raw output, with the identity env vars cleared
/// so a developer shell cannot leak into the assertions.
fn cli_raw(url: &str, args: &[&str], env: &[(&str, String)]) -> std::process::Output {
    let mut command = Command::new(env!("CARGO_BIN_EXE_memphant-cli"));
    command
        .args(args)
        .env("MEMPHANT_URL", url)
        .env_remove("MEMPHANT_API_KEY")
        .env_remove("MEMPHANT_CAPTURE_URL")
        .env_remove("MEMPHANT_SUBJECT_ID")
        .env_remove("MEMPHANT_SCOPE_ID")
        .env_remove("MEMPHANT_ACTOR_ID")
        .env_remove("MEMPHANT_AGENT_NODE_ID")
        .env_remove("MEMPHANT_SUBJECT_GENERATION");
    for (name, value) in env {
        command.env(name, value);
    }
    command.output().expect("cli runs")
}

/// A one-shot raw HTTP responder: captures the single request the CLI sends
/// and replies with `response` (a JSON body, HTTP 200). The join handle
/// delivers the raw request bytes — headers and body — so tests can assert
/// exactly what reached the wire, including the request target.
fn capture_one_request(response: &str) -> (String, std::thread::JoinHandle<Option<String>>) {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind ephemeral port");
    listener.set_nonblocking(true).unwrap();
    let address = listener.local_addr().unwrap();
    let response = response.to_string();
    let server = std::thread::spawn(move || {
        let deadline = Instant::now() + Duration::from_secs(3);
        let (mut socket, _) = loop {
            match listener.accept() {
                Ok(connection) => break connection,
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                    if Instant::now() >= deadline {
                        return None;
                    }
                    std::thread::sleep(Duration::from_millis(5));
                }
                Err(error) => panic!("accept failed: {error}"),
            }
        };
        socket.set_nonblocking(false).unwrap();
        socket
            .set_read_timeout(Some(Duration::from_secs(2)))
            .unwrap();
        let mut request = Vec::new();
        let mut buffer = [0u8; 8192];
        loop {
            let read = socket.read(&mut buffer).expect("read request");
            if read == 0 {
                break;
            }
            request.extend_from_slice(&buffer[..read]);
            let Some(header_end) = request.windows(4).position(|window| window == b"\r\n\r\n")
            else {
                continue;
            };
            let headers = String::from_utf8_lossy(&request[..header_end + 4]);
            let length = headers
                .lines()
                .find_map(|line| {
                    line.to_ascii_lowercase()
                        .strip_prefix("content-length:")
                        .map(str::trim)
                        .and_then(|value| value.parse::<usize>().ok())
                })
                .unwrap_or(0);
            if request.len() >= header_end + 4 + length {
                break;
            }
        }
        write!(
            socket,
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
            response.len(),
            response
        )
        .unwrap();
        Some(String::from_utf8_lossy(&request).into_owned())
    });
    (format!("http://{address}"), server)
}

/// A parse error must fail BEFORE the CLI dials the server: exit 2, the
/// existing `<verb>=error` line, and no request on the wire.
#[test]
fn unknown_flag_typo_exits_two_without_sending_a_request() {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind ephemeral port");
    listener.set_nonblocking(true).unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let output = cli_raw(&url, &["recall", "--limt", "5"], &[]);
    assert_eq!(output.status.code(), Some(2));
    assert!(
        output.stdout.is_empty(),
        "stdout: {}",
        String::from_utf8_lossy(&output.stdout)
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("recall=error"), "{stderr}");
    assert!(
        stderr.contains("unknown flag --limt for recall"),
        "{stderr}"
    );
    // The process already exited: nothing may be waiting on the listener.
    match listener.accept() {
        Ok(_) => panic!("the CLI sent a request despite the parse error"),
        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {}
        Err(error) => panic!("accept failed: {error}"),
    }
}

/// `--limit=5` is a value flag, not a flag literally named `limit=5`: the
/// parsed limit must reach the request body.
#[test]
fn recall_limit_equals_syntax_reaches_the_request_body() {
    let (url, server) = capture_one_request(r#"{"items":[],"trace_id":"t-limit"}"#);
    let output = cli_raw(
        &url,
        &[
            "recall",
            "--json",
            "--subject-id",
            "s",
            "--scope",
            "sc",
            "--actor",
            "a",
            "--agent-node",
            "an",
            "--subject-generation",
            "1",
            "--query",
            "encode me",
            "--limit=5",
        ],
        &[],
    );
    assert!(
        output.status.success(),
        "stdout: {} stderr: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let request = server.join().unwrap().expect("the CLI sent a request");
    let body = request
        .split_once("\r\n\r\n")
        .map(|(_, body)| body)
        .unwrap_or_default();
    let body: Value = serde_json::from_str(body).expect("recall body is JSON");
    assert_eq!(body["limit"], serde_json::json!(5));
    assert_eq!(body["query"], "encode me");
}

/// `trace` interpolates ids into a URL: a space or `&` in the trace id or in
/// MEMPHANT_SUBJECT_ID must arrive percent-encoded, not as a broken path or a
/// forged extra query parameter.
#[test]
fn trace_percent_encodes_the_path_segment_and_query_values() {
    let (url, server) = capture_one_request(r#"{"id":"my trace&id"}"#);
    let output = cli_raw(
        &url,
        &["trace", "my trace&id"],
        &[
            ("MEMPHANT_SUBJECT_ID", "sub ject&id".to_string()),
            ("MEMPHANT_SCOPE_ID", "scope-1".to_string()),
            ("MEMPHANT_ACTOR_ID", "actor-1".to_string()),
            ("MEMPHANT_AGENT_NODE_ID", "agent-1".to_string()),
            ("MEMPHANT_SUBJECT_GENERATION", "7".to_string()),
        ],
    );
    assert!(
        output.status.success(),
        "stdout: {} stderr: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let request = server.join().unwrap().expect("the CLI sent a request");
    let request_line = request.lines().next().expect("request line");
    assert_eq!(
        request_line,
        "GET /v1/traces/my%20trace%26id?subject_id=sub%20ject%26id&scope_id=scope-1&actor_id=actor-1&agent_node_id=agent-1&subject_generation=7 HTTP/1.1"
    );
}

/// Extra positionals must not be silently ignored: `trace <id> <junk>`
/// exits 2 before dialing, like every other verb.
#[test]
fn trace_rejects_extra_positional_arguments() {
    let (url, server) = capture_one_request(r#"{"id":"t"}"#);
    let output = cli_raw(
        &url,
        &["trace", "t-1", "t-2"],
        &[
            ("MEMPHANT_SUBJECT_ID", "s".to_string()),
            ("MEMPHANT_SCOPE_ID", "sc".to_string()),
            ("MEMPHANT_ACTOR_ID", "a".to_string()),
            ("MEMPHANT_AGENT_NODE_ID", "an".to_string()),
            ("MEMPHANT_SUBJECT_GENERATION", "1".to_string()),
        ],
    );
    assert_eq!(output.status.code(), Some(2));
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("trace=error"), "{stderr}");
    assert!(
        stderr.contains("unexpected positional arguments"),
        "{stderr}"
    );
    assert!(server.join().unwrap().is_none(), "no request may be sent");
}

static DEEP_ENV_LOCK: Mutex<()> = Mutex::new(());

struct ScopedEnv {
    saved: Vec<(&'static str, Option<String>)>,
}

impl ScopedEnv {
    fn set(variables: &[(&'static str, String)]) -> Self {
        let saved = variables
            .iter()
            .map(|(name, _)| (*name, std::env::var(name).ok()))
            .collect::<Vec<_>>();
        unsafe {
            for (name, value) in variables {
                std::env::set_var(name, value);
            }
        }
        Self { saved }
    }
}

impl Drop for ScopedEnv {
    fn drop(&mut self) {
        unsafe {
            for (name, value) in self.saved.drain(..) {
                match value {
                    Some(value) => std::env::set_var(name, value),
                    None => std::env::remove_var(name),
                }
            }
        }
    }
}

fn build_deep_service(
    store: memphant_core::InMemoryStore,
    base_url: &str,
) -> memphant_core::service::MemoryService<memphant_runtime::AnyStore> {
    let prompt = tempfile::NamedTempFile::new().unwrap();
    std::fs::write(prompt.path(), "Use tools only.").unwrap();
    let variables = [
        ("MEMPHANT_DEEP", "on".to_string()),
        ("OPENROUTER_API_KEY", "test-key".to_string()),
        (
            "MEMPHANT_DEEP_MODEL",
            "anthropic/claude-sonnet-5-20260630".to_string(),
        ),
        (
            "MEMPHANT_DEEP_RESPONSE_MODEL",
            "anthropic/claude-sonnet-5".to_string(),
        ),
        (
            "MEMPHANT_DEEP_PROMPT_PATH",
            prompt.path().display().to_string(),
        ),
        ("MEMPHANT_DEEP_PROVIDERS", "azure".to_string()),
        (
            "MEMPHANT_DEEP_INPUT_PRICE_MICROS_PER_MILLION",
            "2000000".to_string(),
        ),
        (
            "MEMPHANT_DEEP_OUTPUT_PRICE_MICROS_PER_MILLION",
            "10000000".to_string(),
        ),
        ("MEMPHANT_DEEP_OPENROUTER_BASE_URL", base_url.to_string()),
        ("MEMPHANT_EMBEDDINGS", "off".to_string()),
        // Rerank is on-by-default (server default), but this test scripts an
        // exact deep-provider interaction; pin it off so the snapshot fed to the
        // scripted provider stays deterministic (same reason embeddings are off).
        ("MEMPHANT_CROSS_RERANK", "off".to_string()),
    ];
    let _env_lock = DEEP_ENV_LOCK
        .lock()
        .unwrap_or_else(|error| error.into_inner());
    let _env = ScopedEnv::set(&variables);
    memphant_runtime::build_service(memphant_runtime::AnyStore::Mem(store))
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn explicit_deep_without_provider_surfaces_stable_error() {
    let (url, binding, _) = spawn_server().await;
    let subject = binding.subject_id.as_uuid().to_string();
    let scope = binding.scope_id.as_uuid().to_string();
    let actor = binding.actor_id.as_uuid().to_string();
    let agent = binding.agent_node_id.as_uuid().to_string();
    let generation = binding.subject_generation.to_string();

    let (body, ok) = cli(
        &url,
        &[
            "recall",
            "--json",
            "--subject-id",
            &subject,
            "--scope",
            &scope,
            "--actor",
            &actor,
            "--agent-node",
            &agent,
            "--subject-generation",
            &generation,
            "--query",
            "search deeply",
            "--mode",
            "deep",
        ],
    );

    assert!(!ok);
    assert_eq!(body["error"]["code"], "deep_unavailable");
    assert_eq!(body["error"]["message"], "deep recall is unavailable");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn packaged_cli_and_rest_deep_use_runtime_streaming_provider_only_when_explicit() {
    let (seed_url, binding, state) = spawn_server().await;
    let subject = binding.subject_id.as_uuid().to_string();
    let scope = binding.scope_id.as_uuid().to_string();
    let actor = binding.actor_id.as_uuid().to_string();
    let agent = binding.agent_node_id.as_uuid().to_string();
    let generation = binding.subject_generation.to_string();
    let retained = cli(
        &seed_url,
        &[
            "retain",
            "--subject-id",
            &subject,
            "--scope",
            &scope,
            "--actor",
            &actor,
            "--agent-node",
            &agent,
            "--subject-generation",
            &generation,
            "--idempotency-key",
            "deep-smoke-retain",
            "--source-ref",
            "deep:smoke",
            "--observed-at",
            "2026-07-20T00:00:00Z",
            "--body",
            "Buried archive says launch code is heliotrope.",
        ],
    );
    assert!(retained.1, "retain failed: {}", retained.0);
    let reflected = cli(
        &seed_url,
        &[
            "reflect",
            "--subject-id",
            &subject,
            "--scope",
            &scope,
            "--actor",
            &actor,
            "--agent-node",
            &agent,
            "--subject-generation",
            &generation,
            "--idempotency-key",
            "deep-smoke-reflect",
        ],
    );
    assert!(reflected.1, "reflect failed: {}", reflected.0);
    state.service().run_worker_tick(usize::MAX).await.unwrap();

    let (provider_url, provider_calls, provider_server) = scripted_openrouter();
    let service = build_deep_service(state.store().clone(), &provider_url);
    let deep_state = AppState::from_service(service, "memory").with_dev_tenant(
        TenantId::from_u128(uuid::Uuid::parse_str(TENANT).unwrap().as_u128()),
    );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    tokio::spawn(async move {
        axum::serve(listener, memphant_server::app(deep_state))
            .await
            .unwrap();
    });
    let url = format!("http://{address}");

    let response = cli(
        &url,
        &[
            "recall",
            "--json",
            "--subject-id",
            &subject,
            "--scope",
            &scope,
            "--actor",
            &actor,
            "--agent-node",
            &agent,
            "--subject-generation",
            &generation,
            "--query",
            "launch code",
            "--mode",
            "fast",
        ],
    );
    assert!(response.1, "fast failed: {}", response.0);
    assert_eq!(provider_calls.load(Ordering::SeqCst), 0);

    let (deep, ok) = cli(
        &url,
        &[
            "recall",
            "--json",
            "--subject-id",
            &subject,
            "--scope",
            &scope,
            "--actor",
            &actor,
            "--agent-node",
            &agent,
            "--subject-generation",
            &generation,
            "--query",
            "What is the buried launch code?",
            "--mode",
            "deep",
        ],
    );
    assert!(ok, "Deep failed: {deep}");
    assert_eq!(deep["deep"]["status"], "completed");
    assert_eq!(deep["deep"]["evidence"]["status"], "insufficient");
    assert_eq!(deep["abstention"], true);
    assert_eq!(
        deep["deep"]["generation_ids"],
        serde_json::json!(["gen-cli-1", "gen-cli-2"])
    );
    assert!(
        deep["items"][0]["body"]
            .as_str()
            .unwrap()
            .contains("heliotrope")
    );
    let trace_id = deep["trace_id"].as_str().unwrap();
    let (trace, ok) = cli(
        &url,
        &[
            "trace",
            trace_id,
            "--subject-id",
            &subject,
            "--scope",
            &scope,
            "--actor",
            &actor,
            "--agent-node",
            &agent,
            "--subject-generation",
            &generation,
        ],
    );
    assert!(ok, "trace failed: {trace}");
    assert_eq!(trace["l4_observed_provider"], "Azure");
    assert_eq!(trace["l4_observed_model"], "anthropic/claude-sonnet-5");
    assert_eq!(
        trace["deep"]["generation_ids"],
        deep["deep"]["generation_ids"]
    );
    provider_server.join().unwrap();
    assert_eq!(provider_calls.load(Ordering::SeqCst), 2);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn retain_reflect_recall_then_forget_round_trips_over_http() {
    let (url, binding, state) = spawn_server().await;
    let subject = binding.subject_id.as_uuid().to_string();
    let scope = binding.scope_id.as_uuid().to_string();
    let actor = binding.actor_id.as_uuid().to_string();
    let agent = binding.agent_node_id.as_uuid().to_string();
    let generation = binding.subject_generation.to_string();

    // retain (episode shape)
    let (retained, ok) = cli(
        &url,
        &[
            "retain",
            "--subject-id",
            &subject,
            "--scope",
            &scope,
            "--actor",
            &actor,
            "--agent-node",
            &agent,
            "--subject-generation",
            &generation,
            "--idempotency-key",
            "cli-retain-release-region",
            "--source-ref",
            "cli:test:release-region",
            "--observed-at",
            "2026-07-15T00:00:00Z",
            "--body",
            "Release region is Taipei.",
        ],
    );
    assert!(ok, "retain exits zero");
    let episode_id = retained["episode_id"]
        .as_str()
        .expect("retain prints episode_id")
        .to_string();

    // reflect
    let (reflected, ok) = cli(
        &url,
        &[
            "reflect",
            "--subject-id",
            &subject,
            "--scope",
            &scope,
            "--actor",
            &actor,
            "--agent-node",
            &agent,
            "--subject-generation",
            &generation,
            "--idempotency-key",
            "cli-reflect-release-region",
        ],
    );
    assert!(ok, "reflect exits zero");
    assert!(reflected["job_id"].is_string());
    state
        .service()
        .run_worker_tick(usize::MAX)
        .await
        .expect("worker processes retained episode and scope barrier");

    // recall returns the body
    let (recalled, ok) = cli(
        &url,
        &[
            "recall",
            "--json",
            "--subject-id",
            &subject,
            "--scope",
            &scope,
            "--actor",
            &actor,
            "--agent-node",
            &agent,
            "--subject-generation",
            &generation,
            "--query",
            "Where is the release region?",
        ],
    );
    assert!(ok, "recall exits zero");
    assert_eq!(
        recalled["items"][0]["body"].as_str(),
        Some("Release region is Taipei."),
        "recall returns the retained body: {recalled}"
    );

    // forget by episode id
    let (forgotten, ok) = cli(
        &url,
        &[
            "forget",
            "--subject-id",
            &subject,
            "--scope",
            &scope,
            "--actor",
            &actor,
            "--agent-node",
            &agent,
            "--subject-generation",
            &generation,
            "--idempotency-key",
            "cli-forget-episode",
            "--episode",
            &episode_id,
            "--reason",
            "cli-contract-test",
        ],
    );
    assert!(ok, "forget exits zero: {forgotten}");

    // recall is now empty
    let (recalled, ok) = cli(
        &url,
        &[
            "recall",
            "--json",
            "--subject-id",
            &subject,
            "--scope",
            &scope,
            "--actor",
            &actor,
            "--agent-node",
            &agent,
            "--subject-generation",
            &generation,
            "--query",
            "Where is the release region?",
        ],
    );
    assert!(ok, "recall exits zero after forget");
    assert_eq!(
        recalled["items"].as_array().map(Vec::len),
        Some(0),
        "forgotten memory never recalls: {recalled}"
    );
}

/// The `memphant recall` default-lane decision (docs/specs/cli-recall-default-lane.md):
/// a BARE `memphant recall` is the coding union lane — it serves the agent's own
/// freshly-captured, still-unconfirmed Candidate WITHOUT any flags, labelled
/// `captured_unconfirmed`. PERTURBATION: the same recall with `--general` opts
/// into the anti-poison general lane and must NOT surface the Candidate.
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn bare_recall_serves_captured_candidate_general_hides_it() {
    let (url, binding, state) = spawn_server().await;
    let subject = binding.subject_id.as_uuid().to_string();
    let scope = binding.scope_id.as_uuid().to_string();
    let actor = binding.actor_id.as_uuid().to_string();
    let agent = binding.agent_node_id.as_uuid().to_string();
    let generation = binding.subject_generation.to_string();

    // A cross-harness CAPTURE: an `agent` episode whose source_ref is
    // `capture://summary`. The reflect nominator mints ONE inert Semantic
    // Candidate carrying the capture marker.
    let (captured, ok) = cli(
        &url,
        &[
            "retain",
            "--subject-id",
            &subject,
            "--scope",
            &scope,
            "--actor",
            &actor,
            "--agent-node",
            &agent,
            "--subject-generation",
            &generation,
            "--idempotency-key",
            "cli-capture-magic-byte",
            "--source-kind",
            "agent",
            "--source-ref",
            "capture://summary",
            "--subject",
            "acme-magic-byte",
            "--observed-at",
            "2026-08-01T00:00:00Z",
            "--body",
            "The acme wire format magic byte is 0xA7.",
        ],
    );
    assert!(ok, "capture retain exits zero: {captured}");

    let (_reflected, ok) = cli(
        &url,
        &[
            "reflect",
            "--subject-id",
            &subject,
            "--scope",
            &scope,
            "--actor",
            &actor,
            "--agent-node",
            &agent,
            "--subject-generation",
            &generation,
            "--idempotency-key",
            "cli-capture-reflect",
        ],
    );
    assert!(ok, "capture reflect exits zero");
    state
        .service()
        .run_worker_tick(usize::MAX)
        .await
        .expect("worker mints the captured Candidate");

    // BARE recall (no lane flags) — the union lane serves the Candidate.
    let (bare, ok) = cli(
        &url,
        &[
            "recall",
            "--json",
            "--subject-id",
            &subject,
            "--scope",
            &scope,
            "--actor",
            &actor,
            "--agent-node",
            &agent,
            "--subject-generation",
            &generation,
            "--query",
            "acme magic byte",
        ],
    );
    assert!(ok, "bare recall exits zero: {bare}");
    let item = &bare["items"][0];
    assert_eq!(
        item["body"].as_str(),
        Some("The acme wire format magic byte is 0xA7."),
        "bare recall serves the captured Candidate: {bare}"
    );
    assert!(
        item["inclusion_reason"]
            .as_str()
            .unwrap_or_default()
            .contains("captured_unconfirmed"),
        "captured Candidate is labelled unconfirmed: {bare}"
    );

    // `--general` opts out: the anti-poison lane hides the Candidate.
    let (general, ok) = cli(
        &url,
        &[
            "recall",
            "--json",
            "--general",
            "--subject-id",
            &subject,
            "--scope",
            &scope,
            "--actor",
            &actor,
            "--agent-node",
            &agent,
            "--subject-generation",
            &generation,
            "--query",
            "acme magic byte",
        ],
    );
    assert!(ok, "general recall exits zero: {general}");
    assert_eq!(
        general["items"].as_array().map(Vec::len),
        Some(0),
        "general lane must hide the unconfirmed Candidate (anti-poison): {general}"
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn resource_retain_and_trace_round_trip_over_http() {
    let (url, binding, state) = spawn_server().await;
    let subject = binding.subject_id.as_uuid().to_string();
    let scope = binding.scope_id.as_uuid().to_string();
    let actor = binding.actor_id.as_uuid().to_string();
    let agent = binding.agent_node_id.as_uuid().to_string();
    let generation = binding.subject_generation.to_string();

    let mut body_file = std::env::temp_dir();
    body_file.push(format!("memphant-cli-test-{}.txt", uuid::Uuid::new_v4()));
    std::fs::write(&body_file, "fn main() { println!(\"release: taipei\"); }")
        .expect("write body file");

    let (retained, ok) = cli(
        &url,
        &[
            "retain",
            "--subject-id",
            &subject,
            "--scope",
            &scope,
            "--actor",
            &actor,
            "--agent-node",
            &agent,
            "--subject-generation",
            &generation,
            "--idempotency-key",
            "cli-retain-resource",
            "--source-ref",
            "cli:test:resource",
            "--observed-at",
            "2026-07-15T00:00:00Z",
            "--resource",
            "--uri",
            "repo://demo/src/main.rs",
            "--revision",
            "abc123",
            "--content-hash",
            "sha256:e05ca253b856f3ce50676893df80d1417d6ef34baf6d06d0ff27b40d2790b0c9",
            "--body-file",
            body_file.to_str().expect("utf-8 temp path"),
        ],
    );
    std::fs::remove_file(&body_file).ok();
    assert!(ok, "resource retain exits zero: {retained}");
    assert!(retained["resource_id"].is_string());
    assert_eq!(retained["enqueued"][0].as_str(), Some("reflect_resource"));

    cli(
        &url,
        &[
            "reflect",
            "--subject-id",
            &subject,
            "--scope",
            &scope,
            "--actor",
            &actor,
            "--agent-node",
            &agent,
            "--subject-generation",
            &generation,
            "--idempotency-key",
            "cli-reflect-resource",
        ],
    );
    state
        .service()
        .run_worker_tick(usize::MAX)
        .await
        .expect("worker processes retained resource and scope barrier");
    let (recalled, ok) = cli(
        &url,
        &[
            "recall",
            "--json",
            "--subject-id",
            &subject,
            "--scope",
            &scope,
            "--actor",
            &actor,
            "--agent-node",
            &agent,
            "--subject-generation",
            &generation,
            "--query",
            "release taipei",
        ],
    );
    assert!(ok, "resource recall exits zero: {recalled}");
    let trace_id = recalled["trace_id"].as_str().expect("trace id").to_string();

    let (trace, ok) = cli(
        &url,
        &[
            "trace",
            &trace_id,
            "--subject-id",
            &subject,
            "--scope",
            &scope,
            "--actor",
            &actor,
            "--agent-node",
            &agent,
            "--subject-generation",
            &generation,
        ],
    );
    assert!(ok, "trace exits zero");
    assert_eq!(trace["id"].as_str(), Some(trace_id.as_str()));
}
