#!/bin/bash
# build.sh — local T114 firmware build using Podman.
#
# Builds the ff-builder image (Podman caches the LeapYeet clone + ARM
# toolchain layer, so only patch-t114.py changes bust the cache) then runs
# against the baked-in LeapYeet source at the pinned SHA — identical to what
# GitHub Actions does.
#
# Usage:
#   ./build.sh
#
# Env:
#   IMAGE  Container image name. Defaults to ff-builder.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUTPUT_DIR="$SCRIPT_DIR/firmware/heltec_t114"
IMAGE="${IMAGE:-ff-builder}"

mkdir -p "$OUTPUT_DIR"

podman build -t "$IMAGE" "$SCRIPT_DIR"

podman run --rm \
  -v "$OUTPUT_DIR":/output:Z \
  "$IMAGE"

echo
echo "Artifact: $OUTPUT_DIR/firmware.uf2"
