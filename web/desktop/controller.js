import { DesktopBridgeError } from "./contract.js";

/** Presentation lifetime only; transport failure is terminal until app restart. */
export function createBridgeController(adapter, {
  onChange = () => {}, schedule = setTimeout, unschedule = clearTimeout,
  timeoutMs = 7000,
} = {}) {
  let state = Object.freeze({ phase: "STARTING", capabilities: null, read: null, errorCode: null });
  let active = false;
  let disposed = false;
  let generation = 0;

  function publish(next) { state = Object.freeze(next); onChange(state); }
  async function boundedCall(call) {
    let timer;
    try {
      return await Promise.race([
        Promise.resolve().then(call),
        new Promise((_, reject) => { timer = schedule(() => reject(new DesktopBridgeError("BRIDGE_TIMEOUT")), timeoutMs); }),
      ]);
    } finally { if (timer !== undefined) unschedule(timer); }
  }

  async function refresh({ loadMark = true } = {}) {
    if (disposed || state.phase === "FAILED") return false;
    if (active) return false;
    active = true;
    const current = ++generation;
    try {
      const capabilities = await boundedCall(() => adapter.getCapabilities());
      if (disposed || current !== generation) return false;
      const read = loadMark ? await boundedCall(() => adapter.loadPersonalMark()) : state.read;
      if (disposed || current !== generation) return false;
      publish({ phase: "READY", capabilities, read, errorCode: null });
      return true;
    } catch (error) {
      if (!disposed && current === generation) {
        ++generation;
        publish({ phase: "FAILED", capabilities: null, read: null,
          errorCode: error instanceof DesktopBridgeError ? error.code : "BRIDGE_UNAVAILABLE" });
      }
      return false;
    } finally {
      active = false;
    }
  }

  return Object.freeze({
    get state() { return state; }, refresh,
    dispose() { disposed = true; ++generation; },
  });
}
