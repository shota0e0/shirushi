import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve, dirname, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { DesktopAdapter } from "../adapters/desktop-adapter.js";
import { BrowserFoundationAdapter } from "../adapters/browser-foundation-adapter.js";
import { DesktopBridgeError, validateDesktopCapabilities, validateDesktopMarkRead } from "./contract.js";
import { createDesktopTransport } from "./transport.js";
import { createBridgeController } from "./controller.js";
import { bridgePresentationModel } from "./presentation.js";
import { DESKTOP_MESSAGES } from "./i18n.js";

let checks = 0;
const check = (value, message) => { assert.ok(value, message); ++checks; };
const throws = (run, message) => { assert.throws(run, DesktopBridgeError, message); ++checks; };
const rejects = async (run, message) => { await assert.rejects(run, DesktopBridgeError, message); ++checks; };
const copy = (value) => structuredClone(value);
const caps = {
  bridgeProtocolVersion: 1, personalMarkSchemaVersions: [1, 2],
  renderProfiles: [{ id: "shirushi-typed", version: 1, state: "ASSETS_UNAVAILABLE" }],
  capabilities: { personalMarkRead: true, personalMarkWrite: false, nativeTargetSelection: false, coreAdd: false, coreVerify: false, coreReadback: false, c2paPersonalMarkEmbedding: false, explorerIntegration: false },
};
const absent = { contract: "shirushi-personal-mark-read", contractVersion: 1, state: "absent" };
const typed = { ...absent, state: "v2", sourceVersion: 2,
  mark: { version: 2, type: "typed", text: "Private name", renderProfile: { id: "shirushi-typed", version: 1 } },
  renderProfileSupport: { state: "ASSETS_UNAVAILABLE", errorCode: "RENDER_PROFILE_ASSETS_UNAVAILABLE" },
};
// Use the registry's published code, not a second registry definition.
const { resolveRenderProfile } = await import("../personal-mark-v2/profile-registry.js");
typed.renderProfileSupport = ((r) => ({ state: r.state, errorCode: r.errorCode }))(resolveRenderProfile(typed.mark.renderProfile));
const handwritten = { ...absent, state: "v2", sourceVersion: 2, mark: { version: 2, type: "handwritten", coordinateSpace: { width: 700, height: 400 }, strokes: [{ points: [{ x: .2, y: .3, t: 0 }] }] } };
const legacy = { ...absent, state: "legacy_v1", sourceVersion: 1, geometryProvenance: "legacy-unknown", payload: { version: 1, type: "handwritten", strokes: [{ points: [{ x: .2, y: .3, t: 0 }] }] } };

const accepted = validateDesktopCapabilities(caps);
check(Object.isFrozen(accepted) && Object.isFrozen(accepted.capabilities) && Object.isFrozen(accepted.renderProfiles[0]), "detached immutable negotiation");
caps.renderProfiles[0].id = "changed";
check(accepted.renderProfiles[0].id === "shirushi-typed", "capability object detached from source");
caps.renderProfiles[0].id = "shirushi-typed";
for (const mutation of [
  (x) => { x.extra = true; }, (x) => { x.bridgeProtocolVersion = 2; }, (x) => { x.bridgeProtocolVersion = true; },
  (x) => { x.personalMarkSchemaVersions = [2]; }, (x) => { x.renderProfiles[0].version = 2; },
  (x) => { x.renderProfiles[0].state = "SUPPORTED"; }, (x) => { x.renderProfiles[0].extra = true; },
  (x) => { delete x.capabilities.coreReadback; }, (x) => { x.capabilities.unknown = false; },
  (x) => { x.capabilities.personalMarkRead = "true"; }, (x) => { x.capabilities.coreAdd = true; },
  (x) => { x.capabilities.coreVerify = true; }, (x) => { x.capabilities.nativeTargetSelection = true; },
]) { const bad = copy(caps); mutation(bad); throws(() => validateDesktopCapabilities(bad), "bad/inflated negotiation rejected"); }

