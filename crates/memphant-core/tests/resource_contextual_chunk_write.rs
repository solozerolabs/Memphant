//! R1 docs-domain contextual-chunk write path: the reflect-stage compile
//! (`MemoryService::compile_job`, shared by the public reflect verb and the
//! worker tick) mints per-resource contextual chunks tied to their parent
//! DOCUMENT resource when the `resource_chunks_write_enabled` service option is
//! on — and stays chunk-free (today's behavior) when it is off (the shipped
//! default) or when the resource is not `kind=document`. Recall surfaces a
//! chunk-matched resource and cites it back to the PARENT resource (chunk id ↔
//! parent linkage), exactly like the episode twin.

use std::collections::BTreeMap;
use std::future::Future;
use std::pin::Pin;
use std::sync::{Arc, Mutex};

use memphant_core::service::MemoryService;
use memphant_core::{
    FixedClock, InMemoryStore, MemoryStore, NoopEmbedding, StructuredObservation,
    StructuredObservationDisposition, StructuredStateProvider, StructuredStateProviderError,
    StructuredStateProviderIdentity, StructuredStateRequest, StubEmbedding,
};
use memphant_types::{
    CitationVerification, EvidenceSourceKind, MemoryKind, RecallHttpRequest, ResolvedMemoryContext,
    ResourceKind, RetainEpisodeHttpRequest, RetainResourcePayload, TenantId, TrustLevel,
};
use serde_json::json;
use sha2::{Digest, Sha256};

const CLOCK: FixedClock = FixedClock("2026-07-11T00:00:00Z");
const RESOURCE_URI: &str = "syndai/docs/deploy/configuration.md";
const STRUCTURED_RESOURCE_BODY: &str =
    "Deployment region is eu-west-1. Always run migrations before rollout.";

struct ResourceStructuredProvider {
    identity: StructuredStateProviderIdentity,
    calls: Mutex<Vec<StructuredStateRequest>>,
}

struct WorkflowStateProvider {
    identity: StructuredStateProviderIdentity,
    namespace: String,
}

impl WorkflowStateProvider {
    fn new(namespace: &str) -> Self {
        Self {
            identity: StructuredStateProviderIdentity {
                model: "test/workflow-compiler".to_string(),
                prompt_hash: "workflow-prompt".to_string(),
                schema_hash: "workflow-schema".to_string(),
            },
            namespace: namespace.to_string(),
        }
    }
}

impl StructuredStateProvider for WorkflowStateProvider {
    fn identity(&self) -> &StructuredStateProviderIdentity {
        &self.identity
    }

    fn extract<'a>(
        &'a self,
        request: &'a StructuredStateRequest,
    ) -> Pin<
        Box<
            dyn Future<Output = Result<Vec<StructuredObservation>, StructuredStateProviderError>>
                + Send
                + 'a,
        >,
    > {
        let slice = request.evidence_slices[0].clone();
        let namespace = self.namespace.clone();
        Box::pin(async move {
            let fields = if slice.body.starts_with("Retire ") {
                BTreeMap::new()
            } else {
                BTreeMap::from([("step".to_string(), json!(slice.body))])
            };
            Ok(vec![StructuredObservation {
                namespace,
                item_key: "migration".to_string(),
                fields,
                disposition: StructuredObservationDisposition::State,
                evidence_slice_id: slice.id,
                evidence_quote: slice.body,
                valid_from: None,
                valid_to: None,
            }])
        })
    }
}

impl ResourceStructuredProvider {
    fn new() -> Self {
        Self {
            identity: StructuredStateProviderIdentity {
                model: "test/resource-compiler".to_string(),
                prompt_hash: "resource-prompt".to_string(),
                schema_hash: "resource-schema".to_string(),
            },
            calls: Mutex::new(Vec::new()),
        }
    }
}

impl StructuredStateProvider for ResourceStructuredProvider {
    fn identity(&self) -> &StructuredStateProviderIdentity {
        &self.identity
    }

