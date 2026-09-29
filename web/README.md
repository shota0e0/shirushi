# Shirushi shared Web foundation — F2A.1

This directory is the reversible shared Web UI foundation. It formalizes the
approved F1 Capsule-derived Compact Utility structure, five-locale layout,
responsive behavior, accessibility baseline, and reduced-motion behavior. It
does not finalize the visual design and does not connect to the Python Core.

## Entries

- `index.html` is the truthful foundation entry. It can preview a local image
  and edit a Personal Mark for the current page session only. Core Add,
  Verify, and file readback capabilities are false, so their controls remain
  disabled and explain why. Unsupported adapter methods return `UNSUPPORTED`
  even when called directly.
- `dev-preview.html` is the explicit non-production motion review entry. Its
  persistent banner and fixture provenance identify every outcome as a
  preview. Only this entry imports `DevPreviewAdapter`, fixed fixtures, and the
  fixture selector. Outcomes are `PREVIEW_ADD` / `PREVIEW_VERIFY`, never
  `SUCCESS` / `VERIFIED`.

Serve the directory with any static HTTP server. For example:

```powershell
python -m http.server 4174 --bind 127.0.0.1 --directory web
```

- Normal foundation: <http://127.0.0.1:4174/>
- Development motion preview: <http://127.0.0.1:4174/dev-preview.html>
- Force approved full motion: add `?motion=normal`
- Force the static reduced-motion review path: add `?motion=reduce`

Local file selection uses an object URL. It does not upload an image. The
adapter revokes the previous object URL on replacement and unload. Image and
operation generations reject stale load/async completions.

## Contract boundary

`bootstrapShirushi` requires an injected adapter and never creates a concrete
adapter. `contracts.js` documents the adapter, capability, result, Personal
Mark, and coordinate-space view-model shapes. The `coordinateSpace` field is
visual session data needed to preserve drawing geometry. It is not adoption of
a production Personal Mark v2 schema, validator, migration, or persistence
format.

Verify requests contain only a target reference and options. The current
profile mark is never passed. In the development entry, the returned fixture is
file-target stand-in data and remains distinct after profile edits. Fixed Niki /
Mori names and synthetic strokes are demonstration/test data, not real-user
profiles or marks read from a selected image. No browser-local profile is part
of this source export.

## Mandatory next visual slice (F2A.2)

F2A.1 deliberately carries the approved F1 treatment without claiming visual
finalization. The next separately authorized visual slice must address all
three Owner-required items:

1. **P1 neutral primary:** reduce black-fill dependence while retaining action
   discovery through white/neutral structure and a small orange accent.
2. **P2 distinctive one-shot icon family:** replace generic line icons and add
   an action-ready, non-looping mark trace shared across Add, Watch Again, and
   Verify actions.
3. **P3 typed ink-bloom:** prototype the approved luminous, organic ink-edge
   bloom/diffusion/dissolve with a static reduced-motion fallback.

No P1–P3 claim is made in this slice.

## Isolated Personal Mark v2 contract (F2B)

`personal-mark-v2/` is the additive formal parser, validator, Typed-save
confirmation, profile-support, and coordinate-transform contract for the
Owner-approved v2 schema. It is intentionally absent from the normal Browser
entry/import graph: the current Browser adapter remains session-only and its
Core operations remain `UNSUPPORTED`. The Web validator is advisory; Python
remains authoritative for persistence, dual-read decisions, and any future
application-service snapshot/digest.

The Web contract performs no Web Storage save, upload, IPC, C2PA embedding, or
security-authoritative digest. Its legacy API accepts only the narrow result
envelope emitted after Python's existing v1 validator; it does not implement a
second v1 validator, infer capture geometry, or convert v1 to v2. The known
`shirushi-typed@1` registry entry is recognized but explicitly reports missing
licensed render assets, so there is no platform-font fallback.

## Read-only Desktop bridge (F2C.1)

`desktop.html` / `desktop.js` reuse this same UI with a separated Desktop
adapter. The local Tauri project under `../desktop/` uses an explicit asset
allowlist excluding development preview and fixtures. Two native commands read
capabilities and the saved Personal Mark only; Add/Verify/save remain unavailable.
An ordinary browser opening this entry reports bridge unavailable.

The status describes a last confirmed connection check; Reload checks again.
There is no periodic request/restart loop. Loaded Typed and legacy values are
not projected into session rendering or fallback system fonts. Normal
`index.html` remains independent and native mark loading is unsupported.

Current native verification is blocked by Windows Application Control. See
the separate [F2C.1 CI-only setup and evidence ledger](../docs/development/f2c-verification.md):
F2C.1 **ENVIRONMENT BLOCKED**, code **PARTIAL PASS**, native manual **PENDING**, G2 **NOT READY**.
The workflow has not been run; hosted CI never substitutes for local native
canary evidence. The linked ledger separates source-only checks, excluded
legacy coverage, and the unverified native/manual gate.
P1–P3 remain carry-over, not claimed complete by the bridge slice.

## Verification

```powershell
node web/verify.mjs
node web/desktop/verify.mjs
node web/personal-mark-v2/verify.mjs
node web/personal-mark-v2/unicode16.verify.mjs
node web-canary/verify.mjs
Get-ChildItem web -Recurse -Filter *.js | ForEach-Object { node --check $_.FullName; if ($LASTEXITCODE -ne 0) { throw 'JS syntax failure' } }
git diff --check
```