check(validateDesktopMarkRead(absent).state === "ABSENT", "absence preserved");
const readTyped = validateDesktopMarkRead(typed);
check(readTyped.state === "V2" && readTyped.mark.text === "Private name", "v2 typed data preserved");
check(validateDesktopMarkRead(handwritten).mark.coordinateSpace.width === 700, "handwritten capture plane preserved");
const readLegacy = validateDesktopMarkRead(legacy);
check(readLegacy.state === "LEGACY_V1" && readLegacy.geometryProvenance === "legacy-unknown" && readLegacy.conversion === "NOT_PERFORMED", "legacy not converted");
for (const state of ["malformed", "unsupported", "io_error"]) {
  check(validateDesktopMarkRead({ ...absent, state, source: "v2", errorCode: "TEST_ERROR" }).state === state.toUpperCase(), `${state} not flattened to absent`);
}
const unknown = copy(typed);
unknown.mark.renderProfile.version = 99;
unknown.renderProfileSupport = ((r) => ({ state: r.state, errorCode: r.errorCode }))(resolveRenderProfile(unknown.mark.renderProfile));
check(validateDesktopMarkRead(unknown).renderProfileSupport.state === "UNSUPPORTED", "unknown render profile is readable not defaulted");
for (const bad of [{ ...absent, extra: 1 }, { ...absent, state: "success" }, { ...legacy, geometryProvenance: "exact" }, { ...typed, sourceVersion: 1 }]) {
  throws(() => validateDesktopMarkRead(bad), "invalid read envelope rejected");
}

const calls = [];
const transport = createDesktopTransport({ __TAURI__: { core: { invoke: async (...args) => { calls.push(args); return args[0] === "bridge_get_capabilities" ? copy(caps) : copy(typed); } } } });
const adapter = new DesktopAdapter(transport);
await rejects(() => adapter.loadPersonalMark(), "read before handshake rejected");
check((await adapter.getCapabilities()).capabilities.personalMarkRead, "capabilities uses real transport boundary");
check((await adapter.loadPersonalMark()).state === "V2", "load uses second narrow transport call");
check(JSON.stringify(calls) === JSON.stringify([["bridge_get_capabilities"], ["bridge_load_personal_mark"]]), "two literal command names with no Web arguments");
check(adapter.loadSessionMark() === null, "no profile-to-session conversion");
throws(() => adapter.saveSessionMark({}), "Desktop session editor cannot silently become a save feature");
check(!adapter.capabilities.sessionMarkEdit && !adapter.capabilities.coreReadback && !adapter.capabilities.coreAdd && !adapter.capabilities.coreVerify, "unimplemented operations remain disabled");
for (const method of ["addMark", "verifyFileMark", "readFileMark"]) check((await adapter[method]({})).status === "UNSUPPORTED", `${method} remains unsupported`);
await rejects(() => createDesktopTransport({}).getCapabilities(), "no native runtime means unavailable");
await rejects(() => createDesktopTransport({ __TAURI__: { core: { invoke: () => Promise.reject({ code: "C:/private/path", message: "private traceback" }) } } }).getCapabilities(), "unsafe error details not returned");
const browser = new BrowserFoundationAdapter();
check((await browser.loadPersonalMark()).status === "UNSUPPORTED", "Browser native mark read remains unsupported");
check(!(await browser.getCapabilities()).coreReadback, "Browser capabilities retain no core readback");

