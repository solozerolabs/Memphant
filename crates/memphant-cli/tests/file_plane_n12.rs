use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

use memphant_core::MemoryStore;
use memphant_core::service::file_sync_plan_sha256;
use memphant_server::AppState;
use memphant_types::{
    ContextBindingAgentRef, ContextBindingEntityRef, ContextBindingRequest, ContextBindingResponse,
    ContextBindingScopeRef, FileSyncOperation, MemoryEdgeKind, MemoryKind, NewMemoryUnit, TenantId,
    TrustLevel, UnitId, UnitState,
};
use serde::Deserialize;
use sha2::{Digest, Sha256};

const TENANT: &str = "00000000-0000-0000-0000-00000000b205";
const FIXTURE: &str = include_str!("../../../tests/fixtures/file-plane-n12.json");

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct GateFixture {
    schema_version: u32,
    cases: Vec<GateCase>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct GateCase {
    id: String,
    scenario_theme: ScenarioTheme,
    edit_class: EditClass,
    seed_kind: SeedKind,
    seed_fact_key: String,
    seed_body: String,
    edited_fact_key: Option<String>,
    edited_body: Option<String>,
    inbox_name: Option<String>,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd)]
#[serde(rename_all = "snake_case")]
enum ScenarioTheme {
    ArchitectureDecision,
    CompactionRehydration,
    CrossAgentTransfer,
    TaskPlusSemanticComposite,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd)]
#[serde(rename_all = "snake_case")]
enum EditClass {
    Mutation,
    NewFact,
    Deletion,
    Contradiction,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "snake_case")]
