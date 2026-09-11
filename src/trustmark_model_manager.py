"""Acquire pinned TrustMark 0.9.0 model resources into a user cache."""

from __future__ import annotations

from dataclasses import dataclass
import errno
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import ssl
import threading
from typing import Any, Callable
import urllib.error
import urllib.request
from urllib.parse import urlparse
import uuid

from trustmark import TrustMark
from runtime_paths import RuntimePaths, resolve_runtime_paths


TRUSTMARK_VERSION = "0.9.0"
MODEL_VARIANT = "P"
MODEL_ERROR_CODES = (
    "MODEL_NETWORK_UNAVAILABLE",
    "MODEL_CACHE_NOT_WRITABLE",
    "MODEL_STORAGE_FULL",
    "MODEL_INTEGRITY_FAILED",
    "MODEL_LOAD_FAILED",
    "MODEL_PREPARATION_FAILED",
)
FORBIDDEN_MODEL_BASENAMES = frozenset(
    {"trustmark_P.yaml", "decoder_P.ckpt", "encoder_P.ckpt"}
)
FORBIDDEN_MODEL_SHA256 = frozenset(
    {
        "43f37103f92efa8bd6b1c5902bb537cc12a981dc699fca19d9bb7de8c62d03d9",
        "f5f1d570c889c5908c6e4a28c07dc90cf33990ebaf24037254ebe0b8849b1bdc",
        "659d427f72d16eea4e8fbda175ae72e3a78830dc6cf44b63642a5a4b28a2b4e2",
    }
)
_ACQUISITION_LOCK = threading.Lock()
_PREPARATION_ACTIVE = threading.Event()


class ModelPreparationError(RuntimeError):
    """A stable model-preparation failure safe to map at product boundaries."""

    def __init__(self, code: str):
        if code not in MODEL_ERROR_CODES:
            raise ValueError(f"unknown model error code: {code}")
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ModelResource:
    filename: str
    size: int
    md5: str
    sha256: str
    url: str
    purpose: str


def default_cache_directory() -> Path:
    if os.name == "nt":
        return resolve_runtime_paths().user_data_root / "models/trustmark" / TRUSTMARK_VERSION / MODEL_VARIANT
    base = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    return base / "Shirushi/models/trustmark" / TRUSTMARK_VERSION / MODEL_VARIANT


def is_model_preparation_in_progress() -> bool:
    return _PREPARATION_ACTIVE.is_set()


def _hashes(path: Path) -> tuple[int, str, str]:
    md5 = hashlib.md5(usedforsecurity=False)
    sha256 = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(block)
            md5.update(block)
            sha256.update(block)
    return size, md5.hexdigest(), sha256.hexdigest()


def _mapped_os_error(exc: OSError) -> ModelPreparationError:
    if exc.errno in {errno.ENOSPC, getattr(errno, "EDQUOT", -1)}:
        return ModelPreparationError("MODEL_STORAGE_FULL")
    if isinstance(exc, PermissionError) or exc.errno in {errno.EACCES, errno.EPERM, errno.EROFS}:
        return ModelPreparationError("MODEL_CACHE_NOT_WRITABLE")
    return ModelPreparationError("MODEL_PREPARATION_FAILED")


