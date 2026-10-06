import { assertAdapter, isCoreSuccess, isPreviewOutcome, operationEnabled } from "./contracts.js";
import { GenerationGuard, MotionScheduler } from "./generation-guard.js";
import { blankPersonalMarkDraft, clone, sanitizeMark } from "./mark.js";
import { MESSAGES, normalizeLocale, translator } from "./i18n.js";
import { createStrokeSvg, motionCopyKeys, NORMAL_MOTION_MS, REDUCED_MOTION_MS, renderMotionMark, resetMotionClasses, shouldReduceMotion } from "./motion.js";
import { mountShell } from "./shell.js";

function preferredLocale(windowRef) {
  return normalizeLocale(windowRef.navigator?.language || "ja");
}

export function bootstrapShirushi({ root, adapter, presentation = null, windowRef = window, documentRef = document }) {
  assertAdapter(adapter);
  if (!root) throw new TypeError("ROOT_REQUIRED");
  const elements = mountShell(root);
  const motion = new MotionScheduler(windowRef.setTimeout.bind(windowRef), windowRef.clearTimeout.bind(windowRef));
  const imageGuard = new GenerationGuard();
  const queryMotion = new URLSearchParams(windowRef.location.search).get("motion");
  const mediaReduce = windowRef.matchMedia("(prefers-reduced-motion: reduce)");
  const isDevelopmentPreview = adapter.capabilities.previewAddMotion || adapter.capabilities.previewVerifyMotion;
  // Separate decorative entry motion; never touches applied/verification state.
  function syncEntryMotion() {
    elements.emptyStage.setAttribute("data-entry-motion", shouldReduceMotion(queryMotion, mediaReduce.matches) ? "reduce" : "normal");
  }
  syncEntryMotion();
  mediaReduce.addEventListener?.("change", syncEntryMotion);
  const state = {
    locale: preferredLocale(windowRef), translate: null, image: null, mark: adapter.loadSessionMark(), draft: null,
    statusKey: "statusReady", motionPlayed: false, verifyPlayed: false,
    motionKind: null, pointerId: null, activeStroke: null,
    targetMarked: false, operationPending: false, imageLoading: false,
    selectionPending: false, selectionAction: null, editorContinuation: null, destroyed: false,
    appliedResult: null,
  };

  function setStatus(key) {
    state.statusKey = key;
    elements.statusLine.textContent = state.translate(key);
    elements.statusLine.hidden = !state.image && key !== "imageLoadFailed";
  }

  function actionEnabled(kind) {
    if (state.destroyed || state.operationPending || state.imageLoading || state.selectionPending) return false;
    return operationEnabled(adapter.capabilities, kind, { hasImage: Boolean(state.image), hasMark: Boolean(state.mark) });
  }

  // Entry permission is not permission to execute without an image.
  function entryEnabled(kind) {
    if (state.destroyed || state.operationPending || state.imageLoading || state.selectionPending || state.editorContinuation) return false;
    const supported = kind === "add"
      ? adapter.capabilities.coreAdd || adapter.capabilities.previewAddMotion
      : adapter.capabilities.coreVerify || adapter.capabilities.previewVerifyMotion;
    return supported && adapter.capabilities.localImagePreview;
  }

  function refreshActions() {
    const hasImage = Boolean(state.image);
    elements.markSummary.hidden = false;
    elements.primaryActions.hidden = false;
    elements.changeImageButton.hidden = !hasImage;
    elements.imageName.textContent = hasImage ? state.image.name : "";
    elements.rightsIntent.hidden = !hasImage || !state.targetMarked;
    elements.editMarkButton.disabled = !hasImage || !adapter.capabilities.sessionMarkEdit || state.operationPending || state.imageLoading || state.selectionPending;
    elements.addButton.hidden = false;
    elements.addButton.disabled = !entryEnabled("add") || (hasImage && !state.mark && !adapter.capabilities.sessionMarkEdit);
    elements.verifyButton.hidden = false;
    elements.verifyButton.disabled = !entryEnabled("verify");
    elements.selectImageButton.disabled = state.destroyed || state.operationPending || state.imageLoading || state.selectionPending;
    elements.changeImageButton.disabled = elements.selectImageButton.disabled;
    elements.addButtonLabel.textContent = state.translate(state.motionPlayed ? "addAgain" : "addMark");
    elements.verifyButtonLabel.textContent = state.translate(state.targetMarked ? "verifyAgain" : "verifyMark");
    elements.capabilityNote.textContent = isDevelopmentPreview ? state.translate("previewReady") : state.translate("capabilityUnavailable");
  }

  function applyTranslations() {
    state.translate = translator(state.locale);
    documentRef.documentElement.lang = state.locale;
    root.querySelectorAll("[data-i18n]").forEach((node) => { node.textContent = state.translate(node.dataset.i18n); });
    root.querySelectorAll("[data-i18n-aria]").forEach((node) => { node.setAttribute("aria-label", state.translate(node.dataset.i18nAria)); });
    elements.previewImage.alt = state.translate("selectedImageAlt");
    elements.motionRights.textContent = state.translate("rightsIntent");
    elements.motionSource.textContent = state.translate("storedMarkSource");
    elements.languageSelect.value = state.locale;
    renderMarkPreview();
    refreshActions();
    setStatus(state.statusKey);
    presentation?.render({ elements, locale: state.locale, documentRef });
  }

  function renderMarkPreview() {
    elements.markPreview.replaceChildren();
    if (!state.image || !state.mark) {
      const empty = documentRef.createElement("span");
      empty.className = "mark-empty";
      empty.textContent = state.translate("noMark");
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
    elements.motionRights.hidden = true;
  }

  function clearMotion() {
    motion.cancel();
    state.operationPending = false;
    resetMotionVisuals();
  }

  async function playMotion(kind) {
    if (!actionEnabled(kind)) return;
    const generation = motion.begin();
    resetMotionVisuals();
    state.motionKind = kind;
    state.operationPending = true;
    refreshActions();
    const verify = kind === "verify";
    const copyKeys = motionCopyKeys(kind);
    setStatus(isDevelopmentPreview ? copyKeys.progress : verify ? "statusLimitedRunning" : "statusNativeAdding");
    let result;
    try {
      result = !verify && state.motionPlayed && state.appliedResult ? state.appliedResult : verify
        ? await adapter.verifyFileMark({ targetReference: state.image.reference })
        : await adapter.addMark({ targetReference: state.image.reference, mark: clone(state.mark) });
    } catch {
      if (motion.accepts(generation)) { if (verify) state.targetMarked = false; state.operationPending = false; refreshActions(); setStatus(verify ? "statusVerifyFailed" : isDevelopmentPreview ? "statusSelected" : "statusNativeAddFailed"); }
      return;
    }
    if (!motion.accepts(generation)) return;
    if (verify && result?.status === "LIMITED_INSPECTION") {
      // A native limited inspection is not a VERIFIED/full-success animation.
      state.targetMarked = result.intentPresent === true;
      state.operationPending = false;
      refreshActions();
      setStatus(result.intentPresent ? "statusLimitedIntentPresent" : "statusLimitedIntentAbsent");
      return;
    }
    const accepted = (verify ? adapter.capabilities.coreVerify : adapter.capabilities.coreAdd)
      ? isCoreSuccess(result, kind)
      : isPreviewOutcome(result, kind);
    const resultMark = sanitizeMark(result?.mark);
    if (!accepted || !resultMark) {
      if (verify) state.targetMarked = false;
      state.operationPending = false;
      refreshActions();
      const previewUnmarked = verify && !adapter.capabilities.coreVerify && result?.status === "PREVIEW_UNMARKED";
      setStatus(verify ? (previewUnmarked ? "statusVerifyUnmarkedPreview" : "statusVerifyFailed") : isDevelopmentPreview ? "statusSelected" : "statusNativeAddFailed");
      return;
    }
    state.targetMarked = true;
    if (!verify && result.outputImage) {
      state.image = result.outputImage;
      elements.previewImage.src = result.outputImage.url;
      elements.imageName.textContent = result.outputImage.name;
      state.appliedResult = result;
    }
    refreshActions();
    renderMotionMark(documentRef, elements.motionMark, resultMark, { kind, imageAspectRatio: elements.previewImage.naturalWidth / elements.previewImage.naturalHeight });
    elements.motionSource.hidden = !verify;
    elements.motionRights.hidden = false;
    elements.motionLayer.setAttribute("aria-hidden", "false");
    void elements.motionLayer.offsetWidth;
    const reduced = shouldReduceMotion(queryMotion, mediaReduce.matches);
    elements.motionLayer.classList.add(reduced ? "reduced" : "playing", `motion-${kind}`);
    const doneKey = isDevelopmentPreview ? copyKeys.done : "statusNativeAddDone";
    // Let the mark finish first; the existing generation-bound cleanup publishes
    // completion once, without a second timer or an early competing announcement.
    motion.schedule(generation, () => {
      resetMotionVisuals();
      state.operationPending = false;
      if (verify) state.verifyPlayed = true;
      else state.motionPlayed = true;
      refreshActions();
      setStatus(doneKey);
    }, reduced ? REDUCED_MOTION_MS : NORMAL_MOTION_MS);
  }

  function resetImageState() {
    clearMotion();
    state.image = null;
    state.imageLoading = false;
    state.editorContinuation = null;
    if (elements.markDialog.open) elements.markDialog.close("cancel");
    state.targetMarked = false;
    state.motionPlayed = false;
    state.verifyPlayed = false;
    state.appliedResult = null;
    elements.emptyStage.hidden = false;
    elements.selectedStage.hidden = true;
    renderMarkPreview();
    refreshActions();
  }

  function failImageLoad() {
    imageGuard.invalidate();
    resetImageState();
    setStatus("imageLoadFailed");
  }

  function setImage(record, continuation = null) {
    resetImageState();
    state.imageLoading = true;
    refreshActions();
    const generation = imageGuard.next();
    let settled = false;
    setStatus("statusReady");
    elements.previewImage.onload = () => {
      if (settled || state.destroyed || !imageGuard.accepts(generation)) return;
      const ratio = elements.previewImage.naturalWidth / elements.previewImage.naturalHeight;
      if (!Number.isFinite(ratio) || ratio <= 0) { failImageLoad(); return; }
      settled = true;
      elements.imageSurface.style.setProperty("--image-ratio", ratio);
      // A reselected / same-name file is a different target, not prior proof.
      state.image = { ...record, reference: record.reference || `local:${generation}` };
      state.imageLoading = false;
      state.motionPlayed = false;
      state.verifyPlayed = false;
      elements.emptyStage.hidden = true;
      elements.selectedStage.hidden = false;
      elements.imageName.textContent = record.name;
      renderMarkPreview();
      refreshActions();
      setStatus("statusSelected");
      // The captured action belongs only to this successfully decoded generation.
      if (continuation) void startAction(continuation);
    };
    elements.previewImage.onerror = () => {
      if (settled || !imageGuard.accepts(generation)) return;
      failImageLoad();
    };
    elements.previewImage.src = record.url;
  }

  async function chooseImage(kind = null) {
    if (state.destroyed || state.operationPending || state.imageLoading || state.selectionPending) return;
    state.selectionAction = kind;
    state.selectionPending = true;
    refreshActions();
    if (typeof adapter.selectImageFromNative === "function" && adapter.capabilities.coreAdd) {
      try {
        const record = await adapter.selectImageFromNative();
        if (state.destroyed) return;
        cancelSelection();
        if (record) setImage(record, kind);
      } catch { cancelSelection(); failImageLoad(); }
      return;
    }
    elements.imageInput.value = "";
    elements.imageInput.click();
  }

  function cancelSelection() {
    state.selectionAction = null;
    state.selectionPending = false;
    refreshActions();
  }

  async function startAction(kind) {
    if (!entryEnabled(kind)) return;
    if (!state.image) { chooseImage(kind); return; }
    if (kind === "add" && !state.mark) {
      openMarkDialog({ kind, reference: state.image.reference });
      refreshActions();
      return;
    }
    await playMotion(kind);
  }

  function setDraftMode(mode) {
    endDraftStroke();
    state.draft.mode = mode;
    const typed = mode === "typed";
    elements.typedPanel.hidden = !typed;
    elements.handwrittenPanel.hidden = typed;
    elements.typedTab.setAttribute("aria-selected", String(typed));
    elements.handwrittenTab.setAttribute("aria-selected", String(!typed));
    if (!typed) windowRef.requestAnimationFrame(drawDraftCanvas);
  }

  function openMarkDialog(continuation = null) {
    if (state.destroyed || state.operationPending || state.imageLoading || state.selectionPending || !state.image || !adapter.capabilities.sessionMarkEdit) return;
    endDraftStroke();
    state.editorContinuation = continuation;
    state.draft = state.mark ? clone(state.mark) : blankPersonalMarkDraft();
    elements.typedInput.value = state.draft.typed;
    elements.typedSample.textContent = state.draft.typed;
    setDraftMode(state.draft.mode);
    elements.markDialog.showModal();
  }

  function endDraftStroke() {
    if (state.pointerId !== null && elements.drawingCanvas.hasPointerCapture(state.pointerId)) {
      elements.drawingCanvas.releasePointerCapture(state.pointerId);
    }
    state.pointerId = null;
    state.activeStroke = null;
  }

  function clearDrawing() {
    if (!state.draft) return;
    endDraftStroke();
    // All strokes only; preserve the draft's capture geometry and typed choice.
    state.draft.handwritten.strokes = [];
    drawDraftCanvas();
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
    if (state.destroyed || !state.image || !state.draft || !elements.markDialog.open) { event.preventDefault(); return; }
    if (!adapter.capabilities.sessionMarkEdit) { event.preventDefault(); return; }
    if (event.submitter?.value !== "default") { state.editorContinuation = null; endDraftStroke(); return; }
    event.preventDefault(); state.draft.typed = elements.typedInput.value.trim();
    if (state.draft.mode === "typed" && !state.draft.typed) { elements.typedInput.setCustomValidity(state.translate("typedRequired")); elements.typedInput.reportValidity(); return; }
    if (state.draft.mode === "handwritten" && !state.draft.handwritten.strokes.length) { setStatus("drawingRequired"); return; }
    const continuation = state.editorContinuation;
    state.editorContinuation = null;
    elements.typedInput.setCustomValidity(""); state.mark = adapter.saveSessionMark(state.draft); renderMarkPreview(); clearMotion();
    state.motionPlayed = false; state.verifyPlayed = false; elements.markDialog.close("default"); refreshActions(); setStatus(state.image ? "statusSelected" : "statusReady");
    if (continuation && state.image?.reference === continuation.reference) void startAction(continuation.kind);
  }

  elements.devBanner.hidden = !isDevelopmentPreview;
  elements.languageSelect.addEventListener("change", () => { state.locale = normalizeLocale(elements.languageSelect.value); applyTranslations(); });
  elements.selectImageButton.addEventListener("click", () => chooseImage()); elements.changeImageButton.addEventListener("click", () => chooseImage());
  elements.imageInput.addEventListener("cancel", cancelSelection);
  elements.imageInput.addEventListener("change", () => {
    const continuation = state.selectionAction;
    cancelSelection();
    const file = elements.imageInput.files?.[0];
    if (!file || state.destroyed) return;
    try { setImage(adapter.imageFromFile(file), continuation); } catch { failImageLoad(); }
  });
  elements.editMarkButton.addEventListener("click", () => openMarkDialog()); elements.detailsButton.addEventListener("click", () => elements.detailsDialog.showModal());
  elements.addButton.addEventListener("click", () => startAction("add")); elements.verifyButton.addEventListener("click", () => startAction("verify"));
  root.querySelectorAll("[data-mark-mode]").forEach((tab) => tab.addEventListener("click", () => setDraftMode(tab.dataset.markMode)));
  elements.typedInput.addEventListener("input", () => { elements.typedInput.setCustomValidity(""); state.draft.typed = elements.typedInput.value; elements.typedSample.textContent = elements.typedInput.value || "　"; });
  elements.clearDrawingButton.addEventListener("click", clearDrawing);
  elements.drawingCanvas.addEventListener("pointerdown", pointerDown); elements.drawingCanvas.addEventListener("pointermove", pointerMove); elements.drawingCanvas.addEventListener("pointerup", pointerUp); elements.drawingCanvas.addEventListener("pointercancel", pointerUp);
  elements.markDialog.addEventListener("cancel", () => { state.editorContinuation = null; endDraftStroke(); });
  elements.markForm.addEventListener("submit", saveDraft); elements.markDialog.addEventListener("close", () => {
    // Native close events are queued; an older close must not erase a reopened draft.
    if (elements.markDialog.open) return;
    endDraftStroke(); state.draft = null; state.editorContinuation = null; refreshActions();
  });
  function destroy() { state.destroyed = true; mediaReduce.removeEventListener?.("change", syncEntryMotion); cancelSelection(); resetImageState(); imageGuard.invalidate(); adapter.releaseImage(); presentation?.destroy?.(); }
  windowRef.addEventListener("beforeunload", destroy);

  resetMotionVisuals(); applyTranslations(); renderMarkPreview(); setStatus(state.statusKey);
  return Object.freeze({ capabilities: adapter.capabilities, refreshPresentation: applyTranslations, destroy });
}

export function markPreviewText(mark, translate) {
  return mark ? null : translate("noMark");
}

export function localeKeyParity() {
  const expected = Object.keys(MESSAGES.ja).sort();
  return Object.fromEntries(Object.entries(MESSAGES).map(([locale, messages]) => [locale, JSON.stringify(Object.keys(messages).sort()) === JSON.stringify(expected)]));
}
