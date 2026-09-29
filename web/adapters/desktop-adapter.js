import { BrowserFoundationAdapter } from "./browser-foundation-adapter.js";
import { freezeCapabilities, unsupported } from "../contracts.js";
import { DesktopBridgeError, validateDesktopCapabilities, validateDesktopMarkRead } from "../desktop/contract.js";

/** Local File/blob preview is reusable presentation, NOT a native Core target. */
export class DesktopAdapter extends BrowserFoundationAdapter {
  capabilities = freezeCapabilities({ localImagePreview: true, sessionMarkEdit: false });
  #transport;
  #negotiated = false;

  constructor(transport) {
    super();
    if (typeof transport?.getCapabilities !== "function" || typeof transport?.loadPersonalMark !== "function") {
      throw new DesktopBridgeError("BRIDGE_UNAVAILABLE");
    }
    this.#transport = transport;
  }

  loadSessionMark() { return null; }
  saveSessionMark() { throw new DesktopBridgeError("PERSONAL_MARK_WRITE_UNSUPPORTED"); }

  async getCapabilities() {
    this.#negotiated = false;
    const result = validateDesktopCapabilities(await this.#transport.getCapabilities());
    this.#negotiated = true;
    return result;
  }

  async loadPersonalMark() {
    if (!this.#negotiated) throw new DesktopBridgeError("BRIDGE_NOT_READY");
    return validateDesktopMarkRead(await this.#transport.loadPersonalMark());
  }

  async addMark() { return unsupported("addMark"); }
  async verifyFileMark() { return unsupported("verifyFileMark"); }
  async readFileMark() { return unsupported("readFileMark"); }
}
