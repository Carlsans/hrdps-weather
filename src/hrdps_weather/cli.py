"""Command line entry point: `hrdps-weather <command>` / `python -m hrdps_weather <command>`."""
import argparse
import sys

from . import __version__

HELP = """\
commandes :
  tray            icône de zone de notification + fenêtre (Windows, ou Linux avec une zone de notification)
  popup           fenêtre météo (GTK4 si disponible, sinon Tk) ; --kill pour fermer ; --tk pour forcer Tk
  waybar          sortie JSON pour un module waybar custom (Linux)
  refresh         télécharge le dernier run HRDPS (--force pour retélécharger)
  png FICHIER [couche] [heures]   image fixe du tableau de bord (couche : rt|tt|ws|nt)
  config          affiche (et crée au besoin) le fichier de configuration
  selftest        vérifie hors ligne que le rendu et les dépendances fonctionnent (utilisé par la CI)
"""


def main(argv=None):
    ap = argparse.ArgumentParser(prog="hrdps-weather", description="Météo HRDPS (Environnement Canada, 2,5 km)",
                                 epilog=HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["tray", "popup", "waybar", "refresh", "png", "config", "selftest"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--kill", action="store_true")
    ap.add_argument("--tk", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--version", action="version", version=__version__)
    a = ap.parse_args(argv)

    if a.command == "selftest":
        sys.exit(selftest())
    if a.command == "config":
        from . import config
        print(config.write_template())
    elif a.command == "refresh":
        from . import hrdps
        hrdps.refresh(force=a.force)
    elif a.command == "waybar":
        from . import waybar
        waybar.print_json()
    elif a.command == "png":
        from . import hrdps, view
        if not a.args:
            ap.error("png : nom de fichier requis")
        d = hrdps.load()
        if d is None:
            sys.exit("pas de données en cache : lancez `hrdps-weather refresh`")
        try:
            base = hrdps.basemap()
        except Exception:
            base = None
        view.render_png(a.args[0], d, a.args[1] if len(a.args) > 1 else "rt",
                        int(a.args[2]) if len(a.args) > 2 else 0, hrdps.load_maps(), base)
    elif a.command == "popup":
        use_gtk = False
        if not a.tk and sys.platform != "win32":
            try:
                import gi
                gi.require_version("Gtk", "4.0")
                use_gtk = True
            except (ImportError, ValueError):
                pass
        if use_gtk:
            from . import popup_gtk
            popup_gtk.main(kill=a.kill)
        elif a.kill:
            pass
        else:
            from . import ui_tk
            ui_tk.run_standalone()
    elif a.command == "tray":
        if sys.platform == "win32":                 # crisp rendering on high-DPI screens
            try:
                import ctypes
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                pass
        from . import tray
        tray.run()


def selftest():
    """Offline check of the installed/packaged build: imports, bundled sample run, one rendered frame."""
    import json
    import tempfile
    from pathlib import Path
    import cairo
    import numpy
    import PIL
    import tkinter
    from zoneinfo import ZoneInfo
    from . import hrdps, view
    ZoneInfo("America/Toronto")
    sample = Path(hrdps.__file__).parent / "data" / "sample_series.json"
    d = hrdps.Data(json.loads(sample.read_text(encoding="utf-8")))
    out = Path(tempfile.gettempdir()) / "hrdps-weather-selftest.png"
    view.render_png(str(out), d, "rt", 6)
    ok = out.stat().st_size > 20_000
    out.unlink(missing_ok=True)
    print(f"hrdps-weather {__version__} · cairo {cairo.version} · numpy {numpy.__version__} · "
          f"Pillow {PIL.__version__} · tk {tkinter.TkVersion} · pango={view.HAVE_PANGO} · render={'ok' if ok else 'FAILED'}")
    return 0 if ok else 1


def tray_main():
    """gui-script entry point (`hrdps-weather-tray`): no console window on Windows."""
    main(["tray"])


if __name__ == "__main__":
    main()
