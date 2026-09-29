import { deepFreeze, fail, immutableClone } from "./errors.js";
import { assertNativeDepth, assertWellFormedUnicode, MAX_LOCAL_BYTES, utf8ByteLengthStrict } from "./parser.js";
import { validateMarkV2 } from "./contract.js";

const CONTRACT = "shirushi-personal-mark-read";
const CONTRACT_VERSION = 1;
const own = (value, name) => Object.prototype.hasOwnProperty.call(value, name);

function record(value, field) {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail("INVALID_FIELD_TYPE", { field, expected: "object" });
}

function exact(value, fields) {
  for (const field of fields) if (!own(value, field)) fail("MISSING_FIELD", { field });
  for (const field of Object.keys(value)) if (!fields.includes(field)) fail("UNKNOWN_FIELD", { field });
}

function assertJsonCompatible(value, field = "envelope") {
  if (value === null || typeof value === "boolean") return;
  if (typeof value === "string") {
    assertWellFormedUnicode(value);
    return;
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) fail("INVALID_NUMBER", { field });
    return;
  }
  if (Array.isArray(value)) {
    value.forEach((item, index) => assertJsonCompatible(item, `${field}[${index}]`));
    return;
  }
  if (!value || typeof value !== "object") fail("INVALID_FIELD_TYPE", { field, expected: "json_value" });
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) fail("INVALID_FIELD_TYPE", { field, expected: "json_object" });
  const enumerableKeys = Object.keys(value);
  if (Reflect.ownKeys(value).length !== enumerableKeys.length) fail("INVALID_FIELD_TYPE", { field, expected: "plain_json_object" });
  for (const key of enumerableKeys) {
    assertWellFormedUnicode(key);
    assertJsonCompatible(value[key], `${field}.${key}`);
  }
}

function boundedEnvelope(value) {
  assertNativeDepth(value);
  assertJsonCompatible(value);
  let text;
  try {
    text = JSON.stringify(value);
  } catch {
    fail("INVALID_FIELD_TYPE", { field: "envelope", expected: "json_value" });
  }
  if (typeof text !== "string") fail("INVALID_FIELD_TYPE", { field: "envelope", expected: "json_value" });
  const size = utf8ByteLengthStrict(text);
  if (size > MAX_LOCAL_BYTES) fail("PAYLOAD_TOO_LARGE", { maximum: MAX_LOCAL_BYTES, actual: size });
}

/**
 * Validates the narrow result envelope produced by Python's authoritative
 * dual reader. This deliberately does not validate or convert Python v1.
 */
export function validatePythonReadEnvelope(value) {
  record(value, "envelope");
  boundedEnvelope(value);
  if (value.contract !== CONTRACT || value.contractVersion !== CONTRACT_VERSION) {
    fail("INVALID_FIELD_TYPE", { field: "envelope.contract", expected: `${CONTRACT}@${CONTRACT_VERSION}` });
  }
  if (typeof value.state !== "string") fail("INVALID_FIELD_TYPE", { field: "state", expected: "string" });

  if (value.state === "absent") {
    exact(value, ["contract", "contractVersion", "state"]);
    return deepFreeze({ state: "ABSENT", source: "PYTHON_AUTHORITY" });
  }
  if (["malformed", "unsupported", "io_error"].includes(value.state)) {
    exact(value, ["contract", "contractVersion", "state", "source", "errorCode"]);
    if (!['v1', 'v2'].includes(value.source) || typeof value.errorCode !== "string" || !value.errorCode) {
      fail("INVALID_FIELD_TYPE", { field: "error envelope" });
    }
    return deepFreeze({
      state: value.state.toUpperCase(),
      source: value.source,
      errorCode: value.errorCode,
      authority: "PYTHON_AUTHORITY",
    });
  }
  if (value.state === "legacy_v1") {
    exact(value, ["contract", "contractVersion", "state", "sourceVersion", "geometryProvenance", "payload"]);
    if (value.sourceVersion !== 1 || value.geometryProvenance !== "legacy-unknown") {
      fail("INVALID_FIELD_TYPE", { field: "legacy metadata" });
    }
    record(value.payload, "payload");
    return deepFreeze({
      state: "LEGACY_V1",
      sourceVersion: 1,
      geometryProvenance: "legacy-unknown",
      payload: immutableClone(value.payload),
      authority: "PYTHON_AUTHORITY",
      conversion: "NOT_PERFORMED",
    });
  }
  if (value.state === "v2") {
    const typed = value.mark?.type === "typed";
    exact(value, typed
      ? ["contract", "contractVersion", "state", "sourceVersion", "mark", "renderProfileSupport"]
      : ["contract", "contractVersion", "state", "sourceVersion", "mark"]);
    if (value.sourceVersion !== 2) fail("INVALID_FIELD_TYPE", { field: "sourceVersion", expected: 2 });
    const validation = validateMarkV2(value.mark);
    if (validation.state !== "VALID") fail("INVALID_FIELD_TYPE", { field: "mark", reason: validation.state });
    if (typed) {
      record(value.renderProfileSupport, "renderProfileSupport");
      exact(value.renderProfileSupport, ["state", "errorCode"]);
      if (value.renderProfileSupport.state !== validation.renderProfileSupport.state
        || value.renderProfileSupport.errorCode !== validation.renderProfileSupport.errorCode) {
        fail("INVALID_FIELD_TYPE", { field: "renderProfileSupport", reason: "profile_state_mismatch" });
      }
    }
    return deepFreeze({
      state: "V2",
      sourceVersion: 2,
      mark: validation.mark,
      renderProfileSupport: validation.renderProfileSupport,
      authority: "PYTHON_AUTHORITY",
    });
  }
  fail("INVALID_FIELD_TYPE", { field: "state", reason: "unknown_python_read_state" });
}
