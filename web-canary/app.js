import { BrowserAdapter } from "./adapters/browser-adapter.js";
import { normalizeLocale, translator } from "./i18n.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const DRAW_WIDTH = 480;
const DRAW_HEIGHT = 220;
const NORMAL_MOTION_MS = 5250;
const REDUCED_MOTION_MS = 2200;

const adapter = new BrowserAdapter();
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
const motionPreview = new URLSearchParams(window.location.search).get("motion");

const elements = {
  languageSelect: document.querySelector("#languageSelect"),
  detailsButton: document.querySelector("#detailsButton"),
  detailsDialog: document.querySelector("#detailsDialog"),
  emptyStage: document.querySelector("#emptyStage"),
  selectedStage: document.querySelector("#selectedStage"),
  selectImageButton: document.querySelector("#selectImageButton"),
  demoImageButton: document.querySelector("#demoImageButton"),
  changeImageButton: document.querySelector("#changeImageButton"),
  imageInput: document.querySelector("#imageInput"),
  imageSurface: document.querySelector("#imageSurface"),
  previewImage: document.querySelector("#previewImage"),
  imageName: document.querySelector("#imageName"),
  motionLayer: document.querySelector("#motionLayer"),
  motionSource: document.querySelector("#motionSource"),
  motionMark: document.querySelector("#motionMark"),
  motionRights: document.querySelector("#motionRights"),
  motionComplete: document.querySelector("#motionComplete"),
  markPreview: document.querySelector("#markPreview"),
  editMarkButton: document.querySelector("#editMarkButton"),
  markDialog: document.querySelector("#markDialog"),
  markForm: document.querySelector("#markForm"),
  typedTab: document.querySelector("#typedTab"),
  handwrittenTab: document.querySelector("#handwrittenTab"),
  typedPanel: document.querySelector("#typedPanel"),
  handwrittenPanel: document.querySelector("#handwrittenPanel"),
  typedInput: document.querySelector("#typedInput"),
  typedSample: document.querySelector("#typedSample"),
  drawingCanvas: document.querySelector("#drawingCanvas"),
  drawingHint: document.querySelector("#drawingHint"),
  redrawButton: document.querySelector("#redrawButton"),
  addButton: document.querySelector("#addButton"),
  addButtonLabel: document.querySelector("#addButton [data-i18n]"),
  verifyButton: document.querySelector("#verifyButton"),
  verifyFixture: document.querySelector("#verifyFixture"),
  verifySnapshotInputs: document.querySelectorAll('input[name="verifySnapshotMode"]'),
  statusLine: document.querySelector("#statusLine"),
};

const state = {
  locale: preferredLocale(),
  translate: null,
  image: null,
  mark: adapter.loadPersonalMark(),
  draftMark: null,
  statusKey: "statusReady",
  motionGeneration: 0,
  motionTimers: [],
  motionPlayed: false,
  verifyPlayed: false,
  verifySnapshotMode: "typed",
  motionKind: null,
  pointerId: null,
  activeStroke: null,
};

