# -*- mode: python ; coding: utf-8 -*-
"""FrameFlow 的 PyInstaller 打包配置（onedir）。"""
import os
import sys
from pathlib import Path

ROOT = Path(SPECPATH)

if sys.platform == "win32":
    # DLL analysis must not pick up another application's runtime from PATH.
    windows = Path(os.environ["SystemRoot"])
    search_dirs = (windows / "System32", windows, Path(sys.executable).parent, Path(sys.base_prefix))
    os.environ["PATH"] = os.pathsep.join(map(str, search_dirs))

datas = []
for name in ("ffmpeg.exe", "ffprobe.exe"):
    candidate = ROOT / "bin" / name
    if candidate.is_file():
        datas.append((str(candidate), "bin"))

a = Analysis(
    [str(ROOT / "run.pyw")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
              "PySide6.Qt3DCore", "PySide6.QtQuick3D", "PySide6.QtCharts",
              "PySide6.QtDataVisualization", "PySide6.QtNetworkAuth"],
    noarchive=False,
)

if sys.platform == "win32":
    # QtCore uses the Windows ICU ABI. Leave these DLLs to System32.
    windows_icu = {"icu.dll", "icuin.dll", "icuuc.dll"}
    a.binaries = [entry for entry in a.binaries if Path(entry[0]).name.lower() not in windows_icu]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FrameFlow",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="FrameFlow",
)
