export function mountShell(root) {
  root.innerHTML = `
    <div class="dev-banner" id="devBanner" hidden role="note">
      <strong data-i18n="devBanner"></strong><span data-i18n="devProvenance"></span>
    </div>
    <div class="app-shell">
      <header class="app-header">
        <a class="brand" href="#main" aria-label="Shirushi"><span class="brand-mark" aria-hidden="true"><span></span></span><span><strong>Shirushi</strong><small>しるし</small></span></a>
        <div class="header-actions">
          <label class="language-control"><span class="sr-only" data-i18n="languageLabel"></span><svg class="language-globe" aria-hidden="true" viewBox="0 0 20 20"><circle cx="10" cy="10" r="7.5"/><ellipse cx="10" cy="10" rx="3.3" ry="7.5"/><path d="M2.5 10h15M4 5.5h12M4 14.5h12"/></svg><select id="languageSelect" data-i18n-aria="languageLabel"><option value="ja">日本語</option><option value="en">English</option><option value="zh-CN">简体中文</option><option value="zh-TW">繁體中文</option><option value="ko">한국어</option></select><span class="language-chevron" aria-hidden="true"></span></label>
          <button class="icon-button" id="detailsButton" type="button" data-i18n-aria="detailsLabel"><span aria-hidden="true">ⓘ</span></button>
        </div>
      </header>
      <main id="main" class="workspace">
        <div class="intro"><p class="eyebrow" data-i18n="eyebrow"></p><h1 data-i18n="headline"></h1></div>
        <section class="image-workspace">
          <div class="image-stage">
          <div class="empty-stage" id="emptyStage">
            <button class="image-entry" id="selectImageButton" type="button">
              <svg class="image-entry-motion" viewBox="0 0 128 112" aria-hidden="true" focusable="false">
                <path class="entry-frame" d="M36 52v-8h8M84 44h8v8M36 84v8h8M84 92h8v-8"/>
                <g class="entry-card">
                  <rect x="43" y="4" width="42" height="36" rx="3"/>
                  <circle cx="73" cy="14" r="3"/>
                  <path d="M48 32l10-11 7 8 5-5 10 8"/>
                </g>
                <path class="entry-accent" d="M55 98h18"/>
                <circle class="entry-pulse" cx="64" cy="98" r="2"/>
              </svg>
              <span class="image-entry-label" data-i18n="selectImage"></span>
            </button>
            <p class="image-entry-hint" data-i18n="emptyHint"></p>
          </div>
          <div class="selected-stage" id="selectedStage" hidden>
            <div class="image-surface" id="imageSurface"><img id="previewImage" alt=""/><div class="motion-layer" id="motionLayer" aria-hidden="true"><p class="motion-source" id="motionSource" hidden></p><div class="motion-mark" id="motionMark"></div><p class="motion-rights" id="motionRights"></p></div></div>
          </div>
          </div>
          <div class="image-meta"><span id="imageName"></span><button class="text-button" id="changeImageButton" type="button" hidden data-i18n="changeImage"></button></div>
          <input id="imageInput" type="file" accept="image/png,image/jpeg,image/webp,image/gif" hidden/>
        </section>
        <section class="mark-summary" id="markSummary"><div><span class="section-label" data-i18n="yourMark"></span><div class="mark-preview" id="markPreview"></div></div><button class="text-button" id="editMarkButton" type="button" disabled data-i18n="editMark"></button></section>
        <section class="primary-actions" id="primaryActions">
          <button class="button button-primary add-button" id="addButton" type="button" disabled><svg class="cta-icon cta-pen" id="addButtonIcon" viewBox="0 0 20 20" aria-hidden="true" focusable="false"><path d="m4 13 9-9 3 3-9 9-4 1zM11 6l3 3M4 13l3 3"/><path class="cta-icon-accent" d="M11 17h5"/></svg><span id="addButtonLabel"></span></button>
          <button class="button button-quiet verify-button" id="verifyButton" type="button" disabled><svg class="cta-icon cta-inspect" id="verifyButtonIcon" viewBox="0 0 20 20" aria-hidden="true" focusable="false"><circle cx="8.5" cy="8.5" r="4.8"/><path d="m12 12 4.5 4.5"/><path class="cta-icon-accent" d="m6.4 8.6 1.4 1.4 2.7-2.8"/></svg><span id="verifyButtonLabel"></span></button>
        </section>
        <div class="action-feedback" id="actionFeedback">
          <p class="capability-note" id="capabilityNote"></p>
          <p class="status-line" id="statusLine" role="status" aria-live="polite" hidden></p>
        </div>
        <div class="rights-intent" id="rightsIntent" hidden><span aria-hidden="true">◇</span><span data-i18n="rightsIntent"></span></div>
      </main>
      <footer data-i18n="footerNormal"></footer>
    </div>
    <dialog class="dialog" id="markDialog"><form method="dialog" class="dialog-card" id="markForm">
      <div class="dialog-header"><h2 data-i18n="markDialogTitle"></h2><button class="icon-button" value="cancel" type="submit" data-i18n-aria="close">×</button></div>
      <div class="mark-tabs" role="tablist"><button type="button" id="handwrittenTab" data-mark-mode="handwritten" data-i18n="handwrittenMark"></button><button type="button" id="typedTab" data-mark-mode="typed" data-i18n="typedMark"></button></div>
      <div id="typedPanel"><label class="field-label" for="typedInput" data-i18n="displayName"></label><input class="text-input" id="typedInput" maxlength="48"/><p class="field-help" data-i18n="typedHelp"></p><div class="typed-sample" id="typedSample"></div></div>
      <div id="handwrittenPanel" hidden><p class="field-help" data-i18n="handwrittenHelp"></p><div class="drawing-wrap"><canvas id="drawingCanvas" width="480" height="220"></canvas><span class="drawing-hint" data-i18n="drawHere"></span></div><button class="text-button" id="clearDrawingButton" type="button" data-i18n="clearDrawing"></button><p class="identity-note" data-i18n="identityBoundary"></p></div>
      <div class="dialog-actions"><button class="button button-quiet" value="cancel" type="submit" data-i18n="cancel"></button><button class="button button-primary" id="saveMarkButton" value="default" type="submit" data-i18n="saveMark"></button></div>
    </form></dialog>
    <dialog class="dialog" id="detailsDialog"><form method="dialog" class="dialog-card"><div class="dialog-header"><h2 data-i18n="detailsTitle"></h2><button class="icon-button" value="cancel" type="submit" data-i18n-aria="close">×</button></div><p class="details-body" data-i18n="detailsBody"></p><p class="details-body" data-i18n="emptyBody"></p><div class="dialog-actions"><button class="button button-primary" value="default" data-i18n="done"></button></div></form></dialog>`;

  const byId = (id) => root.querySelector(`#${id}`);
  return {
    root, devBanner: byId("devBanner"), languageSelect: byId("languageSelect"), detailsButton: byId("detailsButton"), detailsDialog: byId("detailsDialog"),
    emptyStage: byId("emptyStage"), selectedStage: byId("selectedStage"), selectImageButton: byId("selectImageButton"), changeImageButton: byId("changeImageButton"), imageInput: byId("imageInput"), imageSurface: byId("imageSurface"), previewImage: byId("previewImage"), imageName: byId("imageName"),
    motionLayer: byId("motionLayer"), motionSource: byId("motionSource"), motionMark: byId("motionMark"), motionRights: byId("motionRights"),
    markSummary: byId("markSummary"), primaryActions: byId("primaryActions"), rightsIntent: byId("rightsIntent"),
    markPreview: byId("markPreview"), editMarkButton: byId("editMarkButton"), markDialog: byId("markDialog"), markForm: byId("markForm"), typedTab: byId("typedTab"), handwrittenTab: byId("handwrittenTab"), typedPanel: byId("typedPanel"), handwrittenPanel: byId("handwrittenPanel"), typedInput: byId("typedInput"), typedSample: byId("typedSample"), drawingCanvas: byId("drawingCanvas"), clearDrawingButton: byId("clearDrawingButton"),
    addButton: byId("addButton"), addButtonLabel: byId("addButtonLabel"), verifyButton: byId("verifyButton"), verifyButtonLabel: byId("verifyButtonLabel"), capabilityNote: byId("capabilityNote"), statusLine: byId("statusLine"),
  };
}
