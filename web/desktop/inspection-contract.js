import { DesktopBridgeError } from "./contract.js";

const MAX_SOURCE_SIZE = 64 * 1024 * 1024;
const SOURCE_FORMATS = new Set(["png", "jpeg", "unknown"]);
const STATES = new Set([
  "COMPLETE",
  "NO_INTENT",
  "IDENTIFIER_ONLY",
  "CANNOT_VERIFY",
  "MALFORMED_OR_UNSUPPORTED",
]);
const USE_STATES = new Set(["NOT_WANTED", "NO_PERMISSION_INFO", "INDETERMINATE"]);
const RIGHTS_STATES = new Set(["DETECTED", "NOT_DETECTED", "PARTIAL"]);
const CAWG_STATES = new Set(["DETECTED", "NOT_DETECTED", "VERIFICATION_FAILED", "INDETERMINATE", "UNKNOWN"]);
const C2PA_STATES = new Set(["DETECTED", "NOT_DETECTED", "VERIFICATION_FAILED", "UNKNOWN"]);
const TRUSTMARK_STATES = new Set(["DETECTED", "NOT_DETECTED", "DECODE_FAILED", "UNKNOWN"]);
const TRUSTMARK_PAYLOAD_STATES = new Set(["MATCH", "NOT_AVAILABLE", "INVALID", "MISMATCH", "UNKNOWN", "VALID_UNBOUND"]);
const INTEGRITY_STATES = new Set(["OK", "VERIFICATION_FAILED", "UNKNOWN"]);
const SIGNATURE_STATES = new Set(["PREVIEW", "TRUSTED", "INDETERMINATE"]);
const RECOVERY_STATES = new Set(["NONE", "IDENTIFIER_RECOVERED"]);

// This is the fixed creator_verify reason vocabulary. Unknown diagnostic text
// must not acquire meaning merely because it resembles an uppercase code.
const REASON_CODES = new Set([
  "INTERNAL_ERROR",
  "FILE_NOT_FOUND",
  "PNG_INVALID",
  "PNG_DIMENSIONS_MISMATCH",
  "JPEG_INVALID",
  "JPEG_DIMENSIONS_MISMATCH",
  "C2PA_CLAIM_MISSING",
  "C2PA_MANIFEST_UNREADABLE",
  "C2PA_CLAIM_REFERENCE_MISMATCH",
  "C2PA_ASSERTION_DIGEST_MISMATCH",
  "C2PA_ASSET_DATA_HASH_MISMATCH",
  "C2PA_VALIDATION_FAILED",
  "C2PA_RIGHTS_ASSERTION_MISSING",
  "C2PA_RIGHTS_VALUE_MISMATCH",
  "TRUSTMARK_NOT_DETECTED",
  "TRUSTMARK_SCHEMA_MISMATCH",
  "TRUSTMARK_PAYLOAD_LENGTH_MISMATCH",
  "TRUSTMARK_PAYLOAD_MISMATCH",
  "SOFT_BINDING_MISSING",
  "SOFT_BINDING_ALGORITHM_MISMATCH",
  "SOFT_BINDING_VALUE_MISMATCH",
  "SIGNATURE_MISSING",
  "SIGNATURE_INVALID",
  "SIGNATURE_VERIFIER_UNAVAILABLE",
  "SIGNATURE_VERIFICATION_ERROR",
  "C2PATOOL_MISSING",
  "C2PATOOL_INTEGRITY_FAILED",
]);

const MALFORMED_REASONS = new Set([
  "PNG_INVALID",
  "PNG_DIMENSIONS_MISMATCH",
  "JPEG_INVALID",
  "JPEG_DIMENSIONS_MISMATCH",
]);

const fail = () => { throw new DesktopBridgeError("INVALID_INSPECTION_RESULT"); };

function exact(value, keys) {
  if (!value || typeof value !== "object" || Array.isArray(value)
      || Object.getPrototypeOf(value) !== Object.prototype
      || Reflect.ownKeys(value).length !== keys.length
      || keys.some((key) => !Object.hasOwn(value, key))) fail();
}

function member(value, allowed) {
  if (typeof value !== "string" || !allowed.has(value)) fail();
}

function completeDimensions(value) {
  return value.aiTrainingUse === "NOT_WANTED"
    && value.aiInferenceUse === "NOT_WANTED"
    && value.rights === "DETECTED"
    && value.cawg === "DETECTED"
    && value.c2pa === "DETECTED"
    && value.trustmark === "DETECTED"
    && value.trustmarkPayload === "MATCH"
    && value.integrity === "OK"
    && (value.signature === "PREVIEW" || value.signature === "TRUSTED")
    && value.durableRecovery === "NONE"
    && value.reasonCode === null;
}

function noIntentDimensions(value) {
  return value.aiTrainingUse === "NO_PERMISSION_INFO"
    && value.aiInferenceUse === "NO_PERMISSION_INFO"
    && value.rights === "NOT_DETECTED"
    && value.cawg === "UNKNOWN"
    && value.c2pa === "NOT_DETECTED"
    && value.trustmark === "NOT_DETECTED"
    && value.trustmarkPayload === "NOT_AVAILABLE"
    && value.integrity === "UNKNOWN"
    && value.signature === "INDETERMINATE"
    && value.durableRecovery === "NONE"
    && value.reasonCode === "C2PA_CLAIM_MISSING";
}

