//! Isolated core/state skeleton only: no helper, IPC, process kill or hard deadline.
//! Service control data never enters the unchanged inner1/outer2 wire contract.

use crate::{
    snapshot::{DetectedFormat, Snapshot, SnapshotFingerprint},
    Failure,
};
use serde_json::Value;
use std::fmt;

/// Closed, path/payload-free service taxonomy. Unused variants are reserved;
/// declaring them does not prove their runtime classification or implementation.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ServiceFailure {
    SourceChanged,
    UnsupportedFormat,
    InputUnavailable,
    C2paAbsent,
    C2paMalformed,
    IntegrityFailure,
    AssetBindingFailure,
    SignatureInvalid,
    CawgAbsent,
    CawgUnsupported,
    InternalSdkFailure,
    ResourceLimitExceeded,
    Timeout,
    Cancelled,
    ServiceUnavailable,
    ResultInvalid,
    CleanupFailed,
}

impl ServiceFailure {
    pub fn code(self) -> &'static str {
        match self {
            Self::SourceChanged => "SOURCE_CHANGED",
            Self::UnsupportedFormat => "UNSUPPORTED_FORMAT",
            Self::InputUnavailable => "INPUT_UNAVAILABLE",
            Self::C2paAbsent => "C2PA_ABSENT",
            Self::C2paMalformed => "C2PA_MALFORMED",
            Self::IntegrityFailure => "INTEGRITY_FAILURE",
            Self::AssetBindingFailure => "ASSET_BINDING_FAILURE",
            Self::SignatureInvalid => "SIGNATURE_INVALID",
            Self::CawgAbsent => "CAWG_ABSENT",
            Self::CawgUnsupported => "CAWG_UNSUPPORTED",
            Self::InternalSdkFailure => "INTERNAL_SDK_FAILURE",
            Self::ResourceLimitExceeded => "RESOURCE_LIMIT_EXCEEDED",
            Self::Timeout => "TIMEOUT",
            Self::Cancelled => "CANCELLED",
            Self::ServiceUnavailable => "SERVICE_UNAVAILABLE",
            Self::ResultInvalid => "RESULT_INVALID",
            Self::CleanupFailed => "CLEANUP_FAILED",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct RequestIdentity {
    pub request: u64,
    pub generation: u64,
}

pub struct InspectionRequest {
    identity: RequestIdentity,
    snapshot: Snapshot,
}

impl InspectionRequest {
    pub fn new(identity: RequestIdentity, snapshot: Snapshot) -> Self {
        Self { identity, snapshot }
    }

    pub fn identity(&self) -> RequestIdentity {
        self.identity
    }

    pub fn fingerprint(&self) -> &SnapshotFingerprint {
        self.snapshot.fingerprint()
    }
}

/// Only the existing strict validator can create a successful formal result.
/// Borrowed access prevents callers from changing an already-validated envelope.
#[derive(PartialEq, Eq)]
pub struct LimitedResult {
    envelope: Value,
}

impl fmt::Debug for LimitedResult {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("LimitedResult(VALIDATED_INNER1_OUTER2)")
    }
}

impl LimitedResult {
    pub fn envelope(&self) -> &Value {
        &self.envelope
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum ServiceOutcome {
    Success(LimitedResult),
    Failure(ServiceFailure),
}

/// Inspection completion is not publication. Its outcome stays private until
/// caller-owned identity, source observation and one-terminal guards accept it.
pub struct Completion {
    identity: RequestIdentity,
    fingerprint: SnapshotFingerprint,
    outcome: ServiceOutcome,
}

impl Completion {
    pub fn identity(&self) -> RequestIdentity {
        self.identity
    }

    pub fn fingerprint(&self) -> &SnapshotFingerprint {
        &self.fingerprint
    }
}

/// A separately captured source observation, not a claim of continuous immutability.
pub enum SourceObservation {
    Captured(SnapshotFingerprint),
    Unavailable,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CommitResult {
    Committed,
    StaleRequest,
    AlreadyTerminal,
}

/// Single-owner model: Rust exclusive mutable access serializes terminal commits.
/// It models publication cancellation, not SDK interruption or process cleanup.
pub struct TerminalState {
    identity: RequestIdentity,
    terminal: Option<ServiceOutcome>,
}

impl TerminalState {
    pub fn new(identity: RequestIdentity) -> Self {
        Self {
            identity,
            terminal: None,
        }
    }

    pub fn outcome(&self) -> Option<&ServiceOutcome> {
        self.terminal.as_ref()
    }

    pub fn complete(
        &mut self,
        current: RequestIdentity,
        completion: Completion,
        source: SourceObservation,
    ) -> CommitResult {
        if current != self.identity || completion.identity != self.identity {
            return CommitResult::StaleRequest;
        }
        if self.terminal.is_some() {
            return CommitResult::AlreadyTerminal;
        }
        let outcome = match (completion.outcome, source) {
            (ServiceOutcome::Success(_), SourceObservation::Unavailable) => {
                ServiceOutcome::Failure(ServiceFailure::InputUnavailable)
            }
            (ServiceOutcome::Success(_), SourceObservation::Captured(observed))
                if observed != completion.fingerprint =>
            {
                ServiceOutcome::Failure(ServiceFailure::SourceChanged)
            }
            (outcome, _) => outcome,
        };
        self.terminal = Some(outcome);
        CommitResult::Committed
    }

    pub fn fail(&mut self, current: RequestIdentity, failure: ServiceFailure) -> CommitResult {
        if current != self.identity {
            return CommitResult::StaleRequest;
        }
        if self.terminal.is_some() {
            return CommitResult::AlreadyTerminal;
        }
        self.terminal = Some(ServiceOutcome::Failure(failure));
        CommitResult::Committed
    }

    pub fn cancel(&mut self, current: RequestIdentity) -> CommitResult {
        self.fail(current, ServiceFailure::Cancelled)
    }
}

/// Inspect exactly the snapshot bytes. Never reopen a locator; only the golden
/// fixture (or byte-identical copy) is eligible for the preserved success wire.
pub fn inspect(request: &InspectionRequest) -> Completion {
    let outcome = inspect_snapshot(&request.snapshot)
        .map(ServiceOutcome::Success)
        .unwrap_or_else(ServiceOutcome::Failure);
    Completion {
        identity: request.identity,
        fingerprint: *request.snapshot.fingerprint(),
        outcome,
    }
}

fn inspect_snapshot(snapshot: &Snapshot) -> Result<LimitedResult, ServiceFailure> {
    if snapshot.fingerprint().format() != DetectedFormat::Png {
        return Err(ServiceFailure::UnsupportedFormat);
    }
    // Non-golden PNGs are unsupported in this slice, not evidence of source mutation.
    if snapshot.fingerprint().size() != crate::FIXTURE_SIZE
        || snapshot.fingerprint().sha256_hex() != crate::FIXTURE_SHA256
    {
        return Err(ServiceFailure::UnsupportedFormat);
    }
    // Do not infer specific signature/C2PA failures from the PoC's aggregate error.
    let envelope = crate::inspect_fixed(snapshot.bytes()).map_err(|failure| match failure {
        Failure::ResultSerializationFailed => ServiceFailure::ResultInvalid,
        Failure::FixtureChanged | Failure::FixtureInvalid | Failure::LimitedResultInvalid => {
            ServiceFailure::ResultInvalid
        }
    })?;
    let raw = crate::encode_envelope(&envelope, 0).map_err(|_| ServiceFailure::ResultInvalid)?;
    let validated = crate::parse_envelope(&raw, 0).map_err(|_| ServiceFailure::ResultInvalid)?;
    let source = &validated["inspection"]["source"];
    if source["sha256"].as_str() != Some(snapshot.fingerprint().sha256_hex().as_str())
        || source["size"].as_u64() != Some(snapshot.fingerprint().size() as u64)
        || source["format"].as_str() != Some(snapshot.fingerprint().format().as_str())
    {
        return Err(ServiceFailure::ResultInvalid);
    }
    Ok(LimitedResult {
        envelope: validated,
    })
}
