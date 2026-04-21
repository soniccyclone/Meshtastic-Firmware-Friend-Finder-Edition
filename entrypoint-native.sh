#!/bin/bash
# ff-builder-native container entrypoint.
#
# Uses /firmware-src if bind-mounted, otherwise falls back to the baked-in
# clone at /firmware-src-baked. Writes build.log to /output if /output is
# bind-mounted; otherwise logs only to the container stdout.
set -euo pipefail

if [[ -d /firmware-src ]]; then
  SRC=/firmware-src
else
  SRC=/firmware-src-baked
fi

cd "$SRC"

echo "=== Patching native (Portduino) variant ==="
python3 /usr/local/bin/patch-native.py

echo "=== Building env:native ==="
if [[ -d /output ]]; then
  pio run --environment native 2>&1 | tee /output/build.log
else
  pio run --environment native
fi

echo "=== Done ==="
