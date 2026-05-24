# Friend Finder Firmware — Fork Setup Plan

## Context

This repo is a fork of `meshtastic/firmware`. It adds Friend Finder functionality to the
Heltec Mesh Node T114 (nRF52840 + SX1262). The previous implementation (`soniccyclone/Meshtastic-Firmware-Friend-Finder-Edition`) was a Python script that patched a LeapYeet firmware clone at build time — that approach is dead. This is the clean replacement: real C++ checked into a real fork.

**Reference repos (read-only, never pull from automatically):**
- https://github.com/meshtastic/firmware — upstream, NEVER merge
- https://github.com/LeapYeet/firmware/tree/f49f9b7967311a08c7bf1c1af6e8f28671182cd1 — T114 board support reference and source of our existing module code (pinned SHA)
- https://github.com/meshtastic/Adafruit_nRF52_Arduino/tree/e13f5820002a4fb2a5e6754b42ace185277e5adf — the nRF52 Arduino framework package at the SHA LeapYeet pinned; Wire_nRF52.cpp lives here

---

## Step 1: Check What's Already Here

Before doing anything, audit what Meshtastic upstream already has:

```bash
ls variants/nrf52840/
```

If `heltec_mesh_node_t114/` exists, great — we have T114 support already. If not, we need to copy it
from LeapYeet. Either way, proceed to Step 2.

---

## Step 2: Add T114 Variant (if not present)

If `variants/nrf52840/heltec_mesh_node_t114/` is missing, copy it from LeapYeet at the pinned SHA:

Source: https://github.com/LeapYeet/firmware/tree/f49f9b7967311a08c7bf1c1af6e8f28671182cd1/variants/nrf52840/heltec_mesh_node_t114

```bash
git clone https://github.com/LeapYeet/firmware.git /tmp/leapyeet
cd /tmp/leapyeet && git checkout f49f9b7967311a08c7bf1c1af6e8f28671182cd1
cp -r /tmp/leapyeet/variants/nrf52840/heltec_mesh_node_t114 variants/nrf52840/
```

Then check and clean up the `platformio.ini` inside that variant directory — remove any LeapYeet-specific
comments and make sure the build flags match what we actually need:

```ini
[env:heltec-mesh-node-t114]
lib_ignore = Adafruit BluefruitLE nRF51
extends = nrf52840_base
board = heltec_mesh_node_t114
board_level = pr
debug_tool = jlink

build_flags = ${nrf52840_base.build_flags}
  -Ivariants/nrf52840/heltec_mesh_node_t114
  -DGPS_POWER_TOGGLE
  -DHELTEC_T114
  -DSS=0
  -DI2C0_SDA_PIN=PIN_WIRE_SDA
  -DI2C0_SCL_PIN=PIN_WIRE_SCL
  -DI2C1_SDA_PIN=PIN_WIRE1_SDA
  -DI2C1_SCL_PIN=PIN_WIRE1_SCL

build_src_filter = ${nrf52_base.build_src_filter} +<../variants/nrf52840/heltec_mesh_node_t114>
lib_deps =
  ${nrf52840_base.lib_deps}
  lewisxhe/PCF8563_Library@^1.0.1
  https://github.com/meshtastic/st7789/archive/bd33ea58ddfe4a5e4a66d53300ccbd38d66ac21f.zip
```

Verify it builds before touching anything else:
```bash
pio run --environment heltec-mesh-node-t114
```

---

## Step 3: Add Our Custom Modules

Copy our module files from LeapYeet at the pinned SHA. These are our modules — they originated
in this project, they just lived in the wrong repo.

Sources:
- https://github.com/LeapYeet/firmware/blob/f49f9b7967311a08c7bf1c1af6e8f28671182cd1/src/modules/FriendFinderModule.cpp
- https://github.com/LeapYeet/firmware/blob/f49f9b7967311a08c7bf1c1af6e8f28671182cd1/src/modules/FriendFinderModule.h
- https://github.com/LeapYeet/firmware/blob/f49f9b7967311a08c7bf1c1af6e8f28671182cd1/src/modules/MagnetometerModule.cpp
- https://github.com/LeapYeet/firmware/blob/f49f9b7967311a08c7bf1c1af6e8f28671182cd1/src/modules/MagnetometerModule.h

```bash
git clone https://github.com/LeapYeet/firmware.git /tmp/leapyeet  # skip if already done
cd /tmp/leapyeet && git checkout f49f9b7967311a08c7bf1c1af6e8f28671182cd1
cp /tmp/leapyeet/src/modules/FriendFinderModule.cpp src/modules/
cp /tmp/leapyeet/src/modules/FriendFinderModule.h   src/modules/
cp /tmp/leapyeet/src/modules/MagnetometerModule.cpp src/modules/
cp /tmp/leapyeet/src/modules/MagnetometerModule.h   src/modules/
```

