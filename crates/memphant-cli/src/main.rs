use std::collections::BTreeMap;
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::str::FromStr;

use memphant_core::MemoryStore;
use memphant_store_postgres::{Provider, lint_migrations};
use memphant_types::{
    ActorId, AgentNodeId, MemphantLock, ScopeId, SubjectId, TenantId, VerifyReport,
};
mod file_plane;
mod structured_state_census;

const DEFAULT_PROVIDER_PROFILE_DIR: &str = "deploy/provider-profiles";
const PITR_RETENTION_MARGIN_DAYS: u64 = 1;

fn main() -> ExitCode {
    let args: Vec<String> = env::args().skip(1).collect();
    if args.first().is_some_and(|value| value == "admin")
        && args.get(1).is_some_and(|value| value == "create-key")
    {
        return admin_create_key(&args[2..]);
    }
    if args.first().is_some_and(|verb| verb == "compile") {
        return file_plane::run_compile(&args[1..]);
    }
    if args.first().is_some_and(|verb| verb == "sync") {
        return file_plane::run_sync(&args[1..]);
    }
    if args.first().is_some_and(|verb| verb == "structured-state") {
        return structured_state_census::run(&args[1..]);
    }
    if let Some(verb) = args.first().map(String::as_str)
        && matches!(
            verb,
            "retain" | "recall" | "reflect" | "correct" | "forget" | "mark" | "trace"
        )
    {
        return http_verbs::run(verb, &args[1..]);
    }
    match args.as_slice() {
        [lock, out_flag, out] if lock == "lock" && out_flag == "--out" => emit_lock(out),
        [verify, lock_flag, path] if verify == "verify" && lock_flag == "--lock" => {
            verify_lock(path, None)
        }
        [verify, lock_flag, path, export_flag, export_dir]
            if verify == "verify" && lock_flag == "--lock" && export_flag == "--export" =>
        {
            verify_lock(path, Some(Path::new(export_dir)))
        }
        [db, lint, provider_flag, provider]
            if db == "db" && lint == "lint" && provider_flag == "--provider" =>
        {
            match Provider::from_str(provider).and_then(lint_migrations) {
                Ok(()) => {
                    println!("db_lint=clean provider={provider}");
                    ExitCode::SUCCESS
                }
                Err(error) => {
                    eprintln!("db_lint=dirty provider={provider}");
                    eprintln!("{error}");
                    ExitCode::from(1)
                }
            }
        }
        [db, command, provider_flag, provider]
            if db == "db" && command == "bootstrap-check" && provider_flag == "--provider" =>
        {
            bootstrap_check(provider, None)
        }
        [db, command, provider_flag, provider, profile_flag, profile]
            if db == "db"
                && command == "bootstrap-check"
                && provider_flag == "--provider"
                && profile_flag == "--profile" =>
        {
            bootstrap_check(provider, Some(Path::new(profile)))
        }
        [admin, command, name_flag, name, url_flag, url]
            if admin == "admin"
                && command == "create-tenant"
                && name_flag == "--name"
                && url_flag == "--database-url" =>
        {
            admin_create_tenant(name, url)
        }
        [admin, command, id_flag, id, url_flag, url]
            if admin == "admin"
                && command == "revoke-key"
                && id_flag == "--id"
                && url_flag == "--database-url" =>
        {
            admin_revoke_key(id, url)
        }
        _ => {
            eprintln!(
                "usage: memphant <compile|sync|verify|lock|retain|recall|reflect|correct|forget|mark|trace|structured-state|db|admin> [options]; memory context commands use --subject-id <uuid> --scope <uuid> --actor <uuid> --agent-node <uuid> --subject-generation <n> or env MEMPHANT_SUBJECT_ID/MEMPHANT_SCOPE_ID/MEMPHANT_ACTOR_ID/MEMPHANT_AGENT_NODE_ID/MEMPHANT_SUBJECT_GENERATION (env: MEMPHANT_URL or MEMPHANT_CAPTURE_URL, MEMPHANT_API_KEY); recall prints a card, --json for raw"
            );
            ExitCode::from(2)
        }
    }
}

/// Thin HTTP clients for the six public memory verbs + trace inspection
/// (Task 8): each command posts the frozen REST contract to `MEMPHANT_URL`
/// (default http://127.0.0.1:8080) with `Authorization: Bearer
/// $MEMPHANT_API_KEY` and prints the JSON response to stdout.
mod http_verbs {
    use std::collections::HashMap;
    use std::process::ExitCode;

    use serde_json::{Value, json};

    const DEFAULT_URL: &str = "http://127.0.0.1:8080";

    pub fn run(verb: &str, args: &[String]) -> ExitCode {
        match execute(verb, args) {
            Ok(exit) => exit,
            Err(message) => {
                eprintln!("{verb}=error");
                eprintln!("{message}");
                ExitCode::from(2)
            }
        }
    }

