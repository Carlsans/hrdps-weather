"""PyInstaller entry point — windowed build (`hrdps-weather-tray.exe`, no console).

Started without arguments it runs the tray app; the background refresh re-launches this
same executable with `refresh` (see hrdps.ensure_fresh)."""
import sys

from hrdps_weather.cli import main

main(sys.argv[1:] or ["tray"])
