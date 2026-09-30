//! Development one-shot IPC v1; unchanged inspection inner1/outer2 is nested.
//! Locators exist only in private request bytes. Errors never carry parser text.
use crate::{
    service::{
        self, InspectionRequest, RequestIdentity, ServiceFailure, ServiceOutcome,
        SourceObservation, TerminalState,
    },
    snapshot::InputLocator,
};
use serde_json::{json, Value};
use std::{
    io::{Read, Write},
    path::Path,
};

pub const PROTOCOL_VERSION: u64 = 1;
pub const MAX_REQUEST_BYTES: usize = 8192;
pub const MAX_RESPONSE_BYTES: usize = crate::MAX_JSON_BYTES;
pub const MAX_STDERR_BYTES: usize = 4096;

pub struct HelperRequest {
    pub identity: RequestIdentity,
    locator: String,
}
impl std::fmt::Debug for HelperRequest {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("HelperRequest(REDACTED)")
    }
}

#[derive(PartialEq, Eq)]
pub enum HelperOutcome {
    Success(Value),
    Failure(ServiceFailure),
}
impl std::fmt::Debug for HelperOutcome {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Success(_) => f.write_str("Success(VALIDATED_INNER1_OUTER2)"),
            Self::Failure(e) => f.write_str(e.code()),
        }
    }
}

