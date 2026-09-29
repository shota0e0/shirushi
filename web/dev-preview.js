import { DevPreviewAdapter } from "./adapters/dev-preview-adapter.js";
import { bootstrapShirushi } from "./bootstrap.js";
import { mountPreviewControls } from "./dev/preview-controls.js";

bootstrapShirushi({ root: document.querySelector("#app"), adapter: new DevPreviewAdapter(), previewTools: mountPreviewControls });
