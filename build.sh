#!/bin/bash
# build.sh — local T114 firmware build using Podman.
#
# Bind-mounts a long-lived LeapYeet/firmware checkout into the ff-builder
# container and writes firmware.uf2 back into firmware/heltec_t114/.
#
# Usage:
#   ./build.sh
#
# Env:
#   FIRMWARE_SRC  Path to a LeapYeet/firmware checkout with submodules.
#                 Defaults to ../LeapYeet-firmware relative to this script.
#   IMAGE         Container image to run. Defaults to ff-builder.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FIRMWARE_SRC="${FIRMWARE_SRC:-$SCRIPT_DIR/../LeapYeet-firmware}"
OUTPUT_DIR="$SCRIPT_DIR/firmware/heltec_t114"
IMAGE="${IMAGE:-ff-builder}"

if [[ ! -d "$FIRMWARE_SRC" ]]; then
  echo "ERROR: firmware source not found at $FIRMWARE_SRC" >&2
  echo "Clone it first:" >&2
  echo "  git clone --recurse-submodules https://github.com/LeapYeet/firmware.git $FIRMWARE_SRC" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"

podman run --rm \
  -v "$FIRMWARE_SRC":/firmware-src:Z \
  -v "$OUTPUT_DIR":/output:Z \
  "$IMAGE"

echo
echo "Artifact: $OUTPUT_DIR/firmware.uf2"
