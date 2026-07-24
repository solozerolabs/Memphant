use std::collections::BTreeMap;

use memphant_core::MemoryStore;
use memphant_core::service::{MemoryService, file_sync_plan_sha256};
use memphant_runtime::AnyStore;
use memphant_types::{
    CanonicalProjectionResponse, CanonicalProjectionUnit, EpisodeId, FileSyncOperation,
    FileSyncRequest, FileSyncUnitMetadata, ResolvedMemoryContext, ResourceId, TraceId, UnitId,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use thiserror::Error;
use uuid::Uuid;

pub const ANTHROPIC_MEMORY_TOOL_TYPE: &str = "memory_20250818";
pub const MEMORY_ROOT: &str = "/memories";
pub const MAX_MEMORY_INDEX_BYTES: usize = 25 * 1024;
pub const MAX_MEMORY_INDEX_LINES: usize = 200;
pub const MAX_TOPIC_BYTES: usize = 64 * 1024;
pub const MAX_VIEW_CHARACTERS: usize = 16_000;
pub const MAX_DIRECTORY_BYTES: usize = 64 * 1024;
pub const MAX_DIRECTORY_ENTRIES: usize = 1_000;
pub const MAX_RESOURCE_BYTES: usize = 64 * 1024;
const MEMORY_PATH_FACT_KEY_PREFIX: &str = "memory_path:";
const MAX_MEMORY_PATH_BYTES: usize = 512;

pub fn anthropic_memory_tool() -> serde_json::Value {
    serde_json::json!({"type": ANTHROPIC_MEMORY_TOOL_TYPE, "name": "memory"})
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "command", rename_all = "snake_case", deny_unknown_fields)]
pub enum MemoryCommand {
    View {
        path: String,
        #[serde(default)]
        view_range: Option<[i64; 2]>,
    },
    Create {
        path: String,
        file_text: String,
    },
    StrReplace {
        path: String,
        old_str: String,
        #[serde(default)]
        new_str: Option<String>,
    },
    Insert {
        path: String,
        insert_line: i64,
        insert_text: String,
    },
    Delete {
        path: String,
    },
    Rename {
        old_path: String,
        new_path: String,
    },
}

#[derive(Debug, Clone, PartialEq, Eq, Error)]
#[error("{code}: {message}")]
pub struct MemoryToolError {
    pub code: &'static str,
    pub message: String,
}

impl MemoryToolError {
    fn new(code: &'static str, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }
}

#[derive(Clone)]
pub struct MemoryProjection {
    service: MemoryService<AnyStore>,
    context: ResolvedMemoryContext,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MemoryResourceContent {
    pub uri: String,
    pub mime_type: String,
    pub text: String,
}

impl MemoryProjection {
    pub fn new(service: MemoryService<AnyStore>, context: ResolvedMemoryContext) -> Self {
        Self { service, context }
    }

    pub async fn snapshot(&self) -> Result<CanonicalProjectionResponse, MemoryToolError> {
        let snapshot = self
            .service
            .canonical_projection(&self.context)
            .await
            .map_err(|_| MemoryToolError::new("backend_unavailable", "memory store unavailable"))?;
        let mut paths = snapshot.items.iter().map(topic_path).collect::<Vec<_>>();
        paths.sort();
        let index_path = format!("{MEMORY_ROOT}/MEMORY.md");
        let invalid = paths.iter().any(|path| path == &index_path)
            || paths
                .windows(2)
                .any(|pair| pair[0] == pair[1] || pair[1].starts_with(&format!("{}/", pair[0])));
        if invalid {
            return Err(MemoryToolError::new(
                "invalid_projection",
                "canonical memory contains a reserved or colliding memory path",
            ));
        }
        Ok(snapshot)
    }

