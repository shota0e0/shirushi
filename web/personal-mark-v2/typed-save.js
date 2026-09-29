import { deepFreeze, fail } from "./errors.js";
import { validateMarkV2, validateTypedText } from "./contract.js";
import { MAX_LOCAL_BYTES, utf8ByteLengthStrict } from "./parser.js";
import { newTypedProfileReference } from "./profile-registry.js";
import { hasProperty, normalizeNfc } from "./unicode16.js";

function scalarEntries(text) {
  return [...text].map((character, index) => deepFreeze({
    index,
    character,
    codePoint: character.codePointAt(0),
    label: `U+${character.codePointAt(0).toString(16).toUpperCase().padStart(4, "0")}`,
  }));
}

function codePointDiff(original, candidate) {
  const left = scalarEntries(original);
  const right = scalarEntries(candidate);
  let prefix = 0;
  while (prefix < left.length && prefix < right.length && left[prefix].codePoint === right[prefix].codePoint) prefix += 1;
  let suffix = 0;
  while (suffix < left.length - prefix && suffix < right.length - prefix
    && left[left.length - 1 - suffix].codePoint === right[right.length - 1 - suffix].codePoint) suffix += 1;
  return deepFreeze({
    commonPrefixScalars: prefix,
    commonSuffixScalars: suffix,
    original: left.slice(prefix, suffix === 0 ? left.length : left.length - suffix),
    candidate: right.slice(prefix, suffix === 0 ? right.length : right.length - suffix),
  });
}

function specialWarnings(text) {
  return scalarEntries(text)
    .filter(({ codePoint }) => codePoint === 0x200c || codePoint === 0x200d || hasProperty(codePoint, "variation_selector"))
    .map((entry) => deepFreeze({
      ...entry,
      kind: entry.codePoint === 0x200c ? "ZWNJ" : entry.codePoint === 0x200d ? "ZWJ" : "VARIATION_SELECTOR",
    }));
}

export function prepareTypedSave(text, renderProfile = newTypedProfileReference()) {
  if (typeof text !== "string") fail("INVALID_FIELD_TYPE", { field: "$.text", expected: "string" });
  // Resource ceiling before Unicode property scans/NFC. This does not replace
  // the final 128-scalar / 512-byte semantic limits and never truncates input.
  if (text.length > MAX_LOCAL_BYTES) {
    fail("PAYLOAD_TOO_LARGE", { maximum: MAX_LOCAL_BYTES, actualCodeUnits: text.length });
  }
  const rawDraftBytes = utf8ByteLengthStrict(text);
  if (rawDraftBytes > MAX_LOCAL_BYTES) {
    fail("PAYLOAD_TOO_LARGE", { maximum: MAX_LOCAL_BYTES, actual: rawDraftBytes });
  }
  // Draft policy is checked without requiring NFC or applying final size bounds.
  validateTypedText(text, { requireNfc: false, enforceSize: false });
  const normalizedCandidate = normalizeNfc(text);
  validateTypedText(normalizedCandidate, { requireNfc: true, enforceSize: true });
  const differenceExists = normalizedCandidate !== text;
  const warnings = specialWarnings(normalizedCandidate);
  const result = validateMarkV2({ version: 2, type: "typed", text: normalizedCandidate, renderProfile });
  if (result.state !== "VALID") fail("UNKNOWN_SCHEMA_VERSION");
  return deepFreeze({
    kind: "TYPED_SAVE_PREPARATION",
    original: text,
    normalizedCandidate,
    differenceExists,
    difference: differenceExists ? codePointDiff(text, normalizedCandidate) : null,
    warnings,
    requiresNormalizationConfirmation: differenceExists,
    requiresSpecialCharacterConfirmation: warnings.length > 0,
    renderProfile: result.mark.renderProfile,
    renderProfileSupport: result.renderProfileSupport,
  });
}

export function confirmTypedSave(preparation, {
  acceptNormalization = false,
  acceptSpecialCharacters = false,
} = {}) {
  if (!preparation || preparation.kind !== "TYPED_SAVE_PREPARATION" || typeof preparation.original !== "string") {
    fail("INVALID_FIELD_TYPE", { field: "preparation", expected: "TYPED_SAVE_PREPARATION" });
  }
  // Recompute so a caller cannot forge a confirmation payload or reuse stale derived fields.
  const current = prepareTypedSave(preparation.original, preparation.renderProfile);
  if (current.renderProfileSupport.state === "UNSUPPORTED") fail("UNSUPPORTED_RENDER_PROFILE");
  if (current.differenceExists && acceptNormalization !== true) fail("NORMALIZATION_CONFIRMATION_REQUIRED");
  if (current.warnings.length > 0 && acceptSpecialCharacters !== true) fail("SPECIAL_CHARACTER_CONFIRMATION_REQUIRED");
  const result = validateMarkV2({
    version: 2,
    type: "typed",
    text: current.normalizedCandidate,
    renderProfile: current.renderProfile,
  });
  return result.mark;
}