Also copy the generated protobuf files Friend Finder depends on:

Sources:
- https://github.com/LeapYeet/firmware/blob/f49f9b7967311a08c7bf1c1af6e8f28671182cd1/src/mesh/generated/meshtastic/friendfinder.pb.cpp
- https://github.com/LeapYeet/firmware/blob/f49f9b7967311a08c7bf1c1af6e8f28671182cd1/src/mesh/generated/meshtastic/friendfinder.pb.h

```bash
cp /tmp/leapyeet/src/mesh/generated/meshtastic/friendfinder.pb.cpp src/mesh/generated/meshtastic/
cp /tmp/leapyeet/src/mesh/generated/meshtastic/friendfinder.pb.h   src/mesh/generated/meshtastic/
```

Also check for the MenuHandler changes — Friend Finder hooks into `src/graphics/draw/MenuHandler.cpp`.
Diff the LeapYeet version against what's here and apply only our additions (Friend Finder menu
entries, Track a Friend, Saved Places, Compass Cal). Do not blindly overwrite — Meshtastic
upstream may have moved ahead.

LeapYeet reference: https://github.com/LeapYeet/firmware/blob/f49f9b7967311a08c7bf1c1af6e8f28671182cd1/src/graphics/draw/MenuHandler.cpp

```bash
diff /tmp/leapyeet/src/graphics/draw/MenuHandler.cpp src/graphics/draw/MenuHandler.cpp
```

Verify build still passes after adding modules.

---

## Step 4: Fix Wire_nRF52.cpp (the device freeze bug)

### Root Cause

A user reported their device freezing. The log ends mid-operation with no error — the device
stopped responding entirely. The cause is `Wire_nRF52.cpp` from `framework-arduinoadafruitnrf52`,
which uses bare TWIM spin loops with no timeout:

```cpp
// endTransmission() — three loops, no timeout
while(!_p_twim->EVENTS_TXSTARTED && !_p_twim->EVENTS_ERROR);
while(!_p_twim->EVENTS_LASTTX    && !_p_twim->EVENTS_ERROR);
while(!_p_twim->EVENTS_STOPPED);   // no ERROR check here either

// requestFrom() — three more loops, same problem
while(!_p_twim->EVENTS_RXSTARTED && !_p_twim->EVENTS_ERROR);
while(!_p_twim->EVENTS_LASTRX   && !_p_twim->EVENTS_ERROR);
while(!_p_twim->EVENTS_STOPPED);
```

When the QMC5883L magnetometer's TWIM peripheral gets stuck (LoRa RF noise is the likely
trigger), no events ever fire. The CPU spins forever. The device freezes. The log ends exactly
there because the device is completely hung — not crashed, not rebooted, just frozen.

Any fix that lives above the Wire layer (e.g. a failure counter in MagnetometerModule) does
not help — `qmcReadRaw` never returns, so code above it never runs.

### The Fix

Copy `Wire_nRF52.cpp` from the nRF52 Arduino framework into `lib/Wire/` and add
`millis()`-based timeouts to all six spin loops. PlatformIO automatically prefers files in
the project `lib/` directory over the framework package — no build system hacks needed, and
the fixed file is visible in this repo as real C++.

Source (the exact SHA LeapYeet/Meshtastic pins for the nRF52 Arduino framework):
https://github.com/meshtastic/Adafruit_nRF52_Arduino/blob/e13f5820002a4fb2a5e6754b42ace185277e5adf/libraries/Wire/Wire_nRF52.cpp

```bash
mkdir -p lib/Wire
# Either download directly from the URL above, or grab from the local PlatformIO cache:
find ~/.platformio -name "Wire_nRF52.cpp" 2>/dev/null
cp <found path> lib/Wire/Wire_nRF52.cpp
```

### Timeout Implementation

For each of the six spin loops, replace the bare spin with a deadline check. On timeout,
abort the TWIM transaction cleanly before returning an error.

Pattern for loops that check ERROR (four of the six loops):

```cpp
// BEFORE:
while(!_p_twim->EVENTS_TXSTARTED && !_p_twim->EVENTS_ERROR);

// AFTER:
{
    const uint32_t _deadline = millis() + 50;
    while (!_p_twim->EVENTS_TXSTARTED && !_p_twim->EVENTS_ERROR) {
        if (millis() > _deadline) {
            // abort and return error — see abort sequence below
            _twim_force_reset();
            return <error value>;
        }
    }
}
```

Pattern for `EVENTS_STOPPED` loops (two of the six — these have no ERROR check in the original):

