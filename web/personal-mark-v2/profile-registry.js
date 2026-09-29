import { deepFreeze, fail } from "./errors.js";
import { assertNativeDepth, assertWellFormedUnicode } from "./parser.js";

export const PROFILE_SUPPORT = deepFreeze({
  ASSETS_UNAVAILABLE: "ASSETS_UNAVAILABLE",
  UNSUPPORTED: "UNSUPPORTED",
});

const KNOWN_PROFILE = deepFreeze({
  id: "shirushi-typed",
  version: 1,
  state: PROFILE_SUPPORT.ASSETS_UNAVAILABLE,
  errorCode: "RENDER_ASSETS_UNAVAILABLE",
  renderReady: false,
  reason: "licensed_profile_assets_not_adopted",
});

export function resolveRenderProfile(reference) {
  assertNativeDepth(reference);
  if (!reference || typeof reference !== "object" || Array.isArray(reference)) {
    fail("INVALID_FIELD_TYPE", { field: "renderProfile", expected: "object" });
  }
  for (const field of ["id", "version"]) {
    if (!Object.prototype.hasOwnProperty.call(reference, field)) fail("MISSING_FIELD", { field: `renderProfile.${field}` });
  }
  for (const field of Object.keys(reference)) {
    if (field !== "id" && field !== "version") fail("UNKNOWN_FIELD", { field: `renderProfile.${field}` });
  }
  const id = reference.id;
  const version = reference.version;
  if (typeof id !== "string") fail("INVALID_FIELD_TYPE", { field: "renderProfile.id", expected: "string" });
  assertWellFormedUnicode(id);
  if (typeof version !== "number") {
    fail("INVALID_FIELD_TYPE", { field: "renderProfile.version", expected: "integer" });
  }
  if (!Number.isFinite(version)) fail("INVALID_NUMBER", { field: "renderProfile.version" });
  if (!Number.isInteger(version)) {
    fail("INVALID_FIELD_TYPE", { field: "renderProfile.version", expected: "integer" });
  }
  if (version < 0 || !Number.isSafeInteger(version)) {
    fail("OUT_OF_RANGE", { field: "renderProfile.version", minimum: 0, maximum: Number.MAX_SAFE_INTEGER });
  }
  if (id === KNOWN_PROFILE.id && version === KNOWN_PROFILE.version) return KNOWN_PROFILE;
  return deepFreeze({
    id,
    version,
    state: PROFILE_SUPPORT.UNSUPPORTED,
    errorCode: "UNSUPPORTED_RENDER_PROFILE",
    renderReady: false,
    reason: "profile_id_or_version_not_registered",
  });
}

export function newTypedProfileReference() {
  return deepFreeze({ id: KNOWN_PROFILE.id, version: KNOWN_PROFILE.version });
}
