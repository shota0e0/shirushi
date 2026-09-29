import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, extname, join, normalize, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  assessEmbeddingReadiness,
  centerFitTransform,
  confirmTypedSave,
  createCapturePlane,
  encodeMarkV2,
  inverseMapPoint,
  mapNormalizedPoint,
  MAX_LOCAL_BYTES,
  parseStrictJson,
  parseMarkV2,
  prepareTypedSave,
  resolveRenderProfile,
  UNICODE_VERSION,
  validateMarkV2,
  validatePythonReadEnvelope,
} from "./index.js";

const here = dirname(fileURLToPath(import.meta.url));
let checks = 0;
const check = (condition, message) => { assert.ok(condition, message); checks += 1; };
const equal = (actual, expected, message) => { assert.deepEqual(actual, expected, message); checks += 1; };
const errorCode = (operation, code, message = code) => {
  assert.throws(operation, (error) => error?.code === code, message);
  checks += 1;
};

const typed = {
  version: 2,
  type: "typed",
  text: "森 / Mori",
  renderProfile: { id: "shirushi-typed", version: 1 },
};
const handwritten = {
  version: 2,
  type: "handwritten",
  coordinateSpace: { width: 480, height: 220 },
  strokes: [{ points: [{ x: 0.12, y: 0.43, t: 0 }, { x: 0.14, y: 0.42, t: 16 }] }],
};

const typedResult = validateMarkV2(typed);
check(typedResult.state === "VALID" && typedResult.mark.type === "typed", "Typed v2 validates");
check(typedResult.renderProfileSupport.state === "ASSETS_UNAVAILABLE", "known profile is recognized but not render-ready");
check(typedResult.renderProfileSupport.renderReady === false, "known profile never falls back to a system font");
check(typedResult.unicodeVersion === "16.0.0" && UNICODE_VERSION === "16.0.0", "validation reports pinned Unicode 16");
const handwrittenResult = validateMarkV2(handwritten);
check(handwrittenResult.state === "VALID" && handwrittenResult.mark.type === "handwritten", "Handwritten v2 validates");

const sourceBefore = JSON.stringify(handwritten);
check(Object.isFrozen(handwrittenResult) && Object.isFrozen(handwrittenResult.mark)
  && Object.isFrozen(handwrittenResult.mark.strokes[0].points[0]), "validated result is deeply frozen");
check(JSON.stringify(handwritten) === sourceBefore && !Object.isFrozen(handwritten), "validation does not mutate or freeze its source");
const encoded = encodeMarkV2(handwritten);
equal(parseMarkV2(encoded).mark, handwrittenResult.mark, "Uint8Array encode/parse roundtrip is exact");
equal(parseMarkV2(new TextDecoder().decode(encoded)).mark, handwrittenResult.mark, "string parse roundtrip is exact");
equal([...encodeMarkV2(parseMarkV2(encoded).mark)], [...encoded], "explicit new-write bytes are deterministic");

check(validateMarkV2({ version: 3, type: "typed" }).state === "UNSUPPORTED_VERSION", "unknown schema version is explicit");
check(validateMarkV2({ version: 2, type: "future" }).state === "UNSUPPORTED_TYPE", "unknown type is explicit");
const unknownProfile = validateMarkV2({ ...typed, renderProfile: { id: "future-profile", version: 7 } });
check(unknownProfile.state === "VALID" && unknownProfile.renderProfileSupport.state === "UNSUPPORTED", "unknown profile remains parse-valid but render-unsupported");
check(unknownProfile.renderProfileSupport.errorCode === "UNSUPPORTED_RENDER_PROFILE", "unknown profile carries stable error code");
check(validateMarkV2({ ...typed, renderProfile: { id: "shirushi-typed", version: 2 } }).renderProfileSupport.state === "UNSUPPORTED", "profile version is separate from schema version");
check(resolveRenderProfile({ id: "shirushi-typed", version: 1 }).errorCode === "RENDER_ASSETS_UNAVAILABLE", "registry exposes missing licensed assets");
errorCode(() => resolveRenderProfile({ id: "bad\ud800", version: 1 }), "MALFORMED_UNICODE", "direct registry calls reject malformed Unicode profile ids");
errorCode(() => resolveRenderProfile({ id: "shirushi-typed", version: 1, extra: true }), "UNKNOWN_FIELD", "public registry enforces the exact reference shape");
errorCode(() => validateMarkV2({ ...typed, renderProfile: { id: "shirushi-typed", version: -1 } }), "OUT_OF_RANGE", "negative profile versions are outside the interoperable domain");
const maxProfileVersion = validateMarkV2({ ...typed, renderProfile: { id: "shirushi-typed", version: Number.MAX_SAFE_INTEGER } });
check(maxProfileVersion.state === "VALID" && maxProfileVersion.renderProfileSupport.state === "UNSUPPORTED", "2^53-1 profile version is structurally valid but unregistered");
errorCode(() => validateMarkV2({ ...typed, renderProfile: { id: "shirushi-typed", version: 2 ** 53 } }), "OUT_OF_RANGE", "2^53 profile version is outside the interoperable domain");
errorCode(() => validateMarkV2({ ...typed, renderProfile: { id: "shirushi-typed", version: Infinity } }), "INVALID_NUMBER", "profile version must be finite");
errorCode(() => validateMarkV2({ ...typed, renderProfile: { id: "bad\ud800", version: 1 } }), "MALFORMED_UNICODE", "native values reject malformed Unicode outside text too");