    pub async fn handle(&self, command: MemoryCommand) -> Result<String, MemoryToolError> {
        let snapshot = self.snapshot().await?;
        match command {
            MemoryCommand::View { path, view_range } => view(&snapshot, &path, view_range),
            MemoryCommand::Create { path, file_text } => {
                let name = writable_memory_path(&path)?;
                ensure_topic_body(&file_text)?;
                if path_conflicts(&topic_map(&snapshot), &path) {
                    return Err(MemoryToolError::new(
                        "already_exists",
                        format!("Error: File {path} already exists"),
                    ));
                }
                self.apply(
                    &snapshot,
                    vec![FileSyncOperation::Retain {
                        fact_key: name,
                        predicate: "memory_file".to_string(),
                        body: file_text,
                        confidence: 1.0,
                        valid_from: None,
                        valid_to: None,
                    }],
                )
                .await?;
                Ok(format!("File created successfully at: {path}"))
            }
            MemoryCommand::StrReplace {
                path,
                old_str,
                new_str,
            } => {
                let item = topic(&snapshot, &path)?;
                if old_str.is_empty() {
                    return Err(MemoryToolError::new(
                        "invalid_request",
                        "old_str must not be empty",
                    ));
                }
                let matches = item.body.match_indices(&old_str).collect::<Vec<_>>();
                if matches.is_empty() {
                    return Err(MemoryToolError::new(
                        "text_not_found",
                        format!(
                            "No replacement was performed, old_str `{old_str}` did not appear verbatim in {path}."
                        ),
                    ));
                }
                if matches.len() > 1 {
                    let lines = matches
                        .iter()
                        .map(|(offset, _)| {
                            item.body[..*offset].bytes().filter(|b| *b == b'\n').count() + 1
                        })
                        .map(|line| line.to_string())
                        .collect::<Vec<_>>()
                        .join(", ");
                    return Err(MemoryToolError::new(
                        "ambiguous_match",
                        format!(
                            "No replacement was performed. Multiple occurrences of old_str `{old_str}` in lines: {lines}. Please ensure it is unique"
                        ),
                    ));
                }
                let body = item
                    .body
                    .replacen(&old_str, new_str.as_deref().unwrap_or(""), 1);
                ensure_topic_body(&body)?;
                let changed_line = item.body[..matches[0].0]
                    .bytes()
                    .filter(|byte| *byte == b'\n')
                    .count()
                    + 1;
                let response = replacement_response(&body, changed_line);
                self.apply(&snapshot, vec![correct(item, body)]).await?;
                Ok(response)
            }
            MemoryCommand::Insert {
                path,
                insert_line,
                insert_text,
            } => {
                let item = topic(&snapshot, &path)?;
                let body = insert_after_line(&item.body, insert_line, &insert_text)?;
                ensure_topic_body(&body)?;
                self.apply(&snapshot, vec![correct(item, body)]).await?;
                Ok(format!("The file {path} has been edited."))
            }
            MemoryCommand::Delete { path } => {
                if path == MEMORY_ROOT {
                    return Err(MemoryToolError::new(
                        "root_protected",
                        "the memory root cannot be deleted",
                    ));
                }
                validate_path(&path)?;
                let topics = topic_map(&snapshot);
                let operations = if let Some(item) = topics.get(&path) {
                    vec![FileSyncOperation::Forget {
                        base: metadata(item),
                    }]
                } else {
                    let prefix = format!("{path}/");
                    let descendants = topics
                        .iter()
                        .filter(|(candidate, _)| candidate.starts_with(&prefix))
                        .map(|(_, item)| FileSyncOperation::Forget {
                            base: metadata(item),
                        })
                        .collect::<Vec<_>>();
                    if descendants.is_empty() {
                        return Err(MemoryToolError::new(
                            "not_found",
                            format!("Error: The path {path} does not exist"),
                        ));
                    }
                    descendants
                };
                self.apply(&snapshot, operations).await?;
                Ok(format!("Successfully deleted {path}"))
            }
            MemoryCommand::Rename { old_path, new_path } => {
                if old_path == MEMORY_ROOT {
                    return Err(MemoryToolError::new(
                        "root_protected",
                        "the memory root cannot be renamed",
                    ));
                }
                validate_path(&old_path)?;
                writable_memory_path(&new_path)?;
                if new_path.starts_with(&format!("{old_path}/")) {
                    return Err(MemoryToolError::new(
                        "invalid_path",
                        "a memory directory cannot be moved inside itself",
                    ));
                }
                let topics = topic_map(&snapshot);
                if topics.contains_key(&new_path)
                    || topics.keys().any(|candidate| {
                        candidate.starts_with(&format!("{new_path}/"))
                            || new_path.starts_with(&format!("{candidate}/"))
                    })
                {
                    return Err(MemoryToolError::new(
                        "already_exists",
                        format!("Error: The destination {new_path} already exists"),
                    ));
                }
                let sources = if let Some(item) = topics.get(&old_path) {
                    vec![(old_path.clone(), *item)]
                } else {
                    let prefix = format!("{old_path}/");
                    let descendants = topics
                        .iter()
                        .filter(|(candidate, _)| candidate.starts_with(&prefix))
                        .map(|(candidate, item)| (candidate.clone(), *item))
                        .collect::<Vec<_>>();
                    if descendants.is_empty() {
                        return Err(MemoryToolError::new(
                            "not_found",
                            format!("Error: The path {old_path} does not exist"),
                        ));
                    }
                    descendants
                };
                let mut operations = Vec::with_capacity(sources.len() * 2);
                for (source_path, item) in sources {
                    let suffix = source_path.strip_prefix(&old_path).unwrap_or_default();
                    let target_path = format!("{new_path}{suffix}");
                    let name = writable_memory_path(&target_path)?;
                    operations.extend([
                        FileSyncOperation::Forget {
                            base: metadata(item),
                        },
                        FileSyncOperation::Retain {
                            fact_key: name,
                            predicate: "memory_file".to_string(),
                            body: item.body.clone(),
                            confidence: item.confidence.unwrap_or(1.0),
                            valid_from: item.valid_from.clone(),
                            valid_to: item.valid_to.clone(),
                        },
                    ]);
                }
                self.apply(&snapshot, operations).await?;
                Ok(format!("Successfully renamed {old_path} to {new_path}"))
            }
        }
    }