    fn execute(verb: &str, args: &[String]) -> Result<ExitCode, String> {
        let (flags, positional) = parse_flags(args)?;
        if verb == "trace" {
            let id = positional
                .first()
                .cloned()
                .or_else(|| flags.get("id").cloned())
                .ok_or("usage: memphant trace <trace-id>")?;
            let (subject, scope, actor, agent_node, generation) = ids(&flags)?;
            return request(
                "GET",
                &format!(
                    "/v1/traces/{id}?subject_id={subject}&scope_id={scope}&actor_id={actor}&agent_node_id={agent_node}&subject_generation={generation}"
                ),
                None,
                None,
                true,
            );
        }
        if !positional.is_empty() {
            return Err(format!("unexpected positional arguments: {positional:?}"));
        }
        let body = build_body(verb, &flags)?;
        let path = match verb {
            "retain" => "/v1/episodes",
            "recall" => "/v1/recall",
            "reflect" => "/v1/reflect",
            "correct" => "/v1/correct",
            "forget" => "/v1/forget",
            "mark" => "/v1/mark",
            other => return Err(format!("unknown verb: {other}")),
        };
        // Mutating verbs need an idempotency key; a fresh uuid is the env-only
        // default so an agent can `memphant retain ...` without minting one.
        let idempotency_key = matches!(verb, "retain" | "reflect" | "correct" | "forget" | "mark")
            .then(|| {
                flags
                    .get("idempotency-key")
                    .cloned()
                    .unwrap_or_else(|| uuid::Uuid::new_v4().to_string())
            });
        let raw = verb != "recall" || flags.contains_key("json");
        request("POST", path, Some(body), idempotency_key.as_deref(), raw)
    }

    /// The compact human-readable recall card (default `recall` output). One
    /// line per item — `[unit_id] kind: body` — plus the trace id so the
    /// agent can `memphant mark --trace <id> --success --used <ids>`.
    pub(crate) fn render_card(response: &Value) -> String {
        let items = response
            .get("items")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let trace = response
            .get("trace_id")
            .and_then(Value::as_str)
            .unwrap_or("-");
        if items.is_empty() {
            return format!("memphant: no memory for this query (trace {trace})\n");
        }
        let mut out = format!("memphant memory ({} items, trace {trace}):\n", items.len());
        for item in &items {
            let id = item.get("unit_id").and_then(Value::as_str).unwrap_or("?");
            let kind = item.get("kind").and_then(Value::as_str).unwrap_or("?");
            // Surface the trust label so the model does not treat an unconfirmed
            // capture as established fact (parity with the file/hooks surfaces).
            let unconfirmed = item
                .get("inclusion_reason")
                .and_then(Value::as_str)
                .is_some_and(|reason| reason.contains("captured_unconfirmed"));
            let label = if unconfirmed { "[unconfirmed] " } else { "" };
            let body = item
                .get("body")
                .and_then(Value::as_str)
                .unwrap_or("")
                .trim()
                .replace('\n', "\n    ");
            out.push_str(&format!("- [{id}] {label}{kind}: {body}\n"));
        }
        out.push_str("mark outcome: memphant mark --trace ");
        out.push_str(trace);
        out.push_str(" --success --used <unit_id,...>\n");
        out
    }

    /// `--flag value` pairs plus bare `--resource` style booleans.
    fn parse_flags(args: &[String]) -> Result<(HashMap<String, String>, Vec<String>), String> {
        let mut flags = HashMap::new();
        let mut positional = Vec::new();
        let mut index = 0;
        while index < args.len() {
            let arg = &args[index];
            if let Some(name) = arg.strip_prefix("--") {
                let next = args.get(index + 1);
                match next {
                    Some(value) if !value.starts_with("--") => {
                        flags.insert(name.to_string(), value.clone());
                        index += 2;
                    }
                    _ => {
                        flags.insert(name.to_string(), "true".to_string());
                        index += 1;
                    }
                }
            } else {
                positional.push(arg.clone());
                index += 1;
            }
        }
        Ok((flags, positional))
    }

    fn now_rfc3339() -> String {
        jiff::Timestamp::now().to_string()
    }

