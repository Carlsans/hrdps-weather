#!/usr/bin/env bash
# Smoke-test dist/hrdps-weather on several distributions in Docker. Each container gets what a real
# desktop already has (X11 client libs, a font, xvfb for the headless window test), then runs the
# offline selftest, a status line, and opens the Tk window under Xvfb.
#
#   packaging/test-docker.sh [path/to/hrdps-weather]
set -uo pipefail
cd "$(dirname "$0")/.."
BIN="${1:-dist/hrdps-weather}"
[ -x "$BIN" ] || { echo "$BIN not found — run packaging/build-docker.sh first." >&2; exit 1; }
BIN="$(cd "$(dirname "$BIN")" && pwd)/$(basename "$BIN")"

TEST_BODY='
set -e
export HRDPS_NO_REFRESH=1 HOME=/tmp/home XDG_CACHE_HOME=/tmp/cache XDG_CONFIG_HOME=/tmp/cfg
mkdir -p $HOME
cp /bin-under-test /tmp/hrdps-weather && chmod +x /tmp/hrdps-weather
echo "--- version / selftest ---"
/tmp/hrdps-weather --version
/tmp/hrdps-weather selftest | tee /tmp/selftest.out
grep -q "render=ok" /tmp/selftest.out
echo "--- status (no cache yet: placeholder is expected) ---"
/tmp/hrdps-weather status --format plain
echo "--- window under Xvfb ---"
xvfb-run -a /tmp/hrdps-weather popup --tk &
PID=$!
sleep 5
if kill -0 "$PID" 2>/dev/null; then echo "window still running after 5 s — OK"; kill "$PID"
else wait "$PID"; echo "window exited early"; exit 1; fi
'

# name | image | install command (X11 client libs + Xvfb + a font)
DISTROS=(
  "ubuntu-22.04|ubuntu:22.04|apt-get update -qq && apt-get install -y -qq --no-install-recommends xvfb xauth libx11-6 libxft2 fonts-dejavu-core fontconfig >/dev/null"
  "ubuntu-24.04|ubuntu:24.04|apt-get update -qq && apt-get install -y -qq --no-install-recommends xvfb xauth libx11-6 libxft2 fonts-dejavu-core fontconfig >/dev/null"
  "debian-12|debian:12|apt-get update -qq && apt-get install -y -qq --no-install-recommends xvfb xauth libx11-6 libxft2 fonts-dejavu-core fontconfig >/dev/null"
  "fedora|fedora:latest|dnf install -y -q xorg-x11-server-Xvfb xorg-x11-xauth libX11 libXft dejavu-sans-fonts fontconfig >/dev/null"
  "arch|archlinux:latest|pacman -Sy --noconfirm --quiet xorg-server-xvfb xorg-xauth libx11 libxft ttf-dejavu fontconfig >/dev/null 2>&1"
)

# Pin the platform: a cached image of the other architecture under the same tag otherwise produces a
# misleading "No such file or directory" (the kernel has no loader for the wrong architecture).
case "$(uname -m)" in x86_64) PLATFORM=linux/amd64 ;; aarch64|arm64) PLATFORM=linux/arm64 ;; *) PLATFORM="" ;; esac
PLATFORM_ARGS=(); [ -n "$PLATFORM" ] && PLATFORM_ARGS=(--platform "$PLATFORM")

FAILED=()
for entry in "${DISTROS[@]}"; do
    IFS='|' read -r name image install_cmd <<< "$entry"
    echo; echo "=================================================================="; echo "  $name ($image)"; echo "=================================================================="
    if docker run --rm "${PLATFORM_ARGS[@]}" -v "$BIN:/bin-under-test:ro" "$image" bash -c "$install_cmd; $TEST_BODY"; then
        echo "PASS: $name"
    else
        echo "FAIL: $name"; FAILED+=("$name")
    fi
done
echo; echo "=================================================================="
if [ ${#FAILED[@]} -eq 0 ]; then echo "All ${#DISTROS[@]} distros passed."; else echo "FAILED: ${FAILED[*]}"; exit 1; fi
