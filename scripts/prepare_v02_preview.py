"""Prepare audited Preview inputs/config for Tauri NSIS; never build or install.

The accepted three-file assembler remains authority for native byte integrity.
Generated NSIS registration derives from the existing exact Explorer descriptor.
No vendor execution, download, registry write, signing or publication occurs here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile

from scripts import package_v02_development as package
from scripts import explorer_context_v02 as explorer

CHANNEL = "V02_PREVIEW_PUBLIC_TEST_CREDENTIALS"
ENV = "SHIRUSHI_PREVIEW_INSPECTION_MANIFEST_SHA256"
DOCUMENTS = ("README.md", "DISCLAIMER.md", "PRIVACY.md", "THIRD_PARTY_NOTICES.md")
SENTINEL = r"C:\ShirushiPreview\shirushi-desktop.exe"


def nsis_string(value: str, *, installed_path=False) -> str:
    # Escape constants first; only our fixed sentinel becomes an NSIS variable.
    value = value.replace("$", "$$").replace('"', '$\\"').replace("\n", "$\\n").replace("\r", "$\\r")
    return value.replace(SENTINEL, r"$INSTDIR\shirushi-desktop.exe") if installed_path else value


def registration_hook(desktop_sha256: str) -> str:
    """Emit finite, exact HKCU descriptors; never recursive/wildcard deletion.

    Every present tree must have exact node/value counts, REG_SZ types and values
    before register/remove. Missing roots are safe; changed/foreign roots stop.
    OPEN_LINK prevents an owned leaf being followed through a registry symlink.
    Ancestor links below the Windows Software/Classes anchor also fail closed.
    """
    trees = explorer.plan(SENTINEL, desktop_sha256)
    lines = ['; Generated from explorer_context_v02.plan; do not edit.', '!include "LogicLib.nsh"']
    def emit(line): lines.append(line)
    for prefix in ("", "un."):
        for root_index, (root, nodes) in enumerate(trees.items()):
            emit(f"Function {prefix}ShirushiAssertRoot_{root_index}")
            emit("  SetRegView 64")
            # Check each parent other than the OS's standard Classes anchor.
            parts = root.split("\\")
            for count in (1, *range(3, len(parts))):
                parent = "\\".join(parts[:count])
                emit(f"  System::Call 'advapi32::RegOpenKeyExW(p 0x80000001, w \"{parent}\", i 8, i 0x20119, *p .r9) i .r8'")
                emit("  ${If} $8 == 0")
                emit("    System::Call 'advapi32::RegQueryValueExW(p r9, w \"SymbolicLinkValue\", p 0, *i .r5, p 0, p 0) i .r8'")
                emit("    System::Call 'advapi32::RegCloseKey(p r9)'")
                emit("    ${If} $8 != 2")
                emit('      Abort "Shirushi registry ancestor is ambiguous; no registration changed."')
                emit("    ${EndIf}")
                emit("  ${ElseIf} $8 != 2")
                emit('    Abort "Shirushi registry ancestor could not be checked."')
                emit("  ${EndIf}")
            emit(f"  System::Call 'advapi32::RegOpenKeyExW(p 0x80000001, w \"{root}\", i 8, i 0x20119, *p .r9) i .r8'")
            emit("  ${If} $8 == 2")
            emit(f"    Goto shirushi_missing_{root_index}")
            emit("  ${ElseIf} $8 != 0")
            emit('    Abort "Cannot inspect Shirushi-owned registration."')
            emit("  ${EndIf}")
            emit("  System::Call 'advapi32::RegCloseKey(p r9)'")
            for relative, values in nodes.items():
                path = root + ("\\" + relative if relative else "")
                children = sum(1 for k in nodes if k and (k.rsplit("\\", 1)[0] if "\\" in k else "") == relative)
                emit(f"  System::Call 'advapi32::RegOpenKeyExW(p 0x80000001, w \"{path}\", i 8, i 0x20119, *p .r9) i .r8'")
                emit("  ${If} $8 != 0")
                emit('    Abort "Incomplete or inaccessible Shirushi registration."')
                emit("  ${EndIf}")
                emit("  System::Call 'advapi32::RegQueryInfoKeyW(p r9, p 0, p 0, p 0, *i .r6, p 0, p 0, *i .r7, p 0, p 0, p 0, p 0) i .r8'")
                emit(f"  ${{If}} $8 != 0\n  ${{OrIf}} $6 != {children}\n  ${{OrIf}} $7 != {len(values)}")
                emit("    System::Call 'advapi32::RegCloseKey(p r9)'")
                emit('    Abort "Foreign or changed Shirushi registration; nothing removed."')
                emit("  ${EndIf}")
                for name, value in values.items():
                    emit(f"  StrCpy $4 ${{NSIS_MAX_STRLEN}}\n  IntOp $4 $4 * 2")
                    emit(f"  System::Call 'advapi32::RegQueryValueExW(p r9, w \"{nsis_string(name)}\", p 0, *i .r5, w .r0, *i r4) i .r8'")
                    emit(f'  ${{If}} $8 != 0\n  ${{OrIf}} $5 != 1\n  ${{OrIf}} $0 != "{nsis_string(value, installed_path=True)}"')
                    emit("    System::Call 'advapi32::RegCloseKey(p r9)'")
                    emit('    Abort "Shirushi registration ownership mismatch."')
                    emit("  ${EndIf}")
                emit("  System::Call 'advapi32::RegCloseKey(p r9)'")
            emit("  StrCpy $R0 1\n  Return")
            emit(f"  shirushi_missing_{root_index}:\n  StrCpy $R0 0\nFunctionEnd")
        emit(f"Function {prefix}ShirushiAssertRegistration")
        for index in range(len(trees)): emit(f"  Call {prefix}ShirushiAssertRoot_{index}")
        emit("FunctionEnd")
        emit(f"Function {prefix}ShirushiRequireRegistration")
        for index in range(len(trees)):
            emit(f"  Call {prefix}ShirushiAssertRoot_{index}")
            emit('  ${If} $R0 != 1\n    Abort "Shirushi registration was not completed."\n  ${EndIf}')
        emit("FunctionEnd")
    emit("!macro SHIRUSHI_REGISTER_EXPLORER")
    emit("  Call ShirushiAssertRegistration")
    for index, (root, nodes) in enumerate(trees.items()):
        emit(f"  Call ShirushiAssertRoot_{index}")
        emit("  ${If} $R0 == 0")
        for relative, values in nodes.items():
            path = root + ("\\" + relative if relative else "")
            emit(f"    System::Call 'advapi32::RegCreateKeyExW(p 0x80000001, w \"{path}\", i 0, p 0, i 0, i 0x2011F, p 0, *p .r9, *i .r6) i .r8'")
            emit("    ${If} $8 != 0")
            emit('      Abort "Shirushi registration could not reserve a key."')
            emit("    ${EndIf}")
            emit("    System::Call 'advapi32::RegCloseKey(p r9)'")
            emit("    ${If} $6 != 1")
            emit('      Abort "Concurrent registration conflict; existing key not overwritten."')
            emit("    ${EndIf}")
            for name, value in values.items():
                emit("    ClearErrors")
                emit(f'    WriteRegStr HKCU "{path}" "{nsis_string(name)}" "{nsis_string(value, installed_path=True)}"')
                emit('    ${If} ${Errors}\n      Abort "Shirushi registration write failed."\n    ${EndIf}')
        emit("  ${EndIf}")
    emit("  Call ShirushiRequireRegistration\n!macroend")
    emit("!macro SHIRUSHI_UNREGISTER_EXPLORER")
    emit("  Call un.ShirushiAssertRegistration")
    for index, (root, nodes) in enumerate(trees.items()):
        emit(f"  Call un.ShirushiAssertRoot_{index}")
        emit("  ${If} $R0 == 1")
        for node_index, (relative, values) in reversed(list(enumerate(nodes.items()))):
            path = root + ("\\" + relative if relative else "")
            emit(f"    Call un.ShirushiAssertLeaf_{index}_{node_index}")
            for name in values:
                emit("    ClearErrors")
                emit(f'    DeleteRegValue HKCU "{path}" "{nsis_string(name)}"')
                emit('    ${If} ${Errors}\n      Abort "Shirushi unregister value removal failed."\n    ${EndIf}')
            emit(f'    ClearErrors\n    DeleteRegKey /ifempty HKCU "{path}"')
            emit('    ${If} ${Errors}\n      Abort "Shirushi unregister key removal failed."\n    ${EndIf}')
        emit("  ${EndIf}")
        emit(f"  System::Call 'advapi32::RegOpenKeyExW(p 0x80000001, w \"{root}\", i 8, i 0x20119, *p .r9) i .r8'")
        emit("  ${If} $8 == 0\n    System::Call 'advapi32::RegCloseKey(p r9)'\n  ${EndIf}")
        emit('  ${If} $8 != 2\n    Abort "Shirushi registration removal is not proven."\n  ${EndIf}')
    emit("!macroend")
    # Immediate leaf checks use zero remaining children because removal is
    # strictly leaf-first. Unknown values/children are retained, never purged.
    for index, (root, nodes) in enumerate(trees.items()):
        for node_index, (relative, values) in enumerate(nodes.items()):
            path = root + ("\\" + relative if relative else "")
            emit(f"Function un.ShirushiAssertLeaf_{index}_{node_index}")
            emit(f"  System::Call 'advapi32::RegOpenKeyExW(p 0x80000001, w \"{path}\", i 8, i 0x20119, *p .r9) i .r8'")
            emit('  ${If} $8 != 0\n    Abort "Shirushi unregister leaf changed."\n  ${EndIf}')
            emit("  System::Call 'advapi32::RegQueryInfoKeyW(p r9, p 0, p 0, p 0, *i .r6, p 0, p 0, *i .r7, p 0, p 0, p 0, p 0) i .r8'")
            emit(f'  ${{If}} $8 != 0\n  ${{OrIf}} $6 != 0\n  ${{OrIf}} $7 != {len(values)}')
            emit("    System::Call 'advapi32::RegCloseKey(p r9)'")
            emit('    Abort "Shirushi unregister leaf contains foreign state."\n  ${EndIf}')
            for name, value in values.items():
                emit('  StrCpy $4 ${NSIS_MAX_STRLEN}\n  IntOp $4 $4 * 2')
                emit(f"  System::Call 'advapi32::RegQueryValueExW(p r9, w \"{nsis_string(name)}\", p 0, *i .r5, w .r0, *i r4) i .r8'")
                emit(f'  ${{If}} $8 != 0\n  ${{OrIf}} $5 != 1\n  ${{OrIf}} $0 != "{nsis_string(value, installed_path=True)}"')
                emit("    System::Call 'advapi32::RegCloseKey(p r9)'")
                emit('    Abort "Shirushi unregister leaf ownership changed."\n  ${EndIf}')
            emit("  System::Call 'advapi32::RegCloseKey(p r9)'\nFunctionEnd")
    return "\n".join(lines) + "\n"


def config(package_root: Path, work_root: Path) -> dict:
    """Explicit overlay only. Base Development bundle.active remains false."""
    resources = {str(package_root / n): n for n in (package.HELPER, package.MANIFEST)}
    resources.update({str(work_root / "notices" / n): n for n in DOCUMENTS})
    return {"productName": "Shirushi Preview", "version": "0.2.0-preview.1",
            "identifier": "io.shirushi.desktop.preview",
            "bundle": {"active": True, "targets": ["nsis"], "resources": resources,
                "windows": {"allowDowngrades": False, "webviewInstallMode": {"type": "skip"},
                    "nsis": {"installMode": "currentUser", "installerHooks": str(work_root / "preview-hooks.nsh"),
                        "languages": ["English", "Japanese", "SimpChinese", "TradChinese", "Korean"],
                        "displayLanguageSelector": True}}}}


def cleanup_stage(stage: Path, owner) -> None:
    """Exact owned staging inventory only; never recursive best-effort cleanup."""
    if package._identity(package._metadata(stage, directory=True)) != owner:
        raise package.PackageError("PREVIEW_STAGE_CHANGED")
    allowed = {"package", "notices", "explorer-generated.nsh", "preview-hooks.nsh",
               "tauri-preview.generated.json", "preparation.json"}
    entries = list(stage.iterdir())
    if any(p.name not in allowed for p in entries): raise package.PackageError("PREVIEW_CLEANUP_REFUSED")
    for p in entries: package._metadata(p, directory=p.name in {"package", "notices"})
    if (stage / "package").exists():
        runtime_files = list((stage / "package").iterdir())
        if any(p.name not in package.FILES for p in runtime_files):
            raise package.PackageError("PREVIEW_CLEANUP_REFUSED")
        for p in runtime_files: package._metadata(p)
        for p in runtime_files: p.unlink()
        (stage / "package").rmdir()
    if (stage / "notices").exists():
        notices = list((stage / "notices").iterdir())
        if any(p.name not in DOCUMENTS for p in notices): raise package.PackageError("PREVIEW_CLEANUP_REFUSED")
        for p in notices: package._metadata(p)
        for p in notices: p.unlink()
        (stage / "notices").rmdir()
    for p in entries:
        if p.name not in {"package", "notices"}: p.unlink()
    stage.rmdir()


def prepare(desktop: str, helper: str, output_root: str, compiled_manifest_sha256: str) -> dict:
    if os.name != "nt": raise package.PackageError("WINDOWS_REQUIRED")
    if not package.HASH.fullmatch(compiled_manifest_sha256): raise package.PackageError("HASH_INVALID")
    frozen = package.capture_inputs(desktop, helper)
    root, parent_identity = package._output(output_root, frozen)
    repo = Path(__file__).resolve().parents[1]
    hook_source = repo / "desktop" / "installer" / "preview-hooks.nsh"
    hook_raw = package._read_small(hook_source, 32768)
    docs = {name: package._read_small(repo / name, 256 * 1024) for name in DOCUMENTS}
    manifest = package.prepare_manifest(helper)
    if hashlib.sha256(manifest).hexdigest() != compiled_manifest_sha256:
        raise package.PackageError("COMPILED_MANIFEST_DIGEST_MISMATCH")
    # Owned temporary subtree only; never overwrite output/source.
    stage = Path(tempfile.mkdtemp(prefix=".shirushi-preview-stage-", dir=root.parent))
    owner = package._identity(package._metadata(stage, directory=True))
    published = False
    try:
        record = package.assemble(desktop, helper, stage / "package", snapshot=frozen)
        package.audit_package(stage / "package", record)
        if (stage / "package" / package.MANIFEST).read_bytes() != manifest:
            raise package.PackageError("PREPARED_MANIFEST_CHANGED")
        (stage / "notices").mkdir()
        for name, raw in docs.items(): (stage / "notices" / name).write_bytes(raw)
        (stage / "explorer-generated.nsh").write_text(registration_hook(frozen.desktop.sha256), encoding="utf-8")
        (stage / "preview-hooks.nsh").write_bytes(hook_raw)
        overlay = config(root / "package", root)
        (stage / "tauri-preview.generated.json").write_bytes(package._canonical(overlay))
        result = {"schemaVersion": 1, "channel": CHANNEL, "installer": "TAURI_NSIS_CURRENT_USER_X64",
                  "compiledManifestSha256": compiled_manifest_sha256, "packageAudit": record,
                  "installerGenerated": False, "nativeBuildProven": False,
                  "prerequisiteExecutionAllowed": False, "productionTrust": False,
                  "config": "tauri-preview.generated.json"}
        (stage / "preparation.json").write_bytes(package._canonical(result))
        package._unchanged(frozen.desktop)
        package._unchanged(frozen.helper)
        _, now = package._output(root, frozen)
        if now != parent_identity: raise package.PackageError("OUTPUT_PARENT_CHANGED")
        if package._identity(package._metadata(stage, directory=True)) != owner:
            raise package.PackageError("PREVIEW_STAGE_CHANGED")
        # Windows rename: fail if output appeared; no overwrite/replace fallback.
        os.rename(stage, root)
        published = True
    finally:
        if not published and os.path.lexists(stage): cleanup_stage(stage, owner)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--desktop", required=True)
    parser.add_argument("--helper", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--compiled-manifest-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(prepare(args.desktop, args.helper, args.output_root, args.compiled_manifest_sha256), sort_keys=True))
        return 0
    except (package.PackageError, explorer.RegistrationError, OSError) as error:
        code = str(error) if not isinstance(error, OSError) else "FILESYSTEM_OPERATION_FAILED"
        print(json.dumps({"classification": "PREVIEW_PREPARATION_FAILED", "errorCode": code}))
        return 2


if __name__ == "__main__": raise SystemExit(main())