    pub async fn read_resource(&self, uri: &str) -> Result<MemoryResourceContent, MemoryToolError> {
        let (kind, id) = parse_resource_uri(uri)?;
        let (mime_type, text) = match kind {
            "memory" => {
                let snapshot = self.snapshot().await?;
                let item = snapshot
                    .items
                    .iter()
                    .find(|item| item.unit_id.as_uuid() == id)
                    .ok_or_else(|| MemoryToolError::new("not_found", "resource not found"))?;
                ("text/markdown".to_string(), item.body.clone())
            }
            "episode" => {
                let episode = self
                    .service
                    .store()
                    .fetch_episode(&self.context, EpisodeId::from_u128(id.as_u128()))
                    .await
                    .map_err(|_| {
                        MemoryToolError::new("backend_unavailable", "memory store unavailable")
                    })?
                    .ok_or_else(|| MemoryToolError::new("not_found", "resource not found"))?;
                ("text/markdown".to_string(), episode.body)
            }
            "resource" => {
                let resource = self
                    .service
                    .store()
                    .fetch_resource(&self.context, ResourceId::from_u128(id.as_u128()))
                    .await
                    .map_err(|_| {
                        MemoryToolError::new("backend_unavailable", "memory store unavailable")
                    })?
                    .ok_or_else(|| MemoryToolError::new("not_found", "resource not found"))?;
                if !resource.acl.is_empty() {
                    return Err(MemoryToolError::new(
                        "scope_denied",
                        "resource access is not authorized",
                    ));
                }
                if !is_textual_mime(&resource.mime_type) {
                    return Err(MemoryToolError::new(
                        "unsupported_content",
                        "resource is not available as text",
                    ));
                }
                let body = resource.body.ok_or_else(|| {
                    MemoryToolError::new("not_found", "resource body is unavailable")
                })?;
                (resource.mime_type, body)
            }
            "trace" => {
                let trace = self
                    .service
                    .trace(&self.context, TraceId::from_u128(id.as_u128()))
                    .await
                    .map_err(|_| {
                        MemoryToolError::new("backend_unavailable", "memory store unavailable")
                    })?
                    .ok_or_else(|| MemoryToolError::new("not_found", "resource not found"))?;
                let mut body = serde_json::to_string_pretty(&trace).map_err(|_| {
                    MemoryToolError::new("backend_unavailable", "trace encoding failed")
                })?;
                body.push('\n');
                ("application/json".to_string(), body)
            }
            _ => unreachable!("validated resource kind"),
        };
        if text.len() > MAX_RESOURCE_BYTES {
            return Err(MemoryToolError::new(
                "content_too_large",
                "resource exceeds the configured read bound",
            ));
        }
        Ok(MemoryResourceContent {
            uri: uri.to_string(),
            mime_type,
            text,
        })
    }

