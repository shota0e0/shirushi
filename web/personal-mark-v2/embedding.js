import { deepFreeze, fail } from "./errors.js";
import { validateMarkV2 } from "./contract.js";

export function assessEmbeddingReadiness(mark) {
  const validation = validateMarkV2(mark);
  if (validation.state !== "VALID") fail("INVALID_FIELD_TYPE", { field: "mark", reason: validation.state });
  return deepFreeze({ state: "POLICY_UNKNOWN", policyVersion: null });
}

