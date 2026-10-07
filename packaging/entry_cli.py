"""PyInstaller entry point — console build (`hrdps-weather.exe`)."""
import sys

from hrdps_weather.cli import main

main(sys.argv[1:])
