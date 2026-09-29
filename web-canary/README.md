# Shirushi v0.2 F1 Visual Direction Canary

This is an independent, static Web UI Canary for Owner Review. It does not call
the Python Core, rewrite the selected image, embed a Personal Mark, or produce a
downloadable output file.

The fixed Niki / Mori names, synthetic strokes, and code-native
`assets/demo-art.svg` are demonstration/test fixtures, not real-user profiles.
Verify uses a stored-snapshot simulation, not data read from the selected file.
User-created browser-local records are not included in this source export.

## Run locally

From the repository root:

```powershell
.\.venv-py312\Scripts\python.exe -m http.server 4173 --bind 127.0.0.1 --directory web-canary
```

Open <http://127.0.0.1:4173/>.

The page respects the operating system's reduced-motion preference. For visual
QA of the full 5.25-second sequence on a machine that requests reduced motion,
use <http://127.0.0.1:4173/?motion=normal>. `?motion=reduce` forces the static
fallback for QA.

## Review path

1. Confirm the empty state.
2. Choose a local image or select the bundled demo artwork.
3. Open **あなたのしるし / Your mark** and compare Typed and Handwritten modes.
4. Select **しるしを付ける / Add my mark** and review the Preview overlay.
5. Under **Canary snapshot**, select **Typed** and then **Handwritten**. Run
   **しるしを確認 / Check a mark** for each and confirm that both are labeled as
   simulated stored-file marks, not the current profile mark or actual readback.
6. Return to **あなたのしるし / Your mark**, save the other profile mode, and
   confirm both Typed Add and Handwritten Add.
7. Change among `ja`, `en`, `zh-CN`, `zh-TW`, and `ko`.
8. Open Details to confirm that technical information is not permanently visible.

Browser-local mark persistence is isolated under
`shirushi.canary.personal-mark.v1`. The Canary record includes an explicit
`coordinateSpace` width and height so handwritten geometry can retain its source
aspect ratio. It is not the Python v1 Personal Mark schema and does not migrate
or overwrite existing Tk data.

## Canary boundary

- `adapters/browser-adapter.js` owns browser-only file selection and local mark
  storage. Its `addMark()` method returns a simulated SUCCESS solely to exercise
  the UI timeline. `inspectStoredSnapshot(mode)` returns fixed Typed and
  Handwritten `canary-file-snapshot` fixtures to review both Verify treatments.
  Neither fixture is the current profile mark, and neither implies C2PA readback.
- `app.js` owns shared interaction and motion behavior.
- `i18n.js` owns all five locale catalogs; components do not contain locale copy.
- The visible Personal Mark exists only in the Preview overlay. The demo never
  changes image pixels.

Adjustment 01 remains isolated in `editorial.css`. Adjustment 02 is the final
override in `capsule.css`: a white/black/neutral Capsule-derived Compact Utility
language with thin structural rules, large-type/micro-detail contrast, and orange
reserved for small action, focus, status, and motion accents. It does not copy a
dashboard layout and keeps the image dominant.

Full motion remains approximately 5.25 seconds. Typed Marks sit near the visual
center, reveal in language-natural typography, then use a light luminance response,
an irregular upward mask, and a few tiny fragments to sublimate. Handwritten Marks
separate the original SVG geometry into sharp stroke, glow, and diffusion layers;
the sharp layer fades first while the other layers spread slightly and are absorbed
into the image plane. These are temporary overlay metaphors. No Personal Mark
pixels are burned into the source or an output image. The final release runs from
approximately 4.30 to 5.20 seconds. Reduced motion remains static.

The future Tauri adapter should implement the same UI-facing operations while
delegating creation and inspection to the existing Python application-service
contracts. That integration is intentionally outside F1.

## Verify

```powershell
node --check web-canary/app.js
node --check web-canary/i18n.js
node --check web-canary/adapters/browser-adapter.js
node web-canary/verify.mjs
```