class TrustMarkModelManager:
    """Prepare model files without accepting or observing any image information."""

    def __init__(
        self,
        *,
        manifest_path: Path | None = None,
        cache_directory: Path | None = None,
        runtime_paths: RuntimePaths | None = None,
        opener: Callable[..., Any] = urllib.request.urlopen,
        version_getter: Callable[[str], str] = importlib.metadata.version,
        timeout_seconds: float = 60.0,
    ) -> None:
        paths = runtime_paths or resolve_runtime_paths()
        self.manifest_path = Path(manifest_path) if manifest_path is not None else paths.trustmark_manifest
        self.cache_directory = (
            Path(cache_directory)
            if cache_directory is not None
            else paths.user_data_root / "models/trustmark" / TRUSTMARK_VERSION / MODEL_VARIANT
        )
        self._opener = opener
        self._version_getter = version_getter
        self.timeout_seconds = timeout_seconds
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ModelPreparationError("MODEL_PREPARATION_FAILED") from exc
        if data.get("schemaVersion") != "1.0" or data.get("trustmarkVersion") != TRUSTMARK_VERSION:
            raise ModelPreparationError("MODEL_PREPARATION_FAILED")
        if data.get("variant") != MODEL_VARIANT:
            raise ModelPreparationError("MODEL_PREPARATION_FAILED")
        official_base = "https://cc-assets.netlify.app/watermarking/trustmark-models/"
        if data.get("officialBaseUrl") != official_base:
            raise ModelPreparationError("MODEL_PREPARATION_FAILED")
        try:
            self.resources = tuple(ModelResource(**item) for item in data.get("resources", []))
        except (TypeError, ValueError) as exc:
            raise ModelPreparationError("MODEL_PREPARATION_FAILED") from exc
        if {item.filename for item in self.resources} != FORBIDDEN_MODEL_BASENAMES:
            raise ModelPreparationError("MODEL_PREPARATION_FAILED")
        if any(
            item.size <= 0
            or len(item.md5) != 32
            or len(item.sha256) != 64
            or item.url != official_base + item.filename
            for item in self.resources
        ):
            raise ModelPreparationError("MODEL_PREPARATION_FAILED")
        self._by_name = {item.filename: item for item in self.resources}

    def _check_version(self) -> None:
        try:
            version = self._version_getter("trustmark")
        except Exception as exc:
            raise ModelPreparationError("MODEL_PREPARATION_FAILED") from exc
        if version != TRUSTMARK_VERSION:
            raise ModelPreparationError("MODEL_PREPARATION_FAILED")

    def _valid(self, path: Path, resource: ModelResource) -> bool:
        try:
            size, md5, sha256 = _hashes(path)
        except (OSError, ValueError):
            return False
        return (
            size == resource.size
            and md5.lower() == resource.md5.lower()
            and sha256.lower() == resource.sha256.lower()
        )

    def require_valid(self, path: Path) -> None:
        resource = self._by_name.get(path.name)
        if resource is None or path.resolve() != (self.cache_directory / path.name).resolve():
            raise ModelPreparationError("MODEL_INTEGRITY_FAILED")
        if not self._valid(path, resource):
            raise ModelPreparationError("MODEL_INTEGRITY_FAILED")

    def _download(self, resource: ModelResource, destination: Path) -> None:
        request = urllib.request.Request(
            resource.url,
            headers={"User-Agent": "Shirushi/0.1 model-acquisition"},
            method="GET",
        )
        try:
            response_context = self._opener(request, timeout=self.timeout_seconds)
            with response_context as response, destination.open("xb") as output:
                resolved_url = response.geturl() if hasattr(response, "geturl") else resource.url
                parsed = urlparse(resolved_url)
                if parsed.scheme != "https" or parsed.hostname not in {
                    "cc-assets.netlify.app",
                    "cai-watermark.adobe.net",
                }:
                    raise ModelPreparationError("MODEL_NETWORK_UNAVAILABLE")
                for block in iter(lambda: response.read(1024 * 1024), b""):
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
        except ModelPreparationError:
            raise
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ssl.SSLError) as exc:
            raise ModelPreparationError("MODEL_NETWORK_UNAVAILABLE") from exc
        except OSError as exc:
            raise _mapped_os_error(exc) from exc

    def _prepare_one(self, resource: ModelResource) -> Path:
        final = self.cache_directory / resource.filename
        if self._valid(final, resource):
            return final
        try:
            if final.exists():
                final.unlink()
        except OSError as exc:
            raise _mapped_os_error(exc) from exc
        temporary = final.with_name(f".{final.name}.{uuid.uuid4().hex}.partial")
        try:
            self._download(resource, temporary)
            if not self._valid(temporary, resource):
                raise ModelPreparationError("MODEL_INTEGRITY_FAILED")
            try:
                os.replace(temporary, final)
            except OSError as exc:
                raise _mapped_os_error(exc) from exc
            if not self._valid(final, resource):
                try:
                    final.unlink(missing_ok=True)
                except OSError:
                    pass
                raise ModelPreparationError("MODEL_INTEGRITY_FAILED")
            return final
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def ensure_models(self) -> dict[str, Path]:
        self._check_version()
        with _ACQUISITION_LOCK:
            paths = {item.filename: self.cache_directory / item.filename for item in self.resources}
            if all(self._valid(paths[item.filename], item) for item in self.resources):
                return paths
            _PREPARATION_ACTIVE.set()
            try:
                try:
                    self.cache_directory.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    raise _mapped_os_error(exc) from exc
                return {item.filename: self._prepare_one(item) for item in self.resources}
            finally:
                _PREPARATION_ACTIVE.clear()


