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
pub const MAX_REQUEST_BYTES: usize = crate::product_contract::MAX_PRODUCT_REQUEST_BYTES;
pub const MAX_RESPONSE_BYTES: usize = crate::MAX_JSON_BYTES;
pub const MAX_STDERR_BYTES: usize = 4096;

pub struct HelperRequest {
    pub identity: RequestIdentity,
    locator: String,
    operation: String,
    output: Option<String>,
    stage: Option<String>,
    mark: Option<Value>,
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
    let operation = v["operation"]
        .as_str()
        .ok_or(ServiceFailure::ResultInvalid)?;
    if (operation == "add"
        && !keys(
            &v,
            &[
                "protocolVersion",
                "operation",
                "request",
                "generation",
                "inputLocator",
                "outputLocator",
                "stagingLocator",
                "personalMark",
            ],
        ))
        || (operation != "add"
            && !keys(
                &v,
                &[
                    "protocolVersion",
                    "operation",
                    "request",
                    "generation",
                    "inputLocator",
                ],
            ))
        || !matches!(operation, "INSPECT_LIMITED" | "limited_inspect" | "add")
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
        operation: operation.to_owned(),
        output: if operation == "add" {
            Some(locator_field(&v, "outputLocator")?)
        } else {
            None
        },
        stage: if operation == "add" {
            Some(locator_field(&v, "stagingLocator")?)
        } else {
            None
        },
        mark: if operation == "add" {
            crate::product_contract::validate_personal_mark(&v["personalMark"])
                .map_err(|_| ServiceFailure::ResultInvalid)?;
            Some(v["personalMark"].clone())
        } else {
            None
        },
    })
}
fn locator_field(v: &Value, field: &str) -> Result<String, ServiceFailure> {
    let s = v[field].as_str().ok_or(ServiceFailure::ResultInvalid)?;
    if s.is_empty() || s.len() > 4096 || s.contains('\0') {
        return Err(ServiceFailure::ResultInvalid);
    }
    Ok(s.to_owned())
}
pub fn encode_product_request(
    id: RequestIdentity,
    operation: &str,
    input: &Path,
    output: Option<&Path>,
    stage: Option<&Path>,
    mark: Option<&Value>,
) -> Result<Vec<u8>, ServiceFailure> {
    let mut v = json!({"protocolVersion":1,"operation":operation,"request":id.request,"generation":id.generation,"inputLocator":input.to_str().ok_or(ServiceFailure::InputUnavailable)?});
    if operation == "add" {
        v["outputLocator"] = json!(output
            .and_then(Path::to_str)
            .ok_or(ServiceFailure::InputUnavailable)?);
        v["stagingLocator"] = json!(stage
            .and_then(Path::to_str)
            .ok_or(ServiceFailure::InputUnavailable)?);
        v["personalMark"] = mark.ok_or(ServiceFailure::ResultInvalid)?.clone();
    }
    let raw = serde_json::to_vec(&v).map_err(|_| ServiceFailure::ResultInvalid)?;
    parse_request(&raw)?;
    Ok(raw)
}
pub fn parse_product_response(
    raw: &[u8],
    expected: RequestIdentity,
    operation: &str,
    exit: i32,
) -> Result<HelperOutcome, ServiceFailure> {
    if !matches!(operation, "add" | "limited_inspect") {
        return Err(ServiceFailure::ResultInvalid);
    }
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
            crate::product_contract::validate_product_result(&v["result"], operation)
                .map_err(|_| ServiceFailure::ResultInvalid)?;
            Ok(HelperOutcome::Success(v["result"].clone()))
        }
        Some("FAILURE") => parse_response(raw, expected, exit),
        _ => Err(ServiceFailure::ResultInvalid),
    }
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
    if req.operation != "INSPECT_LIMITED" {
        let result = if req.operation == "limited_inspect" {
            crate::product::inspect_path(Path::new(&req.locator))
        } else {
            crate::product::add_stage(
                Path::new(&req.locator),
                Path::new(req.output.as_deref().ok_or(ServiceFailure::ResultInvalid)?),
                Path::new(req.stage.as_deref().ok_or(ServiceFailure::ResultInvalid)?),
                req.mark.as_ref().ok_or(ServiceFailure::ResultInvalid)?,
            )
        };
        let v = match result {
            Ok(result) => {
                json!({"protocolVersion":1,"request":req.identity.request,"generation":req.identity.generation,"status":"SUCCESS","result":result})
            }
            Err(error) => {
                json!({"protocolVersion":1,"request":req.identity.request,"generation":req.identity.generation,"status":"FAILURE","error":error.code()})
            }
        };
        let mut raw = serde_json::to_vec(&v).map_err(|_| ServiceFailure::ResultInvalid)?;
        raw.push(b'\n');
        parse_product_response(&raw, req.identity, &req.operation, 0)?;
        return writer
            .write_all(&raw)
            .and_then(|_| writer.flush())
            .map_err(|_| ServiceFailure::ServiceUnavailable);
    }
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
