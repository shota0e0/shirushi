export class MarkV2Error extends Error {
  constructor(code, details = {}) {
    super(code);
    this.name = "MarkV2Error";
    this.code = code;
    this.details = deepFreeze(structuredCloneSafe(details));
  }
}

function structuredCloneSafe(value) {
  if (Array.isArray(value)) return value.map(structuredCloneSafe);
  if (value && typeof value === "object") {
    const copy = {};
    for (const [key, item] of Object.entries(value)) copy[key] = structuredCloneSafe(item);
    return copy;
  }
  return value;
}

export function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  for (const item of Object.values(value)) deepFreeze(item);
  return Object.freeze(value);
}

export function immutableClone(value) {
  if (Array.isArray(value)) return deepFreeze(value.map(immutableClone));
  if (value && typeof value === "object") {
    const copy = {};
    for (const [key, item] of Object.entries(value)) {
      Object.defineProperty(copy, key, {
        value: immutableClone(item),
        enumerable: true,
        configurable: false,
        writable: false,
      });
    }
    return deepFreeze(copy);
  }
  return value;
}

export function fail(code, details = {}) {
  throw new MarkV2Error(code, details);
}

