use std::collections::{BTreeMap, BTreeSet};
use std::env;
use std::ffi::OsString;
use std::fmt;
use std::fs;
use std::io::{Read, Write};
#[cfg(unix)]
use std::os::fd::AsFd;
use std::path::{Component, Path, PathBuf};
use std::process::ExitCode;
use std::time::Duration;
#[cfg(windows)]
use std::{ffi::OsStr, os::windows::ffi::OsStrExt, os::windows::io::AsRawHandle};

use memphant_core::service::{canonical_projection_fingerprint, file_sync_plan_sha256};
use memphant_types::{
    CanonicalProjectionResponse, CanonicalProjectionUnit, FileSyncOperation,
    FileSyncOperationResult, FileSyncRequest, FileSyncResult, FileSyncUnitMetadata,
    MAX_FILE_SYNC_REQUEST_ENCODED_BYTES, MemoryKind, UnitId, UnitState,
};
use serde::de::{self, DeserializeOwned, MapAccess, SeqAccess, Visitor};
use serde::{Deserialize, Deserializer, Serialize};
use serde_json::{Map, Number, Value};
use sha2::{Digest, Sha256};
use uuid::Uuid;

use cap_fs_ext::{DirExt, FollowSymlinks, MetadataExt, OpenOptionsFollowExt, OpenOptionsSyncExt};
use cap_std::ambient_authority;
#[cfg(unix)]
use cap_std::fs::DirBuilderExt as _;
#[cfg(windows)]
use cap_std::fs::OpenOptionsExt as _;
use cap_std::fs::{Dir, DirBuilder, OpenOptions};
#[cfg(unix)]
use rustix::fs::{Mode, OFlags, openat};
#[cfg(any(
    target_vendor = "apple",
    target_os = "linux",
    target_os = "android",
    target_os = "redox"
))]
use rustix::fs::{RenameFlags, renameat_with};
#[cfg(windows)]
use windows_sys::Win32::Foundation::{ERROR_ALREADY_EXISTS, ERROR_FILE_EXISTS};
#[cfg(windows)]
use windows_sys::Win32::Storage::FileSystem::{
    DELETE, FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT, FILE_READ_ATTRIBUTES,
    FILE_RENAME_INFO, FILE_SHARE_DELETE, FILE_SHARE_READ, FILE_SHARE_WRITE, FileRenameInfo,
    SetFileInformationByHandle,
};

const DEFAULT_URL: &str = "http://127.0.0.1:8080";
// D2 (2026-08-01) bumped 1 -> 2: the unit footer gained state, valid_from,
// valid_to and source_span, and MEMORY.md became a table. Both are
// `deny_unknown_fields` round-trips, so a v1 tree on disk cannot be parsed by
// this binary — the manifest check must fail loudly rather than mid-parse.
const SCHEMA_VERSION: u32 = 2;
const COMPILER_VERSION: &str = "b2-file-plane-v1";
const MEMORY_FILE: &str = "MEMORY.md";
const MANIFEST_FILE: &str = "memphant-export.json";
const UNITS_DIR: &str = "units";
const INBOX_DIR: &str = "inbox";
const MAX_MANIFEST_BYTES: u64 = 4 * 1024 * 1024;
const MAX_MANAGED_FILE_BYTES: u64 = 2 * 1024 * 1024;
const MAX_DIRECTORY_ENTRIES: usize = 100_000;
const DEFAULT_HTTP_TIMEOUT_MS: u64 = 30_000;
const MAX_HTTP_TIMEOUT_MS: u64 = 300_000;
const CONTEXT_HELP: &str = "\
Context (required):
  --subject-id <UUID>          Subject identity
  --scope <UUID>               Scope identity
  --actor <UUID>               Actor identity
  --agent-node <UUID>          Agent-node identity
  --subject-generation <N>     Subject generation
  --out <DIR>                  Projection directory