    fn extract<'a>(
        &'a self,
        request: &'a StructuredStateRequest,
    ) -> Pin<
        Box<
            dyn Future<Output = Result<Vec<StructuredObservation>, StructuredStateProviderError>>
                + Send
                + 'a,
        >,
    > {
        self.calls.lock().unwrap().push(request.clone());
        let evidence_slice_id = request.evidence_slices[0].id.clone();
        let evidence_body = request.evidence_slices[0].body.clone();
        let batch_index = request.batch_index;
        Box::pin(async move {
            if evidence_body != STRUCTURED_RESOURCE_BODY {
                return Ok(vec![StructuredObservation {
                    namespace: "deployment".to_string(),
                    item_key: "region".to_string(),
                    fields: BTreeMap::from([("value".to_string(), json!(batch_index))]),
                    disposition: StructuredObservationDisposition::State,
                    evidence_slice_id,
                    evidence_quote: evidence_body,
                    valid_from: None,
                    valid_to: None,
                }]);
            }
            Ok(vec![
                StructuredObservation {
                    namespace: "deployment".to_string(),
                    item_key: "region".to_string(),
                    fields: BTreeMap::from([("value".to_string(), json!("eu-west-1"))]),
                    disposition: StructuredObservationDisposition::State,
                    evidence_slice_id: evidence_slice_id.clone(),
                    evidence_quote: "Deployment region is eu-west-1.".to_string(),
                    valid_from: None,
                    valid_to: None,
                },
                StructuredObservation {
                    namespace: "workflow".to_string(),
                    item_key: "migration".to_string(),
                    fields: BTreeMap::from([(
                        "step".to_string(),
                        json!("run migrations before rollout"),
                    )]),
                    disposition: StructuredObservationDisposition::Event,
                    evidence_slice_id,
                    evidence_quote: "Always run migrations before rollout.".to_string(),
                    valid_from: None,
                    valid_to: None,
                },
            ])
        })
    }
}

/// A markdown section (as the gate ingests one: a `###` heading then several
/// paragraphs) long enough to span more than one char-budget window, so the
/// chunker mints ≥2 chunks. The third paragraph carries a distinctive phrase
/// ("peregrine falcon telemetry") used to drive a chunk-specific recall.
const RESOURCE_BODY: &str = "### Deployment Configuration Reference\n\n\
The deployment configuration reference documents every environment variable the service reads at \
startup, grouped by subsystem, so that operators can audit a running cluster against a known-good \
baseline before promoting a release candidate to production traffic across every region and \
availability zone in the fleet during a carefully staged progressive rollout window.\n\n\
Database connection pooling is governed by the pool ceiling knob, which bounds the maximum number \
of concurrent physical connections the worker fleet may open against the primary Postgres writer \
before backpressure and queueing engage to protect the database from connection storms during a \
coordinated cold start of the entire worker fleet after a full regional failover event occurs.\n\n\
The peregrine falcon telemetry exporter streams per-request tracing spans to the collector sidecar \
over a unix domain socket, batching spans in memory and flushing them on a fixed cadence so that a \
slow collector never stalls the hot request path nor leaks unbounded memory when the downstream \
tracing backend happens to be temporarily offline during a collector deployment or restart.\n\n\
Rate limiting is enforced at the edge proxy using a token bucket per API key, refilled at a steady \
rate with a small burst allowance, so that a bursty client is smoothed rather than hard rejected \
while a sustained abuser is throttled down to its fair share of the shared multi-tenant capacity \
envelope that protects every other tenant colocated on the same physical node in the cluster.\n";

fn service(store: InMemoryStore, resource_chunks: bool) -> MemoryService<InMemoryStore> {
    MemoryService::new(
        Arc::new(store),
        Arc::new(CLOCK),
        Arc::new(StubEmbedding::default()),
    )
    .with_resource_chunks_write_enabled(resource_chunks)
}

fn retain_resource_request(
    context: &ResolvedMemoryContext,
    kind: ResourceKind,
) -> RetainEpisodeHttpRequest {
    RetainEpisodeHttpRequest {
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        source_ref: RESOURCE_URI.to_string(),
        observed_at: CLOCK.0.to_string(),
        payload: memphant_types::RetainPayload::Resource(RetainResourcePayload {
            uri: RESOURCE_URI.to_string(),
            mime_type: "text/markdown".to_string(),
            content_hash: format!("sha256:{:x}", Sha256::digest(RESOURCE_BODY.as_bytes())),
            kind: Some(kind),
            revision: Some("r1-gate".to_string()),
            body: Some(RESOURCE_BODY.to_string()),
        }),
    }
}

