import { BrowserFoundationAdapter } from "./browser-foundation-adapter.js";
import { freezeCapabilities } from "../contracts.js";
import { clone, sanitizeMark } from "../mark.js";
import { PREVIEW_PROVENANCE, previewProfileFixture, verificationFixture } from "../dev/fixtures.js";

const wait = () => new Promise((resolve) => setTimeout(resolve, 140));

export class DevPreviewAdapter extends BrowserFoundationAdapter {
  // Preview-only target state, never evidence of embedding in the source file.
  #appliedMarks = new Map();
  capabilities = freezeCapabilities({
    coreAdd: false,
    coreVerify: false,
    coreReadback: false,
    localImagePreview: true,
    sessionMarkEdit: true,
    previewAddMotion: true,
    previewVerifyMotion: true,
  });

  constructor() {
    super(previewProfileFixture());
  }

  releaseImage() {
    this.#appliedMarks.clear();
    super.releaseImage();
  }

  async addMark(request) {
    const targetReference = request?.targetReference;
    const mark = sanitizeMark(request?.mark);
    if (typeof targetReference !== "string" || !targetReference || !mark) {
      return { status: "ERROR", reason: "invalid_preview_mark" };
    }
    await wait();
    this.#appliedMarks.set(targetReference, clone(mark));
    return { status: "PREVIEW_ADD", source: "dev-preview", provenance: PREVIEW_PROVENANCE, targetReference, mark: clone(mark) };
  }

  async verifyFileMark(request) {
    // Deliberately accepts only a target reference and preview options. A
    // current-profile mark is neither part of this request nor consulted here.
    if (!request || Object.keys(request).some((key) => !["targetReference", "options"].includes(key))) {
      return { status: "ERROR", reason: "invalid_verify_request_shape" };
    }
    await wait();
    const applied = this.#appliedMarks.get(request.targetReference);
    // Only this explicit test target is a known marked fixture. A selected
    // local image / selector value never implies a mark exists in that image.
    const fixture = request.targetReference === "dev-target-fixture"
      ? verificationFixture(request.options?.fixtureMode ?? "handwritten") : null;
    const mark = applied ?? fixture?.mark;
    if (!mark) return { status: "PREVIEW_UNMARKED", source: "dev-preview", targetReference: request.targetReference };
    return {
      status: "PREVIEW_VERIFY",
      source: applied ? "dev-preview-applied" : "dev-preview-fixture",
      provenance: PREVIEW_PROVENANCE,
      targetReference: request.targetReference,
      mark: sanitizeMark(mark),
    };
  }
}
