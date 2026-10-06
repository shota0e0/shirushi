// Deterministic event/DOM/timer doubles exercise the real shared bootstrap.
// No native transport, browser automation, file mutation or sleep races.
import assert from "node:assert/strict";
import { bootstrapShirushi } from "./bootstrap.js";
import { BrowserFoundationAdapter } from "./adapters/browser-foundation-adapter.js";
import { freezeCapabilities } from "./contracts.js";
import { previewProfileFixture, verificationFixture } from "./dev/fixtures.js";
import { clone, sanitizeMark } from "./mark.js";
import { DRAW_MOTION_MS, handwrittenMotionBounds, renderMotionMark } from "./motion.js";
import { readFile } from "node:fs/promises";

let checks = 0;
const check = (value, label) => { assert.ok(value, label); checks += 1; };
class Element {
  constructor() {
    this.dataset = {}; this.attributes = {}; this.listeners = new Map();
    this.children = []; this.hidden = false; this.disabled = false; this.value = "";
    this.classes = new Set();
    this.classList = {
      add: (...names) => names.forEach((name) => this.classes.add(name)),
      remove: (...names) => names.forEach((name) => this.classes.delete(name)),
      toggle: (name, force) => force ? this.classes.add(name) : this.classes.delete(name),
    };
    this.style = { setProperty(name, value) { this[name] = value; }, removeProperty(name) { delete this[name]; } };
  }
  setAttribute(name, value) { this.attributes[name] = value; }
  append(child) { this.children.push(child); }
  replaceChildren(...children) { this.children = children; }
  addEventListener(name, callback) {
    if (!this.listeners.has(name)) this.listeners.set(name, []);
    this.listeners.get(name).push(callback);
  }
  async fire(name, fields = {}) {
    const event = { preventDefault() {}, ...fields };
    await Promise.all((this.listeners.get(name) || []).map((fn) => fn(event)));
  }
  showModal() { this.open = true; }
  close() { this.open = false; if (this.queueClose) this.queueClose(() => this.fire("close")); else void this.fire("close"); }
  setCustomValidity(message) { this.validity = message; }
  reportValidity() {}
  click() { this.clickCount = (this.clickCount || 0) + 1; return this.fire("click"); }
}

class TestAdapter extends BrowserFoundationAdapter {
  capabilities = freezeCapabilities({ sessionMarkEdit: true, localImagePreview: true, previewAddMotion: true, previewVerifyMotion: true });
  constructor(mark = previewProfileFixture()) {
    super(mark); this.targets = new Map(); this.calls = []; this.nextAdd = null; this.nextVerify = null;
  }
  imageFromFile(file) { this.targets.clear(); return { url: "blob:test", name: file.name }; }
  async addMark(request) {
    this.calls.push({ kind: "add", request: clone(request) });
    const result = this.nextAdd;
    this.nextAdd = null;
    if (result) return result;
    this.targets.set(request.targetReference, clone(request.mark));
    return { status: "PREVIEW_ADD", mark: clone(request.mark) };
  }
  async verifyFileMark(request) {
    this.calls.push({ kind: "verify", request: clone(request) });
    const result = this.nextVerify;
    this.nextVerify = null;
    if (result) return result;
    const mark = this.targets.get(request.targetReference);
    return mark ? { status: "PREVIEW_VERIFY", mark: clone(mark) } : { status: "PREVIEW_UNMARKED" };
  }
}

function setup(adapter = new TestAdapter(), reduced = false, queuedClose = false, queryMotion = "") {
  const root = new Element();
  const nodes = {};
  Object.defineProperty(root, "innerHTML", { set(html) {
    for (const tag of html.matchAll(/<[^>]+>/g)) {
      const id = /\bid="([^"]+)"/.exec(tag[0])?.[1];
      if (!id) continue;
      const node = nodes[id] = new Element();
      for (const item of tag[0].matchAll(/data-([\w-]+)="([^"]+)"/g)) {
        node.dataset[item[1].replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = item[2];
      }
      node.hidden = /\bhidden\b/.test(tag[0]);
    }
    const canvas = nodes.drawingCanvas;
    canvas.width = 480; canvas.height = 220; canvas.parentElement = new Element();
    canvas.captures = new Set();
    canvas.hasPointerCapture = (id) => canvas.captures.has(id);
    canvas.setPointerCapture = (id) => canvas.captures.add(id);
    canvas.releasePointerCapture = (id) => canvas.captures.delete(id);
    canvas.getBoundingClientRect = () => ({ left: 0, top: 0, width: 480, height: 220 });
    canvas.renderedStrokes = 0;
    canvas.getContext = () => ({
      clearRect() { canvas.renderedStrokes = 0; }, beginPath() {}, moveTo() {}, lineTo() {}, arc() {},
      fill() { canvas.renderedStrokes += 1; }, stroke() { canvas.renderedStrokes += 1; },
    });
    nodes.previewImage.naturalWidth = 800; nodes.previewImage.naturalHeight = 600;
  } });
  root.querySelector = (query) => nodes[query.slice(1)];
  root.querySelectorAll = (query) => Object.values(nodes).filter((node) => {
    const key = { "[data-mark-mode]": "markMode", "[data-i18n]": "i18n", "[data-i18n-aria]": "i18nAria" }[query];
    return key && key in node.dataset;
  });
  const timers = new Map(); const deadlines = new Map(); const delays = []; let timerId = 0; let now = 0;
  const closeEvents = [];
  const mediaListeners = new Set();
  const media = { matches: reduced, addEventListener(name, listener) { if (name === "change") mediaListeners.add(listener); }, removeEventListener(name, listener) { if (name === "change") mediaListeners.delete(listener); } };
  const windowRef = {
    navigator: { language: "ja" }, location: { search: queryMotion },
    matchMedia: () => media,
    setTimeout(fn, delay) { delays.push(delay); timers.set(++timerId, fn); deadlines.set(timerId, now + delay); return timerId; },
    clearTimeout(id) { timers.delete(id); deadlines.delete(id); }, requestAnimationFrame(fn) { fn(); }, addEventListener() {},
  };
  const documentRef = { documentElement: {}, createElement: () => new Element(), createElementNS: () => new Element() };
  const app = bootstrapShirushi({ root, adapter, windowRef, documentRef });
  if (queuedClose) nodes.markDialog.queueClose = (fn) => closeEvents.push(fn);
  const flushClose = async () => { for (const fn of closeEvents.splice(0)) await fn(); };
  const flushMotion = () => { for (const fn of [...timers.values()]) fn(); timers.clear(); };
  const advanceTo = (time) => {
    assert.ok(time >= now, "test clock must be monotonic");
    const due = [...timers.keys()].filter((id) => deadlines.get(id) <= time).sort((a, b) => deadlines.get(a) - deadlines.get(b));
    for (const id of due) {
      const fn = timers.get(id); now = deadlines.get(id);
      timers.delete(id); deadlines.delete(id); fn();
    }
    now = time;
  };
  const select = async (name = "image.png") => {
    nodes.imageInput.files = [{ name, type: "image/png" }];
    await nodes.imageInput.fire("change");
    nodes.previewImage.onload();
  };
  const draw = async (id = 1) => {
    await nodes.drawingCanvas.fire("pointerdown", { pointerId: id, clientX: 20, clientY: 30 });
    await nodes.drawingCanvas.fire("pointermove", { pointerId: id, clientX: 120, clientY: 110 });
    await nodes.drawingCanvas.fire("pointerup", { pointerId: id });
  };
  const save = () => nodes.markForm.fire("submit", { submitter: { value: "default" } });
  const changeReduced = (value) => { media.matches = value; for (const listener of mediaListeners) listener(); };
  return { nodes, adapter, app, select, draw, save, flushMotion, flushClose, timers, delays, advanceTo, changeReduced, mediaListeners };
}

