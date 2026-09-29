import { desktopMessages } from "./i18n.js";

/** No stored text or geometry is projected into the session renderer. */
export function bridgePresentationModel(state, locale) {
  const copy = desktopMessages(locale);
  let summary = copy.notLoaded;
  let detail = copy.readOnly;
  const read = state.read;
  if (state.phase === "READY" && read) {
    if (read.state === "V2") {
      summary = read.mark.type === "typed" ? copy.typed : copy.handwritten;
      detail = read.mark.type === "typed"
        ? `${read.mark.renderProfile.id}@${read.mark.renderProfile.version} · ${read.renderProfileSupport.state === "UNSUPPORTED" ? copy.profile : copy.assets}`
        : `${read.mark.strokes.length} ${copy.strokes} · ${read.mark.coordinateSpace.width} × ${read.mark.coordinateSpace.height} · ${copy.readOnly}`;
    } else {
      summary = ({ ABSENT: copy.absent, LEGACY_V1: copy.legacy, MALFORMED: copy.malformed, UNSUPPORTED: copy.unsupported, IO_ERROR: copy.io_error })[read.state] || copy.notLoaded;
    }
  }
  return Object.freeze({ status: copy[state.phase === "READY" ? "ready" : state.phase === "FAILED" ? "failed" : "starting"], summary, detail, copy });
}

export function createDesktopPresentation(getState, refresh) {
  let bridgeRow;
  let status;
  let reload;
  let summary;
  let detail;
  return Object.freeze({
    render({ elements, locale, documentRef }) {
      const state = getState();
      const model = bridgePresentationModel(state, locale);
      if (!bridgeRow) {
        bridgeRow = documentRef.createElement("section"); bridgeRow.className = "bridge-status"; bridgeRow.id = "bridgeStatus";
        status = documentRef.createElement("p"); status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
        reload = documentRef.createElement("button"); reload.type = "button"; reload.className = "text-button"; reload.id = "reloadStoredMark";
        reload.addEventListener("click", () => { void refresh(); });
        bridgeRow.append(status, reload); elements.root.querySelector(".intro").after(bridgeRow);
        summary = documentRef.createElement("span"); detail = documentRef.createElement("small");
      }
      bridgeRow.dataset.phase = state.phase;
      status.textContent = model.status;
      reload.textContent = model.copy.reload;
      reload.disabled = state.phase !== "READY";
      elements.markPreview.replaceChildren(summary, detail);
      elements.markPreview.classList.add("stored-mark-read");
      summary.textContent = model.summary; detail.textContent = model.detail;
      elements.editMarkButton.hidden = true;
      elements.capabilityNote.textContent = model.copy.boundary;
      elements.root.querySelector("footer").textContent = `${model.copy.boundary} ${model.copy.preview}`;
      elements.detailsDialog.querySelector(".details-body").textContent = model.copy.details;
    },
    destroy() { bridgeRow?.remove(); },
  });
}
