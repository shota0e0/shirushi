use serde::de::{self, DeserializeSeed, MapAccess, SeqAccess, Visitor};
use serde::Serialize;
use serde_json::{Map, Value};
use std::collections::BTreeSet;
use std::fmt;

pub const PROTOCOL_VERSION: u64 = 1;
pub const MAX_REQUEST_BYTES: usize = 16_384;
pub const MAX_RESPONSE_BYTES: usize = 4_194_304;
pub const MAX_JSON_DEPTH: usize = 16;

pub const GET_CAPABILITIES: &str = "get_capabilities";
pub const LOAD_PERSONAL_MARK: &str = "load_personal_mark";

#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct BridgeError {
    pub code: String,
    pub message: String,
}

impl BridgeError {
    pub fn new(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            code: code.into(),
            message: message.into(),
        }
    }
}

impl fmt::Display for BridgeError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "{}: {}", self.code, self.message)
    }
}

impl std::error::Error for BridgeError {}

pub fn encode_request(request_id: &str, method: &str) -> Result<Vec<u8>, BridgeError> {
    if !valid_request_id(request_id) {
        return Err(BridgeError::new("INTERNAL_REQUEST_ID", "generated request id is invalid"));
    }
    if !matches!(method, GET_CAPABILITIES | LOAD_PERSONAL_MARK) {
        return Err(BridgeError::new("INTERNAL_METHOD", "bridge method is not allowlisted"));
    }
    let mut bytes = serde_json::to_vec(&serde_json::json!({
        "protocolVersion": PROTOCOL_VERSION,
        "requestId": request_id,
        "method": method,
        "params": {},
    }))
    .map_err(|_| BridgeError::new("REQUEST_ENCODING_FAILED", "could not encode bridge request"))?;
    if bytes.len() > MAX_REQUEST_BYTES {
        return Err(BridgeError::new("REQUEST_TOO_LARGE", "bridge request exceeds its byte limit"));
    }
    bytes.push(b'\n');
    Ok(bytes)
}

pub fn decode_response(raw: &[u8], expected_id: &str, method: &str) -> Result<Value, BridgeError> {
    if raw.len() > MAX_RESPONSE_BYTES {
        return Err(BridgeError::new("RESPONSE_TOO_LARGE", "bridge response exceeds its byte limit"));
    }
    let mut deserializer = serde_json::Deserializer::from_slice(raw);
    let value = StrictSeed { depth: 1 }
        .deserialize(&mut deserializer)
        .map_err(|error| BridgeError::new("MALFORMED_RESPONSE", format!("invalid bridge response: {error}")))?;
    deserializer
        .end()
        .map_err(|error| BridgeError::new("MALFORMED_RESPONSE", format!("trailing response data: {error}")))?;
    validate_response(value, expected_id, method)
}

fn validate_response(value: Value, expected_id: &str, method: &str) -> Result<Value, BridgeError> {
    let object = as_object(&value, "response")?;
    let ok = object
        .get("ok")
        .and_then(Value::as_bool)
        .ok_or_else(|| BridgeError::new("MALFORMED_RESPONSE", "response ok must be boolean"))?;
    exact_keys(
        object,
        if ok {
            &["protocolVersion", "requestId", "ok", "result"]
        } else {
            &["protocolVersion", "requestId", "ok", "error"]
        },
        "response",
    )?;
    if object.get("protocolVersion").and_then(Value::as_u64) != Some(PROTOCOL_VERSION) {
        return Err(BridgeError::new(
            "PROTOCOL_VERSION_MISMATCH",
            "sidecar response protocol version does not match",
        ));
    }
    if object.get("requestId").and_then(Value::as_str) != Some(expected_id) {
        return Err(BridgeError::new(
            "RESPONSE_ID_MISMATCH",
            "sidecar response request id does not match",
        ));
    }
    if ok {
        let result = object.get("result").expect("exact result key").clone();
        match method {
            GET_CAPABILITIES => validate_capabilities(&result)?,
            LOAD_PERSONAL_MARK => validate_personal_mark_read(&result)?,
            _ => return Err(BridgeError::new("INTERNAL_METHOD", "unexpected bridge method")),
        }
        Ok(result)
    } else {
        let error = as_object(object.get("error").expect("exact error key"), "response error")?;
        exact_keys(error, &["code", "message"], "response error")?;
        let code = nonempty_string(error.get("code"), "response error code")?;
        let message = nonempty_string(error.get("message"), "response error message")?;
        Err(BridgeError::new(code, message))
    }
}

