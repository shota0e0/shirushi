import { BrowserFoundationAdapter } from "./browser-foundation-adapter.js";
import { freezeCapabilities, unsupported } from "../contracts.js";
import { DesktopBridgeError, PREVIEW_AUTHORITY, validateDesktopCapabilities, validateDesktopMarkRead } from "../desktop/contract.js";
import { inspectionUiOutcome, localPath, strictUiMark, validateAddResult, validateImageRecord } from "../desktop/product-flow.js";

/** Local File/blob preview is reusable presentation, NOT a native Core target. */
export class DesktopAdapter extends BrowserFoundationAdapter {
  capabilities = freezeCapabilities({ localImagePreview: true, sessionMarkEdit: false });
  #transport;
  #negotiated = false;
  #authority = null;
  #productFlow = false;
  #selected = null;
  #active = false;
  #explorerConsumed = false;

  async takeExplorerRequest() {
    if (this.#explorerConsumed) return null;
    this.#explorerConsumed = true; // errors/cancellation cannot replay a launch intent
    if (!this.#productFlow || this.#active || typeof this.#transport.takeExplorerRequest !== "function") throw new DesktopBridgeError("PRODUCT_UNAVAILABLE");
    const value = await this.#transport.takeExplorerRequest();
    if (value === null) return null;
    if (!value || Object.keys(value).sort().join(",") !== "image,operation"
      || !["add", "limited_inspect"].includes(value.operation)) throw new DesktopBridgeError("INVALID_EXPLORER_REQUEST");
    this.#selected = validateImageRecord(value.image, { maximum:(value.operation === "limited_inspect" ? 64 : 32)*1024*1024 });
    return { operation: value.operation, image: this.#selected };
  }

  constructor(transport, { productFlow = false } = {}) {
    super();
    if (typeof transport?.getCapabilities !== "function" || typeof transport?.loadPersonalMark !== "function") {
      throw new DesktopBridgeError("BRIDGE_UNAVAILABLE");
    }
    this.#transport = transport;
    this.#productFlow = productFlow === true;
    if (this.#productFlow) {
      if (["selectImage", "readImage", "productOperation"].some((k) => typeof transport[k] !== "function")) throw new DesktopBridgeError("BRIDGE_UNAVAILABLE");
      this.capabilities = freezeCapabilities({ localImagePreview:true, sessionMarkEdit:true, coreAdd:true, coreVerify:true });
    }
  }

  loadSessionMark() { return this.#productFlow ? super.loadSessionMark() : null; }
  saveSessionMark(mark) {
    if (!this.#productFlow) throw new DesktopBridgeError("PERSONAL_MARK_WRITE_UNSUPPORTED");
    return super.saveSessionMark(strictUiMark(mark)); // session only, not Python profile write
  }
  async selectImageFromNative() {
    if (!this.#productFlow || this.#active) throw new DesktopBridgeError("PRODUCT_UNAVAILABLE");
    const value = await this.#transport.selectImage();
    if (value === null) return null; // cancelled replacement keeps the selected target
    this.#selected = validateImageRecord(value);
    return this.#selected;
  }

  async getCapabilities() {
    this.#negotiated = false;
    this.#authority = null;
    const result = validateDesktopCapabilities(await this.#transport.getCapabilities());
    if (result.authority === PREVIEW_AUTHORITY && !this.#productFlow) throw new DesktopBridgeError("INCOMPATIBLE_BRIDGE");
    this.#authority = result.authority ?? null;
    this.#negotiated = true;
    return result;
  }

  async loadPersonalMark() {
    if (!this.#negotiated) throw new DesktopBridgeError("BRIDGE_NOT_READY");
    return validateDesktopMarkRead(await this.#transport.loadPersonalMark(), this.#authority);
  }

  async #operation(operation, targetReference, mark = null) {
    if (this.#active || !this.#selected || targetReference !== this.#selected.reference) throw new DesktopBridgeError("INVALID_PRODUCT_TARGET");
    this.#active = true;
    try {
      const request = { operation, inputPath:localPath(targetReference),
        expectedSource:{sha256:this.#selected.sha256,size:this.#selected.size} };
      if (mark) request.personalMark = strictUiMark(mark);
      const raw = await this.#transport.productOperation(request);
      if (raw?.result === "ADD_FAILED") {
        if (Object.keys(raw).length !== 3 || raw.operation !== "add"
          || !/^[A-Z][A-Z0-9_]{0,63}$/.test(raw.errorCode)) throw new DesktopBridgeError("INVALID_PRODUCT_RESULT");
        throw new DesktopBridgeError(raw.errorCode);
      }
      if (operation === "limited_inspect") {
        const result = inspectionUiOutcome(raw);
        if (result.result.inspection.source.sha256 !== this.#selected.sha256
          || result.result.inspection.source.size !== this.#selected.size) throw new DesktopBridgeError("SOURCE_CHANGED");
        return result;
      }
      const result = validateAddResult(raw, mark);
      if (result.source.sha256 !== this.#selected.sha256 || result.source.size !== this.#selected.size) throw new DesktopBridgeError("SOURCE_CHANGED");
      const image = validateImageRecord(await this.#transport.readImage(result.output.path), { maximum:64*1024*1024 });
      if (image.sha256 !== result.output.sha256 || image.size !== result.output.size) throw new DesktopBridgeError("OUTPUT_CHANGED");
      this.#selected = image;
      return { status:"SUCCESS", mark:result.personalMark, output:result.output, outputImage:image, developmentSigning:true };
    } finally { this.#active = false; }
  }
  async addMark({ targetReference, mark } = {}) {
    return this.#productFlow ? this.#operation("add", targetReference, mark) : unsupported("addMark");
  }
  async verifyFileMark({ targetReference } = {}) {
    return this.#productFlow ? this.#operation("limited_inspect", targetReference) : unsupported("verifyFileMark");
  }
  async readFileMark() { return unsupported("readFileMark"); }
}