function timers() {
  let id = 0; const pending = new Map();
  return { pending, schedule: (fn, delay) => { pending.set(++id, { fn, delay }); return id; }, unschedule: (key) => pending.delete(key) };
}
const t = timers(); let fail = false;
const changes = [];
const ctl = createBridgeController({ getCapabilities: async () => { if (fail) throw new DesktopBridgeError("SIDECAR_CRASHED"); return accepted; }, loadPersonalMark: async () => readTyped }, { ...t, onChange: (s) => changes.push(s) });
check(ctl.state.phase === "STARTING", "UI begins without false-ready state");
check(await ctl.refresh(), "valid start/ready/read");
check(ctl.state.phase === "READY" && ctl.state.read === readTyped, "read state retained after success");
check(t.pending.size === 0, "idle UI consumes no sidecar request budget");
fail = true;
check(!(await ctl.refresh({ loadMark: false })), "crash request fails");
check(ctl.state.phase === "FAILED" && ctl.state.read === null && ctl.state.capabilities === null, "crash clears success and stale profile");
check(t.pending.size === 0 && !(await ctl.refresh()), "no auto restart or manual request replay in failed session");
ctl.dispose();

let release;
const t2 = timers();
const pendingAdapter = { getCapabilities: () => new Promise((resolve) => { release = resolve; }), loadPersonalMark: async () => readTyped };
const ctl2 = createBridgeController(pendingAdapter, t2);
const first = ctl2.refresh();
await Promise.resolve();
check(!(await ctl2.refresh()), "duplicate UI refresh does not enqueue native requests");
const timeout = [...t2.pending.values()][0]; timeout.fn();
await first;
check(ctl2.state.phase === "FAILED", "UI watchdog fails closed");
release(accepted); await Promise.resolve(); await Promise.resolve();
check(ctl2.state.phase === "FAILED" && ctl2.state.read === null, "late success cannot resurrect failed UI");
ctl2.dispose();

const t3 = timers();
const ctl3 = createBridgeController(pendingAdapter, t3);
const closing = ctl3.refresh(); await Promise.resolve(); ctl3.dispose(); release(accepted); await closing;
check(ctl3.state.phase === "STARTING" && t3.pending.size === 0, "closed UI rejects late completion and clears timers");

const keys = Object.keys(DESKTOP_MESSAGES.ja).sort();
for (const locale of ["ja", "en", "zh-CN", "zh-TW", "ko"]) {
  assert.deepEqual(Object.keys(DESKTOP_MESSAGES[locale]).sort(), keys); ++checks;
  const model = bridgePresentationModel({ phase: "READY", read: readTyped }, locale);
  check(!JSON.stringify(model).includes("Private name"), "stored typed text never falls back to UI font rendering");
  check(model.detail.includes("shirushi-typed@1"), "profile generation remains visible");
  check(bridgePresentationModel({ phase: "FAILED", read: null }, locale).summary === DESKTOP_MESSAGES[locale].notLoaded, "failed presentation shows no stale loaded claim");
}
check(bridgePresentationModel({ phase: "READY", read: readLegacy }, "en").summary.includes("unknown"), "legacy geometry uncertainty displayed");

const web = resolve(dirname(fileURLToPath(import.meta.url)), "..");
async function graph(entry) {
  const files = new Set();
  async function visit(path) {
    if (files.has(path)) return; files.add(path);
    const source = await readFile(path, "utf8");
    for (const match of source.matchAll(/(?:import|export)\s+(?:[^"']*?\s+from\s+)?["'](\.[^"']+)["']/g)) await visit(resolve(dirname(path), match[1]));
  }
  await visit(resolve(web, entry)); return [...files].map((x) => relative(web, x).replaceAll("\\", "/"));
}
const normal = await graph("index.js");
check(!normal.some((name) => name.startsWith("desktop/") || name.includes("desktop-adapter") || name.startsWith("personal-mark-v2/")), "normal Browser import graph isolated from native and formal v2 contracts");
const desktop = await graph("desktop.js");
check(desktop.includes("bootstrap.js") && desktop.includes("shell.js") && desktop.includes("motion.js"), "Desktop shares original UI components");
check(!desktop.some((name) => name.startsWith("dev/") || name.includes("dev-preview")), "Desktop excludes preview fixtures/imports");
console.log(`PASS Desktop adapter/presentation: ${checks} checks, 5 locales`);