    async fn apply(
        &self,
        snapshot: &CanonicalProjectionResponse,
        operations: Vec<FileSyncOperation>,
    ) -> Result<(), MemoryToolError> {
        let plan_sha256 = file_sync_plan_sha256(&operations)
            .map_err(|_| MemoryToolError::new("invalid_request", "invalid memory mutation"))?;
        let request = FileSyncRequest {
            subject_id: self.context.data_subject_id,
            scope_id: self.context.scope_id,
            actor_id: self.context.actor_id,
            agent_node_id: self.context.agent_node_id,
            subject_generation: self.context.subject_generation,
            base_fingerprint: snapshot.fingerprint.clone(),
            plan_sha256: plan_sha256.clone(),
            observed_at: snapshot.evaluated_at.clone(),
            operations,
        };
        let context_bytes = serde_json::to_vec(&self.context)
            .map_err(|_| MemoryToolError::new("invalid_request", "invalid memory context"))?;
        let context_sha256 = format!("{:x}", Sha256::digest(context_bytes));
        self.service
            .file_sync(
                &self.context,
                &format!(
                    "memory-tool:{context_sha256}:{}:{plan_sha256}",
                    snapshot.fingerprint
                ),
                request,
            )
            .await
            .map_err(|error| {
                let text = error.to_string();
                if text.contains("conflict") || text.contains("fingerprint") {
                    MemoryToolError::new("sync_conflict", "memory changed; retry the operation")
                } else if text.contains("policy") || text.contains("scope") {
                    MemoryToolError::new("scope_denied", "memory operation is not authorized")
                } else {
                    MemoryToolError::new("mutation_failed", "memory mutation failed")
                }
            })?;
        Ok(())
    }
}

pub fn memory_index(snapshot: &CanonicalProjectionResponse) -> String {
    let topics = topic_map(snapshot);
    let mut output = format!(
        "# Memory\n\nScope: `{}`\nSnapshot: `{}`\n\n",
        snapshot.scope_id.as_uuid(),
        snapshot.fingerprint
    );
    let mut included = 0usize;
    for (path, item) in topics {
        let title = item
            .fact_key
            .as_deref()
            .filter(|value| !value.starts_with(MEMORY_PATH_FACT_KEY_PREFIX))
            .unwrap_or(path.rsplit('/').next().unwrap_or(&path));
        let line = format!(
            "- [{}]({})\n",
            escape_link_text(title),
            path.trim_start_matches("/memories/")
        );
        if output.lines().count() + 1 > MAX_MEMORY_INDEX_LINES
            || output.len() + line.len() > MAX_MEMORY_INDEX_BYTES
        {
            break;
        }
        output.push_str(&line);
        included += 1;
    }
    let omitted = snapshot.items.len().saturating_sub(included);
    if omitted > 0 {
        let notice = format!("\n{omitted} additional topic files are available on demand.\n");
        if output.len() + notice.len() <= MAX_MEMORY_INDEX_BYTES
            && output.lines().count() + 2 <= MAX_MEMORY_INDEX_LINES
        {
            output.push_str(&notice);
        }
    }
    output
}

pub fn topic_path(item: &CanonicalProjectionUnit) -> String {
    if item.predicate.as_deref() == Some("memory_file")
        && let Some(relative) = item
            .fact_key
            .as_deref()
            .and_then(|value| value.strip_prefix(MEMORY_PATH_FACT_KEY_PREFIX))
            .filter(|value| valid_relative_memory_path(value))
    {
        return format!("{MEMORY_ROOT}/{relative}");
    }
    let stem = item
        .fact_key
        .as_deref()
        .filter(|value| *value != "MEMORY" && valid_topic_stem(value))
        .map(str::to_owned)
        .unwrap_or_else(|| item.unit_id.as_uuid().to_string());
    format!("{MEMORY_ROOT}/{stem}.md")
}

fn topic_map(snapshot: &CanonicalProjectionResponse) -> BTreeMap<String, &CanonicalProjectionUnit> {
    snapshot
        .items
        .iter()
        .map(|item| (topic_path(item), item))
        .collect()
}

fn path_conflicts(topics: &BTreeMap<String, &CanonicalProjectionUnit>, path: &str) -> bool {
    topics.keys().any(|candidate| {
        candidate == path
            || candidate.starts_with(&format!("{path}/"))
            || path.starts_with(&format!("{candidate}/"))
    })
}

fn topic<'a>(
    snapshot: &'a CanonicalProjectionResponse,
    path: &str,
) -> Result<&'a CanonicalProjectionUnit, MemoryToolError> {
    validate_path(path)?;
    topic_map(snapshot).get(path).copied().ok_or_else(|| {
        MemoryToolError::new(
            "not_found",
            format!("Error: The path {path} does not exist"),
        )
    })
}

