from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata, get_package_paths


PROJECT_ROOT = Path(SPECPATH).resolve().parent

# Keep the payload allowlist explicit. TrustMark model resources are deliberately
# absent; the application acquires the three pinned P-variant resources at first use.
datas = [
    (str(PROJECT_ROOT / "config" / "trustmark-models-v0.9.0.json"), "config"),
    (str(PROJECT_ROOT / "config" / "verifier-settings.json"), "config"),
    (str(PROJECT_ROOT / "assets" / "app_icon.png"), "gui"),
    (str(PROJECT_ROOT / "assets" / "app_icon.ico"), "gui"),
]
datas += copy_metadata("trustmark")
datas += copy_metadata("cffi")
datas += copy_metadata("pycparser")

binaries = [
    (
        str(
            PROJECT_ROOT
            / "tools"
            / "c2patool-0.26.60"
            / "c2patool"
            / "c2patool.exe"
        ),
        ".",
    )
]

# torchvision 0.29.0's Windows CPython 3.12 wheel uses stable-ABI extension
# names. The current upstream PyInstaller hook still looks for torchvision._C,
# so list the wheel's native runtime files explicitly.
_, torchvision_package_path = get_package_paths("torchvision")
TORCHVISION_ROOT = Path(torchvision_package_path)
for filename in (
    "_C_stable.pyd",
    "image_stable.pyd",
    "jpeg8.dll",
    "libpng16.dll",
    "libsharpyuv.dll",
    "libwebp.dll",
    "zlib.dll",
):
    binaries.append((str(TORCHVISION_ROOT / filename), "torchvision"))

# These two modules are referenced by the pinned TrustMark YAML at runtime.
hiddenimports = [
    "cffi",
    "pycparser",
    "trustmark.model",
    "trustmark.unet",
]

a = Analysis(
    [str(PROJECT_ROOT / "scripts" / "creator_gui.py")],
    pathex=[str(PROJECT_ROOT / "src"), str(PROJECT_ROOT / "scripts")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Shirushi",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    contents_directory="runtime",
    icon=str(PROJECT_ROOT / "assets" / "app_icon.ico"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Shirushi",
)
