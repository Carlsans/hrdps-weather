"""Official Environment Canada pages for the configured place, and opening them in the browser."""
import os
import shutil
import subprocess
import sys
import webbrowser

from . import hrdps


def forecast_url(lat=None, lon=None):
    """meteo.gc.ca forecast page of the place (current conditions, forecast, radar, warnings)."""
    lat = hrdps.LAT if lat is None else lat
    lon = hrdps.LON if lon is None else lon
    return f"https://meteo.gc.ca/fr/location/index.html?coords={lat:.3f},{lon:.3f}"


def normals_url(station_id):
    """Official 1981–2010 climate-normals page of a station."""
    return f"https://climat.meteo.gc.ca/climate_normals/results_1981_2010_f.html?stnID={int(station_id)}&autofwd=1"


def open_url(url):
    """Open `url` in the default browser. Only http(s) links are ever opened."""
    if not url.startswith(("https://", "http://")):
        return False
    try:
        if sys.platform == "win32":
            os.startfile(url)                                   # noqa: S606 (http(s) only, checked above)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", url])
        elif shutil.which("xdg-open"):
            env = dict(os.environ)
            if getattr(sys, "frozen", False):                   # PyInstaller's library path breaks the system's browser launcher
                orig = env.pop("LD_LIBRARY_PATH_ORIG", None)
                env.pop("LD_LIBRARY_PATH", None)
                if orig is not None:
                    env["LD_LIBRARY_PATH"] = orig
            subprocess.Popen(["xdg-open", url], env=env, start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            webbrowser.open(url)
        return True
    except Exception:
        return False
