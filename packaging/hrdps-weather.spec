# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Windows build (run from the repo root):

    pip install -e ".[build]"
    pyinstaller packaging/hrdps-weather.spec --clean --noconfirm

Produces dist/hrdps-weather/ (onedir) holding two executables that share one runtime:
  hrdps-weather-tray.exe  windowed (no console): the tray app
  hrdps-weather.exe       console: `hrdps-weather selftest | png | refresh | config`

Why onedir and not onefile: a onefile executable unpacks itself into %TEMP% and runs from
there, which is exactly what a malware dropper does, and the stock PyInstaller bootloader
shows up inside real malware so its bytes match antivirus signatures. onedir removes the
self-extraction. UPX compression is also left off (packed binaries are a classic heuristic
trigger), and the executables carry real version metadata and an icon.
The binaries are not code-signed (a certificate is a yearly expense) — see the README.
"""
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src"
VERSION = re.search(r'__version__ = "([^"]+)"', (SRC / "hrdps_weather" / "__init__.py").read_text(encoding="utf-8")).group(1)
ICON = str(ROOT / "packaging" / "icon.ico")


def version_file():
    """Windows 'Details' tab metadata (publisher, product, version)."""
    if sys.platform != "win32":
        return None
    from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct,
                                                     StringTable, VarFileInfo, VarStruct, VSVersionInfo)
    v = tuple(int(x) for x in (VERSION.split(".") + ["0", "0", "0"])[:3]) + (0,)
    info = VSVersionInfo(
        ffi=FixedFileInfo(filevers=v, prodvers=v, mask=0x3F, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0),
        kids=[StringFileInfo([StringTable("040C04B0", [
            StringStruct("CompanyName", "Carl Sansfaçon"),
            StringStruct("FileDescription", "HRDPS weather (Environment Canada) tray app"),
            StringStruct("FileVersion", VERSION),
            StringStruct("InternalName", "hrdps-weather"),
            StringStruct("LegalCopyright", "MIT License"),
            StringStruct("OriginalFilename", "hrdps-weather.exe"),
            StringStruct("ProductName", "hrdps-weather"),
            StringStruct("ProductVersion", VERSION),
        ])]), VarFileInfo([VarStruct("Translation", [0x040C, 1200])])])
    out = ROOT / "build" / "version_info.txt"
    out.parent.mkdir(exist_ok=True)
    out.write_text(str(info), encoding="utf-8")
    return str(out)


VERSION_FILE = version_file()

# Read at runtime through paths / importlib rather than imported, so static analysis cannot see them.
datas = [(str(SRC / "hrdps_weather" / "data"), "hrdps_weather/data"), *collect_data_files("tzdata")]
hidden = ["tkinter", "PIL._tkinter_finder", *collect_submodules("pystray")]

common = dict(pathex=[str(SRC)], binaries=[], datas=datas, hiddenimports=hidden,
              hookspath=[], hooksconfig={}, runtime_hooks=[], noarchive=False,
              excludes=["scipy", "matplotlib", "pandas", "IPython", "pytest"])

cli = Analysis([str(ROOT / "packaging" / "entry_cli.py")], **common)
gui = Analysis([str(ROOT / "packaging" / "entry_gui.py")], **common)
cli_pyz, gui_pyz = PYZ(cli.pure), PYZ(gui.pure)

cli_exe = EXE(cli_pyz, cli.scripts, [], exclude_binaries=True, name="hrdps-weather",
              console=True, icon=ICON, version=VERSION_FILE, upx=False)
gui_exe = EXE(gui_pyz, gui.scripts, [], exclude_binaries=True, name="hrdps-weather-tray",
              console=False, icon=ICON, version=VERSION_FILE, upx=False)

COLLECT(cli_exe, cli.binaries, cli.zipfiles, cli.datas,
        gui_exe, gui.binaries, gui.zipfiles, gui.datas,
        strip=False, upx=False, name="hrdps-weather")