fn structured_resource_request(
    context: &ResolvedMemoryContext,
    body: &str,
) -> RetainEpisodeHttpRequest {
    RetainEpisodeHttpRequest {
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        actor_id: context.actor_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        source_ref: "test://structured-resource".to_string(),
        observed_at: CLOCK.0.to_string(),
        payload: memphant_types::RetainPayload::Resource(RetainResourcePayload {
            uri: "test://structured-resource".to_string(),
            mime_type: "text/plain".to_string(),
            content_hash: format!("sha256:{:x}", Sha256::digest(body.as_bytes())),
            kind: Some(ResourceKind::Document),
            revision: Some("v1".to_string()),
            body: Some(body.to_string()),
        }),
    }
}

#[tokio::test]
async fn resource_reflection_retains_raw_evidence_and_compiles_structured_units() {
    let store = InMemoryStore::default();
    let provider = Arc::new(ResourceStructuredProvider::new());
    let service = MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        Arc::new(NoopEmbedding),
    )
    .with_structured_state_provider(provider.clone());
    let context = memphant_store_testkit::bind_context(&store, TenantId::new()).await;
    let retained = service
        .retain(
            &context,
            concat!("test:", line!()),
            TrustLevel::TrustedSystem,
            structured_resource_request(&context, STRUCTURED_RESOURCE_BODY),
        )
        .await
        .expect("retain resource");
    let retained: memphant_types::RetainEpisodeHttpResponse =
        serde_json::from_slice(retained.body()).expect("retain response");
    let resource_id = retained.resource_id.expect("resource retained");
    let queued = store.reflect_jobs(context.tenant_id);
    assert_eq!(queued.len(), 1);
    assert_eq!(
        queued[0].compiler_version,
        memphant_core::structured_compiler_identity(
            &format!(
                "{}+source-slice-greedy-batches-v2",
                memphant_types::COMPILER_VERSION
            ),
            provider.identity(),
        )
    );

    assert_eq!(
        service.run_worker_tick(1).await.expect("reflect").completed,
        1
    );
    {
        let calls = provider.calls.lock().unwrap();
        assert_eq!(calls.len(), 1, "short resource uses one full-body fallback");
        assert_eq!(
            calls[0].source_kind,
            memphant_core::StructuredSourceKind::Resource
        );
        assert_eq!(
            calls[0].evidence_slices[0].source_span,
            format!("0-{}", STRUCTURED_RESOURCE_BODY.len())
        );
    }
    let page = store
        .scope_memory_page(&context, None, 100)
        .await
        .expect("page");
    let linked = page
        .items
        .iter()
        .filter(|unit| unit.source_resource_id == Some(resource_id))
        .collect::<Vec<_>>();
    assert!(linked.iter().all(|unit| unit.source_episode_id.is_none()));
    assert!(linked.iter().any(|unit| {
        unit.kind == MemoryKind::Resource && unit.body == STRUCTURED_RESOURCE_BODY
    }));
    assert!(linked.iter().any(|unit| unit.kind == MemoryKind::Semantic));
    assert!(
        linked
            .iter()
            .any(|unit| unit.kind == MemoryKind::Procedural)
    );
}

#[tokio::test]
async fn resource_observation_batches_fold_once_in_source_order() {
    let body = (0..4)
        .map(|index| format!("section-{index} {}", "x".repeat(900)))
        .collect::<Vec<_>>()
        .join("\n\n");
    let store = InMemoryStore::default();
    let provider = Arc::new(ResourceStructuredProvider::new());
    let service = MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        Arc::new(NoopEmbedding),
    )
    .with_structured_state_provider(provider.clone());
    let context = memphant_store_testkit::bind_context(&store, TenantId::new()).await;
    let retained = service
        .retain(
            &context,
            concat!("test:", line!()),
            TrustLevel::TrustedSystem,
            structured_resource_request(&context, &body),
        )
        .await
        .expect("retain resource");
    let retained: memphant_types::RetainEpisodeHttpResponse =
        serde_json::from_slice(retained.body()).expect("retain response");
    let resource_id = retained.resource_id.expect("resource retained");

    assert_eq!(
        service.run_worker_tick(1).await.expect("reflect").completed,
        1
    );
    let expected_last = {
        let calls = provider.calls.lock().unwrap();
        assert!(calls.len() > 1, "oversized resources use multiple batches");
        assert!(
            calls
                .iter()
                .all(|request| request.evidence_slices.len() == 1)
        );
        assert_eq!(
            calls
                .iter()
                .map(|request| request.batch_index)
                .collect::<Vec<_>>(),
            (0..calls.len()).collect::<Vec<_>>()
        );
        calls.len() - 1
    };

    let page = store
        .scope_memory_page(&context, None, 100)
        .await
        .expect("page");
    let compiled = page
        .items
        .iter()
        .filter(|unit| {
            unit.source_resource_id == Some(resource_id) && unit.kind == MemoryKind::Semantic
        })
        .collect::<Vec<_>>();
    assert_eq!(compiled.len(), 1, "all batches fold into one final state");
    assert_eq!(
        compiled[0].body,
        format!("deployment item region: {{\"value\":{expected_last}}}")
    );
}

