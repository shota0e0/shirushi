// Read-only Node fixture driver. It generates no security-authoritative digest.
import fs from "node:fs";
import {parseMarkV2} from "./index.js";
const fixture = JSON.parse(fs.readFileSync(new URL("../../tests/fixtures/personal_mark_v2_shared.json", import.meta.url), "utf8"));
const outcomes = fixture.cases.map(test => {
  try {
    const result = parseMarkV2(test.raw);
    if (result.state === "UNSUPPORTED_VERSION") return {id:test.id,code:"UNKNOWN_SCHEMA_VERSION"};
    if (result.state === "UNSUPPORTED_TYPE") return {id:test.id,code:"UNKNOWN_TYPE"};
    return {id:test.id,code:"VALID",mark:result.mark,profile:result.renderProfileSupport?.state ?? null};
  } catch (error) {
    return {id:test.id,code:error.code ?? String(error)};
  }
});
console.log(JSON.stringify(outcomes));
