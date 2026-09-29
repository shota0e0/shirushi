import { BrowserFoundationAdapter } from "./adapters/browser-foundation-adapter.js";
import { bootstrapShirushi } from "./bootstrap.js";

bootstrapShirushi({ root: document.querySelector("#app"), adapter: new BrowserFoundationAdapter() });