    fn required<'a>(flags: &'a HashMap<String, String>, name: &str) -> Result<&'a str, String> {
        flags
            .get(name)
            .map(String::as_str)
            .ok_or_else(|| format!("missing required flag --{name}"))
    }

    /// A flag value, or the named env var when the flag is absent. Lets the
    /// battery / a coding agent drive the verbs with identity from the
    /// environment (`MEMPHANT_SUBJECT_ID` …) instead of five flags per call.
    fn flag_or_env(
        flags: &HashMap<String, String>,
        name: &str,
        env: &str,
    ) -> Result<String, String> {
        flags
            .get(name)
            .cloned()
            .or_else(|| {
                std::env::var(env)
                    .ok()
                    .filter(|value| !value.trim().is_empty())
            })
            .ok_or_else(|| format!("missing required flag --{name} (or env {env})"))
    }

    pub(crate) fn ids(
        flags: &HashMap<String, String>,
    ) -> Result<(String, String, String, String, u64), String> {
        Ok((
            flag_or_env(flags, "subject-id", "MEMPHANT_SUBJECT_ID")?,
            flag_or_env(flags, "scope", "MEMPHANT_SCOPE_ID")?,
            flag_or_env(flags, "actor", "MEMPHANT_ACTOR_ID")?,
            flag_or_env(flags, "agent-node", "MEMPHANT_AGENT_NODE_ID")?,
            flag_or_env(flags, "subject-generation", "MEMPHANT_SUBJECT_GENERATION")?
                .parse()
                .map_err(|error| format!("--subject-generation: {error}"))?,
        ))
    }

    fn build_body(verb: &str, flags: &HashMap<String, String>) -> Result<Value, String> {
        match verb {
            "retain" => {
                let (subject_id, scope, actor, agent_node_id, subject_generation) = ids(flags)?;
                let payload = if flags.contains_key("resource") {
                    let resource_body = match flags.get("body-file") {
                        Some(path) => Some(
                            std::fs::read_to_string(path)
                                .map_err(|error| format!("--body-file {path}: {error}"))?,
                        ),
                        None => flags.get("body").cloned(),
                    };
                    json!({ "resource": {
                        "uri": required(flags, "uri")?,
                        "mime_type": flags.get("mime-type").cloned().unwrap_or_else(|| "text/plain".to_string()),
                        "content_hash": required(flags, "content-hash")?,
                        "kind": flags.get("kind"),
                        "revision": flags.get("revision"),
                        "body": resource_body,
                    }})
                } else if flags.contains_key("unit") {
                    json!({ "unit": {
                        "kind": flags.get("kind").cloned().unwrap_or_else(|| "semantic".to_string()),
                        "fact_key": required(flags, "fact-key")?,
                        "predicate": required(flags, "predicate")?,
                        "body": required(flags, "body")?,
                        "confidence": required(flags, "confidence")?.parse::<f32>()
                            .map_err(|error| format!("--confidence: {error}"))?,
                        "valid_from": flags.get("valid-from"),
                        "valid_to": flags.get("valid-to"),
                    }})
                } else {
                    // `subject`/`predicate` are optional: they make the compiled
                    // unit's fact key explicit (the only thing that lets a rule
                    // supersede a prior one on the same subject, and the key a
                    // capture channel pairs on); absent, the auto content-hash
                    // key is used, exactly as before.
                    json!({ "episode": {
                        "source_kind": flags.get("source-kind").cloned().unwrap_or_else(|| "user".to_string()),
                        "body": required(flags, "body")?,
                        "subject": flags.get("subject"),
                        "predicate": flags.get("predicate"),
                    }})
                };
                Ok(json!({
                    "subject_id": subject_id,
                    "scope_id": scope,
                    "actor_id": actor,
                    "agent_node_id": agent_node_id,
                    "subject_generation": subject_generation,
                    "source_ref": flags.get("source-ref").cloned().unwrap_or_else(|| "memphant-cli".to_string()),
                    "observed_at": flags.get("observed-at").cloned().unwrap_or_else(now_rfc3339),
                    "payload": payload,
                }))
            }
            "recall" => {
                let (subject_id, scope, actor, agent_node_id, subject_generation) = ids(flags)?;
                Ok(json!({
                    "subject_id": subject_id,
                    "scope_id": scope,
                    "actor_id": actor,
                    "agent_node_id": agent_node_id,
                    "subject_generation": subject_generation,
                    "query": required(flags, "query")?,
                    "limit": flags.get("limit").map(|value| value.parse::<usize>()
                        .map_err(|error| format!("--limit: {error}"))).transpose()?,
                    "budget_tokens": flags.get("budget-tokens").map(|value| value.parse::<usize>()
                        .map_err(|error| format!("--budget-tokens: {error}"))).transpose()?,
                    "mode": flags.get("mode"),
                    "include_beliefs": flags.contains_key("include-beliefs").then_some(true),
                    "compact_only": flags.contains_key("compact-only"),
                    // Bare `memphant recall` is the coding union lane: serve the
                    // agent's own captured Candidates alongside its retained
                    // facts. `--general` opts out to the anti-poison general lane;
                    // `--compact-only` selects the higher-precision card lane
                    // (which serves captures too).
                    "serve_captures": !flags.contains_key("general"),
                    "transaction_as_of": flags.get("transaction-as-of"),
                    "valid_at": flags.get("valid-at"),
                }))
            }
            "reflect" => {
                let (subject_id, scope, actor, agent_node_id, subject_generation) = ids(flags)?;
                Ok(json!({
                    "subject_id": subject_id,
                    "scope_id": scope,
                    "actor_id": actor,
                    "agent_node_id": agent_node_id,
                    "subject_generation": subject_generation,
                }))
            }
            "correct" => {
                let (subject_id, scope, actor, agent_node_id, subject_generation) = ids(flags)?;
                Ok(json!({
                    "subject_id": subject_id,
                    "scope_id": scope,
                    "actor_id": actor,
                    "agent_node_id": agent_node_id,
                    "subject_generation": subject_generation,
                    "selector": { "memory_unit_id": required(flags, "unit")? },
                    "correction": {
                        "value": required(flags, "value")?,
                        "reason": required(flags, "reason")?,
                        "source_ref": required(flags, "source-ref")?,
                        "observed_at": required(flags, "observed-at")?,
                        "valid_from": flags.get("valid-from"),
                        "valid_to": flags.get("valid-to"),
                    },
                }))
            }
            "forget" => {
                let (subject_id, scope, actor, agent_node_id, subject_generation) = ids(flags)?;
                Ok(json!({
                    "subject_id": subject_id,
                    "scope_id": scope,
                    "actor_id": actor,
                    "agent_node_id": agent_node_id,
                    "subject_generation": subject_generation,
                    "selector": {
                        "memory_unit_id": flags.get("unit"),
                        "episode_id": flags.get("episode"),
                        "resource_id": flags.get("resource"),
                        "scope_id": scope,
                    },
                    "reason": required(flags, "reason")?,
                }))
            }
            "mark" => {
                let (subject_id, scope, actor, agent_node_id, subject_generation) = ids(flags)?;
                Ok(json!({
                    "subject_id": subject_id,
                    "scope_id": scope,
                    "actor_id": actor,
                    "agent_node_id": agent_node_id,
                    "subject_generation": subject_generation,
                    "trace_id": required(flags, "trace")?,
                    "caller_id": flags.get("caller").cloned().unwrap_or_else(|| "memphant-cli".to_string()),
                    "used_ids": flags
                        .get("used")
                        .map(|used| used.split(',').map(str::trim).filter(|id| !id.is_empty()).collect::<Vec<_>>())
                        .unwrap_or_default(),
                    "outcome": mark_outcome(flags)?,
                }))
            }
            other => Err(format!("unknown verb: {other}")),
        }
    }

    /// `--outcome <o>` or one of the bare `--success|--failure|--corrected|--ignored`.
    fn mark_outcome(flags: &HashMap<String, String>) -> Result<String, String> {
        if let Some(outcome) = flags.get("outcome") {
            return Ok(outcome.clone());
        }
        ["success", "failure", "corrected", "ignored"]
            .into_iter()
            .find(|name| flags.contains_key(*name))
            .map(str::to_string)
            .ok_or_else(|| {
                "missing --outcome <success|failure|corrected|ignored> (or --success …)".to_string()
            })
    }

    /// `MEMPHANT_URL`, else the origin of `MEMPHANT_CAPTURE_URL` (the battery
    /// / hooks export `<base>/v1/episodes`), else the local default.
    pub(crate) fn base_url() -> String {
        std::env::var("MEMPHANT_URL")
            .ok()
            .filter(|url| !url.trim().is_empty())
            .or_else(|| {
                std::env::var("MEMPHANT_CAPTURE_URL")
                    .ok()
                    .filter(|url| !url.trim().is_empty())
                    .and_then(|url| origin_of(&url))
            })
            .unwrap_or_else(|| DEFAULT_URL.to_string())
    }

    /// `scheme://host[:port]` of a URL, or `None` when it has no scheme.
    pub(crate) fn origin_of(url: &str) -> Option<String> {
        let (scheme, rest) = url.split_once("://")?;
        let host = rest.split('/').next()?.split('?').next()?;
        (!host.is_empty()).then(|| format!("{scheme}://{host}"))
    }

    fn request(
        method: &str,
        path: &str,
        body: Option<Value>,
        idempotency_key: Option<&str>,
        raw: bool,
    ) -> Result<ExitCode, String> {
        let base = base_url();
        let url = format!("{}{}", base.trim_end_matches('/'), path);
        let agent: ureq::Agent = ureq::Agent::config_builder()
            .http_status_as_error(false)
            .build()
            .into();
        let api_key = std::env::var("MEMPHANT_API_KEY").ok();
        let mut response = match body {
            Some(body) => {
                let mut request = agent.post(&url);
                if let Some(key) = &api_key {
                    request = request.header("authorization", format!("Bearer {key}"));
                }
                if let Some(key) = idempotency_key {
                    request = request.header("idempotency-key", key);
                }
                request
                    .send_json(&body)
                    .map_err(|error| format!("{method} {url}: {error}"))?
            }
            None => {
                let mut request = agent.get(&url);
                if let Some(key) = &api_key {
                    request = request.header("authorization", format!("Bearer {key}"));
                }
                if let Some(key) = idempotency_key {
                    request = request.header("idempotency-key", key);
                }
                request
                    .call()
                    .map_err(|error| format!("{method} {url}: {error}"))?
            }
        };
        let status = response.status().as_u16();
        let value: Value = response
            .body_mut()
            .read_json()
            .map_err(|error| format!("{method} {url}: non-JSON response: {error}"))?;
        if raw || !(200..300).contains(&status) {
            println!(
                "{}",
                serde_json::to_string_pretty(&value).map_err(|error| error.to_string())?
            );
        } else {
            print!("{}", render_card(&value));
        }
        if (200..300).contains(&status) {
            Ok(ExitCode::SUCCESS)
        } else {
            eprintln!("http_status={status}");
            Ok(ExitCode::from(1))
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn identity_falls_back_to_env_and_flags_win() {
            // Process-global env: one test owns all five vars.
            let vars = [
                ("MEMPHANT_SUBJECT_ID", "s-env"),
                ("MEMPHANT_SCOPE_ID", "scope-env"),
                ("MEMPHANT_ACTOR_ID", "actor-env"),
                ("MEMPHANT_AGENT_NODE_ID", "agent-env"),
                ("MEMPHANT_SUBJECT_GENERATION", "3"),
            ];
            for (name, value) in vars {
                // SAFETY: single-threaded test module; no other test reads these.
                unsafe { std::env::set_var(name, value) };
            }
            let env_only = ids(&HashMap::new()).expect("env identity");
            assert_eq!(
                env_only,
                (
                    "s-env".into(),
                    "scope-env".into(),
                    "actor-env".into(),
                    "agent-env".into(),
                    3
                )
            );
            let flags = HashMap::from([("subject-id".to_string(), "s-flag".to_string())]);
            assert_eq!(ids(&flags).expect("flag wins").0, "s-flag");
            for (name, _) in vars {
                unsafe { std::env::remove_var(name) };
            }
            let error = ids(&HashMap::new()).expect_err("no identity anywhere");
            assert!(
                error.contains("--subject-id") && error.contains("MEMPHANT_SUBJECT_ID"),
                "{error}"
            );
        }

        #[test]
        fn base_url_uses_capture_url_origin() {
            assert_eq!(
                origin_of("http://127.0.0.1:8091/v1/episodes").as_deref(),
                Some("http://127.0.0.1:8091")
            );
            assert_eq!(origin_of("not a url"), None);
        }

        #[test]
        fn card_renders_items_and_trace() {
            let card = render_card(&json!({
                "trace_id": "t-1",
                "items": [
                    {"unit_id": "u-1", "kind": "procedural", "body": "use make types\nbefore commit"},
                    {"unit_id": "u-2", "kind": "semantic", "body": "KV binding map is required"}
                ]
            }));
            assert!(
                card.starts_with("memphant memory (2 items, trace t-1):\n"),
                "{card}"
            );
            assert!(
                card.contains("- [u-1] procedural: use make types\n    before commit\n"),
                "{card}"
            );
            assert!(
                card.contains("- [u-2] semantic: KV binding map is required\n"),
                "{card}"
            );
            assert!(
                card.contains("memphant mark --trace t-1 --success --used"),
                "{card}"
            );
            let empty = render_card(&json!({"trace_id": "t-2", "items": []}));
            assert_eq!(empty, "memphant: no memory for this query (trace t-2)\n");
        }

        #[test]
        fn card_labels_unconfirmed_captures() {
            let card = render_card(&json!({
                "trace_id": "t-3",
                "items": [
                    {"unit_id": "c-1", "kind": "belief", "inclusion_reason": "captured_unconfirmed:coding_lane", "body": "acme magic byte is 0xA7"},
                    {"unit_id": "c-2", "kind": "semantic", "inclusion_reason": "fused_top_k", "body": "confirmed fact"}
                ]
            }));
            assert!(
                card.contains("- [c-1] [unconfirmed] belief: acme magic byte is 0xA7\n"),
                "{card}"
            );
            // A confirmed/normal item carries no label prefix.
            assert!(
                card.contains("- [c-2] semantic: confirmed fact\n"),
                "{card}"
            );
        }
    }
}

