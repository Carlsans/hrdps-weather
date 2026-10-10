"""Consistency of the web app (web/) with the Python package: version numbers and the offline file list."""
import re
from pathlib import Path

from hrdps_weather import __version__

WEB = Path(__file__).resolve().parent.parent / "web"


def test_versions_match():
    assert f'export const VERSION = "{__version__}";' in (WEB / "js" / "version.js").read_text(encoding="utf-8")
    assert f'const VERSION = "{__version__}";' in (WEB / "sw.js").read_text(encoding="utf-8")


def test_service_worker_caches_every_app_file():
    sw = (WEB / "sw.js").read_text(encoding="utf-8")
    listed = set(re.findall(r'"([^"]+)"', sw.split("const FILES = [", 1)[1].split("];", 1)[0]))
    files = {p.relative_to(WEB).as_posix() for p in WEB.rglob("*")
             if p.is_file() and p.suffix in {".js", ".css", ".html", ".png", ".webmanifest"}
             and p.name != "sw.js" and "tests" not in p.parts}
    assert files <= listed, f"missing from sw.js FILES: {sorted(files - listed)}"
    assert all((WEB / f).exists() for f in listed - {"./"}), "sw.js lists a file that does not exist"


def test_build_stamp_placeholder_present():
    # pages.yml replaces this line with the commit id; without it updates would not reach installed apps
    assert 'const BUILD = "dev";' in (WEB / "sw.js").read_text(encoding="utf-8")
