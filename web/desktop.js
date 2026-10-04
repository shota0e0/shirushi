import { bootstrapShirushi } from "./bootstrap.js";
import { DesktopAdapter } from "./adapters/desktop-adapter.js";
import { createDesktopTransport } from "./desktop/transport.js";
import { createBridgeController } from "./desktop/controller.js";
import { createDesktopPresentation } from "./desktop/presentation.js";
import { createLimitedController, createLimitedPresentation } from "./desktop/limited-inspection.js";

const transport = createDesktopTransport(window);
const adapter = new DesktopAdapter(transport);
let app;
const controller = createBridgeController(adapter, { onChange: () => app?.refreshPresentation() });
const markPresentation = createDesktopPresentation(() => controller.state, () => controller.refresh());
const limited = createLimitedController(transport, { onChange: () => app?.refreshPresentation() });
const limitedPresentation = createLimitedPresentation(() => limited.state, (inputPath) => limited.inspect(inputPath));
const presentation = {
  render(context) { markPresentation.render(context); limitedPresentation.render(context); },
  destroy() { markPresentation.destroy(); limitedPresentation.destroy(); },
};
app = bootstrapShirushi({ root: document.querySelector("#app"), adapter, presentation });
// No profile is put through session sanitization or rendered with fallback fonts.
void controller.refresh();
window.addEventListener("beforeunload", () => { controller.dispose(); limited.dispose(); app.destroy(); }, { once: true });
