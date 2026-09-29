import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import { dirname, extname, join, normalize, relative, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { BrowserFoundationAdapter } from "./adapters/browser-foundation-adapter.js";
import { DevPreviewAdapter } from "./adapters/dev-preview-adapter.js";
import { markPreviewText } from "./bootstrap.js";
import { operationEnabled } from "./contracts.js";
import { GenerationGuard, MotionScheduler } from "./generation-guard.js";
import { MESSAGES, SUPPORTED_LOCALES } from "./i18n.js";
import { blankPersonalMarkDraft, sanitizeMark } from "./mark.js";
import { motionCopyKeys, NORMAL_MOTION_MS, REDUCED_MOTION_MS, resetMotionClasses, shouldReduceMotion } from "./motion.js";

const here = dirname(fileURLToPath(import.meta.url));
let checks = 0;
const check = (condition, message) => { assert.ok(condition, message); checks += 1; };

async function importGraph(entry) {
  const visited = new Set();
  async function visit(file) {
    const absolute = normalize(file);
    if (visited.has(absolute)) return;
    visited.add(absolute);
    const source = await readFile(absolute, "utf8");
    const pattern = /(?:import|export)\s+(?:[^"']*?\s+from\s+)?["'](\.[^"']+)["']/g;
    for (const match of source.matchAll(pattern)) {
      let target = resolve(dirname(absolute), match[1]);
      if (!extname(target)) target += ".js";
      await visit(target);
    }
  }
  await visit(resolve(here, entry));
  return visited;
}

const normalGraph = await importGraph("index.js");
const normalNames = [...normalGraph].map((file) => relative(here, file).replaceAll("\\", "/"));
check(!normalNames.some((name) => name.startsWith("dev/") || name.includes("dev-preview")), "normal graph excludes development modules");
const normalText = (await Promise.all([...normalGraph].map((file) => readFile(file, "utf8")))).join("\n");
for (const forbidden of ["Niki", "Mori", "PREVIEW_PROVENANCE", "f1-approved-isolated-dev-fixture", "canary-file-snapshot", "demo-art.svg"]) {
  check(!normalText.includes(forbidden), `normal graph excludes fixture/sample token ${forbidden}`);
}

const browser = new BrowserFoundationAdapter();
check(browser.loadSessionMark() === null, "normal adapter starts without another person's mark");
check(Object.values(browser.capabilities).every((value) => typeof value === "boolean"), "all normal capabilities are explicit booleans");
check(browser.capabilities.coreAdd === false && browser.capabilities.coreVerify === false && browser.capabilities.coreReadback === false, "normal Core capabilities are false");
check((await browser.addMark({})).status === "UNSUPPORTED", "direct normal add cannot report success");
check((await browser.readFileMark({})).status === "UNSUPPORTED", "direct normal readback cannot report success");
check((await browser.verifyFileMark({})).status === "UNSUPPORTED", "direct normal verify cannot report verified");
check(!operationEnabled(browser.capabilities, "add", { hasImage: true, hasMark: true }), "capability gate disables normal Add");
check(!operationEnabled(browser.capabilities, "verify", { hasImage: true }), "capability gate disables normal Verify");

const draft = blankPersonalMarkDraft();
draft.typed = "Session only";
const saved = browser.saveSessionMark(draft);
check(saved.typed === "Session only", "session mark editing uses the real adapter contract");
check(new BrowserFoundationAdapter().loadSessionMark() === null, "session mark is not persisted into a new adapter");
check(saved.handwritten.coordinateSpace.width === 480 && saved.handwritten.coordinateSpace.height === 220, "session view model preserves source coordinateSpace");
check(sanitizeMark({ ...saved, handwritten: { ...saved.handwritten, coordinateSpace: { width: 0, height: 220 } } }) === null, "invalid visual coordinateSpace fails closed");
const originalCreateObjectUrl = URL.createObjectURL;
const originalRevokeObjectUrl = URL.revokeObjectURL;
const revoked = [];
let objectUrlIndex = 0;
try {
  URL.createObjectURL = () => `blob:local-${++objectUrlIndex}`;
  URL.revokeObjectURL = (url) => revoked.push(url);
  const imageAdapter = new BrowserFoundationAdapter();
  check(imageAdapter.imageFromFile({ type: "image/png", name: "one.png" }).url === "blob:local-1", "local preview creates an object URL without upload");
  imageAdapter.imageFromFile({ type: "image/jpeg", name: "two.jpg" });
  imageAdapter.releaseImage();
  assert.deepEqual(revoked, ["blob:local-1", "blob:local-2"], "replacement and release must revoke both object URLs");
  checks += 1;
} finally {
  URL.createObjectURL = originalCreateObjectUrl;
  URL.revokeObjectURL = originalRevokeObjectUrl;
}
for (const networkPrimitive of ["fetch(", "XMLHttpRequest", "sendBeacon", "FormData"]) {
  check(!normalText.includes(networkPrimitive), `normal graph contains no upload primitive ${networkPrimitive}`);
}

const dev = new DevPreviewAdapter();
check(dev.capabilities.coreAdd === false && dev.capabilities.coreVerify === false && dev.capabilities.coreReadback === false, "development preview does not claim Core capabilities");
check((await dev.readFileMark({})).status === "UNSUPPORTED", "development preview does not simulate Core readback");
check(operationEnabled(dev.capabilities, "add", { hasImage: true, hasMark: true }), "development preview gate enables isolated Add motion");
check(operationEnabled(dev.capabilities, "verify", { hasImage: true }), "development preview gate enables isolated Verify motion");
const addPreview = await dev.addMark({ mark: dev.loadSessionMark() });
check(addPreview.status === "PREVIEW_ADD" && addPreview.status !== "SUCCESS", "development Add outcome is explicitly preview-only");
check(addPreview.provenance === "f1-approved-isolated-dev-fixture", "development Add carries fixture provenance");

const beforeEdit = await dev.verifyFileMark({ targetReference: "target-A", options: { fixtureMode: "typed" } });
const edited = dev.loadSessionMark();
edited.typed = "Changed profile";
dev.saveSessionMark(edited);
const afterEdit = await dev.verifyFileMark({ targetReference: "target-A", options: { fixtureMode: "typed" } });
check(beforeEdit.status === "PREVIEW_VERIFY" && beforeEdit.status !== "VERIFIED", "development Verify outcome is explicitly preview-only");
check(beforeEdit.mark.typed === "Mori" && afterEdit.mark.typed === "Mori", "Verify fixture is independent of current profile edits");
check(afterEdit.mark.typed !== dev.loadSessionMark().typed, "returned file fixture remains distinct from edited profile");
check(afterEdit.provenance === "f1-approved-isolated-dev-fixture", "development Verify carries fixture provenance");
const rejectedShape = await dev.verifyFileMark({ targetReference: "target-A", options: {}, mark: dev.loadSessionMark() });
check(rejectedShape.status === "ERROR" && rejectedShape.reason === "invalid_verify_request_shape", "Verify rejects profile-mark request leakage");

const guard = new GenerationGuard();
const first = guard.next();
const second = guard.next();
check(!guard.accepts(first) && guard.accepts(second), "generation guard rejects stale image completion");
let timerId = 0;
const callbacks = new Map();
const scheduler = new MotionScheduler((callback) => { timerId += 1; callbacks.set(timerId, callback); return timerId; }, () => {});
let staleRan = false;
const generation = scheduler.begin();
scheduler.schedule(generation, () => { staleRan = true; }, 10);
const staleCallback = callbacks.get(timerId);
scheduler.begin();
staleCallback();
check(staleRan === false, "cancel/restart guard rejects an already-queued stale async callback");
let currentRan = false;
const currentGeneration = scheduler.begin();
scheduler.schedule(currentGeneration, () => { currentRan = true; }, 10);
callbacks.get(timerId)();
check(currentRan === true, "latest overlapping action remains executable after stale cancellation");

const classNames = new Set(["playing", "motion-add", "unrelated"]);
resetMotionClasses({ remove: (...names) => names.forEach((name) => classNames.delete(name)) });
check(!classNames.has("playing") && !classNames.has("motion-add") && classNames.has("unrelated"), "rapid replay clears only prior motion state classes");
check(motionCopyKeys("verify").complete === "motionVerify" && motionCopyKeys("add").complete === "motionAdd", "Add and Verify completion labels stay distinct");
check(markPreviewText(null, (key) => ({ noMark: "Not set" })[key]) === "Not set", "empty mark label follows the active translator");

check(NORMAL_MOTION_MS === 5250 && REDUCED_MOTION_MS === 2200, "approved normal and reduced motion durations are preserved");
check(shouldReduceMotion("reduce", false) && !shouldReduceMotion("normal", true), "query override deterministically controls reduced motion review");
check(shouldReduceMotion(null, true) && !shouldReduceMotion(null, false), "system reduced-motion preference is honored without override");

const jaKeys = Object.keys(MESSAGES.ja).sort();
check(SUPPORTED_LOCALES.length === 5, "five locales are present");
for (const locale of SUPPORTED_LOCALES) {
  assert.deepEqual(Object.keys(MESSAGES[locale]).sort(), jaKeys, `${locale} locale keys must match Japanese`);
  checks += 1;
}

const allFiles = [];
async function walk(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const full = join(directory, entry.name);
    if (entry.isDirectory()) await walk(full);
    else allFiles.push(full);
  }
}
await walk(here);
const css = await readFile(join(here, "styles.css"), "utf8");
check(css.includes("@media (prefers-reduced-motion:reduce)") && css.includes(".reduced .motion-handwritten"), "CSS retains reduced-motion/static handwritten treatment");
check(allFiles.some((file) => file.endsWith("dev-preview.html")), "explicit development preview entry exists");

console.log(`PASS web foundation: ${checks} contract/invariant checks, ${SUPPORTED_LOCALES.length} locales, ${jaKeys.length} keys`);
