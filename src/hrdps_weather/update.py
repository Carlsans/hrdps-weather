"""Opt-in updates from this project's GitHub releases.

Modes (setting `update`, off by default so nothing extra is contacted unless you ask for it):
  off     never contact GitHub
  notify  check once a day and tell you; you decide when to install
  auto    check once a day, then download, verify and install by itself

Every update is checked twice before anything is installed or replaced:
  1. the release's SHA256SUMS file must carry a valid Ed25519 signature made with the project's release
     key (public key below; the private key only lives in the GitHub Actions secret and the author's backup),
  2. the downloaded file must match its SHA-256 listed in that signed file.
How an update is applied depends on how the program was installed (see install_kind()): the Windows installer
and the Linux single-file binary update themselves; pip / source / portable installs are only notified.
"""
import base64
import hashlib
import json
import os
import platform
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__, config, ed25519, net

REPO = "Carlsans/hrdps-weather"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
UA = f"hrdps-weather/{__version__} (+https://github.com/{REPO})"
PUBLIC_KEY = bytes.fromhex("9b17221f5876b04ba23ffe4c0fe4f1fb0535e0f7421568de3165986df5369991")
CHECK_EVERY = 24 * 3600
MODES = ("off", "notify", "auto")


class UpdateError(Exception):
    """A user-presentable reason why an update could not be installed."""


@dataclass
class Update:
    version: str
    tag: str
    url: str                                   # release page
    assets: dict = field(default_factory=dict)  # asset name -> download URL


# ── settings ─────────────────────────────────────────────────────────────────
def _settings_path():
    return config.config_dir() / "settings.json"


def mode():
    """off | notify | auto — `settings.json` (written by the UI) wins over config.toml; default off."""
    env = os.environ.get("HRDPS_UPDATE")
    if env in MODES:
        return env
    try:
        v = json.loads(_settings_path().read_text(encoding="utf-8")).get("update")
        if v in MODES:
            return v
    except (OSError, ValueError):
        pass
    try:
        v = config.load().get("update")
        return v if v in MODES else "off"
    except Exception:
        return "off"


def set_mode(m):
    if m not in MODES:
        raise ValueError(f"mode inconnu : {m} (off, notify ou auto)")
    p = _settings_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data["update"] = m
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ── state (throttle, last known release, pending install) ────────────────────
def _state_file():
    return config.cache_dir() / "update.json"


def _load_state():
    try:
        return json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(**kw):
    st = _load_state()
    st.update(kw)
    f = _state_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(st), encoding="utf-8")


