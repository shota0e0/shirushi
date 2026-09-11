from __future__ import annotations

from contextlib import contextmanager
import hashlib
import importlib.metadata
import inspect
import json
from pathlib import Path
import shutil
import threading
import unittest
import urllib.error
import uuid
from unittest import mock


PROJECT = Path(__file__).resolve().parents[1]
SRC = PROJECT / "src"
import sys
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from inspection_service import LocalTrustMarkProbe
from trustmark import TrustMark
from trustmark_model_manager import (
    CachedTrustMark,
    FORBIDDEN_MODEL_BASENAMES,
    MODEL_VARIANT,
    ModelPreparationError,
    TrustMarkFactory,
    TrustMarkModelManager,
    find_bundled_model_violations,
)


@contextmanager
def workdir(prefix: str):
    runtime = PROJECT / "tests/.runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    root = runtime / f"{prefix}-{uuid.uuid4().hex}"
    root.mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)
        if runtime.exists() and not any(runtime.iterdir()):
            runtime.rmdir()


class FakeResponse:
    def __init__(self, content: bytes, url: str):
        self.content = content
        self.url = url
        self.offset = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def geturl(self):
        return self.url

    def read(self, size: int):
        block = self.content[self.offset:self.offset + size]
        self.offset += len(block)
        return block


def digest(value: bytes, algorithm: str) -> str:
    return hashlib.new(algorithm, value).hexdigest()


class Fixture:
    def __init__(self, root: Path, opener=None, version="0.9.0"):
        self.content = {
            "trustmark_P.yaml": b"yaml-fixture",
            "decoder_P.ckpt": b"decoder-fixture",
            "encoder_P.ckpt": b"encoder-fixture",
        }
        resources = []
        for name, value in self.content.items():
            resources.append({
                "filename": name,
                "size": len(value),
                "md5": digest(value, "md5"),
                "sha256": digest(value, "sha256"),
                "url": f"https://cc-assets.netlify.app/watermarking/trustmark-models/{name}",
                "purpose": "test",
            })
        self.manifest = root / "manifest.json"
        self.manifest.write_text(json.dumps({
            "schemaVersion": "1.0", "trustmarkVersion": "0.9.0", "variant": "P",
            "officialBaseUrl": "https://cc-assets.netlify.app/watermarking/trustmark-models/",
            "resources": resources,
        }), encoding="utf-8")
        self.calls = []

        def default_opener(request, timeout):
            self.calls.append(request)
            name = Path(request.full_url).name
            return FakeResponse(self.content[name], request.full_url)

        self.cache = root / "cache"
        self.manager = TrustMarkModelManager(
            manifest_path=self.manifest,
            cache_directory=self.cache,
            opener=opener or default_opener,
            version_getter=lambda _: version,
        )

    def seed(self):
        self.cache.mkdir(parents=True, exist_ok=True)
        for name, value in self.content.items():
            (self.cache / name).write_bytes(value)


