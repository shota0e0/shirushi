import { DesktopBridgeError } from "./contract.js";

export const LIMITED_OPERATION = "limited_c2pa_cawg_inspection";
const FAILURE_CODES = new Set([
  "SOURCE_CHANGED", "UNSUPPORTED_FORMAT", "INPUT_UNAVAILABLE", "C2PA_ABSENT",
  "C2PA_MALFORMED", "INTEGRITY_FAILURE", "ASSET_BINDING_FAILURE", "SIGNATURE_INVALID",
  "CAWG_ABSENT", "CAWG_UNSUPPORTED", "INTERNAL_SDK_FAILURE", "RESOURCE_LIMIT_EXCEEDED",
  "TIMEOUT", "CANCELLED", "SERVICE_UNAVAILABLE", "RESULT_INVALID", "CLEANUP_FAILED",
]);

// The accepted helper success scope is these exact PNG bytes, not arbitrary images.
const SUCCESS = {
  contractVersion: 2, operation: LIMITED_OPERATION,
  result: "LIMITED_INSPECTION", completeness: "INCOMPLETE",
  checks: { c2pa: "INSPECTED", cawg: "INSPECTED", trustmark: "NOT_CHECKED" },
  inspection: {
    contract: "shirushi-limited-inspection", contractVersion: 1,
    overall: "LIMITED_INSPECTION", reasonCode: "LIMITED_SCOPE", trustmark: "NOT_CHECKED",
    fullVerificationPerformed: false, successMotionEligible: false,
    source: {
      sha256: "558c4044228761f91ad1ee1a4637bdd868c65f0e9954e7de928a1262e3076316",
      size: 319495, format: "PNG",
    },
    c2pa: {
      state: "INSPECTED", presence: "PRESENT", parse: true,
      assertionDigestsValid: true, assetBindingValid: true,
      signature: "PREVIEW", trustValidated: false,
    },
    cawg: {
      state: "INSPECTED", presence: "PRESENT",
      aiTrainingUse: "NOT_WANTED", aiInferenceUse: "NOT_WANTED",
    },
  },
};

function matches(value, expected) {
  if (!expected || typeof expected !== "object") return value === expected;
  if (!value || typeof value !== "object" || Array.isArray(value)
      || Object.getPrototypeOf(value) !== Object.prototype) return false;
  const keys = Reflect.ownKeys(value);
  return keys.length === Object.keys(expected).length
    && Object.keys(expected).every((key) => Object.hasOwn(value, key) && matches(value[key], expected[key]));
}
function frozenCopy(value) {
  if (!value || typeof value !== "object") return value;
  return Object.freeze(Object.fromEntries(Object.entries(value).map(([key, child]) => [key, frozenCopy(child)])));
}

export function validateLimitedResult(value) {
  if (value?.result === "LIMITED_INSPECTION" && matches(value, SUCCESS)) return frozenCopy(value);
  if (value?.result === "INSPECTION_FAILED" && FAILURE_CODES.has(value.errorCode)) {
    const expected = {
      contractVersion: 2, operation: LIMITED_OPERATION, result: "INSPECTION_FAILED",
      completeness: "INCOMPLETE",
      checks: { c2pa: "NOT_CHECKED", cawg: "NOT_CHECKED", trustmark: "NOT_CHECKED" },
      errorCode: value.errorCode,
    };
    if (matches(value, expected)) return frozenCopy(value);
  }
  throw new DesktopBridgeError("INVALID_LIMITED_RESULT");
}

export function limitedRequest(inputPath) {
  if (typeof inputPath !== "string" || !inputPath || inputPath.length > 4096 || inputPath.includes("\0")) {
    throw new DesktopBridgeError("INVALID_INSPECTION_REQUEST");
  }
  return Object.freeze({ operation: LIMITED_OPERATION, inputPath });
}

/** Independent of the Python Personal Mark bridge. Native supervision owns timeout/cleanup.
 * Keep the single-flight lock until the native call settles; no watchdog replay/overlap.
 */
export function createLimitedController(transport, { onChange = () => {} } = {}) {
  let state = Object.freeze({ phase: "IDLE", result: null, errorCode: null });
  let active = false;
  let disposed = false;
  let generation = 0;
  function publish(next) { state = Object.freeze(next); onChange(state); }
  return Object.freeze({
    get state() { return state; },
    async inspect(inputPath) {
      if (disposed || active) return false;
      active = true;
      const current = ++generation;
      publish({ phase: "RUNNING", result: null, errorCode: null });
      try {
        const request = limitedRequest(inputPath);
        const result = validateLimitedResult(await transport.inspectLimited(request));
        if (disposed || current !== generation) return false;
        publish({ phase: result.result, result, errorCode: result.errorCode ?? null });
        return true;
      } catch (error) {
        if (!disposed && current === generation) {
          publish({ phase: "TRANSPORT_FAILED", result: null,
            errorCode: error instanceof DesktopBridgeError ? error.code : "BRIDGE_UNAVAILABLE" });
        }
        return false;
      } finally { active = false; }
    },
    dispose() { disposed = true; ++generation; },
  });
}

export function limitedPresentationModel(state) {
  if (state.phase === "LIMITED_INSPECTION") {
    return "LIMITED_INSPECTION · INCOMPLETE · C2PA: INSPECTED · CAWG: INSPECTED · TrustMark: NOT_CHECKED · signature: PREVIEW";
  }
  if (state.phase === "INSPECTION_FAILED") return `INSPECTION_FAILED · ${state.errorCode} · INCOMPLETE · TrustMark: NOT_CHECKED`;
  if (state.phase === "TRANSPORT_FAILED") return `Invocation/transport failure · ${state.errorCode}`;
  return state.phase === "RUNNING" ? "Limited Inspection: RUNNING" : "Limited Inspection: IDLE";
}

/** Explicit local path is a development input, never a native picker/opaque-target claim. */
export function createLimitedPresentation(getState, inspect) {
  let section, label, path, button, note, status;
  return Object.freeze({
    render({ elements, locale, documentRef }) {
      if (!section) {
        section = documentRef.createElement("section");
        section.className = "bridge-status"; section.id = "limitedInspection";
        label = documentRef.createElement("label"); label.htmlFor = "limitedInputPath";
        path = documentRef.createElement("input"); path.type = "text"; path.id = "limitedInputPath";
        path.maxLength = 4096; path.autocomplete = "off"; path.spellcheck = false;
        button = documentRef.createElement("button"); button.type = "button";
        button.className = "text-button";
        button.addEventListener("click", () => { void inspect(path.value); });
        note = documentRef.createElement("p");
        status = documentRef.createElement("p");
        status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
        section.append(label, path, button, note, status);
        elements.root.querySelector(".intro").after(section);
      }
      const ja = locale === "ja";
      label.textContent = ja ? "開発用画像の絶対パス" : "Development image absolute path";
      button.textContent = ja ? "限定検査を実行" : "Run Limited Inspection";
      note.textContent = ja
        ? "開発専用。現在の対象は承認済み固定 PNG またはバイト同一のコピーのみ。C2PA / CAWG の限定検査であり、TrustMark・完全検証は行いません。helper は事前準備したビルド時固定入力が必要です。"
        : "Development only: accepted fixed PNG or a byte-identical copy only. Limited C2PA / CAWG inspection; TrustMark and full verification are not performed. Requires independently frozen helper build inputs.";
      const state = getState();
      section.dataset.phase = state.phase;
      button.disabled = state.phase === "RUNNING";
      path.disabled = state.phase === "RUNNING";
      status.textContent = limitedPresentationModel(state);
    },
    destroy() { section?.remove(); },
  });
}
