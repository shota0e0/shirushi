import { deepFreeze, fail, immutableClone } from "./errors.js";
import { assertNativeDepth, assertWellFormedUnicode, MAX_LOCAL_BYTES, parseStrictJson, utf8ByteLengthStrict } from "./parser.js";
import { resolveRenderProfile } from "./profile-registry.js";
import { hasProperty, normalizeNfc, UNICODE_VERSION } from "./unicode16.js";

export const SCHEMA_VERSION = 2;
export const TEXT_MAX_SCALARS = 128;
export const TEXT_MAX_UTF8_BYTES = 512;
export const COORDINATE_MIN = 1;
export const COORDINATE_MAX = 16384;
export const ASPECT_MIN = 1 / 16;
export const ASPECT_MAX = 16;
export const STROKE_MAX = 128;
export const POINTS_PER_STROKE_MAX = 4096;
export const POINTS_TOTAL_MAX = 20000;
export const STROKE_DURATION_MAX_MS = 120000;

const own = (value, name) => Object.prototype.hasOwnProperty.call(value, name);

function record(value, path) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    fail(path === "$" ? "ROOT_NOT_OBJECT" : "INVALID_FIELD_TYPE", { field: path, expected: "object" });
  }
}

function exactFields(value, required, path) {
  for (const field of required) if (!own(value, field)) fail("MISSING_FIELD", { field: `${path}.${field}` });
  for (const field of Object.keys(value)) {
    if (!required.includes(field)) fail("UNKNOWN_FIELD", { field: `${path}.${field}` });
  }
}

function integer(value, path, minimum, maximum) {
  if (typeof value !== "number") fail("INVALID_FIELD_TYPE", { field: path, expected: "number" });
  if (!Number.isFinite(value)) fail("INVALID_NUMBER", { field: path });
  if (!Number.isInteger(value)) fail("INVALID_FIELD_TYPE", { field: path, expected: "integer" });
  if (value < minimum || value > maximum) fail("OUT_OF_RANGE", { field: path, minimum, maximum });
  return value;
}

function finiteNumber(value, path, minimum, maximum) {
  if (typeof value !== "number") fail("INVALID_FIELD_TYPE", { field: path, expected: "number" });
  if (!Number.isFinite(value)) fail("INVALID_NUMBER", { field: path });
  if (value < minimum || value > maximum) fail("OUT_OF_RANGE", { field: path, minimum, maximum });
  return value;
}

function unicodeScalars(text) {
  return [...text];
}

export function validateTypedText(text, { requireNfc = true, enforceSize = true } = {}) {
  if (typeof text !== "string") fail("INVALID_FIELD_TYPE", { field: "$.text", expected: "string" });
  assertWellFormedUnicode(text);
  // normalizeNfc is the vendored Unicode 16 implementation; native String.normalize is not normative here.
  const scalars = unicodeScalars(text);
  for (let index = 0; index < scalars.length; index += 1) {
    const character = scalars[index];
    const cp = character.codePointAt(0);
    if ((cp >= 0 && cp <= 0x1f) || (cp >= 0x7f && cp <= 0x9f) || cp === 0x2028 || cp === 0x2029) {
      fail("TEXT_CONTROL", { index, codePoint: cp });
    }
    if (hasProperty(cp, "bidi_control")) fail("TEXT_BIDI_CONTROL", { index, codePoint: cp });
    const permittedWarning = cp === 0x200c || cp === 0x200d || hasProperty(cp, "variation_selector");
    if (!permittedWarning && (hasProperty(cp, "format") || hasProperty(cp, "default_ignorable"))) {
      fail("TEXT_FORBIDDEN_INVISIBLE", { index, codePoint: cp });
    }
  }
  if (scalars.length === 0) fail("TEXT_EMPTY");
  if (hasProperty(scalars[0].codePointAt(0), "white_space")) fail("TEXT_EDGE_WHITESPACE", { edge: "start" });
  if (hasProperty(scalars.at(-1).codePointAt(0), "white_space")) fail("TEXT_EDGE_WHITESPACE", { edge: "end" });
  if (!scalars.some((character) => hasProperty(character.codePointAt(0), "visible_base"))) {
    fail("TEXT_VISIBLE_BASE_REQUIRED");
  }
  if (requireNfc && normalizeNfc(text) !== text) fail("TEXT_NOT_NFC");
  if (enforceSize && scalars.length > TEXT_MAX_SCALARS) {
    fail("TEXT_TOO_LONG", { maximum: TEXT_MAX_SCALARS, actual: scalars.length });
  }
  if (enforceSize) {
    const bytes = utf8ByteLengthStrict(text);
    if (bytes > TEXT_MAX_UTF8_BYTES) fail("TEXT_UTF8_TOO_LONG", { maximum: TEXT_MAX_UTF8_BYTES, actual: bytes });
  }
  return text;
}

function validateTyped(value) {
  exactFields(value, ["version", "type", "text", "renderProfile"], "$" );
  validateTypedText(value.text);
  record(value.renderProfile, "$.renderProfile");
  exactFields(value.renderProfile, ["id", "version"], "$.renderProfile");
  const support = resolveRenderProfile(value.renderProfile);
  const mark = immutableClone({
    version: SCHEMA_VERSION,
    type: "typed",
    text: value.text,
    renderProfile: { id: value.renderProfile.id, version: value.renderProfile.version },
  });
  return { mark, renderProfileSupport: support };
}