fn parse(raw: &[u8], limit: usize) -> Result<Value, ServiceFailure> {
    if raw.is_empty() || raw.len() > limit {
        return Err(ServiceFailure::ResultInvalid);
    }
    // Reuse duplicate-key rejection, including nested inspection objects.
    let v = serde_json::from_slice::<crate::UniqueValue>(raw)
        .map_err(|_| ServiceFailure::ResultInvalid)?
        .0;
    fn depth(v: &Value, n: usize) -> bool {
        n <= 10
            && match v {
                Value::Object(o) => o.values().all(|v| depth(v, n + 1)),
                Value::Array(a) => a.iter().all(|v| depth(v, n + 1)),
                _ => true,
            }
    }
    if !depth(&v, 0) {
        return Err(ServiceFailure::ResultInvalid);
    }
    Ok(v)
}
fn keys(v: &Value, names: &[&str]) -> bool {
    v.as_object()
        .is_some_and(|o| o.len() == names.len() && names.iter().all(|k| o.contains_key(*k)))
}
fn identity(v: &Value) -> Result<RequestIdentity, ServiceFailure> {
    if v["protocolVersion"].as_u64() != Some(PROTOCOL_VERSION) {
        return Err(ServiceFailure::ResultInvalid);
    }
    Ok(RequestIdentity {
        request: v["request"].as_u64().ok_or(ServiceFailure::ResultInvalid)?,
        generation: v["generation"]
            .as_u64()
            .ok_or(ServiceFailure::ResultInvalid)?,
    })
}
pub fn parse_request(raw: &[u8]) -> Result<HelperRequest, ServiceFailure> {
    let v = parse(raw, MAX_REQUEST_BYTES)?;
    if !keys(
        &v,
        &[
            "protocolVersion",
            "operation",
            "request",
            "generation",
            "inputLocator",
        ],
    ) || v["operation"] != "INSPECT_LIMITED"
    {
        return Err(ServiceFailure::ResultInvalid);
    }
    let locator = v["inputLocator"]
        .as_str()
        .ok_or(ServiceFailure::ResultInvalid)?;
    if locator.is_empty() || locator.len() > 4096 || locator.contains('\0') {
        return Err(ServiceFailure::ResultInvalid);
    }
    Ok(HelperRequest {
        identity: identity(&v)?,
        locator: locator.to_owned(),
    })
}
pub fn encode_request(id: RequestIdentity, path: &Path) -> Result<Vec<u8>, ServiceFailure> {
    let path = path.to_str().ok_or(ServiceFailure::InputUnavailable)?;
    let raw = serde_json::to_vec(&json!({"protocolVersion":1,"operation":"INSPECT_LIMITED",
        "request":id.request,"generation":id.generation,"inputLocator":path}))
    .map_err(|_| ServiceFailure::ResultInvalid)?;
    parse_request(&raw)?;
    Ok(raw)
}
fn failure(code: &str) -> Option<ServiceFailure> {
    use ServiceFailure::*;
    [
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
    ]
    .into_iter()
    .find(|e| e.code() == code)
}
pub fn parse_response(
    raw: &[u8],
    expected: RequestIdentity,
    exit: i32,
) -> Result<HelperOutcome, ServiceFailure> {
    if exit != 0 {
        return Err(ServiceFailure::ServiceUnavailable);
    }
    let v = parse(raw, MAX_RESPONSE_BYTES)?;
    if identity(&v)? != expected {
        return Err(ServiceFailure::ResultInvalid);
    }
    match v["status"].as_str() {
        Some("SUCCESS")
            if keys(
                &v,
                &[
                    "protocolVersion",
                    "request",
                    "generation",
                    "status",
                    "result",
                ],
            ) =>
        {
            crate::validate_envelope(&v["result"], 0).map_err(|_| ServiceFailure::ResultInvalid)?;
            Ok(HelperOutcome::Success(v["result"].clone()))
        }
        Some("FAILURE")
            if keys(
                &v,
                &[
                    "protocolVersion",
                    "request",
                    "generation",
                    "status",
                    "error",
                ],
            ) =>
        {
            Ok(HelperOutcome::Failure(
                v["error"]
                    .as_str()
                    .and_then(failure)
                    .ok_or(ServiceFailure::ResultInvalid)?,
            ))
        }
        _ => Err(ServiceFailure::ResultInvalid),
    }
}
pub fn encode_response(
    id: RequestIdentity,
    outcome: &ServiceOutcome,
) -> Result<Vec<u8>, ServiceFailure> {
    let v = match outcome {
        ServiceOutcome::Success(r) => json!({"protocolVersion":1,"request":id.request,
            "generation":id.generation,"status":"SUCCESS","result":r.envelope()}),
        ServiceOutcome::Failure(e) => json!({"protocolVersion":1,"request":id.request,
            "generation":id.generation,"status":"FAILURE","error":e.code()}),
    };
    let raw = serde_json::to_vec(&v).map_err(|_| ServiceFailure::ResultInvalid)?;
    parse_response(&raw, id, 0)?;
    Ok(raw)
}
pub fn read_bounded(reader: &mut impl Read, cap: usize) -> Result<Vec<u8>, ServiceFailure> {
    let mut raw = Vec::new();
    let mut buffer = [0u8; 1024];
    loop {
        let n = (cap + 1 - raw.len()).min(buffer.len());
        let count = match reader.read(&mut buffer[..n]) {
            Ok(0) => return Ok(raw),
            Ok(n) => n,
            Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(_) => return Err(ServiceFailure::ServiceUnavailable),
        };
        if raw.len() + count > cap {
            return Err(ServiceFailure::ResourceLimitExceeded);
        }
        raw.extend_from_slice(&buffer[..count]);
    }
}
pub fn serve(reader: &mut impl Read, writer: &mut impl Write) -> Result<(), ServiceFailure> {
    let req = parse_request(&read_bounded(reader, MAX_REQUEST_BYTES)?)?;
    let mut terminal = TerminalState::new(req.identity);
    match InputLocator::new(Path::new(&req.locator)).and_then(|l| l.capture()) {
        Ok(snapshot) => {
            let request = InspectionRequest::new(req.identity, snapshot);
            let completion = service::inspect(&request);
            // Separate observation only; never reopen from the semantic core.
            let source = match InputLocator::new(Path::new(&req.locator)).and_then(|l| l.capture())
            {
                Ok(s) => SourceObservation::Captured(*s.fingerprint()),
                Err(_) => SourceObservation::Unavailable,
            };
            terminal.complete(req.identity, completion, source);
        }
        Err(e) => {
            terminal.fail(req.identity, e);
        }
    }
    let raw = encode_response(
        req.identity,
        terminal.outcome().ok_or(ServiceFailure::ResultInvalid)?,
    )?;
    writer
        .write_all(&raw)
        .and_then(|_| writer.flush())
        .map_err(|_| ServiceFailure::ServiceUnavailable)
}