const entryRitual = setup();
check(entryRitual.nodes.emptyStage.attributes["data-entry-motion"] === "normal", "empty entry follows ordinary motion preference");
entryRitual.changeReduced(true);
check(entryRitual.nodes.emptyStage.attributes["data-entry-motion"] === "reduce", "live OS reduced-motion change makes empty entry static");
check(entryRitual.adapter.calls.length === 0 && entryRitual.nodes.rightsIntent.hidden && entryRitual.nodes.motionRights.hidden, "decorative entry preference cannot imply applied mark or invoke Add/Verify");
entryRitual.changeReduced(false);
check(entryRitual.nodes.emptyStage.attributes["data-entry-motion"] === "normal", "live OS preference can return to normal entry motion");
await entryRitual.select();
check(entryRitual.nodes.emptyStage.hidden && !entryRitual.nodes.selectedStage.hidden, "selected image hides the decorative entry independently of Personal Mark motion");
entryRitual.app.destroy();
check(entryRitual.mediaListeners.size === 0, "destroy removes entry-motion media listener");
check(setup(new TestAdapter(), false, false, "?motion=reduce").nodes.emptyStage.attributes["data-entry-motion"] === "reduce", "Preview reduce override makes empty entry static even without OS reduce");
const overrideNormal = setup(new TestAdapter(), true, false, "?motion=normal");
overrideNormal.changeReduced(true);
check(overrideNormal.nodes.emptyStage.attributes["data-entry-motion"] === "normal", "existing explicit normal Preview override stays authoritative for empty entry");

const empty = setup(new TestAdapter(null));
await empty.nodes.editMarkButton.fire("click");
check(!empty.nodes.markDialog.open, "image-first flow cannot open editor without a loaded image");
await empty.select();
await empty.nodes.editMarkButton.fire("click");
check(empty.nodes.handwrittenTab.attributes["aria-selected"] === "true", "first empty Personal Mark dialog selects handwritten");
check(empty.nodes.typedPanel.hidden && !empty.nodes.handwrittenPanel.hidden, "handwritten panel is primary");
check(empty.nodes.clearDrawingButton.textContent === "クリア", "clear-all action has explicit Japanese label");
await empty.draw();
check(empty.nodes.drawingCanvas.renderedStrokes === 1, "handwriting stroke drawn");
await empty.nodes.drawingCanvas.fire("pointerdown", { pointerId: 2, clientX: 50, clientY: 60 });
await empty.nodes.clearDrawingButton.fire("click");
check(empty.nodes.drawingCanvas.renderedStrokes === 0 && !empty.nodes.drawingCanvas.parentElement.classes.has("has-strokes"), "clear removes every draft stroke/hint state");
check(empty.nodes.drawingCanvas.captures.size === 0, "clear releases active pointer capture");
await empty.nodes.drawingCanvas.fire("pointermove", { pointerId: 2, clientX: 160, clientY: 100 });
check(empty.nodes.drawingCanvas.renderedStrokes === 0, "late pointer movement cannot revive cleared stroke");
await empty.save();
check(empty.nodes.markDialog.open && empty.adapter.loadSessionMark() === null, "empty cleared drawing cannot be saved");
await empty.draw(3); await empty.save();
const drawn = empty.adapter.loadSessionMark();
check(drawn.mode === "handwritten" && drawn.handwritten.strokes.length === 1, "redraw/save after clear works");
check(drawn.handwritten.coordinateSpace.width === 480 && drawn.handwritten.coordinateSpace.height === 220, "clear preserves capture size/layout");
await empty.nodes.editMarkButton.fire("click");
await empty.nodes.clearDrawingButton.fire("click");
empty.nodes.markDialog.close();
check(empty.adapter.loadSessionMark().handwritten.strokes.length === 1, "cancelled clear cannot erase saved Personal Mark");

await empty.nodes.editMarkButton.fire("click");
await empty.nodes.typedTab.fire("click");
empty.nodes.typedInput.value = "Explicit typed choice"; await empty.nodes.typedInput.fire("input"); await empty.save();
await empty.nodes.editMarkButton.fire("click");
check(empty.nodes.typedTab.attributes["aria-selected"] === "true", "explicit saved Typed choice survives reopening");
empty.nodes.markDialog.close();

