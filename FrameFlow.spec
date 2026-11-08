# -*- mode: python ; coding: utf-8 -*-
"""FrameFlow 的 PyInstaller 打包配置（onedir）。"""
from pathlib import Path

ROOT = Path(SPECPATH)

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