function validateHandwritten(value) {
  exactFields(value, ["version", "type", "coordinateSpace", "strokes"], "$" );
  record(value.coordinateSpace, "$.coordinateSpace");
  exactFields(value.coordinateSpace, ["width", "height"], "$.coordinateSpace");
  const width = integer(value.coordinateSpace.width, "$.coordinateSpace.width", COORDINATE_MIN, COORDINATE_MAX);
  const height = integer(value.coordinateSpace.height, "$.coordinateSpace.height", COORDINATE_MIN, COORDINATE_MAX);
  const aspect = width / height;
  if (aspect < ASPECT_MIN || aspect > ASPECT_MAX) {
    fail("OUT_OF_RANGE", { field: "$.coordinateSpace.aspect", minimum: ASPECT_MIN, maximum: ASPECT_MAX });
  }
  if (!Array.isArray(value.strokes)) fail("INVALID_FIELD_TYPE", { field: "$.strokes", expected: "array" });
  if (value.strokes.length < 1 || value.strokes.length > STROKE_MAX) {
    fail("LIMIT_EXCEEDED", { field: "$.strokes", minimum: 1, maximum: STROKE_MAX, actual: value.strokes.length });
  }
  let total = 0;
  const strokes = value.strokes.map((stroke, strokeIndex) => {
    const strokePath = `$.strokes[${strokeIndex}]`;
    record(stroke, strokePath);
    exactFields(stroke, ["points"], strokePath);
    if (!Array.isArray(stroke.points)) fail("INVALID_FIELD_TYPE", { field: `${strokePath}.points`, expected: "array" });
    if (stroke.points.length < 1 || stroke.points.length > POINTS_PER_STROKE_MAX) {
      fail("LIMIT_EXCEEDED", { field: `${strokePath}.points`, minimum: 1, maximum: POINTS_PER_STROKE_MAX, actual: stroke.points.length });
    }
    total += stroke.points.length;
    if (total > POINTS_TOTAL_MAX) fail("LIMIT_EXCEEDED", { field: "$.strokes.totalPoints", maximum: POINTS_TOTAL_MAX, actual: total });
    let previousTime = -1;
    const points = stroke.points.map((point, pointIndex) => {
      const pointPath = `${strokePath}.points[${pointIndex}]`;
      record(point, pointPath);
      exactFields(point, ["x", "y", "t"], pointPath);
      const x = finiteNumber(point.x, `${pointPath}.x`, 0, 1);
      const y = finiteNumber(point.y, `${pointPath}.y`, 0, 1);
      const t = integer(point.t, `${pointPath}.t`, 0, STROKE_DURATION_MAX_MS);
      if (pointIndex === 0 && t !== 0) fail("TIMING_INVALID", { field: `${pointPath}.t`, reason: "first_point_must_be_zero" });
      if (t < previousTime) fail("TIMING_INVALID", { field: `${pointPath}.t`, reason: "must_be_nondecreasing" });
      previousTime = t;
      return { x, y, t };
    });
    return { points };
  });
  return {
    mark: immutableClone({ version: SCHEMA_VERSION, type: "handwritten", coordinateSpace: { width, height }, strokes }),
    renderProfileSupport: null,
  };
}

function encodedSize(mark) {
  const json = JSON.stringify(mark);
  const size = utf8ByteLengthStrict(json);
  if (size > MAX_LOCAL_BYTES) fail("PAYLOAD_TOO_LARGE", { maximum: MAX_LOCAL_BYTES, actual: size });
  return size;
}

export function validateMarkV2(value) {
  assertNativeDepth(value);
  record(value, "$");
  if (!own(value, "version")) fail("MISSING_FIELD", { field: "$.version" });
  if (typeof value.version !== "number") fail("INVALID_FIELD_TYPE", { field: "$.version", expected: "number" });
  if (!Number.isFinite(value.version)) fail("INVALID_NUMBER", { field: "$.version" });
  if (!Number.isInteger(value.version)) fail("INVALID_FIELD_TYPE", { field: "$.version", expected: "integer" });
  if (value.version !== SCHEMA_VERSION) return deepFreeze({ state: "UNSUPPORTED_VERSION", version: value.version });
  if (!own(value, "type")) fail("MISSING_FIELD", { field: "$.type" });
  if (typeof value.type !== "string") fail("INVALID_FIELD_TYPE", { field: "$.type", expected: "string" });
  if (value.type !== "typed" && value.type !== "handwritten") {
    return deepFreeze({ state: "UNSUPPORTED_TYPE", type: value.type, version: SCHEMA_VERSION });
  }
  const validated = value.type === "typed" ? validateTyped(value) : validateHandwritten(value);
  encodedSize(validated.mark);
  return deepFreeze({ state: "VALID", ...validated, unicodeVersion: UNICODE_VERSION });
}

export function parseMarkV2(raw) {
  return validateMarkV2(parseStrictJson(raw));
}

export function encodeMarkV2(value) {
  const result = validateMarkV2(value);
  if (result.state !== "VALID") fail(result.state === "UNSUPPORTED_VERSION" ? "UNKNOWN_SCHEMA_VERSION" : "UNKNOWN_TYPE", result);
  const text = JSON.stringify(result.mark);
  const bytes = new TextEncoder().encode(text);
  if (bytes.byteLength > MAX_LOCAL_BYTES) fail("PAYLOAD_TOO_LARGE", { maximum: MAX_LOCAL_BYTES, actual: bytes.byteLength });
  return bytes;
}