fn block_on<F: std::future::Future>(future: F) -> F::Output {
    tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .expect("tokio runtime")
        .block_on(future)
}

fn connect_pg(url: &str) -> Result<memphant_store_postgres::PgStore, String> {
    block_on(memphant_store_postgres::PgStore::connect_provisioner(url))
        .map_err(|error| error.to_string())
}

fn admin_create_tenant(name: &str, url: &str) -> ExitCode {
    match connect_pg(url)
        .and_then(|store| block_on(store.create_tenant(name)).map_err(|error| error.to_string()))
    {
        Ok(id) => {
            println!("tenant_created id={id} name={name}");
            ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("admin=error command=create-tenant");
            eprintln!("{error}");
            ExitCode::from(1)
        }
    }
}

fn admin_create_key(args: &[String]) -> ExitCode {
    let flags = match admin_flags(args) {
        Ok(flags) => flags,
        Err(error) => {
            eprintln!("admin=error command=create-key");
            eprintln!("{error}");
            return ExitCode::from(2);
        }
    };
    let required = |name: &str| {
        flags
            .get(name)
            .map(String::as_str)
            .ok_or_else(|| format!("missing --{name}"))
    };
    let tenant = match required("tenant") {
        Ok(value) => value,
        Err(error) => {
            eprintln!("admin=error command=create-key\n{error}");
            return ExitCode::from(2);
        }
    };
    let url = match required("database-url") {
        Ok(value) => value,
        Err(error) => {
            eprintln!("admin=error command=create-key\n{error}");
            return ExitCode::from(2);
        }
    };
    let max_trust = flags
        .get("max-trust")
        .map(String::as_str)
        .unwrap_or("trusted_user");
    let tenant_id = match uuid::Uuid::parse_str(tenant) {
        Ok(tenant_id) => tenant_id,
        Err(error) => {
            eprintln!("admin=error command=create-key");
            eprintln!("--tenant must be a UUID: {error}");
            return ExitCode::from(1);
        }
    };
    let trust: memphant_types::TrustLevel = match serde_json::from_value(serde_json::Value::String(
        max_trust.to_string(),
    )) {
        Ok(trust) => trust,
        Err(_) => {
            eprintln!("admin=error command=create-key");
            eprintln!(
                "--max-trust must be one of: trusted_user, trusted_system, verified_tool, unverified_tool, web_content, agent_output, imported_external, quarantined"
            );
            return ExitCode::from(1);
        }
    };

    // The plaintext key is printed exactly ONCE; only its sha256 is stored.
    let plaintext = memphant_core::generate_api_key_secret();
    let key_hash = sha256_hex(&plaintext);

    let context_names = [
        "subject-id",
        "subject-generation",
        "scope",
        "actor",
        "agent-node",
    ];
    let context_count = context_names
        .iter()
        .filter(|name| flags.contains_key(**name))
        .count();
    if context_count != 0 && context_count != context_names.len() {
        eprintln!("admin=error command=create-key");
        eprintln!(
            "context binding requires --subject-id, --subject-generation, --scope, --actor, and --agent-node together"
        );
        return ExitCode::from(2);
    }

    match connect_pg(url).and_then(|store| {
        block_on(async {
            let context = if context_count == context_names.len() {
                let parse_uuid = |name: &str| {
                    uuid::Uuid::parse_str(flags.get(name).expect("complete context flags"))
                        .map_err(|error| format!("--{name} must be a UUID: {error}"))
                };
                let expected_generation = flags["subject-generation"]
                    .parse::<u64>()
                    .map_err(|error| format!("--subject-generation must be an integer: {error}"))?;
                let context = store
                    .resolve_memory_context(
                        TenantId::from_u128(tenant_id.as_u128()),
                        SubjectId::from_u128(parse_uuid("subject-id")?.as_u128()),
                        ActorId::from_u128(parse_uuid("actor")?.as_u128()),
                        ScopeId::from_u128(parse_uuid("scope")?.as_u128()),
                        AgentNodeId::from_u128(parse_uuid("agent-node")?.as_u128()),
                    )
                    .await
                    .map_err(|error| format!("context binding is invalid: {error}"))?;
                if context.subject_generation != expected_generation {
                    return Err(format!(
                        "--subject-generation is stale: expected {}, got {expected_generation}",
                        context.subject_generation
                    ));
                }
                Some(context)
            } else {
                None
            };
            store
                .create_api_key(
                    TenantId::from_u128(tenant_id.as_u128()),
                    &key_hash,
                    "cli",
                    trust,
                    context.as_ref(),
                )
                .await
                .map_err(|error| error.to_string())
        })
    }) {
        Ok(id) => {
            println!("key_created id={id} tenant={tenant_id} max_trust={max_trust}");
            println!("{plaintext}");
            ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("admin=error command=create-key");
            eprintln!("{error}");
            ExitCode::from(1)
        }
    }
}

