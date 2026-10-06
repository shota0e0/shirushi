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
check(draft.mode === "handwritten", "new Personal Mark defaults to handwritten");
draft.mode = "typed";
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
check(dev.loadSessionMark().mode === "handwritten", "development profile defaults to handwritten");
for (const fixtureMode of ["typed", "handwritten"]) {
  const unmarked = await dev.verifyFileMark({ targetReference: "local:unmarked", options: { fixtureMode } });
  check(unmarked.status === "PREVIEW_UNMARKED" && !unmarked.mark, "fixture selection cannot mark an arbitrary unmarked image");
}
const addPreview = await dev.addMark({ targetReference: "local:applied", mark: dev.loadSessionMark() });
check(addPreview.status === "PREVIEW_ADD" && addPreview.status !== "SUCCESS", "development Add outcome is explicitly preview-only");
check(addPreview.provenance === "f1-approved-isolated-dev-fixture", "development Add carries fixture provenance");

const beforeEdit = await dev.verifyFileMark({ targetReference: "dev-target-fixture", options: { fixtureMode: "typed" } });
const edited = dev.loadSessionMark();
edited.typed = "Changed profile";
dev.saveSessionMark(edited);
const afterEdit = await dev.verifyFileMark({ targetReference: "dev-target-fixture", options: { fixtureMode: "typed" } });
check(beforeEdit.status === "PREVIEW_VERIFY" && beforeEdit.status !== "VERIFIED", "development Verify outcome is explicitly preview-only");
check(beforeEdit.mark.typed === "Mori" && afterEdit.mark.typed === "Mori", "Verify fixture is independent of current profile edits");
check(afterEdit.mark.typed !== dev.loadSessionMark().typed, "returned file fixture remains distinct from edited profile");
check(afterEdit.provenance === "f1-approved-isolated-dev-fixture", "development Verify carries fixture provenance");
const appliedVerify = await dev.verifyFileMark({ targetReference: "local:applied", options: { fixtureMode: "typed" } });
check(JSON.stringify(appliedVerify.mark) === JSON.stringify(addPreview.mark), "Verify reads exact applied snapshot, not fixture selector/current profile");
appliedVerify.mark.handwritten.strokes.length = 0;
check((await dev.verifyFileMark({ targetReference: "local:applied" })).mark.handwritten.strokes.length > 0, "returned marks cannot mutate target state");
check((await dev.verifyFileMark({ targetReference: "local:replacement" })).status === "PREVIEW_UNMARKED", "replacement target has no inherited applied mark");
dev.releaseImage();
check((await dev.verifyFileMark({ targetReference: "local:applied" })).status === "PREVIEW_UNMARKED", "image release forgets preview-only applied state");
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
assert.deepEqual(MESSAGES.ja.eyebrow.split("\n"), ["あなたの作品に、", "あなたの意思を"], "Japanese eyebrow has exactly two approved lines");
checks += 1;
check(MESSAGES.ja.headline === "作品にあなたのしるしを", "Japanese headline has no comma or final punctuation");
check(css.includes(":lang(ja) .intro { grid-template-columns: minmax(0,1fr); column-gap: 0; }"), "Japanese headline track uses full shared image-content column, not the former right-hand track");
check(css.includes(":lang(ja) .intro > .eyebrow { grid-area: 1 / 1; justify-self: start; }"), "subcopy remains at the shared column's left edge");
const introRules = css.match(/\.intro \{([^}]*)\}/)?.[1] || "";
const eyebrowRules = css.match(/\.eyebrow \{([^}]*)\}/)?.[1] || "";
check(introRules.includes("align-items: center;") && eyebrowRules.includes("margin: 0;") && !/(?:^|;)\s*(?:transform|top|translate):/.test(introRules + eyebrowRules), "wide header aligns actual text block centers without margin bias or positional nudges");
check(css.includes('.intro { display: block; margin: 13px 0 14px; }') && css.includes('.intro > .eyebrow { margin-bottom: 6px; }'), "narrow header retains its stacked layout and six-pixel text separation");
const headlineAlignment = css.match(/:lang\(ja\) \.intro > h1 \{([^}]*)\}/)?.[1] || "";
check(headlineAlignment.includes("grid-area: 1 / 1;") && headlineAlignment.includes("width: 100%;") && headlineAlignment.includes("text-align: center;"), "headline is centered in the same content column that centers the responsive image frame");
check(!/\b(?:left|right|transform|margin-left|margin-right):/.test(headlineAlignment), "headline alignment requires no fixed positional correction or transform");
check(css.includes(".image-stage { height: var(--stage-height); display: grid; place-items: center; }") && css.includes("grid-area: 1 / 1; width: 100%; height: 100%;"), "empty and selected image content share a stable responsive stage");
check(css.includes(".mark-summary { min-height: 84px;") && css.includes("minmax(190px,.65fr)"), "summary and both action slots reserve consistent layout space");
check(css.includes("container-type: inline-size;") && css.includes("@container (max-width:260px)") && css.includes(".motion-rights { top: 62%; width: 88%;"), "small portrait frame adjusts copy layout without changing mark/motion timing");
check(MESSAGES.ja.addAgain === "しるしを見る" && MESSAGES.ja.verifyMark === "しるしを確認する" && MESSAGES.ja.verifyAgain === MESSAGES.ja.verifyMark, "Japanese replay and Verify describe distinct purposes without internal again-state wording");
check(SUPPORTED_LOCALES.every((locale) => MESSAGES[locale].verifyAgain === MESSAGES[locale].verifyMark && MESSAGES[locale].addAgain !== MESSAGES[locale].verifyMark), "all existing locales preserve view-versus-check distinction and stable Verify label");
check(/\.motion-rights \{[^}]*width: max-content;[^}]*max-width: calc\(100% - 20px\);[^}]*white-space: nowrap;/.test(css) && /\.rights-intent \{[^}]*white-space: nowrap;/.test(css), "standard overlay and intent summary preserve complete single-line label");
check(/@container \(max-width:260px\) \{\s*\.motion-rights \{[^}]*font-size: \.68rem;[^}]*white-space: normal;[^}]*text-wrap: balance;/.test(css), "only narrow image containers allow readable balanced full-copy fallback without extreme shrinking");
const rightsRules = css.match(/\.motion-rights \{[^}]*\}/g) || [];
check(rightsRules.every((rule) => !/text-overflow|ellipsis|overflow:\s*hidden/.test(rule)) && MESSAGES.ja.rightsIntent === "AI学習・生成利用を希望しない", "intent copy remains complete with no ellipsis or clipping rule");
check(css.includes(".language-control:hover, .language-control:focus-within") && css.includes("appearance: none;") && css.includes(".language-chevron"), "language selector has quiet pointer/keyboard affordance without replacing native semantics");
const shell = await readFile(join(here, "shell.js"), "utf8");
const bootstrap = await readFile(join(here, "bootstrap.js"), "utf8");
const emptyShell = shell.split('id="emptyStage"')[1].split('id="selectedStage"')[0];
check(emptyShell.includes('class="image-entry-motion"') && emptyShell.includes('aria-hidden="true" focusable="false"'), "image receptacle is decorative inline SVG, not another keyboard target");
const receiverPath = emptyShell.match(/<path class="entry-frame" d="([^"]+)"\/>/)?.[1] || "";
check(emptyShell.includes('class="entry-card"') && receiverPath === 'M36 52v-8h8M84 44h8v8M36 84v8h8M84 92h8v-8', "recognizable photo card seats inside four open image-frame corners");
check((receiverPath.match(/M/g) || []).length === 4 && !/[zZ]/.test(receiverPath) && !/<rect class="entry-frame"|entry-frame-guides|entry-lip|entry-container/.test(emptyShell), "receiver has only four open corner strokes, no enclosing box/fill or tray lip");
check(css.includes('.entry-frame { fill: none; stroke: #b8b8af; stroke-width: 1;') && emptyShell.includes('<rect x="43" y="4" width="42" height="36" rx="3"/>'), "thin unfilled receiver keeps original photo-card geometry visually dominant");
check(emptyShell.includes('<circle cx="73" cy="14"') && emptyShell.includes('M48 32l10-11 7 8 5-5 10 8'), "incoming card retains explicit sun/mountain photo glyph");
check(emptyShell.includes('class="image-entry-label" data-i18n="selectImage"') && !emptyShell.includes('type="button" data-i18n="selectImage"'), "localized chooser label cannot replace decorative SVG on locale change");
check(emptyShell.includes('data-i18n="emptyHint"') && !emptyShell.includes('data-i18n="emptyBody"') && shell.includes('class="details-body" data-i18n="emptyBody"'), "empty copy stays short while full non-upload Preview explanation remains in details");
check(MESSAGES.ja.emptyHint === "ローカルでPreview" && SUPPORTED_LOCALES.every((locale) => MESSAGES[locale].emptyHint.length < 32), "short supporting copy is localized and does not claim actual file processing");
check(css.includes('.empty-stage[data-entry-motion="normal"] .entry-card { animation: image-entry-card 4200ms cubic-bezier(.4,0,.2,1) infinite; }'), "entry loop is 4.2 seconds with soft easing, scoped away from Personal Mark");
const entryKeyframes = css.split("@keyframes image-entry-card {")[1].split("@keyframes image-entry-pulse")[0];
check(entryKeyframes.includes("34%, 40%") && entryKeyframes.includes("55%") && entryKeyframes.includes("62%, 88%") && entryKeyframes.includes("98%, 100%") && !/scale|width:|height:/.test(entryKeyframes), "longer tiny anticipation, soft settle, hold and quiet reset use geometry-preserving transforms only");
check(entryKeyframes.includes("translateY(47.5px) rotate(.6deg)") && entryKeyframes.includes("translateY(45.5px) rotate(-.2deg)"), "entry settles within two pixels with no exaggerated bounce or distortion");
check(css.includes('.empty-stage[data-entry-motion="reduce"] .entry-card { animation: none; opacity: 1; }') && css.includes('.empty-stage[data-entry-motion="reduce"] .entry-pulse { animation: none; opacity: 0; }'), "reduced entry is a visible static seated card without pulse");
check(css.includes("transform: translateY(46px) rotate(0deg);") && css.includes(".image-entry-motion { width: 128px; height: 112px;"), "static card is aligned inside image frame with unchanged fixed icon footprint");
const cardGeometry = emptyShell.match(/<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)" rx="3"\/>/)?.slice(1).map(Number) || [];
const seatedTranslation = Number(css.match(/\.entry-card \{[^}]*translateY\(([\d.]+)px\)/)?.[1]);
const settleTranslation = Math.max(...[...entryKeyframes.matchAll(/translateY\(([-\d.]+)px\)/g)].map((match) => Number(match[1])));
check(cardGeometry.length === 4 && cardGeometry[0] > 36 && cardGeometry[0] + cardGeometry[2] < 92 && cardGeometry[1] + seatedTranslation > 44 && cardGeometry[1] + cardGeometry[3] + settleTranslation < 92, "actual seated photo geometry and bounded settle remain inside corner-defined receiver");
const receiverKeyframes = css.split('@keyframes image-entry-frame {')[1].split('}')[0];
check(css.includes('.empty-stage[data-entry-motion="normal"] .entry-frame { animation: image-entry-frame 4200ms ease-out infinite; }') && receiverKeyframes.includes('opacity: .65;') && !/transform|filter|width|height|stroke-width/.test(css.split('@keyframes image-entry-frame {')[1].split('.empty-stage[data-entry-motion="reduce"] .entry-frame')[0]), "corners react through restrained opacity only, synchronized with unchanged loop");
check(css.includes('.empty-stage[data-entry-motion="reduce"] .entry-frame { animation: none; opacity: 1; transition: none; }') && css.includes('.empty-stage:not([data-entry-motion="normal"]) .entry-frame { animation: none; opacity: 1; }'), "explicit and OS reduced motion retain static visible corners with no loop");
check(css.includes("height: 100%; min-width: 0; min-height: 0;") && css.includes(".empty-stage { padding: 12px; }") && css.includes(".image-entry-motion { width: 120px; height: 105px; }"), "narrow minimum-height stage does not expand to the icon's grid min-content size");
check(!/image-entry[^\n]*(?:hover|focus)[^\n]*animation:/.test(css), "hover/focus styling never replays entry animation");
check(css.includes("padding: 4px 18px; color: #65635a;") && css.includes("background: #eeeee7; font-size: .62rem;") && shell.includes('id="devBanner" hidden role="note"'), "development warning remains identifiable but compact and neutral rather than dominant brown");
check(css.includes('.primary-actions > .button::before, .primary-actions > .button::after { content: none; }') && css.includes('.primary-actions > .button:disabled .cta-icon') && css.includes(".verify-button:active:not(:disabled)"), "E icons replace capsule bars with explicit disabled/press feedback");
check(css.includes(".entry-frame { transition: none; }") && css.includes('.workspace:has(.empty-stage[data-entry-motion="reduce"])'), "OS and Preview reduced motion suppress frame/CTA movement");
const ctaGeometry = css.match(/\.primary-actions > \.button \{([^}]*)\}/)?.[1] || "";
check(ctaGeometry.includes("min-height: 47px;") && ctaGeometry.includes("padding: 0 22px 0 42px;") && ctaGeometry.includes("font-size: .75rem;") && ctaGeometry.includes("border-radius: 3px;"), "both E CTAs share unchanged height/type/spacing with near-rectangular corners");
for (const [id, iconClass] of [["addButtonIcon", "cta-pen"], ["verifyButtonIcon", "cta-inspect"]]) {
  check(shell.includes(`class="cta-icon ${iconClass}" id="${id}"`) && new RegExp(`<svg[^>]*id="${id}"[^>]*aria-hidden="true" focusable="false"`).test(shell), "purpose-specific CTA icon is decorative, not another focus/action target");
}
check(shell.includes('id="verifyButtonLabel"') && !/elements\.verifyButton\.textContent\s*=/.test(bootstrap), "Verify label refresh cannot erase its SVG or change button authority");
check(css.includes('transform: translate(4px,-50%) rotate(-6deg);') && css.includes('transform: translate(4px,-50%) rotate(3deg);') && !/\.primary-actions[^\n]*animation:/.test(css), "icons nudge four pixels with tiny independent tilt and no looping animation");
check(!ctaGeometry.includes("clip-path") && !shell.includes('class="button-spark"'), "CTA focus/hit areas stay intact without clipping or stacked spark motifs");
const summaryShell = shell.split('id="markSummary"')[1].split('id="primaryActions"')[0];
check(!/statusLine|capabilityNote|motionComplete/.test(summaryShell), "Personal Mark summary contains no processing/completion comment");
check(shell.indexOf('id="actionFeedback"') > shell.indexOf('id="primaryActions"') && shell.indexOf('id="statusLine"') > shell.indexOf('id="actionFeedback"'), "dedicated feedback region follows action buttons, not signature row");
check(!shell.includes('id="motionComplete"') && shell.includes('id="statusLine" role="status" aria-live="polite"'), "completion has one external live status source, not a duplicate over the image mark");
check(css.includes(".action-feedback { min-height: 64px;") && /\.status-line \{[^}]*position:/.test(css) === false && css.includes("font-weight: 500; letter-spacing: .025em;"), "quiet feedback reserves normal-flow space and cannot overlay the signature");
check(shell.includes('class="language-globe" aria-hidden="true"') && shell.includes('data-i18n-aria="languageLabel"'), "decorative language icon accompanies localized accessible native selector");
check((shell.match(/<option value=/g) || []).length === SUPPORTED_LOCALES.length, "language menu offers exactly existing supported locales");
check(/:lang\(ja\) \.eyebrow \{[^}]*white-space: pre;[^}]*font-size: \.7rem;[^}]*line-height: 1\.65;/.test(css), "Japanese two-line copy grows about 1.18x with readable leading");
check(!/Shirushi Dot|DotGothic16|@font-face/.test(css), "retracted pixel font has no active CSS font declaration or reference");
check(/:lang\(ja\) \.intro h1 \{[^}]*"BIZ UDPGothic", "BIZ UDGothic", "Noto Sans JP", "Yu Gothic UI", Meiryo, sans-serif;[^}]*font-size: 30px;[^}]*font-weight: 600;[^}]*letter-spacing: \.025em;[^}]*line-height: 1\.2;/.test(css), "headline uses safe Japanese Gothic stack and medium editorial styling");
check(/\.empty-stage \{[^}]*border: 1px solid #d4d4ce;/.test(css), "empty image area uses a light 1px rule");
check(/\.image-surface \{[^}]*border: 1px solid #d4d4ce;/.test(css), "selected image frame retains the same light 1px rule");
check(css.includes(".empty-stage:hover, .empty-stage:focus-within { border-color: #aaa9a3; }"), "empty-area emphasis is limited to hover and keyboard engagement");
check(!/@import|https?:\/\//.test(css), "CSS requires no runtime remote font service");
check(/\.add-button:not\(:disabled\) \{[^}]*color: #30342b;[^}]*border-color: #918a7f;[^}]*background: #fcfaf6;/.test(css) && /\.verify-button \{[^}]*border: 1px solid #b8b9b1;[^}]*background: #fff;/.test(css), "quiet E pair keeps warm off-white primary/stronger border and white/light-gray secondary");
check(css.includes('--cta-accent: #bf855c; --cta-icon-response: #b4774c;') && css.includes('--cta-accent: #aa9682; --cta-icon-response: #a1836a;') && css.includes('stroke: var(--cta-accent, var(--accent));'), "per-CTA muted signature accents preserve orange identity without globally recoloring the UI");
check(css.includes('background: #f8f3eb;') && css.includes('background: #fcfbf8;') && css.includes('color: var(--cta-icon-response, var(--accent-strong)); transform: translate(4px,-50%);'), "hover tint remains stronger for Add while the accepted icon motion stays unchanged");
check(/\.status-line \{[^}]*color: #70706a;[^}]*font-size: \.6rem;/.test(css), "status is smaller and lower contrast while retaining separate reserved space");
for (const interaction of ["hover", "focus-visible", "active"]) {
  check(css.includes(`.add-button:${interaction}:not(:disabled)`), `CTA has explicit ${interaction} feedback without changing disabled behavior`);
}
check(css.includes("outline: 2px solid var(--accent); outline-offset: 3px;"), "existing keyboard focus outline remains visible");
const ctaReducedRules = css.split("@media (prefers-reduced-motion:reduce) {")[1].split("@media")[0];
check(ctaReducedRules.includes(".primary-actions .cta-icon { transition: none; }") && ctaReducedRules.includes(".primary-actions > .button:active:not(:disabled) { transform: none; }") && ctaReducedRules.includes("transform: translateY(-50%); filter: none;"), "OS reduced motion keeps icon positioning but removes interaction movement/transitions");
check(css.includes('.workspace:has(.empty-stage[data-entry-motion="reduce"]) .primary-actions .cta-icon { transition: none; transform: translateY(-50%); filter: none; }'), "Preview reduced override also suppresses icon motion without shifting its baseline");
check(css.includes('.workspace:has(.empty-stage[data-entry-motion="normal"]) .primary-actions .cta-icon { transition: transform 180ms') && css.includes('.workspace:has(.empty-stage[data-entry-motion="normal"]) .primary-actions > .button:focus-visible:not(:disabled) .cta-pen { transform: translate(4px,-50%) rotate(-6deg); }'), "existing resolved normal Preview mode opts CTA into the same restrained interaction motion");
const ctaNormalRules = css.split('/* Reuse the existing resolved Preview motion mode;')[1].split('/* The existing Preview reduce override')[0];
check(ctaNormalRules.indexOf('rotate(3deg);') < ctaNormalRules.indexOf(':active:not(:disabled) .cta-icon { transform: translate(2px,-50%); filter: none; }') && bootstrap.includes('shouldReduceMotion(queryMotion, mediaReduce.matches) ? "reduce" : "normal"'), "resolved mode preserves OS/query reduction and press wins over hovered icon motion");
const luminance = (hex) => {
  const channels = hex.match(/\w\w/g).map((part) => parseInt(part, 16) / 255).map((v) => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4);
  return channels[0] * .2126 + channels[1] * .7152 + channels[2] * .0722;
};
const ctaRules = css.split("\n").filter((line) => /^\.(add|verify)-button/.test(line) && line.includes("background:"));
check(ctaRules.length === 6, "normal, hover/focus and pressed backgrounds of both E CTAs are audited");
for (const rule of ctaRules) {
  let background = rule.match(/background: #([0-9a-f]{3,6});/)?.[1];
  if (background?.length === 3) background = [...background].map((c) => c + c).join("");
  const ink = rule.startsWith(".add-button") ? "30342b" : "52574b";
  check(background && !rule.includes("gradient") && (luminance(background) + .05) / (luminance(ink) + .05) >= 4.5, "dark E CTA labels retain sufficient contrast for rest/hover/press tints");
}
check(css.includes("@media (prefers-reduced-motion:reduce)") && css.includes(".reduced .motion-handwritten"), "CSS retains reduced-motion/static handwritten treatment");
check(allFiles.some((file) => file.endsWith("dev-preview.html")), "explicit development preview entry exists");

console.log(`PASS web foundation: ${checks} contract/invariant checks, ${SUPPORTED_LOCALES.length} locales, ${jaKeys.length} keys`);
await import("./ui-flow.verify.mjs");