errorCode(() => parseMarkV2('{"version":2,"type":"typed","text":"a","\\u0074ext":"b","renderProfile":{"id":"shirushi-typed","version":1}}'), "DUPLICATE_KEY", "decoded duplicate root key is rejected");
errorCode(() => parseMarkV2('{"version":2,"type":"typed","text":"a","renderProfile":{"id":"shirushi-typed","version":1,"\\u0069d":"x"}}'), "DUPLICATE_KEY", "decoded duplicate nested key is rejected");
check(Array.isArray(parseStrictJson(`{"a":${"[".repeat(7)}0${"]".repeat(7)}}`).a), "depth 8 boundary is accepted by the bounded parser");
errorCode(() => parseMarkV2(`{"version":2,"type":"typed","x":${"[".repeat(8)}0${"]".repeat(8)}}`), "MAX_DEPTH_EXCEEDED");
errorCode(() => parseMarkV2(new Uint8Array(MAX_LOCAL_BYTES + 1)), "PAYLOAD_TOO_LARGE");
const minimal = JSON.stringify(typed);
const exactCeiling = minimal + " ".repeat(MAX_LOCAL_BYTES - new TextEncoder().encode(minimal).byteLength);
check(parseMarkV2(exactCeiling).state === "VALID", "exact 2 MiB raw boundary is accepted");
errorCode(() => parseMarkV2(exactCeiling + " "), "PAYLOAD_TOO_LARGE");
errorCode(() => parseMarkV2(`\ufeff${minimal}`), "TRANSPORT_BOM");
errorCode(() => parseMarkV2(Uint8Array.of(0xef, 0xbb, 0xbf, 0x7b, 0x7d)), "TRANSPORT_BOM");
errorCode(() => parseMarkV2(Uint8Array.of(0xc3, 0x28)), "INVALID_UTF8");
errorCode(() => parseMarkV2('{"version":2,"type":"typed","text":"\\ud800","renderProfile":{"id":"shirushi-typed","version":1}}'), "MALFORMED_UNICODE");
errorCode(() => parseMarkV2(`{"version":2,"type":"typed","text":"a","renderProfile":{"id":"shirushi-typed","version":1},"n":1e400}`), "INVALID_NUMBER");
errorCode(() => parseMarkV2(`{"version":2,"type":"handwritten","coordinateSpace":{"width":1,"height":1},"strokes":[{"points":[{"x":1e-999,"y":0,"t":0}]}]}`), "INVALID_NUMBER", "nonzero numeric underflow cannot silently become zero");
check(parseMarkV2(`{"version":2,"type":"handwritten","coordinateSpace":{"width":1,"height":1},"strokes":[{"points":[{"x":0e-999,"y":0,"t":0}]}]}`).state === "VALID", "lexical zero remains a valid zero across an exponent");
errorCode(() => parseMarkV2(`{"version":2,"type":"typed","text":"a","renderProfile":{"id":"shirushi-typed","version":1},"n":NaN}`), "MALFORMED_JSON");

for (const changed of [
  { ...typed, extra: 1 },
  { ...typed, renderProfile: { ...typed.renderProfile, extra: 1 } },
  { ...handwritten, coordinateSpace: { ...handwritten.coordinateSpace, extra: 1 } },
  { ...handwritten, strokes: [{ points: handwritten.strokes[0].points, extra: 1 }] },
  { ...handwritten, strokes: [{ points: [{ ...handwritten.strokes[0].points[0], extra: 1 }] }] },
]) errorCode(() => validateMarkV2(changed), "UNKNOWN_FIELD", "unknown fields fail at every supported-v2 level");