";
const ENVIRONMENT_HELP: &str = "\
Environment:
  MEMPHANT_URL                 Server URL (default: http://127.0.0.1:8080)
  MEMPHANT_API_KEY             Bearer API key (optional for local dev mode)
  MEMPHANT_HTTP_TIMEOUT_MS     Request timeout, 1..=300000 (default: 30000)

";
const COMPILE_HELP: &str = "\
Usage: memphant compile [CONTEXT OPTIONS]

Compile the canonical scope into a deterministic editable projection.

Output:
  --out <DIR> contains MEMORY.md, units/, inbox/, and memphant-export.json.
  Refuses to overwrite a dirty projection; no canonical memory is changed.

Next: edit units/*.md or add inbox/*.md, then run `memphant sync` with the same context.
";
const SYNC_HELP: &str = "\
Usage: memphant sync [CONTEXT OPTIONS] [--apply]

Validate local edits and build one digest-bound sync plan.

Options:
  --apply                      Atomically apply the validated plan

Default: dry-run; prints the JSON plan to stdout and changes nothing.

Safe next steps:
  Review the plan, then rerun the same command with --apply.
  outcome_unknown: the request may have committed; do not retry a different plan.
  First create the binary contract with `memphant lock --out memphant.lock`.
  After apply: run `memphant verify --lock memphant.lock --export <DIR>`.
";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct FileIdentity {
    device: u64,
    inode: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct ManagedFileSnapshot {
    identity: FileIdentity,
    bytes: Vec<u8>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct OutputParentAnchor {
    path: PathBuf,
    identity: FileIdentity,
}

type ExactProjectionSnapshot = (
    ExportManifest,
    String,
    BTreeMap<String, ManagedFileSnapshot>,
);

#[derive(Debug)]
struct ProjectionWriteOutcome {
    snapshot: ExactProjectionSnapshot,
    recovery: Option<PathBuf>,
}

#[derive(Debug)]
struct RecoveryArea {
    name: String,
    path: PathBuf,
    identity: FileIdentity,
    root: Dir,
    units: Dir,
    units_identity: FileIdentity,
    inbox: Dir,
    inbox_identity: FileIdentity,
}

#[derive(Debug)]
struct RecoverySession {
    anchor: OutputParentAnchor,
    area: Option<RecoveryArea>,
    retained_managed_inodes: usize,
}

#[derive(Debug)]
struct DirectoryBinding {
    parent: Dir,
    name: String,
    identity: FileIdentity,
}

#[derive(Debug)]
struct TreeHandles {
    anchor: OutputParentAnchor,
    anchor_parent: Dir,
    outer_bindings: Vec<DirectoryBinding>,
    parent: Dir,
    root_name: String,
    root: Dir,
    units: Dir,
    inbox: Dir,
    parent_identity: FileIdentity,
    root_identity: FileIdentity,
    units_identity: FileIdentity,
    inbox_identity: FileIdentity,
}

#[derive(Debug)]
struct ValidatedExport {
    manifest: ExportManifest,
    snapshot_sha256: String,
    handles: TreeHandles,
    managed_files: BTreeMap<String, ManagedFileSnapshot>,
}

#[derive(Debug)]
struct AbsentOutput {
    anchor: OutputParentAnchor,
    anchor_parent: Dir,
    parent: Dir,
    missing_components: Vec<String>,
}

#[derive(Debug)]
struct EmptyOutput {
    anchor: OutputParentAnchor,
    anchor_parent: Dir,
    parent: Dir,
    root_name: String,
    root: Dir,
    root_identity: FileIdentity,
    root_names: Vec<String>,
}

#[derive(Debug)]
enum OutputState {
    Absent(AbsentOutput),
    Empty(EmptyOutput),
    Existing(Box<ValidatedExport>),
}

#[derive(Debug)]
struct CompileArgs {
    subject_id: Uuid,
    scope_id: Uuid,
    actor_id: Uuid,
    agent_node_id: Uuid,
    subject_generation: u64,
    out: PathBuf,
}

#[derive(Debug)]
struct SyncArgs {
    context: CompileArgs,
    apply: bool,
}

#[derive(Debug, Clone, PartialEq)]
struct SyncSnapshot {
    manifest: ExportManifest,
    managed_files: BTreeMap<String, ManagedFileSnapshot>,
    inbox_files: BTreeMap<String, ManagedFileSnapshot>,
    operations: Vec<FileSyncOperation>,
}

#[derive(Debug)]
struct SyncState {
    handles: TreeHandles,
    snapshot: SyncSnapshot,
}

#[derive(Debug, Serialize)]
struct SyncPlan {
    schema_version: u32,
    subject_id: String,
    scope_id: String,
    actor_id: String,
    agent_node_id: String,
    subject_generation: u64,
    base_fingerprint: String,
    plan_sha256: String,
    operations: Vec<FileSyncOperation>,
    destructive: Vec<String>,
    consumed_inbox: Vec<String>,
}

#[derive(Debug)]
enum SyncFailure {
    Invalid(Vec<String>),
    Conflict(String),
    Unavailable(String),
    OutcomeUnknown(String),
    PostCommit(String),
    Error(String),
}

#[derive(Debug)]
enum ProjectionFetchFailure {
    Unavailable(String),
    Status { status: u16, code: String },
    Invalid(String),
}

impl fmt::Display for ProjectionFetchFailure {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Unavailable(error) | Self::Invalid(error) => formatter.write_str(error),
            Self::Status { status, code } => {
                write!(
                    formatter,
                    "projection request failed: status={status} code={code}"
                )
            }
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct UnitFooter {
    pub unit_id: String,
    pub body_sha256: String,
    pub subject_generation: u64,
    pub kind: MemoryKind,
    pub fact_key: Option<String>,
    pub predicate: Option<String>,
    pub confidence: Option<f32>,
    // D2. The projection already sends these and the renderer discarded them,
    // so a unit file could not tell you when its rule became true, when it
    // stopped, whether it had been validated, or which source bytes it came
    // from — the four things a human reviewing memory actually asks. The
    // projection is state- and interval-filtered by construction, so these
    // are provenance for the row that IS here, not a filter to apply.
    pub state: UnitState,
    pub valid_from: Option<String>,
    pub valid_to: Option<String>,
    pub source_span: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ManifestEntry {
    pub unit_id: String,
    pub path: String,
    pub kind: MemoryKind,
    pub fact_key: Option<String>,
    pub predicate: Option<String>,
    pub confidence: Option<f32>,
    pub valid_from: Option<String>,
    pub valid_to: Option<String>,
    pub state: UnitState,
    pub source_span: Option<String>,
    pub body_sha256: String,
    pub file_sha256: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ExportManifest {
    pub schema_version: u32,
    pub compiler_version: String,
    pub tenant_id: String,
    pub subject_id: String,
    pub actor_id: String,
    pub scope_id: String,
    pub agent_node_id: String,
    pub subject_generation: u64,
    pub snapshot_sha256: String,
    pub memory_sha256: String,
    pub entries: Vec<ManifestEntry>,
}

#[derive(Debug)]
struct RenderedProjection {
    memory: Vec<u8>,
    units: BTreeMap<String, Vec<u8>>,
    manifest: ExportManifest,
    manifest_bytes: Vec<u8>,
}

#[derive(Debug)]
enum CompileFailure {
    Dirty(Vec<String>),
    Error(String),
}

pub(crate) fn run_compile(args: &[String]) -> ExitCode {
    if args == ["--help"] {
        print!("{COMPILE_HELP}\n{CONTEXT_HELP}{ENVIRONMENT_HELP}");
        return ExitCode::SUCCESS;
    }
    match compile(args) {
        Ok((scope, out, snapshot, entries, recovery)) => {
            if let Some(recovery) = recovery {
                println!(
                    "compile=written scope={scope} snapshot={snapshot} out={} entries={entries} recovery={}",
                    out.display(),
                    recovery.display()
                );
            } else {
                println!(
                    "compile=written scope={scope} snapshot={snapshot} out={} entries={entries}",
                    out.display()
                );
            }
            ExitCode::SUCCESS
        }
        Err(CompileFailure::Dirty(findings)) => {
            eprintln!("compile=dirty");
            for finding in findings {
                eprintln!("{finding}");
            }
            eprintln!("run `memphant sync` or restore the projection before compiling");
            ExitCode::from(1)
        }
        Err(CompileFailure::Error(error)) => {
            eprintln!("compile=error");
            eprintln!("{error}");
            ExitCode::from(1)
        }
    }
}

pub(crate) fn run_sync(args: &[String]) -> ExitCode {
    if args == ["--help"] {
        print!("{SYNC_HELP}\n{CONTEXT_HELP}{ENVIRONMENT_HELP}");
        return ExitCode::SUCCESS;
    }
    match sync(args) {
        Ok(SyncRun::Plan(plan)) => match serde_json::to_string_pretty(&plan) {
            Ok(json) => {
                println!("{json}");
                ExitCode::SUCCESS
            }
            Err(error) => {
                eprintln!("sync=error");
                eprintln!("cannot encode sync plan: {error}");
                ExitCode::from(1)
            }
        },
        Ok(SyncRun::Noop(plan)) => {
            println!(
                "sync=noop scope={} snapshot={} plan={}",
                plan.scope_id, plan.base_fingerprint, plan.plan_sha256
            );
            ExitCode::SUCCESS
        }
        Ok(SyncRun::Applied {
            scope_id,
            plan_sha256,
            committed_fingerprint,
            final_fingerprint,
            operations,
            created,
            recovery,
        }) => {
            let created = if created.is_empty() {
                "-".to_string()
            } else {
                created.join(",")
            };
            if let Some(recovery) = recovery {
                println!(
                    "sync=applied scope={scope_id} plan={plan_sha256} committed_snapshot={committed_fingerprint} final_snapshot={final_fingerprint} operations={operations} created={created} recovery={}",
                    recovery.display()
                );
            } else {
                println!(
                    "sync=applied scope={scope_id} plan={plan_sha256} committed_snapshot={committed_fingerprint} final_snapshot={final_fingerprint} operations={operations} created={created}"
                );
            }
            ExitCode::SUCCESS
        }
        Err(SyncFailure::Invalid(findings)) => {
            eprintln!("sync=invalid");
            for finding in findings {
                eprintln!("{finding}");
            }
            ExitCode::from(1)
        }
        Err(SyncFailure::Conflict(error)) => {
            eprintln!("sync=conflict");
            eprintln!("{error}");
            ExitCode::from(1)
        }
        Err(SyncFailure::Unavailable(error)) => {
            eprintln!("sync=unavailable");
            eprintln!("{error}");
            ExitCode::from(1)
        }
        Err(SyncFailure::OutcomeUnknown(error)) => {
            eprintln!("sync=outcome_unknown");
            eprintln!("{error}");
            eprintln!(
                "the request may have committed; do not construct a different request against this local tree"
            );
            ExitCode::from(1)
        }
        Err(SyncFailure::PostCommit(error)) => {
            eprintln!("sync=post_commit_error remote_committed=true");
            eprintln!("{error}");
            eprintln!("canonical memory committed; local projection was not reported clean");
            ExitCode::from(1)
        }
        Err(SyncFailure::Error(error)) => {
            eprintln!("sync=error");
            eprintln!("{error}");
            ExitCode::from(1)
        }
    }
}

#[derive(Debug)]
enum SyncRun {
    Plan(SyncPlan),
    Noop(SyncPlan),
    Applied {
        scope_id: Uuid,
        plan_sha256: String,
        committed_fingerprint: String,
        final_fingerprint: String,
        operations: usize,
        created: Vec<String>,
        recovery: Option<PathBuf>,
    },
}

fn sync(args: &[String]) -> Result<SyncRun, SyncFailure> {
    let args = parse_sync_args(args).map_err(SyncFailure::Error)?;
    let state = inspect_sync_output(&args.context.out)?;
    validate_manifest_binding(&state.snapshot.manifest, &args.context)
        .map_err(SyncFailure::Invalid)?;

    let projection = fetch_projection(&args.context).map_err(classify_projection_failure)?;
    validate_response_binding(&projection, &args.context)
        .map_err(|error| SyncFailure::Invalid(vec![error]))?;
    validate_sync_base(&state.snapshot.manifest, &projection)?;
    revalidate_sync_state(&state)?;

    let plan = sync_plan(&state.snapshot).map_err(SyncFailure::Error)?;
    let request = FileSyncRequest {
        subject_id: projection.subject_id,
        scope_id: projection.scope_id,
        actor_id: projection.actor_id,
        agent_node_id: projection.agent_node_id,
        subject_generation: projection.subject_generation,
        base_fingerprint: plan.base_fingerprint.clone(),
        plan_sha256: plan.plan_sha256.clone(),
        observed_at: projection.evaluated_at.clone(),
        operations: plan.operations.clone(),
    };
    let encoded_request = encode_file_sync_request(&request)?;
    if !args.apply {
        return Ok(SyncRun::Plan(plan));
    }
    if plan.operations.is_empty() {
        return Ok(SyncRun::Noop(plan));
    }

    revalidate_sync_state(&state)?;
    let idempotency_key = format!("file-sync:{}:{}", plan.plan_sha256, Uuid::new_v4());
    let receipt = post_file_sync(&request, &encoded_request, &idempotency_key)?;
    validate_sync_receipt(&request, &receipt).map_err(|error| {
        SyncFailure::OutcomeUnknown(format!(
            "file-sync returned 200 but the receipt did not prove commit for plan {}: {error}",
            request.plan_sha256
        ))
    })?;

    let final_projection = fetch_projection(&args.context)
        .map_err(|error| post_commit_failure(&receipt, error.to_string()))?;
    validate_response_binding(&final_projection, &args.context)
        .map_err(|error| post_commit_failure(&receipt, error))?;
    revalidate_sync_state(&state).map_err(|error| {
        post_commit_failure(
            &receipt,
            format!(
                "local projection changed while the committed batch was in flight: {}",
                display_sync_failure(error)
            ),
        )
    })?;
    let rendered = render_projection(&final_projection)
        .map_err(|error| post_commit_failure(&receipt, error))?;

    let previous = ValidatedExport {
        manifest: state.snapshot.manifest.clone(),
        snapshot_sha256: String::new(),
        handles: state.handles,
        managed_files: state.snapshot.managed_files,
    };
    let outcome = write_rendered_projection(
        &previous.handles,
        Some(&previous),
        &rendered,
        &state.snapshot.inbox_files,
        &mut |_| {},
    )
    .map_err(|error| {
        post_commit_failure(&receipt, format!("local projection update failed: {error}"))
    })?;
    let created = receipt
        .operations
        .iter()
        .flat_map(|operation| match operation {
            FileSyncOperationResult::Correct { created, .. }
            | FileSyncOperationResult::Retain { created } => created.as_slice(),
            FileSyncOperationResult::Forget { .. } => &[],
        })
        .map(|id| id.as_uuid().to_string())
        .collect();

    Ok(SyncRun::Applied {
        scope_id: args.context.scope_id,
        plan_sha256: plan.plan_sha256,
        committed_fingerprint: receipt.fingerprint,
        final_fingerprint: final_projection.fingerprint,
        operations: receipt.operations.len(),
        created,
        recovery: outcome.recovery,
    })
}

fn post_commit_failure(receipt: &FileSyncResult, error: String) -> SyncFailure {
    SyncFailure::PostCommit(format!(
        "committed_snapshot={}; {error}",
        receipt.fingerprint
    ))
}

fn display_sync_failure(error: SyncFailure) -> String {
    match error {
        SyncFailure::Invalid(findings) => findings.join("; "),
        SyncFailure::Conflict(error)
        | SyncFailure::Unavailable(error)
        | SyncFailure::OutcomeUnknown(error)
        | SyncFailure::PostCommit(error)
        | SyncFailure::Error(error) => error,
    }
}

fn classify_projection_failure(error: ProjectionFetchFailure) -> SyncFailure {
    match error {
        ProjectionFetchFailure::Unavailable(error) => SyncFailure::Unavailable(error),
        ProjectionFetchFailure::Status { status, code } => SyncFailure::Error(format!(
            "projection request failed: status={status} code={code}"
        )),
        ProjectionFetchFailure::Invalid(error) => SyncFailure::Error(error),
    }
}

fn compile(
    args: &[String],
) -> Result<(Uuid, PathBuf, String, usize, Option<PathBuf>), CompileFailure> {
    let args = parse_compile_args(args).map_err(CompileFailure::Error)?;
    let output = inspect_output(&args.out)?;
    if let OutputState::Existing(previous) = &output {
        validate_manifest_binding(&previous.manifest, &args).map_err(CompileFailure::Dirty)?;
    }
    let projection =
        fetch_projection(&args).map_err(|error| CompileFailure::Error(error.to_string()))?;
    validate_response_binding(&projection, &args).map_err(CompileFailure::Error)?;
    if let OutputState::Existing(previous) = &output {
        validate_manifest_response_context(&previous.manifest, &projection)
            .map_err(CompileFailure::Dirty)?;
    }
    let rendered = render_projection(&projection).map_err(CompileFailure::Error)?;
    output.revalidate().map_err(CompileFailure::Dirty)?;
    let recovery =
        replace_projection(&args.out, &output, &rendered).map_err(CompileFailure::Error)?;
    Ok((
        args.scope_id,
        args.out,
        projection.fingerprint,
        projection.items.len(),
        recovery,
    ))
}

fn parse_compile_args(args: &[String]) -> Result<CompileArgs, String> {
    parse_context_args(args)
}

fn parse_sync_args(args: &[String]) -> Result<SyncArgs, String> {
    let mut context = Vec::new();
    let mut apply = false;
    let mut index = 0;
    while index < args.len() {
        if args[index] == "--apply" {
            if apply {
                return Err("duplicate flag --apply".to_string());
            }
            apply = true;
            index += 1;
        } else {
            let value = args
                .get(index + 1)
                .ok_or_else(|| format!("missing value for {}", args[index]))?;
            context.push(args[index].clone());
            context.push(value.clone());
            index += 2;
        }
    }
    Ok(SyncArgs {
        context: parse_context_args(&context)?,
        apply,
    })
}

fn parse_context_args(args: &[String]) -> Result<CompileArgs, String> {
    let mut flags = BTreeMap::new();
    let mut index = 0;
    while index < args.len() {
        let name = args[index]
            .strip_prefix("--")
            .ok_or_else(|| format!("unexpected positional argument {}", args[index]))?;
        let value = args
            .get(index + 1)
            .filter(|value| !value.starts_with("--"))
            .ok_or_else(|| format!("missing value for --{name}"))?;
        if !matches!(
            name,
            "subject-id" | "scope" | "actor" | "agent-node" | "subject-generation" | "out"
        ) {
            return Err(format!("unknown flag --{name}"));
        }
        if flags.insert(name.to_string(), value.clone()).is_some() {
            return Err(format!("duplicate flag --{name}"));
        }
        index += 2;
    }
    let required = |name: &str| {
        flags
            .get(name)
            .map(String::as_str)
            .ok_or_else(|| format!("missing required flag --{name}"))
    };
    let parse_uuid = |name: &str| {
        Uuid::parse_str(required(name)?).map_err(|_| format!("--{name} must be a canonical UUID"))
    };
    Ok(CompileArgs {
        subject_id: parse_uuid("subject-id")?,
        scope_id: parse_uuid("scope")?,
        actor_id: parse_uuid("actor")?,
        agent_node_id: parse_uuid("agent-node")?,
        subject_generation: required("subject-generation")?
            .parse()
            .map_err(|error| format!("--subject-generation: {error}"))?,
        out: PathBuf::from(required("out")?),
    })
}

fn http_agent() -> Result<ureq::Agent, String> {
    let timeout_ms = match env::var("MEMPHANT_HTTP_TIMEOUT_MS") {
        Ok(value) => value.parse::<u64>().map_err(|_| {
            "MEMPHANT_HTTP_TIMEOUT_MS must be an integer number of milliseconds".to_string()
        })?,
        Err(env::VarError::NotPresent) => DEFAULT_HTTP_TIMEOUT_MS,
        Err(env::VarError::NotUnicode(_)) => {
            return Err("MEMPHANT_HTTP_TIMEOUT_MS must be valid UTF-8".to_string());
        }
    };
    if !(1..=MAX_HTTP_TIMEOUT_MS).contains(&timeout_ms) {
        return Err(format!(
            "MEMPHANT_HTTP_TIMEOUT_MS must be between 1 and {MAX_HTTP_TIMEOUT_MS} milliseconds"
        ));
    }
    Ok(ureq::Agent::config_builder()
        .http_status_as_error(false)
        .timeout_global(Some(Duration::from_millis(timeout_ms)))
        .build()
        .into())
}

fn fetch_projection(
    args: &CompileArgs,
) -> Result<CanonicalProjectionResponse, ProjectionFetchFailure> {
    let base = env::var("MEMPHANT_URL")
        .ok()
        .filter(|url| !url.trim().is_empty())
        .unwrap_or_else(|| DEFAULT_URL.to_string());
    let url = format!(
        "{}/v1/scopes/{}/projection?subject_id={}&actor_id={}&agent_node_id={}&subject_generation={}",
        base.trim_end_matches('/'),
        args.scope_id,
        args.subject_id,
        args.actor_id,
        args.agent_node_id,
        args.subject_generation
    );
    let agent = http_agent().map_err(ProjectionFetchFailure::Invalid)?;
    let mut request = agent.get(&url);
    if let Ok(key) = env::var("MEMPHANT_API_KEY")
        && !key.is_empty()
    {
        request = request.header("authorization", format!("Bearer {key}"));
    }
    let mut response = request.call().map_err(|error| {
        ProjectionFetchFailure::Unavailable(format!("projection request unavailable: {error}"))
    })?;
    let status = response.status().as_u16();
    if status != 200 {
        let body: Value = response
            .body_mut()
            .read_json()
            .unwrap_or_else(|_| serde_json::json!({}));
        let code = body
            .pointer("/error/code")
            .and_then(Value::as_str)
            .unwrap_or("remote_error");
        return if status >= 500 {
            Err(ProjectionFetchFailure::Unavailable(format!(
                "projection request unavailable: status={status} code={code}"
            )))
        } else {
            Err(ProjectionFetchFailure::Status {
                status,
                code: code.to_string(),
            })
        };
    }
    response.body_mut().read_json().map_err(|error| {
        ProjectionFetchFailure::Invalid(format!("projection response was not valid JSON: {error}"))
    })
}

fn inspect_sync_output(root: &Path) -> Result<SyncState, SyncFailure> {
    let handles = TreeHandles::open(root)
        .map_err(|error| SyncFailure::Invalid(vec![format!("output root: {error}")]))?;
    let snapshot = scan_sync_handles(&handles).map_err(SyncFailure::Invalid)?;
    Ok(SyncState { handles, snapshot })
}

fn revalidate_sync_state(state: &SyncState) -> Result<(), SyncFailure> {
    let current = scan_sync_handles(&state.handles).map_err(SyncFailure::Invalid)?;
    if current == state.snapshot {
        Ok(())
    } else {
        Err(SyncFailure::Invalid(vec![
            "local projection changed during sync; rerun sync from the new tree".to_string(),
        ]))
    }
}

fn scan_sync_handles(handles: &TreeHandles) -> Result<SyncSnapshot, Vec<String>> {
    let mut findings = Vec::new();
    if let Err(error) = handles.ensure_bound() {
        return Err(vec![error]);
    }

    let (manifest_bytes, manifest_identity) =
        read_regular_at(&handles.root, MANIFEST_FILE, MAX_MANIFEST_BYTES)
            .map_err(|error| vec![format!("{MANIFEST_FILE}: {error}")])?;
    let manifest = strict_from_slice::<ExportManifest>(&manifest_bytes)
        .map_err(|error| vec![format!("{MANIFEST_FILE}: invalid JSON: {error}")])?;
    validate_manifest_fields(&manifest, &mut findings);
    match canonical_manifest_bytes(&manifest) {
        Ok(canonical) if canonical != manifest_bytes => findings.push(format!(
            "{MANIFEST_FILE}: bytes are not the canonical generated serialization"
        )),
        Err(error) => findings.push(format!("{MANIFEST_FILE}: {error}")),
        _ => {}
    }
    let mut managed_files = BTreeMap::from([(
        MANIFEST_FILE.to_string(),
        ManagedFileSnapshot {
            identity: manifest_identity,
            bytes: manifest_bytes,
        },
    )]);

    match read_regular_at(&handles.root, MEMORY_FILE, MAX_MANAGED_FILE_BYTES) {
        Ok((bytes, identity)) => {
            if sha256(&bytes) != manifest.memory_sha256
                || Uuid::parse_str(&manifest.scope_id)
                    .ok()
                    .map(|scope| render_memory(scope, &manifest.snapshot_sha256, &manifest.entries))
                    .as_deref()
                    != Some(bytes.as_slice())
            {
                findings.push(format!(
                    "{MEMORY_FILE}: immutable generated index differs from manifest"
                ));
            }
            managed_files.insert(
                MEMORY_FILE.to_string(),
                ManagedFileSnapshot { identity, bytes },
            );
        }
        Err(error) => findings.push(format!("{MEMORY_FILE}: {error}")),
    }

    let mut operations = Vec::new();
    let mut expected_units = BTreeSet::new();
    let mut operation_fact_keys = BTreeSet::new();
    for entry in &manifest.entries {
        let expected_path = format!("{UNITS_DIR}/{}.md", entry.unit_id);
        if entry.path != expected_path || !is_safe_relative_unit_path(&entry.path) {
            findings.push(format!("{}: path must be {expected_path}", entry.path));
            continue;
        }
        let name = format!("{}.md", entry.unit_id);
        expected_units.insert(name.clone());
        let Ok(unit_id) = Uuid::parse_str(&entry.unit_id) else {
            continue;
        };
        let base = file_sync_metadata(entry, UnitId::from_u128(unit_id.as_u128()));
        match handles.units.symlink_metadata(&name) {
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                if let Some(fact_key) = &entry.fact_key {
                    operation_fact_keys.insert(fact_key.clone());
                }
                operations.push(FileSyncOperation::Forget { base });
            }
            Err(error) => findings.push(format!("{}: cannot inspect: {error}", entry.path)),
            Ok(metadata) if !metadata.is_file() || metadata.is_symlink() => findings.push(format!(
                "{}: expected a non-symlink regular file",
                entry.path
            )),
            Ok(_) => match read_regular_at(&handles.units, &name, MAX_MANAGED_FILE_BYTES) {
                Err(error) => findings.push(format!("{}: {error}", entry.path)),
                Ok((bytes, identity)) => {
                    let parsed = parse_unit(&bytes);
                    managed_files.insert(
                        entry.path.clone(),
                        ManagedFileSnapshot {
                            identity,
                            bytes: bytes.clone(),
                        },
                    );
                    match parsed {
                        Err(error) => findings.push(format!("{}: {error}", entry.path)),
                        Ok((body, footer)) => {
                            let mut footer_findings = Vec::new();
                            validate_footer(
                                entry,
                                manifest.subject_generation,
                                &footer,
                                &mut footer_findings,
                            );
                            findings.extend(footer_findings);
                            let expected = CanonicalProjectionUnit {
                                unit_id: base.unit_id,
                                kind: entry.kind,
                                fact_key: entry.fact_key.clone(),
                                predicate: entry.predicate.clone(),
                                body: body.clone(),
                                confidence: entry.confidence,
                                state: entry.state,
                                source_span: entry.source_span.clone(),
                                valid_from: entry.valid_from.clone(),
                                valid_to: entry.valid_to.clone(),
                                body_sha256: entry.body_sha256.clone(),
                            };
                            match render_unit(manifest.subject_generation, &expected) {
                                Ok(expected_bytes) if expected_bytes != bytes => {
                                    findings.push(format!(
                                        "{}: only the exact semantic body may change",
                                        entry.path
                                    ))
                                }
                                Err(error) => findings.push(format!("{}: {error}", entry.path)),
                                _ => {}
                            }
                            if sha256(body.as_bytes()) != entry.body_sha256 {
                                if body.trim().is_empty() {
                                    findings.push(format!(
                                        "{}: corrected body must not be blank",
                                        entry.path
                                    ));
                                } else {
                                    if let Some(fact_key) = &entry.fact_key {
                                        operation_fact_keys.insert(fact_key.clone());
                                    }
                                    operations.push(FileSyncOperation::Correct { base, body });
                                }
                            } else if sha256(&bytes) != entry.file_sha256 {
                                findings.push(format!(
                                    "{}: bytes differ without a semantic body change",
                                    entry.path
                                ));
                            }
                        }
                    }
                }
            },
        }
    }

    let units_names = directory_names(&handles.units).unwrap_or_else(|error| {
        findings.push(format!("{UNITS_DIR}: {error}"));
        Vec::new()
    });
    for name in &units_names {
        if !expected_units.contains(name) {
            findings.push(format!("{UNITS_DIR}/{name}: unexpected path"));
        }
    }

    let manifest_by_fact_key = manifest
        .entries
        .iter()
        .filter_map(|entry| entry.fact_key.as_ref().map(|key| (key, entry)))
        .collect::<BTreeMap<_, _>>();
    let mut inbox_files = BTreeMap::new();
    let mut inbox_fact_keys = BTreeSet::new();
    let inbox_names = directory_names(&handles.inbox).unwrap_or_else(|error| {
        findings.push(format!("{INBOX_DIR}: {error}"));
        Vec::new()
    });
    for name in &inbox_names {
        let path = format!("{INBOX_DIR}/{name}");
        if !is_safe_inbox_name(name) {
            findings.push(format!("{path}: unexpected or reserved path"));
            continue;
        }
        match read_regular_at(&handles.inbox, name, MAX_MANAGED_FILE_BYTES) {
            Err(error) => findings.push(format!("{path}: {error}")),
            Ok((bytes, identity)) => {
                inbox_files.insert(
                    path.clone(),
                    ManagedFileSnapshot {
                        identity,
                        bytes: bytes.clone(),
                    },
                );
                match parse_inbox(&bytes) {
                    Err(error) => findings.push(format!("{path}: {error}")),
                    Ok((fact_key, body)) => {
                        if !inbox_fact_keys.insert(fact_key.clone()) {
                            findings.push(format!("{path}: duplicate inbox fact_key {fact_key}"));
                            continue;
                        }
                        if operation_fact_keys.contains(&fact_key) {
                            findings.push(format!(
                                "{path}: fact_key {fact_key} is already corrected or forgotten by this plan"
                            ));
                            continue;
                        }
                        let inherited = manifest_by_fact_key.get(&fact_key);
                        operations.push(FileSyncOperation::Retain {
                            fact_key,
                            predicate: inherited
                                .and_then(|entry| entry.predicate.clone())
                                .unwrap_or_else(|| "states".to_string()),
                            body,
                            confidence: inherited.and_then(|entry| entry.confidence).unwrap_or(1.0),
                            valid_from: None,
                            valid_to: None,
                        });
                    }
                }
            }
        }
    }

    let root_names = directory_names(&handles.root).unwrap_or_else(|error| {
        findings.push(format!("output root: {error}"));
        Vec::new()
    });
    collect_name_mismatches(
        &root_names,
        &BTreeSet::from([
            MEMORY_FILE.to_string(),
            MANIFEST_FILE.to_string(),
            UNITS_DIR.to_string(),
            INBOX_DIR.to_string(),
        ]),
        false,
        ".",
        &mut findings,
    );
    if let Err(error) = handles.ensure_bound() {
        findings.push(error);
    }
    if findings.is_empty() {
        Ok(SyncSnapshot {
            manifest,
            managed_files,
            inbox_files,
            operations,
        })
    } else {
        findings.sort();
        findings.dedup();
        Err(findings)
    }
}

fn parse_inbox(bytes: &[u8]) -> Result<(String, String), String> {
    let text = std::str::from_utf8(bytes).map_err(|_| "file is not UTF-8".to_string())?;
    if text.contains('\r') {
        return Err("inbox files must use LF line endings".to_string());
    }
    if !text.ends_with('\n') || text.ends_with("\n\n") {
        return Err("inbox file must end in exactly one LF".to_string());
    }
    if text.contains("<!-- memphant") {
        return Err("inbox file must be footer-free".to_string());
    }
    let (heading, body) = text
        .strip_suffix('\n')
        .and_then(|text| text.split_once("\n\n"))
        .ok_or_else(|| "inbox file must contain an H1, one blank line, then a body".to_string())?;
    let fact_key = heading
        .strip_prefix("# ")
        .ok_or_else(|| "inbox file must start with one H1 fact key".to_string())?;
    require_single_line("inbox fact key", fact_key)?;
    if fact_key.trim() != fact_key {
        return Err("inbox fact key must not contain surrounding whitespace".to_string());
    }
    if body.starts_with('\n')
        || body
            .lines()
            .next()
            .is_some_and(|line| line.trim().is_empty())
    {
        return Err(
            "inbox body must begin immediately after exactly one blank separator line".to_string(),
        );
    }
    if body.trim().is_empty() {
        return Err("inbox body must not be blank".to_string());
    }
    Ok((fact_key.to_string(), body.to_string()))
}

fn file_sync_metadata(entry: &ManifestEntry, unit_id: UnitId) -> FileSyncUnitMetadata {
    FileSyncUnitMetadata {
        unit_id,
        kind: entry.kind,
        fact_key: entry.fact_key.clone(),
        predicate: entry.predicate.clone(),
        confidence: entry.confidence,
        valid_from: entry.valid_from.clone(),
        valid_to: entry.valid_to.clone(),
        body_sha256: entry.body_sha256.clone(),
    }
}

fn validate_sync_base(
    manifest: &ExportManifest,
    projection: &CanonicalProjectionResponse,
) -> Result<(), SyncFailure> {
    validate_manifest_response_context(manifest, projection).map_err(SyncFailure::Invalid)?;
    if manifest.snapshot_sha256 != projection.fingerprint {
        return Err(SyncFailure::Conflict(format!(
            "manifest base {} no longer matches canonical projection {}",
            manifest.snapshot_sha256, projection.fingerprint
        )));
    }
    let canonical = render_projection(projection).map_err(|error| {
        SyncFailure::Invalid(vec![format!(
            "canonical base projection cannot be rendered: {error}"
        )])
    })?;
    if manifest != &canonical.manifest {
        return Err(SyncFailure::Invalid(vec![
            "manifest differs from the complete canonical base projection".to_string(),
        ]));
    }
    Ok(())
}

fn sync_plan(snapshot: &SyncSnapshot) -> Result<SyncPlan, String> {
    let plan_sha256 =
        file_sync_plan_sha256(&snapshot.operations).map_err(|error| error.to_string())?;
    Ok(SyncPlan {
        schema_version: SCHEMA_VERSION,
        subject_id: snapshot.manifest.subject_id.clone(),
        scope_id: snapshot.manifest.scope_id.clone(),
        actor_id: snapshot.manifest.actor_id.clone(),
        agent_node_id: snapshot.manifest.agent_node_id.clone(),
        subject_generation: snapshot.manifest.subject_generation,
        base_fingerprint: snapshot.manifest.snapshot_sha256.clone(),
        plan_sha256,
        operations: snapshot.operations.clone(),
        destructive: snapshot
            .operations
            .iter()
            .filter_map(|operation| match operation {
                FileSyncOperation::Forget { base } => {
                    Some(format!("forget:{}", base.unit_id.as_uuid()))
                }
                _ => None,
            })
            .collect(),
        consumed_inbox: snapshot.inbox_files.keys().cloned().collect(),
    })
}

fn post_file_sync(
    request: &FileSyncRequest,
    encoded: &[u8],
    idempotency_key: &str,
) -> Result<FileSyncResult, SyncFailure> {
    let base = env::var("MEMPHANT_URL")
        .ok()
        .filter(|url| !url.trim().is_empty())
        .unwrap_or_else(|| DEFAULT_URL.to_string());
    let url = format!("{}/v1/file-sync", base.trim_end_matches('/'));
    let agent = http_agent().map_err(SyncFailure::Error)?;
    let mut outbound = agent
        .post(&url)
        .header("idempotency-key", idempotency_key)
        .header("content-type", "application/json");
    if let Ok(key) = env::var("MEMPHANT_API_KEY")
        && !key.is_empty()
    {
        outbound = outbound.header("authorization", format!("Bearer {key}"));
    }
    let mut response = outbound.send(encoded).map_err(|error| {
        SyncFailure::OutcomeUnknown(format!(
            "file-sync transport failed for plan {}: {error}",
            request.plan_sha256
        ))
    })?;
    let status = response.status().as_u16();
    if status != 200 {
        let body: Value = response
            .body_mut()
            .read_json()
            .unwrap_or_else(|_| serde_json::json!({}));
        let code = body
            .pointer("/error/code")
            .and_then(Value::as_str)
            .unwrap_or("remote_error");
        let message = body
            .pointer("/error/message")
            .and_then(Value::as_str)
            .unwrap_or("file-sync request failed");
        if (200..300).contains(&status) {
            return Err(SyncFailure::OutcomeUnknown(format!(
                "file-sync returned non-contract success status={status} plan={}",
                request.plan_sha256
            )));
        }
        return match code {
            "sync_conflict" => Err(SyncFailure::Conflict(message.to_string())),
            "sync_invalid" => Err(SyncFailure::Invalid(vec![message.to_string()])),
            _ if status >= 500 => Err(SyncFailure::OutcomeUnknown(format!(
                "file-sync server failed after dispatch: status={status} code={code} plan={}",
                request.plan_sha256
            ))),
            _ => Err(SyncFailure::Error(format!(
                "file-sync request failed: status={status} code={code}"
            ))),
        };
    }
    response.body_mut().read_json().map_err(|error| {
        SyncFailure::OutcomeUnknown(format!(
            "file-sync returned 200 with an invalid receipt for plan {}: {error}",
            request.plan_sha256
        ))
    })
}

fn encode_file_sync_request(request: &FileSyncRequest) -> Result<Vec<u8>, SyncFailure> {
    let encoded = serde_json::to_vec(request)
        .map_err(|error| SyncFailure::Error(format!("cannot encode file-sync request: {error}")))?;
    if encoded.len() > MAX_FILE_SYNC_REQUEST_ENCODED_BYTES {
        return Err(SyncFailure::Invalid(vec![format!(
            "file-sync request is {} bytes and exceeds the {} byte limit",
            encoded.len(),
            MAX_FILE_SYNC_REQUEST_ENCODED_BYTES
        )]));
    }
    Ok(encoded)
}

fn validate_sync_receipt(
    request: &FileSyncRequest,
    receipt: &FileSyncResult,
) -> Result<(), String> {
    if receipt.base_fingerprint != request.base_fingerprint
        || receipt.plan_sha256 != request.plan_sha256
        || receipt.operations.len() != request.operations.len()
    {
        return Err("committed receipt does not match the submitted batch".to_string());
    }
    require_sha256("receipt fingerprint", &receipt.fingerprint)?;
    require_rfc3339_utc("committed receipt evaluated_at", &receipt.evaluated_at)?;
    let mut created_ids = BTreeSet::new();
    for (operation, result) in request.operations.iter().zip(&receipt.operations) {
        let matches = match (operation, result) {
            (
                FileSyncOperation::Correct { base, .. },
                FileSyncOperationResult::Correct {
                    memory_unit_id,
                    created,
                },
            ) => {
                !created.is_empty()
                    && created.iter().all(|id| created_ids.insert(id.as_uuid()))
                    && base.unit_id == *memory_unit_id
            }
            (FileSyncOperation::Retain { .. }, FileSyncOperationResult::Retain { created }) => {
                !created.is_empty() && created.iter().all(|id| created_ids.insert(id.as_uuid()))
            }
            (
                FileSyncOperation::Forget { base },
                FileSyncOperationResult::Forget { memory_unit_id, .. },
            ) => base.unit_id == *memory_unit_id,
            _ => false,
        };
        if !matches {
            return Err("committed receipt operation variants do not match the plan".to_string());
        }
    }
    Ok(())
}

fn validate_response_binding(
    response: &CanonicalProjectionResponse,
    args: &CompileArgs,
) -> Result<(), String> {
    let bindings = [
        ("subject_id", response.subject_id.as_uuid(), args.subject_id),
        ("actor_id", response.actor_id.as_uuid(), args.actor_id),
        ("scope_id", response.scope_id.as_uuid(), args.scope_id),
        (
            "agent_node_id",
            response.agent_node_id.as_uuid(),
            args.agent_node_id,
        ),
    ];
    for (field, actual, expected) in bindings {
        require_binding(field, actual, expected)?;
    }
    if response.subject_generation != args.subject_generation {
        return Err(format!(
            "projection context mismatch: subject_generation expected={} actual={}",
            args.subject_generation, response.subject_generation
        ));
    }
    require_rfc3339_utc("projection evaluated_at", &response.evaluated_at)?;
    require_sha256("projection fingerprint", &response.fingerprint)?;
    let actual = canonical_projection_fingerprint(&response.items)
        .map_err(|error| format!("cannot fingerprint projection response: {error}"))?;
    if response.fingerprint != actual {
        return Err(format!(
            "projection fingerprint mismatch: expected={} actual={actual}",
            response.fingerprint
        ));
    }
    Ok(())
}

fn require_binding(field: &str, actual: Uuid, expected: Uuid) -> Result<(), String> {
    if actual == expected {
        Ok(())
    } else {
        Err(format!(
            "projection context mismatch: {field} expected={expected} actual={actual}"
        ))
    }
}

fn validate_manifest_binding(
    manifest: &ExportManifest,
    args: &CompileArgs,
) -> Result<(), Vec<String>> {
    let expected = [
        ("subject_id", &manifest.subject_id, args.subject_id),
        ("actor_id", &manifest.actor_id, args.actor_id),
        ("scope_id", &manifest.scope_id, args.scope_id),
        ("agent_node_id", &manifest.agent_node_id, args.agent_node_id),
    ];
    let mut findings = Vec::new();
    for (field, actual, expected) in expected {
        if actual != &expected.to_string() {
            findings.push(format!(
                "manifest context mismatch: {field} expected={expected} actual={actual}"
            ));
        }
    }
    if manifest.subject_generation != args.subject_generation {
        findings.push(format!(
            "manifest context mismatch: subject_generation expected={} actual={}",
            args.subject_generation, manifest.subject_generation
        ));
    }
    if findings.is_empty() {
        Ok(())
    } else {
        Err(findings)
    }
}

fn validate_manifest_response_context(
    manifest: &ExportManifest,
    response: &CanonicalProjectionResponse,
) -> Result<(), Vec<String>> {
    let expected = [
        (
            "tenant_id",
            &manifest.tenant_id,
            response.tenant_id.as_uuid(),
        ),
        (
            "subject_id",
            &manifest.subject_id,
            response.subject_id.as_uuid(),
        ),
        ("actor_id", &manifest.actor_id, response.actor_id.as_uuid()),
        ("scope_id", &manifest.scope_id, response.scope_id.as_uuid()),
        (
            "agent_node_id",
            &manifest.agent_node_id,
            response.agent_node_id.as_uuid(),
        ),
    ];
    let findings = expected
        .into_iter()
        .filter(|(_, actual, expected)| *actual != &expected.to_string())
        .map(|(field, actual, expected)| {
            format!(
                "server context differs from manifest: {field} expected={actual} actual={expected}"
            )
        })
        .collect::<Vec<_>>();
    if findings.is_empty() {
        Ok(())
    } else {
        Err(findings)
    }
}

fn render_projection(response: &CanonicalProjectionResponse) -> Result<RenderedProjection, String> {
    let mut units = BTreeMap::new();
    let mut entries = Vec::new();
    let mut ids = BTreeSet::new();
    let mut fact_keys = BTreeSet::new();
    let mut last_id = None;
    for item in &response.items {
        let id = item.unit_id.as_uuid().to_string();
        if last_id.as_ref().is_some_and(|previous| previous >= &id) {
            return Err("projection items are not strictly ordered by unit_id".to_string());
        }
        last_id = Some(id.clone());
        if !ids.insert(id.clone()) {
            return Err(format!("projection contains duplicate unit_id {id}"));
        }
        if let Some(fact_key) = &item.fact_key
            && !fact_keys.insert(fact_key.clone())
        {
            return Err(format!("projection contains duplicate fact_key {fact_key}"));
        }
        if !matches!(item.kind, MemoryKind::Semantic | MemoryKind::Procedural) {
            return Err(format!(
                "projection contains unsupported kind for unit {id}"
            ));
        }
        require_single_line("fact_key", item.fact_key.as_deref().unwrap_or(&id))?;
        require_sha256("body_sha256", &item.body_sha256)?;
        let actual_body_sha = sha256(item.body.as_bytes());
        if item.body_sha256 != actual_body_sha {
            return Err(format!("projection body hash mismatch for unit {id}"));
        }
        let path = format!("{UNITS_DIR}/{id}.md");
        let bytes = render_unit(response.subject_generation, item)?;
        let file_sha256 = sha256(&bytes);
        units.insert(path.clone(), bytes);
        entries.push(ManifestEntry {
            unit_id: id,
            path,
            kind: item.kind,
            fact_key: item.fact_key.clone(),
            predicate: item.predicate.clone(),
            confidence: item.confidence,
            valid_from: item.valid_from.clone(),
            valid_to: item.valid_to.clone(),
            state: item.state,
            source_span: item.source_span.clone(),
            body_sha256: item.body_sha256.clone(),
            file_sha256,
        });
    }

    let memory = render_memory(response.scope_id.as_uuid(), &response.fingerprint, &entries);
    let manifest = ExportManifest {
        schema_version: SCHEMA_VERSION,
        compiler_version: COMPILER_VERSION.to_string(),
        tenant_id: response.tenant_id.as_uuid().to_string(),
        subject_id: response.subject_id.as_uuid().to_string(),
        actor_id: response.actor_id.as_uuid().to_string(),
        scope_id: response.scope_id.as_uuid().to_string(),
        agent_node_id: response.agent_node_id.as_uuid().to_string(),
        subject_generation: response.subject_generation,
        snapshot_sha256: response.fingerprint.clone(),
        memory_sha256: sha256(&memory),
        entries,
    };
    let mut findings = Vec::new();
    validate_manifest_fields(&manifest, &mut findings);
    if !findings.is_empty() {
        findings.sort();
        findings.dedup();
        return Err(format!(
            "projection metadata is invalid: {}",
            findings.join("; ")
        ));
    }
    let manifest_bytes = canonical_manifest_bytes(&manifest)?;
    Ok(RenderedProjection {
        memory,
        units,
        manifest,
        manifest_bytes,
    })
}

fn canonical_manifest_bytes(manifest: &ExportManifest) -> Result<Vec<u8>, String> {
    let mut bytes = serde_json::to_vec_pretty(manifest)
        .map_err(|error| format!("manifest serialization failed: {error}"))?;
    bytes.push(b'\n');
    Ok(bytes)
}

fn render_unit(generation: u64, item: &CanonicalProjectionUnit) -> Result<Vec<u8>, String> {
    let title = item
        .fact_key
        .as_deref()
        .map(str::to_string)
        .unwrap_or_else(|| item.unit_id.as_uuid().to_string());
    require_single_line("unit title", &title)?;
    let footer = UnitFooter {
        unit_id: item.unit_id.as_uuid().to_string(),
        body_sha256: item.body_sha256.clone(),
        subject_generation: generation,
        kind: item.kind,
        fact_key: item.fact_key.clone(),
        predicate: item.predicate.clone(),
        confidence: item.confidence,
        state: item.state,
        valid_from: item.valid_from.clone(),
        valid_to: item.valid_to.clone(),
        source_span: item.source_span.clone(),
    };
    let footer = serde_json::to_string(&footer)
        .map_err(|error| format!("footer serialization failed: {error}"))?;
    Ok(format!("# {title}\n\n{}\n\n<!-- memphant {footer} -->\n", item.body).into_bytes())
}

/// D2. The index is a table, not a bullet list: state, the valid interval and
/// confidence are what make a memory file reviewable, and every one of them was
/// already sitting in `ManifestEntry`. Nothing here filters — the projection
/// already excludes superseded and out-of-interval rows by construction, so the
/// moment supersession fires a dead rule leaves this file on its own. The
/// columns explain what survived, they do not decide it.
fn render_memory(scope_id: Uuid, fingerprint: &str, entries: &[ManifestEntry]) -> Vec<u8> {
    let mut memory =
        format!("# MemPhant Memory\n\nScope: `{scope_id}`\nSnapshot: `{fingerprint}`\n\n");
    if entries.is_empty() {
        return memory.into_bytes();
    }
    memory.push_str("| Memory | State | Since | Until | Confidence |\n");
    memory.push_str("| --- | --- | --- | --- | --- |\n");
    for entry in entries {
        let title = entry.fact_key.as_deref().unwrap_or(&entry.unit_id);
        memory.push_str(&format!(
            "| [{}]({}) | {} | {} | {} | {} |\n",
            escape_link_text(title),
            entry.path,
            escape_cell(&state_label(entry.state)),
            escape_cell(entry.valid_from.as_deref().unwrap_or("—")),
            escape_cell(entry.valid_to.as_deref().unwrap_or("—")),
            entry
                .confidence
                .map_or_else(|| "—".to_string(), |value| format!("{value:.2}")),
        ));
    }
    memory.into_bytes()
}

/// The wire name of a unit state, taken from its own `Serialize` impl rather
/// than a hand-written match: a second mapping would drift from the enum's
/// `rename_all = "snake_case"` the first time a variant is added or renamed,
/// and the footer already writes states under those exact names.
fn state_label(state: UnitState) -> String {
    serde_json::to_string(&state)
        .map(|quoted| quoted.trim_matches('"').to_string())
        .unwrap_or_default()
}

/// A table cell may not contain a `|` or a newline, or it splits the row. The
/// projection's own validators already reject newlines in these fields; the
/// pipe escape is belt-and-braces for values that arrive from a store.
fn escape_cell(value: &str) -> String {
    value
        .replace('\\', "\\\\")
        .replace('|', "\\|")
        .replace(['\n', '\r'], " ")
}

fn escape_link_text(value: &str) -> String {
    value
        .replace('\\', "\\\\")
        .replace('[', "\\[")
        .replace(']', "\\]")
}

impl TreeHandles {
    fn open(root: &Path) -> Result<Self, String> {
        let (mut current, components, anchor) = output_anchor(root)?;
        let anchor_parent = current.try_clone().map_err(|error| error.to_string())?;
        for (index, name) in components.iter().enumerate() {
            if index + 1 == components.len() {
                let root_dir = open_directory_at(&current, name)
                    .map_err(|error| format!("output root: {error}"))?;
                return Self::from_root(current, name.clone(), root_dir, anchor_parent, anchor);
            }
            current = open_directory_at(&current, name)
                .map_err(|error| format!("output component {name}: {error}"))?;
        }
        Err("output root may not be the filesystem root".to_string())
    }

    fn from_root(
        parent: Dir,
        root_name: String,
        root: Dir,
        anchor_parent: Dir,
        anchor: OutputParentAnchor,
    ) -> Result<Self, String> {
        let units =
            open_directory_at(&root, UNITS_DIR).map_err(|error| format!("{UNITS_DIR}: {error}"))?;
        let inbox =
            open_directory_at(&root, INBOX_DIR).map_err(|error| format!("{INBOX_DIR}: {error}"))?;
        let handles = Self {
            anchor,
            anchor_parent,
            outer_bindings: Vec::new(),
            parent_identity: directory_identity(&parent)?,
            root_identity: directory_identity(&root)?,
            units_identity: directory_identity(&units)?,
            inbox_identity: directory_identity(&inbox)?,
            parent,
            root_name,
            root,
            units,
            inbox,
        };
        handles.ensure_bound()?;
        Ok(handles)
    }

    fn ensure_bound(&self) -> Result<(), String> {
        self.anchor.ensure_current(&self.anchor_parent)?;
        for binding in &self.outer_bindings {
            let actual = directory_identity_at(&binding.parent, &binding.name)
                .map_err(|error| format!("output ancestor {} changed: {error}", binding.name))?;
            if actual != binding.identity {
                return Err(format!(
                    "output ancestor {} no longer names the installed directory",
                    binding.name
                ));
            }
        }
        if directory_identity(&self.parent)? != self.parent_identity {
            return Err("output parent handle changed identity".to_string());
        }
        let root = directory_identity_at(&self.parent, &self.root_name)
            .map_err(|error| format!("output root path changed: {error}"))?;
        let units = directory_identity_at(&self.root, UNITS_DIR)
            .map_err(|error| format!("{UNITS_DIR} path changed: {error}"))?;
        let inbox = directory_identity_at(&self.root, INBOX_DIR)
            .map_err(|error| format!("{INBOX_DIR} path changed: {error}"))?;
        if root != self.root_identity {
            return Err("output root path no longer names the validated directory".to_string());
        }
        if units != self.units_identity {
            return Err("units path no longer names the validated directory".to_string());
        }
        if inbox != self.inbox_identity {
            return Err("inbox path no longer names the validated directory".to_string());
        }
        Ok(())
    }
}

impl OutputParentAnchor {
    fn ensure_current(&self, retained_parent: &Dir) -> Result<(), String> {
        let retained_identity = directory_identity(retained_parent)
            .map_err(|error| format!("output parent changed: {error}"))?;
        if retained_identity != self.identity {
            return Err(
                "output parent changed: retained handle identity differs from inspection"
                    .to_string(),
            );
        }
        let reopened = open_absolute_directory_nofollow(&self.path)
            .map_err(|error| format!("output parent changed: {error}"))?;
        let reopened_identity = directory_identity(&reopened)
            .map_err(|error| format!("output parent changed: {error}"))?;
        if reopened_identity != retained_identity {
            return Err(
                "output parent changed: captured path no longer names the retained parent"
                    .to_string(),
            );
        }
        Ok(())
    }
}

fn open_absolute_directory_nofollow(path: &Path) -> Result<Dir, String> {
    open_absolute_directory_nofollow_with_hook(path, |_| {})
}

fn absolute_directory_components(path: &Path) -> Result<(PathBuf, Vec<OsString>), String> {
    if !path.is_absolute() {
        return Err("captured output parent is not absolute".to_string());
    }
    let mut root = PathBuf::new();
    let mut names = Vec::new();
    let mut saw_root = false;
    for component in path.components() {
        match component {
            Component::Prefix(_) if names.is_empty() && !saw_root => {
                root.push(component.as_os_str());
            }
            Component::RootDir if names.is_empty() => {
                root.push(component.as_os_str());
                saw_root = true;
            }
            Component::Normal(name) => names.push(name.to_os_string()),
            Component::CurDir => {}
            Component::Prefix(_) | Component::RootDir | Component::ParentDir => {
                return Err("captured output parent contains traversal".to_string());
            }
        }
    }
    if root.as_os_str().is_empty() || !saw_root {
        return Err("captured output parent has no filesystem root".to_string());
    }
    Ok((root, names))
}

fn open_absolute_directory_nofollow_with_hook(
    path: &Path,
    mut after_open: impl FnMut(&std::ffi::OsStr),
) -> Result<Dir, String> {
    let (root, names) = absolute_directory_components(path)?;
    let mut current = Dir::open_ambient_dir(&root, ambient_authority())
        .map_err(|error| format!("cannot open captured filesystem root: {error}"))?;
    let mut bindings = Vec::new();
    for name in names {
        let next = open_directory_at(&current, &name).map_err(|error| {
            format!(
                "cannot reopen captured output parent component {} without following links: {error}",
                name.to_string_lossy()
            )
        })?;
        let identity = directory_identity(&next)?;
        bindings.push((
            current.try_clone().map_err(|error| error.to_string())?,
            name.clone(),
            identity,
        ));
        current = next;
        after_open(&name);
    }
    for (parent, name, expected) in bindings {
        let actual = directory_identity_at(&parent, &name).map_err(|error| {
            format!(
                "captured output parent component {} changed during reopen: {error}",
                name.to_string_lossy()
            )
        })?;
        if actual != expected {
            return Err(format!(
                "captured output parent component {} changed during reopen",
                name.to_string_lossy()
            ));
        }
    }
    Ok(current)
}

fn output_anchor(root: &Path) -> Result<(Dir, Vec<String>, OutputParentAnchor), String> {
    if root
        .components()
        .any(|component| matches!(component, Component::ParentDir))
    {
        return Err("output path may not contain parent traversal".to_string());
    }
    let absolute = if root.is_absolute() {
        root.to_path_buf()
    } else {
        std::env::current_dir()
            .map_err(|error| error.to_string())?
            .join(root)
    };
    let mut ancestor = absolute
        .parent()
        .ok_or_else(|| "output root may not be the filesystem root".to_string())?
        .to_path_buf();
    let mut components = vec![
        absolute
            .file_name()
            .and_then(|name| name.to_str())
            .ok_or_else(|| "output path must end in a UTF-8 component".to_string())?
            .to_string(),
    ];
    while fs::symlink_metadata(&ancestor)
        .is_err_and(|error| error.kind() == std::io::ErrorKind::NotFound)
    {
        components.push(
            ancestor
                .file_name()
                .and_then(|name| name.to_str())
                .ok_or_else(|| "output path must be UTF-8".to_string())?
                .to_string(),
        );
        ancestor = ancestor
            .parent()
            .ok_or_else(|| "output path has no existing ancestor".to_string())?
            .to_path_buf();
    }
    let ancestor = fs::canonicalize(&ancestor)
        .map_err(|error| format!("cannot resolve output parent: {error}"))?;
    let parent = open_absolute_directory_nofollow(&ancestor)
        .map_err(|error| format!("cannot safely open output parent: {error}"))?;
    let identity = directory_identity(&parent)?;
    components.reverse();
    Ok((
        parent,
        components,
        OutputParentAnchor {
            path: ancestor,
            identity,
        },
    ))
}

fn initialize_empty_output(empty: &EmptyOutput) -> Result<TreeHandles, String> {
    empty.anchor.ensure_current(&empty.anchor_parent)?;
    let parent = empty
        .parent
        .try_clone()
        .map_err(|error| error.to_string())?;
    let root_name = empty.root_name.clone();
    let root = empty.root.try_clone().map_err(|error| error.to_string())?;
    initialize_root_directories(&root, &empty.anchor, &empty.anchor_parent)?;
    let handles = TreeHandles::from_root(
        parent,
        root_name,
        root,
        empty
            .anchor_parent
            .try_clone()
            .map_err(|error| error.to_string())?,
        empty.anchor.clone(),
    )?;
    ensure_initialized_tree(&handles)?;
    Ok(handles)
}

fn initialize_root_directories(
    root: &Dir,
    anchor: &OutputParentAnchor,
    anchor_parent: &Dir,
) -> Result<(), String> {
    for name in [UNITS_DIR, INBOX_DIR] {
        anchor.ensure_current(anchor_parent)?;
        root.create_dir(name)
            .map_err(|error| format!("cannot create {name}: {error}"))?;
        sync_directory(root).map_err(|error| format!("cannot sync output root: {error}"))?;
    }
    Ok(())
}

fn ensure_initialized_tree(handles: &TreeHandles) -> Result<(), String> {
    let root_names = directory_names(&handles.root)?;
    if root_names != [INBOX_DIR.to_string(), UNITS_DIR.to_string()]
        || !directory_names(&handles.units)?.is_empty()
        || !directory_names(&handles.inbox)?.is_empty()
    {
        return Err("new output tree changed before initialization".to_string());
    }
    Ok(())
}

fn create_unique_directory(
    parent: &Dir,
    label: &str,
    anchor: &OutputParentAnchor,
    anchor_parent: &Dir,
) -> Result<(String, Dir), String> {
    for _ in 0..32 {
        anchor.ensure_current(anchor_parent)?;
        let name = format!(".memphant-{label}-{}", Uuid::new_v4());
        match parent.create_dir(&name) {
            Ok(()) => {
                sync_directory(parent)
                    .map_err(|error| format!("cannot sync staging parent: {error}"))?;
                let directory = open_directory_at(parent, &name)?;
                if directory_identity_at(parent, &name)? != directory_identity(&directory)? {
                    return Err(format!("unique {label} directory changed while opening"));
                }
                return Ok((name, directory));
            }
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(format!("cannot create unique {label} directory: {error}")),
        }
    }
    Err(format!("cannot allocate unique {label} directory"))
}

fn build_and_install_absent_projection(
    absent: &AbsentOutput,
    rendered: &RenderedProjection,
    hook: &mut impl FnMut(&str),
) -> Result<Option<PathBuf>, String> {
    let first = absent
        .missing_components
        .first()
        .ok_or_else(|| "output path has no missing component".to_string())?;
    let (staging_name, staging_root) = create_unique_directory(
        &absent.parent,
        "stage",
        &absent.anchor,
        &absent.anchor_parent,
    )?;
    let staging_identity = directory_identity(&staging_root)?;
    let mut outer_bindings = Vec::new();

    let (parent, root_name, root) = if absent.missing_components.len() == 1 {
        (
            absent
                .parent
                .try_clone()
                .map_err(|error| error.to_string())?,
            staging_name.clone(),
            staging_root,
        )
    } else {
        outer_bindings.push(DirectoryBinding {
            parent: absent
                .parent
                .try_clone()
                .map_err(|error| error.to_string())?,
            name: staging_name.clone(),
            identity: staging_identity,
        });
        let mut current = staging_root;
        let mut result = None;
        for (index, name) in absent.missing_components.iter().enumerate().skip(1) {
            absent.anchor.ensure_current(&absent.anchor_parent)?;
            current
                .create_dir(name)
                .map_err(|error| format!("cannot create staging component {name}: {error}"))?;
            sync_directory(&current)
                .map_err(|error| format!("cannot sync staging component: {error}"))?;
            let next = open_directory_at(&current, name)?;
            if index + 1 == absent.missing_components.len() {
                result = Some((current, name.clone(), next));
                break;
            }
            outer_bindings.push(DirectoryBinding {
                parent: current.try_clone().map_err(|error| error.to_string())?,
                name: name.clone(),
                identity: directory_identity(&next)?,
            });
            current = next;
        }
        result.ok_or_else(|| "cannot resolve staging output root".to_string())?
    };

    initialize_root_directories(&root, &absent.anchor, &absent.anchor_parent)?;
    let mut handles = TreeHandles::from_root(
        parent,
        root_name,
        root,
        absent
            .anchor_parent
            .try_clone()
            .map_err(|error| error.to_string())?,
        absent.anchor.clone(),
    )?;
    handles.outer_bindings = outer_bindings;
    ensure_initialized_tree(&handles)?;
    let staged = write_rendered_projection(&handles, None, rendered, &BTreeMap::new(), hook)?;
    if staged.recovery.is_some() {
        return Err("new projection unexpectedly created durable recovery".to_string());
    }

    install_staged_projection(
        absent,
        &staging_name,
        first,
        staging_identity,
        handles,
        &staged.snapshot,
        rendered,
        hook,
    )?;
    Ok(None)
}

fn absent_install_error(error: std::io::Error, staging_name: &str) -> String {
    if error.kind() == std::io::ErrorKind::AlreadyExists {
        format!(
            "output appeared before install; left it untouched and retained validated staging {staging_name}: {error}"
        )
    } else {
        format!("atomic output install failed; retained validated staging {staging_name}: {error}")
    }
}

#[cfg(any(windows, test))]
fn open_installed_tree_from_parent(
    parent: &Dir,
    components: &[String],
    anchor: &OutputParentAnchor,
    anchor_parent: &Dir,
) -> Result<TreeHandles, String> {
    anchor.ensure_current(anchor_parent)?;
    let retained_anchor = anchor_parent
        .try_clone()
        .map_err(|error| error.to_string())?;
    let mut current = parent.try_clone().map_err(|error| error.to_string())?;
    let mut outer_bindings = Vec::new();
    for (index, name) in components.iter().enumerate() {
        let next = open_directory_at(&current, name)
            .map_err(|error| format!("installed output component {name}: {error}"))?;
        if index + 1 == components.len() {
            let mut handles = TreeHandles::from_root(
                current,
                name.clone(),
                next,
                retained_anchor,
                anchor.clone(),
            )?;
            handles.outer_bindings = outer_bindings;
            handles.ensure_bound()?;
            return Ok(handles);
        }
        outer_bindings.push(DirectoryBinding {
            parent: current.try_clone().map_err(|error| error.to_string())?,
            name: name.clone(),
            identity: directory_identity(&next)?,
        });
        current = next;
    }
    Err("output path has no missing component".to_string())
}

#[cfg(not(windows))]
#[allow(clippy::too_many_arguments)]
fn install_staged_projection(
    absent: &AbsentOutput,
    staging_name: &str,
    first: &str,
    staging_identity: FileIdentity,
    mut handles: TreeHandles,
    staged_snapshot: &ExactProjectionSnapshot,
    rendered: &RenderedProjection,
    hook: &mut impl FnMut(&str),
) -> Result<(), String> {
    hook("absent:before_install");
    absent.anchor.ensure_current(&absent.anchor_parent)?;
    rename_noreplace(&absent.parent, staging_name, &absent.parent, first)
        .map_err(|error| absent_install_error(error, staging_name))?;
    sync_directory(&absent.parent)
        .map_err(|error| format!("cannot sync installed output parent: {error}"))?;

    if absent.missing_components.len() == 1 {
        handles.root_name = first.to_string();
    } else if let Some(binding) = handles.outer_bindings.first_mut() {
        binding.name = first.to_string();
    }
    if directory_identity_at(&absent.parent, first)? != staging_identity {
        return Err("installed output identity differs from validated staging".to_string());
    }
    handles.ensure_bound()?;
    let installed = validate_rendered_projection_twice(&handles, rendered, hook)?;
    if &installed != staged_snapshot {
        return Err("installed output differs from the validated staging tree".to_string());
    }
    Ok(())
}

#[cfg(windows)]
#[allow(clippy::too_many_arguments)]
fn install_staged_projection(
    absent: &AbsentOutput,
    staging_name: &str,
    first: &str,
    staging_identity: FileIdentity,
    handles: TreeHandles,
    staged_snapshot: &ExactProjectionSnapshot,
    rendered: &RenderedProjection,
    hook: &mut impl FnMut(&str),
) -> Result<(), String> {
    // Windows cannot rename a populated directory while MemPhant still owns
    // handles into that subtree. The exact staged snapshot above contains no
    // handles, so dropping this aggregate closes every staging descendant.
    drop(handles);
    hook("absent:before_install");
    absent.anchor.ensure_current(&absent.anchor_parent)?;
    rename_noreplace(&absent.parent, staging_name, &absent.parent, first)
        .map_err(|error| absent_install_error(error, staging_name))?;
    sync_directory(&absent.parent)
        .map_err(|error| format!("cannot sync installed output parent: {error}"))?;

    if directory_identity_at(&absent.parent, first)? != staging_identity {
        return Err("installed output identity differs from validated staging".to_string());
    }
    let handles = open_installed_tree_from_parent(
        &absent.parent,
        &absent.missing_components,
        &absent.anchor,
        &absent.anchor_parent,
    )
    .map_err(|error| {
        format!("installed output could not be reopened from retained parent: {error}")
    })?;
    let installed = validate_rendered_projection_twice(&handles, rendered, hook)?;
    if &installed != staged_snapshot {
        return Err("installed output differs from the validated staging tree".to_string());
    }
    Ok(())
}

impl ValidatedExport {
    fn revalidate(&self) -> Result<(), Vec<String>> {
        let (manifest, snapshot_sha256, managed_files) =
            validate_export_handles(&self.handles, false)?;
        if manifest != self.manifest
            || snapshot_sha256 != self.snapshot_sha256
            || managed_files != self.managed_files
        {
            return Err(vec![
                "projection changed while the canonical snapshot was being fetched".to_string(),
            ]);
        }
        Ok(())
    }
}

impl OutputState {
    fn revalidate(&self) -> Result<(), Vec<String>> {
        match self {
            Self::Existing(existing) => existing.revalidate(),
            Self::Absent(absent) => {
                absent
                    .anchor
                    .ensure_current(&absent.anchor_parent)
                    .map_err(|error| vec![error])?;
                let first = absent
                    .missing_components
                    .first()
                    .ok_or_else(|| vec!["output path has no missing component".to_string()])?;
                match absent.parent.symlink_metadata(first) {
                    Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
                    Ok(_) => Err(vec![
                        "output path appeared while the canonical snapshot was being fetched"
                            .to_string(),
                    ]),
                    Err(error) => Err(vec![format!(
                        "cannot revalidate absent output component {first}: {error}"
                    )]),
                }
            }
            Self::Empty(empty) => {
                let mut findings = Vec::new();
                if let Err(error) = empty.anchor.ensure_current(&empty.anchor_parent) {
                    findings.push(error);
                }
                if directory_identity(&empty.root).ok() != Some(empty.root_identity) {
                    findings.push("output root handle changed identity".to_string());
                }
                match directory_identity_at(&empty.parent, &empty.root_name) {
                    Ok(identity) if identity == empty.root_identity => {}
                    _ => findings.push(
                        "output root path no longer names the validated empty directory"
                            .to_string(),
                    ),
                }
                match directory_names(&empty.root) {
                    Ok(names) if names == empty.root_names => {}
                    Ok(_) => findings.push(
                        "empty output changed while the canonical snapshot was being fetched"
                            .to_string(),
                    ),
                    Err(error) => findings.push(format!("cannot revalidate empty output: {error}")),
                }
                if findings.is_empty() {
                    Ok(())
                } else {
                    Err(findings)
                }
            }
        }
    }
}

fn open_directory_at(dir: &Dir, path: impl AsRef<Path>) -> Result<Dir, String> {
    dir.open_dir_nofollow(path)
        .map_err(|error| format!("refusing symlink or non-directory: {error}"))
}

fn metadata_identity(metadata: &cap_std::fs::Metadata) -> FileIdentity {
    FileIdentity {
        device: MetadataExt::dev(metadata),
        inode: MetadataExt::ino(metadata),
    }
}

fn directory_identity(dir: &Dir) -> Result<FileIdentity, String> {
    let metadata = dir.dir_metadata().map_err(|error| error.to_string())?;
    if !metadata.is_dir() {
        return Err("expected directory".to_string());
    }
    Ok(metadata_identity(&metadata))
}

fn directory_identity_at(dir: &Dir, path: impl AsRef<Path>) -> Result<FileIdentity, String> {
    let metadata = dir
        .symlink_metadata(path)
        .map_err(|error| error.to_string())?;
    if !metadata.is_dir() || metadata.is_symlink() {
        return Err("expected a non-symlink directory".to_string());
    }
    Ok(metadata_identity(&metadata))
}

fn read_regular_at(
    dir: &Dir,
    name: &str,
    max_bytes: u64,
) -> Result<(Vec<u8>, FileIdentity), String> {
    let mut options = OpenOptions::new();
    options.read(true);
    options.follow(FollowSymlinks::No);
    options.nonblock(true);
    let file = dir
        .open_with(name, &options)
        .map_err(|error| format!("refusing symlink or unreadable file: {error}"))?;
    let metadata = file.metadata().map_err(|error| error.to_string())?;
    if !metadata.is_file() {
        return Err("expected a regular file".to_string());
    }
    if metadata.len() > max_bytes {
        return Err(format!("file exceeds {max_bytes} bytes"));
    }
    let identity = metadata_identity(&metadata);
    let mut bytes = Vec::with_capacity(metadata.len() as usize);
    file.take(max_bytes + 1)
        .read_to_end(&mut bytes)
        .map_err(|error| error.to_string())?;
    if bytes.len() as u64 > max_bytes {
        return Err(format!("file exceeds {max_bytes} bytes"));
    }
    Ok((bytes, identity))
}

fn directory_names(directory: &Dir) -> Result<Vec<String>, String> {
    let mut names = Vec::new();
    for entry in directory.entries().map_err(|error| error.to_string())? {
        let name = entry
            .map_err(|error| error.to_string())?
            .file_name()
            .into_string()
            .map_err(|_| "non-UTF-8 path is forbidden".to_string())?;
        names.push(name);
        if names.len() > MAX_DIRECTORY_ENTRIES {
            return Err(format!("directory exceeds {MAX_DIRECTORY_ENTRIES} entries"));
        }
    }
    names.sort();
    Ok(names)
}

fn inspect_output(root: &Path) -> Result<OutputState, CompileFailure> {
    let (mut current, components, anchor) = output_anchor(root).map_err(CompileFailure::Error)?;
    let anchor_parent = current
        .try_clone()
        .map_err(|error| CompileFailure::Error(error.to_string()))?;
    for (index, name) in components.iter().enumerate() {
        match current.symlink_metadata(name) {
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                return Ok(OutputState::Absent(AbsentOutput {
                    anchor,
                    anchor_parent,
                    parent: current,
                    missing_components: components[index..].to_vec(),
                }));
            }
            Err(error) => {
                return Err(CompileFailure::Error(format!(
                    "cannot inspect output component {name}: {error}"
                )));
            }
            Ok(metadata) if !metadata.is_dir() || metadata.is_symlink() => {
                return Err(CompileFailure::Dirty(vec![format!(
                    "output component is a symlink or non-directory: {name}"
                )]));
            }
            Ok(_) => {}
        }
        let next = open_directory_at(&current, name).map_err(|error| {
            CompileFailure::Dirty(vec![format!(
                "cannot safely open output component {name}: {error}"
            )])
        })?;
        if index + 1 == components.len() {
            let names = directory_names(&next).map_err(|error| {
                CompileFailure::Dirty(vec![format!("cannot read output root: {error}")])
            })?;
            if names.is_empty() {
                return Ok(OutputState::Empty(EmptyOutput {
                    anchor,
                    anchor_parent,
                    root_identity: directory_identity(&next).map_err(CompileFailure::Error)?,
                    parent: current,
                    root_name: name.clone(),
                    root: next,
                    root_names: names,
                }));
            }
            let handles =
                TreeHandles::from_root(current, name.clone(), next, anchor_parent, anchor)
                    .map_err(|error| CompileFailure::Dirty(vec![error]))?;
            let (manifest, snapshot_sha256, managed_files) =
                validate_export_handles(&handles, false).map_err(CompileFailure::Dirty)?;
            return Ok(OutputState::Existing(Box::new(ValidatedExport {
                manifest,
                snapshot_sha256,
                handles,
                managed_files,
            })));
        }
        current = next;
    }
    Err(CompileFailure::Error(
        "output root may not be the filesystem root".to_string(),
    ))
}

pub(crate) fn verify_export(root: &Path) -> Result<ExportManifest, Vec<String>> {
    validate_export(root, false)
}

pub(crate) fn validate_export(
    root: &Path,
    allow_inbox_files: bool,
) -> Result<ExportManifest, Vec<String>> {
    validate_export_anchored(root, allow_inbox_files).map(|validated| validated.manifest)
}

fn validate_export_anchored(
    root: &Path,
    allow_inbox_files: bool,
) -> Result<ValidatedExport, Vec<String>> {
    let handles = TreeHandles::open(root).map_err(|error| vec![error])?;
    let (manifest, snapshot_sha256, managed_files) =
        validate_export_handles(&handles, allow_inbox_files)?;
    Ok(ValidatedExport {
        manifest,
        snapshot_sha256,
        handles,
        managed_files,
    })
}

fn validate_export_handles(
    handles: &TreeHandles,
    allow_inbox_files: bool,
) -> Result<
    (
        ExportManifest,
        String,
        BTreeMap<String, ManagedFileSnapshot>,
    ),
    Vec<String>,
> {
    let mut findings = Vec::new();
    if let Err(error) = handles.ensure_bound() {
        return Err(vec![error]);
    }
    let (manifest_bytes, manifest_identity) =
        read_regular_at(&handles.root, MANIFEST_FILE, MAX_MANIFEST_BYTES)
            .map_err(|error| vec![format!("{MANIFEST_FILE}: {error}")])?;
    let mut managed_files = BTreeMap::from([(
        MANIFEST_FILE.to_string(),
        ManagedFileSnapshot {
            identity: manifest_identity,
            bytes: manifest_bytes.clone(),
        },
    )]);
    let manifest = strict_from_slice::<ExportManifest>(&manifest_bytes)
        .map_err(|error| vec![format!("{MANIFEST_FILE}: invalid JSON: {error}")]);
    let manifest = match manifest {
        Ok(manifest) => manifest,
        Err(mut errors) => {
            findings.append(&mut errors);
            return Err(findings);
        }
    };
    validate_manifest_fields(&manifest, &mut findings);
    match canonical_manifest_bytes(&manifest) {
        Ok(canonical) if canonical != manifest_bytes => findings.push(format!(
            "{MANIFEST_FILE}: bytes are not the canonical generated serialization"
        )),
        Err(error) => findings.push(format!("{MANIFEST_FILE}: {error}")),
        _ => {}
    }

    let memory_bytes = match read_regular_at(&handles.root, MEMORY_FILE, MAX_MANAGED_FILE_BYTES) {
        Ok((bytes, identity)) => {
            managed_files.insert(
                MEMORY_FILE.to_string(),
                ManagedFileSnapshot {
                    identity,
                    bytes: bytes.clone(),
                },
            );
            bytes
        }
        Err(error) => {
            findings.push(format!("{MEMORY_FILE}: {error}"));
            Vec::new()
        }
    };
    if sha256(&memory_bytes) != manifest.memory_sha256 {
        findings.push(format!("{MEMORY_FILE}: content hash differs from manifest"));
    }

    let mut expected_unit_names = BTreeSet::new();
    let mut canonical_items = Vec::new();
    let mut unit_bytes = BTreeMap::new();
    for entry in &manifest.entries {
        let expected_path = format!("{UNITS_DIR}/{}.md", entry.unit_id);
        if entry.path != expected_path || !is_safe_relative_unit_path(&entry.path) {
            findings.push(format!("{}: path must be {expected_path}", entry.path));
            continue;
        }
        let file_name = format!("{}.md", entry.unit_id);
        expected_unit_names.insert(file_name.clone());
        let bytes = match read_regular_at(&handles.units, &file_name, MAX_MANAGED_FILE_BYTES) {
            Ok((bytes, identity)) => {
                managed_files.insert(
                    entry.path.clone(),
                    ManagedFileSnapshot {
                        identity,
                        bytes: bytes.clone(),
                    },
                );
                bytes
            }
            Err(error) => {
                findings.push(format!("{}: {error}", entry.path));
                continue;
            }
        };
        unit_bytes.insert(file_name, bytes.clone());
        if sha256(&bytes) != entry.file_sha256 {
            findings.push(format!("{}: file hash differs from manifest", entry.path));
        }
        match parse_unit(&bytes) {
            Ok((body, footer)) => {
                validate_footer(entry, manifest.subject_generation, &footer, &mut findings);
                if sha256(body.as_bytes()) != entry.body_sha256 {
                    findings.push(format!("{}: body hash differs from manifest", entry.path));
                }
                if let Ok(unit_id) = Uuid::parse_str(&entry.unit_id) {
                    canonical_items.push(CanonicalProjectionUnit {
                        unit_id: UnitId::from_u128(unit_id.as_u128()),
                        kind: entry.kind,
                        fact_key: entry.fact_key.clone(),
                        predicate: entry.predicate.clone(),
                        body,
                        confidence: entry.confidence,
                        state: entry.state,
                        source_span: entry.source_span.clone(),
                        valid_from: entry.valid_from.clone(),
                        valid_to: entry.valid_to.clone(),
                        body_sha256: entry.body_sha256.clone(),
                    });
                }
            }
            Err(error) => findings.push(format!("{}: {error}", entry.path)),
        }
    }

    let units_names = directory_names(&handles.units).unwrap_or_else(|error| {
        findings.push(format!("{UNITS_DIR}: {error}"));
        Vec::new()
    });
    collect_name_mismatches(
        &units_names,
        &expected_unit_names,
        false,
        UNITS_DIR,
        &mut findings,
    );
    let inbox_names = directory_names(&handles.inbox).unwrap_or_else(|error| {
        findings.push(format!("{INBOX_DIR}: {error}"));
        Vec::new()
    });
    collect_name_mismatches(
        &inbox_names,
        &BTreeSet::new(),
        allow_inbox_files,
        INBOX_DIR,
        &mut findings,
    );
    if allow_inbox_files {
        for name in &inbox_names {
            if is_safe_inbox_name(name)
                && let Err(error) = read_regular_at(&handles.inbox, name, MAX_MANAGED_FILE_BYTES)
            {
                findings.push(format!("{INBOX_DIR}/{name}: {error}"));
            }
        }
    }
    let expected_root = BTreeSet::from([
        MEMORY_FILE.to_string(),
        MANIFEST_FILE.to_string(),
        UNITS_DIR.to_string(),
        INBOX_DIR.to_string(),
    ]);
    let root_names = directory_names(&handles.root).unwrap_or_else(|error| {
        findings.push(format!("output root: {error}"));
        Vec::new()
    });
    collect_name_mismatches(&root_names, &expected_root, false, ".", &mut findings);

    if let Ok(scope_id) = Uuid::parse_str(&manifest.scope_id) {
        let expected_memory = render_memory(scope_id, &manifest.snapshot_sha256, &manifest.entries);
        if memory_bytes != expected_memory {
            findings.push(format!(
                "{MEMORY_FILE}: content does not match the canonical manifest index"
            ));
        }
    }
    if canonical_items.len() == manifest.entries.len() {
        match canonical_projection_fingerprint(&canonical_items) {
            Ok(actual) if actual != manifest.snapshot_sha256 => findings.push(format!(
                "snapshot_sha256: canonical entries hash to {actual}, not {}",
                manifest.snapshot_sha256
            )),
            Err(error) => findings.push(format!("snapshot_sha256: cannot fingerprint: {error}")),
            _ => {}
        }
    }

    if let Err(error) = handles.ensure_bound() {
        findings.push(error);
    }

    if findings.is_empty() {
        let snapshot_sha256 = tree_snapshot_sha256(
            handles,
            &root_names,
            &units_names,
            &inbox_names,
            &manifest_bytes,
            &memory_bytes,
            &unit_bytes,
        );
        Ok((manifest, snapshot_sha256, managed_files))
    } else {
        findings.sort();
        findings.dedup();
        Err(findings)
    }
}

fn validate_manifest_fields(manifest: &ExportManifest, findings: &mut Vec<String>) {
    if manifest.schema_version != SCHEMA_VERSION {
        findings.push(format!(
            "schema_version: expected {SCHEMA_VERSION}, got {}",
            manifest.schema_version
        ));
    }
    if manifest.compiler_version != COMPILER_VERSION {
        findings.push(format!(
            "compiler_version: expected {COMPILER_VERSION}, got {}",
            manifest.compiler_version
        ));
    }
    for (name, value) in [
        ("tenant_id", &manifest.tenant_id),
        ("subject_id", &manifest.subject_id),
        ("actor_id", &manifest.actor_id),
        ("scope_id", &manifest.scope_id),
        ("agent_node_id", &manifest.agent_node_id),
    ] {
        if Uuid::parse_str(value).map(|id| id.to_string()).as_ref() != Ok(value) {
            findings.push(format!("{name}: must be a lowercase canonical UUID"));
        }
    }
    if require_sha256("snapshot_sha256", &manifest.snapshot_sha256).is_err() {
        findings.push("snapshot_sha256: must be a lowercase SHA-256 digest".to_string());
    }
    if require_sha256("memory_sha256", &manifest.memory_sha256).is_err() {
        findings.push("memory_sha256: must be a lowercase SHA-256 digest".to_string());
    }
    let mut ids = BTreeSet::new();
    let mut paths = BTreeSet::new();
    let mut fact_keys = BTreeSet::new();
    let mut previous = None;
    for entry in &manifest.entries {
        if Uuid::parse_str(&entry.unit_id)
            .map(|id| id.to_string())
            .as_ref()
            != Ok(&entry.unit_id)
        {
            findings.push(format!(
                "{}: unit_id must be a lowercase canonical UUID",
                entry.path
            ));
        }
        if !ids.insert(entry.unit_id.clone()) {
            findings.push(format!("duplicate unit_id: {}", entry.unit_id));
        }
        if !matches!(entry.kind, MemoryKind::Semantic | MemoryKind::Procedural) {
            findings.push(format!("{}: unsupported memory kind", entry.path));
        }
        if let Some(value) = entry.fact_key.as_deref()
            && require_single_line("fact_key", value).is_err()
        {
            findings.push(format!(
                "{}: fact_key must be one non-empty line",
                entry.path
            ));
        }
        if let Some(value) = entry.predicate.as_deref()
            && require_single_line("predicate", value).is_err()
        {
            findings.push(format!(
                "{}: predicate must be one non-empty line",
                entry.path
            ));
        }
        if let Some(value) = entry.confidence
            && (!value.is_finite() || !(0.0..=1.0).contains(&value))
        {
            findings.push(format!(
                "{}: confidence must be finite and within 0..=1",
                entry.path
            ));
        }
        let valid_from =
            validate_timestamp(entry.valid_from.as_deref(), "valid_from", entry, findings);
        let valid_to = validate_timestamp(entry.valid_to.as_deref(), "valid_to", entry, findings);
        if let (Some(valid_from), Some(valid_to)) = (valid_from, valid_to)
            && valid_from >= valid_to
        {
            findings.push(format!(
                "{}: valid_from must be earlier than valid_to",
                entry.path
            ));
        }
        if !paths.insert(entry.path.clone()) {
            findings.push(format!("duplicate path: {}", entry.path));
        }
        if let Some(fact_key) = &entry.fact_key
            && !fact_keys.insert(fact_key.clone())
        {
            findings.push(format!("duplicate fact_key: {fact_key}"));
        }
        if previous.as_ref().is_some_and(|id| id >= &entry.unit_id) {
            findings.push("entries must be strictly ordered by unit_id".to_string());
        }
        previous = Some(entry.unit_id.clone());
        for (field, hash) in [
            ("body_sha256", &entry.body_sha256),
            ("file_sha256", &entry.file_sha256),
        ] {
            if require_sha256(field, hash).is_err() {
                findings.push(format!("{}: invalid {field}", entry.path));
            }
        }
    }
}

fn validate_timestamp(
    value: Option<&str>,
    field: &str,
    entry: &ManifestEntry,
    findings: &mut Vec<String>,
) -> Option<jiff::Timestamp> {
    let value = value?;
    if !(value.ends_with('Z') || value.ends_with("+00:00")) {
        findings.push(format!("{}: {field} must use UTC", entry.path));
        return None;
    }
    match value.parse() {
        Ok(timestamp) => Some(timestamp),
        Err(_) => {
            findings.push(format!("{}: {field} must be RFC3339", entry.path));
            None
        }
    }
}

fn validate_footer(
    entry: &ManifestEntry,
    subject_generation: u64,
    footer: &UnitFooter,
    findings: &mut Vec<String>,
) {
    let expected = UnitFooter {
        unit_id: entry.unit_id.clone(),
        body_sha256: entry.body_sha256.clone(),
        subject_generation,
        kind: entry.kind,
        fact_key: entry.fact_key.clone(),
        predicate: entry.predicate.clone(),
        confidence: entry.confidence,
        state: entry.state,
        valid_from: entry.valid_from.clone(),
        valid_to: entry.valid_to.clone(),
        source_span: entry.source_span.clone(),
    };
    if *footer != expected {
        findings.push(format!(
            "{}: footer metadata differs from manifest",
            entry.path
        ));
    }
}

pub(crate) fn parse_unit(bytes: &[u8]) -> Result<(String, UnitFooter), String> {
    let text = std::str::from_utf8(bytes).map_err(|_| "file is not UTF-8".to_string())?;
    if !text.ends_with(" -->\n") {
        return Err("missing exact final memphant footer".to_string());
    }
    let footer_start = text
        .rfind("\n\n<!-- memphant ")
        .ok_or_else(|| "missing exact final memphant footer".to_string())?;
    let footer_json = &text[footer_start + "\n\n<!-- memphant ".len()..text.len() - " -->\n".len()];
    let footer: UnitFooter = strict_from_slice(footer_json.as_bytes())
        .map_err(|error| format!("invalid footer JSON: {error}"))?;
    let prefix_end = text
        .find("\n\n")
        .ok_or_else(|| "missing H1/body separator".to_string())?;
    let title = &text[..prefix_end];
    if !title.starts_with("# ") || title[2..].contains('\n') {
        return Err("unit must start with one H1 display key".to_string());
    }
    let expected_title = footer.fact_key.as_deref().unwrap_or(&footer.unit_id);
    if &title[2..] != expected_title {
        return Err("H1 display key differs from footer".to_string());
    }
    Ok((text[prefix_end + 2..footer_start].to_string(), footer))
}

fn replace_projection(
    root: &Path,
    output: &OutputState,
    rendered: &RenderedProjection,
) -> Result<Option<PathBuf>, String> {
    replace_projection_with_hook(root, output, rendered, |_| {})
}

fn replace_projection_with_hook(
    _root: &Path,
    output: &OutputState,
    rendered: &RenderedProjection,
    mut after_mutation: impl FnMut(&str),
) -> Result<Option<PathBuf>, String> {
    output
        .revalidate()
        .map_err(|findings| findings.join("; "))?;
    after_mutation("parent:before_first_mutation");
    output
        .revalidate()
        .map_err(|findings| findings.join("; "))?;
    if let OutputState::Absent(absent) = output {
        return build_and_install_absent_projection(absent, rendered, &mut after_mutation);
    }
    let previous = match output {
        OutputState::Existing(previous) => Some(previous.as_ref()),
        OutputState::Empty(_) => None,
        OutputState::Absent(_) => unreachable!("absent output handled above"),
    };
    let owned_handles = match output {
        OutputState::Empty(empty) => Some(initialize_empty_output(empty)?),
        OutputState::Existing(_) => None,
        OutputState::Absent(_) => unreachable!("absent output handled above"),
    };
    let handles = previous
        .map(|validated| &validated.handles)
        .or(owned_handles.as_ref())
        .expect("existing or newly opened output handles");
    write_rendered_projection(
        handles,
        previous,
        rendered,
        &BTreeMap::new(),
        &mut after_mutation,
    )
    .map(|outcome| outcome.recovery)
}

fn write_rendered_projection(
    handles: &TreeHandles,
    previous: Option<&ValidatedExport>,
    rendered: &RenderedProjection,
    consumed_inbox: &BTreeMap<String, ManagedFileSnapshot>,
    after_mutation: &mut impl FnMut(&str),
) -> Result<ProjectionWriteOutcome, String> {
    let mut recovery = RecoverySession::new(&handles.anchor);
    let result = (|| {
        handles.ensure_bound()?;

        for (path, expected) in consumed_inbox {
            let name = Path::new(path)
                .file_name()
                .and_then(|name| name.to_str())
                .ok_or_else(|| format!("invalid consumed inbox path {path}"))?;
            validate_current_file(&handles.inbox, name, expected)?;
            let recovery_was_empty = !recovery.contains_managed_data();
            let recovery_directory =
                recovery.target(&handles.anchor_parent, ManagedDirectory::Inbox)?;
            if recovery_was_empty {
                after_mutation("recovery:created");
                recovery.ensure(&handles.anchor_parent)?;
            }
            move_to_recovery(
                &handles.inbox,
                name,
                &recovery_directory,
                &handles.anchor,
                &handles.anchor_parent,
                &mut recovery,
            )?;
            after_mutation(&format!("consume:{name}:detached"));
            if let Err(error) = validate_detached_file(&recovery_directory, name, name, expected) {
                return Err(format!(
                    "cannot consume {path}: {error}; unexpected inode remains in durable recovery"
                ));
            }
            if managed_identity_at(&handles.inbox, name)?.is_some() {
                return Err(format!(
                    "{path} reappeared after recovery move; concurrent path was left untouched"
                ));
            }
            after_mutation(&format!("consume:{name}:recovered"));
        }

        for (path, bytes) in &rendered.units {
            let name = Path::new(path)
                .file_name()
                .and_then(|name| name.to_str())
                .ok_or_else(|| format!("invalid rendered unit path {path}"))?;
            handles.ensure_bound()?;
            replace_managed_file(
                ManagedTarget {
                    directory: &handles.units,
                    name,
                    location: ManagedDirectory::Units,
                },
                bytes,
                expected_managed_file(previous, path),
                &handles.anchor_parent,
                &handles.anchor,
                &mut recovery,
                after_mutation,
            )?;
            after_mutation(path);
        }
        handles.ensure_bound()?;
        replace_managed_file(
            ManagedTarget {
                directory: &handles.root,
                name: MEMORY_FILE,
                location: ManagedDirectory::Root,
            },
            &rendered.memory,
            expected_managed_file(previous, MEMORY_FILE),
            &handles.anchor_parent,
            &handles.anchor,
            &mut recovery,
            after_mutation,
        )?;
        after_mutation(MEMORY_FILE);

        if let Some(previous) = previous {
            let current = rendered.units.keys().collect::<BTreeSet<_>>();
            for entry in &previous.manifest.entries {
                if !current.contains(&entry.path) {
                    let name = Path::new(&entry.path)
                        .file_name()
                        .and_then(|name| name.to_str())
                        .ok_or_else(|| format!("invalid stale unit path {}", entry.path))?;
                    handles.ensure_bound()?;
                    if let Some(expected) = previous.managed_files.get(&entry.path) {
                        delete_managed_file(
                            ManagedTarget {
                                directory: &handles.units,
                                name,
                                location: ManagedDirectory::Units,
                            },
                            expected,
                            &handles.anchor_parent,
                            &handles.anchor,
                            &mut recovery,
                            after_mutation,
                        )?;
                    } else if managed_identity_at(&handles.units, name)?.is_some() {
                        return Err(format!(
                            "{} reappeared after the sync plan observed it missing",
                            entry.path
                        ));
                    }
                    after_mutation(&entry.path);
                }
            }
        }
        handles.ensure_bound()?;
        replace_managed_file(
            ManagedTarget {
                directory: &handles.root,
                name: MANIFEST_FILE,
                location: ManagedDirectory::Root,
            },
            &rendered.manifest_bytes,
            expected_managed_file(previous, MANIFEST_FILE),
            &handles.anchor_parent,
            &handles.anchor,
            &mut recovery,
            after_mutation,
        )?;
        after_mutation(MANIFEST_FILE);
        handles.ensure_bound()?;
        validate_rendered_projection_twice(handles, rendered, after_mutation)
    })();
    match result {
        Ok(snapshot) => match recovery.confirmed_path(&handles.anchor_parent) {
            Ok(path) => Ok(ProjectionWriteOutcome {
                snapshot,
                recovery: path,
            }),
            Err(error) => Err(recovery.annotate(&handles.anchor_parent, error)),
        },
        Err(error) => Err(recovery.annotate(&handles.anchor_parent, error)),
    }
}

fn validate_rendered_projection_twice(
    handles: &TreeHandles,
    rendered: &RenderedProjection,
    hook: &mut impl FnMut(&str),
) -> Result<ExactProjectionSnapshot, String> {
    let first = validate_export_handles(handles, false).map_err(|findings| {
        format!(
            "final rendered-tree validation failed: {}",
            findings.join("; ")
        )
    })?;
    if first.0 != rendered.manifest {
        return Err("final rendered tree differs from the canonical projection".to_string());
    }
    hook("final:between_sweeps");
    let second = validate_export_handles(handles, false)
        .map_err(|findings| format!("final stability sweep failed: {}", findings.join("; ")))?;
    if second != first {
        return Err("final rendered tree changed during the stability sweep".to_string());
    }
    Ok(second)
}

fn expected_managed_file<'a>(
    previous: Option<&'a ValidatedExport>,
    path: &str,
) -> Option<&'a ManagedFileSnapshot> {
    previous.and_then(|validated| validated.managed_files.get(path))
}

fn managed_identity_at(directory: &Dir, name: &str) -> Result<Option<FileIdentity>, String> {
    match directory.symlink_metadata(name) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(format!("cannot inspect {name}: {error}")),
        Ok(metadata) if metadata.is_file() && !metadata.is_symlink() => {
            Ok(Some(metadata_identity(&metadata)))
        }
        Ok(_) => Err(format!("{name}: expected a non-symlink regular file")),
    }
}

fn validate_detached_file(
    directory: &Dir,
    detached_name: &str,
    original_name: &str,
    expected: &ManagedFileSnapshot,
) -> Result<(), String> {
    let max_bytes = if original_name == MANIFEST_FILE {
        MAX_MANIFEST_BYTES
    } else {
        MAX_MANAGED_FILE_BYTES
    };
    let (bytes, identity) = read_regular_at(directory, detached_name, max_bytes)?;
    if identity == expected.identity && bytes == expected.bytes {
        Ok(())
    } else {
        Err(format!(
            "{detached_name}: detached file differs from the validated snapshot"
        ))
    }
}

#[cfg(any(
    target_vendor = "apple",
    target_os = "linux",
    target_os = "android",
    target_os = "redox"
))]
fn rename_noreplace(
    source_directory: &Dir,
    from: &str,
    target_directory: &Dir,
    to: &str,
) -> std::io::Result<()> {
    Ok(renameat_with(
        source_directory,
        from,
        target_directory,
        to,
        RenameFlags::NOREPLACE,
    )?)
}

#[cfg(windows)]
fn rename_noreplace(
    source_directory: &Dir,
    from: &str,
    target_directory: &Dir,
    to: &str,
) -> std::io::Result<()> {
    fn is_relative_component(name: &str) -> bool {
        let mut components = Path::new(name).components();
        matches!(components.next(), Some(Component::Normal(_))) && components.next().is_none()
    }

    if !is_relative_component(from) || !is_relative_component(to) {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "atomic rename names must each be one relative path component",
        ));
    }

    let mut options = OpenOptions::new();
    options
        .read(true)
        .follow(FollowSymlinks::No)
        .access_mode(DELETE | FILE_READ_ATTRIBUTES)
        .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS);
    let source = source_directory.open_with(from, &options)?;

    let target = OsStr::new(to).encode_wide().collect::<Vec<_>>();
    let target_bytes = target
        .len()
        .checked_mul(std::mem::size_of::<u16>())
        .ok_or_else(|| {
            std::io::Error::new(std::io::ErrorKind::InvalidInput, "rename target too long")
        })?;
    let information_bytes = std::mem::offset_of!(FILE_RENAME_INFO, FileName)
        .checked_add(target_bytes)
        .ok_or_else(|| {
            std::io::Error::new(std::io::ErrorKind::InvalidInput, "rename target too long")
        })?;
    let information_size = u32::try_from(information_bytes).map_err(|_| {
        std::io::Error::new(std::io::ErrorKind::InvalidInput, "rename target too long")
    })?;
    let slots = information_bytes.div_ceil(std::mem::size_of::<FILE_RENAME_INFO>());
    let mut information = vec![FILE_RENAME_INFO::default(); slots];
    let information_ptr = information.as_mut_ptr();

    // SAFETY: `information` is aligned for FILE_RENAME_INFO and has at least
    // `information_bytes` initialized bytes. The target UTF-16 slice is copied
    // into the structure's documented flexible trailing array. Both handles
    // stay alive for the call, and SetFileInformationByHandle only borrows the
    // buffer for its duration.
    let renamed = unsafe {
        (*information_ptr).Anonymous.ReplaceIfExists = false;
        (*information_ptr).RootDirectory = target_directory.as_raw_handle();
        (*information_ptr).FileNameLength = target_bytes as u32;
        std::ptr::copy_nonoverlapping(
            target.as_ptr(),
            std::ptr::addr_of_mut!((*information_ptr).FileName).cast::<u16>(),
            target.len(),
        );
        SetFileInformationByHandle(
            source.as_raw_handle(),
            FileRenameInfo,
            information_ptr.cast(),
            information_size,
        )
    };
    if renamed == 0 {
        let error = std::io::Error::last_os_error();
        if matches!(
            error.raw_os_error(),
            Some(code) if code == ERROR_ALREADY_EXISTS as i32 || code == ERROR_FILE_EXISTS as i32
        ) {
            Err(std::io::Error::new(
                std::io::ErrorKind::AlreadyExists,
                error,
            ))
        } else {
            Err(error)
        }
    } else {
        Ok(())
    }
}

#[cfg(all(
    unix,
    not(any(
        target_vendor = "apple",
        target_os = "linux",
        target_os = "android",
        target_os = "redox"
    ))
))]
fn rename_noreplace(
    _source_directory: &Dir,
    _from: &str,
    _target_directory: &Dir,
    _to: &str,
) -> std::io::Result<()> {
    Err(std::io::Error::new(
        std::io::ErrorKind::Unsupported,
        "this Unix target has no audited atomic no-replace rename backend",
    ))
}

#[cfg(not(any(unix, windows)))]
fn rename_noreplace(
    _source_directory: &Dir,
    _from: &str,
    _target_directory: &Dir,
    _to: &str,
) -> std::io::Result<()> {
    Err(std::io::Error::new(
        std::io::ErrorKind::Unsupported,
        "this target has no audited atomic no-replace rename backend",
    ))
}

#[cfg(unix)]
fn sync_directory(directory: &Dir) -> Result<(), String> {
    // No-follow traversal uses O_PATH on Linux. Reopen `.` relative to the
    // captured descriptor so fsync receives a syncable handle without
    // resolving an ambient path or weakening the inode anchor.
    let syncable: fs::File = openat(
        directory.as_fd(),
        ".",
        OFlags::RDONLY | OFlags::DIRECTORY | OFlags::CLOEXEC,
        Mode::empty(),
    )
    .map_err(|error| error.to_string())?
    .into();
    syncable.sync_all().map_err(|error| error.to_string())
}

#[cfg(not(unix))]
fn sync_directory(_directory: &Dir) -> Result<(), String> {
    // Windows does not support FlushFileBuffers on cap-std's read-only
    // directory handles. Every prepared file is synced before the namespace
    // operation; the supported best-effort directory barrier is therefore a
    // non-failing no-op rather than an invalid read-only-directory flush.
    Ok(())
}

fn unique_internal_name(label: &str) -> String {
    format!(".memphant-{label}-{}", Uuid::new_v4())
}

#[derive(Debug, Clone, Copy)]
enum ManagedDirectory {
    Root,
    Units,
    Inbox,
}

fn prepared_cleanup_skipped(error: String, location: ManagedDirectory, prepared: &str) -> String {
    let last_known = match location {
        ManagedDirectory::Root => prepared.to_string(),
        ManagedDirectory::Units => format!("{UNITS_DIR}/{prepared}"),
        ManagedDirectory::Inbox => format!("{INBOX_DIR}/{prepared}"),
    };
    format!(
        "{error}; prepared_name_last_known={last_known}; prepared cleanup skipped because portable atomic handle-bound unlink is unavailable"
    )
}

#[derive(Debug, Clone, Copy)]
struct ManagedTarget<'a> {
    directory: &'a Dir,
    name: &'a str,
    location: ManagedDirectory,
}

impl RecoverySession {
    fn new(anchor: &OutputParentAnchor) -> Self {
        Self {
            anchor: anchor.clone(),
            area: None,
            retained_managed_inodes: 0,
        }
    }

    fn ensure(&mut self, output_parent: &Dir) -> Result<&RecoveryArea, String> {
        if self.area.is_none() {
            self.area = Some(create_recovery_area(output_parent, &self.anchor)?);
        }
        let area = self.area.as_ref().expect("recovery area initialized");
        area.ensure_current(&self.anchor, output_parent)?;
        Ok(area)
    }

    fn target(&mut self, output_parent: &Dir, location: ManagedDirectory) -> Result<Dir, String> {
        let area = self.ensure(output_parent)?;
        match location {
            ManagedDirectory::Root => area.root.try_clone().map_err(|error| error.to_string()),
            ManagedDirectory::Units => area.units.try_clone().map_err(|error| error.to_string()),
            ManagedDirectory::Inbox => area.inbox.try_clone().map_err(|error| error.to_string()),
        }
    }

    fn confirmed_path(&self, output_parent: &Dir) -> Result<Option<PathBuf>, String> {
        match &self.area {
            Some(area) => {
                area.ensure_current(&self.anchor, output_parent)?;
                Ok(Some(area.path.clone()))
            }
            None => Ok(None),
        }
    }

    fn note_managed_inode_recovered(&mut self) {
        self.retained_managed_inodes = self
            .retained_managed_inodes
            .checked_add(1)
            .expect("recovery managed-inode count overflowed");
    }

    fn contains_managed_data(&self) -> bool {
        self.retained_managed_inodes != 0
    }

    fn annotate(&self, output_parent: &Dir, error: String) -> String {
        match &self.area {
            Some(area) if area.ensure_current(&self.anchor, output_parent).is_ok() => {
                if error.contains("recovery=") {
                    error
                } else {
                    format!("{error}; recovery={}", area.path.display())
                }
            }
            Some(area) => last_known_recovery(error, &area.path, self.contains_managed_data()),
            None => error,
        }
    }
}

impl RecoveryArea {
    fn ensure_current(
        &self,
        anchor: &OutputParentAnchor,
        output_parent: &Dir,
    ) -> Result<(), String> {
        ensure_recovery_current(
            anchor,
            output_parent,
            &self.name,
            &self.path,
            self.identity,
            &self.root,
        )?;
        let units_handle_identity = directory_identity(&self.units).map_err(|error| {
            format!("durable recovery units handle could not be confirmed: {error}")
        })?;
        if units_handle_identity != self.units_identity {
            return Err("durable recovery units handle changed identity".to_string());
        }
        let units_path_identity =
            directory_identity_at(&self.root, UNITS_DIR).map_err(|error| {
                format!("durable recovery units path could not be confirmed: {error}")
            })?;
        if units_path_identity != self.units_identity {
            return Err("durable recovery units path changed identity".to_string());
        }
        let inbox_handle_identity = directory_identity(&self.inbox).map_err(|error| {
            format!("durable recovery inbox handle could not be confirmed: {error}")
        })?;
        if inbox_handle_identity != self.inbox_identity {
            return Err("durable recovery inbox handle changed identity".to_string());
        }
        let inbox_path_identity =
            directory_identity_at(&self.root, INBOX_DIR).map_err(|error| {
                format!("durable recovery inbox path could not be confirmed: {error}")
            })?;
        if inbox_path_identity != self.inbox_identity {
            return Err("durable recovery inbox path changed identity".to_string());
        }
        Ok(())
    }
}

fn ensure_recovery_current(
    anchor: &OutputParentAnchor,
    output_parent: &Dir,
    name: &str,
    path: &Path,
    identity: FileIdentity,
    root: &Dir,
) -> Result<(), String> {
    anchor.ensure_current(output_parent)?;
    let handle_identity = directory_identity(root)
        .map_err(|error| format!("durable recovery handle could not be confirmed: {error}"))?;
    if handle_identity != identity {
        return Err("durable recovery handle changed identity".to_string());
    }
    let name_identity = directory_identity_at(output_parent, name)
        .map_err(|error| format!("durable recovery name could not be confirmed: {error}"))?;
    if name_identity != identity {
        return Err("durable recovery name changed identity".to_string());
    }
    let reopened = open_absolute_directory_nofollow(path)
        .map_err(|error| format!("durable recovery path changed: {error}"))?;
    if directory_identity(&reopened)? != identity {
        return Err("durable recovery path no longer names the retained directory".to_string());
    }
    anchor.ensure_current(output_parent)?;
    Ok(())
}

fn last_known_recovery(error: String, path: &Path, contains_managed_data: bool) -> String {
    let parent_changed = error.contains("output parent changed");
    let mut diagnostic = if error.contains("recovery_last_known=") {
        error
    } else {
        format!("{error}; recovery_last_known={}", path.display())
    };
    if contains_managed_data {
        if parent_changed {
            diagnostic.push_str("; recovery was retained under that parent");
        } else {
            diagnostic.push_str(
                "; recovery contains retained managed data, but its current pathname could not be confirmed",
            );
        }
    }
    diagnostic
}

fn private_dir_builder() -> DirBuilder {
    #[cfg(unix)]
    {
        let mut builder = DirBuilder::new();
        builder.mode(0o700);
        builder
    }
    #[cfg(not(unix))]
    {
        DirBuilder::new()
    }
}

fn create_recovery_area(
    output_parent: &Dir,
    anchor: &OutputParentAnchor,
) -> Result<RecoveryArea, String> {
    for _ in 0..32 {
        anchor.ensure_current(output_parent)?;
        let name = unique_internal_name("recovery");
        let path = anchor.path.join(&name);
        match output_parent.create_dir_with(&name, &private_dir_builder()) {
            Ok(()) => {
                if let Err(error) = sync_directory(output_parent) {
                    return Err(last_known_recovery(
                        format!("cannot sync recovery parent: {error}"),
                        &path,
                        false,
                    ));
                }
                let root = match open_directory_at(output_parent, &name) {
                    Ok(root) => root,
                    Err(error) => {
                        return Err(last_known_recovery(
                            format!("cannot open durable recovery directory: {error}"),
                            &path,
                            false,
                        ));
                    }
                };
                let identity = match directory_identity(&root) {
                    Ok(identity) => identity,
                    Err(error) => {
                        return Err(last_known_recovery(
                            format!("cannot identify durable recovery directory: {error}"),
                            &path,
                            false,
                        ));
                    }
                };
                if let Err(error) =
                    ensure_recovery_current(anchor, output_parent, &name, &path, identity, &root)
                {
                    return Err(last_known_recovery(error, &path, false));
                }
                anchor
                    .ensure_current(output_parent)
                    .map_err(|error| last_known_recovery(error, &path, false))?;
                if let Err(error) = root.create_dir_with(UNITS_DIR, &private_dir_builder()) {
                    return Err(
                        if ensure_recovery_current(
                            anchor,
                            output_parent,
                            &name,
                            &path,
                            identity,
                            &root,
                        )
                        .is_ok()
                        {
                            format!(
                                "cannot create recovery units directory: {error}; recovery={}",
                                path.display()
                            )
                        } else {
                            last_known_recovery(
                                format!("cannot create recovery units directory: {error}"),
                                &path,
                                false,
                            )
                        },
                    );
                }
                anchor
                    .ensure_current(output_parent)
                    .map_err(|error| last_known_recovery(error, &path, false))?;
                if let Err(error) = root.create_dir_with(INBOX_DIR, &private_dir_builder()) {
                    return Err(
                        if ensure_recovery_current(
                            anchor,
                            output_parent,
                            &name,
                            &path,
                            identity,
                            &root,
                        )
                        .is_ok()
                        {
                            format!(
                                "cannot create recovery inbox directory: {error}; recovery={}",
                                path.display()
                            )
                        } else {
                            last_known_recovery(
                                format!("cannot create recovery inbox directory: {error}"),
                                &path,
                                false,
                            )
                        },
                    );
                }
                if let Err(error) = sync_directory(&root) {
                    return Err(
                        if ensure_recovery_current(
                            anchor,
                            output_parent,
                            &name,
                            &path,
                            identity,
                            &root,
                        )
                        .is_ok()
                        {
                            format!(
                                "cannot sync durable recovery directory: {error}; recovery={}",
                                path.display()
                            )
                        } else {
                            last_known_recovery(
                                format!("cannot sync durable recovery directory: {error}"),
                                &path,
                                false,
                            )
                        },
                    );
                }
                let units = match open_directory_at(&root, UNITS_DIR) {
                    Ok(units) => units,
                    Err(error) => {
                        return Err(
                            if ensure_recovery_current(
                                anchor,
                                output_parent,
                                &name,
                                &path,
                                identity,
                                &root,
                            )
                            .is_ok()
                            {
                                format!(
                                    "cannot open recovery units directory: {error}; recovery={}",
                                    path.display()
                                )
                            } else {
                                last_known_recovery(
                                    format!("cannot open recovery units directory: {error}"),
                                    &path,
                                    false,
                                )
                            },
                        );
                    }
                };
                let units_identity = match directory_identity(&units) {
                    Ok(identity) => identity,
                    Err(error) => {
                        return Err(last_known_recovery(
                            format!("cannot identify recovery units directory: {error}"),
                            &path,
                            false,
                        ));
                    }
                };
                let inbox = match open_directory_at(&root, INBOX_DIR) {
                    Ok(inbox) => inbox,
                    Err(error) => {
                        return Err(last_known_recovery(
                            format!("cannot open recovery inbox directory: {error}"),
                            &path,
                            false,
                        ));
                    }
                };
                let inbox_identity = match directory_identity(&inbox) {
                    Ok(identity) => identity,
                    Err(error) => {
                        return Err(last_known_recovery(
                            format!("cannot identify recovery inbox directory: {error}"),
                            &path,
                            false,
                        ));
                    }
                };
                let area = RecoveryArea {
                    name,
                    path,
                    identity,
                    root,
                    units,
                    units_identity,
                    inbox,
                    inbox_identity,
                };
                area.ensure_current(anchor, output_parent)
                    .map_err(|error| last_known_recovery(error, &area.path, false))?;
                return Ok(area);
            }
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => {
                return Err(format!("cannot create durable recovery directory: {error}"));
            }
        }
    }
    Err("cannot allocate unique durable recovery directory".to_string())
}

fn prepare_file(
    directory: &Dir,
    bytes: &[u8],
    location: ManagedDirectory,
    anchor: &OutputParentAnchor,
    anchor_parent: &Dir,
) -> Result<(String, FileIdentity), String> {
    for _ in 0..32 {
        anchor.ensure_current(anchor_parent)?;
        let temporary = unique_internal_name("prepared");
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        options.follow(FollowSymlinks::No);
        match directory.open_with(&temporary, &options) {
            Ok(mut file) => {
                file.write_all(bytes).map_err(|error| {
                    prepared_cleanup_skipped(
                        format!("cannot write prepared file: {error}"),
                        location,
                        &temporary,
                    )
                })?;
                file.sync_all().map_err(|error| {
                    prepared_cleanup_skipped(
                        format!("cannot sync prepared file: {error}"),
                        location,
                        &temporary,
                    )
                })?;
                let metadata = file.metadata().map_err(|error| {
                    prepared_cleanup_skipped(
                        format!("cannot identify prepared file: {error}"),
                        location,
                        &temporary,
                    )
                })?;
                return Ok((temporary, metadata_identity(&metadata)));
            }
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(format!("cannot create prepared file: {error}")),
        }
    }
    Err("cannot allocate unique prepared file".to_string())
}

fn move_to_recovery(
    source_directory: &Dir,
    source_name: &str,
    recovery_directory: &Dir,
    anchor: &OutputParentAnchor,
    anchor_parent: &Dir,
    recovery: &mut RecoverySession,
) -> Result<(), String> {
    anchor.ensure_current(anchor_parent)?;
    rename_noreplace(
        source_directory,
        source_name,
        recovery_directory,
        source_name,
    )
    .map_err(|error| format!("cannot move {source_name} to durable recovery: {error}"))?;
    recovery.note_managed_inode_recovered();
    sync_directory(source_directory)
        .map_err(|error| format!("cannot sync source after recovering {source_name}: {error}"))?;
    sync_directory(recovery_directory).map_err(|error| {
        format!("cannot sync durable recovery after moving {source_name}: {error}")
    })
}

fn validate_current_file(
    directory: &Dir,
    name: &str,
    expected: &ManagedFileSnapshot,
) -> Result<(), String> {
    let max_bytes = if name == MANIFEST_FILE {
        MAX_MANIFEST_BYTES
    } else {
        MAX_MANAGED_FILE_BYTES
    };
    let (bytes, identity) = read_regular_at(directory, name, max_bytes)?;
    if identity == expected.identity && bytes == expected.bytes {
        Ok(())
    } else {
        Err(format!(
            "{name}: changed after validation; refusing to overwrite or skip it"
        ))
    }
}

fn replace_managed_file(
    target: ManagedTarget<'_>,
    bytes: &[u8],
    expected: Option<&ManagedFileSnapshot>,
    output_parent: &Dir,
    anchor: &OutputParentAnchor,
    recovery: &mut RecoverySession,
    hook: &mut impl FnMut(&str),
) -> Result<bool, String> {
    let ManagedTarget {
        directory,
        name,
        location,
    } = target;
    if let Some(expected) = expected
        && expected.bytes == bytes
    {
        validate_current_file(directory, name, expected)?;
        return Ok(false);
    }

    let (prepared, prepared_identity) =
        prepare_file(directory, bytes, location, anchor, output_parent)?;
    let recovered = if let Some(expected) = expected {
        let recovery_was_empty = !recovery.contains_managed_data();
        let recovery_directory = match recovery.target(output_parent, location) {
            Ok(target) => target,
            Err(error) => return Err(prepared_cleanup_skipped(error, location, &prepared)),
        };
        if recovery_was_empty {
            hook("recovery:created");
            if let Err(error) = recovery.ensure(output_parent) {
                return Err(prepared_cleanup_skipped(error, location, &prepared));
            }
        }
        if let Err(error) = move_to_recovery(
            directory,
            name,
            &recovery_directory,
            anchor,
            output_parent,
            recovery,
        ) {
            return Err(prepared_cleanup_skipped(error, location, &prepared));
        }
        hook(&format!("write:{name}:detached"));
        if let Err(error) = validate_detached_file(&recovery_directory, name, name, expected) {
            hook(&format!("write:{name}:validation_failed"));
            return Err(prepared_cleanup_skipped(
                format!(
                    "cannot replace {name}: {error}; automatic restoration is disabled; recovered original remains in durable recovery"
                ),
                location,
                &prepared,
            ));
        }
        hook(&format!("write:{name}:recovered"));
        anchor
            .ensure_current(output_parent)
            .map_err(|error| prepared_cleanup_skipped(error, location, &prepared))?;
        true
    } else {
        hook(&format!("write:{name}:detached"));
        anchor
            .ensure_current(output_parent)
            .map_err(|error| prepared_cleanup_skipped(error, location, &prepared))?;
        false
    };

    anchor
        .ensure_current(output_parent)
        .map_err(|error| prepared_cleanup_skipped(error, location, &prepared))?;
    if let Err(error) = rename_noreplace(directory, &prepared, directory, name) {
        let error = if recovered {
            format!(
                "cannot install prepared {name} without replacement: {error}; recovered original remains in durable recovery"
            )
        } else {
            format!(
                "cannot install prepared {name} without replacement: {error}; prepared file was never installed"
            )
        };
        return Err(prepared_cleanup_skipped(error, location, &prepared));
    }
    sync_directory(directory)?;

    let installed = managed_identity_at(directory, name)?;
    if installed != Some(prepared_identity) {
        return Err(format!(
            "installed {name} changed before settlement; {}",
            if recovered {
                "recovered original retained in durable recovery"
            } else {
                "no prior file existed"
            }
        ));
    }
    Ok(true)
}

fn delete_managed_file(
    target: ManagedTarget<'_>,
    expected: &ManagedFileSnapshot,
    output_parent: &Dir,
    anchor: &OutputParentAnchor,
    recovery: &mut RecoverySession,
    hook: &mut impl FnMut(&str),
) -> Result<(), String> {
    let ManagedTarget {
        directory,
        name,
        location,
    } = target;
    let recovery_was_empty = !recovery.contains_managed_data();
    let recovery_directory = recovery.target(output_parent, location)?;
    if recovery_was_empty {
        hook("recovery:created");
        recovery.ensure(output_parent)?;
    }
    move_to_recovery(
        directory,
        name,
        &recovery_directory,
        anchor,
        output_parent,
        recovery,
    )?;
    hook(&format!("delete:{name}:detached"));
    if let Err(error) = validate_detached_file(&recovery_directory, name, name, expected) {
        hook(&format!("delete:{name}:validation_failed"));
        return Err(format!(
            "cannot delete {name}: {error}; automatic restoration is disabled; recovered original remains in durable recovery"
        ));
    }
    hook(&format!("delete:{name}:recovered"));
    anchor.ensure_current(output_parent)?;
    if managed_identity_at(directory, name)?.is_some() {
        return Err(format!(
            "{name} reappeared after recovery move; concurrent path left untouched and recovered original retained in durable recovery"
        ));
    }
    Ok(())
}

fn collect_name_mismatches(
    names: &[String],
    expected: &BTreeSet<String>,
    allow_regular_files: bool,
    label: &str,
    findings: &mut Vec<String>,
) {
    for name in names {
        if !(expected.contains(name) || allow_regular_files && is_safe_inbox_name(name)) {
            findings.push(format!("{label}/{name}: unexpected path"));
        }
    }
    for name in expected {
        if !names.contains(name) {
            findings.push(format!("{label}/{name}: missing path"));
        }
    }
}

fn tree_snapshot_sha256(
    handles: &TreeHandles,
    root_names: &[String],
    units_names: &[String],
    inbox_names: &[String],
    manifest: &[u8],
    memory: &[u8],
    units: &BTreeMap<String, Vec<u8>>,
) -> String {
    let mut hasher = Sha256::new();
    for identity in [
        handles.root_identity,
        handles.units_identity,
        handles.inbox_identity,
    ] {
        hasher.update(identity.device.to_be_bytes());
        hasher.update(identity.inode.to_be_bytes());
    }
    for (label, names) in [
        ("root", root_names),
        ("units", units_names),
        ("inbox", inbox_names),
    ] {
        hasher.update(label.as_bytes());
        for name in names {
            hasher.update((name.len() as u64).to_be_bytes());
            hasher.update(name.as_bytes());
        }
    }
    for (name, bytes) in [(MANIFEST_FILE, manifest), (MEMORY_FILE, memory)] {
        hasher.update(name.as_bytes());
        hasher.update((bytes.len() as u64).to_be_bytes());
        hasher.update(bytes);
    }
    for (name, bytes) in units {
        hasher.update(name.as_bytes());
        hasher.update((bytes.len() as u64).to_be_bytes());
        hasher.update(bytes);
    }
    format!("{:x}", hasher.finalize())
}

fn is_safe_relative_unit_path(value: &str) -> bool {
    let path = Path::new(value);
    let components = path.components().collect::<Vec<_>>();
    components.len() == 2
        && matches!(components[0], Component::Normal(name) if name == UNITS_DIR)
        && matches!(components[1], Component::Normal(_))
}

fn is_safe_inbox_name(name: &str) -> bool {
    let lowercase = name.to_ascii_lowercase();
    let stem = lowercase.split('.').next().unwrap_or_default();
    let windows_device = matches!(stem, "con" | "prn" | "aux" | "nul" | "clock$")
        || stem.strip_prefix("com").is_some_and(|suffix| {
            matches!(suffix, "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9")
        })
        || stem.strip_prefix("lpt").is_some_and(|suffix| {
            matches!(suffix, "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9")
        });
    !name.is_empty()
        && name.ends_with(".md")
        && !name.starts_with('.')
        && !matches!(lowercase.as_str(), "memory.md" | "memphant-export.json")
        && !windows_device
        && name
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '-' | '_' | '.'))
}

fn require_single_line(field: &str, value: &str) -> Result<(), String> {
    if value.is_empty() || value.contains(['\n', '\r']) {
        Err(format!("{field} must be one non-empty line"))
    } else {
        Ok(())
    }
}

fn require_sha256(field: &str, value: &str) -> Result<(), String> {
    if value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        Ok(())
    } else {
        Err(format!("{field} must be a lowercase SHA-256 digest"))
    }
}

fn require_rfc3339_utc(field: &str, value: &str) -> Result<(), String> {
    if (value.ends_with('Z') || value.ends_with("+00:00"))
        && value.parse::<jiff::Timestamp>().is_ok()
    {
        Ok(())
    } else {
        Err(format!("{field} must be RFC3339 UTC"))
    }
}

fn sha256(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn strict_from_slice<T: DeserializeOwned>(bytes: &[u8]) -> Result<T, String> {
    let mut deserializer = serde_json::Deserializer::from_slice(bytes);
    let strict = StrictValue::deserialize(&mut deserializer).map_err(|error| error.to_string())?;
    deserializer.end().map_err(|error| error.to_string())?;
    serde_json::from_value(strict.0).map_err(|error| error.to_string())
}

struct StrictValue(Value);

impl<'de> Deserialize<'de> for StrictValue {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        deserializer.deserialize_any(StrictValueVisitor)
    }
}

struct StrictValueVisitor;

impl<'de> Visitor<'de> for StrictValueVisitor {
    type Value = StrictValue;

    fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("a JSON value without duplicate object keys")
    }

    fn visit_bool<E: de::Error>(self, value: bool) -> Result<Self::Value, E> {
        Ok(StrictValue(Value::Bool(value)))
    }

    fn visit_i64<E: de::Error>(self, value: i64) -> Result<Self::Value, E> {
        Ok(StrictValue(Value::Number(Number::from(value))))
    }

    fn visit_u64<E: de::Error>(self, value: u64) -> Result<Self::Value, E> {
        Ok(StrictValue(Value::Number(Number::from(value))))
    }

    fn visit_f64<E: de::Error>(self, value: f64) -> Result<Self::Value, E> {
        Number::from_f64(value)
            .map(Value::Number)
            .map(StrictValue)
            .ok_or_else(|| E::custom("non-finite JSON number"))
    }

    fn visit_str<E: de::Error>(self, value: &str) -> Result<Self::Value, E> {
        Ok(StrictValue(Value::String(value.to_string())))
    }

    fn visit_string<E: de::Error>(self, value: String) -> Result<Self::Value, E> {
        Ok(StrictValue(Value::String(value)))
    }

    fn visit_none<E: de::Error>(self) -> Result<Self::Value, E> {
        Ok(StrictValue(Value::Null))
    }

    fn visit_unit<E: de::Error>(self) -> Result<Self::Value, E> {
        Ok(StrictValue(Value::Null))
    }

    fn visit_seq<A: SeqAccess<'de>>(self, mut sequence: A) -> Result<Self::Value, A::Error> {
        let mut values = Vec::new();
        while let Some(value) = sequence.next_element::<StrictValue>()? {
            values.push(value.0);
        }
        Ok(StrictValue(Value::Array(values)))
    }

    fn visit_map<A: MapAccess<'de>>(self, mut object: A) -> Result<Self::Value, A::Error> {
        let mut values = Map::new();
        while let Some(key) = object.next_key::<String>()? {
            if values.contains_key(&key) {
                return Err(de::Error::custom(format!("duplicate JSON key {key}")));
            }
            let value = object.next_value::<StrictValue>()?;
            values.insert(key, value.0);
        }
        Ok(StrictValue(Value::Object(values)))
    }
}

#[cfg(test)]
mod tests {
    use super::{
        MEMORY_FILE, OutputState, canonical_projection_fingerprint, encode_file_sync_request,
        inspect_output, is_safe_inbox_name, open_directory_at, render_projection,
        replace_projection, replace_projection_with_hook, sha256, sync_directory,
        validate_sync_receipt,
    };

    #[test]
    fn nofollow_directory_handles_are_syncable() {
        let temporary = tempfile::tempdir().unwrap();
        let parent =
            cap_std::fs::Dir::open_ambient_dir(temporary.path(), cap_std::ambient_authority())
                .unwrap();
        parent.create_dir("child").unwrap();
        let child = open_directory_at(&parent, "child").unwrap();
        sync_directory(&child).unwrap();
    }
    use memphant_types::{
        ActorId, AgentNodeId, CanonicalProjectionResponse, CanonicalProjectionUnit,
        FileSyncOperation, FileSyncOperationResult, FileSyncRequest, FileSyncResult,
        MAX_FILE_SYNC_REQUEST_ENCODED_BYTES, MemoryKind, ScopeId, SubjectId, TenantId, UnitId,
    };

    #[test]
    fn inbox_names_reject_reserved_and_device_components() {
        assert!(is_safe_inbox_name("queue-decision.md"));
        for name in [
            "MEMORY.md",
            "memphant-export.json",
            "CON.md",
            "nul.md",
            "COM1.md",
            "lpt9.md",
            ".hidden.md",
            "nested/name.md",
        ] {
            assert!(!is_safe_inbox_name(name), "reserved inbox name {name}");
        }
    }

    #[test]
    fn committed_receipts_must_match_operation_count_and_variant() {
        let operation = FileSyncOperation::Retain {
            fact_key: "decision:test".to_string(),
            predicate: "states".to_string(),
            body: "Use the tested path.".to_string(),
            confidence: 1.0,
            valid_from: None,
            valid_to: None,
        };
        let request = FileSyncRequest {
            subject_id: SubjectId::new(),
            scope_id: ScopeId::new(),
            actor_id: ActorId::new(),
            agent_node_id: AgentNodeId::new(),
            subject_generation: 0,
            base_fingerprint: "a".repeat(64),
            plan_sha256: "b".repeat(64),
            observed_at: "2026-07-23T00:00:00Z".to_string(),
            operations: vec![operation],
        };
        let mut receipt = FileSyncResult {
            base_fingerprint: request.base_fingerprint.clone(),
            fingerprint: "c".repeat(64),
            evaluated_at: "2026-07-23T00:00:00Z".to_string(),
            plan_sha256: request.plan_sha256.clone(),
            operations: Vec::new(),
        };
        assert!(
            validate_sync_receipt(&request, &receipt)
                .unwrap_err()
                .contains("does not match")
        );
        receipt.operations = vec![FileSyncOperationResult::Forget {
            memory_unit_id: UnitId::new(),
            deletion_generation: 1,
            invalidated: Vec::new(),
        }];
        assert!(
            validate_sync_receipt(&request, &receipt)
                .unwrap_err()
                .contains("variants")
        );
        receipt.operations = vec![FileSyncOperationResult::Retain {
            created: vec![UnitId::new()],
        }];
        for invalid in ["not-a-timestamp", "2026-07-23T01:00:00+01:00"] {
            receipt.evaluated_at = invalid.to_string();
            assert!(
                validate_sync_receipt(&request, &receipt)
                    .unwrap_err()
                    .contains("evaluated_at must be RFC3339 UTC")
            );
        }
        receipt.evaluated_at = "2026-07-23T00:00:00+00:00".to_string();
        validate_sync_receipt(&request, &receipt).unwrap();
    }

    #[test]
    fn file_sync_request_limit_is_exact_encoded_json_bytes() {
        let request = |body: String| FileSyncRequest {
            subject_id: SubjectId::new(),
            scope_id: ScopeId::new(),
            actor_id: ActorId::new(),
            agent_node_id: AgentNodeId::new(),
            subject_generation: 0,
            base_fingerprint: "a".repeat(64),
            plan_sha256: "b".repeat(64),
            observed_at: "2026-07-23T00:00:00Z".to_string(),
            operations: vec![FileSyncOperation::Retain {
                fact_key: "decision:test".to_string(),
                predicate: "states".to_string(),
                body,
                confidence: 1.0,
                valid_from: None,
                valid_to: None,
            }],
        };
        let empty = serde_json::to_vec(&request(String::new())).unwrap().len();
        let at_limit = request("x".repeat(MAX_FILE_SYNC_REQUEST_ENCODED_BYTES - empty));
        let encoded = encode_file_sync_request(&at_limit).unwrap();
        assert_eq!(encoded.len(), MAX_FILE_SYNC_REQUEST_ENCODED_BYTES);

        let over_limit = request("x".repeat(MAX_FILE_SYNC_REQUEST_ENCODED_BYTES - empty + 1));
        let error = encode_file_sync_request(&over_limit).expect_err("oversize request accepted");
        assert!(matches!(error, super::SyncFailure::Invalid(_)));
    }

    #[test]
    fn sync_refuses_a_replaced_recovery_inbox_before_consuming_a_fact() {
        let directory = tempfile::tempdir().unwrap();
        let output = directory.path().join("memory");
        let rendered = rendered_projection(&[(1, "alpha")]);
        let absent = inspect_output(&output).unwrap();
        replace_projection(&output, &absent, &rendered).unwrap();
        let OutputState::Existing(existing) = inspect_output(&output).unwrap() else {
            panic!("expected existing projection");
        };
        std::fs::write(
            output.join(super::INBOX_DIR).join("new.md"),
            "# decision:new\n\nUse the new path.\n",
        )
        .unwrap();
        let snapshot = super::scan_sync_handles(&existing.handles).unwrap();

        let result = super::write_rendered_projection(
            &existing.handles,
            Some(&existing),
            &rendered,
            &snapshot.inbox_files,
            &mut |point| {
                if point == "recovery:created" {
                    let recovery = find_recovery(directory.path());
                    std::fs::rename(
                        recovery.join(super::INBOX_DIR),
                        recovery.join("displaced-inbox"),
                    )
                    .unwrap();
                    std::fs::create_dir(recovery.join(super::INBOX_DIR)).unwrap();
                }
            },
        );

        let error = result.expect_err("replaced recovery inbox was accepted");
        assert!(error.contains("durable recovery inbox path changed identity"));
        assert!(output.join(super::INBOX_DIR).join("new.md").is_file());
    }

    #[test]
    fn sync_retains_a_late_inbox_write_in_durable_recovery() {
        let directory = tempfile::tempdir().unwrap();
        let output = directory.path().join("memory");
        let rendered = rendered_projection(&[(1, "alpha")]);
        let absent = inspect_output(&output).unwrap();
        replace_projection(&output, &absent, &rendered).unwrap();
        let OutputState::Existing(existing) = inspect_output(&output).unwrap() else {
            panic!("expected existing projection");
        };
        let inbox = output.join(super::INBOX_DIR).join("new.md");
        std::fs::write(&inbox, "# decision:new\n\nUse the new path.\n").unwrap();
        let mut held = std::fs::OpenOptions::new()
            .write(true)
            .open(&inbox)
            .unwrap();
        let snapshot = super::scan_sync_handles(&existing.handles).unwrap();

        let result = super::write_rendered_projection(
            &existing.handles,
            Some(&existing),
            &rendered,
            &snapshot.inbox_files,
            &mut |point| {
                if point == "consume:new.md:detached" {
                    held.set_len(0).unwrap();
                    std::io::Write::write_all(&mut held, b"late inbox edit").unwrap();
                    held.sync_all().unwrap();
                }
            },
        );

        let error = result.expect_err("late inbox edit was silently consumed");
        assert!(error.contains("unexpected inode remains in durable recovery"));
        assert_eq!(
            std::fs::read_to_string(
                find_recovery(directory.path())
                    .join(super::INBOX_DIR)
                    .join("new.md")
            )
            .unwrap(),
            "late inbox edit"
        );
    }

    #[test]
    fn sync_preserves_original_and_substitute_when_source_path_changes_before_move() {
        let directory = tempfile::tempdir().unwrap();
        let output = directory.path().join("memory");
        let rendered = rendered_projection(&[(1, "alpha")]);
        let absent = inspect_output(&output).unwrap();
        replace_projection(&output, &absent, &rendered).unwrap();
        let OutputState::Existing(existing) = inspect_output(&output).unwrap() else {
            panic!("expected existing projection");
        };
        let inbox = output.join(super::INBOX_DIR);
        let source = inbox.join("new.md");
        let preserved = inbox.join("original-preserved.md");
        let original = b"# decision:new\n\nUse the original fact.\n";
        let substitute = b"# decision:substitute\n\nDo not consume the substitute.\n";
        std::fs::write(&source, original).unwrap();
        let snapshot = super::scan_sync_handles(&existing.handles).unwrap();

        let result = super::write_rendered_projection(
            &existing.handles,
            Some(&existing),
            &rendered,
            &snapshot.inbox_files,
            &mut |point| {
                if point == "recovery:created" {
                    std::fs::rename(&source, &preserved).unwrap();
                    std::fs::write(&source, substitute).unwrap();
                }
            },
        );

        let error = result.expect_err("a substituted source path was silently consumed");
        assert!(error.contains("unexpected inode remains in durable recovery"));
        assert_eq!(std::fs::read(&preserved).unwrap(), original);
        assert_eq!(
            std::fs::read(
                find_recovery(directory.path())
                    .join(super::INBOX_DIR)
                    .join("new.md")
            )
            .unwrap(),
            substitute
        );
    }

    #[cfg(windows)]
    #[test]
    fn windows_noreplace_backend_preserves_an_existing_destination() {
        let directory = tempfile::tempdir().unwrap();
        std::fs::write(directory.path().join("source"), "source").unwrap();
        std::fs::write(directory.path().join("destination"), "destination").unwrap();
        std::fs::create_dir(directory.path().join("source-directory")).unwrap();
        std::fs::write(
            directory.path().join("source-directory").join("source"),
            "source",
        )
        .unwrap();
        std::fs::create_dir(directory.path().join("destination-directory")).unwrap();
        std::fs::write(
            directory
                .path()
                .join("destination-directory")
                .join("destination"),
            "destination",
        )
        .unwrap();
        let capability =
            cap_std::fs::Dir::open_ambient_dir(directory.path(), cap_std::ambient_authority())
                .unwrap();

        let file_error =
            super::rename_noreplace(&capability, "source", &capability, "destination").unwrap_err();
        let directory_error = super::rename_noreplace(
            &capability,
            "source-directory",
            &capability,
            "destination-directory",
        )
        .unwrap_err();

        assert_eq!(file_error.kind(), std::io::ErrorKind::AlreadyExists);
        assert_eq!(directory_error.kind(), std::io::ErrorKind::AlreadyExists);
        assert_eq!(
            std::fs::read_to_string(directory.path().join("source")).unwrap(),
            "source"
        );
        assert_eq!(
            std::fs::read_to_string(directory.path().join("destination")).unwrap(),
            "destination"
        );
        assert_eq!(
            std::fs::read_to_string(directory.path().join("source-directory").join("source"))
                .unwrap(),
            "source"
        );
        assert_eq!(
            std::fs::read_to_string(
                directory
                    .path()
                    .join("destination-directory")
                    .join("destination")
            )
            .unwrap(),
            "destination"
        );
    }

    #[cfg(windows)]
    #[test]
    fn windows_noreplace_backend_moves_files_and_directories() {
        let directory = tempfile::tempdir().unwrap();
        std::fs::write(directory.path().join("source-file"), "source").unwrap();
        std::fs::create_dir(directory.path().join("source-directory")).unwrap();
        std::fs::write(
            directory.path().join("source-directory").join("sentinel"),
            "sentinel",
        )
        .unwrap();
        let capability =
            cap_std::fs::Dir::open_ambient_dir(directory.path(), cap_std::ambient_authority())
                .unwrap();

        super::rename_noreplace(&capability, "source-file", &capability, "destination-file")
            .unwrap();
        super::rename_noreplace(
            &capability,
            "source-directory",
            &capability,
            "destination-directory",
        )
        .unwrap();

        assert!(!directory.path().join("source-file").exists());
        assert_eq!(
            std::fs::read_to_string(directory.path().join("destination-file")).unwrap(),
            "source"
        );
        assert!(!directory.path().join("source-directory").exists());
        assert_eq!(
            std::fs::read_to_string(
                directory
                    .path()
                    .join("destination-directory")
                    .join("sentinel")
            )
            .unwrap(),
            "sentinel"
        );
    }

    #[cfg(windows)]
    #[test]
    fn windows_noreplace_backend_moves_into_a_distinct_retained_directory() {
        let directory = tempfile::tempdir().unwrap();
        std::fs::create_dir(directory.path().join("source")).unwrap();
        std::fs::create_dir(directory.path().join("recovery")).unwrap();
        std::fs::write(directory.path().join("source").join("unit.md"), "original").unwrap();
        let parent =
            cap_std::fs::Dir::open_ambient_dir(directory.path(), cap_std::ambient_authority())
                .unwrap();
        let source = parent.open_dir("source").unwrap();
        let recovery = parent.open_dir("recovery").unwrap();

        super::rename_noreplace(&source, "unit.md", &recovery, "unit.md").unwrap();

        assert!(!directory.path().join("source").join("unit.md").exists());
        assert_eq!(
            std::fs::read_to_string(directory.path().join("recovery").join("unit.md")).unwrap(),
            "original"
        );
    }

    #[cfg(windows)]
    #[test]
    fn windows_absent_install_drops_staging_handles_and_reopens_from_parent() {
        let directory = tempfile::tempdir().unwrap();
        let output = directory.path().join("memory");
        let absent = inspect_output(&output).unwrap();
        let rendered = rendered_projection(&[(1, "windows staged projection")]);

        replace_projection(&output, &absent, &rendered).unwrap();

        assert!(super::verify_export(&output).is_ok());
        assert!(recovery_directories(directory.path()).is_empty());
    }

    #[test]
    fn compile_refuses_a_managed_file_replaced_during_writes() {
        let directory = tempfile::tempdir().unwrap();
        let output = directory.path().join("memory");
        let initial = rendered_projection(&[(1, "alpha before"), (2, "beta before")]);
        let empty = inspect_output(&output).unwrap();
        replace_projection(&output, &empty, &initial).unwrap();
        let existing = inspect_output(&output).unwrap();
        assert!(matches!(existing, OutputState::Existing(_)));

        let replacement = rendered_projection(&[(1, "alpha after"), (2, "beta after")]);
        let first_path = replacement.units.keys().next().unwrap().clone();
        let second_path = replacement.units.keys().nth(1).unwrap().clone();
        let second_name = std::path::Path::new(&second_path)
            .file_name()
            .unwrap()
            .to_str()
            .unwrap();
        let victim = output.join(&second_path);
        let mut swapped = false;
        let result = replace_projection_with_hook(&output, &existing, &replacement, |written| {
            if written == first_path && !swapped {
                std::fs::write(&victim, "concurrent sentinel").unwrap();
                swapped = true;
            }
        });

        let error = result.unwrap_err();
        assert!(error.contains("detached file differs"), "{error}");
        assert!(
            error.contains("automatic restoration is disabled"),
            "{error}"
        );
        assert!(!victim.exists());
        assert_eq!(
            std::fs::read_to_string(
                find_recovery(directory.path())
                    .join(super::UNITS_DIR)
                    .join(second_name)
            )
            .unwrap(),
            "concurrent sentinel"
        );
    }

    #[test]
    fn compile_never_opens_an_absent_root_after_creating_its_name() {
        let directory = tempfile::tempdir().unwrap();
        let output = directory.path().join("memory");
        let displaced = directory.path().join("displaced-memory");
        let absent = inspect_output(&output).unwrap();
        let rendered = rendered_projection(&[(1, "alpha")]);
        let result = replace_projection_with_hook(&output, &absent, &rendered, |point| {
            if point == "absent:before_install" {
                if output.exists() {
                    std::fs::rename(&output, &displaced).unwrap();
                }
                std::fs::create_dir(&output).unwrap();
                std::fs::write(output.join("sentinel"), "concurrent root").unwrap();
            }
        });

        assert!(result.is_err(), "concurrent root was accepted");
        assert_eq!(
            super::directory_names(
                &cap_std::fs::Dir::open_ambient_dir(&output, cap_std::ambient_authority()).unwrap()
            )
            .unwrap(),
            ["sentinel"]
        );
        assert_eq!(
            std::fs::read_to_string(output.join("sentinel")).unwrap(),
            "concurrent root"
        );
        let staging = find_internal(directory.path(), ".memphant-stage-");
        assert!(super::verify_export(&staging).is_ok());
    }

    #[test]
    fn compile_atomically_installs_a_nested_absent_projection() {
        let directory = tempfile::tempdir().unwrap();
        let output = directory.path().join("missing/parents/memory");
        let absent = inspect_output(&output).unwrap();
        let rendered = rendered_projection(&[(1, "nested projection")]);

        replace_projection(&output, &absent, &rendered).unwrap();

        assert!(super::verify_export(&output).is_ok());
        let absent = match &absent {
            OutputState::Absent(absent) => absent,
            _ => unreachable!("test begins with an absent output"),
        };
        let reopened = super::open_installed_tree_from_parent(
            &absent.parent,
            &absent.missing_components,
            &absent.anchor,
            &absent.anchor_parent,
        )
        .unwrap();
        fn no_hook(_: &str) {}
        let mut no_hook: fn(&str) = no_hook;
        let reopened_snapshot =
            super::validate_rendered_projection_twice(&reopened, &rendered, &mut no_hook).unwrap();
        assert_eq!(reopened_snapshot.0, rendered.manifest);
        assert!(std::fs::read_dir(directory.path()).unwrap().all(|entry| {
            !entry
                .unwrap()
                .file_name()
                .to_string_lossy()
                .starts_with(".memphant-stage-")
        }));
    }

    #[test]
    fn compile_does_not_overwrite_a_target_changed_after_the_old_check() {
        let directory = tempfile::tempdir().unwrap();
        let output = directory.path().join("memory");
        let initial = rendered_projection(&[(1, "alpha before")]);
        let absent = inspect_output(&output).unwrap();
        replace_projection(&output, &absent, &initial).unwrap();
        let existing = inspect_output(&output).unwrap();
        let replacement = rendered_projection(&[(1, "alpha after")]);
        let unit_path = replacement.units.keys().next().unwrap().clone();
        let unit_name = std::path::Path::new(&unit_path)
            .file_name()
            .unwrap()
            .to_str()
            .unwrap()
            .to_string();
        let victim = output.join(&unit_path);
        let event = format!("write:{unit_name}:detached");

        let result = replace_projection_with_hook(&output, &existing, &replacement, |point| {
            if point == event {
                std::fs::write(&victim, "compare-use sentinel").unwrap();
            }
        });

        assert!(result.is_err(), "compare/use replacement was accepted");
        assert_eq!(
            std::fs::read_to_string(victim).unwrap(),
            "compare-use sentinel"
        );
        let backup = find_recovery(directory.path())
            .join(super::UNITS_DIR)
            .join(unit_name);
        assert!(
            std::fs::read_to_string(backup)
                .unwrap()
                .contains("alpha before")
        );
    }

    #[test]
    fn compile_preserves_late_writes_to_a_replaced_inode_in_durable_recovery() {
        let directory = tempfile::tempdir().unwrap();
        let output = directory.path().join("memory");
        let initial = rendered_projection(&[(1, "alpha before")]);
        let absent = inspect_output(&output).unwrap();
        replace_projection(&output, &absent, &initial).unwrap();
        let existing = inspect_output(&output).unwrap();
        let replacement = rendered_projection(&[(1, "alpha after")]);
        let unit_path = replacement.units.keys().next().unwrap().clone();
        let unit_name = std::path::Path::new(&unit_path)
            .file_name()
            .unwrap()
            .to_str()
            .unwrap()
            .to_string();
        let victim = output.join(&unit_path);
        let mut held = std::fs::OpenOptions::new()
            .write(true)
            .open(&victim)
            .unwrap();
        let event = format!("write:{unit_name}:recovered");

        replace_projection_with_hook(&output, &existing, &replacement, |point| {
            if point == event {
                held.set_len(0).unwrap();
                std::io::Write::write_all(&mut held, b"late replacement edit").unwrap();
                held.sync_all().unwrap();
            }
        })
        .unwrap();

        assert!(
            std::fs::read_to_string(&victim)
                .unwrap()
                .contains("alpha after")
        );
        let recovery = find_recovery(directory.path());
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                std::fs::metadata(&recovery).unwrap().permissions().mode() & 0o777,
                0o700
            );
        }
        assert_eq!(
            std::fs::read_to_string(recovery.join(super::UNITS_DIR).join(unit_name)).unwrap(),
            "late replacement edit"
        );
    }

    #[test]
    fn compile_refuses_a_stale_managed_file_replaced_before_deletion() {
        let directory = tempfile::tempdir().unwrap();
        let output = directory.path().join("memory");
        let initial = rendered_projection(&[(1, "alpha before"), (2, "beta before")]);
        let empty = inspect_output(&output).unwrap();
        replace_projection(&output, &empty, &initial).unwrap();
        let existing = inspect_output(&output).unwrap();
        assert!(matches!(existing, OutputState::Existing(_)));

        let replacement = rendered_projection(&[(1, "alpha after")]);
        let stale_path = initial.units.keys().nth(1).unwrap().clone();
        let stale_name = std::path::Path::new(&stale_path)
            .file_name()
            .unwrap()
            .to_str()
            .unwrap();
        let victim = output.join(&stale_path);
        let mut swapped = false;
        let result = replace_projection_with_hook(&output, &existing, &replacement, |written| {
            if written == MEMORY_FILE && !swapped {
                std::fs::write(&victim, "concurrent stale sentinel").unwrap();
                swapped = true;
            }
        });

        let error = result.unwrap_err();
        assert!(error.contains("detached file differs"), "{error}");
        assert!(
            error.contains("automatic restoration is disabled"),
            "{error}"
        );
        assert!(!victim.exists());
        assert_eq!(
            std::fs::read_to_string(
                find_recovery(directory.path())
                    .join(super::UNITS_DIR)
                    .join(stale_name)
            )
            .unwrap(),
            "concurrent stale sentinel"
        );
    }

    #[test]
    fn compile_does_not_delete_a_target_changed_after_the_old_check() {
        let directory = tempfile::tempdir().unwrap();
        let output = directory.path().join("memory");
        let initial = rendered_projection(&[(1, "alpha"), (2, "beta")]);
        let absent = inspect_output(&output).unwrap();
        replace_projection(&output, &absent, &initial).unwrap();
        let existing = inspect_output(&output).unwrap();
        let replacement = rendered_projection(&[(1, "alpha")]);
        let stale_path = initial.units.keys().nth(1).unwrap().clone();
        let stale_name = std::path::Path::new(&stale_path)
            .file_name()
            .unwrap()
            .to_str()
            .unwrap()
            .to_string();
        let victim = output.join(&stale_path);
        let event = format!("delete:{stale_name}:detached");

        let result = replace_projection_with_hook(&output, &existing, &replacement, |point| {
            if point == event {
                std::fs::write(&victim, "delete-use sentinel").unwrap();
            }
        });

        assert!(result.is_err(), "compare/use deletion was accepted");
        assert_eq!(
            std::fs::read_to_string(victim).unwrap(),
            "delete-use sentinel"
        );
        let backup = find_recovery(directory.path())
            .join(super::UNITS_DIR)
            .join(stale_name);
        assert!(std::fs::read_to_string(backup).unwrap().contains("beta"));
    }

    #[test]
    fn compile_preserves_late_writes_to_a_deleted_inode_in_durable_recovery() {
        let directory = tempfile::tempdir().unwrap();
        let output = directory.path().join("memory");
        let initial = rendered_projection(&[(1, "alpha"), (2, "beta")]);
        let absent = inspect_output(&output).unwrap();
        replace_projection(&output, &absent, &initial).unwrap();
        let existing = inspect_output(&output).unwrap();
        let replacement = rendered_projection(&[(1, "alpha")]);
        let stale_path = initial.units.keys().nth(1).unwrap().clone();
        let stale_name = std::path::Path::new(&stale_path)
            .file_name()
            .unwrap()
            .to_str()
            .unwrap()
            .to_string();
        let victim = output.join(&stale_path);
        let mut held = std::fs::OpenOptions::new()
            .write(true)
            .open(&victim)
            .unwrap();
        let event = format!("delete:{stale_name}:recovered");

        replace_projection_with_hook(&output, &existing, &replacement, |point| {
            if point == event {
                held.set_len(0).unwrap();
                std::io::Write::write_all(&mut held, b"late deletion edit").unwrap();
                held.sync_all().unwrap();
            }
        })
        .unwrap();

        assert!(!victim.exists());
        let recovery = find_recovery(directory.path());
        assert_eq!(
            std::fs::read_to_string(recovery.join(super::UNITS_DIR).join(stale_name)).unwrap(),
            "late deletion edit"
        );
    }

    #[test]
    fn anchor_reopen_rejects_a_component_renamed_during_the_walk() {
        let directory = tempfile::tempdir().unwrap();
        let parent = directory.path().join("output-parent");
        let displaced_parent = directory.path().join("displaced-output-parent");
        std::fs::create_dir(&parent).unwrap();
        let anchor = std::fs::canonicalize(&parent).unwrap();
        let mut swapped = false;

        let error = super::open_absolute_directory_nofollow_with_hook(&anchor, |component| {
            if component == std::ffi::OsStr::new("output-parent") && !swapped {
                std::fs::rename(&parent, &displaced_parent).unwrap();
                std::fs::create_dir(&parent).unwrap();
                swapped = true;
            }
        })
        .unwrap_err();

        assert!(error.contains("changed during reopen"), "{error}");
        assert!(parent.is_dir());
        assert!(displaced_parent.is_dir());
    }

    #[cfg(windows)]
    #[test]
    fn windows_anchor_parser_retains_verbatim_disk_and_unc_roots() {
        let (disk_root, disk_names) =
            super::absolute_directory_components(std::path::Path::new(r"\\?\C:\parent\memory"))
                .unwrap();
        assert_eq!(disk_root, std::path::Path::new(r"\\?\C:\"));
        assert_eq!(
            disk_names,
            [
                std::ffi::OsString::from("parent"),
                std::ffi::OsString::from("memory")
            ]
        );

        let (unc_root, unc_names) = super::absolute_directory_components(std::path::Path::new(
            r"\\?\UNC\server\share\parent\memory",
        ))
        .unwrap();
        assert_eq!(unc_root, std::path::Path::new(r"\\?\UNC\server\share\"));
        assert_eq!(
            unc_names,
            [
                std::ffi::OsString::from("parent"),
                std::ffi::OsString::from("memory")
            ]
        );
    }

    #[test]
    fn compile_refuses_a_parent_swap_before_its_first_mutation() {
        let directory = tempfile::tempdir().unwrap();
        let parent = directory.path().join("output-parent");
        let displaced_parent = directory.path().join("displaced-output-parent");
        std::fs::create_dir(&parent).unwrap();
        let output = parent.join("memory");
        let initial = rendered_projection(&[(1, "alpha before")]);
        let absent = inspect_output(&output).unwrap();
        replace_projection(&output, &absent, &initial).unwrap();
        let existing = inspect_output(&output).unwrap();
        let original_unit = initial.units.keys().next().unwrap();
        let original_bytes = std::fs::read(output.join(original_unit)).unwrap();
        let replacement = rendered_projection(&[(1, "alpha after")]);
        let mut swapped = false;

        let result = replace_projection_with_hook(&output, &existing, &replacement, |point| {
            if point == "parent:before_first_mutation" && !swapped {
                std::fs::rename(&parent, &displaced_parent).unwrap();
                std::fs::create_dir(&parent).unwrap();
                std::fs::write(parent.join("sentinel"), "replacement parent").unwrap();
                swapped = true;
            }
        });

        let error = result.expect_err("a replaced output-parent anchor was accepted");
        assert!(error.contains("output parent changed"), "{error}");
        assert_eq!(
            std::fs::read_to_string(parent.join("sentinel")).unwrap(),
            "replacement parent"
        );
        assert_eq!(
            std::fs::read(displaced_parent.join("memory").join(original_unit)).unwrap(),
            original_bytes
        );
        assert!(recovery_directories(&parent).is_empty());
        assert!(recovery_directories(&displaced_parent).is_empty());
    }

    #[test]
    fn unchanged_parent_recovery_setup_failure_preserves_its_true_cause() {
        let diagnostic = super::last_known_recovery(
            "cannot sync recovery parent: injected failure".to_string(),
            std::path::Path::new("/captured-parent/.memphant-recovery-test"),
            false,
        );

        assert!(
            diagnostic.contains("cannot sync recovery parent: injected failure"),
            "{diagnostic}"
        );
        assert!(diagnostic.contains("recovery_last_known="), "{diagnostic}");
        assert!(
            !diagnostic.contains("output parent changed"),
            "{diagnostic}"
        );
        assert!(
            !diagnostic.contains("recovery was retained"),
            "{diagnostic}"
        );
    }

    #[test]
    fn empty_unconfirmed_recovery_is_not_described_as_retained_data() {
        let directory = tempfile::tempdir().unwrap();
        let parent = directory.path().join("output-parent");
        let displaced_recovery = directory.path().join("empty-recovery");
        std::fs::create_dir(&parent).unwrap();
        let output = parent.join("memory");
        let initial = rendered_projection(&[(1, "alpha before")]);
        let absent = inspect_output(&output).unwrap();
        replace_projection(&output, &absent, &initial).unwrap();
        let existing = inspect_output(&output).unwrap();
        let original_unit = initial.units.keys().next().unwrap();
        let original_bytes = std::fs::read(output.join(original_unit)).unwrap();
        let replacement = rendered_projection(&[(1, "alpha after")]);
        let mut displaced = false;

        let result = replace_projection_with_hook(&output, &existing, &replacement, |point| {
            if point == "recovery:created" && !displaced {
                std::fs::rename(find_recovery(&parent), &displaced_recovery).unwrap();
                displaced = true;
            }
        });

        let error = result.expect_err("an unconfirmed recovery name was accepted");
        assert!(
            error.contains("durable recovery name could not be confirmed"),
            "{error}"
        );
        assert!(error.contains("recovery_last_known="), "{error}");
        assert!(!error.contains("output parent changed"), "{error}");
        assert!(!error.contains("recovery was retained"), "{error}");
        assert_eq!(
            std::fs::read(output.join(original_unit)).unwrap(),
            original_bytes
        );
        assert_eq!(
            super::directory_names(
                &cap_std::fs::Dir::open_ambient_dir(
                    &displaced_recovery,
                    cap_std::ambient_authority(),
                )
                .unwrap()
            )
            .unwrap(),
            [super::INBOX_DIR, super::UNITS_DIR]
        );
        assert!(
            std::fs::read_dir(displaced_recovery.join(super::UNITS_DIR))
                .unwrap()
                .next()
                .is_none()
        );
    }

    #[test]
    fn replaced_recovery_units_are_rejected_before_the_source_inode_moves() {
        let directory = tempfile::tempdir().unwrap();
        let parent = directory.path().join("output-parent");
        std::fs::create_dir(&parent).unwrap();
        let output = parent.join("memory");
        let initial = rendered_projection(&[(1, "alpha before")]);
        let absent = inspect_output(&output).unwrap();
        replace_projection(&output, &absent, &initial).unwrap();
        let existing = inspect_output(&output).unwrap();
        let replacement = rendered_projection(&[(1, "alpha after")]);
        let unit_path = replacement.units.keys().next().unwrap().clone();
        let original_bytes = std::fs::read(output.join(&unit_path)).unwrap();
        let mut replaced = false;

        let result = replace_projection_with_hook(&output, &existing, &replacement, |point| {
            if point == "recovery:created" && !replaced {
                let recovery = find_recovery(&parent);
                std::fs::rename(
                    recovery.join(super::UNITS_DIR),
                    recovery.join("displaced-units"),
                )
                .unwrap();
                std::fs::create_dir(recovery.join(super::UNITS_DIR)).unwrap();
                replaced = true;
            }
        });

        let error = result.expect_err("a replaced recovery units directory was accepted");
        assert!(
            error.contains("durable recovery units path changed identity"),
            "{error}"
        );
        assert!(error.contains("recovery_last_known="), "{error}");
        assert!(!error.contains("output parent changed"), "{error}");
        assert!(!error.contains("retained managed data"), "{error}");
        assert_eq!(
            std::fs::read(output.join(&unit_path)).unwrap(),
            original_bytes
        );
        let recovery = find_recovery(&parent);
        assert!(
            std::fs::read_dir(recovery.join("displaced-units"))
                .unwrap()
                .next()
                .is_none()
        );
        assert!(
            std::fs::read_dir(recovery.join(super::UNITS_DIR))
                .unwrap()
                .next()
                .is_none()
        );
    }

    #[test]
    fn validation_failure_keeps_recovery_current_and_retained() {
        let directory = tempfile::tempdir().unwrap();
        let parent = directory.path().join("output-parent");
        std::fs::create_dir(&parent).unwrap();
        let output = parent.join("memory");
        let initial = rendered_projection(&[(1, "alpha before")]);
        let absent = inspect_output(&output).unwrap();
        replace_projection(&output, &absent, &initial).unwrap();
        let existing = inspect_output(&output).unwrap();
        let replacement = rendered_projection(&[(1, "alpha after")]);
        let unit_path = replacement.units.keys().next().unwrap().clone();
        let unit_name = std::path::Path::new(&unit_path)
            .file_name()
            .unwrap()
            .to_str()
            .unwrap()
            .to_string();
        let detached = format!("write:{unit_name}:detached");
        let mut corrupted = false;

        let result = replace_projection_with_hook(&output, &existing, &replacement, |point| {
            if point == detached && !corrupted {
                std::fs::write(
                    find_recovery(&parent)
                        .join(super::UNITS_DIR)
                        .join(&unit_name),
                    "recovered validation sentinel",
                )
                .unwrap();
                corrupted = true;
            }
        });

        let error = result.expect_err("a detached-file validation failure was accepted");
        assert!(error.contains("detached file differs"), "{error}");
        assert!(
            error.contains("automatic restoration is disabled"),
            "{error}"
        );
        assert!(error.contains("recovery="), "{error}");
        assert!(!error.contains("recovery_last_known="), "{error}");
        assert!(!error.contains("output parent changed"), "{error}");
        assert!(!output.join(&unit_path).exists());
        assert_eq!(
            std::fs::read_to_string(
                find_recovery(&parent)
                    .join(super::UNITS_DIR)
                    .join(&unit_name)
            )
            .unwrap(),
            "recovered validation sentinel"
        );
    }

    #[test]
    fn replaced_recovery_source_is_never_restored() {
        let directory = tempfile::tempdir().unwrap();
        let parent = directory.path().join("output-parent");
        let displaced_recovery = directory.path().join("recovery-with-original");
        std::fs::create_dir(&parent).unwrap();
        let output = parent.join("memory");
        let initial = rendered_projection(&[(1, "alpha before")]);
        let absent = inspect_output(&output).unwrap();
        replace_projection(&output, &absent, &initial).unwrap();
        let existing = inspect_output(&output).unwrap();
        let replacement = rendered_projection(&[(1, "alpha after")]);
        let unit_path = replacement.units.keys().next().unwrap().clone();
        let unit_name = std::path::Path::new(&unit_path)
            .file_name()
            .unwrap()
            .to_str()
            .unwrap()
            .to_string();
        let displaced_name = format!("displaced-{unit_name}");
        let detached = format!("write:{unit_name}:detached");
        let mut replaced = false;

        let result = replace_projection_with_hook(&output, &existing, &replacement, |point| {
            if point == detached && !replaced {
                let recovery = find_recovery(&parent);
                let units = recovery.join(super::UNITS_DIR);
                std::fs::rename(units.join(&unit_name), units.join(&displaced_name)).unwrap();
                std::fs::write(units.join(&unit_name), "recovery source impostor").unwrap();
                std::fs::rename(recovery, &displaced_recovery).unwrap();
                replaced = true;
            }
        });

        let error = result.expect_err("a replaced recovery source was restored into output");
        assert!(error.contains("detached file differs"), "{error}");
        assert!(
            error.contains("automatic restoration is disabled"),
            "{error}"
        );
        assert!(error.contains("recovery_last_known="), "{error}");
        assert!(
            error.contains("recovery contains retained managed data"),
            "{error}"
        );
        assert!(
            !error
                .split(';')
                .map(str::trim)
                .any(|part| part.starts_with("recovery=")),
            "an unconfirmed recovery pathname was reported as current: {error}"
        );
        assert!(!output.join(&unit_path).exists());
        assert!(
            std::fs::read_to_string(
                displaced_recovery
                    .join(super::UNITS_DIR)
                    .join(&displaced_name)
            )
            .unwrap()
            .contains("alpha before")
        );
        assert_eq!(
            std::fs::read_to_string(displaced_recovery.join(super::UNITS_DIR).join(&unit_name))
                .unwrap(),
            "recovery source impostor"
        );
    }

    #[test]
    fn validation_failure_never_reopens_a_restore_window() {
        let directory = tempfile::tempdir().unwrap();
        let parent = directory.path().join("output-parent");
        std::fs::create_dir(&parent).unwrap();
        let output = parent.join("memory");
        let initial = rendered_projection(&[(1, "alpha before")]);
        let absent = inspect_output(&output).unwrap();
        replace_projection(&output, &absent, &initial).unwrap();
        let existing = inspect_output(&output).unwrap();
        let replacement = rendered_projection(&[(1, "alpha after")]);
        let unit_path = replacement.units.keys().next().unwrap().clone();
        let unit_name = std::path::Path::new(&unit_path)
            .file_name()
            .unwrap()
            .to_str()
            .unwrap()
            .to_string();
        let displaced_name = format!("displaced-{unit_name}");
        let detached = format!("write:{unit_name}:detached");
        let validation_failed = format!("write:{unit_name}:validation_failed");
        let mut corrupted = false;
        let mut planted = false;

        let result = replace_projection_with_hook(&output, &existing, &replacement, |point| {
            if point == detached && !corrupted {
                std::fs::write(
                    find_recovery(&parent)
                        .join(super::UNITS_DIR)
                        .join(&unit_name),
                    "recovered original sentinel",
                )
                .unwrap();
                corrupted = true;
            } else if point == validation_failed && !planted {
                let recovery = find_recovery(&parent);
                let units = recovery.join(super::UNITS_DIR);
                std::fs::rename(units.join(&unit_name), units.join(&displaced_name)).unwrap();
                std::fs::write(units.join(&unit_name), "recovery source impostor").unwrap();
                planted = true;
            }
        });

        let error = result.expect_err("validation failure reopened automatic restoration");
        assert!(planted, "the post-validation seam did not run: {error}");
        assert!(error.contains("detached file differs"), "{error}");
        assert!(
            error.contains("automatic restoration is disabled"),
            "{error}"
        );
        assert!(error.contains("recovery="), "{error}");
        assert!(!error.contains("recovery_last_known="), "{error}");
        assert!(!output.join(&unit_path).exists());
        let recovery = find_recovery(&parent);
        assert_eq!(
            std::fs::read_to_string(recovery.join(super::UNITS_DIR).join(&displaced_name)).unwrap(),
            "recovered original sentinel"
        );
        assert_eq!(
            std::fs::read_to_string(recovery.join(super::UNITS_DIR).join(&unit_name)).unwrap(),
            "recovery source impostor"
        );
    }

    #[test]
    fn validation_failure_never_unlinks_a_replaced_prepared_name() {
        let directory = tempfile::tempdir().unwrap();
        let parent = directory.path().join("output-parent");
        std::fs::create_dir(&parent).unwrap();
        let output = parent.join("memory");
        let initial = rendered_projection(&[(1, "alpha before")]);
        let absent = inspect_output(&output).unwrap();
        replace_projection(&output, &absent, &initial).unwrap();
        let existing = inspect_output(&output).unwrap();
        let replacement = rendered_projection(&[(1, "alpha after")]);
        let unit_path = replacement.units.keys().next().unwrap().clone();
        let unit_name = std::path::Path::new(&unit_path)
            .file_name()
            .unwrap()
            .to_str()
            .unwrap()
            .to_string();
        let detached = format!("write:{unit_name}:detached");
        let validation_failed = format!("write:{unit_name}:validation_failed");
        let displaced_prepared = "displaced-prepared";
        let unrelated_unknown = "unrelated-unknown";
        let mut corrupted = false;
        let mut prepared_name = None;

        let result = replace_projection_with_hook(&output, &existing, &replacement, |point| {
            if point == detached && !corrupted {
                std::fs::write(
                    find_recovery(&parent)
                        .join(super::UNITS_DIR)
                        .join(&unit_name),
                    "recovered original sentinel",
                )
                .unwrap();
                corrupted = true;
            } else if point == validation_failed && prepared_name.is_none() {
                let units = output.join(super::UNITS_DIR);
                let prepared = find_internal(&units, ".memphant-prepared-");
                let name = prepared.file_name().unwrap().to_str().unwrap().to_string();
                std::fs::rename(&prepared, units.join(displaced_prepared)).unwrap();
                std::fs::write(&prepared, "prepared-name sentinel").unwrap();
                std::fs::write(units.join(unrelated_unknown), "unrelated sentinel").unwrap();
                prepared_name = Some(name);
            }
        });

        let error = result.expect_err("validation failure cleaned a prepared pathname");
        let prepared_name = prepared_name.expect("the post-validation seam did not run");
        let units = output.join(super::UNITS_DIR);
        assert_eq!(
            std::fs::read_to_string(units.join(&prepared_name)).unwrap(),
            "prepared-name sentinel"
        );
        assert_eq!(
            std::fs::read(units.join(displaced_prepared)).unwrap(),
            *replacement.units.get(&unit_path).unwrap()
        );
        assert_eq!(
            std::fs::read_to_string(units.join(unrelated_unknown)).unwrap(),
            "unrelated sentinel"
        );
        assert!(!output.join(&unit_path).exists());
        assert_eq!(
            std::fs::read_to_string(
                find_recovery(&parent)
                    .join(super::UNITS_DIR)
                    .join(&unit_name)
            )
            .unwrap(),
            "recovered original sentinel"
        );
        assert!(error.contains("prepared cleanup skipped"), "{error}");
        assert!(
            error.contains(&format!(
                "prepared_name_last_known={}/{}",
                super::UNITS_DIR,
                prepared_name
            )),
            "{error}"
        );
        assert!(error.contains("recovery="), "{error}");
    }

    #[test]
    fn compile_reports_only_last_known_recovery_after_its_parent_moves() {
        let directory = tempfile::tempdir().unwrap();
        let parent = directory.path().join("output-parent");
        let displaced_parent = directory.path().join("displaced-output-parent");
        std::fs::create_dir(&parent).unwrap();
        let canonical_parent = std::fs::canonicalize(&parent).unwrap();
        let output = parent.join("memory");
        let initial = rendered_projection(&[(1, "alpha before")]);
        let absent = inspect_output(&output).unwrap();
        replace_projection(&output, &absent, &initial).unwrap();
        let existing = inspect_output(&output).unwrap();
        let replacement = rendered_projection(&[(1, "alpha after")]);
        let unit_path = replacement.units.keys().next().unwrap().clone();
        let unit_name = std::path::Path::new(&unit_path)
            .file_name()
            .unwrap()
            .to_str()
            .unwrap()
            .to_string();
        let event = format!("write:{unit_name}:recovered");
        let mut swapped = false;

        let result = replace_projection_with_hook(&output, &existing, &replacement, |point| {
            if point == event && !swapped {
                std::fs::rename(&parent, &displaced_parent).unwrap();
                std::fs::create_dir(&parent).unwrap();
                std::fs::write(parent.join("sentinel"), "replacement parent").unwrap();
                swapped = true;
            }
        });

        let error = result.expect_err("compile succeeded after its output parent moved");
        assert!(error.contains("output parent changed"), "{error}");
        assert!(
            error.contains("recovery was retained under that parent"),
            "{error}"
        );
        let last_known = error
            .split(';')
            .map(str::trim)
            .find_map(|part| part.strip_prefix("recovery_last_known="))
            .expect("a moved recovery must be reported only as last-known");
        assert!(
            last_known.starts_with(canonical_parent.to_str().unwrap()),
            "last-known path must use the captured parent: {last_known}"
        );
        assert!(!std::path::Path::new(last_known).exists());
        assert!(
            !error
                .split(';')
                .map(str::trim)
                .any(|part| part.starts_with("recovery=")),
            "a stale recovery pathname was reported as current: {error}"
        );
        assert_eq!(
            std::fs::read_to_string(parent.join("sentinel")).unwrap(),
            "replacement parent"
        );
        let recovery = find_recovery(&displaced_parent);
        assert!(
            std::fs::read_to_string(recovery.join(super::UNITS_DIR).join(unit_name))
                .unwrap()
                .contains("alpha before")
        );
    }

    #[test]
    fn byte_identical_compile_preserves_managed_inodes_without_recovery() {
        let directory = tempfile::tempdir().unwrap();
        let output = directory.path().join("memory");
        let rendered = rendered_projection(&[(1, "alpha")]);
        let absent = inspect_output(&output).unwrap();
        replace_projection(&output, &absent, &rendered).unwrap();
        let existing = inspect_output(&output).unwrap();
        let unit_path = rendered.units.keys().next().unwrap();
        let victim = output.join(unit_path);
        let mut held = std::fs::OpenOptions::new()
            .write(true)
            .open(&victim)
            .unwrap();

        replace_projection(&output, &existing, &rendered).unwrap();
        held.set_len(0).unwrap();
        std::io::Write::write_all(&mut held, b"late no-op edit").unwrap();
        held.sync_all().unwrap();

        assert_eq!(std::fs::read_to_string(&victim).unwrap(), "late no-op edit");
        assert!(recovery_directories(directory.path()).is_empty());
    }

    #[test]
    fn absent_install_reports_noncollision_failures_accurately() {
        let directory = tempfile::tempdir().unwrap();
        let output = directory.path().join("memory");
        let displaced = directory.path().join("displaced-stage");
        let absent = inspect_output(&output).unwrap();
        let rendered = rendered_projection(&[(1, "alpha")]);

        let error = replace_projection_with_hook(&output, &absent, &rendered, |point| {
            if point == "absent:before_install" {
                let staging = find_internal(directory.path(), ".memphant-stage-");
                std::fs::rename(staging, &displaced).unwrap();
            }
        })
        .unwrap_err();

        assert!(error.contains("atomic output install failed"), "{error}");
        assert!(!error.contains("output appeared before install"), "{error}");
        assert!(super::verify_export(&displaced).is_ok());
    }

    #[test]
    fn compile_validates_the_exact_tree_after_manifest_last() {
        let directory = tempfile::tempdir().unwrap();
        let output = directory.path().join("memory");
        let initial = rendered_projection(&[(1, "alpha before")]);
        let absent = inspect_output(&output).unwrap();
        replace_projection(&output, &absent, &initial).unwrap();
        let existing = inspect_output(&output).unwrap();

        let replacement = rendered_projection(&[(1, "alpha after")]);
        let unit = output.join(replacement.units.keys().next().unwrap());
        let result = replace_projection_with_hook(&output, &existing, &replacement, |written| {
            if written == super::MANIFEST_FILE {
                std::fs::write(&unit, "post-manifest sentinel").unwrap();
            }
        });

        let error = result.unwrap_err();
        assert!(
            error.contains("final rendered-tree validation failed"),
            "{error}"
        );
        assert_eq!(
            std::fs::read_to_string(unit).unwrap(),
            "post-manifest sentinel"
        );
    }

    #[test]
    fn compile_requires_a_stable_second_sweep_before_success() {
        let directory = tempfile::tempdir().unwrap();
        let output = directory.path().join("memory");
        let initial = rendered_projection(&[(1, "alpha before")]);
        let absent = inspect_output(&output).unwrap();
        replace_projection(&output, &absent, &initial).unwrap();
        let existing = inspect_output(&output).unwrap();
        let replacement = rendered_projection(&[(1, "alpha after")]);
        let unit = output.join(replacement.units.keys().next().unwrap());

        let result = replace_projection_with_hook(&output, &existing, &replacement, |point| {
            if point == "final:between_sweeps" {
                std::fs::write(&unit, "between-sweeps sentinel").unwrap();
            }
        });

        let error = result.unwrap_err();
        assert!(error.contains("final stability sweep failed"), "{error}");
        let recovery = error
            .split(';')
            .map(str::trim)
            .find_map(|part| part.strip_prefix("recovery="))
            .expect("later validation errors must report durable recovery");
        assert!(std::path::Path::new(recovery).is_dir());
        assert_eq!(
            std::fs::read_to_string(unit).unwrap(),
            "between-sweeps sentinel"
        );
    }

    #[test]
    fn compile_and_verify_accept_optional_procedural_metadata() {
        let directory = tempfile::tempdir().unwrap();
        let output = directory.path().join("memory");
        let body = "Run the recovery procedure.";
        let items = vec![CanonicalProjectionUnit {
            unit_id: UnitId::from_u128(1),
            kind: MemoryKind::Procedural,
            fact_key: Some("procedure:recovery".to_string()),
            predicate: None,
            body: body.to_string(),
            confidence: None,
            valid_from: None,
            valid_to: None,
            state: memphant_types::UnitState::Validated,
            source_span: None,
            body_sha256: sha256(body.as_bytes()),
        }];
        let fingerprint = canonical_projection_fingerprint(&items).unwrap();
        let rendered = render_projection(&CanonicalProjectionResponse {
            tenant_id: TenantId::new(),
            subject_id: SubjectId::new(),
            actor_id: ActorId::new(),
            scope_id: ScopeId::new(),
            agent_node_id: AgentNodeId::new(),
            subject_generation: 0,
            evaluated_at: "2026-07-23T00:00:00Z".to_string(),
            items,
            fingerprint,
        })
        .unwrap();
        let absent = inspect_output(&output).unwrap();

        replace_projection(&output, &absent, &rendered).unwrap();

        assert!(super::verify_export(&output).is_ok());
    }

    /// D2: the four fields the projection has always SENT and the renderer
    /// discarded now reach the unit footer, and the index is a reviewable table
    /// rather than a list of opaque links. Round-trip too: what is written must
    /// parse back and validate against the manifest, or `verify` breaks.
    #[test]
    fn the_unit_footer_and_memory_index_carry_state_interval_and_span() {
        let rendered = rendered_projection(&[(1, "Deploy straight to production.")]);

        let (_, footer) = super::parse_unit(rendered.units.values().next().unwrap()).unwrap();
        assert_eq!(footer.state, memphant_types::UnitState::Active);
        assert_eq!(footer.valid_from.as_deref(), Some("2026-07-01T00:00:00Z"));
        assert_eq!(footer.valid_to, None);
        assert_eq!(footer.source_span.as_deref(), Some("0-30"));

        let memory = String::from_utf8(rendered.memory.clone()).unwrap();
        assert!(
            memory.contains("| Memory | State | Since | Until | Confidence |"),
            "{memory}"
        );
        assert!(
            memory.contains("| [fact:1](units/"),
            "the row still links to the unit file: {memory}"
        );
        assert!(
            memory.contains("| active | 2026-07-01T00:00:00Z | — | 0.90 |"),
            "{memory}"
        );

        // The footer must still validate against the manifest entry it was
        // rendered from — the two carry the same four new fields, so a
        // half-applied change fails here rather than at a user's `verify`.
        let mut findings = Vec::new();
        super::validate_footer(&rendered.manifest.entries[0], 0, &footer, &mut findings);
        assert!(findings.is_empty(), "{findings:?}");
    }

    /// An empty projection renders a header and nothing else — no dangling
    /// table head promising rows that do not exist.
    #[test]
    fn an_empty_projection_renders_no_table_header() {
        let rendered = rendered_projection(&[]);
        let memory = String::from_utf8(rendered.memory).unwrap();
        assert!(!memory.contains("| Memory |"), "{memory}");
        assert!(memory.ends_with("\n\n"), "{memory:?}");
    }

    fn rendered_projection(items: &[(u128, &str)]) -> super::RenderedProjection {
        let items = items
            .iter()
            .map(|(id, body)| CanonicalProjectionUnit {
                unit_id: UnitId::from_u128(*id),
                kind: MemoryKind::Semantic,
                fact_key: Some(format!("fact:{id}")),
                predicate: Some("states".to_string()),
                body: (*body).to_string(),
                confidence: Some(0.9),
                valid_from: Some("2026-07-01T00:00:00Z".to_string()),
                valid_to: None,
                state: memphant_types::UnitState::Active,
                source_span: Some(format!("0-{}", body.len())),
                body_sha256: sha256(body.as_bytes()),
            })
            .collect::<Vec<_>>();
        let fingerprint = canonical_projection_fingerprint(&items).unwrap();
        render_projection(&CanonicalProjectionResponse {
            tenant_id: TenantId::new(),
            subject_id: SubjectId::new(),
            actor_id: ActorId::new(),
            scope_id: ScopeId::new(),
            agent_node_id: AgentNodeId::new(),
            subject_generation: 0,
            evaluated_at: "2026-07-22T00:00:00Z".to_string(),
            items,
            fingerprint,
        })
        .unwrap()
    }

    fn find_internal(directory: &std::path::Path, prefix: &str) -> std::path::PathBuf {
        let matches = std::fs::read_dir(directory)
            .unwrap()
            .map(|entry| entry.unwrap().path())
            .filter(|path| {
                path.file_name()
                    .and_then(|name| name.to_str())
                    .is_some_and(|name| name.starts_with(prefix))
            })
            .collect::<Vec<_>>();
        assert_eq!(matches.len(), 1, "expected one {prefix} artifact");
        matches.into_iter().next().unwrap()
    }

    fn recovery_directories(directory: &std::path::Path) -> Vec<std::path::PathBuf> {
        std::fs::read_dir(directory)
            .unwrap()
            .map(|entry| entry.unwrap().path())
            .filter(|path| {
                path.file_name()
                    .and_then(|name| name.to_str())
                    .is_some_and(|name| name.starts_with(".memphant-recovery-"))
            })
            .collect()
    }

    fn find_recovery(directory: &std::path::Path) -> std::path::PathBuf {
        let recoveries = recovery_directories(directory);
        assert_eq!(recoveries.len(), 1, "expected one durable recovery tree");
        recoveries.into_iter().next().unwrap()
    }
}
