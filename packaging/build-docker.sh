#!/usr/bin/env bash
# Build the portable Linux binary inside Ubuntu 22.04 (see Dockerfile.build).
#
#   packaging/build-docker.sh            # host architecture -> dist/hrdps-weather
#   packaging/build-docker.sh aarch64    # ARM64 through qemu (slow) -> dist-aarch64/hrdps-weather
#
# CI builds both natively (ubuntu-22.04 and ubuntu-22.04-arm runners); this script is for local use.
# For aarch64 register qemu first:  docker run --privileged --rm tonistiigi/binfmt --install arm64
# If the container cannot resolve names (host DNS is a VPN address): HRDPS_DOCKER_NETWORK=host packaging/build-docker.sh
set -euo pipefail
cd "$(dirname "$0")/.."

ARCH="${1:-native}"
case "$ARCH" in
    native)        PLATFORM_ARGS=(); IMAGE="hrdps-weather-builder"; DIST_DIR="dist"; SCRATCH="build-docker-scratch" ;;
    aarch64|arm64) PLATFORM_ARGS=(--platform linux/arm64); IMAGE="hrdps-weather-builder-arm64"
                   DIST_DIR="dist-aarch64"; SCRATCH="build-arm64-scratch" ;;
    *) echo "Unknown architecture '$ARCH' — use 'native' or 'aarch64'." >&2; exit 1 ;;
esac

NETWORK_ARGS=()
[ -n "${HRDPS_DOCKER_NETWORK:-}" ] && NETWORK_ARGS=(--network "$HRDPS_DOCKER_NETWORK")

docker build "${PLATFORM_ARGS[@]}" "${NETWORK_ARGS[@]}" -f packaging/Dockerfile.build -t "$IMAGE" .
mkdir -p "$DIST_DIR"; rm -rf "$SCRATCH"; mkdir -p "$SCRATCH"
docker run --rm "${PLATFORM_ARGS[@]}" --user "$(id -u):$(id -g)" -e HOME=/tmp \
    -v "$(pwd)/$DIST_DIR:/app/dist" -v "$(pwd)/$SCRATCH:/app/build" "$IMAGE"
rm -rf "$SCRATCH"

echo; echo "built (Ubuntu 22.04 base, portable to newer systems):"
ls -lh "$DIST_DIR/hrdps-weather"; file "$DIST_DIR/hrdps-weather" | sed 's/^/  /'
