"""Owned HKCU static Explorer dispatch. No application/process launch or elevation.

Registration is explicit, not an assembler side effect. Uninstall must call
unregister with the originally recorded Desktop path/SHA before discarding them.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import PureWindowsPath
import re
import sys

try:
    from . import package_v02_development as package
except ImportError:
    import package_v02_development as package

OWNER = "Shirushi.Explorer.v02/1"
BASE = r"Software\Classes\SystemFileAssociations"
ROOTS = tuple(BASE + "\\" + ext + r"\shell\Shirushi.v02" for ext in (".png", ".jpg", ".jpeg"))


class RegistrationError(Exception):
    pass


def plan(desktop: str, sha256: str) -> dict:
    """Pure exact tree descriptor; also usable after the executable is removed."""
    if not isinstance(desktop, str) or not re.fullmatch(r"[a-zA-Z]:[\\/].{1,4000}", desktop):
        raise RegistrationError("ABSOLUTE_LOCAL_DESKTOP_REQUIRED")
    parts = re.split(r"[\\/]", desktop[3:])
    if any(not p or p in (".", "..") or p.endswith((".", " "))
           or package.RESERVED.match(p) or any(ord(c) < 32 or c in '<>:"|?*%' for c in p) for p in parts):
        raise RegistrationError("UNSAFE_DESKTOP_PATH")
    path = str(PureWindowsPath(desktop))
    if PureWindowsPath(path).name != "shirushi-desktop.exe" or not package.HASH.fullmatch(sha256):
        raise RegistrationError("DESKTOP_IDENTITY_INVALID")
    nodes = {"": {"MUIVerb": "Shirushi", "MultiSelectModel": "Single",
                  "ShirushiOwner": OWNER, "ShirushiDesktop": path, "ShirushiDesktopSha256": sha256},
             "ExtendedSubCommandsKey": {}, r"ExtendedSubCommandsKey\Shell": {}}
    for verb, operation, label in (("01.add", "add", "しるしを付ける"),
                                   ("02.inspect", "limited_inspect", "しるしを確認する")):
        key = "ExtendedSubCommandsKey\\Shell\\" + verb
        nodes[key] = {"MUIVerb": label, "MultiSelectModel": "Single"}
        # Fixed static verb template, interpreted by Explorer, not cmd/PowerShell.
        # Windows paths cannot contain quotes; argv has exactly one quoted %1.
        nodes[key + r"\command"] = {"": f'"{path}" --shirushi-explorer {operation} -- "%1"'}
    return {root: nodes for root in ROOTS}


def verify_desktop(desktop: str, sha256: str) -> None:
    plan(desktop, sha256)
    path = package._path(desktop)
    _, _, actual = package._read_file(path, package.MAX_EXECUTABLE, subsystem=2)
    if actual != sha256:
        raise RegistrationError("DESKTOP_SHA_MISMATCH")
    # Reject the previously built artifact without this dispatch ABI. These
    # markers supplement the trusted exact SHA, never replace its authority.
    raw = package._read_small(path, package.MAX_EXECUTABLE)
    if any(marker not in raw for marker in (b"--shirushi-explorer", b"bridge_take_explorer_request")):
        raise RegistrationError("DESKTOP_DISPATCH_NOT_PRESENT")
    import hashlib
    if hashlib.sha256(raw).hexdigest() != sha256:
        raise RegistrationError("DESKTOP_CHANGED")


def apply(registry, descriptor: dict, *, remove: bool = False) -> dict:
    """All-root preflight. Never adopt/delete partial, changed or foreign trees."""
    if tuple(descriptor) != ROOTS:
        raise RegistrationError("REGISTRATION_SCOPE_INVALID")
    before = {root: registry.snapshot(root) for root in ROOTS}
    for root, tree in before.items():
        if tree is not None and tree != descriptor[root]:
            raise RegistrationError("REGISTRATION_OWNERSHIP_CONFLICT")
    changed = []
    for root in ROOTS:
        if registry.snapshot(root) != before[root]:
            raise RegistrationError("REGISTRATION_CHANGED")
        if remove and before[root] is not None:
            registry.remove_exact(root, descriptor[root])
            changed.append(root)
        elif not remove and before[root] is None:
            registry.create_exclusive(root, descriptor[root])
            changed.append(root)
    expected = None if remove else descriptor[ROOTS[0]]
    if any(registry.snapshot(root) != expected for root in ROOTS):
        raise RegistrationError("REGISTRATION_POSTCONDITION_FAILED")
    return {"schemaVersion": 1, "operation": "unregister" if remove else "register",
            "classification": "EXPLORER_UNREGISTERED" if remove else "EXPLORER_REGISTERED",
            "changedRoots": changed, "owner": OWNER, "installerExecutions": 0}


class WindowsRegistry:
    """64-bit per-user view; bounded reads; exact leaf-first deletion, no recursion API."""
    def __init__(self):
        if os.name != "nt":
            raise RegistrationError("WINDOWS_REQUIRED")
        import winreg
        self.w = winreg

    def _open(self, path, access=None):
        w = self.w
        # OPEN_LINK prevents a registry symbolic link from redirecting inspection.
        return w.OpenKeyEx(w.HKEY_CURRENT_USER, path, 8,
                           (w.KEY_READ if access is None else access) | w.KEY_WOW64_64KEY)

    def _parents(self, root):
        parts = root.split("\\")
        for count in range(1, len(parts)):
            if count == 2:
                # HKCU\Software\Classes is Windows' standard per-user classes
                # hive link. Follow that OS anchor, but reject redirecting links
                # anywhere below it (and above it at Software).
                continue
            try:
                with self._open("\\".join(parts[:count])) as key:
                    try:
                        _, kind = self.w.QueryValueEx(key, "SymbolicLinkValue")
                    except FileNotFoundError:
                        continue
                    if kind == 6:
                        raise RegistrationError("REGISTRY_LINK_REJECTED")
            except FileNotFoundError:
                pass

    def snapshot(self, root):
        self._parents(root)
        result = {}
        def visit(relative):
            if len(result) >= 24 or relative.count("\\") > 8:
                raise RegistrationError("REGISTRY_TREE_BOUND")
            with self._open(root + ("\\" + relative if relative else "")) as key:
                children, values, _ = self.w.QueryInfoKey(key)
                if children > 16 or values > 16:
                    raise RegistrationError("REGISTRY_TREE_BOUND")
                entries = {}
                for i in range(values):
                    name, value, kind = self.w.EnumValue(key, i)
                    if kind != self.w.REG_SZ or not isinstance(value, str) or len(value) > 8192:
                        raise RegistrationError("REGISTRY_VALUE_UNEXPECTED")
                    entries[name] = value
                result[relative] = entries
                names = [self.w.EnumKey(key, i) for i in range(children)]
            for name in names:
                visit(relative + ("\\" if relative else "") + name)
        try:
            with self._open(root):
                pass
        except FileNotFoundError:
            return None
        visit("")
        return result

    def create_exclusive(self, root, tree):
        self._parents(root)
        w = self.w
        # RegCreateKeyEx disposition is the no-overwrite authority. If creation
        # partially fails, leave its bounded owned subtree for Owner review;
        # never perform an uncertain automatic rollback.
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        create = advapi.RegCreateKeyExW
        create.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32,
                           ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                           ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint32)]
        create.restype = ctypes.c_long
        handle, disposition = ctypes.c_void_p(), ctypes.c_uint32()
        # Sign-extended predefined HKEY is required on x64.
        error = create(ctypes.c_void_p(-2147483647), root, 0, None, 0,
                       w.KEY_READ | w.KEY_WRITE | w.KEY_WOW64_64KEY, None,
                       ctypes.byref(handle), ctypes.byref(disposition))
        if error:
            raise RegistrationError("REGISTRY_CREATE_FAILED")
        # winreg.PyHKEY is not publicly constructible. winreg accepts the raw
        # native handle; close it explicitly on every success/failure path.
        key = handle.value
        try:
            if disposition.value != 1:
                raise RegistrationError("REGISTRATION_CHANGED")
            for relative, values in tree.items():
                if relative:
                    with w.CreateKeyEx(key, relative, 0, w.KEY_WRITE | w.KEY_WOW64_64KEY) as child:
                        for name, value in values.items():
                            w.SetValueEx(child, name, 0, w.REG_SZ, value)
                else:
                    for name, value in values.items():
                        w.SetValueEx(key, name, 0, w.REG_SZ, value)
        finally:
            w.CloseKey(key)
        if self.snapshot(root) != tree:
            raise RegistrationError("REGISTRATION_POSTCONDITION_FAILED")

    def remove_exact(self, root, tree):
        if self.snapshot(root) != tree:
            raise RegistrationError("REGISTRATION_OWNERSHIP_CONFLICT")
        for relative in sorted(tree, key=lambda s: (s.count("\\"), len(s)), reverse=True):
            path = root + ("\\" + relative if relative else "")
            with self._open(path, self.w.KEY_READ | self.w.KEY_WRITE) as key:
                children, values, _ = self.w.QueryInfoKey(key)
                actual = {self.w.EnumValue(key, i)[0]: self.w.EnumValue(key, i)[1:] for i in range(values)}
                if children or actual != {name: (value, self.w.REG_SZ) for name, value in tree[relative].items()}:
                    raise RegistrationError("REGISTRATION_CHANGED")
                for name in tree[relative]:
                    self.w.DeleteValue(key, name)
            self.w.DeleteKeyEx(self.w.HKEY_CURRENT_USER, path, self.w.KEY_WOW64_64KEY, 0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "register", "unregister"))
    parser.add_argument("--desktop", required=True)
    parser.add_argument("--expected-desktop-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        descriptor = plan(args.desktop, args.expected_desktop_sha256)
        if args.action == "plan":
            result = {"schemaVersion": 1, "scope": "HKCU", "trees": descriptor, "mutations": 0}
        else:
            if args.action == "register":
                verify_desktop(args.desktop, args.expected_desktop_sha256)
            result = apply(WindowsRegistry(), descriptor, remove=args.action == "unregister")
            # Standard association-change notification; no Explorer restart.
            ctypes.WinDLL("shell32").SHChangeNotify(0x08000000, 0, None, None)
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 0
    except (RegistrationError, package.PackageError) as error:
        print(json.dumps({"classification": "EXPLORER_REGISTRATION_STOPPED", "errorCode": str(error)}))
    except OSError:
        print(json.dumps({"classification": "EXPLORER_REGISTRATION_STOPPED", "errorCode": "REGISTRY_IO_FAILED"}))
    return 2


if __name__ == "__main__":
    sys.exit(main())
