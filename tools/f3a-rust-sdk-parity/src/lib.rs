//! Development-only fixed-fixture SDK parity PoC; never Full Verification.
//! SDK evidence is separated from the immutable Shirushi inner1/outer2 wire contract.

use c2pa::{Context, Reader, Settings, ValidationState};
use serde::de::{self, MapAccess, SeqAccess, Visitor};
use serde::{Deserialize, Deserializer};
use serde_json::{json, Map, Number, Value};
use sha2::{Digest, Sha256};
use std::{collections::BTreeSet, fmt, io::Cursor};

pub const FIXTURE_SIZE: usize = 319495;
pub const FIXTURE_SHA256: &str = "558c4044228761f91ad1ee1a4637bdd868c65f0e9954e7de928a1262e3076316";
pub const MAX_JSON_BYTES: usize = 64 * 1024;
const MAX_JSON_DEPTH: usize = 8;
const MAX_INPUT_BYTES: usize = 2 * 1024 * 1024;
const RIGHTS_LABEL: &str = "cawg.training-mining";

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Failure {
    FixtureChanged,
    FixtureInvalid,
    LimitedResultInvalid,
    ResultSerializationFailed,
}

impl Failure {
    pub fn code(self) -> &'static str {
        match self {
            Self::FixtureChanged => "FIXTURE_CHANGED",
            Self::FixtureInvalid => "FIXTURE_INVALID",
            Self::LimitedResultInvalid => "LIMITED_RESULT_INVALID",
            Self::ResultSerializationFailed => "RESULT_SERIALIZATION_FAILED",
        }
    }
    pub fn exit_code(self) -> i32 {
        match self {
            Self::FixtureChanged => 10,
            Self::FixtureInvalid => 13,
            Self::LimitedResultInvalid => 14,
            Self::ResultSerializationFailed => 16,
        }
    }
}

/// Internal evidence only. Never serialize SDK strings/URIs into formal output.
#[derive(Clone, Debug)]
pub struct Status {
    pub code: String,
    pub url: Option<String>,
}

#[derive(Clone, Debug)]
pub struct RightsAssertion {
    pub label: String,
    pub instance: usize,
    pub value: Value,
}

#[derive(Clone, Debug)]
pub struct SdkEvidence {
    pub parsed: bool,
    pub embedded: bool,
    pub active_label: Option<String>,
    pub validation_state: String,
    pub references: Vec<String>,
    pub success: Vec<Status>,
    pub informational: Vec<Status>,
    pub failure: Vec<Status>,
    pub legacy_statuses: Vec<Status>,
    pub ingredient_success: Vec<Status>,
    pub ingredient_informational: Vec<Status>,
    pub ingredient_failure: Vec<Status>,
    pub rights: Vec<RightsAssertion>,
}

pub fn fingerprint(bytes: &[u8]) -> Result<(), Failure> {
    if bytes.len() != FIXTURE_SIZE || format!("{:x}", Sha256::digest(bytes)) != FIXTURE_SHA256 {
        return Err(Failure::FixtureChanged);
    }
    if bytes.get(..8) != Some(b"\x89PNG\r\n\x1a\n") || bytes.get(12..16) != Some(b"IHDR") {
        return Err(Failure::FixtureInvalid);
    }
    let width = u32::from_be_bytes(bytes[16..20].try_into().unwrap()) as u64;
    let height = u32::from_be_bytes(bytes[20..24].try_into().unwrap()) as u64;
    if width == 0 || height == 0 || width * height > 4 * 1024 * 1024 {
        return Err(Failure::FixtureInvalid);
    }
    Ok(())
}

