use std::future::Future;
use std::pin::Pin;

use memphant_types::{
    DeepProviderIdentity, DeepRecallLimits, DeepRecallStatus, DeepRecallStopReason,
    DeepRecallUsage, DeepWorkspace, EvidenceDisposition,
};
use uuid::Uuid;

#[derive(Debug, Clone)]
pub struct DeepRecallProviderRequest {
    pub query: String,
    pub workspace: DeepWorkspace,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DeepRecallProviderResult {
    pub status: DeepRecallStatus,
    pub stop_reason: DeepRecallStopReason,
    pub source_ids: Vec<Uuid>,
    pub usage: DeepRecallUsage,
    pub generation_ids: Vec<String>,
    pub observed_provider: Option<String>,
    pub observed_model: Option<String>,
    pub evidence: EvidenceDisposition,
}

#[derive(Debug, thiserror::Error)]
pub enum DeepRecallProviderError {
    #[error("deep provider unavailable")]
    Unavailable,
    #[error("deep provider returned invalid output")]
    InvalidOutput,
}

pub trait DeepRecallProvider: Send + Sync {
    fn identity(&self) -> &DeepProviderIdentity;
    fn limits(&self) -> DeepRecallLimits;
    fn gather<'a>(
        &'a self,
        request: DeepRecallProviderRequest,
    ) -> Pin<
        Box<
            dyn Future<Output = Result<DeepRecallProviderResult, DeepRecallProviderError>>
                + Send
                + 'a,
        >,
    >;
}
