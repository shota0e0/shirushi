import { fail } from "./errors.js";

export const MAX_LOCAL_BYTES = 2 * 1024 * 1024;
export const MAX_MARK_DEPTH = 8;
const JSON_NUMBER = /-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/y;

function nonzeroLiteralBecameZero(token, value) {
  if (value !== 0) return false;
  const significand = token.split(/[eE]/, 1)[0];
  return /[1-9]/.test(significand);
}

export function assertWellFormedUnicode(text, code = "MALFORMED_UNICODE") {
  for (let index = 0; index < text.length; index += 1) {
    const unit = text.charCodeAt(index);
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const next = text.charCodeAt(index + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) fail(code, { index });
      index += 1;
    } else if (unit >= 0xdc00 && unit <= 0xdfff) {
      fail(code, { index });
    }
  }
  return text;
}

export function utf8ByteLengthStrict(text) {
  assertWellFormedUnicode(text);
  let bytes = 0;
  for (let index = 0; index < text.length; index += 1) {
    const unit = text.charCodeAt(index);
    if (unit <= 0x7f) bytes += 1;
    else if (unit <= 0x7ff) bytes += 2;
    else if (unit >= 0xd800 && unit <= 0xdbff) {
      bytes += 4;
      index += 1;
    } else bytes += 3;
    if (bytes > MAX_LOCAL_BYTES) return bytes;
  }
  return bytes;
}

function decodeInput(raw) {
  if (typeof raw === "string") {
    const byteLength = utf8ByteLengthStrict(raw);
    if (byteLength > MAX_LOCAL_BYTES) fail("PAYLOAD_TOO_LARGE", { maximum: MAX_LOCAL_BYTES, actual: byteLength });
    if (raw.charCodeAt(0) === 0xfeff) fail("TRANSPORT_BOM");
    return raw;
  }
  if (!(raw instanceof Uint8Array)) fail("INVALID_FIELD_TYPE", { field: "raw", expected: "string_or_Uint8Array" });
  if (raw.byteLength > MAX_LOCAL_BYTES) fail("PAYLOAD_TOO_LARGE", { maximum: MAX_LOCAL_BYTES, actual: raw.byteLength });
  if (raw.byteLength >= 3 && raw[0] === 0xef && raw[1] === 0xbb && raw[2] === 0xbf) fail("TRANSPORT_BOM");
  try {
    return new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(raw);
  } catch {
    fail("INVALID_UTF8");
  }
}