fn validate_capabilities(value: &Value) -> Result<(), BridgeError> {
    let object = as_object(value, "capabilities result")?;
    exact_keys(
        object,
        &["bridgeProtocolVersion", "personalMarkSchemaVersions", "renderProfiles", "capabilities"],
        "capabilities result",
    )?;
    expect_u64(object.get("bridgeProtocolVersion"), 1, "bridgeProtocolVersion")?;
    let schemas = as_array(object.get("personalMarkSchemaVersions"), "personalMarkSchemaVersions")?;
    if schemas.len() != 2 || schemas[0].as_u64() != Some(1) || schemas[1].as_u64() != Some(2) {
        return malformed("personalMarkSchemaVersions must be exactly [1,2]");
    }
    let profiles = as_array(object.get("renderProfiles"), "renderProfiles")?;
    if profiles.len() != 1 {
        return malformed("renderProfiles must contain exactly one reviewed profile");
    }
    let profile = as_object(&profiles[0], "render profile")?;
    exact_keys(profile, &["id", "version", "state"], "render profile")?;
    expect_string(profile.get("id"), "shirushi-typed", "render profile id")?;
    expect_u64(profile.get("version"), 1, "render profile version")?;
    expect_string(profile.get("state"), "ASSETS_UNAVAILABLE", "render profile state")?;

    let capabilities = as_object(object.get("capabilities").expect("exact capabilities key"), "capabilities")?;
    let expected = [
        ("personalMarkRead", true),
        ("personalMarkWrite", false),
        ("nativeTargetSelection", false),
        ("coreAdd", false),
        ("coreVerify", false),
        ("coreReadback", false),
        ("c2paPersonalMarkEmbedding", false),
        ("explorerIntegration", false),
    ];
    exact_keys(
        capabilities,
        &expected.iter().map(|(name, _)| *name).collect::<Vec<_>>(),
        "capabilities",
    )?;
    for (name, expected_value) in expected {
        if capabilities.get(name).and_then(Value::as_bool) != Some(expected_value) {
            return malformed(format!("capability {name} has an unreviewed value"));
        }
    }
    Ok(())
}

fn validate_personal_mark_read(value: &Value) -> Result<(), BridgeError> {
    let object = as_object(value, "Personal Mark read result")?;
    expect_string(object.get("contract"), "shirushi-personal-mark-read", "read contract")?;
    expect_u64(object.get("contractVersion"), 1, "read contract version")?;
    let state = nonempty_string(object.get("state"), "read state")?;
    match state {
        "absent" => exact_keys(object, &["contract", "contractVersion", "state"], "absent read result"),
        "malformed" | "unsupported" | "io_error" => {
            exact_keys(object, &["contract", "contractVersion", "state", "source", "errorCode"], "failed read result")?;
            match object.get("source").and_then(Value::as_str) {
                Some("v1" | "v2") => {}
                _ => return malformed("failed read source must be v1 or v2"),
            }
            nonempty_string(object.get("errorCode"), "read errorCode")?;
            Ok(())
        }
        "legacy_v1" => {
            exact_keys(object, &["contract", "contractVersion", "state", "sourceVersion", "geometryProvenance", "payload"], "legacy read result")?;
            expect_u64(object.get("sourceVersion"), 1, "legacy sourceVersion")?;
            expect_string(object.get("geometryProvenance"), "legacy-unknown", "legacy geometryProvenance")?;
            validate_legacy_mark(object.get("payload").expect("exact payload key"))
        }
        "v2" => {
            let mark = object.get("mark").ok_or_else(|| BridgeError::new("MALFORMED_RESPONSE", "v2 read result lacks mark"))?;
            expect_u64(object.get("sourceVersion"), 2, "v2 sourceVersion")?;
            let mark_object = as_object(mark, "v2 mark")?;
            match mark_object.get("type").and_then(Value::as_str) {
                Some("typed") => {
                    exact_keys(object, &["contract", "contractVersion", "state", "sourceVersion", "mark", "renderProfileSupport"], "typed v2 read result")?;
                    validate_typed_mark(mark)?;
                    validate_profile_support(object.get("renderProfileSupport").expect("exact profile support key"))
                }
                Some("handwritten") => {
                    exact_keys(object, &["contract", "contractVersion", "state", "sourceVersion", "mark"], "handwritten v2 read result")?;
                    validate_handwritten_mark(mark, 2)
                }
                _ => malformed("v2 mark type is invalid"),
            }
        }
        _ => malformed("read state is not recognized"),
    }
}

