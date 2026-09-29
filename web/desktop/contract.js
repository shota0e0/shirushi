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

export function validateDesktopCapabilities(value) {
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

export function validateDesktopMarkRead(value) {
  try { return validatePythonReadEnvelope(value); }
  catch { throw new DesktopBridgeError("INVALID_MARK_READ_RESULT"); }
}
