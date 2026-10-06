import assert from "node:assert/strict";
import { BrowserFoundationAdapter } from "../adapters/browser-foundation-adapter.js";
import { DesktopBridgeError, validateDesktopCapabilities } from "./contract.js";
import { inspectionMotionEligible, validateInspectionResult } from "./inspection-contract.js";

let checks = 0;
const check = (value, message) => { assert.ok(value, message); ++checks; };
const copy = (value) => structuredClone(value);
const rejected = (value, message) => {
  assert.throws(
    () => validateInspectionResult(value),
    (error) => error instanceof DesktopBridgeError && error.code === "INVALID_INSPECTION_RESULT",
    message,
  );
  ++checks;
};

const complete = {
  contract: "shirushi-inspection",
  contractVersion: 1,
  source: {
    sha256: "a".repeat(64),
    size: 100,
    modifiedNs: "1700000000000000000",
    format: "png",
  },
  verification: {
    state: "COMPLETE",
    aiTrainingUse: "NOT_WANTED",
    aiInferenceUse: "NOT_WANTED",
    rights: "DETECTED",
    cawg: "DETECTED",
    c2pa: "DETECTED",
    trustmark: "DETECTED",
    trustmarkPayload: "MATCH",
    integrity: "OK",
    signature: "PREVIEW",
    durableRecovery: "NONE",
    reasonCode: null,
  },
  personalMark: { state: "NOT_CHECKED", reason: "READBACK_NOT_IMPLEMENTED" },
  successMotionEligible: true,
};

const noIntent = copy(complete);
Object.assign(noIntent.verification, {
  state: "NO_INTENT",
  aiTrainingUse: "NO_PERMISSION_INFO",
  aiInferenceUse: "NO_PERMISSION_INFO",
  rights: "NOT_DETECTED",
  cawg: "UNKNOWN",
  c2pa: "NOT_DETECTED",
  trustmark: "NOT_DETECTED",
  trustmarkPayload: "NOT_AVAILABLE",
  integrity: "UNKNOWN",
  signature: "INDETERMINATE",
  durableRecovery: "NONE",
  reasonCode: "C2PA_CLAIM_MISSING",
});
noIntent.successMotionEligible = false;

const identifierOnly = copy(noIntent);
Object.assign(identifierOnly.verification, {
  state: "IDENTIFIER_ONLY",
  trustmark: "DETECTED",
  trustmarkPayload: "VALID_UNBOUND",
  durableRecovery: "IDENTIFIER_RECOVERED",
});

const cannotVerify = copy(complete);
Object.assign(cannotVerify.verification, {
  state: "CANNOT_VERIFY",
  c2pa: "VERIFICATION_FAILED",
  trustmarkPayload: "VALID_UNBOUND",
  integrity: "VERIFICATION_FAILED",
  signature: "INDETERMINATE",
  reasonCode: "C2PA_ASSET_DATA_HASH_MISMATCH",
});
cannotVerify.successMotionEligible = false;

const malformed = copy(noIntent);
Object.assign(malformed.source, { format: "unknown" });
Object.assign(malformed.verification, {
  state: "MALFORMED_OR_UNSUPPORTED",
  cawg: "INDETERMINATE",
  c2pa: "UNKNOWN",
  trustmark: "NOT_DETECTED",
  trustmarkPayload: "NOT_AVAILABLE",
  reasonCode: "PNG_INVALID",
});

for (const [name, fixture] of Object.entries({ complete, noIntent, identifierOnly, cannotVerify, malformed })) {
  const result = validateInspectionResult(fixture);
  check(result.verification.state === fixture.verification.state, `${name} state retained`);
  check(inspectionMotionEligible(fixture) === (name === "complete"), `${name} motion is derived conservatively`);
}

const trusted = copy(complete);
trusted.verification.signature = "TRUSTED";
check(validateInspectionResult(trusted).verification.signature === "TRUSTED", "future separately validated signature trust is preserved");

for (const value of [null, undefined, false, 1, [], "inspection"]) rejected(value, "non-object result rejected");
for (const mutation of [
  (x) => { x.contractVersion = 2; },
  (x) => { x.contractVersion = "1"; },
  (x) => { x.contract = "shirushi-desktop-bridge"; },
  (x) => { x.extra = true; },
  (x) => { x.source.extra = true; },
  (x) => { x.verification.extra = true; },
  (x) => { x.personalMark.extra = true; },
  (x) => { x.source.path = "C:/private/image.png"; },
  (x) => { x.path = "C:/private/image.png"; },
  (x) => { x.verification.sourcePath = "C:/private/image.png"; },
  (x) => { x.source.sha256 = "A".repeat(64); },
  (x) => { x.source.sha256 = "a".repeat(63); },
  ...["\n", "\r", "\u2028", "\u2029"].map((ending) => (x) => { x.source.sha256 = "a".repeat(64) + ending; }),
  (x) => { delete x.source.sha256; },
  (x) => { x.source.size = 0; },
  (x) => { x.source.size = 64 * 1024 * 1024 + 1; },
  (x) => { x.source.size = Number.NaN; },
  (x) => { x.source.size = Number.POSITIVE_INFINITY; },
  (x) => { x.source.size = 1.5; },
  (x) => { x.source.modifiedNs = 0; },
  (x) => { x.source.modifiedNs = "00"; },
  (x) => { x.source.modifiedNs = "01"; },
  (x) => { x.source.modifiedNs = "-1"; },
  (x) => { x.source.modifiedNs = "1".repeat(21); },
  ...["\n", "\r", "\u2028", "\u2029"].map((ending) => (x) => { x.source.modifiedNs = "1" + ending; }),
  (x) => { x.source.format = "jpg"; },
  (x) => { x.source.format = "unknown"; },
  (x) => { x.verification.state = "SUCCESS"; },
  (x) => { x.verification.integrity = "ok"; },
  (x) => { x.verification.reasonCode = "FUTURE_UNKNOWN_CODE"; },
]) {
  const bad = copy(complete);
  mutation(bad);
  rejected(bad, "malformed, unsupported, unknown, or path-bearing field rejected");
}

