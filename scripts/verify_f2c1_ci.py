"""Read-only F2C.1 resource/config audit; never builds, launches or stages files.

--staged means build-staged desktop/.generated/web, NOT the Git index.
This source/config check is not Rust compilation or native runtime evidence.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys


PROJECT = Path(__file__).resolve().parents[1]
COMMANDS = ["bridge_get_capabilities", "bridge_load_personal_mark"]
SIDECAR_FILES = (
    "scripts/shirushi_bridge.py", "src/desktop_bridge.py",
    "src/personal_mark_v2_store.py", "src/personal_mark_v2.py",
    "src/personal_mark_unicode16.py", "src/personal_mark.py",
    "src/personal_mark_store.py", "src/runtime_paths.py",
    "src/data/personal_mark_unicode16.json", "src/data/Unicode-LICENSE.txt",
)
NATIVE_FILES = (
    "desktop/Cargo.toml", "desktop/Cargo.lock", "desktop/build.rs",
    "desktop/tauri.conf.json", "desktop/capabilities/main-window.json",
    "desktop/src/host.rs", "desktop/src/lib.rs", "desktop/src/main.rs",
    "desktop/src/protocol.rs", "desktop/src/asset_stage.rs",
    "desktop/src/bin/shirushi-canary-runner.rs",
    "desktop/tests/fixtures/bridge_fixture.py",
)
PAIR = re.compile(r'\(\s*"([^"\n]+)"\s*,\s*"([^"\n]+)"\s*\)\s*,?')
IMPORT = re.compile(r'\b(?:import|export)\s+(?:[^;"\']*?\s+from\s+)?["\']([^"\']+)["\']')


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def relative_name(value: str) -> str:
    path = PurePosixPath(value)
    require(bool(value) and not path.is_absolute() and ".." not in path.parts
            and "\\" not in value and ":" not in value, "unsafe resource name")
    require(str(path) == value, "non-canonical resource name")
    return value


def resource(root: Path, name: str) -> Path:
    path = root / relative_name(name)
    require(path.is_file(), f"missing resource: {name}")
    require(path.resolve().is_relative_to(root.resolve()), f"resource escape: {name}")
    for component in (path, *path.parents):
        if component == root:
            break
        require(not component.is_symlink() and not component.is_junction(),
                f"linked resource: {name}")
    return path


def asset_mapping(text: str) -> dict[str, str]:
    body = re.search(r'const ASSET_ALLOWLIST:.*?=\s*&\[(.*?)\];', text, re.S)
    require(body is not None, "asset allowlist missing")
    pairs = PAIR.findall(body[1])
    require(not PAIR.sub("", body[1]).strip(), "unrecognized asset allowlist syntax")
    require(len(pairs) == 29, "unexpected frozen asset count")
    require(len({p[0] for p in pairs}) == len(pairs)
            and len({p[1] for p in pairs}) == len(pairs), "duplicate asset mapping")
    result = {}
    for source, destination in pairs:
        for name in (source, destination):
            relative_name(name)
            require(not any(part in name.lower() for part in
                            ("dev-preview", "dev/", "fixture", "verify", "readme", ".mjs")),
                    "non-production asset")
        require(destination == ("index.html" if source == "desktop.html" else source),
                "unexpected asset rename")
        result[destination] = source
    require(result.get("index.html") == "desktop.html", "Desktop entry missing")
    return result


def audit(root: Path, *, staged: bool = False) -> dict:
    hashes = {}

    def read(name: str) -> str:
        data = resource(root, name).read_bytes()
        hashes[name] = hashlib.sha256(data).hexdigest()
        return data.decode("utf-8")

    for name in (*SIDECAR_FILES, *NATIVE_FILES):
        read(name)
    mapping = asset_mapping(read("desktop/src/asset_stage.rs"))
    sources = set(mapping.values())
    for destination, source in mapping.items():
        text = read(f"web/{source}")
        if source.endswith(".js"):
            require(not re.search(r'\bimport\s*\(', text), "dynamic import needs separate audit")
            imports = IMPORT.findall(text)
        elif source.endswith(".html"):
            imports = re.findall(r'(?:src|href)=["\']([^"\']+)["\']', text)
        else:
            imports = []
        for dependency in imports:
            require(dependency.startswith("."), "non-local frontend dependency")
            resolved = (root / "web" / source).parent.joinpath(dependency).resolve()
            require(resolved.is_relative_to((root / "web").resolve()), "frontend import escape")
            require(resolved.relative_to((root / "web").resolve()).as_posix() in sources,
                    f"frontend dependency missing from assets: {source}")

    config = json.loads(read("desktop/tauri.conf.json"))
    require(config["build"]["frontendDist"] == ".generated/web", "unexpected frontendDist")
    bundle = config["bundle"]
    require(bundle["active"] is False and not bundle.get("externalBin")
            and not bundle.get("resources"), "unapproved distribution/resource bundling")
    for icon in bundle["icon"]:
        require(icon in ("../assets/app_icon.png", "../assets/app_icon.ico"), "unexpected icon")
        name = f"assets/{Path(icon).name}"
        hashes[name] = hashlib.sha256(resource(root, name).read_bytes()).hexdigest()
    security = config["app"]["security"]
    require(security["capabilities"] == ["main-window"], "unexpected capabilities")
    require(security["freezePrototype"] is True
            and security["dangerousDisableAssetCspModification"] is False, "weakened WebView")
    require("connect-src ipc: http://ipc.localhost;" in security["csp"]
            and "'unsafe-eval'" not in security["csp"] and "*" not in security["csp"],
            "unexpected CSP")
    capability = json.loads(read("desktop/capabilities/main-window.json"))
    require(capability["local"] is True and capability["windows"] == ["main"]
            and not capability.get("remote"), "unexpected capability scope")
    require(capability["permissions"] == ["allow-bridge-get-capabilities",
                                           "allow-bridge-load-personal-mark"], "expanded permissions")
    build = read("desktop/build.rs")
    command_block = re.search(r'\.commands\(&\[(.*?)\]\)', build, re.S)
    require(command_block is not None
            and re.findall(r'"([^"]+)"', command_block[1]) == COMMANDS, "command manifest mismatch")
    host = read("desktop/src/host.rs")
    require('Path::new(".venv-py312/Scripts/python.exe")' in host
            and 'Path::new("scripts/shirushi_bridge.py")' in host
            and 'vec!["-I".into(), "-u".into(), script.to_string_lossy().into_owned()]' in host,
            "fixed development sidecar layout changed")
    entry = read("scripts/shirushi_bridge.py")
    require('_SOURCE_ROOT = _REPOSITORY_ROOT / "src"' in entry
            and 'sys.path.insert(0, str(_SOURCE_ROOT))' in entry, "entry source root changed")
    # Check module closure without importing/starting the sidecar or its store.
    for name in SIDECAR_FILES:
        if not name.endswith(".py"):
            continue
        for node in ast.walk(ast.parse(read(name))):
            modules = ([node.module] if isinstance(node, ast.ImportFrom)
                       else [item.name for item in node.names] if isinstance(node, ast.Import) else [])
            for module in modules:
                require(module is not None, "relative Python import needs separate audit")
                first = module.split(".")[0]
                if first not in sys.stdlib_module_names:
                    require(f"src/{first}.py" in SIDECAR_FILES, f"unlisted sidecar dependency: {first}")

    if staged:
        staged_root = root / "desktop/.generated/web"
        require(staged_root.is_dir() and not staged_root.is_symlink()
                and not staged_root.is_junction(), "build-staged asset directory unavailable")
        actual = {p.relative_to(staged_root).as_posix() for p in staged_root.rglob("*") if p.is_file()}
        require(actual == set(mapping), "build-staged asset set mismatch")
        for destination, source in mapping.items():
            built = resource(root, f"desktop/.generated/web/{destination}")
            require(built.read_bytes() == resource(root, f"web/{source}").read_bytes(),
                    f"build-staged bytes mismatch: {destination}")

    return {"audit": "PASS", "mode": "build-staged" if staged else "source-only",
            "webAssetCount": len(mapping), "sidecarResourceCount": len(SIDECAR_FILES),
            "sidecarPackaging": "repository-layout only; no bundled Python runtime",
            "sha256": dict(sorted(hashes.items())),
            "nativeManualVerification": "PENDING", "g2": "NOT READY"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true", help="verify build-staged assets, not Git index")
    args = parser.parse_args()
    try:
        result = audit(PROJECT, staged=args.staged)
    except (ValueError, OSError, KeyError, TypeError, SyntaxError) as error:
        print(json.dumps({"audit": "FAIL", "reason": str(error), "g2": "NOT READY"}))
        return 1
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
