import { bootstrapShirushi } from "./bootstrap.js";
import { DesktopAdapter } from "./adapters/desktop-adapter.js";
import { createDesktopTransport } from "./desktop/transport.js";

const transport = createDesktopTransport(window);
const adapter = new DesktopAdapter(transport, { productFlow:true });
const presentation = {
  render({ elements, locale }) {
    const note = locale === "ja"
      ? "Preview · 公開テスト資格情報（作者・著作権の証明ではありません）· Limited Inspection / INCOMPLETE · 元画像は変更しません"
      : "Preview · public test credentials (not author/copyright proof) · Limited Inspection / INCOMPLETE · source preserved";
    elements.devBanner.hidden = false;
    elements.devBanner.textContent = "Shirushi v0.2 Preview · TEST CREDENTIALS · NO PRODUCTION TRUST";
    elements.capabilityNote.textContent = note;
    elements.root.querySelector("footer").textContent = note;
    elements.detailsDialog.querySelector(".details-body").textContent = note;
  },
};
const app = bootstrapShirushi({ root: document.querySelector("#app"), adapter, presentation });
void app.consumeExplorerEntry();
window.addEventListener("beforeunload", () => app.destroy(), { once: true });