```cpp
// BEFORE:
while(!_p_twim->EVENTS_STOPPED);

// AFTER:
{
    const uint32_t _deadline = millis() + 50;
    while (!_p_twim->EVENTS_STOPPED) {
        if (millis() > _deadline) {
            _twim_force_reset();
            return <error value>;
        }
    }
}
```

### TWIM Abort Sequence

Add a static helper (or inline the sequence) that cleanly resets the peripheral:

```cpp
static void _twim_force_reset(NRF_TWIM_Type *twim) {
    // Request stop
    twim->TASKS_STOP = 1;
    // Wait up to 10ms for a clean stop
    const uint32_t deadline = millis() + 10;
    while (!twim->EVENTS_STOPPED && millis() < deadline) {}
    // If still not stopped, force-disable the peripheral
    if (!twim->EVENTS_STOPPED) {
        twim->ENABLE = 0;  // disable TWIM
        twim->ENABLE = 6;  // re-enable TWIM (value 6 = enabled)
    }
    // Clear all events
    twim->EVENTS_STOPPED   = 0;
    twim->EVENTS_ERROR     = 0;
    twim->EVENTS_TXSTARTED = 0;
    twim->EVENTS_RXSTARTED = 0;
    twim->EVENTS_LASTTX    = 0;
    twim->EVENTS_LASTRX    = 0;
}
```

### Return Values

- `endTransmission()` returns `int`: return `4` on timeout (Wire convention for "other error")
- `requestFrom()` returns `uint8_t` (byte count): return `0` on timeout

The magnetometer's `qmcReadRegs()` checks the return of `endTransmission()` and `requestFrom()`
already — it returns false on any failure, which causes `runOnce()` to schedule a 100ms retry.
Once Wire stops hanging, the QMC reinitializes on the next cycle automatically.

### Verify

Build must pass cleanly. Then flash and confirm the device no longer freezes when the
magnetometer encounters I2C errors.

---

## Step 5: Set Up CI

Create `.github/workflows/build.yml`:

```yaml
name: Build T114 Firmware

on:
  push:
    branches: [main, 'ff-*']
  pull_request:
    branches: [main]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          submodules: recursive

      - uses: actions/cache@v4
        with:
          path: ~/.platformio
          key: pio-${{ hashFiles('platformio.ini', 'variants/nrf52840/heltec_mesh_node_t114/platformio.ini') }}

      - name: Install PlatformIO
        run: pip install platformio

      - name: Build
        run: pio run --environment heltec-mesh-node-t114

      - name: Upload firmware
        uses: actions/upload-artifact@v4
        with:
          name: firmware-t114
          path: .pio/build/heltec-mesh-node-t114/firmware.uf2
```

Releases: add a separate `release.yml` that triggers on version tags and publishes `firmware.uf2`
as a GitHub release asset under `ghcr.io/soniccyclone/`.

---

## Step 6: Local Build Script

Create `build.sh` at repo root:

```bash
#!/bin/bash
set -euo pipefail
pio run --environment heltec-mesh-node-t114
echo "Artifact: .pio/build/heltec-mesh-node-t114/firmware.uf2"
```

No Docker required for local builds — PlatformIO handles the toolchain directly.
If Docker isolation is still wanted, the Dockerfile becomes:

```dockerfile
FROM ubuntu:24.04
RUN apt-get update && apt-get install -y python3 python3-pip git && rm -rf /var/lib/apt/lists/*
RUN pip3 install platformio
WORKDIR /firmware
COPY . .
RUN git submodule update --init --recursive
RUN pio pkg install --environment heltec-mesh-node-t114
CMD ["pio", "run", "--environment", "heltec-mesh-node-t114"]
```

---

## Step 7: Commit Structure

Keep a clean commit history. Suggested initial commits in order:

1. `seed: Meshtastic firmware fork base` — the fork itself
2. `feat: add Heltec T114 variant` — Step 2 (if needed)
3. `feat: add FriendFinder and Magnetometer modules` — Step 3
4. `fix: shadow Wire_nRF52 with timeout-safe TWIM spin loops` — Step 4
5. `ci: add GitHub Actions build for heltec-mesh-node-t114` — Step 5

---

## What's NOT Here (Deferred)

- Protobuf `.proto` source for `friendfinder.pb.h` — confirm whether the generated file is sufficient
  or if we need to add the `.proto` to the build
- Native Linux build (`Dockerfile.native`, `patch-native.py` equivalent) — drop unless there's
  a specific need for it
- Beads issue tracker integration — set that up in the new repo once the build is green

---

## Reference: Old Repo

`soniccyclone/Meshtastic-Firmware-Friend-Finder-Edition` — the Python patcher repo.
Read it for historical context on what patches were applied and why.
Do not copy `patch-t114.py` or any `.py` patch files — the whole point is they're gone.
The commit history there shows what each patch did; translate those into normal C++ commits here.