enum SeedKind {
    Semantic,
    Procedural,
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn b2_file_plane_round_trips_balanced_n12_gate() {
    let fixture: GateFixture = serde_json::from_str(FIXTURE).expect("valid n=12 fixture");
    assert_eq!(fixture.schema_version, 1);
    assert_eq!(fixture.cases.len(), 12);
    assert_fixture_contract(&fixture);

    let tenant = tenant();
    let state = AppState::new_in_memory().with_dev_tenant(tenant);
    let mut bindings = Vec::with_capacity(fixture.cases.len());
    for case in &fixture.cases {
        bindings.push(bind_case(&state, case).await);
    }
    let url = serve(memphant_server::app(state.clone())).await;

    let mut passed = 0usize;
    let mut theme_counts = BTreeMap::new();
    let mut edit_counts = BTreeMap::new();
    let mut runtime_scopes = BTreeSet::new();
    let mut runtime_units = BTreeSet::new();

    for (case, binding) in fixture.cases.iter().zip(&bindings) {
        assert!(runtime_scopes.insert(binding.scope_id.as_uuid().to_string()));
        *theme_counts.entry(case.scenario_theme).or_insert(0usize) += 1;
        *edit_counts.entry(case.edit_class).or_insert(0usize) += 1;

        let seed_id = seed_unit(&state, binding, case).await;
        assert!(runtime_units.insert(seed_id.as_uuid().to_string()));
        let parent = tempfile::tempdir().unwrap();
        let out = parent.path().join("memory");

        assert_success(case, "initial compile", &compile(&url, binding, &out));
        assert_success(case, "initial verify", &verify(&out));
        apply_edit(case, seed_id, &out);

        let first = sync(&url, binding, &out, false);
        assert_success(case, "first dry-run", &first);
        let second = sync(&url, binding, &out, false);
        assert_success(case, "second dry-run", &second);
        assert_eq!(first.stdout, second.stdout, "{}: stable dry-run", case.id);
        let plan: serde_json::Value = serde_json::from_slice(&first.stdout).unwrap();
        let operations: Vec<FileSyncOperation> =
            serde_json::from_value(plan["operations"].clone()).unwrap();
        assert_eq!(operations.len(), 1, "{}: one operation", case.id);
        assert_eq!(
            plan["operations"][0]["op"],
            expected_op(case.edit_class),
            "{}: native operation",
            case.id
        );
        assert_eq!(
            plan["plan_sha256"],
            file_sync_plan_sha256(&operations).unwrap(),
            "{}: exact plan digest",
            case.id
        );

        assert_success(case, "apply", &sync(&url, binding, &out, true));
        assert_store_effect(&state, binding, case, seed_id).await;
        assert!(fs::read_dir(out.join("inbox")).unwrap().next().is_none());

        let empty = sync(&url, binding, &out, false);
        assert_success(case, "post-apply dry-run", &empty);
        let empty: serde_json::Value = serde_json::from_slice(&empty.stdout).unwrap();
        assert_eq!(empty["operations"], serde_json::json!([]), "{}", case.id);
        assert_success(case, "post-apply verify", &verify(&out));

        let first_tree = tree_bytes(&out);
        let first_hash = tree_sha256(&first_tree);
        assert_success(case, "second compile", &compile(&url, binding, &out));
        let second_tree = tree_bytes(&out);
        assert_eq!(second_tree, first_tree, "{}: compile fixed point", case.id);
        assert_eq!(tree_sha256(&second_tree), first_hash, "{}", case.id);
        assert_success(case, "fixed-point verify", &verify(&out));
        passed += 1;
    }

    assert_eq!(passed, 12);
    for theme in [
        ScenarioTheme::ArchitectureDecision,
        ScenarioTheme::CompactionRehydration,
        ScenarioTheme::CrossAgentTransfer,
        ScenarioTheme::TaskPlusSemanticComposite,
    ] {
        assert_eq!(theme_counts.get(&theme), Some(&3));
    }
    for edit in [
        EditClass::Mutation,
        EditClass::NewFact,
        EditClass::Deletion,
        EditClass::Contradiction,
    ] {
        assert_eq!(edit_counts.get(&edit), Some(&3));
    }
}

fn assert_fixture_contract(fixture: &GateFixture) {
    let mut ids = BTreeSet::new();
    for case in &fixture.cases {
        assert!(ids.insert(&case.id), "duplicate case id {}", case.id);
        assert!(!case.id.is_empty());
        match case.edit_class {
            EditClass::Mutation => {
                assert!(case.edited_body.is_some());
                assert!(case.edited_fact_key.is_none());
                assert!(case.inbox_name.is_none());
                assert_ne!(case.seed_kind, SeedKind::Procedural);
            }
            EditClass::Deletion => {
                assert!(case.edited_body.is_none());
                assert!(case.edited_fact_key.is_none());
                assert!(case.inbox_name.is_none());
            }
            EditClass::NewFact | EditClass::Contradiction => {
                assert!(case.edited_body.is_some());
                assert!(case.edited_fact_key.is_some());
                let inbox = case.inbox_name.as_deref().unwrap();
                assert!(inbox.ends_with(".md"));
                assert!(!inbox.contains(['/', '\\']));
                assert!(!matches!(inbox, "." | ".."));
            }
        }
        if case.edit_class == EditClass::Contradiction {
            assert_eq!(
                case.edited_fact_key.as_deref(),
                Some(case.seed_fact_key.as_str())
            );
        }
    }
    assert!(fixture.cases.iter().any(|case| {
        case.scenario_theme == ScenarioTheme::CrossAgentTransfer
            && case.edit_class == EditClass::Deletion
            && case.seed_kind == SeedKind::Procedural
    }));
}

async fn assert_store_effect(
    state: &AppState<memphant_core::InMemoryStore>,
    binding: &ContextBindingResponse,
    case: &GateCase,
    seed_id: UnitId,
) {
    let context = state
        .store()
        .resolve_memory_context(
            tenant(),
            binding.subject_id,
            binding.actor_id,
            binding.scope_id,
            binding.agent_node_id,
        )
        .await
        .unwrap();
    let projection = state
        .service()
        .canonical_projection(&context)
        .await
        .unwrap();
    let units = state.store().scope_memory(tenant(), binding.scope_id);
    let seed = units.iter().find(|unit| unit.id == seed_id).unwrap();
    let edges = state
        .store()
        .memory_edges(tenant())
        .into_iter()
        .filter(|edge| edge.scope_id == binding.scope_id)
        .collect::<Vec<_>>();

    match case.edit_class {
        EditClass::Mutation => {
            let expected = case.edited_body.as_deref().unwrap();
            assert_eq!(seed.state, UnitState::Superseded, "{}", case.id);
            assert!(projection.items.iter().any(|unit| {
                unit.fact_key.as_deref() == Some(&case.seed_fact_key) && unit.body == expected
            }));
            assert!(edges.iter().any(|edge| {
                edge.kind == MemoryEdgeKind::Supersedes
                    && (edge.src_id == seed_id || edge.dst_id == seed_id)
            }));
        }
        EditClass::NewFact => {
            let expected_key = case.edited_fact_key.as_deref().unwrap();
            let expected_body = case.edited_body.as_deref().unwrap();
            assert!(matches!(
                seed.state,
                UnitState::Active | UnitState::Validated
            ));
            assert!(projection.items.iter().any(|unit| {
                unit.fact_key.as_deref() == Some(expected_key) && unit.body == expected_body
            }));
            assert!(
                edges
                    .iter()
                    .all(|edge| edge.kind != MemoryEdgeKind::Contradicts)
            );
        }
        EditClass::Deletion => {
            assert_eq!(seed.state, UnitState::Deleted, "{}", case.id);
            assert!(projection.items.is_empty(), "{}", case.id);
            assert!(edges.is_empty(), "{}", case.id);
        }
        EditClass::Contradiction => {
            let expected = case.edited_body.as_deref().unwrap();
            let replacement = projection
                .items
                .iter()
                .find(|unit| unit.body == expected)
                .unwrap_or_else(|| panic!("{}: current contradiction body", case.id));
            assert!(!projection.items.iter().any(|unit| unit.unit_id == seed_id));
            assert!(edges.iter().any(|edge| {
                edge.kind == MemoryEdgeKind::Contradicts
                    && edge.src_id == seed_id
                    && edge.dst_id == replacement.unit_id
            }));
        }
    }
}

fn apply_edit(case: &GateCase, seed_id: UnitId, out: &Path) {
    match case.edit_class {
        EditClass::Mutation => replace_body(
            &out.join("units").join(format!("{}.md", seed_id.as_uuid())),
            case.edited_body.as_deref().unwrap(),
        ),
        EditClass::Deletion => {
            fs::remove_file(out.join("units").join(format!("{}.md", seed_id.as_uuid()))).unwrap();
        }
        EditClass::NewFact | EditClass::Contradiction => {
            fs::write(
                out.join("inbox").join(case.inbox_name.as_deref().unwrap()),
                format!(
                    "# {}\n\n{}\n",
                    case.edited_fact_key.as_deref().unwrap(),
                    case.edited_body.as_deref().unwrap()
                ),
            )
            .unwrap();
        }
    }
}

fn expected_op(edit: EditClass) -> &'static str {
    match edit {
        EditClass::Mutation => "correct",
        EditClass::NewFact | EditClass::Contradiction => "retain",
        EditClass::Deletion => "forget",
    }
}

