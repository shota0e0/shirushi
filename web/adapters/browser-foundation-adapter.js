import { freezeCapabilities, unsupported } from "../contracts.js";
import { clone, sanitizeMark } from "../mark.js";

export class BrowserFoundationAdapter {
  capabilities = freezeCapabilities({
    coreAdd: false,
    coreVerify: false,
    coreReadback: false,
    localImagePreview: true,
    sessionMarkEdit: true,
    previewAddMotion: false,
    previewVerifyMotion: false,
  });

  #mark = null;
  #objectUrl = null;

  constructor(initialMark = null) {
    if (initialMark !== null) {
      const safe = sanitizeMark(initialMark);
      if (!safe) throw new Error("INVALID_INITIAL_PERSONAL_MARK");
      this.#mark = safe;
    }
  }

  loadSessionMark() {
    return this.#mark ? clone(this.#mark) : null;
  }

  saveSessionMark(mark) {
    const safe = sanitizeMark(mark);
    if (!safe) throw new Error("INVALID_PERSONAL_MARK");
    this.#mark = safe;
    return clone(safe);
  }

  imageFromFile(file) {
    if (!file || typeof file.type !== "string" || !file.type.startsWith("image/")) {
      throw new Error("INVALID_IMAGE");
    }
    this.releaseImage();
    this.#objectUrl = URL.createObjectURL(file);
    return { url: this.#objectUrl, name: file.name || "image", local: true };
  }

  releaseImage() {
    if (!this.#objectUrl) return;
    URL.revokeObjectURL(this.#objectUrl);
    this.#objectUrl = null;
  }

  async addMark() {
    return unsupported("addMark");
  }

  async getCapabilities() {
    return this.capabilities;
  }

  async loadPersonalMark() {
    return unsupported("loadPersonalMark", "native_unavailable");
  }

  async readFileMark() {
    return unsupported("readFileMark");
  }

  async verifyFileMark() {
    return unsupported("verifyFileMark");
  }
}
