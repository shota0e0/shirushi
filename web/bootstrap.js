import { assertAdapter, isCoreSuccess, isPreviewOutcome, operationEnabled } from "./contracts.js";
import { GenerationGuard, MotionScheduler } from "./generation-guard.js";
import { blankPersonalMarkDraft, clone, DRAW_HEIGHT, DRAW_WIDTH } from "./mark.js";
import { MESSAGES, normalizeLocale, translator } from "./i18n.js";
import { createStrokeSvg, motionCopyKeys, NORMAL_MOTION_MS, REDUCED_MOTION_MS, renderMotionMark, resetMotionClasses, shouldReduceMotion } from "./motion.js";
import { mountShell } from "./shell.js";

function preferredLocale(windowRef) {
  return normalizeLocale(windowRef.navigator?.language || "ja");
}

export function bootstrapShirushi({ root, adapter, previewTools = null, presentation = null, windowRef = window, documentRef = document }) {
  assertAdapter(adapter);
  if (!root) throw new TypeError("ROOT_REQUIRED");
  const elements = mountShell(root);
  const motion = new MotionScheduler(windowRef.setTimeout.bind(windowRef), windowRef.clearTimeout.bind(windowRef));
  const imageGuard = new GenerationGuard();
  const queryMotion = new URLSearchParams(windowRef.location.search).get("motion");
  const mediaReduce = windowRef.matchMedia("(prefers-reduced-motion: reduce)");
  const isDevelopmentPreview = adapter.capabilities.previewAddMotion || adapter.capabilities.previewVerifyMotion;
  const previewElements = previewTools ? previewTools({ slot: elements.previewToolsSlot, documentRef }) : { fixtureInputs: [] };
  const state = {
    locale: preferredLocale(windowRef), translate: null, image: null, mark: adapter.loadSessionMark(), draft: null,
    statusKey: isDevelopmentPreview ? "previewReady" : "statusReady", motionPlayed: false, verifyPlayed: false,
    motionKind: null, fixtureMode: "typed", pointerId: null, activeStroke: null,
  };

  function setStatus(key) {
    state.statusKey = key;
    elements.statusLine.textContent = state.translate(key);
  }

  function actionEnabled(kind) {
    return operationEnabled(adapter.capabilities, kind, { hasImage: Boolean(state.image), hasMark: Boolean(state.mark) });
  }

  function refreshActions() {
    elements.editMarkButton.disabled = !adapter.capabilities.sessionMarkEdit;
    elements.addButton.disabled = !actionEnabled("add");
    elements.verifyButton.disabled = !actionEnabled("verify");
    elements.addButtonLabel.textContent = state.translate(state.motionPlayed ? "addAgain" : "addMark");
    elements.verifyButton.textContent = state.translate(state.verifyPlayed ? "verifyAgain" : "verifyMark");
    elements.capabilityNote.textContent = isDevelopmentPreview ? state.translate("previewReady") : state.translate("capabilityUnavailable");
  }

  function applyTranslations() {
    state.translate = translator(state.locale);
    documentRef.documentElement.lang = state.locale;
    root.querySelectorAll("[data-i18n]").forEach((node) => { node.textContent = state.translate(node.dataset.i18n); });
    root.querySelectorAll("[data-i18n-aria]").forEach((node) => { node.setAttribute("aria-label", state.translate(node.dataset.i18nAria)); });
    elements.previewImage.alt = state.translate("selectedImageAlt");
    elements.motionRights.textContent = state.translate("rightsIntent");
    elements.motionComplete.textContent = state.translate(state.motionKind === "verify" ? "motionVerify" : "motionAdd");
    elements.motionSource.textContent = state.translate("storedMarkSource");
    elements.languageSelect.value = state.locale;
    renderMarkPreview();
    refreshActions();
    setStatus(state.statusKey);
    presentation?.render({ elements, locale: state.locale, documentRef });
  }

  function renderMarkPreview() {
    elements.markPreview.replaceChildren();
    if (!state.mark) {
      const empty = documentRef.createElement("span");
      empty.className = "mark-empty";
      empty.textContent = markPreviewText(state.mark, state.translate);
      elements.markPreview.append(empty);
      return;
    }
    if (state.mark.mode === "typed") {
      const span = documentRef.createElement("span");
      span.className = "typed-preview";
      span.textContent = state.mark.typed;
      elements.markPreview.append(span);
    } else {
      elements.markPreview.append(createStrokeSvg(documentRef, state.mark.handwritten, "handwritten-preview"));
    }
  }

  function resetMotionVisuals() {
    resetMotionClasses(elements.motionLayer.classList);
    elements.motionLayer.setAttribute("aria-hidden", "true");
    elements.motionSource.hidden = true;
  }

  function clearMotion() {
    motion.cancel();
    resetMotionVisuals();
  }

  async function playMotion(kind) {
    if (!actionEnabled(kind)) return;
    const generation = motion.begin();
    resetMotionVisuals();
    state.motionKind = kind;
    const verify = kind === "verify";
    const copyKeys = motionCopyKeys(kind);
    elements.motionComplete.textContent = state.translate(copyKeys.complete);
    setStatus(copyKeys.progress);
    let result;
    try {
      result = verify
        ? await adapter.verifyFileMark({ targetReference: state.image.reference, options: { fixtureMode: state.fixtureMode } })
        : await adapter.addMark({ targetReference: state.image.reference, mark: clone(state.mark) });
    } catch {
      if (motion.accepts(generation)) setStatus("statusSelected");
      return;
    }
    if (!motion.accepts(generation)) return;
    const accepted = (verify ? adapter.capabilities.coreVerify : adapter.capabilities.coreAdd)
      ? isCoreSuccess(result, kind)
      : isPreviewOutcome(result, kind);
    if (!accepted || !result.mark) {
      setStatus("statusSelected");
      return;
    }
    renderMotionMark(documentRef, elements.motionMark, result.mark);
    elements.motionSource.hidden = !verify;
    elements.motionLayer.setAttribute("aria-hidden", "false");
    void elements.motionLayer.offsetWidth;
    const reduced = shouldReduceMotion(queryMotion, mediaReduce.matches);
    elements.motionLayer.classList.add(reduced ? "reduced" : "playing", `motion-${kind}`);
    const doneKey = copyKeys.done;
    motion.schedule(generation, () => setStatus(doneKey), reduced ? 140 : 3260);
    motion.schedule(generation, () => {
      resetMotionClasses(elements.motionLayer.classList);
      elements.motionLayer.setAttribute("aria-hidden", "true");
      elements.motionSource.hidden = true;
      if (verify) state.verifyPlayed = true;
      else state.motionPlayed = true;
      refreshActions();
      setStatus(doneKey);
    }, reduced ? REDUCED_MOTION_MS : NORMAL_MOTION_MS);
  }

  function setImage(record) {
    clearMotion();
    const generation = imageGuard.next();
    state.image = null;
    refreshActions();
    elements.previewImage.onload = () => {
      if (!imageGuard.accepts(generation)) return;
      const ratio = elements.previewImage.naturalWidth / elements.previewImage.naturalHeight;
      if (!Number.isFinite(ratio) || ratio <= 0) return;
      elements.imageSurface.style.setProperty("--image-ratio", ratio);
      state.image = { ...record, reference: `local:${record.name}` };
      state.motionPlayed = false;
      state.verifyPlayed = false;
      elements.emptyStage.hidden = true;
      elements.selectedStage.hidden = false;
      elements.imageName.textContent = record.name;
      refreshActions();
      setStatus("statusSelected");
    };
    elements.previewImage.onerror = () => {
      if (!imageGuard.accepts(generation)) return;
      state.image = null;
      elements.emptyStage.hidden = false;
      elements.selectedStage.hidden = true;
      refreshActions();
      setStatus("imageLoadFailed");
    };
    elements.previewImage.src = record.url;
  }

  function chooseImage() {
    elements.imageInput.value = "";
    elements.imageInput.click();
  }

  function setDraftMode(mode) {
    state.draft.mode = mode;
    const typed = mode === "typed";
    elements.typedPanel.hidden = !typed;
    elements.handwrittenPanel.hidden = typed;
    elements.typedTab.setAttribute("aria-selected", String(typed));
    elements.handwrittenTab.setAttribute("aria-selected", String(!typed));
    if (!typed) windowRef.requestAnimationFrame(drawDraftCanvas);
  }

  function openMarkDialog() {
    if (!adapter.capabilities.sessionMarkEdit) return;
    state.draft = state.mark ? clone(state.mark) : blankPersonalMarkDraft();
    elements.typedInput.value = state.draft.typed;
    elements.typedSample.textContent = state.draft.typed;
    setDraftMode(state.draft.mode);
    elements.markDialog.showModal();
  }

  function drawDraftCanvas() {
    const canvas = elements.drawingCanvas;
    const context = canvas.getContext("2d");
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.lineWidth = 4; context.lineCap = "round"; context.lineJoin = "round"; context.strokeStyle = "#11110f"; context.fillStyle = "#11110f";
    const strokes = state.draft?.handwritten?.strokes || [];
    for (const stroke of strokes) {
      context.beginPath();
      stroke.forEach((point, index) => index ? context.lineTo(point.x * canvas.width, point.y * canvas.height) : context.moveTo(point.x * canvas.width, point.y * canvas.height));
      if (stroke.length === 1) context.arc(stroke[0].x * canvas.width, stroke[0].y * canvas.height, 2, 0, Math.PI * 2);
      stroke.length === 1 ? context.fill() : context.stroke();
    }
    canvas.parentElement.classList.toggle("has-strokes", strokes.length > 0);
  }

  function canvasPoint(event) {
    const rect = elements.drawingCanvas.getBoundingClientRect();
    return { x: Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)), y: Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height)) };
  }

  function pointerDown(event) {
    if (!state.draft || state.draft.mode !== "handwritten") return;
    event.preventDefault(); state.pointerId = event.pointerId; state.activeStroke = [canvasPoint(event)]; state.draft.handwritten.strokes.push(state.activeStroke);
    elements.drawingCanvas.setPointerCapture(event.pointerId); drawDraftCanvas();
  }

  function pointerMove(event) {
    if (event.pointerId !== state.pointerId || !state.activeStroke) return;
    event.preventDefault(); const point = canvasPoint(event); const previous = state.activeStroke.at(-1);
    if (Math.hypot(point.x - previous.x, point.y - previous.y) >= 0.003) { state.activeStroke.push(point); drawDraftCanvas(); }
  }

  function pointerUp(event) {
    if (event.pointerId !== state.pointerId) return;
    event.preventDefault(); if (elements.drawingCanvas.hasPointerCapture(event.pointerId)) elements.drawingCanvas.releasePointerCapture(event.pointerId);
    state.pointerId = null; state.activeStroke = null; drawDraftCanvas();
  }

  function saveDraft(event) {
    if (!adapter.capabilities.sessionMarkEdit) { event.preventDefault(); return; }
    if (event.submitter?.value !== "default") return;
    event.preventDefault(); state.draft.typed = elements.typedInput.value.trim();
    if (state.draft.mode === "typed" && !state.draft.typed) { elements.typedInput.setCustomValidity(state.translate("typedRequired")); elements.typedInput.reportValidity(); return; }
    if (state.draft.mode === "handwritten" && !state.draft.handwritten.strokes.length) { setStatus("drawingRequired"); return; }
    elements.typedInput.setCustomValidity(""); state.mark = adapter.saveSessionMark(state.draft); renderMarkPreview(); clearMotion();
    state.motionPlayed = false; state.verifyPlayed = false; elements.markDialog.close("default"); refreshActions(); setStatus(state.image ? "statusSelected" : "statusReady");
  }

  elements.devBanner.hidden = !isDevelopmentPreview;
  elements.languageSelect.addEventListener("change", () => { state.locale = normalizeLocale(elements.languageSelect.value); applyTranslations(); });
  elements.selectImageButton.addEventListener("click", chooseImage); elements.changeImageButton.addEventListener("click", chooseImage);
  elements.imageInput.addEventListener("change", () => { const file = elements.imageInput.files?.[0]; if (!file) return; try { setImage(adapter.imageFromFile(file)); } catch { setStatus("imageLoadFailed"); } });
  elements.editMarkButton.addEventListener("click", openMarkDialog); elements.detailsButton.addEventListener("click", () => elements.detailsDialog.showModal());
  elements.addButton.addEventListener("click", () => playMotion("add")); elements.verifyButton.addEventListener("click", () => playMotion("verify"));
  previewElements.fixtureInputs.forEach((input) => input.addEventListener("change", () => { if (input.checked) { state.fixtureMode = input.value; state.verifyPlayed = false; clearMotion(); refreshActions(); } }));
  root.querySelectorAll("[data-mark-mode]").forEach((tab) => tab.addEventListener("click", () => setDraftMode(tab.dataset.markMode)));
  elements.typedInput.addEventListener("input", () => { elements.typedInput.setCustomValidity(""); state.draft.typed = elements.typedInput.value; elements.typedSample.textContent = elements.typedInput.value || "　"; });
  elements.redrawButton.addEventListener("click", () => { state.draft.handwritten = { coordinateSpace: { width: DRAW_WIDTH, height: DRAW_HEIGHT }, strokes: [] }; drawDraftCanvas(); });
  elements.drawingCanvas.addEventListener("pointerdown", pointerDown); elements.drawingCanvas.addEventListener("pointermove", pointerMove); elements.drawingCanvas.addEventListener("pointerup", pointerUp); elements.drawingCanvas.addEventListener("pointercancel", pointerUp);
  elements.markForm.addEventListener("submit", saveDraft); elements.markDialog.addEventListener("close", () => { state.pointerId = null; state.activeStroke = null; state.draft = null; });
  windowRef.addEventListener("beforeunload", () => { clearMotion(); imageGuard.invalidate(); adapter.releaseImage(); });

  applyTranslations(); renderMarkPreview(); setStatus(state.statusKey);
  return Object.freeze({ capabilities: adapter.capabilities, refreshPresentation: applyTranslations, destroy: () => { clearMotion(); imageGuard.invalidate(); adapter.releaseImage(); presentation?.destroy?.(); } });
}

export function markPreviewText(mark, translate) {
  return mark ? null : translate("noMark");
}

export function localeKeyParity() {
  const expected = Object.keys(MESSAGES.ja).sort();
  return Object.fromEntries(Object.entries(MESSAGES).map(([locale, messages]) => [locale, JSON.stringify(Object.keys(messages).sort()) === JSON.stringify(expected)]));
}