#[tokio::test]
async fn procedural_resource_state_replaces_then_retires_across_jobs() {
    for namespace in ["workflow", "procedure", "gotcha"] {
        let store = InMemoryStore::default();
        let service = MemoryService::new(
            Arc::new(store.clone()),
            Arc::new(CLOCK),
            Arc::new(NoopEmbedding),
        )
        .with_structured_state_provider(Arc::new(WorkflowStateProvider::new(namespace)));
        let context = memphant_store_testkit::bind_context(&store, TenantId::new()).await;

        for (index, body) in [
            "Run database migrations before rollout.",
            "Run database migrations after backup.",
            "Retire the database migration workflow.",
        ]
        .into_iter()
        .enumerate()
        {
            service
                .retain(
                    &context,
                    &format!("{namespace}-state-{index}"),
                    TrustLevel::TrustedSystem,
                    structured_resource_request(&context, body),
                )
                .await
                .expect("retain procedural state");
            assert_eq!(
                service.run_worker_tick(1).await.expect("reflect").completed,
                1
            );

            let active = store
                .scope_memory_page(&context, None, 100)
                .await
                .expect("scope page")
                .items
                .into_iter()
                .filter(|unit| {
                    unit.kind == MemoryKind::Procedural
                        && unit.state == memphant_types::UnitState::Active
                        && unit.valid_to.is_none()
                        && unit
                            .body
                            .starts_with(&format!("{namespace} item migration:"))
                })
                .collect::<Vec<_>>();
            match index {
                0 => assert_eq!(active.len(), 1),
                1 => {
                    assert_eq!(
                        active.len(),
                        1,
                        "replacement must retire the prior {namespace}"
                    );
                    assert!(active[0].body.contains("after backup"));
                }
                2 => assert!(active.is_empty(), "retirement must remove the {namespace}"),
                _ => unreachable!(),
            }
        }
    }
}

#[tokio::test]
async fn identical_resource_extraction_mints_tenant_local_units_and_citations() {
    let store = InMemoryStore::default();
    let service = MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        Arc::new(NoopEmbedding),
    )
    .with_structured_state_provider(Arc::new(ResourceStructuredProvider::new()));
    let left = memphant_store_testkit::bind_context(&store, TenantId::new()).await;
    let right = memphant_store_testkit::bind_context(&store, TenantId::new()).await;

    for context in [&left, &right] {
        service
            .retain(
                context,
                "same-extraction",
                TrustLevel::TrustedSystem,
                structured_resource_request(context, STRUCTURED_RESOURCE_BODY),
            )
            .await
            .expect("retain resource");
    }
    assert_eq!(
        service.run_worker_tick(2).await.expect("reflect").completed,
        2
    );

    let left_response = service
        .recall(
            left.clone(),
            recall_request(&left, "deployment region eu-west-1"),
        )
        .await
        .expect("left recall");
    let right_response = service
        .recall(
            right.clone(),
            recall_request(&right, "deployment region eu-west-1"),
        )
        .await
        .expect("right recall");
    let left_units = left_response
        .items
        .iter()
        .map(|item| item.unit_id)
        .collect::<std::collections::HashSet<_>>();
    let right_units = right_response
        .items
        .iter()
        .map(|item| item.unit_id)
        .collect::<std::collections::HashSet<_>>();
    assert!(!left_units.is_empty() && !right_units.is_empty());
    assert!(left_units.is_disjoint(&right_units));
    let left_citations = left_response
        .citations
        .iter()
        .map(|citation| citation.unit_id)
        .collect::<std::collections::HashSet<_>>();
    let right_citations = right_response
        .citations
        .iter()
        .map(|citation| citation.unit_id)
        .collect::<std::collections::HashSet<_>>();
    assert!(!left_citations.is_empty() && !right_citations.is_empty());
    assert!(left_citations.is_disjoint(&right_citations));
}