for (const changed of [
  { ...handwritten, coordinateSpace: { width: true, height: 220 } },
  { ...handwritten, strokes: [{ points: [{ x: true, y: 0, t: 0 }] }] },
  { ...handwritten, strokes: [{ points: [{ x: 0, y: false, t: 0 }] }] },
  { ...handwritten, strokes: [{ points: [{ x: 0, y: 0, t: true }] }] },
]) errorCode(() => validateMarkV2(changed), "INVALID_FIELD_TYPE", "booleans are not numbers");
for (const numeric of [NaN, Infinity, -Infinity]) {
  errorCode(() => validateMarkV2({ ...handwritten, strokes: [{ points: [{ x: numeric, y: 0, t: 0 }] }] }), "INVALID_NUMBER");
}
errorCode(() => validateMarkV2({ ...handwritten, coordinateSpace: { width: 0, height: 220 } }), "OUT_OF_RANGE");
errorCode(() => validateMarkV2({ ...handwritten, coordinateSpace: { width: 17, height: 1 } }), "OUT_OF_RANGE");
check(validateMarkV2({ ...handwritten, coordinateSpace: { width: 16, height: 1 } }).state === "VALID", "maximum aspect boundary is inclusive");
check(validateMarkV2({ ...handwritten, coordinateSpace: { width: 1, height: 16 } }).state === "VALID", "minimum aspect boundary is inclusive");
errorCode(() => validateMarkV2({ ...handwritten, strokes: [] }), "LIMIT_EXCEEDED");
errorCode(() => validateMarkV2({ ...handwritten, strokes: [{ points: [] }] }), "LIMIT_EXCEEDED");
check(validateMarkV2({ ...handwritten, strokes: [{ points: [{ x: 0.5, y: 0.5, t: 0 }] }] }).state === "VALID", "one-point dot is valid");
check(validateMarkV2({ ...handwritten, strokes: Array.from({ length: 128 }, () => ({ points: [{ x: 0, y: 0, t: 0 }] })) }).state === "VALID", "128-stroke boundary is inclusive");
errorCode(() => validateMarkV2({ ...handwritten, strokes: Array.from({ length: 129 }, () => ({ points: [{ x: 0, y: 0, t: 0 }] })) }), "LIMIT_EXCEEDED");
errorCode(() => validateMarkV2({ ...handwritten, strokes: [{ points: Array.from({ length: 4097 }, () => ({ x: 0, y: 0, t: 0 })) }] }), "LIMIT_EXCEEDED");
errorCode(() => validateMarkV2({ ...handwritten, strokes: Array.from({ length: 5 }, () => ({ points: Array.from({ length: 4096 }, () => ({ x: 0, y: 0, t: 0 })) })) }), "LIMIT_EXCEEDED", "total point ceiling is independent of per-stroke ceiling");
const rawPoint = '{"x":0,"y":0,"t":0}';
const rawStroke = (count) => `{"points":[${Array.from({ length: count }, () => rawPoint).join(",")}]}`;
const rawAtPointLimit = `{"version":2,"type":"handwritten","coordinateSpace":{"width":480,"height":220},"strokes":[${Array.from({ length: 5 }, () => rawStroke(4000)).join(",")}]}`;
const parseStart = performance.now();
check(parseMarkV2(rawAtPointLimit).mark.strokes.length === 5, "raw 20,000-point payload parses at the exact local point ceiling");
check(performance.now() - parseStart < 10000, "raw 20,000-point parsing completes within a generous bounded-runtime check");
const rawOverPointLimit = `{"version":2,"type":"handwritten","coordinateSpace":{"width":480,"height":220},"strokes":[${[rawStroke(4000), rawStroke(4000), rawStroke(4000), rawStroke(4000), rawStroke(4001)].join(",")}]}`;
errorCode(() => parseMarkV2(rawOverPointLimit), "LIMIT_EXCEEDED", "raw 20,001-point payload is rejected without simplification");
errorCode(() => validateMarkV2({ ...handwritten, strokes: [{ points: [{ x: 0, y: 0, t: 1 }] }] }), "TIMING_INVALID");
errorCode(() => validateMarkV2({ ...handwritten, strokes: [{ points: [{ x: 0, y: 0, t: 0 }, { x: 0, y: 0, t: 3 }, { x: 0, y: 0, t: 2 }] }] }), "TIMING_INVALID");
errorCode(() => validateMarkV2({ ...handwritten, strokes: [{ points: [{ x: 0, y: 0, t: 0 }, { x: 0, y: 0, t: 120001 }] }] }), "OUT_OF_RANGE");
check(validateMarkV2({ ...handwritten, strokes: [{ points: [{ x: 0, y: 0, t: 0 }, { x: 1, y: 1, t: 120000 }] }] }).state === "VALID", "coordinate and duration maxima are inclusive");