fn view(
    snapshot: &CanonicalProjectionResponse,
    path: &str,
    view_range: Option<[i64; 2]>,
) -> Result<String, MemoryToolError> {
    validate_path(path)?;
    let topics = topic_map(snapshot);
    let is_directory = path == MEMORY_ROOT
        || topics
            .keys()
            .any(|candidate| candidate.starts_with(&format!("{path}/")));
    if is_directory {
        if view_range.is_some() {
            return Err(MemoryToolError::new(
                "invalid_request",
                "view_range applies only to files",
            ));
        }
        let mut output = format!(
            "Here're the files and directories up to 2 levels deep in {path}, excluding hidden items and node_modules:"
        );
        push_directory_line(&mut output, &format_size(0, path));
        if path == MEMORY_ROOT {
            push_directory_line(
                &mut output,
                &format_size(
                    memory_index(snapshot).len(),
                    &format!("{MEMORY_ROOT}/MEMORY.md"),
                ),
            );
        }
        let prefix = format!("{path}/");
        let mut entries = BTreeMap::<String, usize>::new();
        for (topic_path, item) in topics {
            let Some(relative) = topic_path.strip_prefix(&prefix) else {
                continue;
            };
            let parts = relative.split('/').collect::<Vec<_>>();
            if parts.len() == 1 {
                entries.insert(topic_path, item.body.len());
            } else {
                entries.insert(format!("{path}/{}/", parts[0]), 0);
                if parts.len() == 2 {
                    entries.insert(topic_path, item.body.len());
                } else {
                    entries.insert(format!("{path}/{}/{}/", parts[0], parts[1]), 0);
                }
            }
        }
        for (index, (entry_path, bytes)) in entries.into_iter().enumerate() {
            if index >= MAX_DIRECTORY_ENTRIES {
                push_directory_line(
                    &mut output,
                    "additional topic files omitted; use MEMORY.md or MCP pagination",
                );
                break;
            }
            let line = format_size(bytes, &entry_path);
            if !push_directory_line(&mut output, &line) {
                push_directory_line(
                    &mut output,
                    "additional topic files omitted; use MEMORY.md or MCP pagination",
                );
                break;
            }
        }
        return Ok(output);
    }

    let body = if path == format!("{MEMORY_ROOT}/MEMORY.md") {
        memory_index(snapshot)
    } else {
        let item = topic(snapshot, path)?;
        ensure_topic_body(&item.body)?;
        item.body.clone()
    };
    numbered_view(path, &body, view_range)
}

