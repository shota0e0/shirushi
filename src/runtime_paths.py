"""Resolve Shirushi read-only resources and user-writable runtime paths."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import sys
from typing import Mapping


PRODUCT_DIRECTORY_NAME = "Shirushi"
C2PATOOL_VERSION = "0.26.60"
C2PATOOL_SHA256 = "90CBCEBE30250F8E8C53416D32ED86065DC04A23BE86E4A2337F5CD1BADFA0B7"
C2PATOOL_ERROR_CODES = (
    "C2PATOOL_MISSING",
    "C2PATOOL_INTEGRITY_FAILED",
)


class RuntimeComponentError(RuntimeError):
    """A stable failure for a required packaged runtime component."""

    def __init__(self, code: str):
        if code not in C2PATOOL_ERROR_CODES:
            raise ValueError(f"unknown runtime component error code: {code}")
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RuntimePaths:
    installation_root: Path
    resource_root: Path
    user_data_root: Path
    frozen: bool

    @property
    def c2patool(self) -> Path:
        if self.frozen:
            return self.resource_root / "c2patool.exe"
        return self.resource_root / "tools/c2patool-0.26.60/c2patool/c2patool.exe"

    @property
    def trustmark_manifest(self) -> Path:
        return self.resource_root / "config/trustmark-models-v0.9.0.json"

    @property
    def verifier_settings(self) -> Path:
        return self.resource_root / "config/verifier-settings.json"

    @property
    def app_icon(self) -> Path:
        if self.frozen:
            return self.resource_root / "gui/app_icon.png"
        return self.resource_root / "assets/app_icon.png"

    @property
    def app_icon_ico(self) -> Path:
        if self.frozen:
            return self.resource_root / "gui/app_icon.ico"
        return self.resource_root / "assets/app_icon.ico"

    @property
    def creator_log(self) -> Path:
        return self.user_data_root / "logs/creator_service.log"

    @property
    def development_scripts(self) -> Path | None:
        return None if self.frozen else self.resource_root / "scripts"


def resolve_runtime_paths(
    *,
    frozen: bool | None = None,
    executable: Path | None = None,
    resource_root: Path | None = None,
    local_app_data: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> RuntimePaths:
    """Return deterministic paths without consulting the process CWD."""

    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    environment = os.environ if environ is None else environ
    if is_frozen:
        install = Path(executable or sys.executable).resolve().parent
        bundle = resource_root or getattr(sys, "_MEIPASS", None)
        if bundle is None:
            raise RuntimeError("frozen application resource root is unavailable")
        resources = Path(bundle).resolve()
    else:
        resources = (
            Path(resource_root).resolve()
            if resource_root is not None
            else Path(__file__).resolve().parent.parent
        )
        install = resources

    if local_app_data is not None:
        user_base = Path(local_app_data)
    else:
        configured = environment.get("LOCALAPPDATA")
        user_base = Path(configured) if configured else Path.home() / "AppData/Local"
    return RuntimePaths(install, resources, user_base / PRODUCT_DIRECTORY_NAME, is_frozen)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def require_c2patool(path: Path) -> Path:
    """Require the exact pinned c2patool component and return its absolute path."""

    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise RuntimeComponentError("C2PATOOL_MISSING")
    try:
        digest = sha256_file(resolved)
    except OSError as exc:
        raise RuntimeComponentError("C2PATOOL_INTEGRITY_FAILED") from exc
    if digest != C2PATOOL_SHA256:
        raise RuntimeComponentError("C2PATOOL_INTEGRITY_FAILED")
    return resolved
