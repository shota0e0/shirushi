# v0.2 single-image Explorer dispatch (development)

Owner's 2026-10-07 RUN authorizes this bounded implementation. Explorer is
IN V0.2 / DESKTOP CORE; batch is deferred to v0.3. No signing, installer redesign,
prerequisite work, COM DLL, processing inside Explorer, commit or push.

## Implementation contract

Static per-user submenu: **Shirushi → しるしを付ける / しるしを確認する**.
Scope is `.png`, `.jpg`, `.jpeg` only. Parent and both verbs explicitly use
`MultiSelectModel=Single`. The static mechanism is described by Microsoft's
[ExtendedSubCommandsKey documentation](https://learn.microsoft.com/en-us/windows/win32/shell/how-to-create-cascading-menus-with-the-extendedsubcommandskey-registry-entry)
and [single-selection model](https://learn.microsoft.com/en-us/windows/win32/shell/how-to-employ-the-verb-selection-model).
This is a classic static menu, not a claim of a Windows 11 modern top-level
IExplorerCommand integration; actual placement must be observed in native smoke.

Registry template (no cmd.exe/PowerShell or executable lookup):

```text
"<exact desktop path>" --shirushi-explorer add -- "%1"
"<exact desktop path>" --shirushi-explorer limited_inspect -- "%1"
```

`args_os` accepts no arguments for normal startup, or exactly these four
arguments. Unknown operation/extra arguments/relative paths/UNC/devices/ADS/
traversal/reserved names/control characters/unsupported extensions are rejected.
Existing local fixed-drive, reparse, pin/read/hash and image-magic checks apply;
extension and MIME must agree. Image decoding must succeed in the existing UI
before an action starts. Add retains its32MiB source limit; inspection accepts
the existing64MiB generated-image allowance. No extra helper protocol is added.

`bridge_take_explorer_request` takes no frontend path/intent. It atomically
consumes the startup request before image I/O, returning null or a strict
`{operation,image}` once. IPC failure/reload/concurrent retrieval cannot replay it.
A session-local Windows named mutex (current token SID, canonical path,
operation digest) rejects concurrent duplicate Explorer requests. Its handle
lasts for the Explorer-launched Desktop process lifetime. Total ordering of
distinct images/actions and batch support are not introduced.

Each Explorer launch opens a separate ordinary Desktop window; it does not
forward to an existing window or copy private session state across processes.
If a mark is available in that UI session, existing Add reuses it. Otherwise
the existing handwritten-default editor opens and waits for explicit save;
cancel does not Add. No mark is invented or silently migrated from the legacy
profile. Another invocation of the same operation/path is rejected while that
Explorer window retains its claim; closing it releases the claim. This bounds
duplicate launches without a new global singleton/IPC service.

The existing UI bootstrap, DesktopAdapter, product command, supervisor and Rust
helper do all work **outside explorer.exe**. Verify is actual Limited Inspection,
never an applied-state assumption. Unmarked stays absent; `INCOMPLETE`,
`INSPECTED` C2PA/CAWG, `NOT_CHECKED` TrustMark remain unchanged. Existing no-clobber
separate output/source identity protections remain authoritative. Personal Mark
pixels/motion remain UI-only.

## Explicit development registration / uninstall seam

`scripts/explorer_context_v02.py` is a no-launch developer registration tool.
Python is needed for this development tool, not a new image-processing engine.
No assembler/install side effect is added. `bundle.active=false` is unchanged.

```text
python scripts/explorer_context_v02.py plan --desktop <absolute desktop path> --expected-desktop-sha256 <trusted lowercase SHA>
python scripts/explorer_context_v02.py register --desktop <same path> --expected-desktop-sha256 <same SHA>
python scripts/explorer_context_v02.py unregister --desktop <same path> --expected-desktop-sha256 <same SHA>
```

Register requires the exact expected x64 GUI PE SHA, safe local/no-reparse path,
and both new dispatch ABI strings. Those strings reject the old artifact;
they do NOT authorize an unknown executable independently of its trusted SHA.
No supplied size/hash becomes authority without reading the actual file.

Exactly these HKCU64 roots are owned:

```text
Software\Classes\SystemFileAssociations\.png\shell\Shirushi.v02
Software\Classes\SystemFileAssociations\.jpg\shell\Shirushi.v02
Software\Classes\SystemFileAssociations\.jpeg\shell\Shirushi.v02
```

Each tree has seven keys: root, `ExtendedSubCommandsKey`, its `Shell`, then
`01.add` / `02.inspect` with individual `command` leaves. Values include owner
`Shirushi.Explorer.v02/1`, exact Desktop path and SHA. Parent default is unset.
All values are exact REG_SZ. No CommandStore/HKLM/default association/other
handler modification. HKCU's standard `Software\Classes` hive link is accepted;
redirecting registry links below it are rejected.

All three trees must be absent or exactly equal before any write. Registration
uses exclusive root creation; repeats are no-ops. Unregister needs the original
descriptor, NOT a still-existing executable; it compares all trees before any
deletion and deletes exact known leaves only. Missing is idempotent. Foreign
keys/values/changed identity cause STOP, not adoption or broad cleanup.
Concurrent modification is rechecked, and an I/O/partial failure never reports
success or triggers uncertain rollback. A partial tree is left for Owner review;
the normal remover refuses to guess its ownership. Installation must retain the
original path/SHA and call this exact removal contract before discarding it.
This defines the uninstall seam, not an already-built NSIS uninstaller.
After explicit register/remove, standard association-change notification is
sent; Explorer is not restarted.

## Evidence and remaining boundary

- Python:88 tests,84PASS, four existing skips; includes12 synthetic
  registration/path/identity/ownership/partial-failure tests. No live writes.
- Web:719 checks PASS across foundation137, UI193, Desktop76, product55,
  limited58, inspection83, Personal Mark117. These are nonnative/synthetic proof.
- Rust:12 tests authored (parser/one-shot/concurrency/Windows claim plus three
  actual PNG/JPEG handoff/duplicate/malformed-image tests), formatting check PASS;
  compilation/native test execution pending the current authorized CI run.
- CI-only native Python test uses the actual WindowsRegistry implementation,
  mapping the three fixed logical roots to nonce-owned physical HKCU keys. It
  checks real Desktop identity, Windows argv decoding, repeat/missing removal,
  unrelated sentinel retention and foreign-value refusal. Exact-owned cleanup
  only; default/local tests do not write registry. This test is not counted as
  local PASS and does not alter the four accepted pre-existing skips.
- Read-only Windows adapter probe: all three exact owned roots ABSENT.
- Actual registration/unregistration/menu display and Desktop/helper right-click
  end-to-end remain NOT_RUN. No old artifact registered or launched.

Current classification: `V02_EXPLORER_CONTEXT_INTEGRATION_PARTIAL` — implementation
and available regressions complete; new native build/menu evidence unavailable.
Existing Development PASS classifications stay valid; native UI access-denied
launch limitation (`POLICY_ATTRIBUTION_UNPROVEN`) is not converted into a product
failure. SAC/SmartScreen/policy cause is not determined. No security-policy bypass.
Owner has now authorized one coherent Explorer-only validation commit/push and
the existing Windows CI path; native and registration evidence is pending that
run. Successful native contracts can establish Development PASS without a real
Explorer UI click; that remains a later bounded Owner smoke. No new
research lane or prerequisite Gate is required. After native closure, return to
v0.2 release closure. Do not claim Explorer PASS from the older CI run.