for (const text of [" a", "a ", "a\n", "a\u0085", "a\u2028b"]) {
  errorCode(() => prepareTypedSave(text), text.startsWith(" ") || text.endsWith(" ") ? "TEXT_EDGE_WHITESPACE" : "TEXT_CONTROL");
}
errorCode(() => prepareTypedSave("a".repeat(MAX_LOCAL_BYTES + 1)), "PAYLOAD_TOO_LARGE", "oversized raw draft is rejected before normalization without truncation");
errorCode(() => prepareTypedSave("森".repeat(Math.floor(MAX_LOCAL_BYTES / 3) + 1)), "PAYLOAD_TOO_LARGE", "raw draft resource ceiling counts strict UTF-8 bytes, not only code units");
errorCode(() => prepareTypedSave("a\u202Eb"), "TEXT_BIDI_CONTROL");
errorCode(() => prepareTypedSave("a\u200Bb"), "TEXT_FORBIDDEN_INVISIBLE");
errorCode(() => prepareTypedSave("\u0301"), "TEXT_VISIBLE_BASE_REQUIRED");
errorCode(() => prepareTypedSave("a".repeat(129)), "TEXT_TOO_LONG");
check(prepareTypedSave("😀".repeat(128)).normalizedCandidate.length === 256, "128-scalar / 512-byte boundary is accepted");
const normalization = prepareTypedSave("Cafe\u0301");
check(normalization.original === "Cafe\u0301" && normalization.normalizedCandidate === "Café" && normalization.differenceExists, "NFC preparation returns original and candidate without rewriting source");
check(normalization.difference.original.length > 0 && normalization.difference.candidate.length > 0, "NFC preparation exposes a code-point diff");
errorCode(() => confirmTypedSave(normalization), "NORMALIZATION_CONFIRMATION_REQUIRED");
check(confirmTypedSave(normalization, { acceptNormalization: true }).text === "Café", "explicit NFC confirmation returns the normalized saved mark");
const unsupportedSaveProfile = prepareTypedSave("森", { id: "future-profile", version: 1 });
check(unsupportedSaveProfile.renderProfileSupport.state === "UNSUPPORTED", "new-save preparation exposes an unsupported profile without fallback");
errorCode(() => confirmTypedSave(unsupportedSaveProfile), "UNSUPPORTED_RENDER_PROFILE", "new saving cannot confirm an unsupported render profile");
const special = prepareTypedSave("木\uFE0F‍木");
check(special.warnings.some(({ kind }) => kind === "VARIATION_SELECTOR") && special.warnings.some(({ kind }) => kind === "ZWJ"), "joiner and variation selector warnings identify code points");
errorCode(() => confirmTypedSave(special), "SPECIAL_CHARACTER_CONFIRMATION_REQUIRED");
check(confirmTypedSave(special, { acceptSpecialCharacters: true }).text === special.original, "explicit special-character confirmation preserves text exactly");

const plane = createCapturePlane(480, 220);
check(Object.isFrozen(plane) && plane.width === 480, "capture plane is validated and frozen before capture");
errorCode(() => createCapturePlane(undefined, 220), "INVALID_FIELD_TYPE", "capture plane has no inferred/default width");
const transform = centerFitTransform(plane, { x: 0, y: 0, width: 300, height: 180 });
check(transform.scale === 0.625 && transform.x === 0 && transform.y === 21.25, "non-square center-fit uses one uniform scale");
equal(mapNormalizedPoint(transform, { x: 0.12, y: 0.43 }), { x: 36, y: 80.375 }, "accepted center-fit example maps exactly");
const inverse = inverseMapPoint(transform, { x: 36, y: 80.375 });
check(inverse.state === "INSIDE" && Math.abs(inverse.point.x - 0.12) < 1e-12 && Math.abs(inverse.point.y - 0.43) < 1e-12, "inverse mapping roundtrips without independent-axis stretch");
check(inverseMapPoint(transform, { x: 10, y: 2 }).state === "OUTSIDE", "pointer-down in letterbox is ignored");
const clamped = inverseMapPoint(transform, { x: 400, y: 200 }, { clampActiveStroke: true });
equal(clamped, { state: "CLAMPED", point: { x: 1, y: 1 } }, "active stroke may clamp at the frozen plane edge");

