import { DesktopBridgeError } from "./contract.js";

/** Two fixed no-argument calls only. Rust owns IDs, child paths and arguments. */
export function createDesktopTransport(windowRef) {
  const invoke = windowRef?.__TAURI__?.core?.invoke;
  async function call(command) {
    if (typeof invoke !== "function") throw new DesktopBridgeError("BRIDGE_UNAVAILABLE");
    try { return await invoke(command); }
    catch (error) { throw new DesktopBridgeError(typeof error?.code === "string" ? error.code : "BRIDGE_UNAVAILABLE"); }
  }
  return Object.freeze({
    getCapabilities: () => call("bridge_get_capabilities"),
    loadPersonalMark: () => call("bridge_load_personal_mark"),
  });
}
