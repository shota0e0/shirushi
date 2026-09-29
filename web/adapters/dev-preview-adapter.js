import { BrowserFoundationAdapter } from "./browser-foundation-adapter.js";
import { freezeCapabilities } from "../contracts.js";
import { clone, sanitizeMark } from "../mark.js";
import { PREVIEW_PROVENANCE, previewProfileFixture, verificationFixture } from "../dev/fixtures.js";

const wait = () => new Promise((resolve) => setTimeout(resolve, 140));

export class DevPreviewAdapter extends BrowserFoundationAdapter {
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

  async addMark(request) {
    await wait();
    const mark = sanitizeMark(request?.mark);
    if (!mark) return { status: "ERROR", reason: "invalid_preview_mark" };
    return { status: "PREVIEW_ADD", source: "dev-preview", provenance: PREVIEW_PROVENANCE, mark: clone(mark) };
  }

  async verifyFileMark(request) {
    // Deliberately accepts only a target reference and preview options. A
    // current-profile mark is neither part of this request nor consulted here.
    if (!request || Object.keys(request).some((key) => !["targetReference", "options"].includes(key))) {
      return { status: "ERROR", reason: "invalid_verify_request_shape" };
    }
    await wait();
    const fixture = verificationFixture(request.options?.fixtureMode || "typed");
    return {
      status: "PREVIEW_VERIFY",
      source: "dev-preview-fixture",
      provenance: fixture.provenance,
      targetReference: request.targetReference,
      mark: sanitizeMark(fixture.mark),
    };
  }
}