fn numbered_view(
    path: &str,
    body: &str,
    view_range: Option<[i64; 2]>,
) -> Result<String, MemoryToolError> {
    let default_body;
    let body = if view_range.is_none() && body.chars().count() > MAX_VIEW_CHARACTERS {
        default_body = body.chars().take(MAX_VIEW_CHARACTERS).collect::<String>();
        default_body.as_str()
    } else {
        body
    };
    let lines = body.split('\n').collect::<Vec<_>>();
    if lines.len() > 999_999 {
        return Err(MemoryToolError::new(
            "too_many_lines",
            format!("File {path} exceeds maximum line limit of 999,999 lines."),
        ));
    }
    let (start, end) = match view_range {
        None => (1usize, lines.len()),
        Some([start, end]) if start >= 1 && (end == -1 || end >= start) => {
            let end = if end == -1 { lines.len() } else { end as usize };
            (start as usize, end.min(lines.len()))
        }
        _ => {
            return Err(MemoryToolError::new(
                "invalid_range",
                "view_range must be [start_line, end_line] with 1-based lines and -1 allowed only as the end",
            ));
        }
    };
    if start > lines.len().max(1) {
        return Err(MemoryToolError::new(
            "invalid_range",
            format!("view_range starts after the end of {path}"),
        ));
    }
    let mut output = format!("Here's the content of {path} with line numbers:\n");
    for (index, line) in lines
        .iter()
        .enumerate()
        .skip(start.saturating_sub(1))
        .take(end.saturating_sub(start).saturating_add(1))
    {
        output.push_str(&format!("{:>6}\t{}\n", index + 1, line));
    }
    Ok(output)
}

fn push_directory_line(output: &mut String, line: &str) -> bool {
    let required = usize::from(!output.is_empty()) + line.len();
    if output.len() + required > MAX_DIRECTORY_BYTES {
        return false;
    }
    if !output.is_empty() {
        output.push('\n');
    }
    output.push_str(line);
    true
}

fn validate_path(path: &str) -> Result<(), MemoryToolError> {
    if path.contains('\0')
        || path.contains('\\')
        || path.contains('%')
        || path.contains('?')
        || path.contains('#')
        || path.split('/').any(|part| part == "." || part == "..")
        || !(path == MEMORY_ROOT || path.starts_with(&format!("{MEMORY_ROOT}/")))
    {
        return Err(MemoryToolError::new(
            "invalid_path",
            format!("path must remain within {MEMORY_ROOT}"),
        ));
    }
    Ok(())
}

fn writable_memory_path(path: &str) -> Result<String, MemoryToolError> {
    validate_path(path)?;
    let prefix = format!("{MEMORY_ROOT}/");
    let Some(filename) = path.strip_prefix(&prefix) else {
        return Err(MemoryToolError::new(
            "read_only_path",
            "memory writes must target a file below /memories",
        ));
    };
    if filename == "MEMORY.md" {
        return Err(MemoryToolError::new(
            "read_only_path",
            "/memories/MEMORY.md is a generated read-only index",
        ));
    }
    if !valid_relative_memory_path(filename) {
        return Err(MemoryToolError::new(
            "invalid_path",
            "memory paths must use bounded visible ASCII file components",
        ));
    }
    Ok(format!("{MEMORY_PATH_FACT_KEY_PREFIX}{filename}"))
}

fn valid_relative_memory_path(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= MAX_MEMORY_PATH_BYTES
        && value.split('/').all(|component| {
            !component.is_empty()
                && component.len() <= 128
                && !component.starts_with('.')
                && component != "node_modules"
                && component
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-'))
        })
}

fn valid_topic_stem(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && !value.starts_with('.')
        && value != "."
        && value != ".."
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-'))
}

