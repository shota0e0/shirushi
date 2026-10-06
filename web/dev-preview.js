import { DevPreviewAdapter } from "./adapters/dev-preview-adapter.js";
import { bootstrapShirushi } from "./bootstrap.js";

bootstrapShirushi({ root: document.querySelector("#app"), adapter: new DevPreviewAdapter() });