fn validate_typed_mark(value: &Value) -> Result<(), BridgeError> {
    let object = as_object(value, "typed v2 mark")?;
    exact_keys(object, &["version", "type", "text", "renderProfile"], "typed v2 mark")?;
    expect_u64(object.get("version"), 2, "typed mark version")?;
    expect_string(object.get("type"), "typed", "typed mark type")?;
    nonempty_string(object.get("text"), "typed mark text")?;
    let profile = as_object(object.get("renderProfile").expect("exact renderProfile key"), "typed renderProfile")?;
    exact_keys(profile, &["id", "version"], "typed renderProfile")?;
    nonempty_string(profile.get("id"), "render profile id")?;
    integer(profile.get("version"), "render profile version")?;
    Ok(())
}

fn validate_handwritten_mark(value: &Value, version: u64) -> Result<(), BridgeError> {
    let object = as_object(value, "handwritten mark")?;
    if version == 2 {
        exact_keys(object, &["version", "type", "coordinateSpace", "strokes"], "handwritten v2 mark")?;
        let coordinates = as_object(object.get("coordinateSpace").expect("exact coordinateSpace key"), "coordinateSpace")?;
        exact_keys(coordinates, &["width", "height"], "coordinateSpace")?;
        integer(coordinates.get("width"), "coordinateSpace width")?;
        integer(coordinates.get("height"), "coordinateSpace height")?;
    } else {
        exact_keys(object, &["version", "type", "strokes"], "legacy mark")?;
    }
    expect_u64(object.get("version"), version, "mark version")?;
    expect_string(object.get("type"), "handwritten", "mark type")?;
    for stroke in as_array(object.get("strokes"), "strokes")? {
        let stroke = as_object(stroke, "stroke")?;
        exact_keys(stroke, &["points"], "stroke")?;
        for point in as_array(stroke.get("points"), "points")? {
            let point = as_object(point, "point")?;
            exact_keys(point, &["x", "y", "t"], "point")?;
            number(point.get("x"), "point x")?;
            number(point.get("y"), "point y")?;
            integer(point.get("t"), "point t")?;
        }
    }
    Ok(())
}

fn validate_legacy_mark(value: &Value) -> Result<(), BridgeError> {
    validate_handwritten_mark(value, 1)
}

fn validate_profile_support(value: &Value) -> Result<(), BridgeError> {
    let object = as_object(value, "renderProfileSupport")?;
    exact_keys(object, &["state", "errorCode"], "renderProfileSupport")?;
    match object.get("state").and_then(Value::as_str) {
        Some("ASSETS_UNAVAILABLE") => {
            expect_string(object.get("errorCode"), "RENDER_ASSETS_UNAVAILABLE", "profile errorCode")?;
        }
        Some("UNSUPPORTED") => expect_string(object.get("errorCode"), "UNSUPPORTED_RENDER_PROFILE", "profile errorCode")?,
        _ => return malformed("render profile support state is invalid"),
    }
    Ok(())
}

fn valid_request_id(value: &str) -> bool {
    (1..=64).contains(&value.len())
        && value.bytes().all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
}

fn exact_keys(object: &Map<String, Value>, expected: &[&str], context: &str) -> Result<(), BridgeError> {
    let actual = object.keys().map(String::as_str).collect::<BTreeSet<_>>();
    let expected = expected.iter().copied().collect::<BTreeSet<_>>();
    if actual != expected {
        return malformed(format!("{context} fields do not match the protocol"));
    }
    Ok(())
}

