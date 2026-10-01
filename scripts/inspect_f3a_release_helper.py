"""CI-only release-helper evidence. Standard library only; never a packager.

Raw build logs and paths stay in the ephemeral hosted runner. Published evidence
contains basenames, hashes, bounded PE facts and explicit observation limits.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import struct
import subprocess
import sys


TARGET = "x86_64-pc-windows-msvc"
BASELINE = "c5acb3706ea0e38e2dd4d0ccbde93f7fb80ef2ac"
DEBUG_SIZE = 24_624_640
VC_RUNTIME = re.compile(r"(?:vcruntime\d+(?:_\d+)?|msvcp\d+(?:_\w+)?|msvcr\d+|concrt\d+)\.dll", re.I)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def emit(kind: str, value: object) -> None:
    print(kind + ": " + json.dumps(value, sort_keys=True, separators=(",", ":")), flush=True)


def dll_class(name: str, system_names: set[str]) -> str:
    name = name.lower()
    if VC_RUNTIME.fullmatch(name):
        return "VC_RUNTIME"
    if name in system_names or name.startswith(("api-ms-win-", "ext-ms-win-")):
        return "WINDOWS_SYSTEM"
    return "UNKNOWN"


class PE:
    """Bounded read-only PE32+ parser for the built x64 candidate, not a loader."""

    def __init__(self, raw: bytes):
        self.raw = raw
        if not 0 < len(raw) <= 256 * 1024 * 1024 or raw[:2] != b"MZ":
            raise ValueError("PE_DOS_HEADER")
        pe = self.u32(0x3C)
        if self.read(pe, 4) != b"PE\0\0":
            raise ValueError("PE_SIGNATURE")
        self.machine, count, self.timestamp = self.unpack("<HHI", pe + 4)
        optional_size = self.u16(pe + 20)
        self.characteristics = self.u16(pe + 22)
        opt = pe + 24
        if self.u16(opt) != 0x20B or optional_size < 112 or not 0 < count <= 96:
            raise ValueError("PE_OPTIONAL_HEADER")
        self.image_base = self.unpack("<Q", opt + 24)[0]
        self.subsystem = self.u16(opt + 68)
        self.dll_characteristics = self.u16(opt + 70)
        self.headers_size = self.u32(opt + 60)
        dirs = min(self.u32(opt + 108), 16)
        if 112 + dirs * 8 > optional_size:
            raise ValueError("PE_DIRECTORY_BOUNDS")
        self.directories = [self.unpack("<II", opt + 112 + i * 8) for i in range(dirs)]
        self.sections = []
        for i in range(count):
            pos = opt + optional_size + i * 40
            name = self.read(pos, 8).split(b"\0")[0].decode("ascii", "strict")
            virtual_size, rva, size, offset = self.unpack("<IIII", pos + 8)
            self.read(offset, size)
            self.sections.append(dict(name=name, rva=rva, virtualSize=virtual_size,
                                      rawSize=size, rawOffset=offset,
                                      characteristics=self.u32(pos + 36)))

    def read(self, pos: int, size: int) -> bytes:
        if pos < 0 or size < 0 or pos + size > len(self.raw):
            raise ValueError("PE_BOUNDS")
        return self.raw[pos:pos + size]

    def unpack(self, fmt: str, pos: int) -> tuple:
        return struct.unpack(fmt, self.read(pos, struct.calcsize(fmt)))

    def u16(self, pos: int) -> int:
        return self.unpack("<H", pos)[0]

    def u32(self, pos: int) -> int:
        return self.unpack("<I", pos)[0]

    def offset(self, rva: int, size: int = 1) -> int:
        if rva < self.headers_size and rva + size <= self.headers_size:
            self.read(rva, size)
            return rva
        for s in self.sections:
            delta = rva - s["rva"]
            if 0 <= delta and delta + size <= s["rawSize"]:
                pos = s["rawOffset"] + delta
                self.read(pos, size)
                return pos
        raise ValueError("PE_RVA_BOUNDS")

    def directory(self, n: int) -> tuple[int, int]:
        return self.directories[n] if n < len(self.directories) else (0, 0)

    def cstring(self, rva: int, limit: int = 512) -> str:
        result = bytearray()
        for i in range(limit):
            b = self.read(self.offset(rva + i), 1)[0]
            if b == 0:
                return result.decode("ascii", "strict")
            result.append(b)
        raise ValueError("PE_STRING_BOUND")

    def imports(self, delay: bool = False) -> list[dict]:
        rva, size = self.directory(13 if delay else 1)
        if not rva and not size:
            return []
        stride = 32 if delay else 20
        if not rva or size < stride:
            raise ValueError("PE_IMPORT_DIRECTORY")
        rows = []
        for i in range(min(size // stride, 512)):
            pos = self.offset(rva + i * stride, stride)
            fields = self.unpack("<" + "I" * (stride // 4), pos)
            if not any(fields):
                return rows
            if delay:
                attrs, name_rva, _, iat, table, _, _, _ = fields
                if attrs not in (0, 1):
                    raise ValueError("PE_DELAY_ATTRIBUTES")
                adjust = 0 if attrs & 1 else self.image_base
                name_rva -= adjust
                table = (table or iat) - adjust
            else:
                table, _, _, name_rva, iat = fields
                table = table or iat
            name = self.cstring(name_rva)
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}\.dll", name, re.I):
                raise ValueError("PE_IMPORT_NAME")
            symbols = []
            for j in range(4096):
                entry = self.unpack("<Q", self.offset(table + j * 8, 8))[0]
                if entry == 0:
                    break
                if entry & (1 << 63):
                    symbols.append("ordinal:" + str(entry & 0xFFFF))
                else:
                    symbols.append(self.cstring(entry + 2))
            else:
                raise ValueError("PE_IMPORT_SYMBOL_BOUND")
            rows.append(dict(dll=name.lower(), symbols=symbols))
        raise ValueError("PE_IMPORT_TERMINATOR")

    def debug(self) -> list[dict]:
        rva, size = self.directory(6)
        if not rva and not size:
            return []
        if not rva or size % 28 or size // 28 > 64:
            raise ValueError("PE_DEBUG_DIRECTORY")
        entries = []
        for i in range(size // 28):
            pos = self.offset(rva + i * 28, 28)
            _, timestamp, major, minor, kind, length, _, pointer = self.unpack("<IIHHIIII", pos)
            data = self.read(pointer, length)
            row = dict(type=kind, size=length, timestamp=timestamp, major=major, minor=minor)
            if kind == 2:
                row["codeViewSignature"] = data[:4].decode("ascii", "replace")
                path_offset = 24 if data[:4] == b"RSDS" else 16 if data[:4] == b"NB10" else None
                row["pdbReferencePresent"] = path_offset is not None and length > path_offset
                if row["pdbReferencePresent"]:
                    path = data[path_offset:].split(b"\0", 1)[0].decode("utf-8", "replace")
                    row["pdbReferenceIsAbsolute"] = PureWindowsPath(path).is_absolute()
                    # Only fixed product/toolchain basename is public; paths stay local.
                    base = PureWindowsPath(path).name
                    row["pdbBasename"] = base if re.fullmatch(r"shirushi[_-]inspection[_-]helper\.pdb", base) else "REDACTED"
            entries.append(row)
        return entries

    def resources(self) -> dict:
        rva, size = self.directory(2)
        if not rva and not size:
            return dict(present=False, types=[], leaves=[])
        base = self.offset(rva, size)
        leaves, types, visited = [], [], set()

        def walk(relative: int, depth: int, ids: list):
            if depth > 4 or relative in visited or len(visited) >= 256 or relative + 16 > size:
                raise ValueError("PE_RESOURCE_BOUND")
            visited.add(relative)
            pos = base + relative
            named, numbered = self.unpack("<HH", pos + 12)
            count = named + numbered
            if count > 256 or relative + 16 + 8 * count > size:
                raise ValueError("PE_RESOURCE_BOUND")
            for i in range(count):
                name, child = self.unpack("<II", pos + 16 + i * 8)
                label = "NAMED_REDACTED" if name & 0x80000000 else name
                if depth == 0:
                    types.append(label)
                chain = ids + [label]
                if child & 0x80000000:
                    walk(child & 0x7FFFFFFF, depth + 1, chain)
                else:
                    if child + 16 > size or len(leaves) >= 512:
                        raise ValueError("PE_RESOURCE_BOUND")
                    data_rva, length, codepage, _ = self.unpack("<IIII", base + child)
                    data = self.read(self.offset(data_rva, length), length)
                    leaves.append(dict(ids=chain, size=length, codepage=codepage, sha256=sha(data)))

        walk(0, 0, [])
        return dict(present=True, types=types, leaves=leaves)

    def evidence(self, system_names: set[str], workspace: Path) -> dict:
        imports, delay = self.imports(), self.imports(True)
        for row in imports + delay:
            row["classification"] = dll_class(row["dll"], system_names)
        debug = self.debug()
        paths = re.findall(rb"[A-Za-z]:[\\/][\x20-\x7e]{3,260}", self.raw)
        markers = [str(workspace), str(workspace).replace("\\", "/")]
        return dict(machine=hex(self.machine), subsystem=self.subsystem,
                    timestamp=self.timestamp, characteristics=hex(self.characteristics),
                    dllCharacteristics=hex(self.dll_characteristics), sections=self.sections,
                    debugDirectoryPresent=bool(debug), debugEntries=debug,
                    resources=self.resources(), imports=imports, delayImports=delay,
                    pathMetadata=dict(asciiPathLikeOccurrences=len(paths),
                        workspaceMarkerPresent=any(m.encode() in self.raw or m.encode("utf-16le") in self.raw for m in markers),
                        cargoRegistryMarkerPresent=b"registry\\src" in self.raw or b"registry/src" in self.raw,
                        limitation="bounded string heuristics, not exhaustive privacy proof"))


def run_checked(args: list[str], **kwargs) -> str:
    p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", **kwargs)
    if p.returncode:
        raise ValueError("COMMAND_FAILED:" + Path(args[0]).name)
    return p.stdout.strip()


def preflight(root: Path) -> None:
    blocked = {
        "ICU4X_DATA_DIR", "RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS", "RUSTC", "RUSTDOC",
        "RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER", "RUSTC_BOOTSTRAP", "RUSTC_LOG",
        "RUSTC_STAGE", "ENSURE_NO_PANIC", "CL", "_CL_", "LINK", "_LINK_",
        "CARGO_BUILD_RUSTFLAGS", "CARGO_BUILD_RUSTC", "CARGO_BUILD_RUSTC_WRAPPER",
        "CARGO_BUILD_RUSTC_WORKSPACE_WRAPPER", "CARGO_BUILD_TARGET",
        "CARGO_INCREMENTAL", "CARGO_BUILD_INCREMENTAL",
    }
    controlled = []
    for key in os.environ:
        if key in blocked or key.startswith("CARGO_PROFILE_") or (
            key.startswith("CARGO_TARGET_") and key.endswith(("_LINKER", "_RUSTFLAGS"))
        ) or key.startswith("CARGO_CFG_CURVE25519_DALEK_"):
            controlled.append(key)
    emit("BUILD_ENVIRONMENT", dict(ICU4X_DATA_DIR="UNSET" if "ICU4X_DATA_DIR" not in os.environ else "PRESENT",
         unexpectedOverrideNames=sorted(controlled), profile="Cargo default release",
         addedCodegenFlags=[], diagnosticOnlyFlag="RUSTC_LOG=rustc_codegen_ssa::back::link=info"))
    if controlled:
        raise ValueError("UNCONTROLLED_BUILD_ENVIRONMENT")
    cargo_home = Path(os.environ.get("CARGO_HOME", str(Path.home() / ".cargo")))
    directories = [cargo_home, root / "tools/f3a-rust-sdk-parity/.cargo"] + [p / ".cargo" for p in [root, *root.parents]]
    if any((p / n).is_file() for p in directories for n in ("config", "config.toml")):
        raise ValueError("CARGO_CONFIGURATION_REQUIRES_REVIEW")
    # Helper source/config bytes, not only the lock, must equal accepted baseline.
    changed = run_checked(["git", "diff", "--name-only", BASELINE, "--", "tools/f3a-rust-sdk-parity/"], cwd=root)
    if changed:
        raise ValueError("HELPER_SOURCE_BASELINE_CHANGED")


def build(root: Path, output: Path) -> None:
    preflight(root)
    rust = run_checked(["rustc", "-vV"], cwd=root)
    cargo = run_checked(["cargo", "-vV"], cwd=root)
    if not rust.startswith("rustc 1.97.1 ") or not cargo.startswith("cargo 1.97.1 "):
        raise ValueError("TOOLCHAIN_MISMATCH")
    env = os.environ.copy()
    env["RUSTC_LOG"] = "rustc_codegen_ssa::back::link=info"
    args = ["cargo", "build", "--release", "--locked", "--offline", "--target", TARGET,
            "--manifest-path", "tools/f3a-rust-sdk-parity/Cargo.toml", "--bin", "shirushi-inspection-helper"]
    p = subprocess.run(args, cwd=root, env=env, capture_output=True, timeout=1800)
    # Do not echo raw compiler/linker commands: they contain CI directory paths.
    log = (p.stdout + p.stderr).decode("utf-8", "replace")
    if p.returncode:
        emit("BUILD_FAILURE", dict(exitCode=p.returncode, logBytes=len(p.stdout) + len(p.stderr), rawLogPublished=False))
        raise ValueError("RELEASE_BUILD_FAILED")
    link_lines = [line for line in log.splitlines() if "shirushi_inspection_helper" in line and "link.exe" in line and "back::link" in line]
    candidates = set()
    for line in link_lines:
        for match in re.finditer(r'"([^"\r\n]*[\\/]link\.exe)"', line, re.I):
            candidates.add(match.group(1).replace("\\\\", "\\"))
    if len(candidates) != 1:
        emit("LINKER_OBSERVATION", dict(matchingLogLines=len(link_lines), candidateCount=len(candidates)))
        raise ValueError("ACTUAL_LINKER_IDENTITY_UNRESOLVED")
    linker = Path(candidates.pop())
    if not linker.is_file():
        raise ValueError("OBSERVED_LINKER_FILE_MISSING")
    # Version query of the exact observed tool, not a PATH-selected second linker.
    version = subprocess.run([str(linker), "/?"], capture_output=True, timeout=15).stdout.decode("utf-8", "replace")
    version_line = next((s.strip() for s in version.splitlines() if "Linker Version" in s), None)
    if not version_line:
        raise ValueError("LINKER_VERSION_UNRESOLVED")
    msvc = re.search(r"[\\/]MSVC[\\/]([^\\/]+)", str(linker), re.I)
    sdk_versions = sorted(set(re.findall(r"Windows Kits(?:\\\\|[\\/])10(?:\\\\|[\\/])lib(?:\\\\|[\\/])([0-9.]+)", "\n".join(link_lines), re.I)))
    record = dict(sourceCommit=run_checked(["git", "rev-parse", "HEAD"], cwd=root),
                  helperSourceBaseline=BASELINE, rustc=rust.splitlines(), cargo=cargo.splitlines(),
                  host=next((s.split(": ", 1)[1] for s in rust.splitlines() if s.startswith("host: ")), "UNPROVEN"),
                  target=TARGET, profile="default release", command=args,
                  linker=dict(basename=linker.name, sha256=sha(linker.read_bytes()),
                              version=version_line, msvcVersion=msvc.group(1) if msvc else "UNPROVEN",
                              identitySource="rustc final-helper link invocation"),
                  windowsSdkVersionsFromLinkInvocation=sdk_versions or ["UNPROVEN"],
                  MSRV="NOT_PROVEN", licenseNotice="DISTRIBUTION_REVIEW_REQUIRED")
    output.write_text(json.dumps(record, indent=2), encoding="utf-8")
    emit("RELEASE_BUILD_EVIDENCE", record)


def inspect_binary(helper: Path, output: Path, root: Path) -> None:
    raw = helper.read_bytes()
    pe = PE(raw)
    if pe.machine != 0x8664:
        raise ValueError("PE_MACHINE_MISMATCH")
    system = Path(os.environ["SystemRoot"]) / "System32"
    names = {p.name.lower() for p in system.glob("*.dll")}
    record = dict(filename=helper.name, size=len(raw), sha256=sha(raw),
                  debugBaselineSize=DEBUG_SIZE, deltaBytes=len(raw) - DEBUG_SIZE,
                  deltaPercent=(len(raw) - DEBUG_SIZE) * 100 / DEBUG_SIZE,
                  pe=pe.evidence(names, root),
                  importClassificationScope="host System32 names/API-set contracts; not universal OS availability")
    output.write_text(json.dumps(record, indent=2), encoding="utf-8")
    emit("RELEASE_BINARY_EVIDENCE", record)


def compare(binary: Path, test_log: Path) -> None:
    record = json.loads(binary.read_text(encoding="utf-8"))
    lines = [s.split("RELEASE_HELPER_MODULES: ", 1)[1] for s in test_log.read_text(encoding="utf-8").splitlines() if "RELEASE_HELPER_MODULES: " in s]
    if len(lines) != 1:
        raise ValueError("RUNTIME_MODULE_EVIDENCE_COUNT")
    observed = json.loads(lines[0])
    if (observed.get("sampled_post_resume") is not True
            or observed.get("successful_samples", 0) < 1
            or observed.get("handle_closed") is not True
            or not any(m.get("category") == "OWN_HELPER" and m.get("basename") == "shirushi-inspection-helper.exe"
                       for m in observed.get("modules", []))):
        raise ValueError("INSUFFICIENT_RUNTIME_MODULE_OBSERVATION")
    static = {d["dll"] for d in record["pe"]["imports"] + record["pe"]["delayImports"]}
    runtime = {d["basename"].lower() for d in observed["modules"]}
    emit("STATIC_RUNTIME_COMPARISON", dict(staticNotObserved=sorted(static - runtime),
        observedNotStatic=sorted(runtime - static), runtime=observed,
        limitation="sampling window only; API sets can resolve to differently named modules; not exhaustive dynamic-load proof"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="mode", required=True)
    b = commands.add_parser("build")
    b.add_argument("--root", type=Path, required=True)
    b.add_argument("--output", type=Path, required=True)
    p = commands.add_parser("inspect")
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--helper", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    c = commands.add_parser("compare")
    c.add_argument("--binary-evidence", type=Path, required=True)
    c.add_argument("--test-log", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "build":
        build(args.root, args.output)
    elif args.mode == "inspect":
        inspect_binary(args.helper, args.output, args.root)
    else:
        compare(args.binary_evidence, args.test_log)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.TimeoutExpired, UnicodeError, struct.error) as error:
        # Never print traceback/OS exception strings containing private paths.
        code = str(error) if isinstance(error, ValueError) and re.fullmatch(r"[A-Z0-9_:.-]+", str(error)) else type(error).__name__
        emit("RELEASE_EVIDENCE_FAILURE", dict(code=code))
        sys.exit(1)