fn admin_flags(args: &[String]) -> Result<BTreeMap<String, String>, String> {
    if !args.len().is_multiple_of(2) {
        return Err("every create-key flag requires a value".to_string());
    }
    let allowed = [
        "tenant",
        "database-url",
        "max-trust",
        "subject-id",
        "subject-generation",
        "scope",
        "actor",
        "agent-node",
    ];
    let mut flags = BTreeMap::new();
    for pair in args.chunks_exact(2) {
        let name = pair[0]
            .strip_prefix("--")
            .ok_or_else(|| format!("expected a flag, got {}", pair[0]))?;
        if !allowed.contains(&name) {
            return Err(format!("unknown create-key flag --{name}"));
        }
        if flags.insert(name.to_string(), pair[1].clone()).is_some() {
            return Err(format!("duplicate create-key flag --{name}"));
        }
    }
    Ok(flags)
}

fn admin_revoke_key(id: &str, url: &str) -> ExitCode {
    let key_id = match uuid::Uuid::parse_str(id) {
        Ok(key_id) => key_id,
        Err(error) => {
            eprintln!("admin=error command=revoke-key");
            eprintln!("--id must be a UUID: {error}");
            return ExitCode::from(1);
        }
    };
    match connect_pg(url)
        .and_then(|store| block_on(store.revoke_api_key(key_id)).map_err(|error| error.to_string()))
    {
        Ok(true) => {
            println!("key_revoked id={key_id}");
            ExitCode::SUCCESS
        }
        Ok(false) => {
            eprintln!("admin=error command=revoke-key");
            eprintln!("key {key_id} not found or already revoked");
            ExitCode::from(1)
        }
        Err(error) => {
            eprintln!("admin=error command=revoke-key");
            eprintln!("{error}");
            ExitCode::from(1)
        }
    }
}

