# Personal Mark v2 Web contract

This directory contains the isolated, dependency-free Shared Web contract for
the Owner-approved Personal Mark v2 schema. It is not imported by the normal
Browser foundation. The Browser adapter remains session-only and cannot load,
save, add, or verify a formal mark.

Public entry points are exported by `index.js`:

- `parseMarkV2(raw)` accepts a string or `Uint8Array`, performs strict bounded
  JSON parsing (including decoded duplicate-name detection), then validation.
- `validateMarkV2(value)` returns a frozen discriminated result. Unknown schema
  versions and types are explicit states. Unknown Typed profiles remain
  structurally valid but have an explicit unsupported render state.
- `prepareTypedSave` and `confirmTypedSave` expose NFC candidates, code-point
  differences, and ZWJ/ZWNJ/variation-selector confirmations. Unicode 16 data
  in this directory is normative; host `String.normalize` and host Unicode
  properties are not.
- `createCapturePlane`, `centerFitTransform`, `mapNormalizedPoint`, and
  `inverseMapPoint` implement a frozen, explicit plane with uniform center-fit.
- `validatePythonReadEnvelope` accepts only the narrow envelope returned by the
  Python-authoritative dual reader. Its v1 payload is opaque here: there is no
  second v1 validator, inferred geometry, or automatic conversion.
- `assessEmbeddingReadiness` returns `POLICY_UNKNOWN`; F2B defines no embedding
  size policy.

`encodeMarkV2` is deterministic compact JSON for local contract roundtrip. It
is not JCS, a security-authoritative canonicalization, a digest format, an IPC
wire contract, or a C2PA assertion encoding. Python owns any future
authoritative persistence/snapshot encoding.

Run `node web/personal-mark-v2/verify.mjs` from the repository root.