fn as_object<'a>(value: &'a Value, context: &str) -> Result<&'a Map<String, Value>, BridgeError> {
    value.as_object().ok_or_else(|| BridgeError::new("MALFORMED_RESPONSE", format!("{context} must be an object")))
}

fn as_array<'a>(value: Option<&'a Value>, context: &str) -> Result<&'a Vec<Value>, BridgeError> {
    value.and_then(Value::as_array).ok_or_else(|| BridgeError::new("MALFORMED_RESPONSE", format!("{context} must be an array")))
}

fn nonempty_string<'a>(value: Option<&'a Value>, context: &str) -> Result<&'a str, BridgeError> {
    match value.and_then(Value::as_str) {
        Some(text) if !text.is_empty() => Ok(text),
        _ => malformed(format!("{context} must be a nonempty string")),
    }
}

fn expect_string(value: Option<&Value>, expected: &str, context: &str) -> Result<(), BridgeError> {
    if value.and_then(Value::as_str) == Some(expected) { Ok(()) } else { malformed(format!("{context} is invalid")) }
}

fn expect_u64(value: Option<&Value>, expected: u64, context: &str) -> Result<(), BridgeError> {
    if value.and_then(Value::as_u64) == Some(expected) { Ok(()) } else { malformed(format!("{context} is invalid")) }
}

fn integer(value: Option<&Value>, context: &str) -> Result<(), BridgeError> {
    if value.is_some_and(|candidate| candidate.as_i64().is_some() || candidate.as_u64().is_some()) { Ok(()) } else { malformed(format!("{context} must be an integer")) }
}

fn number(value: Option<&Value>, context: &str) -> Result<(), BridgeError> {
    if value.is_some_and(Value::is_number) { Ok(()) } else { malformed(format!("{context} must be a finite number")) }
}

fn malformed<T>(message: impl Into<String>) -> Result<T, BridgeError> {
    Err(BridgeError::new("MALFORMED_RESPONSE", message))
}

struct StrictSeed {
    depth: usize,
}

impl<'de> DeserializeSeed<'de> for StrictSeed {
    type Value = Value;

    fn deserialize<D>(self, deserializer: D) -> Result<Self::Value, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        if self.depth > MAX_JSON_DEPTH {
            return Err(de::Error::custom("JSON depth exceeds protocol limit"));
        }
        deserializer.deserialize_any(StrictVisitor { depth: self.depth })
    }
}

struct StrictVisitor {
    depth: usize,
}

impl<'de> Visitor<'de> for StrictVisitor {
    type Value = Value;

    fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("bounded JSON")
    }

    fn visit_bool<E>(self, value: bool) -> Result<Value, E> { Ok(Value::Bool(value)) }
    fn visit_i64<E>(self, value: i64) -> Result<Value, E> { Ok(Value::Number(value.into())) }
    fn visit_u64<E>(self, value: u64) -> Result<Value, E> { Ok(Value::Number(value.into())) }
    fn visit_f64<E: de::Error>(self, value: f64) -> Result<Value, E> {
        serde_json::Number::from_f64(value).map(Value::Number).ok_or_else(|| E::custom("non-finite number"))
    }
    fn visit_str<E: de::Error>(self, value: &str) -> Result<Value, E> { Ok(Value::String(value.to_owned())) }
    fn visit_string<E>(self, value: String) -> Result<Value, E> { Ok(Value::String(value)) }
    fn visit_none<E>(self) -> Result<Value, E> { Ok(Value::Null) }
    fn visit_unit<E>(self) -> Result<Value, E> { Ok(Value::Null) }

    fn visit_seq<A>(self, mut sequence: A) -> Result<Value, A::Error>
    where
        A: SeqAccess<'de>,
    {
        let mut values = Vec::new();
        while let Some(value) = sequence.next_element_seed(StrictSeed { depth: self.depth + 1 })? {
            values.push(value);
        }
        Ok(Value::Array(values))
    }

    fn visit_map<A>(self, mut map: A) -> Result<Value, A::Error>
    where
        A: MapAccess<'de>,
    {
        let mut values = Map::new();
        while let Some(key) = map.next_key::<String>()? {
            if values.contains_key(&key) {
                return Err(de::Error::custom(format!("duplicate key {key}")));
            }
            let value = map.next_value_seed(StrictSeed { depth: self.depth + 1 })?;
            values.insert(key, value);
        }
        Ok(Value::Object(values))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn response(result: Value) -> Vec<u8> {
        serde_json::to_vec(&serde_json::json!({
            "protocolVersion": 1,
            "requestId": "r1",
            "ok": true,
            "result": result,
        })).unwrap()
    }

    #[test]
    fn capabilities_contract_is_exact() {
        let result = serde_json::json!({
            "bridgeProtocolVersion": 1,
            "personalMarkSchemaVersions": [1, 2],
            "renderProfiles": [{"id":"shirushi-typed","version":1,"state":"ASSETS_UNAVAILABLE"}],
            "capabilities": {
                "personalMarkRead": true,
                "personalMarkWrite": false,
                "nativeTargetSelection": false,
                "coreAdd": false,
                "coreVerify": false,
                "coreReadback": false,
                "c2paPersonalMarkEmbedding": false,
                "explorerIntegration": false
            }
        });
        assert_eq!(decode_response(&response(result.clone()), "r1", GET_CAPABILITIES).unwrap(), result);
    }

    #[test]
    fn rejects_duplicate_keys_mismatches_and_excess_depth() {
        let duplicate = br#"{"protocolVersion":1,"requestId":"r1","requestId":"r1","ok":false,"error":{"code":"X","message":"x"}}"#;
        assert_eq!(decode_response(duplicate, "r1", GET_CAPABILITIES).unwrap_err().code, "MALFORMED_RESPONSE");
        let wrong_id = br#"{"protocolVersion":1,"requestId":"other","ok":false,"error":{"code":"X","message":"x"}}"#;
        assert_eq!(decode_response(wrong_id, "r1", GET_CAPABILITIES).unwrap_err().code, "RESPONSE_ID_MISMATCH");
        let wrong_version = br#"{"protocolVersion":2,"requestId":"r1","ok":false,"error":{"code":"X","message":"x"}}"#;
        assert_eq!(decode_response(wrong_version, "r1", GET_CAPABILITIES).unwrap_err().code, "PROTOCOL_VERSION_MISMATCH");
        let nested = format!("{{\"protocolVersion\":1,\"requestId\":\"r1\",\"ok\":true,\"result\":{}}}", "[".repeat(17) + &"]".repeat(17));
        assert_eq!(decode_response(nested.as_bytes(), "r1", LOAD_PERSONAL_MARK).unwrap_err().code, "MALFORMED_RESPONSE");
    }

    #[test]
    fn personal_mark_states_are_preserved_without_repair() {
        let absent = serde_json::json!({"contract":"shirushi-personal-mark-read","contractVersion":1,"state":"absent"});
        assert_eq!(decode_response(&response(absent.clone()), "r1", LOAD_PERSONAL_MARK).unwrap(), absent);
        let unsupported = serde_json::json!({"contract":"shirushi-personal-mark-read","contractVersion":1,"state":"unsupported","source":"v2","errorCode":"UNKNOWN_SCHEMA_VERSION"});
        assert_eq!(decode_response(&response(unsupported.clone()), "r1", LOAD_PERSONAL_MARK).unwrap(), unsupported);
        let legacy = serde_json::json!({
            "contract":"shirushi-personal-mark-read","contractVersion":1,"state":"legacy_v1",
            "sourceVersion":1,"geometryProvenance":"legacy-unknown",
            "payload":{"version":1,"type":"handwritten","strokes":[{"points":[{"x":0.1,"y":0.2,"t":0}]}]}
        });
        assert_eq!(decode_response(&response(legacy.clone()), "r1", LOAD_PERSONAL_MARK).unwrap(), legacy);
        let typed = serde_json::json!({
            "contract":"shirushi-personal-mark-read","contractVersion":1,"state":"v2","sourceVersion":2,
            "mark":{"version":2,"type":"typed","text":"Mori","renderProfile":{"id":"shirushi-typed","version":1}},
            "renderProfileSupport":{"state":"ASSETS_UNAVAILABLE","errorCode":"RENDER_ASSETS_UNAVAILABLE"}
        });
        assert_eq!(decode_response(&response(typed.clone()), "r1", LOAD_PERSONAL_MARK).unwrap(), typed);
    }
}
