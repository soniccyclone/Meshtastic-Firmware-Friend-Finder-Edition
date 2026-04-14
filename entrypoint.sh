#!/bin/bash
# ff-builder container entrypoint.
# Assumes /firmware-src is a bind-mounted LeapYeet/firmware checkout
# and /output is a bind-mounted directory for the built artifact.
set -euo pipefail

cd /firmware-src

echo "=== Patching T114 variant ==="
python3 /usr/local/bin/patch-t114.py

echo "=== Building heltec-mesh-node-t114 ==="
pio run --environment heltec-mesh-node-t114

echo "=== Copying artifact ==="
cp .pio/build/heltec-mesh-node-t114/firmware.uf2 /output/firmware.uf2
echo "Done: /output/firmware.uf2"