function preferredLocale() {
  try {
    return normalizeLocale(localStorage.getItem("shirushi.canary.locale") || navigator.language);
  } catch {
    return normalizeLocale(navigator.language);
  }
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function setStatus(key) {
  state.statusKey = key;
  elements.statusLine.textContent = state.translate(key);
}

function refreshActionLabels() {
  elements.addButtonLabel.textContent = state.translate(state.motionPlayed ? "addAgain" : "addMark");
  elements.verifyButton.textContent = state.translate(state.verifyPlayed ? "verifyAgain" : "verifyMark");
}

function applyTranslations() {
  state.translate = translator(state.locale);
  document.documentElement.lang = state.locale;
  document.querySelectorAll("[data-i18n]").forEach((element) => {
    element.textContent = state.translate(element.dataset.i18n);
  });
  document.querySelectorAll("[data-i18n-aria]").forEach((element) => {
    element.setAttribute("aria-label", state.translate(element.dataset.i18nAria));
  });
  elements.previewImage.alt = state.translate("selectedImageAlt");
  elements.motionRights.textContent = state.translate("rightsIntent");
  elements.motionComplete.textContent = state.translate(
    state.motionKind === "verify" ? "motionVerified" : "motionComplete",
  );
  elements.motionSource.textContent = state.translate("storedMarkSource");
  elements.imageName.textContent = state.image?.nameKey
    ? state.translate(state.image.nameKey)
    : state.image?.name || "";
  refreshActionLabels();
  setStatus(state.statusKey);
}

function pathData(stroke, width, height) {
  return stroke
    .map((point, index) => `${index === 0 ? "M" : "L"}${(point.x * width).toFixed(2)} ${(point.y * height).toFixed(2)}`)
    .join(" ");
}

function strokeLength(stroke, width, height) {
  let length = 0;
  for (let index = 1; index < stroke.length; index += 1) {
    const previous = stroke[index - 1];
    const current = stroke[index];
    length += Math.hypot((current.x - previous.x) * width, (current.y - previous.y) * height);
  }
  return Math.max(length, 1);
}

function createStrokeSvg(handwritten, className) {
  const { width, height } = handwritten.coordinateSpace;
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.classList.add(className);
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
  svg.setAttribute("aria-hidden", "true");
  handwritten.strokes.forEach((stroke) => {
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", pathData(stroke, width, height));
    svg.append(path);
  });
  return svg;
}

function renderMarkPreview() {
  elements.markPreview.replaceChildren();
  if (state.mark.mode === "typed") {
    const preview = document.createElement("span");
    preview.className = "typed-preview";
    preview.textContent = state.mark.typed;
    elements.markPreview.append(preview);
    return;
  }
  elements.markPreview.append(createStrokeSvg(state.mark.handwritten, "handwritten-preview"));
}

function renderMotionMark(mark) {
  elements.motionMark.replaceChildren();
  elements.motionMark.style.removeProperty("aspect-ratio");
  elements.motionMark.dataset.mode = mark.mode;
  if (mark.mode === "typed") {
    const typed = document.createElement("div");
    typed.className = "motion-typed";
    const aura = document.createElement("span");
    aura.className = "motion-typed-aura";
    aura.textContent = mark.typed;
    aura.setAttribute("aria-hidden", "true");
    const text = document.createElement("span");
    text.className = "motion-typed-text";
    text.textContent = mark.typed;
    const trace = document.createElement("span");
    trace.className = "motion-typed-trace";
    trace.setAttribute("aria-hidden", "true");
    const fragments = document.createElement("span");
    fragments.className = "motion-typed-fragments";
    fragments.setAttribute("aria-hidden", "true");
    typed.append(aura, text, trace, fragments);
    elements.motionMark.append(typed);
    return;
  }

  const handwritten = mark.handwritten;
  const { width, height } = handwritten.coordinateSpace;
  elements.motionMark.style.aspectRatio = `${width} / ${height}`;
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.classList.add("motion-handwritten");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
  svg.setAttribute("aria-hidden", "true");

  const lengths = handwritten.strokes.map((stroke) => strokeLength(stroke, width, height));
  const total = lengths.reduce((sum, value) => sum + value, 0);
  const floor = Math.min(120, 1550 / Math.max(1, handwritten.strokes.length));
  const weightedBudget = Math.max(0, 1550 - floor * handwritten.strokes.length);
  let elapsed = 0;
  handwritten.strokes.forEach((stroke, index) => {
    const duration = Math.round(floor + (weightedBudget * lengths[index]) / total);
    ["diffusion", "glow", "ink"].forEach((className) => {
      const path = document.createElementNS(SVG_NS, "path");
      path.setAttribute("d", pathData(stroke, width, height));
      path.setAttribute("pathLength", "1");
      path.classList.add(className);
      path.style.setProperty("--stroke-delay", `${elapsed}ms`);
      path.style.setProperty("--stroke-duration", `${duration}ms`);
      svg.append(path);
    });
    elapsed += duration;
  });
  elements.motionMark.append(svg);
}

function cancelMotion({ clearStatus = false } = {}) {
  state.motionGeneration += 1;
  state.motionTimers.forEach((timer) => window.clearTimeout(timer));
  state.motionTimers = [];
  elements.motionLayer.classList.remove("playing", "reduced", "motion-add", "motion-verify");
  elements.motionLayer.setAttribute("aria-hidden", "true");
  elements.motionSource.hidden = true;
  refreshActionLabels();
  if (clearStatus) setStatus(state.image ? "statusSelected" : "statusReady");
}

function scheduleMotion(callback, delay, generation) {
  const timer = window.setTimeout(() => {
    if (generation === state.motionGeneration) callback();
  }, delay);
  state.motionTimers.push(timer);
}

async function playMotion(kind) {
  if (!state.image) return;
  const requestedMark = clone(state.mark);
  cancelMotion();
  const generation = state.motionGeneration;
  state.motionKind = kind;
  const verifying = kind === "verify";
  setStatus(verifying ? "statusVerifying" : "statusAdding");
  if (verifying) elements.verifyButton.textContent = state.translate("verifying");
  else elements.addButtonLabel.textContent = state.translate("adding");

  let result;
  try {
    result = verifying
      ? await adapter.inspectStoredSnapshot(state.verifySnapshotMode)
      : await adapter.addMark({ image: state.image, mark: requestedMark });
  } catch {
    if (generation === state.motionGeneration) {
      refreshActionLabels();
      setStatus("statusSelected");
    }
    return;
  }
  const expectedStatus = verifying ? "VERIFIED" : "SUCCESS";
  if (generation !== state.motionGeneration) return;
  if (result.status !== expectedStatus) {
    refreshActionLabels();
    setStatus("statusSelected");
    return;
  }
  const displayMark = verifying ? result.mark : requestedMark;
  if (!displayMark || !["typed", "handwritten"].includes(displayMark.mode)) {
    refreshActionLabels();
    setStatus("statusSelected");
    return;
  }

  renderMotionMark(displayMark);
  elements.motionRights.textContent = state.translate("rightsIntent");
  elements.motionComplete.textContent = state.translate(verifying ? "motionVerified" : "motionComplete");
  elements.motionSource.textContent = state.translate("storedMarkSource");
  elements.motionSource.hidden = !verifying;
  elements.motionLayer.setAttribute("aria-hidden", "false");
  // Restarting the CSS animation is intentional: the action remains available
  // throughout the sequence instead of locking the rest of the interface.
  void elements.motionLayer.offsetWidth;
  const reduced = motionPreview === "reduce" || (motionPreview !== "normal" && reduceMotion.matches);
  elements.motionLayer.classList.add(reduced ? "reduced" : "playing", `motion-${kind}`);

  const total = reduced ? REDUCED_MOTION_MS : NORMAL_MOTION_MS;
  scheduleMotion(
    () => setStatus(verifying ? "statusVerified" : "statusComplete"),
    reduced ? 140 : 3260,
    generation,
  );
  scheduleMotion(() => {
    elements.motionLayer.classList.remove("playing", "reduced", "motion-add", "motion-verify");
    elements.motionLayer.setAttribute("aria-hidden", "true");
    elements.motionSource.hidden = true;
    if (verifying) {
      state.verifyPlayed = true;
      elements.verifyButton.textContent = state.translate("verifyAgain");
      setStatus("statusVerified");
    } else {
      state.motionPlayed = true;
      elements.addButtonLabel.textContent = state.translate("addAgain");
      setStatus("statusComplete");
    }
  }, total, generation);
}

function playAddMotion() {
  return playMotion("add");
}

function playVerifyMotion() {
  return playMotion("verify");
}

function setImage(record) {
  cancelMotion();
  state.image = null;
  elements.addButton.disabled = true;
  elements.verifyButton.disabled = true;
  elements.verifyFixture.disabled = true;
  elements.previewImage.onload = () => {
    const ratio = elements.previewImage.naturalWidth / elements.previewImage.naturalHeight;
    if (!Number.isFinite(ratio) || ratio <= 0) return;
    elements.imageSurface.style.setProperty("--image-ratio", ratio);
    state.image = record;
    state.motionPlayed = false;
    state.verifyPlayed = false;
    elements.emptyStage.hidden = true;
    elements.selectedStage.hidden = false;
    elements.imageName.textContent = record.nameKey ? state.translate(record.nameKey) : record.name;
    elements.addButton.disabled = false;
    elements.verifyButton.disabled = false;
    elements.verifyFixture.disabled = false;
    elements.addButtonLabel.textContent = state.translate("addMark");
    elements.verifyButton.textContent = state.translate("verifyMark");
    setStatus("statusSelected");
  };
  elements.previewImage.onerror = () => {
    state.image = null;
    elements.emptyStage.hidden = false;
    elements.selectedStage.hidden = true;
    elements.addButton.disabled = true;
    elements.verifyButton.disabled = true;
    elements.verifyFixture.disabled = true;
    setStatus("imageLoadFailed");
  };
  elements.previewImage.src = record.url;
}

function chooseImage() {
  elements.imageInput.value = "";
  elements.imageInput.click();
}

function setDraftMode(mode) {
  state.draftMark.mode = mode;
  const typed = mode === "typed";
  elements.typedPanel.hidden = !typed;
  elements.handwrittenPanel.hidden = typed;
  elements.typedTab.setAttribute("aria-selected", String(typed));
  elements.handwrittenTab.setAttribute("aria-selected", String(!typed));
  elements.typedTab.tabIndex = typed ? 0 : -1;
  elements.handwrittenTab.tabIndex = typed ? -1 : 0;
  if (!typed) window.requestAnimationFrame(drawDraftCanvas);
}

function openMarkDialog() {
  state.draftMark = clone(state.mark);
  elements.typedInput.value = state.draftMark.typed;
  elements.typedSample.textContent = state.draftMark.typed;
  setDraftMode(state.draftMark.mode);
  elements.markDialog.showModal();
}

function normalizedCanvasPoint(event) {
  const rect = elements.drawingCanvas.getBoundingClientRect();
  return {
    x: Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)),
    y: Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height)),
  };
}