const flow = setup(); const n = flow.nodes;
check(!n.verifyButton.hidden && !n.verifyButton.disabled && n.verifyButtonLabel.textContent === "しるしを確認する" && n.motionRights.hidden, "initial Verify entry is visible without claiming a prior mark or check");
check(!n.markSummary.hidden && !n.primaryActions.hidden && n.rightsIntent.hidden && !n.addButton.hidden && !n.addButton.disabled, "initial product entrances occupy the same visible slots as selected state");
check(n.markPreview.children.length === 1 && n.markPreview.children[0].textContent === "未設定" && n.editMarkButton.disabled && n.emptyStage.hidden === false && n.selectedStage.hidden, "empty summary is a quiet placeholder, never fixture/image mark evidence");
await n.addButton.fire("click"); await n.verifyButton.fire("click"); await n.editMarkButton.fire("click");
check(flow.adapter.calls.length === 0 && !n.markDialog.open && flow.timers.size === 0, "direct empty-state events cannot dispatch Add/Verify/editor or motion");
check(n.imageInput.clickCount === 1 && n.addButton.disabled && n.verifyButton.disabled, "one chooser owns the pending action; duplicate/opposite entry cannot launch another");
await n.imageInput.fire("cancel");
flow.app.refreshPresentation();
check(!n.markSummary.hidden && !n.primaryActions.hidden && n.rightsIntent.hidden && n.markPreview.children[0].textContent === "未設定", "locale refresh retains entrance slots without exposing empty-state fixture mark");
check(verificationFixture().mark.mode === "handwritten", "known verification fixture defaults to handwritten");
await flow.select();
check(!n.addButton.disabled && !n.verifyButton.disabled && !n.verifyButton.hidden, "selected unmarked target retains both product entrances");
check(!n.markSummary.hidden && !n.primaryActions.hidden && !n.addButton.hidden && !n.editMarkButton.disabled && n.rightsIntent.hidden, "successful image load enables normal mark flow but not an applied-intent expression");
await n.verifyButton.fire("click");
check(flow.adapter.calls.length === 1 && flow.adapter.calls[0].kind === "verify" && n.motionRights.hidden && n.motionLayer.attributes["aria-hidden"] === "true", "fresh Verify checks target without fabricating mark/intent");
check(n.statusLine.textContent.includes("実ファイル検証ではありません") && n.verifyButtonLabel.textContent === "しるしを確認する", "negative preview result is honest and does not imply previous marked state");
flow.adapter.nextAdd = { status: "ERROR" }; await n.addButton.fire("click");
check(n.verifyButtonLabel.textContent === "しるしを確認する" && n.motionRights.hidden && !n.addButton.disabled, "failed Add cannot establish marked state");
flow.adapter.nextAdd = { status: "PREVIEW_ADD", mark: { mode: "handwritten" } }; await n.addButton.fire("click");
check(n.verifyButtonLabel.textContent === "しるしを確認する" && n.motionRights.hidden, "malformed successful payload cannot show mark evidence");
await n.addButton.fire("click");
check(!n.verifyButton.hidden && n.verifyButton.disabled && n.addButton.disabled, "accepted Add shows confirm but locks actions during motion");
check(!n.motionRights.hidden && n.motionLayer.classes.has("motion-add"), "accepted Add retains intended motion/rights preview");
check(!n.rightsIntent.hidden, "only accepted target-mark state reveals the main intent expression");
const callCount = flow.adapter.calls.length; await n.verifyButton.fire("click");
check(flow.adapter.calls.length === callCount, "pending motion prevents duplicate actions");
flow.flushMotion();
check(!n.verifyButton.disabled && n.verifyButtonLabel.textContent === "しるしを確認する" && n.motionRights.hidden, "after Add motion Verify keeps its purpose label without stale overlay");
check(n.addButtonLabel.textContent === "しるしを見る" && !n.rightsIntent.hidden, "completed Add exposes distinct view-mark and verification labels with genuine applied state");
await n.addButton.fire("click");
check(n.motionLayer.classes.has("motion-add") && !n.motionRights.hidden && n.addButton.disabled, "view-mark action preserves existing Add replay path and in-flight lock");
flow.flushMotion();
check(n.addButtonLabel.textContent === "しるしを見る" && n.verifyButtonLabel.textContent === "しるしを確認する", "replay completion preserves the two purpose labels");
const applied = clone(flow.adapter.targets.values().next().value);
await n.editMarkButton.fire("click"); await n.typedTab.fire("click");
n.typedInput.value = "New profile"; await n.typedInput.fire("input"); await flow.save();
check(!n.verifyButton.hidden, "profile edits do not unmark the target");
check(!("previewToolsSlot" in n), "main screen has no development fixture chooser");
await n.verifyButton.fire("click");
const verifyCall = flow.adapter.calls.at(-1);
check(verifyCall.kind === "verify" && !("mark" in verifyCall.request) && !("options" in verifyCall.request), "Verify never sends profile mark or fixture selector");
check(n.motionMark.dataset.mode === applied.mode && n.motionLayer.classes.has("motion-verify"), "Verify renders applied target, not edited Typed profile");
flow.flushMotion();
check(!n.verifyButton.disabled && n.motionRights.hidden, "Verify completion resets motion but preserves confirmation state");
await flow.select("image.png");
check(n.verifyButtonLabel.textContent === "しるしを確認する" && n.motionRights.hidden && !n.addButton.disabled, "same-name replacement starts unmarked but retains Verify entrance");
await n.verifyButton.fire("click");
check(flow.adapter.calls.at(-1).kind === "verify" && flow.adapter.calls.at(-1).request.targetReference !== verifyCall.request.targetReference && n.rightsIntent.hidden, "replacement Verify checks only new target without inheriting previous proof");
await n.addButton.fire("click"); flow.flushMotion();
check(flow.adapter.calls.at(-1).request.targetReference !== verifyCall.request.targetReference, "image selection references are unique even with identical filenames");
flow.adapter.nextVerify = { status: "PREVIEW_UNMARKED" }; await n.verifyButton.fire("click");
check(n.verifyButtonLabel.textContent === "しるしを確認する" && n.motionRights.hidden, "negative Verify invalidates marked UI instead of showing intent");
check(n.rightsIntent.hidden, "negative Verify clears main applied-intent expression too");

