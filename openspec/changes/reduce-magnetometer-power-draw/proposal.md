# Reduce magnetometer power draw

## Why

GitHub issue [#32](https://github.com/soniccyclone/Meshtastic-Firmware-Friend-Finder-Edition/issues/32): we are seeing intermittent crashes on identical devices in the field, correlated with battery age — the device powered by the freshest pack basically never crashes, while older packs crash often. Symptom shape is consistent with a brown-out under transient load, not a software bug. The fork inherits sensor-init values from the upstream Heltec V4 setup (3500 mAh+ pack budget); we're deploying onto a 2000 mAh Maker Nova LiPo, so the sensor subsystem's continuous current draw eats a much larger fraction of the budget and the brown-out margin is thin.

Two specific values are needlessly aggressive for a heading consumer that is downsampled to 20 Hz before display:

1. **QMC5883L magnetometer** is configured with ODR=200 Hz and OSR=512 ([MagnetometerModule.cpp:129](../../../code-stuff/LeapYeet-firmware/src/modules/MagnetometerModule.cpp#L129) writes `CTRL1 = 0x1D`). Per the QMC5883L datasheet, that combination is the highest-current operating point the part offers; the lowest (ODR=10 Hz, OSR=64, written as `CTRL1 = 0xD1`) is roughly an order of magnitude lower for a part that's continuously powered.
2. **LIS3DH accelerometer** is initialized via `lis.begin()` with no follow-up `setDataRate` / `setPerformanceMode` calls ([MagnetometerModule.cpp:227-231](../../../code-stuff/LeapYeet-firmware/src/modules/MagnetometerModule.cpp#L227-L231)), so the Adafruit driver default kicks in: 400 Hz output rate, normal-power mode ([Adafruit_LIS3DH.cpp:138](../../../code-stuff/LeapYeet-firmware/.pio/libdeps/heltec-mesh-node-t114/Adafruit%20LIS3DH/Adafruit_LIS3DH.cpp#L138) calls `setDataRate(LIS3DH_DATARATE_400_HZ)`). The Madgwick filter is fed at 20 Hz and the AHRS output is ultimately consumed by FriendFinder's compass at human-readable rates — 400 Hz is throwing away samples for no benefit.

Both knobs are pure register writes — no API surface change, no behavior change visible to FriendFinder or Compass Cal, no new dependencies. Drop both to the lowest setting that still cleanly feeds the existing 20 Hz fusion path.

## What Changes

- **QMC5883L CTRL1** ([MagnetometerModule.cpp:128-132](../../../code-stuff/LeapYeet-firmware/src/modules/MagnetometerModule.cpp#L128-L132) inside `MagnetometerModule::qmcInit`): change the written value from `0x1D` (OSR=512, RNG=2G, ODR=200 Hz, MODE=continuous) to `0xD1` (OSR=64, RNG=2G, ODR=10 Hz, MODE=continuous). Update the adjacent `LOG_INFO` string so the diagnostic line reflects the new configuration.
- **LIS3DH config** ([MagnetometerModule.cpp:226-238](../../../code-stuff/LeapYeet-firmware/src/modules/MagnetometerModule.cpp#L226-L238) inside `MagnetometerModule::initSensors`, immediately after the `lis.begin(...)` calls succeed): call `lis.setDataRate(LIS3DH_DATARATE_25_HZ)` and `lis.setPerformanceMode(LIS3DH_MODE_LOW_POWER)`. Range stays at the driver default (`LIS3DH_RANGE_2_G`) because a heading sensor never cares about ±4 g, let alone ±16 g.
- **Sensor poll period** ([MagnetometerModule.cpp:497](../../../code-stuff/LeapYeet-firmware/src/modules/MagnetometerModule.cpp#L497) `runOnce` tail): change `return 50` to `return 100`. With ODR=10 Hz the QMC produces a fresh sample every 100 ms; polling faster just re-reads the same sample and burns I2C bus time.
- **Madgwick fusion rate** ([MagnetometerModule.cpp:236](../../../code-stuff/LeapYeet-firmware/src/modules/MagnetometerModule.cpp#L236)): `filter.begin(20)` → `filter.begin(10)` so the filter's internal time-step matches the new ~10 Hz sample arrival.
- Ships as new marker-guarded blocks in `patch-t114.py` and `patch-native.py`, matching the existing patch-architecture discipline. No fork of upstream.

## Capabilities

### New Capabilities

- `magnetometer-power-profile`: normative requirements for the QMC5883L and LIS3DH initialization values used on Friend-Finder-Edition builds. Encodes the "match the ~10 Hz consumer rate; do not run sensors at maximum data rate" intent so future edits to `MagnetometerModule.cpp` (including upstream pulls) don't quietly regress to 200 Hz / 400 Hz defaults.

### Modified Capabilities

*(none)*

## Impact

- **Patch infrastructure**: two new marker-guarded blocks in `patch-t114.py` (one rewriting the `CTRL1 = 0x1D` write + log; one inserting the LIS3DH `setDataRate` / `setPerformanceMode` calls) plus a third small block adjusting the `runOnce` return and `filter.begin` arg, and the matching blocks in `patch-native.py` so smoke tests build the same code path. Same shape as existing patches — anchor on stable strings in upstream, fail loudly if anchors disappear.
- **Build**: no new dependencies. Pure register-value and API-arg changes; the LIS3DH constants used (`LIS3DH_DATARATE_25_HZ`, `LIS3DH_MODE_LOW_POWER`) are already present in the Adafruit_LIS3DH header bundled with the firmware (see [Adafruit_LIS3DH.h:341-356](../../../code-stuff/LeapYeet-firmware/.pio/libdeps/heltec-mesh-node-t114/Adafruit%20LIS3DH/Adafruit_LIS3DH.h#L341-L356)).
- **Runtime cost**: power draw drops, observably; user-visible heading behavior is unchanged on the FriendFinder compass (downsampled to display-frame rates well below 10 Hz already). Calibration UX is unchanged: figure-8 cal collects ~150 samples over 15 s instead of ~3000, and flat-spin cal collects ~120 samples over 12 s — both well above the existing `nXY >= 25` lower bound check ([MagnetometerModule.cpp:370](../../../code-stuff/LeapYeet-firmware/src/modules/MagnetometerModule.cpp#L370)).
- **Test surface**: existing `entrypoint-smoke.sh` build verifies the patches apply and compile. Manual on-device verification: confirm FriendFinder compass still tracks heading smoothly after the change, run figure-8 + flat-spin cal once each, then leave the device on a 2000 mAh pack overnight with the screen off and confirm it survives without the brown-out crash pattern.
- **Risk**: low. The biggest realistic failure mode is ODR=10 Hz interacting badly with the EMA smoother in `runOnce` ([MagnetometerModule.cpp:474-487](../../../code-stuff/LeapYeet-firmware/src/modules/MagnetometerModule.cpp#L474-L487)) — the smoother's `emaAlpha = 0.2f` was tuned implicitly against 20 Hz updates. Lower update rate makes the smoother more responsive in real time (fewer samples per second to average over), which is the desired direction; if it overshoots and the heading needle becomes jittery on-device, lower `emaAlpha` in a follow-up. Not addressed in this change.