check(assessEmbeddingReadiness(typed).state === "POLICY_UNKNOWN", "embedding readiness never guesses an F2B policy");
const legacyEnvelope = {
  contract: "shirushi-personal-mark-read", contractVersion: 1, state: "legacy_v1",
  sourceVersion: 1, geometryProvenance: "legacy-unknown",
  payload: { version: 1, type: "handwritten", strokes: [{ points: [{ x: 0, y: 0, t: 0 }] }] },
};
const legacy = validatePythonReadEnvelope(legacyEnvelope);
check(legacy.state === "LEGACY_V1" && legacy.geometryProvenance === "legacy-unknown" && legacy.conversion === "NOT_PERFORMED", "Python-authoritative v1 is explicit and never auto-converted");
check(!("coordinateSpace" in legacy), "legacy contract invents no geometry or display assumption");
check(Object.isFrozen(legacy.payload), "opaque Python-validated v1 payload is cloned and frozen, not independently revalidated");
check(validatePythonReadEnvelope({ contract: "shirushi-personal-mark-read", contractVersion: 1, state: "absent" }).state === "ABSENT", "v2 absence remains distinct");
for (const state of ["malformed", "unsupported", "io_error"]) {
  check(validatePythonReadEnvelope({ contract: "shirushi-personal-mark-read", contractVersion: 1, state, source: "v2", errorCode: "EXAMPLE" }).state === state.toUpperCase(), `${state} v2 never implies v1 fallback`);
}
const v2Envelope = validatePythonReadEnvelope({
  contract: "shirushi-personal-mark-read", contractVersion: 1, state: "v2", sourceVersion: 2, mark: typed,
  renderProfileSupport: { state: "ASSETS_UNAVAILABLE", errorCode: "RENDER_ASSETS_UNAVAILABLE" },
});
check(v2Envelope.state === "V2" && v2Envelope.renderProfileSupport.renderReady === false, "Python v2 envelope profile support is cross-checked");
errorCode(() => validatePythonReadEnvelope({ ...legacyEnvelope, geometryProvenance: "480x280" }), "INVALID_FIELD_TYPE", "legacy approximate geometry cannot become provenance");

async function importGraph(entry) {
  const visited = new Set();
  async function visit(file) {
    const absolute = normalize(file);
    if (visited.has(absolute)) return;
    visited.add(absolute);
    const sourceText = await readFile(absolute, "utf8");
    const pattern = /(?:import|export)\s+(?:[^"']*?\s+from\s+)?["'](\.[^"']+)["']/g;
    for (const match of sourceText.matchAll(pattern)) {
      let target = resolve(dirname(absolute), match[1]);
      if (!extname(target)) target += ".js";
      await visit(target);
    }
  }
  await visit(resolve(here, "..", entry));
  return visited;
}
const normalGraph = await importGraph("index.js");
const normalNames = [...normalGraph].map((file) => relative(resolve(here, ".."), file).replaceAll("\\", "/"));
check(!normalNames.some((name) => name.startsWith("personal-mark-v2/")), "formal v2 contract stays outside the complete normal Browser import graph");
const moduleFiles = ["contract.js", "embedding.js", "errors.js", "geometry.js", "legacy.js", "parser.js", "profile-registry.js", "typed-save.js"];
const source = (await Promise.all(moduleFiles.map((name) => readFile(join(here, name), "utf8")))).join("\n");
for (const forbidden of ["localStorage", "sessionStorage", "fetch(", "XMLHttpRequest", "sendBeacon", "crypto.subtle", "window.__TAURI__", "invoke(", ".normalize("]) {
  check(!source.includes(forbidden), `isolated contract excludes forbidden authority/storage primitive ${forbidden}`);
}
check(!source.includes("480, height: 280") && !source.includes("480,height:280"), "legacy display assumption is not encoded into the contract");

console.log(`PASS Personal Mark v2 Web contract: ${checks} checks (${relative(resolve(here, ".."), here).replaceAll("\\", "/")})`);
