#!/usr/bin/env python3
"""
Waybar weather module — HRDPS (Environnement Canada, 2.5 km) only.

Bar    : current conditions + tomorrow's morning / afternoon / evening.
Tooltip: now, alerts, next 24 h, the following days, sparklines.
Click  : `hrdps-weather popup` (animated map + full charts).

Never blocks on the network: it reads the cache and spawns a detached refresh
when a new model run may be available (see hrdps.py).
"""
import json
import re
from datetime import timedelta
from xml.sax.saxutils import escape, unescape

from . import hrdps

BARS = "▁▂▃▄▅▆▇█"

def arrow(wd):
    """wd = direction the wind comes FROM; the arrow shows where it blows to."""
    return "↓↙←↖↑↗→↘"[int(((wd % 360) + 22.5) // 45) % 8]

def spark(vals, lo=None, hi=None):
    lo = min(vals) if lo is None else lo
    hi = max(vals) if hi is None else hi
    span = max(hi - lo, 1e-9)
    return "".join(BARS[min(7, max(0, int((v - lo) / span * 7.999)))] for v in vals)

def mm(v):
    return f"{v:.1f}".rstrip("0").rstrip(".") if v >= 0.1 else "0"

def slot_text(s):
    if not s:
        return None
    out = f"{s['icon']} {s['t']}°"
    if s["cm"] >= 0.5:
        out += f" ❄{s['cm']:.0f}cm"
    elif s["mm"] >= 0.1:
        out += f" 💧{mm(s['mm'])}mm"
    return out

def build(d):
    i = d.now_index()
    today = d.local[i].date()
    tomorrow = today + timedelta(days=1)

    # ── bar text ──────────────────────────────────────────────────────────────
    al = d.alerts(i)
    parts = [slot_text(d.slot(tomorrow, *r)) for r in ((6, 12), (12, 18), (18, 22))]
    text = f"{d.icon(i)} {d.tt[i]:.0f}°"
    if d.kind(i) != "none":
        text += f" {mm(d.pr[i])}mm"
    text += "   |   " + "  ·  ".join(p for p in parts if p)
    if al:
        text += "  ⚠"

    # ── tooltip ───────────────────────────────────────────────────────────────
    L = [f"<b>{escape(hrdps.LOCATION)}</b> — {hrdps.fr(d.local[i], '%a %d %b %Hh')}",
         f"<big>{d.icon(i)} <b>{d.tt[i]:.0f}°C</b></big>  {escape(d.describe(i))}"]
    feel = f"ressenti {d.re_[i]:.0f}° · " if d.re_[i] is not None else ""
    wind = f"vent {d.ws[i]:.0f} km/h {arrow(d.wd[i])}"
    if d.gust[i] - d.ws[i] > 5:
        wind += f" (rafales {d.gust[i]:.0f})"
    L.append(f"<span color='#a6adc8'>{feel}{d.utci_l[i].lower()} · {wind} · HR {d.hr[i]:.0f}% · {d.slp[i]:.0f} hPa</span>")
    if al:
        L.append("")
        L += [f"<span color='#fab387'>⚠ {escape(a)}</span>" for a, _ in al]

    # next 24 h, every 3 h
    L += ["", "<b>Prochaines 24 h</b>", "<tt>"]
    for k in range(i + 1, min(i + 25, d.n), 3):
        blk = range(k, min(k + 3, d.n))
        pp, sn = sum(d.pr[j] for j in blk), sum(d.snow_cm[j] for j in blk)
        g = max(d.gust[j] for j in blk)
        prec = f"❄{sn:4.1f}cm" if sn >= 0.5 else f"💧{pp:4.1f}mm" if pp >= 0.05 else "        "
        L.append(f"{hrdps.fr(d.local[k], '%a %Hh'):<7} {d.icon(k)} {d.tt[k]:>4.0f}°  {prec}  "
                 f"{arrow(d.wd[k])}{d.ws[k]:>3.0f}/{g:<3.0f} km/h")
    L.append("</tt>")

    # following days
    L += ["", "<b>Prochains jours</b>", "<tt>"]
    for off in (1, 2):
        day = today + timedelta(days=off)
        cells = []
        for r in ((6, 12), (12, 18), (18, 24)):
            s_ = d.slot(day, *r)
            if s_ is None:
                cells.append(" " * 16); continue
            pr = f"❄{s_['cm']:.0f}cm" if s_["cm"] >= 0.5 else f"💧{mm(s_['mm'])}mm" if s_["mm"] >= 0.1 else ""
            cells.append(f"{s_['icon']}{s_['t']:>3}° {pr:<8}")
        L.append(f"{hrdps.fr(day, '%a %d'):<7}" + " ".join(cells))
    L.append("</tt>")

    # sparklines over the next 24 h
    rng = list(d.upto(i, 25))
    tmin, tmax = min(d.tt[k] for k in rng), max(d.tt[k] for k in rng)
    L += ["", "<tt>",
          f"<span color='#f38ba8'>T°  {spark([d.tt[k] for k in rng])}</span>  {tmin:.0f}…{tmax:.0f}°",
          f"<span color='#89b4fa'>💧  {spark([d.pr[k] for k in rng], 0, 4)}</span>  Σ{sum(d.pr[k] for k in rng):.1f} mm",
          f"<span color='#89dceb'>💨  {spark([d.gust[k] for k in rng], 0, 80)}</span>  ≤{max(d.gust[k] for k in rng):.0f} km/h",
          "</tt>", "",
          f"<span color='#6c7086'>HRDPS 2,5 km · run {d.ref.strftime('%HZ')} · clic : carte animée</span>"]
    return {"text": text, "tooltip": "\n".join(L), "class": "weather" + (" alert" if al else "")}

def main():
    hrdps.ensure_fresh()
    d = hrdps.load()
    if d is None:
        return {"text": "⏳ météo", "tooltip": "Téléchargement des données HRDPS (≈1 min)…", "class": "weather"}
    return build(d)

def _plain(markup):
    return unescape(re.sub(r"<[^>]+>", "", markup))


def print_status(fmt="waybar"):
    """Print the status in the format a given bar understands.

    waybar / i3status-rs : JSON · plain : one line · polybar : one line with a colour tag on alerts ·
    i3blocks : full text, short text, colour (the three lines i3blocks expects)."""
    try:
        out = main()
    except Exception as e:
        out = {"text": "⚠️ météo", "tooltip": escape(f"{type(e).__name__}: {e}"), "class": "weather"}
    text, alert = out["text"], "alert" in out.get("class", "")
    short = text.split("   |   ")[0].replace("  ⚠", "")
    if fmt == "waybar":
        print(json.dumps(out))
    elif fmt == "plain":
        print(text)
    elif fmt == "polybar":
        print(text.replace("⚠", "%{F#fab387}⚠%{F-}"))
    elif fmt == "i3blocks":
        print(text); print(short); print("#fab387" if alert else "")
    elif fmt == "i3status-rs":
        print(json.dumps({"icon": "weather_default", "state": "Warning" if alert else "Idle",
                          "text": text, "short_text": short}))


def print_json():
    print_status("waybar")
