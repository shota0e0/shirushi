import { bootstrapShirushi } from "./bootstrap.js";
import { DesktopAdapter } from "./adapters/desktop-adapter.js";
import { createDesktopTransport } from "./desktop/transport.js";

const transport = createDesktopTransport(window);
const adapter = new DesktopAdapter(transport, { productFlow:true });
const presentation = {
  render({ elements, locale }) {
    const note = locale === "ja"
      ? "Development · Rust Add / Limited Inspection · INCOMPLETE · TrustMark NOT_CHECKED · 元画像は変更しません"
      : "Development · Rust Add / Limited Inspection · INCOMPLETE · TrustMark NOT_CHECKED · source preserved";
    elements.devBanner.hidden = false;
    elements.devBanner.textContent = "Shirushi DEVELOPMENT";
    elements.capabilityNote.textContent = note;
    elements.root.querySelector("footer").textContent = note;
    elements.detailsDialog.querySelector(".details-body").textContent = note;
  },
};
const app = bootstrapShirushi({ root: document.querySelector("#app"), adapter, presentation });
window.addEventListener("beforeunload", () => app.destroy(), { once: true });
