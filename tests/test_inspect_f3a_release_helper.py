"""Pure in-memory parser tests; no native execution or repository fixture changes."""
import importlib.util
import json
import os
from pathlib import Path
import struct
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("release_evidence", Path(__file__).parents[1] / "scripts/inspect_f3a_release_helper.py")
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)


def minimal_pe():
    data = bytearray(0x1200)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HHI", data, 0x84, 0x8664, 1, 123)
    struct.pack_into("<H", data, 0x94, 240)
    opt = 0x98
    struct.pack_into("<H", data, opt, 0x20B)
    struct.pack_into("<Q", data, opt + 24, 0x140000000)
    struct.pack_into("<I", data, opt + 60, 0x200)
    struct.pack_into("<H", data, opt + 68, 3)
    struct.pack_into("<I", data, opt + 108, 16)
    section = opt + 240
    data[section:section + 8] = b".rdata\0\0"
    struct.pack_into("<IIII", data, section + 8, 0x1000, 0x1000, 0x1000, 0x200)
    return data


def directory(data, index, rva, size):
    struct.pack_into("<II", data, 0x98 + 112 + index * 8, rva, size)


class PeTests(unittest.TestCase):
    def test_valid_minimal_x64_console(self):
        pe = evidence.PE(bytes(minimal_pe()))
        self.assertEqual(pe.machine, 0x8664)
        self.assertEqual(pe.subsystem, 3)
        self.assertEqual(pe.sections[0]["name"], ".rdata")
        self.assertEqual(pe.imports(), [])
        self.assertEqual(pe.imports(True), [])
        self.assertEqual(pe.debug(), [])
        self.assertFalse(pe.resources()["present"])

    def test_malformed_and_truncated_headers(self):
        for data in [b"", b"MZ", b"bad", bytes(minimal_pe()[:300])]:
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                evidence.PE(data)

    def test_virtual_tail_is_not_file_bytes(self):
        pe = evidence.PE(bytes(minimal_pe()))
        with self.assertRaises(ValueError):
            pe.offset(0x2000)

    def test_import_and_ordinal(self):
        data = minimal_pe()
        directory(data, 1, 0x1000, 40)
        struct.pack_into("<IIIII", data, 0x200, 0x1080, 0, 0, 0x1060, 0x1080)
        data[0x260:0x26D] = b"KERNEL32.dll\0"
        struct.pack_into("<QQQ", data, 0x280, 0x10B0, (1 << 63) | 3, 0)
        data[0x2B2:0x2BE] = b"ExitProcess\0"
        result = evidence.PE(bytes(data)).imports()
        self.assertEqual(result, [dict(dll="kernel32.dll", symbols=["ExitProcess", "ordinal:3"])])

    def test_delay_import(self):
        data = minimal_pe()
        directory(data, 13, 0x1000, 64)
        struct.pack_into("<IIIIIIII", data, 0x200, 1, 0x1060, 0, 0x1080, 0x1080, 0, 0, 0)
        data[0x260:0x26D] = b"KERNEL32.dll\0"
        struct.pack_into("<QQ", data, 0x280, (1 << 63) | 7, 0)
        self.assertEqual(evidence.PE(bytes(data)).imports(True)[0]["symbols"], ["ordinal:7"])

    def test_import_directory_must_terminate(self):
        data = minimal_pe()
        directory(data, 1, 0x1000, 20)
        struct.pack_into("<IIIII", data, 0x200, 0x1080, 0, 0, 0x1060, 0x1080)
        data[0x260:0x26D] = b"KERNEL32.dll\0"
        with self.assertRaisesRegex(ValueError, "TERMINATOR"):
            evidence.PE(bytes(data)).imports()

    def test_codeview_path_is_not_published(self):
        data = minimal_pe()
        directory(data, 6, 0x1000, 28)
        path = b"C:\\private\\user\\shirushi_inspection_helper.pdb\0"
        cv = b"RSDS" + bytes(20) + path
        struct.pack_into("<IIHHIIII", data, 0x200, 0, 1, 0, 0, 2, len(cv), 0x1080, 0x280)
        data[0x280:0x280 + len(cv)] = cv
        record = evidence.PE(bytes(data)).debug()[0]
        self.assertTrue(record["pdbReferenceIsAbsolute"])
        self.assertTrue(record["pdbReferencePresent"])
        self.assertEqual(record["pdbBasename"], "shirushi_inspection_helper.pdb")
        self.assertNotIn("private", str(record))
        self.assertNotIn("user", str(record))

    def test_resource_leaf_identity(self):
        data = minimal_pe()
        directory(data, 2, 0x1000, 0x100)
        struct.pack_into("<HH", data, 0x20C, 0, 1)
        struct.pack_into("<II", data, 0x210, 24, 0x30)
        struct.pack_into("<IIII", data, 0x230, 0x1080, 4, 0, 0)
        data[0x280:0x284] = b"test"
        resource = evidence.PE(bytes(data)).resources()
        self.assertEqual(resource["types"], [24])
        self.assertEqual(resource["leaves"][0]["sha256"], evidence.sha(b"test"))

    def test_resource_cycle_rejected(self):
        data = minimal_pe()
        directory(data, 2, 0x1000, 0x100)
        struct.pack_into("<HH", data, 0x20C, 0, 1)
        struct.pack_into("<II", data, 0x210, 24, 0x80000000)
        with self.assertRaisesRegex(ValueError, "RESOURCE_BOUND"):
            evidence.PE(bytes(data)).resources()

    def test_vc_runtime_not_promoted_to_os_component(self):
        self.assertEqual(evidence.dll_class("VCRUNTIME140.dll", {"vcruntime140.dll"}), "VC_RUNTIME")
        self.assertEqual(evidence.dll_class("kernel32.dll", {"kernel32.dll"}), "WINDOWS_SYSTEM")
        self.assertEqual(evidence.dll_class("api-ms-win-crt-runtime-l1-1-0.dll", set()), "WINDOWS_SYSTEM")
        self.assertEqual(evidence.dll_class("unknown.dll", set()), "UNKNOWN")

    def test_icu_override_refused_before_any_build(self):
        with patch.dict(os.environ, {"ICU4X_DATA_DIR": ""}, clear=True):
            with self.assertRaisesRegex(ValueError, "UNCONTROLLED_BUILD_ENVIRONMENT"):
                evidence.preflight(Path(__file__).parents[1])

    def test_profile_override_refused(self):
        with patch.dict(os.environ, {"CARGO_PROFILE_RELEASE_PANIC": "abort"}, clear=True):
            with self.assertRaisesRegex(ValueError, "UNCONTROLLED_BUILD_ENVIRONMENT"):
                evidence.preflight(Path(__file__).parents[1])

    def test_incremental_override_refused(self):
        with patch.dict(os.environ, {"CARGO_INCREMENTAL": "1"}, clear=True):
            with self.assertRaisesRegex(ValueError, "UNCONTROLLED_BUILD_ENVIRONMENT"):
                evidence.preflight(Path(__file__).parents[1])

    def test_empty_runtime_observation_is_not_pass(self):
        binary = json.dumps({"pe": {"imports": [], "delayImports": []}})
        log = 'RELEASE_HELPER_MODULES: {"sampled_post_resume":false,"successful_samples":0,"handle_closed":true,"modules":[]}'
        with patch.object(Path, "read_text", side_effect=[binary, log]):
            with self.assertRaisesRegex(ValueError, "INSUFFICIENT_RUNTIME_MODULE_OBSERVATION"):
                evidence.compare(Path("binary-evidence"), Path("test-log"))

    def test_static_and_observed_inventory_are_distinct(self):
        binary = json.dumps({"pe": {"imports": [{"dll": "api-ms-win-core.dll"}], "delayImports": []}})
        observation = dict(sampled_post_resume=True, successful_samples=1, handle_closed=True,
                           modules=[dict(basename="shirushi-inspection-helper.exe", category="OWN_HELPER"),
                                    dict(basename="kernelbase.dll", category="WINDOWS_SYSTEM")])
        with patch.object(Path, "read_text", side_effect=[binary, "RELEASE_HELPER_MODULES: " + json.dumps(observation)]), patch.object(evidence, "emit") as output:
            evidence.compare(Path("binary-evidence"), Path("test-log"))
        record = output.call_args.args[1]
        self.assertEqual(record["staticNotObserved"], ["api-ms-win-core.dll"])
        self.assertEqual(record["observedNotStatic"], ["kernelbase.dll", "shirushi-inspection-helper.exe"])
        self.assertIn("not exhaustive", record["limitation"])


if __name__ == "__main__":
    unittest.main()
