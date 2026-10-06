import { DesktopBridgeError } from "./contract.js";

/** Fixed commands only. Rust owns helper paths, identity and child arguments. */
export function createDesktopTransport(windowRef) {
  const invoke = windowRef?.__TAURI__?.core?.invoke;
  async function call(command, args) {
    if (typeof invoke !== "function") throw new DesktopBridgeError("BRIDGE_UNAVAILABLE");
    try { return args === undefined ? await invoke(command) : await invoke(command, args); }
    catch (error) { throw new DesktopBridgeError(typeof error?.code === "string" ? error.code : "BRIDGE_UNAVAILABLE"); }
  }
  return Object.freeze({
    getCapabilities: () => call("bridge_get_capabilities"),
    loadPersonalMark: () => call("bridge_load_personal_mark"),
    inspectLimited: (request) => call("bridge_inspect_limited", { request }),
    selectImage: () => call("bridge_select_image"),
    takeExplorerRequest: () => call("bridge_take_explorer_request"),
    readImage: (inputPath) => call("bridge_read_image", { inputPath }),
    productOperation: (request) => call("bridge_product_operation", { request }),
  });
}