fn sha256_hex(value: &str) -> String {
    use sha2::{Digest, Sha256};
    let mut hasher = Sha256::new();
    hasher.update(value.as_bytes());
    hasher
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn emit_lock(out: &str) -> ExitCode {
    let json = match serde_json::to_string_pretty(&MemphantLock::current()) {
        Ok(json) => json,
        Err(error) => {
            eprintln!("lock=error");
            eprintln!("{error}");
            return ExitCode::from(1);
        }
    };

    if out == "-" {
        println!("{json}");
        return ExitCode::SUCCESS;
    }

    match fs::write(out, format!("{json}\n")) {
        Ok(()) => {
            println!("lock=written path={out}");
            ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("lock=error path={out}");
            eprintln!("{error}");
            ExitCode::from(1)
        }
    }
}

fn verify_lock(path: &str, export_dir: Option<&Path>) -> ExitCode {
    let lock = match fs::read_to_string(path)
        .map_err(|error| error.to_string())
        .and_then(|content| {
            serde_json::from_str::<MemphantLock>(&content).map_err(|error| error.to_string())
        }) {
        Ok(lock) => lock,
        Err(error) => {
            eprintln!("verify=error path={path}");
            eprintln!("{error}");
            return ExitCode::from(1);
        }
    };

    let report = VerifyReport::from_lock(lock);
    if !report.ok {
        eprintln!("verify=dirty path={path}");
        for mismatch in report.mismatches {
            eprintln!(
                "{} expected={} actual={}",
                mismatch.key, mismatch.expected, mismatch.actual
            );
        }
        return ExitCode::from(1);
    }

    if let Some(export_dir) = export_dir
        && let Err(mismatches) = file_plane::verify_export(export_dir)
    {
        eprintln!("verify=dirty path={path}");
        for mismatch in mismatches {
            eprintln!("{mismatch}");
        }
        return ExitCode::from(1);
    }

    println!("verify=clean path={path}");
    if let Some(export_dir) = export_dir {
        println!("export=clean path={}", export_dir.display());
    }
    ExitCode::SUCCESS
}

fn bootstrap_check(provider: &str, profile_path: Option<&Path>) -> ExitCode {
    let provider = match Provider::from_str(provider) {
        Ok(provider) => provider,
        Err(error) => {
            eprintln!("bootstrap_check=dirty provider={provider}");
            eprintln!("{error}");
            return ExitCode::from(1);
        }
    };
    let profile_path = profile_path.map(Path::to_path_buf).unwrap_or_else(|| {
        PathBuf::from(DEFAULT_PROVIDER_PROFILE_DIR).join(format!("{provider}.env.example"))
    });

    let mut findings = Vec::new();
    if let Err(error) = lint_migrations(provider) {
        findings.extend(
            error
                .findings()
                .iter()
                .map(|finding| format!("migration:{finding}")),
        );
    }

    match read_provider_profile(&profile_path) {
        Ok(profile) => findings.extend(validate_provider_profile(provider, &profile)),
        Err(error) => findings.push(format!("profile:unreadable:{error}")),
    }

    if findings.is_empty() {
        println!(
            "bootstrap_check=clean provider={provider} profile={}",
            profile_path.display()
        );
        println!("migration_lint=clean provider={provider}");
        return ExitCode::SUCCESS;
    }

    eprintln!(
        "bootstrap_check=dirty provider={provider} profile={}",
        profile_path.display()
    );
    for finding in findings {
        eprintln!("{finding}");
    }
    ExitCode::from(1)
}

fn read_provider_profile(path: &Path) -> Result<BTreeMap<String, String>, String> {
    let content = fs::read_to_string(path).map_err(|error| error.to_string())?;
    parse_env_profile(&content)
}

fn parse_env_profile(content: &str) -> Result<BTreeMap<String, String>, String> {
    let mut profile = BTreeMap::new();
    for (index, line) in content.lines().enumerate() {
        let trimmed = line.trim();
        if trimmed.is_empty() || trimmed.starts_with('#') {
            continue;
        }
        let Some((key, value)) = trimmed.split_once('=') else {
            return Err(format!("line:{}:missing_equals", index + 1));
        };
        let key = key.trim();
        if key.is_empty()
            || !key
                .chars()
                .all(|ch| ch.is_ascii_uppercase() || ch.is_ascii_digit() || ch == '_')
        {
            return Err(format!("line:{}:invalid_key", index + 1));
        }
        profile.insert(key.to_string(), unquote_env_value(value.trim()).to_string());
    }
    Ok(profile)
}

fn unquote_env_value(value: &str) -> &str {
    if value.len() >= 2 {
        let bytes = value.as_bytes();
        if (bytes[0] == b'"' && bytes[value.len() - 1] == b'"')
            || (bytes[0] == b'\'' && bytes[value.len() - 1] == b'\'')
        {
            return &value[1..value.len() - 1];
        }
    }
    value
}

fn validate_provider_profile(
    provider: Provider,
    profile: &BTreeMap<String, String>,
) -> Vec<String> {
    let mut findings = Vec::new();
    let expected_provider = provider.to_string();
    if let Some(value) = require_key(profile, "MEMPHANT_PROVIDER", &mut findings)
        && value != expected_provider
    {
        findings.push(format!(
            "profile:provider_mismatch:expected={expected_provider}:actual={value}"
        ));
    }
    if let Some(schema) = require_key(profile, "MEMPHANT_SCHEMA", &mut findings)
        && schema != "memphant"
    {
        findings.push(format!("profile:schema_mismatch:actual={schema}"));
    }
    let migrator_url = require_key(profile, "DATABASE_URL", &mut findings);
    if let Some(database_url) = migrator_url {
        validate_database_url(provider, database_url, &mut findings);
    }
    validate_served_credentials(provider, profile, migrator_url, &mut findings);
    validate_residency_and_retention(profile, &mut findings);

    match provider {
        Provider::PlainPostgres => {}
        Provider::Supabase => validate_supabase_profile(profile, &mut findings),
        Provider::Neon => validate_neon_profile(profile, &mut findings),
    }

    findings
}

/// Login names that carry `rolsuper` or a `using(true)` bypass policy on the
/// providers MemPhant ships profiles for. `memphant_owner` owns every table
/// and holds the `memphant_*_owner` bypass policies; the rest are the
/// providers' own elevated logins. A served process using any of them silently
/// disables all 27 `_tenant_isolation` policies.
const RLS_BYPASSING_LOGINS: &[&str] = &[
    "memphant_owner",
    "postgres",
    "supabase_admin",
    "rdsadmin",
    "cloudsqlsuperuser",
    "neondb_owner",
];

/// The served processes must not share the migrator credential, and must not
/// use a login that bypasses row-level security. `PgStore` refuses to serve on
/// a bypassing role at startup; this catches the same mistake in the profile,
/// before a maintenance window.
fn validate_served_credentials(
    provider: Provider,
    profile: &BTreeMap<String, String>,
    migrator_url: Option<&str>,
    findings: &mut Vec<String>,
) {
    let migrator_login = migrator_url.and_then(url_login);
    for key in [
        "MEMPHANT_APP_DATABASE_URL",
        "MEMPHANT_AUTHN_DATABASE_URL",
        "MEMPHANT_WORKER_DATABASE_URL",
    ] {
        let Some(url) = require_key(profile, key, findings) else {
            continue;
        };
        validate_database_url(provider, url, findings);
        let Some(login) = url_login(url) else {
            findings.push(format!("{key}:missing_login_role"));
            continue;
        };
        if RLS_BYPASSING_LOGINS.contains(&login) {
            findings.push(format!("{key}:rls_bypassing_login:{login}"));
        }
        if migrator_login == Some(login) {
            findings.push(format!("{key}:reuses_migrator_credential:{login}"));
        }
    }
}

/// Userinfo login name from a `scheme://user:password@host/...` URL.
fn url_login(url: &str) -> Option<&str> {
    let (_, tail) = url.split_once("://")?;
    let authority = tail.split(['/', '?']).next()?;
    let userinfo = authority.rsplit_once('@')?.0;
    Some(userinfo.split_once(':').map_or(userinfo, |(user, _)| user))
}

fn validate_database_url(provider: Provider, database_url: &str, findings: &mut Vec<String>) {
    if !(database_url.starts_with("postgres://") || database_url.starts_with("postgresql://")) {
        findings.push("database_url:must_use_postgres_scheme".to_string());
    }
    if provider == Provider::Neon && !database_url.contains("sslmode=require") {
        findings.push("neon:database_url_missing_sslmode_require".to_string());
    }
    if provider == Provider::Supabase
        && database_url
            .split_once("://")
            .and_then(|(_, tail)| tail.split('/').next())
            .and_then(|authority| authority.rsplit_once(':'))
            .is_some_and(|(_, port)| port == "6543")
    {
        findings.push("supabase:database_url_transaction_pooler_forbidden".to_string());
    }
    if database_url.contains("public.") || database_url.contains("syndai.") {
        findings.push("database_url:forbidden_schema_reference".to_string());
    }
}

fn validate_residency_and_retention(
    profile: &BTreeMap<String, String>,
    findings: &mut Vec<String>,
) {
    let pg_region = require_key(profile, "MEMPHANT_PG_REGION", findings).map(str::to_string);
    let object_region =
        require_key(profile, "MEMPHANT_OBJECT_STORE_REGION", findings).map(str::to_string);
    if let (Some(pg_region), Some(object_region)) = (pg_region, object_region)
        && pg_region != object_region
    {
        findings.push(format!(
            "residency:region_mismatch:pg={pg_region}:object_store={object_region}"
        ));
    }

    require_key(profile, "MEMPHANT_OBJECT_STORE", findings);
    require_key(profile, "MEMPHANT_OBJECT_STORE_BUCKET", findings);

    expect_true(profile, "MEMPHANT_OBJECT_VERSIONING_REQUIRED", findings);
    let pitr_days = parse_u64_key(profile, "MEMPHANT_PITR_WINDOW_DAYS", findings);
    let retention_days = parse_u64_key(profile, "MEMPHANT_OBJECT_RETENTION_DAYS", findings);
    if let (Some(pitr_days), Some(retention_days)) = (pitr_days, retention_days)
        && retention_days < pitr_days + PITR_RETENTION_MARGIN_DAYS
    {
        findings.push(format!(
            "restore_retention_floor_violation:pitr_days={pitr_days}:object_retention_days={retention_days}:required_min={}",
            pitr_days + PITR_RETENTION_MARGIN_DAYS
        ));
    }
}

fn validate_supabase_profile(profile: &BTreeMap<String, String>, findings: &mut Vec<String>) {
    if let Some(exposed) = require_key(profile, "MEMPHANT_SUPABASE_EXPOSED_SCHEMAS", findings) {
        let exposed_schemas = exposed
            .split(',')
            .map(|value| value.trim().to_ascii_lowercase())
            .collect::<Vec<_>>();
        if exposed_schemas.iter().any(|schema| schema == "memphant") {
            findings.push("supabase:memphant_schema_exposed_to_postgrest".to_string());
        }
    }
    expect_false(
        profile,
        "MEMPHANT_SUPABASE_ANON_HAS_MEMPHANT_ACCESS",
        findings,
    );
    expect_false(
        profile,
        "MEMPHANT_SUPABASE_AUTHENTICATED_HAS_MEMPHANT_ACCESS",
        findings,
    );
    expect_true(profile, "MEMPHANT_SUPABASE_ADVISORS_REQUIRED", findings);
    if let Some(command) = require_key(profile, "MEMPHANT_SUPABASE_LINT_COMMAND", findings) {
        let command = command.to_ascii_lowercase();
        for needle in ["supabase db lint", "--schema memphant", "--fail-on warning"] {
            if !command.contains(needle) {
                findings.push(format!("supabase:lint_command_missing:{needle}"));
            }
        }
    }
}

fn validate_neon_profile(profile: &BTreeMap<String, String>, findings: &mut Vec<String>) {
    require_key(profile, "MEMPHANT_NEON_BRANCH", findings);
    expect_true(profile, "MEMPHANT_NEON_BRANCHING_FOR_EVALS", findings);
}

fn require_key<'a>(
    profile: &'a BTreeMap<String, String>,
    key: &str,
    findings: &mut Vec<String>,
) -> Option<&'a str> {
    match profile.get(key) {
        Some(value) if !value.trim().is_empty() => Some(value.as_str()),
        _ => {
            findings.push(format!("profile:missing:{key}"));
            None
        }
    }
}

