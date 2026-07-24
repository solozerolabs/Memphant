//! R1 docs-domain contextual-chunk write path: the reflect-stage compile
//! (`MemoryService::compile_job`, shared by the public reflect verb and the
//! worker tick) mints per-resource contextual chunks tied to their parent
//! DOCUMENT resource when the `resource_chunks_write_enabled` service option is
//! on — and stays chunk-free (today's behavior) when it is off (the shipped
//! default) or when the resource is not `kind=document`. Recall surfaces a
//! chunk-matched resource and cites it back to the PARENT resource (chunk id ↔
//! parent linkage), exactly like the episode twin.

use std::sync::Arc;

use memphant_core::service::MemoryService;
use memphant_core::{FixedClock, InMemoryStore, MemoryStore, StubEmbedding};
use memphant_types::{
    CitationVerification, EvidenceSourceKind, RecallHttpRequest, ResolvedMemoryContext,
    ResourceKind, RetainEpisodeHttpRequest, RetainResourcePayload, TenantId, TrustLevel,
};
use sha2::{Digest, Sha256};

const CLOCK: FixedClock = FixedClock("2026-07-11T00:00:00Z");
const RESOURCE_URI: &str = "syndai/docs/deploy/configuration.md";

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

fn recall_request(context: &ResolvedMemoryContext, query: &str) -> RecallHttpRequest {
    RecallHttpRequest {
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
