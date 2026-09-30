//! Desktop-side one-shot helper protocol v1. No SDK or inspection implementation.
//! Locators exist only in private request bytes. Errors never carry parser text.
use serde::de::{self, MapAccess, SeqAccess, Visitor};
use serde::{Deserialize, Deserializer};
use serde_json::{json, Map, Number, Value};
use std::fmt;
use std::{
    io::{Read, Write},
    path::Path,
};

pub const PROTOCOL_VERSION: u64 = 1;
pub const MAX_REQUEST_BYTES: usize = 8192;
pub const MAX_RESPONSE_BYTES: usize = 64 * 1024;
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
    let v = serde_json::from_slice::<UniqueValue>(raw)
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
            if v["result"] != expected_limited_wire() {
                return Err(ServiceFailure::ResultInvalid);
            }
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

fn expected_limited_wire() -> Value {
    json!({
        "contractVersion": 2, "operation": "limited_c2pa_cawg_inspection",
        "result": "LIMITED_INSPECTION", "completeness": "INCOMPLETE",
        "checks": {"c2pa":"INSPECTED", "cawg":"INSPECTED", "trustmark":"NOT_CHECKED"},
        "inspection": {
            "contract":"shirushi-limited-inspection", "contractVersion":1,
            "overall":"LIMITED_INSPECTION", "reasonCode":"LIMITED_SCOPE",
            "trustmark":"NOT_CHECKED", "fullVerificationPerformed":false,
            "successMotionEligible":false,
            "source":{"sha256":"558c4044228761f91ad1ee1a4637bdd868c65f0e9954e7de928a1262e3076316", "size":319495, "format":"PNG"},
            "c2pa":{"state":"INSPECTED", "presence":"PRESENT", "parse":true,
                "assertionDigestsValid":true, "assetBindingValid":true,
                "signature":"PREVIEW", "trustValidated":false},
            "cawg":{"state":"INSPECTED", "presence":"PRESENT",
                "aiTrainingUse":"NOT_WANTED", "aiInferenceUse":"NOT_WANTED"}
        }
    })
}

struct UniqueValue(Value);
impl<'de> Deserialize<'de> for UniqueValue {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct UniqueVisitor;
        impl<'de> Visitor<'de> for UniqueVisitor {
            type Value = UniqueValue;
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("strict JSON")
            }
            fn visit_bool<E: de::Error>(self, v: bool) -> Result<Self::Value, E> {
                Ok(UniqueValue(Value::Bool(v)))
            }
            fn visit_i64<E: de::Error>(self, v: i64) -> Result<Self::Value, E> {
                Ok(UniqueValue(v.into()))
            }
            fn visit_u64<E: de::Error>(self, v: u64) -> Result<Self::Value, E> {
                Ok(UniqueValue(v.into()))
            }
            fn visit_f64<E: de::Error>(self, v: f64) -> Result<Self::Value, E> {
                Number::from_f64(v)
                    .map(|n| UniqueValue(Value::Number(n)))
                    .ok_or_else(|| E::custom("nonfinite"))
            }
            fn visit_str<E: de::Error>(self, v: &str) -> Result<Self::Value, E> {
                Ok(UniqueValue(v.into()))
            }
            fn visit_string<E: de::Error>(self, v: String) -> Result<Self::Value, E> {
                Ok(UniqueValue(v.into()))
            }
            fn visit_unit<E: de::Error>(self) -> Result<Self::Value, E> {
                Ok(UniqueValue(Value::Null))
            }
            fn visit_seq<A: SeqAccess<'de>>(self, mut a: A) -> Result<Self::Value, A::Error> {
                let mut values = Vec::new();
                while let Some(v) = a.next_element::<UniqueValue>()? {
                    values.push(v.0);
                }
                Ok(UniqueValue(Value::Array(values)))
            }
            fn visit_map<A: MapAccess<'de>>(self, mut a: A) -> Result<Self::Value, A::Error> {
                let mut values = Map::new();
                while let Some(k) = a.next_key::<String>()? {
                    if values.contains_key(&k) {
                        return Err(de::Error::custom("duplicate key"));
                    }
                    values.insert(k, a.next_value::<UniqueValue>()?.0);
                }
                Ok(UniqueValue(Value::Object(values)))
            }
        }
        deserializer.deserialize_any(UniqueVisitor)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct RequestIdentity {
    pub request: u64,
    pub generation: u64,
}

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