async fn bind_case(
    state: &AppState<memphant_core::InMemoryStore>,
    case: &GateCase,
) -> ContextBindingResponse {
    state
        .store()
        .resolve_context_binding(
            tenant(),
            format!("b2-n12-bind-{}", case.id),
            ContextBindingRequest {
                subject: ContextBindingEntityRef {
                    external_ref: "b2-n12-user".into(),
                    kind: "user".into(),
                },
                actor: ContextBindingEntityRef {
                    external_ref: "b2-n12-user".into(),
                    kind: "user".into(),
                },
                scope: ContextBindingScopeRef {
                    external_ref: format!("b2-n12-scope-{}", case.id),
                    kind: "project".into(),
                    parent_external_ref: None,
                },
                agent_node: ContextBindingAgentRef {
                    external_ref: format!("b2-n12-agent-{}", case.id),
                    parent_external_ref: None,
                },
                access_policies: Vec::new(),
            },
        )
        .await
        .unwrap()
}

async fn serve(app: axum::Router) -> String {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    format!("http://{address}")
}

async fn seed_unit(
    state: &AppState<memphant_core::InMemoryStore>,
    binding: &ContextBindingResponse,
    case: &GateCase,
) -> UnitId {
    let context = state
        .store()
        .resolve_memory_context(
            tenant(),
            binding.subject_id,
            binding.actor_id,
            binding.scope_id,
            binding.agent_node_id,
        )
        .await
        .unwrap();
    let kind = match case.seed_kind {
        SeedKind::Semantic => MemoryKind::Semantic,
        SeedKind::Procedural => MemoryKind::Procedural,
    };
    let mut transaction = state.store().begin(&context).await.unwrap();
    let id = state
        .store()
        .stage_memory_unit(
            &mut transaction,
            NewMemoryUnit {
                capture: None,
                tenant_id: tenant(),
                data_subject_id: binding.subject_id,
                scope_id: binding.scope_id,
                agent_node_id: binding.agent_node_id,
                subject_generation: binding.subject_generation,
                kind,
                state: if kind == MemoryKind::Procedural {
                    UnitState::Validated
                } else {
                    UnitState::Active
                },
                fact_key: Some(case.seed_fact_key.clone()),
                predicate: Some("states".into()),
                body: case.seed_body.clone(),
                confidence: Some(1.0),
                trust_level: TrustLevel::TrustedSystem,
                churn_class: None,
                freshness_due_at: None,
                actor_id: Some(binding.actor_id),
                source_kind: Some("test".into()),
                source_ref: format!("test:{}", case.id),
                observed_at: "2026-07-22T00:00:00Z".into(),
                source_episode_id: None,
                source_resource_id: None,
                deletion_generation: None,
                contextual_chunks: Vec::new(),
                valid_from: None,
                valid_to: None,
                transaction_from: None,
                transaction_to: None,
            },
        )
        .await
        .unwrap();
    state.store().commit(transaction).await.unwrap();
    id
}