/// Pure in-process SDK path. This function also permits bounded disposable
/// copies in REAL SDK negative tests. The success entry always fingerprints first.
pub fn read_sdk_evidence(bytes: &[u8]) -> Result<SdkEvidence, Failure> {
    if bytes.is_empty() || bytes.len() > MAX_INPUT_BYTES {
        return Err(Failure::FixtureInvalid);
    }
    let settings = Settings::new()
        .with_value("verify.verify_after_reading", true)
        .and_then(|s| s.with_value("verify.verify_trust", false))
        .and_then(|s| s.with_value("verify.verify_timestamp_trust", false))
        .and_then(|s| s.with_value("verify.ocsp_fetch", false))
        .and_then(|s| s.with_value("verify.remote_manifest_fetch", false))
        .and_then(|s| s.with_value("core.decode_identity_assertions", false))
        .map_err(|_| Failure::LimitedResultInvalid)?;
    let context = Context::new()
        .with_settings(settings)
        .map_err(|_| Failure::LimitedResultInvalid)?;
    let reader = Reader::from_context(context)
        .with_stream("image/png", Cursor::new(bytes))
        .map_err(|_| Failure::LimitedResultInvalid)?;
    let active = reader
        .active_manifest()
        .ok_or(Failure::LimitedResultInvalid)?;
    let results = reader
        .validation_results()
        .ok_or(Failure::LimitedResultInvalid)?;
    let statuses = results
        .active_manifest()
        .ok_or(Failure::LimitedResultInvalid)?;
    let convert = |s: &c2pa::validation_status::ValidationStatus| Status {
        code: s.code().to_owned(),
        url: s.url().map(str::to_owned),
    };
    let mut ingredient_success = Vec::new();
    let mut ingredient_informational = Vec::new();
    let mut ingredient_failure = Vec::new();
    if let Some(deltas) = results.ingredient_deltas() {
        for delta in deltas {
            let s = delta.validation_deltas();
            ingredient_success.extend(s.success().iter().map(convert));
            ingredient_informational.extend(s.informational().iter().map(convert));
            ingredient_failure.extend(s.failure().iter().map(convert));
        }
    }
    let rights = active
        .assertions()
        .iter()
        .filter(|a| a.label() == RIGHTS_LABEL || a.label().starts_with("cawg.training-mining__"))
        .map(|a| {
            Ok(RightsAssertion {
                label: a.label().to_owned(),
                instance: a.instance(),
                value: a
                    .value()
                    .map_err(|_| Failure::LimitedResultInvalid)?
                    .clone(),
            })
        })
        .collect::<Result<Vec<_>, Failure>>()?;
    let validation_state = match reader.validation_state() {
        ValidationState::Valid => "VALID",
        ValidationState::Trusted => "TRUSTED",
        ValidationState::Invalid => "INVALID",
    }
    .to_owned();
    Ok(SdkEvidence {
        parsed: true,
        embedded: reader.is_embedded() && reader.remote_url().is_none(),
        active_label: reader.active_label().map(str::to_owned),
        validation_state,
        references: active.assertion_references().map(|r| r.url()).collect(),
        success: statuses.success().iter().map(convert).collect(),
        informational: statuses.informational().iter().map(convert).collect(),
        failure: statuses.failure().iter().map(convert).collect(),
        legacy_statuses: reader
            .validation_status()
            .unwrap_or_default()
            .iter()
            .map(convert)
            .collect(),
        ingredient_success,
        ingredient_informational,
        ingredient_failure,
        rights,
    })
}

fn expected_rights() -> Value {
    json!({"entries": {
        "cawg.ai_inference": {"use": "notAllowed"},
        "cawg.ai_generative_training": {"use": "notAllowed"}
    }})
}

fn known_success(code: &str) -> bool {
    matches!(
        code,
        "claimSignature.validated"
            | "claimSignature.insideValidity"
            | "assertion.hashedURI.match"
            | "assertion.dataHash.match"
            | "signingCredential.ocsp.notRevoked"
    )
}

/// Require positive, active-claim-scoped evidence for every signed reference.
/// Merely constructing a Reader or seeing no failures is never sufficient.
pub fn adapt(e: &SdkEvidence) -> Result<Value, Failure> {
    let bad = || Failure::LimitedResultInvalid;
    let label = e.active_label.as_deref().ok_or_else(bad)?;
    if !e.parsed
        || !e.embedded
        || e.validation_state != "VALID"
        || label.is_empty()
        || !e.ingredient_success.is_empty()
        || !e.ingredient_informational.is_empty()
        || !e.ingredient_failure.is_empty()
        || !e.legacy_statuses.is_empty()
        || e.success.iter().any(|s| !known_success(&s.code))
        || e.informational
            .iter()
            .any(|s| s.code != "signingCredential.ocsp.skipped")
        // The oracle rejects any nonempty legacy validation_status, including
        // untrusted credentials. Positive crypto is still required separately.
        || !e.failure.is_empty()
    {
        return Err(bad());
    }
    let prefix = format!("self#jumbf=/c2pa/{label}/");
    let assertion_prefix = format!("{prefix}c2pa.assertions/");
    let signature_url = format!("{prefix}c2pa.signature");
    let has = |code: &str, url: &str| {
        e.success
            .iter()
            .any(|s| s.code == code && s.url.as_deref() == Some(url))
    };
    if !has("claimSignature.validated", &signature_url)
        || !has("claimSignature.insideValidity", &signature_url)
        || e.references.is_empty()
    {
        return Err(bad());
    }
    let mut unique = BTreeSet::new();
    for reference in &e.references {
        if !reference.starts_with(&assertion_prefix)
            || !unique.insert(reference.as_str())
            || !has("assertion.hashedURI.match", reference)
        {
            return Err(bad());
        }
    }
    if !e.success.iter().any(|s| {
        s.code == "assertion.dataHash.match"
            && s.url.as_ref().is_some_and(|url| {
                unique.contains(url.as_str())
                    && url.starts_with(&format!("{assertion_prefix}c2pa.hash.data"))
            })
    }) {
        return Err(bad());
    }
    if e.rights.len() != 1
        || e.rights[0].label != RIGHTS_LABEL
        // Exact SDK0.85.0 labels::instance returns0 for an uninstanced label.
        // Require the unsuffixed signed reference, not a normalized duplicate.
        || e.rights[0].instance != 0
        || e.rights[0].value != expected_rights()
        || !unique.contains(format!("{assertion_prefix}{RIGHTS_LABEL}").as_str())
    {
        return Err(bad());
    }
    Ok(canonical_success())
}