// Async completion and already-queued timers are exercised without real delays.
let finish; flow.adapter.nextAdd = new Promise((resolve) => { finish = resolve; });
const pending = n.addButton.fire("click");
await flow.select("replacement.png"); finish({ status: "PREVIEW_ADD", mark: applied }); await pending;
check(n.verifyButtonLabel.textContent === "しるしを確認する" && n.motionRights.hidden, "late Add result cannot mark replacement target");
await n.addButton.fire("click");
const queued = [...flow.timers.values()];
await flow.select("third.png"); queued.forEach((fn) => fn());
check(n.verifyButtonLabel.textContent === "しるしを確認する" && n.motionRights.hidden && n.addButtonLabel.textContent === "しるしを付ける", "stale motion timers cannot restore applied/again state");
let finishVerify; await n.addButton.fire("click"); flow.flushMotion();
flow.adapter.nextVerify = new Promise((resolve) => { finishVerify = resolve; });
const pendingVerify = n.verifyButton.fire("click");
await flow.select("fourth.png"); finishVerify({ status: "PREVIEW_VERIFY", mark: applied }); await pendingVerify;
check(n.verifyButtonLabel.textContent === "しるしを確認する" && n.motionRights.hidden, "late Verify result cannot show target mark/rights on replacement");

// Loading, failure and terminal callbacks must not re-enable a no-image surface.
const loading = setup(); const l = loading.nodes;
await loading.select(); await l.addButton.fire("click"); loading.flushMotion();
check(!l.rightsIntent.hidden, "loading regression begins with a genuinely accepted preview target");
l.imageInput.files = [{ name: "replacement.png", type: "image/png" }];
await l.imageInput.fire("change");
const lateLoad = l.previewImage.onload;
check(l.emptyStage.hidden === false && l.selectedStage.hidden && !l.markSummary.hidden && !l.primaryActions.hidden && l.rightsIntent.hidden, "replacement clears old evidence but keeps stable action slots during decode");
check(!l.addButton.hidden && l.addButton.disabled && l.verifyButton.disabled && l.editMarkButton.disabled && l.markPreview.children[0].textContent === "未設定", "pending decode is not permission to use profile, Add or Verify");
const beforeLoadingCalls = loading.adapter.calls.length;
await l.addButton.fire("click"); await l.verifyButton.fire("click"); await l.editMarkButton.fire("click");
check(loading.adapter.calls.length === beforeLoadingCalls && !l.markDialog.open, "direct events during loading cannot execute operations/editor");
l.previewImage.onerror();
check(!l.statusLine.hidden && l.statusLine.textContent === "画像を読み込めませんでした" && !l.primaryActions.hidden, "decode error remains visible beside reusable product entrances");
lateLoad();
check(l.selectedStage.hidden && l.editMarkButton.disabled && l.rightsIntent.hidden, "late onload after terminal error cannot reopen image-dependent UI");
await loading.select("good.png");
check(!l.markSummary.hidden && !l.addButton.disabled && l.verifyButtonLabel.textContent === "しるしを確認する" && l.rightsIntent.hidden, "fresh successful selection recovers normal unmarked controls");
check(loading.adapter.loadSessionMark().mode === "handwritten" && l.markPreview.children.length > 0, "load failure never deletes the existing session Personal Mark");
l.imageInput.files = [{ name: "bad-dimensions.png", type: "image/png" }]; await l.imageInput.fire("change");
const invalidLoad = l.previewImage.onload; l.previewImage.naturalHeight = 0; invalidLoad();
check(l.selectedStage.hidden && !l.primaryActions.hidden && l.editMarkButton.disabled && !l.statusLine.hidden, "invalid dimensions leave an explicit no-image error with stable entrances");
l.previewImage.naturalHeight = 600; invalidLoad();
check(l.selectedStage.hidden && l.editMarkButton.disabled, "late dimension recovery cannot revive a terminal failed generation");
await loading.select();
l.imageInput.files = []; await l.imageInput.fire("change");
check(!l.markSummary.hidden && !l.addButton.disabled, "file chooser cancellation preserves an existing valid selected image");
loading.adapter.imageFromFile = () => { throw new Error("INPUT_REJECTED"); };
l.imageInput.files = [{ name: "rejected.png", type: "image/png" }]; await l.imageInput.fire("change");
check(l.selectedStage.hidden && !l.primaryActions.hidden && l.rightsIntent.hidden && l.editMarkButton.disabled && !l.statusLine.hidden, "adapter input rejection fails closed instead of leaving prior image active");
loading.app.destroy();
flow.app.destroy(); empty.app.destroy();
check(sanitizeMark(drawn)?.mode === "handwritten", "saved handwritten view model remains valid");

// Entrances remember one intent only, consumed after a successful decode.
const addEntry = setup(); const a = addEntry.nodes;
await a.addButton.fire("click");
check(a.imageInput.clickCount === 1 && addEntry.adapter.calls.length === 0, "Add entrance opens chooser before any operation");
await addEntry.select();
check(addEntry.adapter.calls.length === 1 && addEntry.adapter.calls[0].kind === "add" && a.motionLayer.classes.has("motion-add"), "decoded Add selection continues directly into existing Add flow");
a.previewImage.onload();
check(addEntry.adapter.calls.length === 1, "duplicate image completion cannot replay consumed Add intent");
addEntry.flushMotion();
check(a.verifyButtonLabel.textContent === "しるしを確認する" && a.addButtonLabel.textContent === "しるしを見る", "accepted Add retains purpose-specific labels in the same action slots");
await a.verifyButton.fire("click"); addEntry.flushMotion();
check(addEntry.adapter.calls.at(-1).kind === "verify" && !a.rightsIntent.hidden, "existing marked target verifies through same entrance");