class TrustMarkModelManagerTests(unittest.TestCase):
    def assert_code(self, code: str, callable_):
        with self.assertRaises(ModelPreparationError) as caught:
            callable_()
        self.assertEqual(code, caught.exception.code)

    def test_valid_cache_needs_no_network_and_works_offline(self):
        with workdir("model-valid") as root:
            fixture = Fixture(root, opener=lambda *_a, **_k: (_ for _ in ()).throw(urllib.error.URLError("offline")))
            fixture.seed()
            paths = fixture.manager.ensure_models()
            self.assertEqual(FORBIDDEN_MODEL_BASENAMES, set(paths))

    def test_missing_resources_are_downloaded_verified_and_atomically_committed(self):
        with workdir("model-missing") as root:
            fixture = Fixture(root)
            paths = fixture.manager.ensure_models()
            self.assertEqual(3, len(fixture.calls))
            self.assertTrue(all(path.is_file() for path in paths.values()))
            self.assertFalse(list(fixture.cache.glob("*.partial")))

    def test_network_failure_is_mapped_and_same_session_can_retry(self):
        with workdir("model-retry") as root:
            state = {"fail": True}
            fixture = None
            def opener(request, timeout):
                if state["fail"]:
                    raise urllib.error.URLError("offline")
                return FakeResponse(fixture.content[Path(request.full_url).name], request.full_url)
            fixture = Fixture(root, opener=opener)
            self.assert_code("MODEL_NETWORK_UNAVAILABLE", fixture.manager.ensure_models)
            state["fail"] = False
            self.assertEqual(3, len(fixture.manager.ensure_models()))

    def test_permission_and_storage_errors_are_mapped(self):
        for expected, error in (
            ("MODEL_CACHE_NOT_WRITABLE", PermissionError("denied")),
            ("MODEL_STORAGE_FULL", OSError(28, "full")),
        ):
            with self.subTest(expected=expected), workdir("model-fs") as root:
                fixture = Fixture(root, opener=lambda *_a, error=error, **_k: (_ for _ in ()).throw(error))
                self.assert_code(expected, fixture.manager.ensure_models)

    def test_checksum_mismatch_and_partial_download_never_reach_final(self):
        for content in (b"wrong-content", b""):
            with self.subTest(content=content), workdir("model-integrity") as root:
                fixture = Fixture(root, opener=lambda request, timeout, content=content: FakeResponse(content, request.full_url))
                self.assert_code("MODEL_INTEGRITY_FAILED", fixture.manager.ensure_models)
                self.assertFalse(any((fixture.cache / name).exists() for name in fixture.content))
                self.assertFalse(list(fixture.cache.glob("*.partial")))

    def test_corrupt_cached_resource_is_not_used_and_is_reacquired(self):
        with workdir("model-corrupt") as root:
            fixture = Fixture(root)
            fixture.seed()
            (fixture.cache / "decoder_P.ckpt").write_bytes(b"corrupt")
            fixture.manager.ensure_models()
            self.assertEqual(1, len(fixture.calls))
            self.assertEqual(fixture.content["decoder_P.ckpt"], (fixture.cache / "decoder_P.ckpt").read_bytes())

    def test_final_file_is_reopened_and_reverified(self):
        with workdir("model-postverify") as root:
            fixture = Fixture(root)
            original = fixture.manager._valid
            def verify(path, resource):
                if path == fixture.cache / resource.filename and path.exists():
                    return False
                return original(path, resource)
            with mock.patch.object(fixture.manager, "_valid", side_effect=verify):
                self.assert_code("MODEL_INTEGRITY_FAILED", fixture.manager.ensure_models)
            self.assertFalse((fixture.cache / "trustmark_P.yaml").exists())

    def test_concurrent_requests_are_single_flight(self):
        with workdir("model-concurrent") as root:
            fixture = Fixture(root)
            results = []
            threads = [threading.Thread(target=lambda: results.append(fixture.manager.ensure_models())) for _ in range(2)]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
            self.assertEqual(2, len(results))
            self.assertEqual(3, len(fixture.calls))

    def test_version_mismatch_fails_before_network(self):
        with workdir("model-version") as root:
            fixture = Fixture(root, version="0.9.1")
            self.assert_code("MODEL_PREPARATION_FAILED", fixture.manager.ensure_models)
            self.assertEqual([], fixture.calls)

    def test_request_contains_no_image_or_device_information(self):
        with workdir("model-request") as root:
            fixture = Fixture(root)
            fixture.manager.ensure_models()
            for request in fixture.calls:
                self.assertIsNone(request.data)
                serialized = repr((request.full_url, request.headers)).lower()
                for forbidden in ("image", "exif", "cawg", "payload", "username", "device"):
                    self.assertNotIn(forbidden, serialized)

    def test_failed_candidate_is_not_cached_by_local_probe(self):
        attempts = []
        class Decoder:
            def decode(self, *_a, **_k): return ("", False, 0)
        def factory():
            attempts.append(1)
            if len(attempts) == 1:
                raise ModelPreparationError("MODEL_LOAD_FAILED")
            return Decoder()
        probe = LocalTrustMarkProbe(factory=factory)
        with self.assertRaises(ModelPreparationError):
            probe._get_decoder()
        self.assertIsNone(probe._decoder)
        self.assertIsInstance(probe._get_decoder(), Decoder)

    def test_factory_rejects_partial_instances(self):
        class Manager:
            def ensure_models(self): return {}
        class Partial:
            def __init__(self, **_kwargs): self.encoder, self.decoder = object(), None
        factory = TrustMarkFactory(Manager(), Partial)
        self.assert_code("MODEL_LOAD_FAILED", factory.create)

    def test_packaging_gate_detects_names_and_known_hashes(self):
        with workdir("model-payload") as root:
            named = root / "decoder_P.ckpt"
            named.write_bytes(b"unrelated")
            known = root / "renamed.bin"
            known.write_bytes(b"content")
            with mock.patch("trustmark_model_manager._hashes", return_value=(7, "x", next(iter(__import__('trustmark_model_manager').FORBIDDEN_MODEL_SHA256)))):
                violations = find_bundled_model_violations(root)
            self.assertIn(str(named), violations)
            self.assertIn(str(known), violations)

    def test_version_specific_upstream_contract(self):
        self.assertEqual("0.9.0", importlib.metadata.version("trustmark"))
        self.assertTrue(issubclass(CachedTrustMark, TrustMark))
        self.assertIn("load_model", CachedTrustMark.__dict__)
        self.assertIn("check_and_download", CachedTrustMark.__dict__)
        self.assertEqual(
            "(self, config_path, weight_path, device, secret_len, part='all')",
            str(inspect.signature(TrustMark.load_model)),
        )
        self.assertEqual({"trustmark_P.yaml", "decoder_P.ckpt", "encoder_P.ckpt"}, set(FORBIDDEN_MODEL_BASENAMES))

    def test_production_manifest_matches_upstream_md5_and_variant(self):
        data = json.loads((PROJECT / "config/trustmark-models-v0.9.0.json").read_text(encoding="utf-8"))
        self.assertEqual("0.9.0", data["trustmarkVersion"])
        self.assertEqual(MODEL_VARIANT, data["variant"])
        self.assertEqual({
            "trustmark_P.yaml": "fe40df84a7feeebfceb7a7678d7e6ec6",
            "decoder_P.ckpt": "9450972bc0c3c217cb7b8220dd2f7a3c",
            "encoder_P.ckpt": "0a18f6de6d57c6ef7dda30ce6154a775",
        }, {item["filename"]: item["md5"] for item in data["resources"]})
        self.assertEqual({
            "trustmark_P.yaml": (2009, "43f37103f92efa8bd6b1c5902bb537cc12a981dc699fca19d9bb7de8c62d03d9"),
            "decoder_P.ckpt": (47647180, "f5f1d570c889c5908c6e4a28c07dc90cf33990ebaf24037254ebe0b8849b1bdc"),
            "encoder_P.ckpt": (17301306, "659d427f72d16eea4e8fbda175ae72e3a78830dc6cf44b63642a5a4b28a2b4e2"),
        }, {item["filename"]: (item["size"], item["sha256"]) for item in data["resources"]})


if __name__ == "__main__":
    unittest.main()
