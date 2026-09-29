# Shirushi F2C.6 DEVELOPMENT CANARY

This artifact is a debug-only, unsigned DEVELOPMENT CANARY for bounded Owner verification. It is not a release, installer, signed build, or G2 approval. The separate `shirushi-canary-runner.exe` is a **DEVELOPMENT CANARY TOOL**, not a production Shirushi component. All manual results remain PENDING and G2 remains NOT READY until the checklist is completed and independently reviewed.

Prerequisites: Windows with an already installed and locally permitted CPython 3.12.10 x64, Microsoft Edge WebView2 Runtime, and the Microsoft Visual C++ runtime required by the debug executables. The native runner does not install or modify Python, Windows, security policy, WebView2, or the MSVC runtime. It never invokes PowerShell or a shell. Do not use `-ExecutionPolicy Bypass`, `Unblock-File`, elevation, or security-policy workarounds. If Windows explicitly refuses the runner executable, record **RUNNER_POLICY_BLOCKED** and STOP; the runner cannot report its own refusal. A missing runtime, child launch failure, bridge/read failure, or other unknown failure stays **PENDING**, not proof of a policy block.

The runner validates `manifest.json`, `SHA256SUMS`, payload closure, sizes, and hashes before every action. Only `.venv-py312/` after Prepare and `demo-profile/Shirushi/canary-webview/` after first launch are permitted runtime-only additions excluded from immutable payload closure. All other extra payload is rejected. The app and Python child use only the package-local synthetic `demo-profile`; do not copy real Personal Mark or profile data into this package. `Run-Canary.ps1` remains the reference implementation and CI equivalence oracle, not the required Owner launch path.

First verify the downloaded artifact provenance and digest against the authenticated GitHub Actions run/API evidence supplied for this Owner run. The package's internal manifest and checksums detect changed bytes after that provenance check; they are not signatures and do not establish authenticity by themselves. `THIRD_PARTY_NOTICES.md` is the repository's existing historical attribution text and may mention c2patool, TrustMark, or Python distribution. The actual canary `manifest.json` is authoritative: this artifact does not bundle those binaries, models, or a Python runtime.

From a fresh extracted artifact, open an ordinary Windows Command Prompt in the package directory and execute the native runner directly. Supply the explicit absolute path to the already permitted interpreter:

```text
shirushi-canary-runner.exe prepare --python "C:\absolute\path\to\python.exe"
shirushi-canary-runner.exe launch
```

Prepare accepts only CPython 3.12.10 x64 and creates a fresh `.venv-py312` with `--without-pip`. It refuses to overwrite an existing or partial venv and never cleans one automatically. For a retry after partial preparation, preserve the diagnostic and use a fresh extracted package. `PREPARE_OK` confirms the fixed interpreter only; it does not establish a healthy bridge.

## Exact eight-step manual checklist

1. From the authenticated GitHub Actions/API evidence, record the workflow run and artifact IDs, head SHA, tested merge SHA, and external artifact digest; compare them with the supplied evidence before relying on internal hashes. Extract to a fresh directory, confirm no other copy of this canary is running, and run Prepare once with the explicit permitted CPython 3.12.10 x64 path. Record PASS or the exact diagnostic; all status remains PENDING.
2. Run `shirushi-canary-runner.exe launch`. Record `DESKTOP_STARTED` and whether one fresh `shirushi-desktop.exe` process and one window appear without an OS/policy denial. Do not relaunch while it is running.
3. Observe `Connecting to Python bridge…` / `Python bridgeに接続中…` (internal `STARTING`) change to `Python bridge connected` / `Python bridge 接続済み` (internal `READY`). Record any exact visible error. A launch/runtime failure is not automatically LOCAL POLICY BLOCKED.
4. In the existing Personal Mark read surface, verify `Typed v2 loaded` / `Typed v2を読み込みました` from the synthetic fixture. The UI intentionally does not expose the literal `Niki` text; do not expect an image or use Add (Add is disabled).
5. Verify `Render assets unavailable — mark is not redrawn` / `描画資産未採用 — Markの再描画は行いません` (typed internal `ASSETS_UNAVAILABLE` / `RENDER_ASSETS_UNAVAILABLE`). This is the expected typed-not-rendered state, not a successful render and not a bug for this canary.
6. Close the Shirushi window. Record `DESKTOP_EXITED`, `SIDECAR_OBSERVED`, `CHILD_PROCESS_COUNTS`, and `ORPHAN_PROCESS_NONE`. Monitoring unavailable, a missing expected Python observation, or any remaining corroborated child is a nonzero runner failure/PENDING, never a zero count. Do not broadly kill Python processes.
7. Run `shirushi-canary-runner.exe sidecar-unavailable`. Verify `Python bridge unavailable. Restart the app.` / `Python bridgeを利用できません。アプリを再起動してください。` (internal `FAILED`), never a success/connected message, and no package-local Python child is observed. Close the window; this scenario intentionally returns `BRIDGE_UNAVAILABLE` without spawning the sidecar. The runner should emit `SIDECAR_NOT_OBSERVED` and `ORPHAN_PROCESS_NONE`.
8. Run `shirushi-canary-runner.exe launch` again as a fresh process. Verify the same localized connecting then connected messages and the same Typed v2 / render-assets-unavailable summary, then close the window and record shutdown evidence. There is no automatic recovery or request replay; restart is the designed recovery boundary. Mark the manual checklist result for review, still PENDING and G2 NOT READY.

The runner never kills an OS Python installation. The desktop host contains only the canary-owned package-local child in its job object and may terminate that child on crash/timeout. Preserve the artifact and recorded diagnostics for review; do not edit the package and claim a verified result. A runner `SIDECAR_OBSERVED` status proves only process observation; the UI's connected/loaded states are separate checks.