function drawDraftCanvas() {
  const canvas = elements.drawingCanvas;
  const context = canvas.getContext("2d");
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.lineWidth = 4;
  context.lineCap = "round";
  context.lineJoin = "round";
  context.strokeStyle = "#18332d";
  context.fillStyle = "#18332d";
  const strokes = state.draftMark?.handwritten?.strokes || [];
  strokes.forEach((stroke) => {
    if (stroke.length === 1) {
      context.beginPath();
      context.arc(stroke[0].x * canvas.width, stroke[0].y * canvas.height, 2, 0, Math.PI * 2);
      context.fill();
      return;
    }
    context.beginPath();
    stroke.forEach((point, index) => {
      const x = point.x * canvas.width;
      const y = point.y * canvas.height;
      if (index === 0) context.moveTo(x, y);
      else context.lineTo(x, y);
    });
    context.stroke();
  });
  elements.drawingCanvas.parentElement.classList.toggle("has-strokes", strokes.length > 0);
}

function pointerDown(event) {
  if (!state.draftMark || state.draftMark.mode !== "handwritten") return;
  event.preventDefault();
  state.pointerId = event.pointerId;
  state.activeStroke = [normalizedCanvasPoint(event)];
  state.draftMark.handwritten.strokes.push(state.activeStroke);
  elements.drawingCanvas.setPointerCapture(event.pointerId);
  drawDraftCanvas();
}

