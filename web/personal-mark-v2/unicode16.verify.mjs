import assert from "node:assert/strict";
import fs from "node:fs";
import {normalizeNfc, hasProperty, UNICODE_VERSION} from "./unicode16.js";

const fixture = JSON.parse(fs.readFileSync(new URL("../../tests/fixtures/personal_mark_unicode16_normalization.json", import.meta.url), "utf8"));
assert.equal(fixture.unicodeVersion, UNICODE_VERSION);
let cases = 0;
for (const [source,nfc,nfd,nfkc,nfkd] of fixture.vectors) {
  for (const [value,expected] of [[source,nfc],[nfc,nfc],[nfd,nfc],[nfkc,nfkc],[nfkd,nfkc]]) {
    assert.equal(normalizeNfc(value), expected, `Official normalization check ${cases}`);
    cases++;
  }
}
assert.equal(hasProperty(0x1e5d0,"visible_base"), true);
assert.equal(hasProperty(0xe0100,"variation_selector"), true);
assert.equal(hasProperty(0x115f,"default_ignorable"), true);
assert.equal(normalizeNfc("Ａ　a\u200d森\ufe00"), "Ａ　a\u200d森\ufe00");
assert.throws(() => normalizeNfc("\ud800"));
console.log(`Unicode ${UNICODE_VERSION}: ${fixture.vectors.length} official rows / ${cases} NFC conformance checks PASS`);