fn ensure_topic_body(body: &str) -> Result<(), MemoryToolError> {
    if body.is_empty() {
        return Err(MemoryToolError::new(
            "empty_content",
            "memory topic content must not be empty; delete the topic instead",
        ));
    }
    if body.len() > MAX_TOPIC_BYTES {
        Err(MemoryToolError::new(
            "content_too_large",
            format!(
                "memory topic is {} bytes and exceeds the {} byte limit",
                body.len(),
                MAX_TOPIC_BYTES
            ),
        ))
    } else {
        Ok(())
    }
}

fn is_textual_mime(mime_type: &str) -> bool {
    let essence = mime_type
        .split(';')
        .next()
        .unwrap_or_default()
        .trim()
        .to_ascii_lowercase();
    essence.starts_with("text/")
        || matches!(
            essence.as_str(),
            "application/json" | "application/xml" | "application/yaml" | "application/x-yaml"
        )
        || essence.ends_with("+json")
        || essence.ends_with("+xml")
}

fn metadata(item: &CanonicalProjectionUnit) -> FileSyncUnitMetadata {
    FileSyncUnitMetadata {
        unit_id: item.unit_id,
        kind: item.kind,
        fact_key: item.fact_key.clone(),
        predicate: item.predicate.clone(),
        confidence: item.confidence,
        valid_from: item.valid_from.clone(),
        valid_to: item.valid_to.clone(),
        body_sha256: item.body_sha256.clone(),
    }
}

fn correct(item: &CanonicalProjectionUnit, body: String) -> FileSyncOperation {
    FileSyncOperation::Correct {
        base: metadata(item),
        body,
    }
}

fn insert_after_line(
    body: &str,
    insert_line: i64,
    insert_text: &str,
) -> Result<String, MemoryToolError> {
    let mut lines = body.split('\n').map(str::to_string).collect::<Vec<_>>();
    if insert_line < 0 || insert_line as usize > lines.len() {
        return Err(MemoryToolError::new(
            "invalid_line",
            format!(
                "Error: Invalid `insert_line` parameter: {insert_line}. It should be within the range of lines of the file: [0, {}]",
                lines.len()
            ),
        ));
    }
    lines.insert(
        insert_line as usize,
        insert_text
            .strip_suffix('\n')
            .unwrap_or(insert_text)
            .to_string(),
    );
    Ok(lines.join("\n"))
}

fn replacement_response(body: &str, changed_line: usize) -> String {
    let lines = body.split('\n').collect::<Vec<_>>();
    let start = changed_line.saturating_sub(3);
    let end = (changed_line + 2).min(lines.len());
    let mut output =
        "The memory file has been edited. Here is the snippet showing the change (with line numbers):\n"
            .to_string();
    for (index, line) in lines.iter().enumerate().take(end).skip(start) {
        let rendered = format!("{:>6}\t{}\n", index + 1, line);
        if output.len() + rendered.len() > MAX_TOPIC_BYTES {
            break;
        }
        output.push_str(&rendered);
    }
    output
}

fn format_size(bytes: usize, path: &str) -> String {
    if bytes < 1024 {
        format!("{bytes}B\t{path}")
    } else {
        format!("{:.1}K\t{path}", bytes as f64 / 1024.0)
    }
}

fn escape_link_text(value: &str) -> String {
    value
        .replace('\\', "\\\\")
        .replace('[', "\\[")
        .replace(']', "\\]")
}

pub fn memory_resource_uri(unit_id: UnitId) -> String {
    format!("memphant://memory/{}", unit_id.as_uuid())
}

pub fn parse_memory_resource_uri(uri: &str) -> Result<Uuid, MemoryToolError> {
    let (kind, id) = parse_resource_uri(uri)?;
    if kind != "memory" {
        return Err(MemoryToolError::new(
            "invalid_uri",
            "invalid memory resource URI",
        ));
    }
    Ok(id)
}