const verifyEntry = setup(); const v = verifyEntry.nodes;
await v.verifyButton.fire("click"); await verifyEntry.select();
check(verifyEntry.adapter.calls.length === 1 && verifyEntry.adapter.calls[0].kind === "verify", "Verify entrance selects then checks image, never Add");
check(v.motionLayer.attributes["aria-hidden"] === "true" && v.motionRights.hidden && v.rightsIntent.hidden, "unmarked selection has no mark/intent overlay");
check(v.statusLine.textContent.includes("実ファイル検証ではありません"), "fresh Verify entry exposes preview-only negative result");
verifyEntry.adapter.nextVerify = { status: "ERROR" }; await v.verifyButton.fire("click");
check(v.statusLine.textContent === "しるしの確認ができませんでした" && v.rightsIntent.hidden, "Verify failure is distinct from an unmarked result");
verifyEntry.adapter.nextVerify = { status: "PREVIEW_VERIFY", mark: verificationFixture().mark }; await v.verifyButton.fire("click");
check(v.verifyButtonLabel.textContent === "しるしを確認する" && !v.rightsIntent.hidden, "accepted existing marked result retains Verify purpose label with target proof, not profile presence");
verifyEntry.flushMotion();
verifyEntry.adapter.nextVerify = Promise.reject(new Error("PROBE_FAILED")); await v.verifyButton.fire("click");
check(v.verifyButtonLabel.textContent === "しるしを確認する" && v.rightsIntent.hidden && v.statusLine.textContent === "しるしの確認ができませんでした", "thrown Verify error clears marked presentation rather than pretending a successful check");

const cancelled = setup(); const c = cancelled.nodes;
await c.addButton.fire("click"); await c.imageInput.fire("cancel"); await cancelled.select();
check(cancelled.adapter.calls.length === 0 && !c.addButton.disabled, "cancelled Add chooser cannot leak intent into later auxiliary selection");
await c.changeImageButton.fire("click"); await c.imageInput.fire("cancel");
check(!c.selectedStage.hidden && c.verifyButtonLabel.textContent === "しるしを確認する", "chooser cancellation preserves selected unmarked image");

const failedEntry = setup(); const f = failedEntry.nodes;
await f.verifyButton.fire("click"); f.imageInput.files = [{name:"bad.png"}]; await f.imageInput.fire("change");
const staleEntryLoad = f.previewImage.onload; f.previewImage.onerror(); staleEntryLoad(); await failedEntry.select();
check(failedEntry.adapter.calls.length === 0 && !f.verifyButton.disabled, "failed decode and stale callback discard Verify continuation");
await f.changeImageButton.fire("click"); f.imageInput.files = []; await f.imageInput.fire("change");
check(!f.addButton.disabled && !f.verifyButton.disabled, "empty change event releases chooser lock without executing");

const staleChoice = setup(); const s = staleChoice.nodes;
await s.addButton.fire("click"); s.imageInput.files = [{name:"first.png"}]; await s.imageInput.fire("change");
const oldLoad = s.previewImage.onload;
s.imageInput.files = [{name:"second.png"}]; await s.imageInput.fire("change"); oldLoad(); s.previewImage.onload();
check(staleChoice.adapter.calls.length === 0 && s.imageName.textContent === "second.png", "replacement generation cannot inherit old Add continuation");

const draftEntry = setup(new TestAdapter(null)); const d = draftEntry.nodes;
await d.addButton.fire("click"); await draftEntry.select();
check(d.markDialog.open && d.handwrittenTab.attributes["aria-selected"] === "true" && draftEntry.adapter.calls.length === 0, "Add without saved mark continues into handwritten editor first");
await draftEntry.save();
check(draftEntry.adapter.calls.length === 0 && d.markDialog.open, "empty draft cannot auto-Add");
await draftEntry.draw(); await draftEntry.save();
check(draftEntry.adapter.calls.length === 1 && draftEntry.adapter.calls[0].kind === "add", "valid draft save completes the original Add intent exactly once");
await draftEntry.save();
check(draftEntry.adapter.calls.length === 1, "late repeated save cannot execute a second Add");
draftEntry.flushMotion();

const cancelDraft = setup(new TestAdapter(null));
await cancelDraft.nodes.addButton.fire("click"); await cancelDraft.select(); cancelDraft.nodes.markDialog.close();
await cancelDraft.nodes.editMarkButton.fire("click"); await cancelDraft.draw(); await cancelDraft.save();
check(cancelDraft.adapter.calls.length === 0, "cancelled auto-Add editor intent is not revived by ordinary profile save");
const replacedDraft = setup(new TestAdapter(null));
await replacedDraft.nodes.addButton.fire("click"); await replacedDraft.select(); await replacedDraft.draw();
await replacedDraft.select("different.png"); await replacedDraft.save();
check(!replacedDraft.nodes.markDialog.open && replacedDraft.adapter.calls.length === 0, "image replacement closes pending editor and rejects stale save/auto-Add");
const destroyed = setup(); await destroyed.nodes.verifyButton.fire("click");
destroyed.nodes.imageInput.files = [{name:"pending.png"}]; await destroyed.nodes.imageInput.fire("change");
const destroyedLoad = destroyed.nodes.previewImage.onload; destroyed.app.destroy(); destroyedLoad();
await destroyed.nodes.addButton.fire("click");
check(destroyed.adapter.calls.length === 0 && destroyed.nodes.selectedStage.hidden, "destroy rejects pending decode/intent and later action events");

const queuedEditor = setup(new TestAdapter(null), false, true); const qe = queuedEditor.nodes;
await qe.addButton.fire("click"); await queuedEditor.select();
await qe.markForm.fire("submit", {submitter:{value:"cancel"}}); qe.markDialog.close();
await qe.editMarkButton.fire("click"); await queuedEditor.flushClose();
await queuedEditor.draw(); await queuedEditor.save();
check(queuedEditor.adapter.calls.length === 0 && queuedEditor.adapter.loadSessionMark()?.mode === "handwritten", "queued old close cannot erase reopened draft or revive cancelled Add intent");
await queuedEditor.flushClose();
const queuedSave = setup(new TestAdapter(null), false, true);
await queuedSave.nodes.addButton.fire("click"); await queuedSave.select(); await queuedSave.draw(); await queuedSave.save(); await queuedSave.flushClose();
check(queuedSave.adapter.calls.length === 1 && queuedSave.nodes.motionLayer.classes.has("motion-add"), "queued save-close preserves exactly-once Add continuation and its motion");
queuedSave.flushMotion();

