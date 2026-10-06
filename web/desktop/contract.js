import { validatePythonReadEnvelope } from "../personal-mark-v2/legacy.js";

export class DesktopBridgeError extends Error {
  constructor(code = "BRIDGE_UNAVAILABLE") {
    super(code);
    this.name = "DesktopBridgeError";
    this.code = /^[A-Z][A-Z0-9_]{0,63}$/.test(code) ? code : "BRIDGE_UNAVAILABLE";
  }
}

function exact(value, keys) {
  if (!value || typeof value !== "object" || Array.isArray(value)
      || Object.getPrototypeOf(value) !== Object.prototype
      || Reflect.ownKeys(value).length !== keys.length
      || keys.some((key) => !Object.hasOwn(value, key))) throw new DesktopBridgeError("INVALID_BRIDGE_RESULT");
}

const CAPABILITIES = Object.freeze({
  personalMarkRead: true, personalMarkWrite: false, nativeTargetSelection: false,
  coreAdd: false, coreVerify: false, coreReadback: false,
  c2paPersonalMarkEmbedding: false, explorerIntegration: false,
});

export const PREVIEW_AUTHORITY = "NATIVE_PREVIEW_SESSION_ONLY";
const PREVIEW_CAPABILITIES = Object.freeze({
  personalMarkRead: false, personalMarkWrite: false, nativeTargetSelection: true,
  coreAdd: true, coreVerify: true, coreReadback: false,
  c2paPersonalMarkEmbedding: true, explorerIntegration: true,
});

function validatePreviewCapabilities(value) {
  exact(value, ["bridgeProtocolVersion", "authority", "personalMarkSchemaVersions", "renderProfiles", "capabilities"]);
  if (value.authority !== PREVIEW_AUTHORITY || value.bridgeProtocolVersion !== 1
      || !Array.isArray(value.personalMarkSchemaVersions) || value.personalMarkSchemaVersions.length !== 1
      || value.personalMarkSchemaVersions[0] !== 1 || !Array.isArray(value.renderProfiles) || value.renderProfiles.length !== 0) {
    throw new DesktopBridgeError("INCOMPATIBLE_BRIDGE");
  }
  exact(value.capabilities, Object.keys(PREVIEW_CAPABILITIES));
  for (const [name, expected] of Object.entries(PREVIEW_CAPABILITIES)) {
    if (value.capabilities[name] !== expected) throw new DesktopBridgeError("INCOMPATIBLE_BRIDGE");
  }
  return Object.freeze({ bridgeProtocolVersion: 1, authority: PREVIEW_AUTHORITY,
    personalMarkSchemaVersions: Object.freeze([1]), renderProfiles: Object.freeze([]), capabilities: PREVIEW_CAPABILITIES });
}

export function validateDesktopCapabilities(value) {
  if (value?.authority === PREVIEW_AUTHORITY) return validatePreviewCapabilities(value);
  exact(value, ["bridgeProtocolVersion", "personalMarkSchemaVersions", "renderProfiles", "capabilities"]);
  if (value.bridgeProtocolVersion !== 1
      || !Array.isArray(value.personalMarkSchemaVersions)
      || value.personalMarkSchemaVersions.length !== 2
      || value.personalMarkSchemaVersions[0] !== 1 || value.personalMarkSchemaVersions[1] !== 2
      || !Array.isArray(value.renderProfiles) || value.renderProfiles.length !== 1) {
    throw new DesktopBridgeError("INCOMPATIBLE_BRIDGE");
  }
  const profile = value.renderProfiles[0];
  exact(profile, ["id", "version", "state"]);
  if (profile.id !== "shirushi-typed" || profile.version !== 1 || profile.state !== "ASSETS_UNAVAILABLE") {
    throw new DesktopBridgeError("INCOMPATIBLE_BRIDGE");
  }
  exact(value.capabilities, Object.keys(CAPABILITIES));
  for (const [name, expected] of Object.entries(CAPABILITIES)) {
    if (value.capabilities[name] !== expected) throw new DesktopBridgeError("INCOMPATIBLE_BRIDGE");
  }
  return Object.freeze({
    bridgeProtocolVersion: 1, personalMarkSchemaVersions: Object.freeze([1, 2]),
    renderProfiles: Object.freeze([Object.freeze({ ...profile })]), capabilities: CAPABILITIES,
  });
}

export function validateDesktopMarkRead(value, authority = null) {
  if (authority === PREVIEW_AUTHORITY) {
    exact(value, ["contract", "contractVersion", "state"]);
    if (value.contract !== "shirushi-personal-mark-session" || value.contractVersion !== 1 || value.state !== "absent") {
      throw new DesktopBridgeError("INVALID_MARK_READ_RESULT");
    }
    return Object.freeze({ state: "ABSENT", source: PREVIEW_AUTHORITY });
  }
  try { return validatePythonReadEnvelope(value); }
  catch { throw new DesktopBridgeError("INVALID_MARK_READ_RESULT"); }
}