fn parse_resource_uri(uri: &str) -> Result<(&str, Uuid), MemoryToolError> {
    if uri.contains('?') || uri.contains('#') || uri.contains('%') || uri.contains('\\') {
        return Err(MemoryToolError::new(
            "invalid_uri",
            "invalid MemPhant resource URI",
        ));
    }
    let path = uri
        .strip_prefix("memphant://")
        .ok_or_else(|| MemoryToolError::new("invalid_uri", "invalid MemPhant resource URI"))?;
    let mut parts = path.split('/');
    let (Some(kind), Some(raw), None) = (parts.next(), parts.next(), parts.next()) else {
        return Err(MemoryToolError::new(
            "invalid_uri",
            "invalid MemPhant resource URI",
        ));
    };
    if !matches!(kind, "memory" | "episode" | "resource" | "trace") {
        return Err(MemoryToolError::new(
            "invalid_uri",
            "invalid MemPhant resource URI",
        ));
    }
    let id = Uuid::parse_str(raw)
        .map_err(|_| MemoryToolError::new("invalid_uri", "invalid MemPhant resource URI"))?;
    Ok((kind, id))
}

pub fn resource_page<'a>(
    snapshot: &'a CanonicalProjectionResponse,
    cursor: Option<&str>,
    limit: usize,
) -> Result<(Vec<&'a CanonicalProjectionUnit>, Option<String>), MemoryToolError> {
    let limit = limit.clamp(1, 100);
    let start = match cursor {
        None => 0,
        Some(cursor) => {
            let mut parts = cursor.split(':');
            let (Some(version), Some(fingerprint), Some(offset), None) =
                (parts.next(), parts.next(), parts.next(), parts.next())
            else {
                return Err(MemoryToolError::new(
                    "invalid_cursor",
                    "invalid resource cursor",
                ));
            };
            if version != "v1" || fingerprint != snapshot.fingerprint {
                return Err(MemoryToolError::new(
                    "stale_cursor",
                    "resource cursor is stale",
                ));
            }
            offset
                .parse::<usize>()
                .ok()
                .filter(|offset| *offset <= snapshot.items.len())
                .ok_or_else(|| MemoryToolError::new("invalid_cursor", "invalid resource cursor"))?
        }
    };
    let end = (start + limit).min(snapshot.items.len());
    let next = (end < snapshot.items.len()).then(|| format!("v1:{}:{end}", snapshot.fingerprint));
    Ok((snapshot.items[start..end].iter().collect(), next))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn path_and_uri_validation_reject_traversal_and_encoding() {
        for path in [
            "/memories/../secret",
            "/memories/%2e%2e/secret",
            "/memories\\..\\secret",
            "/other/file",
        ] {
            assert_eq!(validate_path(path).unwrap_err().code, "invalid_path");
        }
        for uri in [
            "memphant://memory/../secret",
            "memphant://memory/%2e%2e",
            "memphant://memory/not-a-uuid",
            "https://memory/00000000-0000-0000-0000-000000000000",
        ] {
            assert_eq!(
                parse_memory_resource_uri(uri).unwrap_err().code,
                "invalid_uri"
            );
        }
    }

    #[test]
    fn directory_line_builder_counts_separators_and_never_exceeds_the_bound() {
        let mut output = "x".repeat(MAX_DIRECTORY_BYTES - 2);
        assert!(push_directory_line(&mut output, "y"));
        assert_eq!(output.len(), MAX_DIRECTORY_BYTES);
        assert!(!push_directory_line(&mut output, "z"));
        assert_eq!(output.len(), MAX_DIRECTORY_BYTES);
    }

    #[test]
    fn insertion_matches_the_reference_line_array_semantics() {
        assert_eq!(
            insert_after_line("a\nb\n", 0, "start\n").unwrap(),
            "start\na\nb\n"
        );
        assert_eq!(
            insert_after_line("a\nb\n", 1, "middle").unwrap(),
            "a\nmiddle\nb\n"
        );
        assert_eq!(
            insert_after_line("a\nb\n", 3, "end").unwrap(),
            "a\nb\n\nend"
        );
        assert_eq!(insert_after_line("a", 1, "b").unwrap(), "a\nb");
    }
}