fn parse_u64_key(
    profile: &BTreeMap<String, String>,
    key: &str,
    findings: &mut Vec<String>,
) -> Option<u64> {
    let value = require_key(profile, key, findings)?;
    match value.parse::<u64>() {
        Ok(value) => Some(value),
        Err(_) => {
            findings.push(format!("profile:invalid_u64:{key}:{value}"));
            None
        }
    }
}

fn expect_true(profile: &BTreeMap<String, String>, key: &str, findings: &mut Vec<String>) {
    if let Some(value) = require_key(profile, key, findings)
        && value != "true"
    {
        findings.push(format!("profile:expected_true:{key}:actual={value}"));
    }
}

fn expect_false(profile: &BTreeMap<String, String>, key: &str, findings: &mut Vec<String>) {
    if let Some(value) = require_key(profile, key, findings)
        && value != "false"
    {
        findings.push(format!("profile:expected_false:{key}:actual={value}"));
    }
}

#[cfg(test)]
mod admin_key_tests {
    use super::admin_flags;

    #[test]
    fn create_key_flags_accept_a_complete_context_in_any_order() {
        let values = [
            "--scope",
            "scope-id",
            "--tenant",
            "tenant-id",
            "--database-url",
            "postgres://example",
            "--subject-id",
            "subject-id",
            "--subject-generation",
            "3",
            "--actor",
            "actor-id",
            "--agent-node",
            "agent-id",
        ]
        .map(str::to_string);
        let flags = admin_flags(&values).expect("valid flags");
        assert_eq!(flags["subject-generation"], "3");
        assert_eq!(flags["agent-node"], "agent-id");
    }

    #[test]
    fn create_key_flags_reject_unknown_duplicate_and_unpaired_values() {
        for values in [
            vec!["--unknown".to_string(), "x".to_string()],
            vec![
                "--tenant".to_string(),
                "a".to_string(),
                "--tenant".to_string(),
                "b".to_string(),
            ],
            vec!["--tenant".to_string()],
        ] {
            assert!(admin_flags(&values).is_err());
        }
    }
}