for (const fixture of [noIntent, identifierOnly]) {
  const bad = copy(fixture);
  bad.source.format = "unknown";
  rejected(bad, "normal semantic summaries require an identified PNG or JPEG source");
}

for (const mutation of [
  (x) => { x.verification.state = "CANNOT_VERIFY"; },
  (x) => { x.verification.state = "MALFORMED_OR_UNSUPPORTED"; },
]) {
  const bad = copy(noIntent);
  mutation(bad);
  rejected(bad, "caller-selected label cannot override the derived NO_INTENT state");
}
const malformedAsCannot = copy(malformed);
malformedAsCannot.verification.state = "CANNOT_VERIFY";
rejected(malformedAsCannot, "a malformed reason cannot be relabeled CANNOT_VERIFY");
const nonMalformedAsMalformed = copy(cannotVerify);
nonMalformedAsMalformed.verification.state = "MALFORMED_OR_UNSUPPORTED";
rejected(nonMalformedAsMalformed, "a non-malformed reason cannot be relabeled malformed or unsupported");
const internalErrorAsMalformed = copy(malformed);
internalErrorAsMalformed.verification.reasonCode = "INTERNAL_ERROR";
rejected(internalErrorAsMalformed, "MALFORMED_OR_UNSUPPORTED requires the bounded malformed reason set");

const mtimeZero = copy(complete);
mtimeZero.source.modifiedNs = "0";
check(validateInspectionResult(mtimeZero).source.modifiedNs === "0", "canonical zero mtime remains valid text");
const maxSize = copy(complete);
maxSize.source.size = 64 * 1024 * 1024;
check(validateInspectionResult(maxSize).source.size === 64 * 1024 * 1024, "64 MiB boundary accepted");

for (const mutation of [
  (x) => { x.successMotionEligible = false; },
  (x) => { x.verification.integrity = "VERIFICATION_FAILED"; x.verification.reasonCode = "C2PA_VALIDATION_FAILED"; },
  (x) => { x.verification.rights = "NOT_DETECTED"; },
  (x) => { x.verification.trustmarkPayload = "VALID_UNBOUND"; },
  (x) => { x.verification.reasonCode = "C2PA_CLAIM_MISSING"; },
]) {
  const bad = copy(complete);
  mutation(bad);
  rejected(bad, "false success or contradictory COMPLETE rejected");
}

for (const mutation of [
  (x) => { x.successMotionEligible = true; },
  (x) => { x.verification.state = "COMPLETE"; x.successMotionEligible = true; },
  (x) => { x.verification.durableRecovery = "IDENTIFIER_RECOVERED"; },
  (x) => { x.verification.trustmarkPayload = "MATCH"; },
  (x) => { x.verification.reasonCode = null; },
  (x) => { x.verification.c2pa = "NOT_DETECTED"; },
]) {
  const bad = copy(cannotVerify);
  mutation(bad);
  rejected(bad, "standalone success flag or inconsistent failure dimensions rejected");
}

for (const personalMark of [
  { state: "ABSENT", reason: "READBACK_NOT_IMPLEMENTED" },
  { state: "MALFORMED", reason: "READBACK_NOT_IMPLEMENTED" },
  { state: "UNSUPPORTED", reason: "READBACK_NOT_IMPLEMENTED" },
  { state: "NOT_CHECKED", reason: "UNAVAILABLE" },
  { state: "NOT_CHECKED", reason: "READBACK_NOT_IMPLEMENTED", mark: {} },
  { state: "NOT_CHECKED", reason: "READBACK_NOT_IMPLEMENTED", profile: { id: "shirushi-typed" } },
]) {
  const bad = copy(complete);
  bad.personalMark = personalMark;
  rejected(bad, "Personal Mark placeholder/profile substitution rejected");
}

const original = copy(complete);
const validated = validateInspectionResult(original);
check(Object.isFrozen(validated)
  && Object.isFrozen(validated.source)
  && Object.isFrozen(validated.verification)
  && Object.isFrozen(validated.personalMark), "validated result is deeply immutable");
original.source.sha256 = "b".repeat(64);
original.verification.state = "CANNOT_VERIFY";
original.personalMark.reason = "CHANGED";
check(validated.source.sha256 === "a".repeat(64)
  && validated.verification.state === "COMPLETE"
  && validated.personalMark.reason === "READBACK_NOT_IMPLEMENTED", "validated result is detached from mutable input");

const desktopCaps = validateDesktopCapabilities({
  bridgeProtocolVersion: 1,
  personalMarkSchemaVersions: [1, 2],
  renderProfiles: [{ id: "shirushi-typed", version: 1, state: "ASSETS_UNAVAILABLE" }],
  capabilities: {
    personalMarkRead: true,
    personalMarkWrite: false,
    nativeTargetSelection: false,
    coreAdd: false,
    coreVerify: false,
    coreReadback: false,
    c2paPersonalMarkEmbedding: false,
    explorerIntegration: false,
  },
});
check(!desktopCaps.capabilities.nativeTargetSelection && !desktopCaps.capabilities.coreVerify,
  "Desktop actual selection and Verify remain disabled");
const browserCaps = await new BrowserFoundationAdapter().getCapabilities();
check(!browserCaps.coreVerify && !browserCaps.previewVerifyMotion, "Browser actual and preview Verify remain disabled");

console.log(`PASS inspection result contract: ${checks} checks`);
