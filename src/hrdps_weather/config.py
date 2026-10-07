"""User configuration and per-OS directories (Linux, macOS, Windows)."""
import os
import sys
import tomllib
from pathlib import Path

APP = "hrdps-weather"

DEFAULTS = {
    "latitude": 46.8139,          # Québec (ville) — change it in config.toml
    "longitude": -71.2080,
    "location": "Québec",
    "timezone": "America/Toronto",
}

# HRDPS continental domain (approx.): anything outside has no data.
LAT_RANGE, LON_RANGE = (27.3, 70.6), (-152.7, -40.7)

TEMPLATE = """\
# hrdps-weather configuration
latitude  = 46.8139
longitude = -71.2080
location  = "Québec"            # label shown in the interface
timezone  = "America/Toronto"   # IANA name, used to show local hours
"""


def config_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / APP


def cache_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        return Path(base) / APP / "Cache"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / APP
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / APP


def config_path() -> Path:
    return config_dir() / "config.toml"


def write_template() -> Path:
    p = config_path()
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(TEMPLATE, encoding="utf-8")
    return p


def load() -> dict:
    cfg = dict(DEFAULTS)
    p = config_path()
    if p.exists():
        cfg.update(tomllib.loads(p.read_text(encoding="utf-8")))
    env = {"HRDPS_LAT": "latitude", "HRDPS_LON": "longitude", "HRDPS_LOCATION": "location", "HRDPS_TZ": "timezone"}
    for var, key in env.items():
        if os.environ.get(var):
            cfg[key] = os.environ[var]
    cfg["latitude"], cfg["longitude"] = float(cfg["latitude"]), float(cfg["longitude"])
    if not (LAT_RANGE[0] <= cfg["latitude"] <= LAT_RANGE[1] and LON_RANGE[0] <= cfg["longitude"] <= LON_RANGE[1]):
        raise ValueError(
            f"({cfg['latitude']}, {cfg['longitude']}) est hors du domaine HRDPS "
            f"(lat {LAT_RANGE[0]}–{LAT_RANGE[1]}, lon {LON_RANGE[0]}–{LON_RANGE[1]}). Éditez {p}")
    return cfg