const unsupportedAdapter = new BrowserFoundationAdapter();
unsupportedAdapter.imageFromFile = (file) => ({ url: "blob:test", name: file.name });
const unsupported = setup(unsupportedAdapter);
await unsupported.nodes.addButton.fire("click"); await unsupported.nodes.verifyButton.fire("click");
check(unsupported.nodes.addButton.disabled && unsupported.nodes.verifyButton.disabled && !unsupported.nodes.imageInput.clickCount, "entrances never upgrade unsupported Core capability");
await unsupported.select(); await unsupported.nodes.addButton.fire("click"); await unsupported.nodes.verifyButton.fire("click");
check(unsupported.nodes.addButton.disabled && unsupported.nodes.verifyButton.disabled, "decoded image cannot upgrade unsupported Core operations");
for (const locale of ["ja", "en", "zh-CN", "zh-TW", "ko"]) {
  c.languageSelect.value = locale; await c.languageSelect.fire("change");
  check(c.languageSelect.value === locale && c.verifyButtonLabel.textContent.length > 0 && c.rightsIntent.hidden, `${locale} selection updates current language without marked-state fabrication`);
  check(c.verifyButton.textContent === undefined && c.addButton.textContent === undefined && c.addButtonIcon && c.verifyButtonIcon, "locale refresh writes only labels, never overwrites decorative icon parents");
}
for (const fixture of [addEntry,verifyEntry,cancelled,failedEntry,staleChoice,draftEntry,cancelDraft,replacedDraft,unsupported,queuedEditor,queuedSave]) fixture.app.destroy();

