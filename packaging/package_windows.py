"""Create both Windows x64 release assets from one PyInstaller build."""

import argparse
import hashlib
import shutil
import struct
import subprocess
import sys
import zipfile
from importlib.metadata import distribution
from pathlib import Path

import PyInstaller


ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging"
VERSION = "1.1.0"


def add_notices(payload: Path) -> None:
    notices = payload / "ThirdParty"
    shutil.copytree(PACKAGING / "third_party", notices)
    shutil.copyfile(Path(sys.base_prefix) / "LICENSE.txt", notices / "Python-LICENSE.txt")
    for name in ("Pillow", "PySide6-Essentials", "shiboken6"):
        package = distribution(name)
        for entry in package.files or ():
            metadata_dir = next((part for part in entry.parts if part.endswith(".dist-info")), None)
            if metadata_dir and entry.name.lower().startswith("license"):
                target = notices / f"{metadata_dir}-{entry.name}"
                shutil.copyfile(package.locate_file(entry), target)


def make_uninstall_manifest(payload: Path, manifest: Path) -> None:
    paths = sorted(path.relative_to(payload) for path in payload.rglob("*"))
    files = [path for path in paths if (payload / path).is_file()]
    directories = sorted(set(paths) - set(files), key=lambda path: (-len(path.parts), path))
    commands = [f'  Delete "$INSTDIR\\{path}"' for path in files]
    commands.extend(f'  RMDir "$INSTDIR\\{path}"' for path in directories)
    manifest.write_text("\n".join(commands) + "\n", encoding="utf-8-sig")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--makensis", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="A new directory for this release")
    args = parser.parse_args()
    if sys.platform != "win32" or struct.calcsize("P") != 8:
        parser.error("Windows x64 Python is required")
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        if not (ROOT / "bin" / name).is_file():
            parser.error(f"Required file is missing: bin/{name}")
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip():
        parser.error("Commit the release source before building")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    makensis = args.makensis.resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    dist = output / "dist"
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--distpath", str(dist), "--workpath", str(output / "build"), "FrameFlow.spec"],
                   cwd=ROOT, check=True)
    payload = dist / "FrameFlow"
    add_notices(payload)
    portable = output / "FrameFlow-Windows-x64.zip"
    with zipfile.ZipFile(portable, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(payload.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(dist))
    manifest = output / "uninstall-files.nsh"
    make_uninstall_manifest(payload, manifest)
    setup = output / f"FrameFlow-Setup-v{VERSION}-Windows-x64.exe"
    subprocess.run([str(makensis), "/INPUTCHARSET", "UTF8", f"/DSETUP_OUTPUT={setup}", f"/DPAYLOAD={payload}",
                    f"/DUNINSTALL_MANIFEST={manifest}", "FrameFlow.nsi"], cwd=PACKAGING, check=True)
    nsis = subprocess.check_output([str(makensis), "/VERSION"], text=True).strip()
    records = [f"Git Commit: {commit}", f"PyInstaller: {PyInstaller.__version__}", f"NSIS: {nsis}"]
    for asset in (portable, setup):
        with asset.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        records.append(f"{asset.name} | {asset.stat().st_size} bytes | SHA-256 {digest}")
    details = "\n".join(records) + "\n"
    (output / "BUILD_INFO.txt").write_text(details, encoding="utf-8")
    print(details)


if __name__ == "__main__":
    main()
