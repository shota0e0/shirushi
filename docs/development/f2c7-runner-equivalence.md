# F2C.7 native DEVELOPMENT CANARY runner equivalence

This is an implementation checklist, not a new verification specification. `scripts/canary/Run-Canary.ps1` remains the reference. The native runner has only `prepare`, `launch`, and `sidecar-unavailable` actions. A failed or unobserved check is never a PASS. The tool is not a release or production Shirushi component.

| Reference launcher check | Native runner equivalent | Evidence / failure boundary |
| --- | --- | --- |
| All actions: required package files exist | Require fixed executable, bridge script, demo profile, `manifest.json`, `SHA256SUMS` | Missing component fails before action |
| All actions: package/root/profile/venv/WebView runtime links | Reject Windows reparse-point components, including traversed manifest files | Linked component fails closed |
| All actions: manifest schema and artifact type | Require schemaVersion 1 and `DEVELOPMENT_CANARY`; reject duplicate/unsafe paths | Invalid manifest fails closed |
| All actions: declared file sizes and SHA-256 | Hash each declared file, compare size/digest | Mismatch fails closed |
| All actions: unexpected payload detection | Enumerate files; exclude only `.venv-py312/` and demo WebView runtime; match manifest set exactly | Unmanifested/missing payload fails closed |
| All actions: `SHA256SUMS` grammar and coverage | Require one entry per payload plus `manifest.json`, excluding itself; validate hashes | Missing/duplicate/malformed sum fails closed |
| Prepare: explicit absolute `python.exe` | Accept only one Owner-supplied absolute executable file named `python.exe`; no PATH/Store/download fallback | Invalid path fails before mutation |
| Prepare: original interpreter identity | Run only that path with `-I -B -c` and require exactly `3.12.10|64|CPython` | Wrong identity fails before mutation |
| Prepare: fresh venv only | Refuse if `.venv-py312` exists; never overwrite or auto-clean partial state | Existing/partial state reported, retry requires fresh extraction |
| Prepare: isolated creation | Run validated interpreter with `-I -B -m venv --without-pip` and fixed package path, demo `LOCALAPPDATA`, unavailable flag cleared | Nonzero/blocked creation reports `PREPARE_FAILED` or explicit policy block |
| Prepare: post-create interpreter | Require fixed venv `Scripts/python.exe`, no reparse chain, same exact identity | Only then emit `PREPARE_OK` |
| Launch: fixed environment | Set child-only demo `LOCALAPPDATA`; clear unavailable flag in normal mode or set exactly `1` for negative scenario | Never expose real profile to child |
| Launch: fixed executable and venv | Require fixed package `shirushi-desktop.exe`; normal mode requires prepared venv and no reparse chain | No arbitrary command or Python selection |
| Launch: duplicate prevention | Reject an already-running exact package executable and hold a named single-run lock | No concurrent canary run |
| Launch: process identity | Launch direct child without shell; corroborate executable path and creation time | Failure is not a corroborated launch |
| Launch: descendant observation | Poll full descendant tree every ~300 ms; record PID, creation time, executable path, Python/WebView classification | Monitoring failure is PENDING, not zero children |
| Launch: sidecar evidence | Normal launch requires observed fixed Python bridge child; unavailable mode requires none | Emit `SIDECAR_OBSERVED` or `SIDECAR_NOT_OBSERVED`; negative-mode spawn fails |
| Launch: exit and shutdown | Wait for window/process exit, record exact exit code; nonzero fails | Emit `DESKTOP_STARTED` and `DESKTOP_EXITED` only when observed |
| Launch: orphan detection | Re-enumerate recorded PID+creation-time+path identities and late package bridge/WebView candidates | Emit `ORPHAN_PROCESS_NONE` only after complete monitoring; otherwise fail |
| Launch: policy rejection | Treat only explicit Windows policy rejection as `RUNNER_POLICY_BLOCKED` / `LOCAL POLICY BLOCKED`; other failures remain diagnostic PENDING | Never change security settings or claim policy cause without evidence |

The native implementation may use Windows process APIs instead of CIM, but must retain the observed identities and fail-closed outcomes above. The runner must not call PowerShell, `cmd`, or a shell command, and must not alter the Desktop bridge, Personal Mark, Add/Verify, or C2PA behavior.