fn tenant() -> TenantId {
    TenantId::from_u128(uuid::Uuid::parse_str(TENANT).unwrap().as_u128())
}

fn replace_body(path: &Path, body: &str) {
    let text = fs::read_to_string(path).unwrap();
    let footer = text.rfind("\n\n<!-- memphant ").unwrap();
    let title = text.find("\n\n").unwrap();
    fs::write(
        path,
        format!("{}\n\n{}{}", &text[..title], body, &text[footer..]),
    )
    .unwrap();
}

fn compile(url: &str, binding: &ContextBindingResponse, out: &Path) -> Output {
    cli(url, binding, out, "compile", &[])
}

fn sync(url: &str, binding: &ContextBindingResponse, out: &Path, apply: bool) -> Output {
    cli(
        url,
        binding,
        out,
        "sync",
        if apply { &["--apply"] } else { &[] },
    )
}

fn cli(
    url: &str,
    binding: &ContextBindingResponse,
    out: &Path,
    verb: &str,
    tail: &[&str],
) -> Output {
    Command::new(env!("CARGO_BIN_EXE_memphant-cli"))
        .env("MEMPHANT_URL", url)
        .env_remove("MEMPHANT_API_KEY")
        .arg(verb)
        .args(["--agent-node"])
        .arg(binding.agent_node_id.as_uuid().to_string())
        .args(["--out"])
        .arg(out)
        .args(["--scope"])
        .arg(binding.scope_id.as_uuid().to_string())
        .args([
            "--subject-generation",
            &binding.subject_generation.to_string(),
        ])
        .args(["--actor"])
        .arg(binding.actor_id.as_uuid().to_string())
        .args(["--subject-id"])
        .arg(binding.subject_id.as_uuid().to_string())
        .args(tail)
        .output()
        .unwrap()
}

fn verify(export: &Path) -> Output {
    Command::new(env!("CARGO_BIN_EXE_memphant-cli"))
        .current_dir(repo_root())
        .args(["verify", "--lock", "memphant.lock", "--export"])
        .arg(export)
        .output()
        .unwrap()
}

fn tree_bytes(root: &Path) -> BTreeMap<String, Vec<u8>> {
    fn visit(root: &Path, current: &Path, files: &mut BTreeMap<String, Vec<u8>>) {
        for entry in fs::read_dir(current).unwrap() {
            let path = entry.unwrap().path();
            if path.is_dir() {
                visit(root, &path, files);
            } else {
                files.insert(
                    path.strip_prefix(root)
                        .unwrap()
                        .to_string_lossy()
                        .into_owned(),
                    fs::read(path).unwrap(),
                );
            }
        }
    }
    let mut files = BTreeMap::new();
    visit(root, root, &mut files);
    files
}

fn tree_sha256(files: &BTreeMap<String, Vec<u8>>) -> String {
    let mut digest = Sha256::new();
    for (path, bytes) in files {
        digest.update(path.len().to_be_bytes());
        digest.update(path.as_bytes());
        digest.update(bytes.len().to_be_bytes());
        digest.update(bytes);
    }
    format!("{:x}", digest.finalize())
}

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
}

fn assert_success(case: &GateCase, step: &str, output: &Output) {
    assert!(
        output.status.success(),
        "{} {step}: stdout={} stderr={}",
        case.id,
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
}