fn recall_request(context: &ResolvedMemoryContext, query: &str) -> RecallHttpRequest {
    RecallHttpRequest {
        compact_only: false,
        subject_id: context.data_subject_id,
        scope_id: context.scope_id,
        agent_node_id: context.agent_node_id,
        subject_generation: context.subject_generation,
        actor_id: context.actor_id,
        query: query.to_string(),
        limit: None,
        budget_tokens: None,
        mode: None,
        include_beliefs: None,
        transaction_as_of: None,
        valid_at: None,
        aggregation_window: None,
    }
}

/// Retains + reflects a document resource, returning the parent-resource unit's
/// chunk vector.
async fn reflect_resource_chunks(
    store: &InMemoryStore,
    service: &MemoryService<InMemoryStore>,
    tenant: TenantId,
    kind: ResourceKind,
) -> Vec<memphant_types::ContextualChunk> {
    let context = memphant_store_testkit::bind_context(store, tenant).await;
    let retained = service
        .retain(
            &context,
            concat!("test:", line!()),
            TrustLevel::TrustedSystem,
            retain_resource_request(&context, kind),
        )
        .await
        .expect("retain resource");
    let retained: memphant_types::RetainEpisodeHttpResponse =
        serde_json::from_slice(retained.body()).expect("retain response");
    let resource_id = retained.resource_id.expect("resource retained");
    service.run_worker_tick(usize::MAX).await.expect("reflect");

    let page = store
        .scope_memory_page(&context, None, 100)
        .await
        .expect("page");
    let unit = page
        .items
        .iter()
        .find(|unit| unit.source_resource_id == Some(resource_id))
        .expect("resource-derived unit");
    unit.contextual_chunks.clone()
}

/// Default construction (no builder opt-in) keeps the resource-chunk write path
/// OFF — shipped behavior is byte-identical to today (whole-section unit only).
#[tokio::test]
async fn reflect_stays_chunk_free_by_default() {
    let store = InMemoryStore::default();
    let service = MemoryService::new(
        Arc::new(store.clone()),
        Arc::new(CLOCK),
        Arc::new(StubEmbedding::default()),
    );
    let tenant = TenantId::new();
    let chunks = reflect_resource_chunks(&store, &service, tenant, ResourceKind::Document).await;
    assert!(
        chunks.is_empty(),
        "default (flag off) mints no resource chunks — byte-identical to today"
    );
}

