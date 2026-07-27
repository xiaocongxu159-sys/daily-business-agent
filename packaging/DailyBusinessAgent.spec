# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent

hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
]
for package in (
    "lingxingapi",
    "aiohttp",
    "aiohttp_socks",
    "python_socks",
    "Crypto",
    "asyncssh",
    "cytimes",
    "orjson",
):
    try:
        hiddenimports.extend(collect_submodules(package))
    except Exception:
        pass

hiddenimports = sorted(set(hiddenimports))

datas = [
    (str(ROOT / "VERSION"), "."),
    (str(ROOT / "agent" / "static" / "index.html"), "agent/static"),
    (str(ROOT / "agent" / "static" / "lingxing.html"), "agent/static"),
    (str(ROOT / "config" / "api_config.json"), "config"),
    (str(ROOT / "config" / "field_aliases.json"), "config"),
    (str(ROOT / "LICENSE"), "."),
    (str(ROOT / "NOTICE"), "."),
]
try:
    datas.extend(collect_data_files("certifi"))
except Exception:
    pass


a = Analysis(
    [str(ROOT / "packaging" / "agent_launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "IPython", "pytest"],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DailyBusinessAgent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="x86_64",
    codesign_identity=None,
    entitlements_file=None,
    version=str(ROOT / "packaging" / "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="DailyBusinessAgent",
)