/// Exact read-only oracle values; no SDK diagnostics, identity or extra status.
pub fn canonical_success() -> Value {
    json!({
        "contractVersion": 2, "operation": "limited_c2pa_cawg_inspection",
        "result": "LIMITED_INSPECTION", "completeness": "INCOMPLETE",
        "checks": {"c2pa":"INSPECTED", "cawg":"INSPECTED", "trustmark":"NOT_CHECKED"},
        "inspection": {
            "contract":"shirushi-limited-inspection", "contractVersion":1,
            "overall":"LIMITED_INSPECTION", "reasonCode":"LIMITED_SCOPE",
            "trustmark":"NOT_CHECKED", "fullVerificationPerformed":false,
            "successMotionEligible":false,
            "source":{"sha256":FIXTURE_SHA256, "size":FIXTURE_SIZE, "format":"PNG"},
            "c2pa":{"state":"INSPECTED", "presence":"PRESENT", "parse":true,
                "assertionDigestsValid":true, "assetBindingValid":true,
                "signature":"PREVIEW", "trustValidated":false},
            "cawg":{"state":"INSPECTED", "presence":"PRESENT",
                "aiTrainingUse":"NOT_WANTED", "aiInferenceUse":"NOT_WANTED"}
        }
    })
}

pub fn failure_envelope(error: Failure) -> Value {
    json!({"contractVersion":2, "operation":"limited_c2pa_cawg_inspection",
        "result":"INSPECTION_FAILED", "error":{"code":error.code()}})
}

pub fn inspect_fixed(bytes: &[u8]) -> Result<Value, Failure> {
    fingerprint(bytes)?;
    adapt(&read_sdk_evidence(bytes)?)
}

pub fn validate_envelope(value: &Value, exit: i32) -> Result<(), Failure> {
    if exit == 0 && *value == canonical_success() {
        return Ok(());
    }
    for error in [
        Failure::FixtureChanged,
        Failure::FixtureInvalid,
        Failure::LimitedResultInvalid,
        Failure::ResultSerializationFailed,
    ] {
        if exit == error.exit_code() && *value == failure_envelope(error) {
            return Ok(());
        }
    }
    Err(Failure::LimitedResultInvalid)
}

pub fn encode_envelope(value: &Value, exit: i32) -> Result<Vec<u8>, Failure> {
    validate_envelope(value, exit)?;
    let mut raw = serde_json::to_vec(value).map_err(|_| Failure::ResultSerializationFailed)?;
    raw.push(b'\n');
    if raw.len() > MAX_JSON_BYTES {
        return Err(Failure::ResultSerializationFailed);
    }
    Ok(raw)
}

fn depth(value: &Value, level: usize) -> bool {
    level <= MAX_JSON_DEPTH
        && match value {
            Value::Array(v) => v.iter().all(|x| depth(x, level + 1)),
            Value::Object(v) => v.values().all(|x| depth(x, level + 1)),
            _ => true,
        }
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

pub fn parse_envelope(raw: &[u8], exit: i32) -> Result<Value, Failure> {
    if raw.is_empty() || raw.len() > MAX_JSON_BYTES {
        return Err(Failure::LimitedResultInvalid);
    }
    let value = serde_json::from_slice::<UniqueValue>(raw)
        .map_err(|_| Failure::LimitedResultInvalid)?
        .0;
    if !depth(&value, 0) {
        return Err(Failure::LimitedResultInvalid);
    }
    validate_envelope(&value, exit)?;
    Ok(value)
}

#[cfg(test)]
mod adapter_tests;
#[cfg(test)]
mod real_sdk_tests;
