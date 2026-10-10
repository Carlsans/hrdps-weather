"""
System-tray application (Windows; also works on Linux desktops with a tray).

  - icon showing the current temperature, tooltip with a one-line summary
  - left click / "Ouvrir" : full window (animated map + charts)
  - menu: refresh now, optional start-with-Windows, quit

Network access is limited to the two hosts documented in the README
(geo.weather.gc.ca and tile.openstreetmap.org). Nothing is installed or
started automatically: "Démarrer avec Windows" is an opt-in menu item that
only adds/removes one value under HKCU\\...\\Run.
"""
import queue
import sys
import threading
import tkinter as tk

from PIL import Image, ImageDraw, ImageFont

from . import hrdps
from . import update as upd

REFRESH_EVERY = 300           # seconds between tray updates (the cache itself refreshes per model run)
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "HRDPSWeather"


# ── optional "start with Windows" (opt-in, per-user, no admin) ────────────────
# ── single instance: a second launch asks the running one to show its window ─
# (a lock file + a request file in the cache folder: no port is opened)
def _request_file():
    return hrdps.CACHE / "tray-open.request"


def request_open():
    hrdps.CACHE.mkdir(parents=True, exist_ok=True)
    _request_file().write_text("1", encoding="utf-8")


def consume_open_request():
    f = _request_file()
    if f.exists():
        try:
            f.unlink()
        except OSError:
            pass
        return True
    return False


def autostart_command():
    exe = sys.executable
    if getattr(sys, "frozen", False):                          # packaged build: the exe is the tray app
        return f'"{exe}" tray --minimized'
    if exe.lower().endswith("python.exe"):                     # avoid a console window
        w = exe[:-len("python.exe")] + "pythonw.exe"
        exe = w if __import__("os").path.exists(w) else exe
    return f'"{exe}" -m hrdps_weather tray --minimized'


def autostart_enabled():
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
            return True
    except OSError:
        return False


def set_autostart(on):
    if sys.platform != "win32":
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except OSError:
                pass


