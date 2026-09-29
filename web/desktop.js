import { bootstrapShirushi } from "./bootstrap.js";
import { DesktopAdapter } from "./adapters/desktop-adapter.js";
import { createDesktopTransport } from "./desktop/transport.js";
import { createBridgeController } from "./desktop/controller.js";
import { createDesktopPresentation } from "./desktop/presentation.js";

const adapter = new DesktopAdapter(createDesktopTransport(window));
let app;
const controller = createBridgeController(adapter, { onChange: () => app?.refreshPresentation() });
const presentation = createDesktopPresentation(() => controller.state, () => controller.refresh());
app = bootstrapShirushi({ root: document.querySelector("#app"), adapter, presentation });
// No profile is put through session sanitization or rendered with fallback fonts.
void controller.refresh();
window.addEventListener("beforeunload", () => { controller.dispose(); app.destroy(); }, { once: true });
