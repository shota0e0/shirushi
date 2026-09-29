import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

import { MESSAGES, SUPPORTED_LOCALES } from "./i18n.js";

const expectedLocales = ["ja", "en", "zh-CN", "zh-TW", "ko"];
assert.deepEqual(SUPPORTED_LOCALES, expectedLocales);
assert.deepEqual(Object.keys(MESSAGES), expectedLocales);

const [html, app, adapter, styles, editorial, capsule] = await Promise.all([
  readFile(new URL("./index.html", import.meta.url), "utf8"),
  readFile(new URL("./app.js", import.meta.url), "utf8"),
  readFile(new URL("./adapters/browser-adapter.js", import.meta.url), "utf8"),
  readFile(new URL("./styles.css", import.meta.url), "utf8"),
  readFile(new URL("./editorial.css", import.meta.url), "utf8"),
  readFile(new URL("./capsule.css", import.meta.url), "utf8"),
]);

const referencedKeys = new Set();
for (const match of html.matchAll(/data-i18n(?:-aria)?="([^"]+)"/g)) referencedKeys.add(match[1]);
for (const match of app.matchAll(/(?:translate|setStatus)\("([A-Za-z][A-Za-z0-9]+)"\)/g)) referencedKeys.add(match[1]);

for (const locale of expectedLocales) {
  for (const key of referencedKeys) {
    assert.equal(
      typeof MESSAGES[locale][key],
      "string",
      `Missing ${locale}.${key}`,
    );
    assert.ok(MESSAGES[locale][key].trim(), `Empty ${locale}.${key}`);
  }
}

assert.match(adapter, /coordinateSpace:\s*\{\s*width:/);
assert.match(adapter, /status:\s*"SUCCESS"/);
assert.match(adapter, /source:\s*"canary-file-snapshot"/);
assert.match(adapter, /storedVerificationSnapshot\(mode = "typed"\)/);
assert.match(adapter, /async inspectStoredSnapshot\(mode = "typed"\)/);
assert.match(adapter, /snapshotMode:\s*mode/);
assert.match(app, /const NORMAL_MOTION_MS = 5250/);
assert.match(app, /adapter\.inspectStoredSnapshot\(state\.verifySnapshotMode\)/);
assert.match(app, /\["diffusion", "glow", "ink"\]/);
assert.match(app, /motionPreview !== "normal" && reduceMotion\.matches/);
assert.match(styles, /--motion-draw:\s*1550ms/);
assert.match(editorial, /--motion-dissolve-at:\s*4500ms/);
assert.match(editorial, /editorial-stroke-unravel/);
assert.match(editorial, /editorial-typed-unravel/);
assert.match(capsule, /--motion-dissolve-at:\s*4300ms/);
assert.match(capsule, /capsule-typed-sublime/);
assert.match(capsule, /capsule-diffusion-absorb/);
assert.match(capsule, /\.motion-layer\.motion-verify \.motion-mark/);
assert.match(html, /href="\.\/editorial\.css"/);
assert.match(html, /href="\.\/capsule\.css"/);
assert.match(html, /name="verifySnapshotMode" value="typed"/);
assert.match(html, /name="verifySnapshotMode" value="handwritten"/);
assert.doesNotMatch(app, /innerHTML\s*=/);

console.log(`PASS: ${expectedLocales.length} locales, ${referencedKeys.size} UI keys, Canary invariants present.`);
