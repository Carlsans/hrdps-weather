#!/usr/bin/env bash
# hrdps-weather installer — Linux, x86_64 and aarch64.
#
#   curl -fsSL https://raw.githubusercontent.com/Carlsans/hrdps-weather/main/install.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/Carlsans/hrdps-weather/main/install.sh | bash -s -- --systemd
#   curl -fsSL https://raw.githubusercontent.com/Carlsans/hrdps-weather/main/install.sh | bash -s -- --uninstall
#
# Downloads the prebuilt binary from the latest GitHub release, checks its SHA-256 against the
# release's SHA256SUMS-linux.txt, installs it to ~/.local/bin (no sudo, no system packages touched)
# and adds an applications-menu entry. Safe to re-run: re-running upgrades in place.
#
# --systemd    also enable a user timer that checks for a new model run every 30 min
#              (not needed with a bar module: the bar already triggers refreshes)
# --uninstall  remove the program, menu entry and timer (your config and cache are kept)
set -euo pipefail

REPO="Carlsans/hrdps-weather"
BIN_DIR="$HOME/.local/bin"
APPS_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
UNIT_DIR="$HOME/.config/systemd/user"

WITH_SYSTEMD=0; UNINSTALL=0
for arg in "$@"; do
    case "$arg" in
        --systemd)   WITH_SYSTEMD=1 ;;
        --uninstall) UNINSTALL=1 ;;
        *) echo "Unknown option: $arg" >&2; exit 1 ;;
    esac
done

if [ "$(uname -s)" != "Linux" ]; then
    echo "This installer is for Linux. For Windows see https://github.com/$REPO#windows" >&2; exit 1
fi

if [ "$UNINSTALL" = 1 ]; then
    command -v systemctl >/dev/null 2>&1 && systemctl --user disable --now hrdps-weather-refresh.timer >/dev/null 2>&1 || true
    rm -f "$BIN_DIR/hrdps-weather" "$APPS_DIR/hrdps-weather.desktop" "$ICON_DIR/hrdps-weather.svg" \
          "$UNIT_DIR/hrdps-weather-refresh.service" "$UNIT_DIR/hrdps-weather-refresh.timer"
    echo "hrdps-weather removed. Kept: ~/.config/hrdps-weather (config) and ~/.cache/hrdps-weather (cache)."
    exit 0
fi

ARCH="$(uname -m)"
case "$ARCH" in
    x86_64)        ASSET="hrdps-weather-linux-x86_64" ;;
    aarch64|arm64) ASSET="hrdps-weather-linux-aarch64" ;;
    *) echo "No prebuilt binary for '$ARCH' (x86_64 and aarch64 only). Install from source: https://github.com/$REPO#linux" >&2; exit 1 ;;
esac

if command -v curl >/dev/null 2>&1; then fetch() { curl -fsSL "$1" -o "$2"; }
elif command -v wget >/dev/null 2>&1; then fetch() { wget -q "$1" -O "$2"; }
else echo "Need curl or wget — please install one and re-run." >&2; exit 1; fi

BASE="https://github.com/$REPO/releases/latest/download"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
mkdir -p "$BIN_DIR" "$APPS_DIR" "$ICON_DIR"

echo "Downloading hrdps-weather ($ARCH)..."
fetch "$BASE/$ASSET" "$TMP/$ASSET"
fetch "$BASE/SHA256SUMS-linux.txt" "$TMP/SHA256SUMS-linux.txt"

echo "Verifying SHA-256..."
EXPECTED="$(grep " $ASSET\$" "$TMP/SHA256SUMS-linux.txt" | awk '{print $1}' || true)"
ACTUAL="$(sha256sum "$TMP/$ASSET" | awk '{print $1}')"
if [ -z "$EXPECTED" ] || [ "$EXPECTED" != "$ACTUAL" ]; then
    echo "Checksum mismatch for $ASSET — refusing to install." >&2
    echo "  expected: ${EXPECTED:-<none listed>}" >&2; echo "  actual:   $ACTUAL" >&2; exit 1
fi

chmod +x "$TMP/$ASSET"
mv "$TMP/$ASSET" "$BIN_DIR/hrdps-weather"          # replace the live binary only after a verified download

cat > "$ICON_DIR/hrdps-weather.svg" <<'SVGEOF'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">
  <rect x="8" y="8" width="240" height="240" rx="48" fill="#1e1e2e"/>
  <circle cx="166" cy="86" r="46" fill="#f9e2af"/>
  <g fill="#cdd6f4"><circle cx="88" cy="148" r="44"/><circle cx="140" cy="128" r="48"/><circle cx="178" cy="154" r="42"/>
  <rect x="70" y="150" width="130" height="46" rx="23"/></g>
  <g fill="#89b4fa"><rect x="84" y="200" width="12" height="32" rx="6"/><rect x="124" y="200" width="12" height="32" rx="6"/><rect x="164" y="200" width="12" height="32" rx="6"/></g>
</svg>
SVGEOF

cat > "$APPS_DIR/hrdps-weather.desktop" <<DESKTOPEOF
[Desktop Entry]
Type=Application
Name=Météo HRDPS
Comment=Environment Canada HRDPS weather: animated map and charts
Exec=$BIN_DIR/hrdps-weather popup
Icon=$ICON_DIR/hrdps-weather.svg
Terminal=false
Categories=Utility;
StartupWMClass=hrdps-weather
DESKTOPEOF
chmod +x "$APPS_DIR/hrdps-weather.desktop"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true

if [ "$WITH_SYSTEMD" = 1 ]; then
    if command -v systemctl >/dev/null 2>&1; then
        mkdir -p "$UNIT_DIR"
        fetch "https://raw.githubusercontent.com/$REPO/main/packaging/systemd/hrdps-weather-refresh.service" "$UNIT_DIR/hrdps-weather-refresh.service"
        fetch "https://raw.githubusercontent.com/$REPO/main/packaging/systemd/hrdps-weather-refresh.timer"   "$UNIT_DIR/hrdps-weather-refresh.timer"
        systemctl --user daemon-reload && systemctl --user enable --now hrdps-weather-refresh.timer
        echo "systemd user timer enabled (hrdps-weather-refresh.timer)."
    else
        echo "systemctl not found — skipped --systemd." >&2
    fi
fi

echo
echo "Installed: $BIN_DIR/hrdps-weather ($("$BIN_DIR/hrdps-weather" --version))"
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) echo "Note: $BIN_DIR is not on your PATH — add it to use 'hrdps-weather' from a shell." ;; esac
cat <<MSG

Next steps
  1. Set your location (default: Québec):   hrdps-weather config     # prints the file to edit
  2. Try the window:                        hrdps-weather popup      # or find "Météo HRDPS" in your app menu
     (the first launch downloads a full model run, about a minute)
  3. Add it to your bar — waybar, i3blocks, polybar, i3status-rust ...:
       https://github.com/$REPO/blob/main/docs/linux.md
MSG