def log(msg):
    try:
        f = config.cache_dir() / "update.log"
        f.parent.mkdir(parents=True, exist_ok=True)
        with f.open("a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


# ── what are we, what is out there ───────────────────────────────────────────
def parse_version(s):
    """'v1.2.10' -> (1, 2, 10); non-numeric parts sort as 0."""
    parts = []
    for p in str(s).lstrip("vV").split("."):
        digits = "".join(c for c in p if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def install_kind():
    """windows-installed | windows-portable | linux-binary | package — decides how (or whether) we self-update."""
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        if sys.platform == "win32":
            try:
                import winreg
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\hrdps-weather") as k:
                    root = Path(winreg.QueryValueEx(k, "InstallDir")[0])
                if root.resolve() == exe.parent.resolve():
                    return "windows-installed"
            except OSError:
                pass
            return "windows-portable"
        if sys.platform.startswith("linux") and os.access(exe.parent, os.W_OK) and os.access(exe, os.W_OK):
            return "linux-binary"
    return "package"


def how_to_update(kind=None):
    kind = kind or install_kind()
    return {"windows-portable": "décompressez la nouvelle archive par-dessus l'ancienne",
            "package": "pip install -U git+https://github.com/Carlsans/hrdps-weather",
            "linux-binary": "relancez install.sh"}.get(kind, "voir la page des versions")


def asset_names(kind=None):
    """(asset to download, checksum file) for this installation, or None when it cannot self-update."""
    kind = kind or install_kind()
    if kind == "windows-installed":
        return "hrdps-weather-setup-x86_64.exe", "SHA256SUMS.txt"
    if kind == "linux-binary":
        arch = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}.get(platform.machine().lower())
        if arch:
            return f"hrdps-weather-linux-{arch}", "SHA256SUMS-linux.txt"
    return None


def can_self_update(kind=None):
    return asset_names(kind) is not None


def latest_release():
    raw = net.get(API, UA, timeout=20)
    if not raw:
        return None
    try:
        j = json.loads(raw)
        if j.get("draft") or j.get("prerelease"):
            return None
        return Update(version=j["tag_name"].lstrip("vV"), tag=j["tag_name"], url=j.get("html_url", ""),
                      assets={a["name"]: a["browser_download_url"] for a in j.get("assets", [])})
    except (ValueError, KeyError):
        return None


def check(force=False):
    """The newer release (an Update) or None. At most one request per day unless forced."""
    st, now = _load_state(), time.time()
    if not force and now - st.get("checked", 0) < CHECK_EVERY and "latest" in st:
        rel = st["latest"]
        up = Update(**rel) if rel else None
    else:
        up = latest_release()
        if up is None and "latest" in st and not force:
            up = Update(**st["latest"]) if st["latest"] else None     # network hiccup: keep the last answer
        else:
            _save_state(checked=now, latest=up.__dict__ if up else None)
    if up and parse_version(up.version) > parse_version(__version__):
        return up
    return None


# ── download + verification ──────────────────────────────────────────────────
def _fetch(update, name, timeout=120):
    url = update.assets.get(name)
    if not url:
        raise UpdateError(f"{name} est absent de la version {update.tag}")
    data = net.get(url, UA, timeout=timeout)
    if data is None:
        raise UpdateError(f"téléchargement impossible : {name}")
    return data


def parse_sums(text):
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            out[parts[1].lstrip("*")] = parts[0].lower()
    return out


def download_verified(update, kind=None):
    """Bytes of the asset for this installation, after signature + SHA-256 checks. Raises UpdateError."""
    names = asset_names(kind)
    if names is None:
        raise UpdateError("ce type d'installation ne se met pas à jour tout seul")
    asset, sums_name = names
    sums = _fetch(update, sums_name, 30)
    sig = _fetch(update, sums_name + ".sig", 30)
    if not ed25519.verify(PUBLIC_KEY, sums, sig):
        raise UpdateError("signature invalide : mise à jour refusée")
    expected = parse_sums(sums.decode("utf-8", "replace")).get(asset)
    if not expected:
        raise UpdateError(f"{asset} n'est pas dans la liste signée")
    data = _fetch(update, asset)
    if hashlib.sha256(data).hexdigest() != expected:
        raise UpdateError("empreinte SHA-256 incorrecte : mise à jour refusée")
    return data


# ── applying ─────────────────────────────────────────────────────────────────
def apply(update, kind=None):
    """Download, verify and install. Returns (message, must_exit): must_exit is True when the caller has to quit
    (the Windows installer replaces the running program). Raises UpdateError."""
    kind = kind or install_kind()
    if not can_self_update(kind):
        raise UpdateError(f"installation « {kind} » : {how_to_update(kind)}")
    data = download_verified(update, kind)
    if kind == "linux-binary":
        exe = Path(sys.executable)
        new, old = exe.with_name(exe.name + ".new"), exe.with_name(exe.name + ".old")
        new.write_bytes(data)
        new.chmod(0o755)
        try:
            if exe.exists():
                os.replace(exe, old)                           # keep the previous version for a manual rollback
            os.replace(new, exe)
        except OSError as e:
            if old.exists() and not exe.exists():
                os.replace(old, exe)
            raise UpdateError(f"remplacement impossible : {e}")
        _save_state(pending_version=update.version)
        log(f"installed {update.version} over {__version__} (linux binary)")
        return f"Version {update.version} installée — relancez le programme.", False
    # windows-installed: run the (verified) per-user installer silently; it stops the tray app, replaces the
    # files and restarts the tray minimised. We must not hold files open, so the caller exits right away.
    d = config.cache_dir() / "update"
    d.mkdir(parents=True, exist_ok=True)
    setup = d / f"hrdps-weather-setup-{update.version}.exe"
    setup.write_bytes(data)
    _save_state(pending_version=update.version)
    log(f"launching installer {setup.name} (from {__version__})")
    subprocess.Popen([str(setup), "/S"], creationflags=0x00000008 | 0x08000000, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return f"Installation de la version {update.version}…", True


def consume_installed_notice():
    """After a self-update, the first start of the new version says so once."""
    pend = _load_state().get("pending_version")
    if pend and parse_version(pend) <= parse_version(__version__):
        _save_state(pending_version=None)
        return f"hrdps-weather a été mis à jour vers la version {__version__}."
    return None


# ── background helper shared by the windows and the tray ─────────────────────
class Updater:
    """Checks for an update in the background and exposes a one-line `message` for the UI."""

    def __init__(self):
        self.message = ""
        self.info = None
        self.busy = False
        self.needs_exit = False
        self.on_found = None                    # optional callback(update) — the tray uses it for a notification
        self._thread = None

    def start(self):
        """Begin the daily background check (no-op when updates are off or it is already running)."""
        if mode() == "off" or (self._thread is not None and self._thread.is_alive()):
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while mode() != "off":
            try:
                self.refresh()
            except Exception as e:
                log(f"check failed: {type(e).__name__}: {e}")
            time.sleep(6 * 3600)                # check() itself throttles to once a day

    def refresh(self, force=False):
        up = check(force)
        self.info = up
        if not up:
            self.message = ""
            return
        if self.on_found:
            self.on_found(up)
        if mode() == "auto" and can_self_update():
            self.install()
        elif can_self_update():
            self.message = f"⬆ Version {up.version} disponible — touche U ou clic pour installer"
        else:
            self.message = f"⬆ Version {up.version} disponible : {how_to_update()}"

    def install(self):
        """Install the pending update (in the background); safe to call from a UI handler."""
        if self.busy or not self.info or not can_self_update():
            return
        self.busy = True
        self.message = f"Mise à jour vers {self.info.version}…"

        def work():
            try:
                self.message, self.needs_exit = apply(self.info)
            except UpdateError as e:
                self.message = f"Mise à jour impossible : {e}"
                log(f"apply failed: {e}")
            except Exception as e:
                self.message = f"Mise à jour impossible : {type(e).__name__}"
                log(f"apply failed: {type(e).__name__}: {e}")
            finally:
                self.busy = False
        threading.Thread(target=work, daemon=True).start()