class CachedTrustMark(TrustMark):
    """TrustMark 0.9.0 adapter that resolves its models from the managed cache."""

    def __init__(self, *, manager: TrustMarkModelManager, model_paths: dict[str, Path], **kwargs: Any):
        self._model_manager = manager
        self._model_paths = model_paths
        super().__init__(**kwargs)

    def check_and_download(self, filename: str) -> None:
        self._model_manager.require_valid(Path(filename))

    def load_model(
        self,
        config_path: str,
        weight_path: str,
        device: str,
        secret_len: int,
        part: str = "all",
    ) -> Any:
        try:
            config = self._model_paths[Path(config_path).name]
            weights = self._model_paths[Path(weight_path).name]
        except KeyError as exc:
            raise ModelPreparationError("MODEL_LOAD_FAILED") from exc
        loaded = super().load_model(str(config), str(weights), device, secret_len, part=part)
        if loaded is None:
            raise ModelPreparationError("MODEL_LOAD_FAILED")
        return loaded


class TrustMarkFactory:
    def __init__(
        self,
        manager: TrustMarkModelManager | None = None,
        trustmark_class: type[CachedTrustMark] = CachedTrustMark,
    ) -> None:
        self.manager = manager or get_default_model_manager()
        self.trustmark_class = trustmark_class

    def create(self) -> TrustMark:
        paths = self.manager.ensure_models()
        try:
            candidate = self.trustmark_class(
                manager=self.manager,
                model_paths=paths,
                verbose=False,
                model_type=MODEL_VARIANT,
                encoding_type=TrustMark.Encoding.BCH_4,
                loadRemover=False,
                loadBBoxDetector=False,
            )
        except ModelPreparationError:
            raise
        except Exception as exc:
            raise ModelPreparationError("MODEL_LOAD_FAILED") from exc
        if getattr(candidate, "encoder", None) is None or getattr(candidate, "decoder", None) is None:
            raise ModelPreparationError("MODEL_LOAD_FAILED")
        return candidate


_DEFAULT_MANAGER: TrustMarkModelManager | None = None
_DEFAULT_MANAGER_LOCK = threading.Lock()


def get_default_model_manager() -> TrustMarkModelManager:
    global _DEFAULT_MANAGER
    if _DEFAULT_MANAGER is None:
        with _DEFAULT_MANAGER_LOCK:
            if _DEFAULT_MANAGER is None:
                _DEFAULT_MANAGER = TrustMarkModelManager()
    return _DEFAULT_MANAGER


def create_trustmark() -> TrustMark:
    return TrustMarkFactory().create()


def find_bundled_model_violations(payload_root: Path) -> list[str]:
    """Return model files found in an intended release payload."""
    violations: list[str] = []
    for path in Path(payload_root).rglob("*"):
        if not path.is_file():
            continue
        if path.name in FORBIDDEN_MODEL_BASENAMES:
            violations.append(str(path))
            continue
        try:
            if _hashes(path)[2].lower() in FORBIDDEN_MODEL_SHA256:
                violations.append(str(path))
        except OSError:
            continue
    return violations