function identifierOnlyDimensions(value) {
  return value.aiTrainingUse === "NO_PERMISSION_INFO"
    && value.aiInferenceUse === "NO_PERMISSION_INFO"
    && value.rights === "NOT_DETECTED"
    && value.cawg === "UNKNOWN"
    && value.c2pa === "NOT_DETECTED"
    && value.trustmark === "DETECTED"
    && value.trustmarkPayload === "VALID_UNBOUND"
    && value.integrity === "UNKNOWN"
    && value.signature === "INDETERMINATE"
    && value.durableRecovery === "IDENTIFIER_RECOVERED"
    && value.reasonCode === "C2PA_CLAIM_MISSING";
}

function dimensionsAreConsistent(value) {
  const expectedUse = value.rights === "DETECTED" ? "NOT_WANTED"
    : value.rights === "NOT_DETECTED" ? "NO_PERMISSION_INFO" : "INDETERMINATE";
  if (value.aiTrainingUse !== expectedUse || value.aiInferenceUse !== expectedUse) return false;

  const payloadAllowed = value.trustmark === "DETECTED"
    ? new Set(["MATCH", "MISMATCH", "VALID_UNBOUND"])
    : value.trustmark === "NOT_DETECTED"
      ? new Set(["NOT_AVAILABLE"])
      : value.trustmark === "DECODE_FAILED"
        ? new Set(["INVALID", "UNKNOWN"])
        : new Set(["UNKNOWN"]);
  if (!payloadAllowed.has(value.trustmarkPayload)) return false;
  if (value.c2pa === "NOT_DETECTED" && value.integrity !== "UNKNOWN") return false;

  const recovered = value.durableRecovery === "IDENTIFIER_RECOVERED";
  if (recovered !== (value.c2pa === "NOT_DETECTED"
      && value.trustmark === "DETECTED"
      && value.trustmarkPayload === "VALID_UNBOUND")) return false;

  if (value.reasonCode === null && !completeDimensions(value)) return false;
  if (value.integrity === "OK" && !completeDimensions(value)) return false;
  if (value.trustmarkPayload === "MATCH" && !completeDimensions(value)) return false;
  return true;
}

function derivedState(value) {
  if (completeDimensions(value)) return "COMPLETE";
  if (noIntentDimensions(value)) return "NO_INTENT";
  if (identifierOnlyDimensions(value)) return "IDENTIFIER_ONLY";
  if (MALFORMED_REASONS.has(value.reasonCode)) return "MALFORMED_OR_UNSUPPORTED";
  return "CANNOT_VERIFY";
}

/**
 * Validate inspection result contract version 1. This version is independent
 * of, and must never be interpreted as, the Desktop bridge protocol version.
 */
export function validateInspectionResult(value) {
  exact(value, ["contract", "contractVersion", "source", "verification", "personalMark", "successMotionEligible"]);
  if (value.contract !== "shirushi-inspection" || value.contractVersion !== 1
      || typeof value.successMotionEligible !== "boolean") fail();

  exact(value.source, ["sha256", "size", "modifiedNs", "format"]);
  if (typeof value.source.sha256 !== "string" || value.source.sha256.length !== 64
      || !/^[0-9a-f]{64}(?![\s\S])/.test(value.source.sha256)
      || !Number.isSafeInteger(value.source.size) || value.source.size <= 0 || value.source.size > MAX_SOURCE_SIZE
      || typeof value.source.modifiedNs !== "string" || value.source.modifiedNs.length > 20
      || !/^(?:0|[1-9][0-9]*)(?![\s\S])/.test(value.source.modifiedNs)) fail();
  member(value.source.format, SOURCE_FORMATS);

  const verificationKeys = [
    "state", "aiTrainingUse", "aiInferenceUse", "rights", "cawg", "c2pa",
    "trustmark", "trustmarkPayload", "integrity", "signature", "durableRecovery", "reasonCode",
  ];
  exact(value.verification, verificationKeys);
  member(value.verification.state, STATES);
  member(value.verification.aiTrainingUse, USE_STATES);
  member(value.verification.aiInferenceUse, USE_STATES);
  member(value.verification.rights, RIGHTS_STATES);
  member(value.verification.cawg, CAWG_STATES);
  member(value.verification.c2pa, C2PA_STATES);
  member(value.verification.trustmark, TRUSTMARK_STATES);
  member(value.verification.trustmarkPayload, TRUSTMARK_PAYLOAD_STATES);
  member(value.verification.integrity, INTEGRITY_STATES);
  member(value.verification.signature, SIGNATURE_STATES);
  member(value.verification.durableRecovery, RECOVERY_STATES);
  if (value.verification.reasonCode !== null) member(value.verification.reasonCode, REASON_CODES);

  exact(value.personalMark, ["state", "reason"]);
  if (value.personalMark.state !== "NOT_CHECKED"
      || value.personalMark.reason !== "READBACK_NOT_IMPLEMENTED") fail();

  if (!dimensionsAreConsistent(value.verification)) fail();
  const state = derivedState(value.verification);
  const successMotionEligible = state === "COMPLETE";
  if (value.verification.state !== state || value.successMotionEligible !== successMotionEligible
      || (value.source.format === "unknown"
        && (state === "COMPLETE" || state === "NO_INTENT" || state === "IDENTIFIER_ONLY"))) fail();

  return Object.freeze({
    contract: "shirushi-inspection",
    contractVersion: 1,
    source: Object.freeze({
      sha256: value.source.sha256,
      size: value.source.size,
      modifiedNs: value.source.modifiedNs,
      format: value.source.format,
    }),
    verification: Object.freeze(Object.fromEntries(verificationKeys.map((key) => [key, value.verification[key]]))),
    personalMark: Object.freeze({ state: "NOT_CHECKED", reason: "READBACK_NOT_IMPLEMENTED" }),
    successMotionEligible,
  });
}

export function inspectionMotionEligible(value) {
  return validateInspectionResult(value).successMotionEligible;
}
