export function mountShell(root) {
  root.innerHTML = `
    <div class="dev-banner" id="devBanner" hidden role="note">
      <strong data-i18n="devBanner"></strong><span data-i18n="devProvenance"></span>
    </div>
    <div class="app-shell">
      <header class="app-header">
        <a class="brand" href="#main" aria-label="Shirushi"><span class="brand-mark" aria-hidden="true"><span></span></span><span><strong>Shirushi</strong><small>しるし</small></span></a>
        <div class="header-actions">
          <label><span class="sr-only" data-i18n="languageLabel"></span><select id="languageSelect" data-i18n-aria="languageLabel"><option value="ja">日本語</option><option value="en">English</option><option value="zh-CN">简体中文</option><option value="zh-TW">繁體中文</option><option value="ko">한국어</option></select></label>
          <button class="icon-button" id="detailsButton" type="button" data-i18n-aria="detailsLabel"><span aria-hidden="true">ⓘ</span></button>
        </div>
      </header>
      <main id="main" class="workspace">
        <div class="intro"><p class="eyebrow" data-i18n="eyebrow"></p><h1 data-i18n="headline"></h1></div>
        <section class="image-workspace">
          <div class="empty-stage" id="emptyStage">
            <div class="empty-art" aria-hidden="true"><svg viewBox="0 0 160 112"><rect x="13" y="11" width="134" height="90"/><path d="m31 79 26-25 19 17 19-22 34 30"/><circle cx="111" cy="35" r="8"/></svg></div>
            <div><h2 data-i18n="emptyTitle"></h2><p data-i18n="emptyBody"></p></div>
            <button class="button button-primary" id="selectImageButton" type="button" data-i18n="selectImage"></button>
          </div>
          <div class="selected-stage" id="selectedStage" hidden>
            <div class="image-surface" id="imageSurface"><img id="previewImage" alt=""/><div class="motion-layer" id="motionLayer" aria-hidden="true"><p class="motion-source" id="motionSource" hidden></p><div class="motion-mark" id="motionMark"></div><p class="motion-rights" id="motionRights"></p><div class="motion-complete" id="motionComplete"></div></div></div>
            <div class="image-meta"><span id="imageName"></span><button class="text-button" id="changeImageButton" type="button" data-i18n="changeImage"></button></div>
          </div>
          <input id="imageInput" type="file" accept="image/png,image/jpeg,image/webp,image/gif" hidden/>
        </section>
        <section class="mark-summary"><div><span class="section-label" data-i18n="yourMark"></span><div class="mark-preview" id="markPreview"></div></div><button class="text-button" id="editMarkButton" type="button" data-i18n="editMark"></button></section>
        <section class="primary-actions">
          <button class="button button-primary add-button" id="addButton" type="button" disabled><span class="button-spark" aria-hidden="true"></span><span id="addButtonLabel"></span></button>
          <button class="verify-button" id="verifyButton" type="button" disabled></button>
          <div id="previewToolsSlot" class="preview-tools-slot"></div>
          <p class="capability-note" id="capabilityNote"></p><p class="status-line" id="statusLine" role="status" aria-live="polite"></p>
        </section>
        <div class="rights-intent"><span aria-hidden="true">◇</span><span data-i18n="rightsIntent"></span></div>
      </main>
      <footer data-i18n="footerNormal"></footer>
    </div>
    <dialog class="dialog" id="markDialog"><form method="dialog" class="dialog-card" id="markForm">
      <div class="dialog-header"><h2 data-i18n="markDialogTitle"></h2><button class="icon-button" value="cancel" type="submit" data-i18n-aria="close">×</button></div>
      <div class="mark-tabs" role="tablist"><button type="button" id="typedTab" data-mark-mode="typed" data-i18n="typedMark"></button><button type="button" id="handwrittenTab" data-mark-mode="handwritten" data-i18n="handwrittenMark"></button></div>
      <div id="typedPanel"><label class="field-label" for="typedInput" data-i18n="displayName"></label><input class="text-input" id="typedInput" maxlength="48"/><p class="field-help" data-i18n="typedHelp"></p><div class="typed-sample" id="typedSample"></div></div>
      <div id="handwrittenPanel" hidden><p class="field-help" data-i18n="handwrittenHelp"></p><div class="drawing-wrap"><canvas id="drawingCanvas" width="480" height="220"></canvas><span class="drawing-hint" data-i18n="drawHere"></span></div><button class="text-button" id="redrawButton" type="button" data-i18n="redraw"></button><p class="identity-note" data-i18n="identityBoundary"></p></div>
      <div class="dialog-actions"><button class="button button-quiet" value="cancel" type="submit" data-i18n="cancel"></button><button class="button button-primary" id="saveMarkButton" value="default" type="submit" data-i18n="saveMark"></button></div>
    </form></dialog>
    <dialog class="dialog" id="detailsDialog"><form method="dialog" class="dialog-card"><div class="dialog-header"><h2 data-i18n="detailsTitle"></h2><button class="icon-button" value="cancel" type="submit" data-i18n-aria="close">×</button></div><p class="details-body" data-i18n="detailsBody"></p><div class="dialog-actions"><button class="button button-primary" value="default" data-i18n="done"></button></div></form></dialog>`;

  const byId = (id) => root.querySelector(`#${id}`);
  return {
    root, devBanner: byId("devBanner"), languageSelect: byId("languageSelect"), detailsButton: byId("detailsButton"), detailsDialog: byId("detailsDialog"),
    emptyStage: byId("emptyStage"), selectedStage: byId("selectedStage"), selectImageButton: byId("selectImageButton"), changeImageButton: byId("changeImageButton"), imageInput: byId("imageInput"), imageSurface: byId("imageSurface"), previewImage: byId("previewImage"), imageName: byId("imageName"),
    motionLayer: byId("motionLayer"), motionSource: byId("motionSource"), motionMark: byId("motionMark"), motionRights: byId("motionRights"), motionComplete: byId("motionComplete"),
    markPreview: byId("markPreview"), editMarkButton: byId("editMarkButton"), markDialog: byId("markDialog"), markForm: byId("markForm"), typedTab: byId("typedTab"), handwrittenTab: byId("handwrittenTab"), typedPanel: byId("typedPanel"), handwrittenPanel: byId("handwrittenPanel"), typedInput: byId("typedInput"), typedSample: byId("typedSample"), drawingCanvas: byId("drawingCanvas"), redrawButton: byId("redrawButton"),
    addButton: byId("addButton"), addButtonLabel: byId("addButtonLabel"), verifyButton: byId("verifyButton"), previewToolsSlot: byId("previewToolsSlot"), capabilityNote: byId("capabilityNote"), statusLine: byId("statusLine"),
  };
}
