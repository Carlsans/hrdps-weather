# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Linux single-file binary (run from the repo root):

    pip install -e ".[build]"
    pyinstaller packaging/hrdps-weather-linux.spec --clean --noconfirm

Produces dist/hrdps-weather. One file holds the CLI (waybar / status / refresh / png ...) and the
Tk window (`hrdps-weather popup`). The GTK4 popup is used by source/pip installs when PyGObject is
present; the binary deliberately bundles Tk instead — GTK4 + typelibs do not bundle reliably.

Linux is onefile on purpose (it unpacks to /tmp at start; there is no antivirus false-positive
problem on Linux, unlike Windows — see hrdps-weather.spec). It must be built on an OLD glibc
(Ubuntu 22.04, see Dockerfile.build / the CI workflow): a binary only runs on systems at least as
new as the one that built it.
"""
import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src"

datas = [(str(SRC / "hrdps_weather" / "data"), "hrdps_weather/data"), *collect_data_files("tzdata")]

a = Analysis(
    [str(ROOT / "packaging" / "entry_cli.py")],
    pathex=[str(SRC)], binaries=[], datas=datas,
    hiddenimports=["tkinter", "PIL._tkinter_finder"],
    hookspath=[], hooksconfig={}, runtime_hooks=[], noarchive=False,
    excludes=["gi", "pystray", "scipy", "matplotlib", "pandas", "IPython", "pytest"],
)
# Use the system's fontconfig, not the 2.13 copy from the Ubuntu 22.04 build image: a newer distribution's
# /etc/fonts configuration uses syntax the old library rejects ("invalid attribute 'xsi:nil'").
a.binaries = [b for b in a.binaries if not b[0].startswith("libfontconfig")]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
          name="hrdps-weather", console=True, upx=False)