function pointerMove(event) {
  if (event.pointerId !== state.pointerId || !state.activeStroke) return;
  event.preventDefault();
  const point = normalizedCanvasPoint(event);
  const previous = state.activeStroke[state.activeStroke.length - 1];
  if (Math.hypot(point.x - previous.x, point.y - previous.y) < 0.003) return;
  state.activeStroke.push(point);
  drawDraftCanvas();
}

function pointerUp(event) {
  if (event.pointerId !== state.pointerId) return;
  event.preventDefault();
  if (elements.drawingCanvas.hasPointerCapture(event.pointerId)) {
    elements.drawingCanvas.releasePointerCapture(event.pointerId);
  }
  state.pointerId = null;
  state.activeStroke = null;
  drawDraftCanvas();
}

function saveDraft(event) {
  if (event.submitter?.value !== "default") return;
  event.preventDefault();
  state.draftMark.typed = elements.typedInput.value.trim();
  if (state.draftMark.mode === "typed" && !state.draftMark.typed) {
    elements.typedInput.setCustomValidity(state.translate("typedRequired"));
    elements.typedInput.reportValidity();
    return;
  }
  if (state.draftMark.mode === "handwritten" && !state.draftMark.handwritten.strokes.length) {
    setStatus("drawingRequired");
    return;
  }
  elements.typedInput.setCustomValidity("");
  state.mark = adapter.savePersonalMark(state.draftMark);
  renderMarkPreview();
  cancelMotion();
  state.motionPlayed = false;
  elements.addButtonLabel.textContent = state.translate("addMark");
  elements.markDialog.close("default");
  setStatus(state.image ? "statusSelected" : "statusReady");
}

