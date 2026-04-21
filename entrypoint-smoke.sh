#!/bin/bash
# ff-builder-native container entrypoint for the two-node smoke test.
#
# Patches the firmware tree, builds env:native, then runs
# tests/smoke/two_node_smoke.py against the linked program binary.
# Writes per-node logs + smoke output to /output if /output is
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
pio run --environment native

BIN="$SRC/.pio/build/native/program"
WORKDIR=/tmp/ff-smoke-work

echo "=== Running two-node smoke test ==="
PAIRING_WORKDIR=/tmp/ff-pairing-work
if [[ -d /output ]]; then
  python3 /usr/local/bin/two_node_smoke.py \
    --program "$BIN" \
    --workdir "$WORKDIR" 2>&1 | tee /output/smoke.log
  cp "$WORKDIR"/node-a.log /output/node-a.log 2>/dev/null || true
  cp "$WORKDIR"/node-b.log /output/node-b.log 2>/dev/null || true
else
  python3 /usr/local/bin/two_node_smoke.py --program "$BIN" --workdir "$WORKDIR"
fi

echo "=== Running FriendFinder pairing integration test ==="
if [[ -d /output ]]; then
  python3 /usr/local/bin/pairing_test.py \
    --program "$BIN" \
    --workdir "$PAIRING_WORKDIR" 2>&1 | tee /output/pairing.log
  cp "$PAIRING_WORKDIR"/node-a.log /output/pairing-node-a.log 2>/dev/null || true
  cp "$PAIRING_WORKDIR"/node-b.log /output/pairing-node-b.log 2>/dev/null || true
else
  python3 /usr/local/bin/pairing_test.py --program "$BIN" --workdir "$PAIRING_WORKDIR"
fi

echo "=== All integration tests complete ==="
