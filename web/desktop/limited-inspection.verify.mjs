import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { DesktopBridgeError } from "./contract.js";
import { createDesktopTransport } from "./transport.js";
import {
  LIMITED_OPERATION, validateLimitedResult, limitedRequest, createLimitedController,
  limitedPresentationModel, createLimitedPresentation,
} from "./limited-inspection.js";

let checks = 0;
const check = (value, note) => { assert.ok(value, note); ++checks; };
const throws = (call) => { assert.throws(call, DesktopBridgeError); ++checks; };
const success = {
  contractVersion: 2, operation: LIMITED_OPERATION, result: "LIMITED_INSPECTION", completeness: "INCOMPLETE",
  checks: { c2pa: "INSPECTED", cawg: "INSPECTED", trustmark: "NOT_CHECKED" },
  inspection: {
    contract: "shirushi-limited-inspection", contractVersion: 1, overall: "LIMITED_INSPECTION",
    reasonCode: "LIMITED_SCOPE", trustmark: "NOT_CHECKED", fullVerificationPerformed: false, successMotionEligible: false,
    source: { sha256: "558c4044228761f91ad1ee1a4637bdd868c65f0e9954e7de928a1262e3076316", size: 319495, format: "PNG" },
    c2pa: { state: "INSPECTED", presence: "PRESENT", parse: true, assertionDigestsValid: true,
      assetBindingValid: true, signature: "PREVIEW", trustValidated: false },
    cawg: { state: "INSPECTED", presence: "PRESENT", aiTrainingUse: "NOT_WANTED", aiInferenceUse: "NOT_WANTED" },
  },
};
const failure = (errorCode = "UNSUPPORTED_FORMAT") => ({
  contractVersion: 2, operation: LIMITED_OPERATION, result: "INSPECTION_FAILED", completeness: "INCOMPLETE",
  checks: { c2pa: "NOT_CHECKED", cawg: "NOT_CHECKED", trustmark: "NOT_CHECKED" }, errorCode,
});
const copy = (x) => structuredClone(x);
const accepted = validateLimitedResult(success);
check(accepted !== success && Object.isFrozen(accepted.inspection.source), "deep detached immutable envelope");
check(accepted.completeness === "INCOMPLETE" && !accepted.inspection.successMotionEligible, "no full-success promotion");
for (const change of [
  (x) => { x.result = "PASS"; }, (x) => { x.completeness = "COMPLETE"; },
  (x) => { x.checks.trustmark = "INSPECTED"; }, (x) => { x.extra = 1; },
  (x) => { x.inspection.source.sha256 = "0".repeat(64); }, (x) => { x.inspection.source.size++; },
  (x) => { x.inspection.c2pa.trustValidated = true; }, (x) => { x.inspection.successMotionEligible = true; },
  (x) => { x.inspection.cawg.aiTrainingUse = "ALLOWED"; }, (x) => { delete x.inspection.cawg.presence; },
  (x) => { x.inspection.c2pa.parse = 1; }, (x) => { x.inspection.source.path = "C:/private/input.png"; },
]) { const bad = copy(success); change(bad); throws(() => validateLimitedResult(bad)); }
for (const bad of [null, [], { ...success, operation: "verify" }, failure("C:/private/path"),
  { ...failure(), checks: success.checks }, { ...failure(), inspection: success.inspection },
  { ...failure(), errorCode: "UNKNOWN" }, Object.assign(Object.create(null), success)]) {
  throws(() => validateLimitedResult(bad));
}
for (const code of ["TIMEOUT", "CLEANUP_FAILED", "SERVICE_UNAVAILABLE", "RESULT_INVALID", "INPUT_UNAVAILABLE"]) {
  check(validateLimitedResult(failure(code)).result === "INSPECTION_FAILED", `explicit native failure ${code}`);
}
for (const path of ["", null, 3, "a\0b", "a".repeat(4097)]) throws(() => limitedRequest(path));
const inputPath = "C:/dev/input.png";
const request = limitedRequest(inputPath);
check(request.operation === LIMITED_OPERATION && request.inputPath === inputPath, "explicit literal operation/path");
const calls = [];
let response = success;
const transport = createDesktopTransport({ __TAURI__: { core: { invoke: async (...args) => { calls.push(args); return response; } } } });
const states = [];
const controller = createLimitedController(transport, { onChange: (x) => states.push(x) });
check(await controller.inspect(inputPath), "valid native result");
assert.deepEqual(calls, [["bridge_inspect_limited", { request: { operation: LIMITED_OPERATION, inputPath } }]]); ++checks;
check(states[0].phase === "RUNNING" && controller.state.phase === "LIMITED_INSPECTION", "result-state transition");
const text = limitedPresentationModel(controller.state);
for (const required of ["LIMITED_INSPECTION", "INCOMPLETE", "C2PA: INSPECTED", "CAWG: INSPECTED", "TrustMark: NOT_CHECKED", "PREVIEW"]) {
  check(text.includes(required), `display ${required}`);
}
check(!/FULLY_VERIFIED|\bCOMPLETE\b|\bPASS\b/.test(text), "limited render has no full-success claim");
response = failure();
check(await controller.inspect(inputPath) && controller.state.phase === "INSPECTION_FAILED", "sequential failure separate from invocation error");
check(controller.state.result.inspection === undefined, "no stale success on inspection failure");
check(limitedPresentationModel(controller.state).includes("UNSUPPORTED_FORMAT"), "failure reason displayed");
response = { result: "LIMITED_INSPECTION" };
check(!(await controller.inspect(inputPath)) && controller.state.phase === "TRANSPORT_FAILED"
  && controller.state.errorCode === "INVALID_LIMITED_RESULT", "malformed reply is not successful inspection");