// The same actual renderer supplies Add/Verify and static reduced motion.
const documentRef = { createElement: () => new Element(), createElementNS: () => new Element() };
const target = new Element();
const mark = clone(drawn);
mark.handwritten.strokes.push([{ x: .8, y: .2 }, { x: .7, y: .4 }]);
const original = JSON.stringify(mark);
renderMotionMark(documentRef, target, mark);
const svg = target.children[0], paths = svg.children;
check(paths.length === 6 && paths.map((p) => [...p.classes][0]).join() === "diffusion,glow,ink,diffusion,glow,ink", "each original stroke has diffusion/glow/core layers");
check(svg.attributes.preserveAspectRatio === "xMidYMid meet", "handwritten presentation preserves geometry");
check(paths[0].attributes.d === paths[1].attributes.d && paths[1].attributes.d === paths[2].attributes.d, "layers use identical source stroke coordinates");
check(paths[0].style["--stroke-delay"] === "0ms" && parseInt(paths[3].style["--stroke-delay"]) > 0, "original stroke order progressively draws");
check(parseInt(paths[3].style["--stroke-delay"]) + parseInt(paths[3].style["--stroke-duration"]) === DRAW_MOTION_MS, "draw phase ends exactly at recovered 1550ms");
check(target.style["--handwritten-width"] === "57%", "wide handwriting grows 1.5x to 57% reference sizing");
renderMotionMark(documentRef, target, mark, { kind: "verify" });
check(target.style["--handwritten-width"] === "69%", "Verify grows 1.5x to 69% while preserving emphasis and coordinates");
check(JSON.stringify(mark) === original, "rendering never mutates mark data/source pixels");
const tall = clone(mark); tall.handwritten.strokes = [[{ x: .5, y: 0 }, { x: .5, y: 1 }]];
renderMotionMark(documentRef, target, tall, { imageAspectRatio: 2 });
check(parseFloat(target.style["--handwritten-width"]) < 38, "tall handwriting fits image height instead of stretching");
for (const imageRatio of [.5, 2, 4]) {
  for (const kind of ["add", "verify"]) {
    renderMotionMark(documentRef, target, tall, { kind, imageAspectRatio: imageRatio });
    const b = handwrittenMotionBounds(tall.handwritten), w = parseFloat(target.style["--handwritten-width"]);
    const paintLimit = kind === "verify" ? 92.00001 : 88.00001;
    check(w / b.width * (b.width * 1.04 + 64) <= paintLimit && w / b.width * (b.height * 1.04 + 64) * imageRatio <= paintLimit, `${kind} portrait/landscape/narrow paint bounds include absorption glow`);
  }
}
const tap = clone(mark); tap.handwritten.strokes = [[{ x: .5, y: .5 }]];
const bounds = handwrittenMotionBounds(tap.handwritten);
renderMotionMark(documentRef, target, tap);
check(bounds.width === 48 && bounds.height === 48 && target.children[0].children[2].attributes.d.includes("l0.01 0"), "tap/degenerate bounds remain finite and drawable");
const large = clone(drawn);
large.handwritten.strokes = Array.from({ length: 128 }, () => Array.from({ length: 4096 }, (_, i) => ({ x: i / 4095, y: i / 4095 })));
check(sanitizeMark(large) !== null && Object.values(handwrittenMotionBounds(large.handwritten)).every(Number.isFinite), "maximum accepted handwriting bounds do not exceed JS argument limits");
renderMotionMark(documentRef, target, large);
check(target.children[0].children.length === 384 && parseInt(target.children[0].children.at(-1).style["--stroke-duration"]) >= 0, "maximum accepted mark renders with finite ordered timing");
renderMotionMark(documentRef, target, { mode: "typed", typed: "しるし" });
check(target.children[0].children.length === 4 && target.children[0].children[1].textContent === "しるし", "Typed restores text/aura/trace/fragments with readable identity");
check(!("--handwritten-width" in target.style), "mode change clears handwriting geometry");
const fittingDocument = { createElement: () => { const e = new Element(); e.scrollWidth = 900; e.offsetHeight = 150; return e; } };
for (const [width, height] of [[220, 600], [600, 220], [100, 700]]) {
  const fittingTarget = new Element(); fittingTarget.clientWidth = width * .8; fittingTarget.parentElement = { clientHeight: height };
  renderMotionMark(fittingDocument, fittingTarget, { mode: "typed", typed: "W".repeat(48) }, { kind: "verify" });
  const scale = Number(fittingTarget.children[0].style["--typed-scale"]);
  check(scale > 0 && scale <= 1 && 932 * scale <= width * .8 + .00001 && 182 * scale <= height * .48 + .00001, "long Typed text fits actual portrait/landscape/narrow image bounds uniformly");
  check(fittingTarget.dataset.kind === "verify", "Verify size applies before measuring intrinsic Typed layers");
  if (width < 300) check(fittingTarget.children[0].style.overflowWrap === "anywhere" && fittingTarget.children[0].style.whiteSpace === "normal", "narrow long names wrap complete identity instead of shrinking into unreadability");
  renderMotionMark(documentRef, fittingTarget, mark);
  check(!fittingTarget.children[0].style["--typed-scale"], "mode switch drops stale Typed fit scale");
}
const reduced = setup(new TestAdapter(), true);
await reduced.select(); await reduced.nodes.addButton.fire("click");
check(reduced.nodes.motionLayer.classes.has("reduced") && !reduced.nodes.motionLayer.classes.has("playing"), "reduced mode shows static identity without normal animation");
check(!reduced.nodes.motionRights.hidden && !reduced.nodes.statusLine.hidden && reduced.delays.includes(2200), "reduced mode retains separate external status and bounded settling");
const summaryIdentity = JSON.stringify(reduced.nodes.markPreview.children);
reduced.flushMotion();
check(reduced.nodes.statusLine.textContent.includes("完了") && JSON.stringify(reduced.nodes.markPreview.children) === summaryIdentity, "completion updates external status without changing/crowding Personal Mark summary");
check(!("motionComplete" in reduced.nodes), "no duplicate completion comment is mounted over image/mark");
reduced.flushMotion(); await reduced.nodes.verifyButton.fire("click");
check(reduced.nodes.motionLayer.classes.has("motion-verify") && reduced.nodes.motionLayer.classes.has("reduced"), "Verify uses same static reduced motion language");
reduced.app.destroy();
// Completion is published by the existing end callback only, after the mark.
// Clock-controlled checks cover both operations/preferences without sleep races.
for (const reduce of [false, true]) {
  for (const kind of ["add", "verify"]) {
    const timed = setup(new TestAdapter(), reduce);
    await timed.select();
    if (kind === "verify") { await timed.nodes.addButton.fire("click"); timed.flushMotion(); }
    timed.delays.length = 0;
    await timed.nodes[kind === "add" ? "addButton" : "verifyButton"].fire("click");
    const progress = timed.nodes.statusLine.textContent;
    const duration = reduce ? 2200 : 5250;
    check(timed.delays.length === 1 && timed.delays[0] === duration, "only existing bounded motion-end timer schedules completion");
    timed.advanceTo(reduce ? 140 : 3260);
    check(timed.nodes.statusLine.textContent === progress && timed.nodes.motionLayer.classes.has(reduce ? "reduced" : "playing"), "no early completion announcement competes with mark");
    timed.advanceTo(duration - 1);
    check(timed.nodes.statusLine.textContent === progress, "progress remains until full mark duration elapses");
    timed.advanceTo(duration);
    check(timed.nodes.statusLine.textContent.includes("完了") && timed.nodes.motionLayer.attributes["aria-hidden"] === "true" && !timed.nodes.addButton.disabled, "completion follows mark reset and restores actions at unchanged bound");
    timed.app.destroy();
  }
}
for (const stop of ["replace", "destroy"]) {
  const stale = setup(); await stale.select(); await stale.nodes.addButton.fire("click");
  const queuedDone = [...stale.timers.values()][0];
  if (stop === "replace") await stale.select("replacement.png"); else stale.app.destroy();
  const status = stale.nodes.statusLine.textContent;
  queuedDone();
  check(stale.nodes.statusLine.textContent === status && stale.timers.size === 0, "replacement/destroy reject even an already-queued old completion callback");
}
// Real product UI contract, using deterministic transport doubles only.
class ProductTestAdapter extends TestAdapter {
  capabilities = freezeCapabilities({ coreAdd:true,coreVerify:true,localImagePreview:true,sessionMarkEdit:true });
  async addMark(request) {
    this.calls.push({kind:"add",request:clone(request)});
    return {status:"SUCCESS",mark:clone(request.mark),
      outputImage:{url:"data:image/png;base64,AA==",name:"art_rights.png",reference:"C:/work/art_rights.png"}};
  }
  async verifyFileMark(request) {
    this.calls.push({kind:"verify",request:clone(request)});
    return {status:"LIMITED_INSPECTION",intentPresent:this.marked !== false,mark:null};
  }
}
const nativeUi=setup(new ProductTestAdapter());
await nativeUi.select();await nativeUi.nodes.addButton.fire("click");nativeUi.flushMotion();
check(nativeUi.nodes.imageName.textContent==="art_rights.png" && nativeUi.nodes.addButtonLabel.textContent==="しるしを見る", "actual Add changes target to separate generated output");
await nativeUi.nodes.addButton.fire("click");nativeUi.flushMotion();
check(nativeUi.adapter.calls.filter(c=>c.kind==="add").length===1,"native replay never invokes Add a second time");
await nativeUi.nodes.verifyButton.fire("click");
check(nativeUi.adapter.calls.at(-1).request.targetReference==="C:/work/art_rights.png","verify receives generated output path");
check(nativeUi.nodes.statusLine.textContent.includes("限定検査")&&nativeUi.nodes.statusLine.textContent.includes("INCOMPLETE"),"limited Verify status not full verification");
check(nativeUi.nodes.motionLayer.attributes["aria-hidden"]==="true" && nativeUi.timers.size===0,"limited Verify does not run full success motion");
nativeUi.adapter.marked=false;await nativeUi.nodes.verifyButton.fire("click");
check(nativeUi.nodes.rightsIntent.hidden,"actual absent intent clears positive label");
nativeUi.app.destroy();
const nativeFailure=setup(new ProductTestAdapter());
nativeFailure.adapter.addMark=async()=>{throw new Error("OUTPUT_ALREADY_EXISTS");};
await nativeFailure.select();await nativeFailure.nodes.addButton.fire("click");
check(nativeFailure.nodes.statusLine.textContent.includes("追加できません") && nativeFailure.nodes.rightsIntent.hidden,"native Add failure never collapses to selected-image status or success");
nativeFailure.app.destroy();