# ── icon / tooltip ────────────────────────────────────────────────────────────
def _font(size):
    for name in ("segoeuib.ttf", "DejaVuSans-Bold.ttf", "arialbd.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def make_icon(text, alert=False):
    im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    dr = ImageDraw.Draw(im)
    dr.rounded_rectangle((0, 0, 63, 63), 12, fill=(30, 30, 46, 255), outline=(250, 179, 135, 255) if alert else None, width=3)
    f = _font(40 if len(text) <= 3 else 32)
    box = dr.textbbox((0, 0), text, font=f)
    dr.text(((64 - (box[2] - box[0])) / 2 - box[0], (64 - (box[3] - box[1])) / 2 - box[1]), text, font=f, fill=(205, 214, 244, 255))
    return im


def summary(d):
    i = d.now_index()
    nxt = sum(d.pr[j] for j in d.upto(i, 24))
    al = d.alerts(i)
    s = f"{hrdps.LOCATION} {d.tt[i]:.0f}° · {d.describe(i)}"
    s += f" · {nxt:.0f} mm/24 h" if nxt >= 0.5 else ""
    if al:
        s += " · ⚠ " + al[0][0].split(" · ")[0]
    return s[:127]                                              # Windows tooltip limit


def _say(icon, message, title="hrdps-weather"):
    try:
        icon.notify(message, title)
    except Exception:
        pass                                                         # notifications are best-effort


def _notify_update(icon, u):
    """Tell the user about a release once per version."""
    if icon is None or upd._load_state().get("notified") == u.version:
        return
    upd._save_state(notified=u.version)
    _say(icon, f"Version {u.version} disponible." + (" Menu de l'icône → Mises à jour." if upd.mode() != "auto" else ""))


def _notify(icon):
    try:
        icon.notify("L'icône météo est dans la zone de notification (cliquez sur ^ si elle est masquée). "
                    "Clic gauche : ouvrir la météo.", "hrdps-weather est démarré")
    except Exception:
        pass                                                     # notifications are best-effort


# ── application ──────────────────────────────────────────────────────────────
def run(minimized=False):
    """minimized=True (used by "Start with Windows"): stay quiet in the tray, no window, no notification."""
    lock = hrdps._Lock(hrdps.CACHE / "tray.lock")
    if not lock.acquire():                                       # already running: show its window
        request_open()
        return

    import pystray                                               # imported late: optional on Linux
    from .ui_tk import WeatherWindow

    root = tk.Tk()
    root.withdraw()
    q = queue.Queue()
    state = {"win": None}

    def open_window():
        w = state["win"]
        if w is not None and w.alive:
            w.top.deiconify(); w.top.lift(); w.top.focus_force()
        else:
            state["win"] = WeatherWindow(root, updater)

    def refresh_icon(icon):
        hrdps.ensure_fresh()
        d = hrdps.load()
        if d is None:
            icon.icon, icon.title = make_icon("…"), "HRDPS : téléchargement des données…"
        else:
            icon.icon = make_icon(f"{d.tt[d.now_index()]:.0f}°", bool(d.alerts(d.now_index())))
            icon.title = summary(d)

    def toggle_autostart(icon, item):
        set_autostart(not autostart_enabled())

    updater = upd.Updater()
    updater.on_found = lambda u: _notify_update(icon_ref[0], u)
    icon_ref = [None]

    def pick_mode(m):
        def action(icon, item):
            upd.set_mode(m)
            updater.start()                                         # begins checking now if it was off
        return action

    def check_now(icon, item):
        def work():
            updater.refresh(force=True)
            if updater.info is None:
                _say(icon, f"Vous avez la dernière version ({upd.__version__}).")
        threading.Thread(target=work, daemon=True).start()

    update_menu = pystray.Menu(
        pystray.MenuItem("Désactivées", pick_mode("off"), checked=lambda it: upd.mode() == "off", radio=True),
        pystray.MenuItem("Me prévenir", pick_mode("notify"), checked=lambda it: upd.mode() == "notify", radio=True),
        pystray.MenuItem("Automatiques", pick_mode("auto"), checked=lambda it: upd.mode() == "auto", radio=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Vérifier maintenant", check_now),
        pystray.MenuItem(lambda it: f"Installer la version {updater.info.version}",
                         lambda i, it: updater.install(),
                         visible=lambda it: bool(updater.info and upd.can_self_update() and not updater.busy)))

    menu = [pystray.MenuItem("Ouvrir la météo", lambda i, it: q.put("open"), default=True),
            pystray.MenuItem("Actualiser maintenant", lambda i, it: (hrdps.ensure_fresh(force=True), q.put("update"))),
            pystray.MenuItem("Mises à jour", update_menu)]
    if sys.platform == "win32":
        menu.append(pystray.MenuItem("Démarrer avec Windows", toggle_autostart, checked=lambda it: autostart_enabled()))
    menu.append(pystray.MenuItem("Quitter", lambda i, it: q.put("quit")))
    icon = pystray.Icon("hrdps-weather", make_icon("…"), "HRDPS météo", pystray.Menu(*menu))
    icon_ref[0] = icon
    icon.run_detached()
    updater.start()                                                 # no-op while updates are off (the default)
    notice = upd.consume_installed_notice()                         # "updated to X", once, after a self-update
    if notice:
        threading.Timer(3.0, lambda: _say(icon, notice)).start()

    if not minimized:
        # Windows hides new tray icons under the "^" overflow, which makes a healthy app look dead:
        # open the window right away and say where the icon lives.
        q.put("open")
        threading.Timer(2.5, lambda: _notify(icon)).start()

    stop = threading.Event()

    def icon_loop():                                                # not "updater": that name is the upd.Updater above
        while not stop.is_set():
            try:
                refresh_icon(icon)
            except Exception:
                pass
            stop.wait(REFRESH_EVERY)
    threading.Thread(target=icon_loop, daemon=True).start()

    def poll():
        try:
            if updater.needs_exit:                                  # the installer is replacing this program
                stop.set(); icon.stop(); root.quit(); return
            if consume_open_request():
                open_window()
            while True:
                cmd = q.get_nowait()
                if cmd == "open":
                    open_window()
                elif cmd == "update":
                    threading.Thread(target=refresh_icon, args=(icon,), daemon=True).start()
                elif cmd == "quit":
                    stop.set(); icon.stop(); root.quit(); return
        except queue.Empty:
            pass
        root.after(200, poll)

    root.after(200, poll)
    root.mainloop()