export function parseStrictJson(raw) {
  const source = decodeInput(raw);
  let position = 0;

  const syntax = (reason) => fail("MALFORMED_JSON", { position, reason });
  const skipWhitespace = () => {
    while (position < source.length && /[\x20\x09\x0a\x0d]/.test(source[position])) position += 1;
  };

  const parseString = () => {
    if (source[position] !== '"') syntax("string_expected");
    const start = position;
    position += 1;
    while (position < source.length) {
      const code = source.charCodeAt(position);
      if (code === 0x22) {
        position += 1;
        let value;
        try {
          value = JSON.parse(source.slice(start, position));
        } catch {
          syntax("invalid_string_escape");
        }
        assertWellFormedUnicode(value);
        return value;
      }
      if (code < 0x20) syntax("unescaped_control");
      if (code === 0x5c) {
        position += 1;
        if (position >= source.length) syntax("unterminated_escape");
        const escaped = source[position];
        if (escaped === "u") {
          if (!/^[0-9a-fA-F]{4}$/.test(source.slice(position + 1, position + 5))) syntax("invalid_unicode_escape");
          position += 5;
          continue;
        }
        if (!'"\\/bfnrt'.includes(escaped)) syntax("invalid_escape");
      }
      position += 1;
    }
    syntax("unterminated_string");
  };

  const parseValue = (depth) => {
    skipWhitespace();
    const token = source[position];
    if (token === "{") return parseObject(depth);
    if (token === "[") return parseArray(depth);
    if (token === '"') return parseString();
    for (const [literal, value] of [["true", true], ["false", false], ["null", null]]) {
      if (source.startsWith(literal, position)) {
        position += literal.length;
        return value;
      }
    }
    JSON_NUMBER.lastIndex = position;
    const match = JSON_NUMBER.exec(source);
    if (!match) syntax("value_expected");
    position += match[0].length;
    const value = Number(match[0]);
    if (!Number.isFinite(value) || nonzeroLiteralBecameZero(match[0], value)) {
      fail("INVALID_NUMBER", { position: position - match[0].length });
    }
    return value;
  };

  const parseObject = (depth) => {
    if (depth > MAX_MARK_DEPTH) fail("MAX_DEPTH_EXCEEDED", { maximum: MAX_MARK_DEPTH });
    const object = {};
    const names = new Set();
    position += 1;
    skipWhitespace();
    if (source[position] === "}") {
      position += 1;
      return object;
    }
    while (position < source.length) {
      skipWhitespace();
      const key = parseString();
      if (names.has(key)) fail("DUPLICATE_KEY", { key });
      names.add(key);
      skipWhitespace();
      if (source[position] !== ":") syntax("colon_expected");
      position += 1;
      const value = parseValue(depth + 1);
      Object.defineProperty(object, key, { value, enumerable: true, configurable: true, writable: true });
      skipWhitespace();
      if (source[position] === "}") {
        position += 1;
        return object;
      }
      if (source[position] !== ",") syntax("comma_expected");
      position += 1;
    }
    syntax("unterminated_object");
  };

  const parseArray = (depth) => {
    if (depth > MAX_MARK_DEPTH) fail("MAX_DEPTH_EXCEEDED", { maximum: MAX_MARK_DEPTH });
    const array = [];
    position += 1;
    skipWhitespace();
    if (source[position] === "]") {
      position += 1;
      return array;
    }
    while (position < source.length) {
      array.push(parseValue(depth + 1));
      skipWhitespace();
      if (source[position] === "]") {
        position += 1;
        return array;
      }
      if (source[position] !== ",") syntax("comma_expected");
      position += 1;
    }
    syntax("unterminated_array");
  };

  skipWhitespace();
  const value = parseValue(1);
  skipWhitespace();
  if (position !== source.length) syntax("trailing_data");
  return value;
}

export function assertNativeDepth(value, maximum = MAX_MARK_DEPTH) {
  const active = new Set();
  const visit = (item, depth) => {
    if (item === null || typeof item === "boolean") return;
    if (typeof item === "string") {
      assertWellFormedUnicode(item);
      return;
    }
    if (typeof item === "number") {
      if (!Number.isFinite(item)) fail("INVALID_NUMBER");
      return;
    }
    if (!item || typeof item !== "object") fail("INVALID_FIELD_TYPE", { expected: "json_value" });
    if (depth > maximum) fail("MAX_DEPTH_EXCEEDED", { maximum });
    if (active.has(item)) fail("INVALID_FIELD_TYPE", { expected: "acyclic_json_value" });
    if (Array.isArray(item)) {
      const keys = Object.keys(item);
      if (keys.length !== item.length || keys.some((key, index) => key !== String(index))
        || Reflect.ownKeys(item).length !== item.length + 1) {
        fail("INVALID_FIELD_TYPE", { expected: "dense_json_array" });
      }
    } else {
      const prototype = Object.getPrototypeOf(item);
      if (prototype !== Object.prototype && prototype !== null) fail("INVALID_FIELD_TYPE", { expected: "json_object" });
      const keys = Object.keys(item);
      if (Reflect.ownKeys(item).length !== keys.length) fail("INVALID_FIELD_TYPE", { expected: "plain_json_object" });
      for (const key of keys) assertWellFormedUnicode(key);
    }
    active.add(item);
    for (const child of Array.isArray(item) ? item : Object.values(item)) visit(child, depth + 1);
    active.delete(item);
  };
  visit(value, 1);
}
