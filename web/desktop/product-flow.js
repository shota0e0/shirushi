// Development-only native product contract. No fixture or VERIFIED upgrades.
import { DesktopBridgeError } from "./contract.js";
import { sanitizeMark } from "../mark.js";
const fail = () => { throw new DesktopBridgeError("INVALID_PRODUCT_RESULT"); };
const canonical = (v) => Array.isArray(v) ? v.map(canonical) : v && typeof v === "object"
  ? Object.fromEntries(Object.keys(v).sort().map((k) => [k, canonical(v[k])])) : v;
const same = (a, b) => JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));
function exact(v, fields) {
  if (!v || typeof v !== "object" || Array.isArray(v) || Object.keys(v).length !== fields.length
    || fields.some((k) => !Object.hasOwn(v, k))) fail();
}
const sha = (s) => typeof s === "string" && /^[a-f0-9]{64}$/.test(s);
export function localPath(path) {
  if (typeof path !== "string" || path.length > 4096 || path.includes("\0")
    || !/^[A-Za-z]:[\\/]/.test(path) || path.split(/[\\/]/).includes("..")) fail();
  return path;
}
function source(v) {
  exact(v, ["sha256", "size", "format"]);
  if (!sha(v.sha256) || !Number.isSafeInteger(v.size) || v.size <= 0 || v.size > 64*1024*1024 || !["PNG", "JPEG"].includes(v.format)) fail();
}
export function strictUiMark(mark) {
  exact(mark, ["version", "mode", "typed", "handwritten"]);
  exact(mark.handwritten, ["coordinateSpace", "strokes"]);
  exact(mark.handwritten.coordinateSpace, ["width", "height"]);
  if (!Array.isArray(mark.handwritten.strokes) || mark.handwritten.strokes.length > 128) fail();
  let total = 0;
  for (const stroke of mark.handwritten.strokes) {
    if (!Array.isArray(stroke) || !stroke.length || (total += stroke.length) > 4096) fail();
    for (const point of stroke) exact(point, ["x", "y"]);
  }
  const safe = sanitizeMark(mark);
  if (!safe || !same(safe, mark) || mark.typed.length > 48
    || Object.values(mark.handwritten.coordinateSpace).some((n) => n > 16384)) fail();
  return structuredClone(mark);
}
export function validateImageRecord(v, { maximum = 32*1024*1024 } = {}) {
  exact(v, ["url", "name", "reference", "local", "sha256", "size"]);
  localPath(v.reference);
  if (v.local !== true || typeof v.name !== "string" || !v.name || !sha(v.sha256)
    || !Number.isSafeInteger(v.size) || v.size <= 0 || v.size > maximum
    || typeof v.url !== "string" || v.url.length > Math.ceil(maximum / 3)*4 + 40
    || !/^data:image\/(png|jpeg);base64,[A-Za-z0-9+/]+=*$/.test(v.url)) fail();
  return structuredClone(v);
}
export function validateAddResult(v, mark) {
  exact(v, ["operation", "result", "developmentSigning", "source", "output", "personalMark"]);
  source(v.source); exact(v.output, ["path", "size", "sha256"]); localPath(v.output.path);
  if (v.operation !== "add" || v.result !== "ADD_SUCCESS" || v.developmentSigning !== true
    || !sha(v.output.sha256) || !Number.isSafeInteger(v.output.size) || v.output.size <= 0 || v.output.size > 64*1024*1024) fail();
  strictUiMark(v.personalMark);
  if (!same(v.personalMark, mark)) fail();
  return structuredClone(v);
}
export function validateProductInspection(v) {
  exact(v, ["contractVersion", "operation", "result", "completeness", "checks", "inspection"]);
  exact(v.checks, ["c2pa", "cawg", "trustmark"]);
  if (v.contractVersion !== 2 || v.operation !== "limited_c2pa_cawg_inspection" || v.result !== "LIMITED_INSPECTION"
    || v.completeness !== "INCOMPLETE" || v.checks.c2pa !== "INSPECTED" || v.checks.cawg !== "INSPECTED" || v.checks.trustmark !== "NOT_CHECKED") fail();
  const i = v.inspection;
  exact(i, ["contract", "contractVersion", "overall", "reasonCode", "trustmark", "fullVerificationPerformed", "successMotionEligible", "source", "c2pa", "cawg", "personalMark"]);
  source(i.source);
  if (i.contract !== "shirushi-limited-inspection" || i.contractVersion !== 1 || i.overall !== "LIMITED_INSPECTION"
    || i.reasonCode !== "LIMITED_SCOPE" || i.trustmark !== "NOT_CHECKED" || i.fullVerificationPerformed !== false || i.successMotionEligible !== false) fail();
  exact(i.c2pa, ["state", "presence", "parse", "assertionDigestsValid", "assetBindingValid", "signature", "trustValidated"]);
  const present = i.c2pa.presence === "PRESENT";
  if (i.c2pa.state !== "INSPECTED" || !["PRESENT", "ABSENT"].includes(i.c2pa.presence)
    || i.c2pa.trustValidated !== false || i.c2pa.parse !== present || i.c2pa.assertionDigestsValid !== present
    || i.c2pa.assetBindingValid !== present || i.c2pa.signature !== (present ? "PREVIEW" : "ABSENT")) fail();
  exact(i.cawg, ["state", "presence", "aiTrainingUse", "aiInferenceUse"]);
  if (i.cawg.state !== "INSPECTED" || !["PRESENT", "ABSENT"].includes(i.cawg.presence)
    || (i.cawg.presence === "PRESENT" && !present)
    || [i.cawg.aiTrainingUse, i.cawg.aiInferenceUse].some((s) => !["NOT_WANTED", "UNKNOWN"].includes(s))
    || (i.cawg.presence === "ABSENT" && [i.cawg.aiTrainingUse, i.cawg.aiInferenceUse].some((s) => s !== "UNKNOWN"))) fail();
  if (i.personalMark !== null) { if (!present) fail(); strictUiMark(i.personalMark); }
  return structuredClone(v);
}
export function inspectionUiOutcome(v) {
  const result = validateProductInspection(v);
  const i = result.inspection;
  const intentPresent = i.cawg.presence === "PRESENT" && i.cawg.aiTrainingUse === "NOT_WANTED" && i.cawg.aiInferenceUse === "NOT_WANTED";
  // This is a LIMITED result, never VERIFIED. Reading metadata is not an
  // endorsement of author identity; no full-inspection success motion.
  return { status:"LIMITED_INSPECTION", result, intentPresent,
    mark: i.personalMark, presentationOnly:true };
}