elements.languageSelect.addEventListener("change", () => {
  state.locale = normalizeLocale(elements.languageSelect.value);
  try {
    localStorage.setItem("shirushi.canary.locale", state.locale);
  } catch {
    // Locale persistence is optional in the Canary.
  }
  applyTranslations();
});

elements.selectImageButton.addEventListener("click", chooseImage);
elements.changeImageButton.addEventListener("click", chooseImage);
elements.demoImageButton.addEventListener("click", () => setImage(adapter.demoImage()));
elements.imageInput.addEventListener("change", () => {
  const [file] = elements.imageInput.files;
  if (!file) return;
  try {
    setImage(adapter.imageFromFile(file));
  } catch {
    setStatus("imageLoadFailed");
  }
});

elements.editMarkButton.addEventListener("click", openMarkDialog);
elements.detailsButton.addEventListener("click", () => elements.detailsDialog.showModal());
elements.addButton.addEventListener("click", playAddMotion);
elements.verifyButton.addEventListener("click", playVerifyMotion);
elements.verifySnapshotInputs.forEach((input) => {
  input.addEventListener("change", () => {
    if (!input.checked) return;
    state.verifySnapshotMode = input.value;
    state.verifyPlayed = false;
    cancelMotion({ clearStatus: true });
  });
});

document.querySelectorAll("[data-mark-mode]").forEach((tab) => {
  tab.addEventListener("click", () => setDraftMode(tab.dataset.markMode));
});
elements.typedInput.addEventListener("input", () => {
  elements.typedInput.setCustomValidity("");
  state.draftMark.typed = elements.typedInput.value;
  elements.typedSample.textContent = elements.typedInput.value || "　";
});
elements.redrawButton.addEventListener("click", () => {
  state.draftMark.handwritten = {
    coordinateSpace: { width: DRAW_WIDTH, height: DRAW_HEIGHT },
    strokes: [],
  };
  drawDraftCanvas();
});
elements.drawingCanvas.addEventListener("pointerdown", pointerDown);
elements.drawingCanvas.addEventListener("pointermove", pointerMove);
elements.drawingCanvas.addEventListener("pointerup", pointerUp);
elements.drawingCanvas.addEventListener("pointercancel", pointerUp);
elements.markForm.addEventListener("submit", saveDraft);
elements.markDialog.addEventListener("close", () => {
  state.pointerId = null;
  state.activeStroke = null;
  state.draftMark = null;
});
window.addEventListener("beforeunload", () => adapter.releaseImage());

elements.languageSelect.value = state.locale;
applyTranslations();
renderMarkPreview();
setStatus("statusReady");
