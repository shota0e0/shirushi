"""Pure registration tests plus explicitly selected CI-only native registry proof.

No test launches Desktop. Default/local execution never writes host registry.
"""
import copy
import ctypes
import os
import unittest
import uuid
from unittest.mock import patch

from scripts import explorer_context_v02 as explorer

DESKTOP = r"C:\apps\日本語 space\shirushi-desktop.exe"
SHA = "a" * 64


class Registry:
    def __init__(self):
        self.trees = {r"Software\Classes\SystemFileAssociations\.png\shell\Other": {"": {"MUIVerb": "Other"}}}
        self.changes = []

    def snapshot(self, root):
        return copy.deepcopy(self.trees.get(root))

    def create_exclusive(self, root, tree):
        if root in self.trees:
            raise explorer.RegistrationError("REGISTRATION_CHANGED")
        self.changes.append(("create", root))
        self.trees[root] = copy.deepcopy(tree)

    def remove_exact(self, root, tree):
        if self.snapshot(root) != tree:
            raise explorer.RegistrationError("REGISTRATION_CHANGED")
        self.changes.append(("remove", root))
        del self.trees[root]


class ExplorerRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.plan = explorer.plan(DESKTOP, SHA)
        self.registry = Registry()
        self.unrelated = copy.deepcopy(self.registry.trees)

    def test_png_jpg_jpeg_only(self):
        self.assertEqual(tuple(self.plan), explorer.ROOTS)
        self.assertEqual(len(self.plan), 3)
        self.assertFalse(any("*" in key or ".gif" in key for key in self.plan))

    def test_exact_two_single_selection_actions(self):
        for tree in self.plan.values():
            self.assertEqual(tree[""]["MultiSelectModel"], "Single")
            for key, label, operation in (("01.add", "しるしを付ける", "add"), ("02.inspect", "しるしを確認する", "limited_inspect")):
                node = "ExtendedSubCommandsKey\\Shell\\" + key
                self.assertEqual(tree[node], {"MUIVerb": label, "MultiSelectModel": "Single"})
                self.assertEqual(tree[node + r"\command"][""], f'"{DESKTOP}" --shirushi-explorer {operation} -- "%1"')
            self.assertEqual(len(tree), 7)

    def test_register_and_remove_preserves_unrelated(self):
        self.assertEqual(explorer.apply(self.registry, self.plan)["classification"], "EXPLORER_REGISTERED")
        self.assertEqual(explorer.apply(self.registry, self.plan, remove=True)["classification"], "EXPLORER_UNREGISTERED")
        self.assertEqual(self.registry.trees, self.unrelated)

    def test_repeated_register_unregister(self):
        for _ in range(2):
            self.assertEqual(len(explorer.apply(self.registry, self.plan)["changedRoots"]), 3)
            self.assertEqual(explorer.apply(self.registry, self.plan)["changedRoots"], [])
            self.assertEqual(len(explorer.apply(self.registry, self.plan, remove=True)["changedRoots"]), 3)
            self.assertEqual(explorer.apply(self.registry, self.plan, remove=True)["changedRoots"], [])

    def test_foreign_root_blocks_all_writes(self):
        self.registry.trees[explorer.ROOTS[-1]] = {"": {"MUIVerb": "Third party"}}
        before = copy.deepcopy(self.registry.trees)
        for remove in (False, True):
            with self.assertRaises(explorer.RegistrationError):
                explorer.apply(self.registry, self.plan, remove=remove)
            self.assertEqual(self.registry.trees, before)
            self.assertEqual(self.registry.changes, [])

    def test_changed_owned_tree_is_never_removed(self):
        for mutation in (lambda t: t[""].update(ShirushiOwner="foreign"),
                         lambda t: t.update(extra={}),
                         lambda t: t["ExtendedSubCommandsKey\\Shell\\01.add\\command"].update({"": "other.exe"}),
                         lambda t: t[""].update(ShirushiDesktopSha256="b" * 64)):
            registry = Registry()
            explorer.apply(registry, self.plan)
            mutation(registry.trees[explorer.ROOTS[1]])
            before = copy.deepcopy(registry.trees)
            with self.assertRaises(explorer.RegistrationError):
                explorer.apply(registry, self.plan, remove=True)
            self.assertEqual(registry.trees, before)

    def test_missing_unregister_does_not_require_executable(self):
        with patch.object(explorer, "verify_desktop", side_effect=AssertionError("must not read missing binary")):
            self.assertEqual(explorer.apply(self.registry, self.plan, remove=True)["changedRoots"], [])

    def test_malformed_paths_and_identity_rejected(self):
        for path in ("relative.exe", r"\\server\app\shirushi-desktop.exe", r"C:\..\shirushi-desktop.exe",
                     r"C:\safe\file.exe", 'C:\\quote"\\shirushi-desktop.exe', r"C:\%TEMP%\shirushi-desktop.exe",
                     r"C:\safe:ads\shirushi-desktop.exe", r"C:\CON\shirushi-desktop.exe", r"C:\safe.\shirushi-desktop.exe"):
            with self.subTest(path=path), self.assertRaises(explorer.RegistrationError):
                explorer.plan(path, SHA)
        with self.assertRaises(explorer.RegistrationError):
            explorer.plan(DESKTOP, "A" * 64)

    def test_wrong_scope_rejected(self):
        self.plan[r"Software\Classes\*\shell\Other"] = {}
        with self.assertRaises(explorer.RegistrationError):
            explorer.apply(self.registry, self.plan)
        self.assertEqual(self.registry.changes, [])

    def test_identity_reparse_stale_binary_checks_fail_closed(self):
        with patch.object(explorer.package, "_path", return_value=DESKTOP), \
             patch.object(explorer.package, "_read_file", return_value=(None, 1234, "b" * 64)):
            with self.assertRaisesRegex(explorer.RegistrationError, "DESKTOP_SHA_MISMATCH"):
                explorer.verify_desktop(DESKTOP, SHA)
        with patch.object(explorer.package, "_path", side_effect=explorer.package.PackageError("REPARSE_PATH_REJECTED")):
            with self.assertRaises(explorer.package.PackageError):
                explorer.verify_desktop(DESKTOP, SHA)
        with patch.object(explorer.package, "_path", return_value=DESKTOP), \
             patch.object(explorer.package, "_read_file", return_value=(None, 1234, SHA)), \
             patch.object(explorer.package, "_read_small", return_value=b"old desktop"):
            with self.assertRaisesRegex(explorer.RegistrationError, "DESKTOP_DISPATCH_NOT_PRESENT"):
                explorer.verify_desktop(DESKTOP, SHA)

    def test_partial_create_is_not_success_or_destructive_rollback(self):
        def fail_create(root, tree):
            self.registry.trees[root] = {"": {"ShirushiOwner": explorer.OWNER}}
            raise OSError("synthetic create failure")
        self.registry.create_exclusive = fail_create
        with self.assertRaises(OSError):
            explorer.apply(self.registry, self.plan)
        self.assertTrue(all(self.registry.trees[key] == value for key, value in self.unrelated.items()))
        with self.assertRaises(explorer.RegistrationError):
            explorer.apply(self.registry, self.plan, remove=True)

    def test_native_create_uses_raw_handle_and_always_closes_it(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        class Create:
            def __call__(self, *args):
                ctypes.cast(args[-2], ctypes.POINTER(ctypes.c_void_p)).contents.value = 123
                ctypes.cast(args[-1], ctypes.POINTER(ctypes.c_uint32)).contents.value = 1
                return 0
        tree = {"": {"Owner": "fixture"}}
        for fails in (False, True):
            registry = object.__new__(explorer.WindowsRegistry)
            registry._parents = lambda root: None
            registry.snapshot = lambda root: tree
            registry.w = SimpleNamespace(KEY_READ=1, KEY_WRITE=2, KEY_WOW64_64KEY=4, REG_SZ=1,
                                         SetValueEx=Mock(side_effect=OSError("fixture") if fails else None), CloseKey=Mock())
            with patch.object(ctypes, "WinDLL", return_value=SimpleNamespace(RegCreateKeyExW=Create())):
                if fails:
                    with self.assertRaises(OSError):
                        registry.create_exclusive("fixture", tree)
                else:
                    registry.create_exclusive("fixture", tree)
            registry.w.SetValueEx.assert_called_once_with(123, "Owner", 0, 1, "fixture")
            registry.w.CloseKey.assert_called_once_with(123)


@unittest.skipUnless(os.environ.get("SHIRUSHI_EXPLORER_REGISTRY_CI") == "1", "CI-only isolated native registry proof")
class ExplorerNativeRegistryTests(unittest.TestCase):
    """Explicit CI-only suite, never included in the local pure-test selection.

    Production logical ROOTS/CLI stay fixed. Only this test adapter maps them to
    nonce-owned physical keys; the actual WindowsRegistry implementation is used.
    """
    def test_native_owned_registration_removal_with_real_desktop(self):
        self.assertEqual(os.name, "nt", "Windows CI required")
        self.assertEqual(os.environ.get("SHIRUSHI_EXPLORER_REGISTRY_CI"), "1")
        self.assertEqual(os.environ.get("GITHUB_ACTIONS"), "true")
        desktop = os.environ["SHIRUSHI_EXPLORER_NATIVE_DESKTOP"]
        sha = os.environ["SHIRUSHI_EXPLORER_NATIVE_DESKTOP_SHA256"]
        explorer.verify_desktop(desktop, sha)  # exact newly built binary, no launch
        descriptor = explorer.plan(desktop, sha)
        # Exercise Windows' argv decoding of the exact registered template,
        # not a shell string launcher. These paths are never opened/launched.
        import ctypes
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        shell.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
        shell.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        for tree in descriptor.values():
            for verb, operation in (("01.add", "add"), ("02.inspect", "limited_inspect")):
                template = tree["ExtendedSubCommandsKey\\Shell\\" + verb + "\\command"][""]
                for image in (r"C:\images\with spaces.png", r"C:\作品 & family\手書き.jpeg"):
                    count = ctypes.c_int()
                    values = shell.CommandLineToArgvW(template.replace("%1", image), ctypes.byref(count))
                    self.assertTrue(values)
                    try:
                        self.assertEqual([values[i] for i in range(count.value)],
                                         [desktop.replace("/", "\\"), "--shirushi-explorer", operation, "--", image])
                    finally:
                        kernel.LocalFree(values)
        native = explorer.WindowsRegistry()
        prefix = r"Software\Shirushi.Explorer.Native." + uuid.uuid4().hex
        physical = {root: prefix + "\\" + extension + r"\shell\Shirushi.v02"
                    for root, extension in zip(explorer.ROOTS, (".png", ".jpg", ".jpeg"))}
        marker = {"": {"Owner": "Shirushi.NativeRegistryFixture/1", "Nonce": prefix.rsplit(".", 1)[-1]}}
        self.assertIsNone(native.snapshot(prefix))
        native.create_exclusive(prefix, marker)

        class MappedRegistry:
            def snapshot(self, root):
                return native.snapshot(physical[root])
            def create_exclusive(self, root, tree):
                native.create_exclusive(physical[root], tree)
            def remove_exact(self, root, tree):
                native.remove_exact(physical[root], tree)

        mapped = MappedRegistry()
        sentinel = prefix + r"\.png\shell\UnrelatedFixture"
        sentinel_tree = {"": {"MUIVerb": "Unrelated preserved sentinel"}}
        try:
            native.create_exclusive(sentinel, sentinel_tree)
            for _ in range(2):
                self.assertEqual(len(explorer.apply(mapped, descriptor)["changedRoots"]), 3)
                for root in explorer.ROOTS:
                    self.assertEqual(native.snapshot(physical[root]), descriptor[root])
                self.assertEqual(explorer.apply(mapped, descriptor)["changedRoots"], [])
                self.assertEqual(native.snapshot(sentinel), sentinel_tree)

                # A foreign modification blocks all removal, not warning-only.
                path = physical[explorer.ROOTS[1]]
                with native._open(path, native.w.KEY_READ | native.w.KEY_WRITE) as key:
                    native.w.SetValueEx(key, "ForeignFixtureValue", 0, native.w.REG_SZ, "preserve")
                changed = {root: mapped.snapshot(root) for root in explorer.ROOTS}
                with self.assertRaises(explorer.RegistrationError):
                    explorer.apply(mapped, descriptor, remove=True)
                self.assertEqual({root: mapped.snapshot(root) for root in explorer.ROOTS}, changed)
                with native._open(path, native.w.KEY_READ | native.w.KEY_WRITE) as key:
                    self.assertEqual(native.w.QueryValueEx(key, "ForeignFixtureValue"), ("preserve", native.w.REG_SZ))
                    native.w.DeleteValue(key, "ForeignFixtureValue")  # our exact fixture value only

                self.assertEqual(len(explorer.apply(mapped, descriptor, remove=True)["changedRoots"]), 3)
                self.assertEqual(explorer.apply(mapped, descriptor, remove=True)["changedRoots"], [])
                self.assertTrue(all(mapped.snapshot(root) is None for root in explorer.ROOTS))
                self.assertEqual(native.snapshot(sentinel), sentinel_tree)
        finally:
            # No recursive cleanup. Unknown/partially changed tree is left and
            # fails the job rather than deleting an unproven item.
            for root in explorer.ROOTS:
                if mapped.snapshot(root) is not None:
                    native.remove_exact(physical[root], descriptor[root])
            if native.snapshot(sentinel) is not None:
                native.remove_exact(sentinel, sentinel_tree)
            for extension in (".png", ".jpg", ".jpeg"):
                for relative in (extension + r"\shell", extension):
                    path = prefix + "\\" + relative
                    if native.snapshot(path) is not None:
                        native.remove_exact(path, {"": {}})
            native.remove_exact(prefix, marker)
        self.assertIsNone(native.snapshot(prefix))
        print("EXPLORER_NATIVE_REGISTRY: THREE_OWNED_TREES; ADD_VERIFY; SINGLE_ONLY; REPEAT_SAFE; "
              "FOREIGN_PRESERVED; UNRELATED_UNCHANGED; MISSING_SAFE; FINAL_NONCE_ROOT_ABSENT; NO_APP_LAUNCH")


if __name__ == "__main__":
    unittest.main()
