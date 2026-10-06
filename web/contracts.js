/**
 * @typedef {"SUCCESS"|"VERIFIED"|"PREVIEW_ADD"|"PREVIEW_VERIFY"|"PREVIEW_UNMARKED"|"UNSUPPORTED"|"ERROR"} OperationStatus
 * @typedef {{coreAdd:boolean, coreVerify:boolean, coreReadback:boolean, localImagePreview:boolean, sessionMarkEdit:boolean, previewAddMotion:boolean, previewVerifyMotion:boolean}} Capabilities
 * @typedef {{status:OperationStatus, reason?:string, source?:string, mark?:PersonalMark, provenance?:string}} OperationResult
 * @typedef {{width:number,height:number}} CoordinateSpace
 * @typedef {{x:number,y:number}} MarkPoint
 * @typedef {{coordinateSpace:CoordinateSpace,strokes:MarkPoint[][]}} HandwrittenMark
 * @typedef {{version:1,mode:"typed"|"handwritten",typed:string,handwritten:HandwrittenMark}} PersonalMark
 */

export const REQUIRED_CAPABILITIES = Object.freeze([
  "coreAdd",
  "coreVerify",
  "coreReadback",
  "localImagePreview",
  "sessionMarkEdit",
  "previewAddMotion",
  "previewVerifyMotion",
]);

export function freezeCapabilities(value) {
  const normalized = {};
  for (const name of REQUIRED_CAPABILITIES) normalized[name] = value?.[name] === true;
  return Object.freeze(normalized);
}

export function unsupported(operation, reason = "core_unavailable") {
  return Object.freeze({ status: "UNSUPPORTED", operation, reason });
}

export function assertAdapter(adapter) {
  if (!adapter || typeof adapter !== "object") throw new TypeError("ADAPTER_REQUIRED");
  if (!adapter.capabilities) throw new TypeError("ADAPTER_CAPABILITIES_REQUIRED");
  for (const name of REQUIRED_CAPABILITIES) {
    if (typeof adapter.capabilities[name] !== "boolean") {
      throw new TypeError(`INVALID_CAPABILITY:${name}`);
    }
  }
  for (const method of ["loadSessionMark", "saveSessionMark", "imageFromFile", "releaseImage", "addMark", "readFileMark", "verifyFileMark"]) {
    if (typeof adapter[method] !== "function") throw new TypeError(`ADAPTER_METHOD_REQUIRED:${method}`);
  }
  return adapter;
}

export function isCoreSuccess(result, operation) {
  if (operation === "add") return result?.status === "SUCCESS";
  if (operation === "verify") return result?.status === "VERIFIED";
  return false;
}

export function isPreviewOutcome(result, operation) {
  if (operation === "add") return result?.status === "PREVIEW_ADD";
  if (operation === "verify") return result?.status === "PREVIEW_VERIFY";
  return false;
}

export function operationEnabled(capabilities, operation, { hasImage = false, hasMark = false } = {}) {
  if (!hasImage) return false;
  if (operation === "add") {
    return hasMark && (capabilities.coreAdd || capabilities.previewAddMotion);
  }
  if (operation === "verify") {
    return capabilities.coreVerify || capabilities.previewVerifyMotion;
  }
  return false;
}