const unavailable = createLimitedController(createDesktopTransport({}));
check(!(await unavailable.inspect(inputPath)) && unavailable.state.phase === "TRANSPORT_FAILED", "missing invoke distinct failure");
const rejected = createLimitedController(createDesktopTransport({ __TAURI__: { core: { invoke: async () => {
  throw { code: "C:/private/trace", message: "private body" };
} } } }));
await rejected.inspect(inputPath);
check(rejected.state.errorCode === "BRIDGE_UNAVAILABLE" && !JSON.stringify(rejected.state).includes("private"), "path-safe transport rejection");
let release;
let count = 0;
const pending = createLimitedController({ inspectLimited: () => { ++count; return new Promise((resolve) => { release = resolve; }); } });
const first = pending.inspect(inputPath);
check(!(await pending.inspect(inputPath)) && count === 1 && pending.state.phase === "RUNNING", "one request until native cleanup/settlement");
release(success); await first;
const second = pending.inspect(inputPath);
pending.dispose(); release(success); await second;
check(pending.state.phase === "RUNNING" && !(await pending.inspect(inputPath)), "disposed UI cannot publish late native reply/replay");

class Node {
  children = []; dataset = {}; events = {}; value = "";
  append(...children) { this.children.push(...children); }
  after(child) { this.children.push(child); }
  setAttribute(key, value) { this[key] = value; }
  addEventListener(key, fn) { this.events[key] = fn; }
  remove() { this.removed = true; }
}
const intro = new Node();
const elements = { root: { querySelector: (selector) => { assert.equal(selector, ".intro"); return intro; } } };
const documentRef = { createElement: () => new Node() };
let uiState = { phase: "IDLE", result: null, errorCode: null }, uiPath;
const panel = createLimitedPresentation(() => uiState, (path) => { uiPath = path; });
panel.render({ elements, locale: "en", documentRef });
const section = intro.children[0];
const [label, path, button, note, status] = section.children;
check(label.textContent.includes("absolute path") && note.textContent.includes("byte-identical"), "explicit development input/scope");
path.value = inputPath; button.events.click();
check(uiPath === inputPath && status.textContent.includes("IDLE"), "button dispatches only explicit path");
uiState = { phase: "RUNNING" }; panel.render({ elements, locale: "ja", documentRef });
check(button.disabled && path.disabled && intro.children.length === 1, "running inputs disabled; section reused");
uiState = controller.state; panel.render({ elements, locale: "en", documentRef });
check(status.textContent.includes("Invocation/transport failure") && !button.disabled, "transport failure displayed; explicit retry allowed after settlement");
panel.destroy(); check(section.removed, "panel cleanup");
const build = await readFile(new URL("../../desktop/build.rs", import.meta.url), "utf8");
// Source wiring checks remain separate from the actual Rust/helper integration proof.
const lib = await readFile(new URL("../../desktop/src/lib.rs", import.meta.url), "utf8");
const stage = await readFile(new URL("../../desktop/src/asset_stage.rs", import.meta.url), "utf8");
const acl = JSON.parse(await readFile(new URL("../../desktop/capabilities/main-window.json", import.meta.url), "utf8"));
check(lib.includes("bridge_inspect_limited") && build.includes('"bridge_inspect_limited"')
  && acl.permissions.includes("allow-bridge-inspect-limited"), "native command + local-window ACL registered");
check(/\(\s*"desktop\/limited-inspection\.js",\s*"desktop\/limited-inspection\.js",?\s*\)/.test(stage), "focused legacy runtime module remains staged (format independent)");
console.log(`PASS Limited Inspection Web/controller/presentation: ${checks} checks`);
