// Pinned Unicode data, not String.normalize or the engine's Unicode properties.
import data from "./unicode16-data.js";

export const UNICODE_VERSION = "16.0.0";
if (data.unicodeVersion !== UNICODE_VERSION) throw new Error("UNICODE_DATA_VERSION_MISMATCH");

export function hasProperty(cp, name) {
  if (!Number.isInteger(cp) || cp < 0 || cp > 0x10ffff || (cp >= 0xd800 && cp <= 0xdfff)) {
    throw new TypeError("EXPECTED_UNICODE_SCALAR");
  }
  const ranges = data.ranges[name];
  if (!ranges) throw new TypeError("UNKNOWN_UNICODE_PROPERTY");
  let low = 0, high = ranges.length;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    const [start, end] = ranges[middle];
    if (cp < start) high = middle;
    else if (cp > end) low = middle + 1;
    else return true;
  }
  return false;
}

function decompose(cp, output) {
  const index = cp - 0xac00;
  if (index >= 0 && index < 11172) {
    output.push(0x1100 + Math.floor(index / 588), 0x1161 + Math.floor((index % 588) / 28));
    const tail = index % 28;
    if (tail) output.push(0x11a7 + tail);
    return;
  }
  const mapped = data.decomposition[cp];
  if (mapped) for (const child of mapped) decompose(child, output);
  else output.push(cp);
}

function compose(first, second) {
  const leading = first - 0x1100, vowel = second - 0x1161;
  if (leading >= 0 && leading < 19 && vowel >= 0 && vowel < 21) {
    return 0xac00 + (leading * 21 + vowel) * 28;
  }
  const syllable = first - 0xac00, trailing = second - 0x11a7;
  if (syllable >= 0 && syllable < 11172 && syllable % 28 === 0 && trailing > 0 && trailing < 28) {
    return first + trailing;
  }
  return data.composition[`${first},${second}`];
}

export function normalizeNfc(text) {
  if (typeof text !== "string") throw new TypeError("TEXT_MUST_BE_STRING");
  const ordered = [], segment = [];
  const flush = () => {
    segment.sort((a, b) => (data.ccc[a] || 0) - (data.ccc[b] || 0));
    for (const point of segment) ordered.push(point);
    segment.length = 0;
  };
  for (const character of text) {
    const cp = character.codePointAt(0);
    if (cp >= 0xd800 && cp <= 0xdfff) throw new TypeError("MALFORMED_UNICODE");
    const decomposed = [];
    decompose(cp, decomposed);
    for (const point of decomposed) {
      if (!(data.ccc[point] || 0)) { flush(); ordered.push(point); }
      else segment.push(point);
    }
  }
  flush();
  if (!ordered.length) return "";
  const result = [ordered[0]];
  let starterIndex = 0, starter = ordered[0], lastCC = data.ccc[starter] || 0;
  for (let i = 1; i < ordered.length; i++) {
    const point = ordered[i], cc = data.ccc[point] || 0;
    const composite = compose(starter, point);
    if (composite !== undefined && (lastCC === 0 || lastCC < cc)) {
      result[starterIndex] = composite;
      starter = composite;
    } else {
      if (cc === 0) { starterIndex = result.length; starter = point; }
      result.push(point);
      lastCC = cc;
    }
  }
  return result.map(point => String.fromCodePoint(point)).join("");
}