// Real shared bootstrap: Explorer intent is not an applied-mark claim. No native
// executable/registry mutation; image load and async handoff are explicit signals.
for (const extension of ["png", "jpeg"]) {
  for (const operation of ["add", "limited_inspect"]) {
    const adapter = new ProductTestAdapter(null);
    adapter.marked = false;
    let takes = 0;
    adapter.takeExplorerRequest = async()=>{ takes++; return {operation,image:{name:`日本語 image.${extension}`,url:"data:image/png;base64,AA==",reference:`C:/work/日本語 image.${extension}`}}; };
    const ui = setup(adapter);
    await ui.app.consumeExplorerEntry(); await ui.app.consumeExplorerEntry();
    check(takes === 1 && adapter.calls.length === 0,"Explorer intent once; no action before image decodes");
    ui.nodes.previewImage.onload();
    await Promise.resolve();
    if (operation === "add") {
      check(ui.nodes.markDialog.open && ui.nodes.handwrittenTab.attributes["aria-selected"] === "true","Explorer Add without saved mark opens handwritten configuration");
      check(adapter.calls.length === 0,"no invented mark or premature Add");
      await ui.draw(); await ui.save();
      check(adapter.calls.length === 1 && adapter.calls[0].kind === "add","Explorer continues existing Add after explicit mark save");
    } else {
      check(adapter.calls.length === 1 && adapter.calls[0].kind === "verify","Explorer Verify enters existing flow without requesting mark");
      check(ui.nodes.rightsIntent.hidden && !ui.nodes.markDialog.open,"unmarked Explorer image has no false intent/mark dialog");
    }
    ui.app.destroy();
  }
}
const savedEntry = setup(new ProductTestAdapter());
savedEntry.adapter.takeExplorerRequest = async()=>({operation:"add",image:{name:"saved.png",url:"data:image/png;base64,AA==",reference:"C:/saved.png"}});
await savedEntry.app.consumeExplorerEntry(); savedEntry.nodes.previewImage.onload(); await Promise.resolve();
check(savedEntry.adapter.calls.length === 1 && !savedEntry.nodes.markDialog.open,"Explorer Add reuses available session mark");
savedEntry.app.destroy();
const lateEntry = setup(new ProductTestAdapter());
let resolveEntry;
lateEntry.adapter.takeExplorerRequest = ()=>new Promise(resolve=>{resolveEntry=resolve;});
const pendingEntry = lateEntry.app.consumeExplorerEntry();
check(lateEntry.nodes.addButton.disabled && lateEntry.nodes.verifyButton.disabled,"startup handoff locks action selection while pending");
lateEntry.app.destroy(); resolveEntry({operation:"add",image:{name:"late.png",url:"data:image/png;base64,AA=="}});
await pendingEntry;
check(lateEntry.adapter.calls.length === 0 && !lateEntry.nodes.markDialog.open,"destroyed startup cannot dispatch late image");
const failedExplorerEntry = setup(new ProductTestAdapter());
failedExplorerEntry.adapter.takeExplorerRequest = async()=>{throw new Error("EXPLORER_DUPLICATE");};
await failedExplorerEntry.app.consumeExplorerEntry();
check(failedExplorerEntry.adapter.calls.length === 0 && failedExplorerEntry.nodes.rightsIntent.hidden && !failedExplorerEntry.nodes.statusLine.hidden,"invalid/duplicate native entry is visible failure, not product success");
failedExplorerEntry.app.destroy();
const corruptExplorerEntry = setup(new ProductTestAdapter());
corruptExplorerEntry.adapter.takeExplorerRequest = async()=>({operation:"add",image:{name:"corrupt.png",url:"data:image/png;base64,AA=="}});
await corruptExplorerEntry.app.consumeExplorerEntry(); corruptExplorerEntry.nodes.previewImage.onerror();
check(corruptExplorerEntry.adapter.calls.length === 0 && corruptExplorerEntry.nodes.rightsIntent.hidden && !corruptExplorerEntry.nodes.markDialog.open,"malformed image decode never enters Explorer Add/Verify");
corruptExplorerEntry.app.destroy();

const css = await readFile(new URL("./styles.css", import.meta.url), "utf8");
check(css.includes("--motion-dissolve-at: 4300ms") && css.includes("--motion-dissolve-duration: 900ms") && flow.delays.includes(5250), "draw/recognize/hold/diffuse/quiet finish use recovered 5.25s schedule");
for (const name of ["core-absorb", "glow-absorb", "diffusion-absorb"]) {
  check(css.includes(`@keyframes ${name} { 0% { stroke-dashoffset: 0;`), `${name} starts from fully drawn strokes, not a hard reset`);
}
check(css.includes("translateY(-17px)") && css.includes("91% 57%") && css.includes("brightness(1.18)"), "Typed uses recovered upward irregular contour sublimation");
check(css.includes('data-mode="typed"] { top: 44%') && css.includes('data-mode="typed"][data-kind="verify"] { top: 43%'), "Typed placement stays above center in Add and Verify");
check(css.includes("clamp(2.325rem,7.95vw,5.175rem)") && css.includes("clamp(2.625rem,8.85vw,5.775rem)") && css.includes("scale(var(--typed-scale,1))"), "Typed font grows1.5x with all layers fit together");
const entry = await readFile(new URL("./dev-preview.js", import.meta.url), "utf8");
check(!entry.includes("preview-controls") && !css.includes("verify-fixture"), "main fixture selection seam and styling are removed");
console.log(`PASS UI confirm/personal-mark flow: ${checks} deterministic checks`);