/// Flag ON + `kind=document` → the parent whole-section unit REMAINS stored
/// verbatim and additionally carries ≥2 contextual chunks, each linked to the
/// parent resource with a byte-offset span that reproduces its body verbatim.
#[tokio::test]
async fn reflect_mints_resource_chunks_when_enabled_for_document() {
    let store = InMemoryStore::default();
    let service = service(store.clone(), true);
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;

    let retained = service
        .retain(
            &context,
            concat!("test:", line!()),
            TrustLevel::TrustedSystem,
            retain_resource_request(&context, ResourceKind::Document),
        )
        .await
        .expect("retain resource");
    let retained: memphant_types::RetainEpisodeHttpResponse =
        serde_json::from_slice(retained.body()).expect("retain response");
    let resource_id = retained.resource_id.expect("resource retained");
    service.run_worker_tick(usize::MAX).await.expect("reflect");

    let page = store
        .scope_memory_page(&context, None, 100)
        .await
        .expect("page");
    let unit = page
        .items
        .iter()
        .find(|unit| unit.source_resource_id == Some(resource_id))
        .expect("resource-derived unit");

    // Verbatim is the memory: the parent unit body is the whole section, untouched.
    assert_eq!(unit.body, RESOURCE_BODY, "parent unit body stays verbatim");
    assert!(
        unit.contextual_chunks.len() >= 2,
        "a multi-paragraph section yields ≥2 chunks: {}",
        unit.contextual_chunks.len()
    );

    let resource_uuid = resource_id.as_uuid();
    for (index, chunk) in unit.contextual_chunks.iter().enumerate() {
        assert_eq!(
            chunk.id,
            format!("chunk-{resource_uuid}-{index}"),
            "chunk id derives from the parent resource + window index"
        );
        assert_eq!(
            chunk.header, "### Deployment Configuration Reference",
            "every chunk carries the section heading as its context header"
        );
        assert!(!chunk.body.trim().is_empty(), "no empty-body chunks");
        // Span reproduces the chunk body verbatim from the parent body.
        let (start, end) = chunk
            .source_span
            .as_deref()
            .and_then(|span| span.split_once('-'))
            .map(|(s, e)| (s.parse::<usize>().unwrap(), e.parse::<usize>().unwrap()))
            .expect("chunk carries a byte span");
        assert_eq!(
            &RESOURCE_BODY[start..end],
            chunk.body,
            "byte span reproduces the chunk body verbatim"
        );
    }
    // Disjoint, document-ordered spans (non-overlapping partition).
    let spans: Vec<(usize, usize)> = unit
        .contextual_chunks
        .iter()
        .map(|c| {
            let (s, e) = c.source_span.as_deref().unwrap().split_once('-').unwrap();
            (s.parse().unwrap(), e.parse().unwrap())
        })
        .collect();
    for pair in spans.windows(2) {
        assert!(
            pair[0].1 <= pair[1].0,
            "chunk spans are disjoint and ordered"
        );
    }
}

/// Flag ON but a NON-document resource (`kind=code`) stays chunk-free: only the
/// docs domain is chunked.
#[tokio::test]
async fn reflect_stays_chunk_free_for_non_document_kind() {
    let store = InMemoryStore::default();
    let service = service(store.clone(), true);
    let tenant = TenantId::new();
    let chunks = reflect_resource_chunks(&store, &service, tenant, ResourceKind::Code).await;
    assert!(
        chunks.is_empty(),
        "non-document resources are never chunked, even with the flag on"
    );
}

/// End-to-end: retain → reflect (chunks on) → recall. A query for a phrase that
/// lives in one chunk surfaces the document resource via that chunk and cites it
/// back to the PARENT resource (not the chunk).
#[tokio::test]
async fn recall_surfaces_document_resource_via_chunk_and_cites_parent() {
    let store = InMemoryStore::default();
    let service = service(store.clone(), true);
    let tenant = TenantId::new();
    let context = memphant_store_testkit::bind_context(&store, tenant).await;

    let retained = service
        .retain(
            &context,
            concat!("test:", line!()),
            TrustLevel::TrustedSystem,
            retain_resource_request(&context, ResourceKind::Document),
        )
        .await
        .expect("retain resource");
    let retained: memphant_types::RetainEpisodeHttpResponse =
        serde_json::from_slice(retained.body()).expect("retain response");
    let resource_id = retained.resource_id.expect("resource retained");
    service.run_worker_tick(usize::MAX).await.expect("reflect");

    let response = service
        .recall(
            context.clone(),
            recall_request(&context, "peregrine falcon telemetry exporter"),
        )
        .await
        .expect("recall");
    let item = response
        .items
        .iter()
        .find(|item| item.citation_resource_id == Some(resource_id))
        .expect("document resource recalled");
    assert_eq!(
        item.citation_resource_id,
        Some(resource_id),
        "chunk-matched recall cites the PARENT resource, not the chunk"
    );
    assert!(
        item.body.contains("peregrine falcon telemetry"),
        "recalled context carries the matched chunk content: {}",
        item.body
    );
    let receipt = response
        .citations
        .iter()
        .find_map(|citation| match &citation.verification {
            CitationVerification::Verified { receipt }
                if citation.resource_id == Some(resource_id) =>
            {
                Some(receipt)
            }
            _ => None,
        })
        .expect("resource citation is backed by a verified receipt");
    assert_eq!(receipt.trace_id, response.trace_id);
    assert_eq!(receipt.source_kind, EvidenceSourceKind::Resource);
    assert_eq!(receipt.source_id, resource_id.as_uuid());
}
