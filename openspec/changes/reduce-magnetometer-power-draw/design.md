# Design: reduce-magnetometer-power-draw

## Context

`MagnetometerModule` runs as an `OSThread` and currently:

- Configures the QMC5883L for 200 Hz / OSR=512 continuous-mode sampling — the highest-current setting the part offers.
- Lets the Adafruit LIS3DH driver fall through to its 400 Hz / normal-power default because no `setDataRate` / `setPerformanceMode` is called after `begin()`.
- Polls the QMC at ~20 Hz (`runOnce` returns `50` ms).
- Runs the Madgwick fusion at 20 Hz (`filter.begin(20)`).

End-to-end the heading is consumed by the FriendFinder compass renderer, which redraws at display-frame rates (well below 10 Hz). The only consumers of the high-rate sensor data are the EMA smoother and the Madgwick filter inside `MagnetometerModule` itself; both are happy with 10 Hz input. So the sensors are running 20× faster than the slowest downstream rate, paying current for samples that get averaged together and never seen.

The fork ships on a 2000 mAh pack — meaningfully smaller than the 3500 mAh+ packs the upstream Heltec V4 baseline assumed. We are seeing field crashes correlated with battery age, the fingerprint of a brown-out under transient load. Reducing continuous-mode sensor current is the cheapest knob to turn.

## Goals

- Drop QMC5883L and LIS3DH continuous current draw to the lowest setting that still feeds the existing 10–20 Hz heading pipeline cleanly.
- Keep the change as a build-time patch over upstream LeapYeet/firmware, idempotent and marker-guarded, matching the existing patch discipline in `patch-t114.py` / `patch-native.py`.
- No API change. `magnetometerModule->getHeading()` / `hasHeading()` / calibration entry points all continue to work bit-identically from FriendFinder's perspective.

## Non-goals

- **On-demand wake/sleep gating** (putting the QMC into MODE=standby when no consumer needs heading). This is the architecturally correct end state — the magnetometer only matters when the user is in the FriendFinder tracking view or running compass cal, which is a small fraction of session time — but it requires an `setActive()` API on `MagnetometerModule`, lifecycle hooks in `FriendFinderModule`, and care around the QMC's ~10 ms warm-up at re-entry. Out of scope for this proposal; tracked separately if Pass A doesn't close enough margin to stop the field crashes.
- Tuning the EMA smoother's `emaAlpha` ([MagnetometerModule.cpp:138](../../../code-stuff/LeapYeet-firmware/src/modules/MagnetometerModule.cpp#L138)) for the new sample rate. Will revisit if the on-device compass needle becomes visibly jittery; the change direction (lower rate → more responsive smoother) is desired, so we let it ride at the default and observe.
- Touching upstream. All changes go through the patch scripts. We do not fork `MagnetometerModule.cpp`.

## Decisions

### D1 — QMC5883L CTRL1 value: `0xD1` (OSR=64, RNG=2G, ODR=10 Hz, MODE=continuous)

The QMC5883L CTRL1 layout, per the QST datasheet:

```
bits[7:6] OSR   00=512  01=256  10=128  11=64
bits[5:4] RNG   00=2G   01=8G        (other bits reserved)
bits[3:2] ODR   00=10Hz 01=50Hz  10=100Hz  11=200Hz
bits[1:0] MODE  00=standby  01=continuous
```

Current `0x1D` = `00 01 11 01` = OSR=512, RNG=2G, ODR=200 Hz, continuous. Lowest-current operating point that still gives us continuous samples is `0xD1` = `11 01 00 01` = OSR=64, RNG=2G, ODR=10 Hz, continuous. RNG stays at 2G (= 8 Gauss full scale) because Earth's field is well under 1 G; we have headroom to spare. OSR=64 trades a small amount of per-sample noise for current; the EMA smoother already does additional averaging downstream, so noise floor at the heading output is dominated by environmental factors (steel objects, motors), not sensor OSR.

### D2 — LIS3DH: 25 Hz, low-power mode, default 2G range

Madgwick is updated at 10 Hz (post-D4); LIS3DH at 25 Hz means each filter step gets a fresh accel sample with margin for I2C jitter. `LIS3DH_DATARATE_25_HZ` is the closest standard rate above 10 Hz. `LIS3DH_MODE_LOW_POWER` enables 8-bit-per-axis sampling internally, which the part documents as a several-fold current reduction; tilt-compensation accuracy is not affected at the levels the heading filter cares about. Range stays at the driver default `LIS3DH_RANGE_2_G` because tilt comp only needs to know which way is down.

### D3 — Poll period: 100 ms

With QMC ODR=10 Hz the chip has a fresh sample every 100 ms. Polling faster re-reads the same sample (the QMC latches; it doesn't oversample on read), so the only effect of staying at 50 ms would be doubled I2C bus traffic and CPU wakeups — exactly what we are trying to avoid. Match poll period to ODR.

### D4 — Madgwick rate: 10 Hz

`filter.begin(N)` configures the Madgwick filter's expected sample rate; the filter integrates gyro/accel/mag at that rate to produce orientation. Keeping it at 20 Hz when the inputs arrive at 10 Hz means each filter step under-integrates by 2×, which warps the time constant of orientation convergence. Match it to the sample rate.

### D5 — Idempotent string-replace patches, two anchor strings per block

Same shape as `friend-finder-menu-ordering`: each new block in `patch-t114.py` looks for a stable, unique substring in the upstream file, replaces it, and skips if the replacement marker is already present. Three patch blocks:

1. **MAG_CTRL1 block**: anchor on `if (!qmcWriteReg(bus, addr, QMC_REG_CTRL1, 0x1D)) {`. Replace the `0x1D` literal with `0xD1` and rewrite the immediately-following `LOG_INFO("[Magnetometer] QMC configured (CONT mode, 200Hz, 2G, OSR512).")` string to reflect the new values.
2. **LIS3DH block**: anchor on the `LOG_INFO("[Magnetometer] LIS3DH detected on Wire1. Start Madgwick @20 Hz.")` line and rewrite the `if (haveAccel) { ... filter.begin(20); }` body to also call `lis.setDataRate(LIS3DH_DATARATE_25_HZ)`, `lis.setPerformanceMode(LIS3DH_MODE_LOW_POWER)` before `filter.begin(10)`. Single block covers both LIS3DH config and the Madgwick rate change because they share an `if (haveAccel)` body — atomically reasoning about both keeps the patch readable.
3. **runOnce return block**: anchor on the trailing `return 50;` at end of `runOnce` (unique within the file when paired with the surrounding context line); replace with `return 100;`.

Each block emits a `// ff-builder: magnetometer power profile` marker in the patched output so re-runs of the patch script see the marker and skip.

### D6 — `patch-native.py` mirrors `patch-t114.py`

The native (Portduino) build does not run the magnetometer code path (`MagnetometerModule.cpp` is `#if !defined(ARCH_PORTDUINO)`-guarded), so the patches have no runtime effect under native — but the strings still exist in the source tree, and the smoke build compiles them. The native patch script must produce a tree byte-identical to the T114 patch script's output for the magnetometer file, otherwise we have a bifurcation between what smoke tests check and what ships. Apply the same three blocks in both scripts.

## Risks & Mitigations

- **EMA smoother behaves differently at 10 Hz.** The smoother's `emaAlpha = 0.2f` ([MagnetometerModule.cpp:138](../../../code-stuff/LeapYeet-firmware/src/modules/MagnetometerModule.cpp#L138)) is a per-sample weight; at 20 Hz it gives a ~250 ms time constant, at 10 Hz ~500 ms. Heading needle becomes more sluggish but smoother. Mitigation: ship as-is and observe on-device. If sluggish, retune `emaAlpha` upward in a follow-up — that's a one-line change with the same patch shape.
- **OSR=64 vs OSR=512 noise floor.** OSR=64 gives ~3× more per-sample sigma than OSR=512. EMA smoother continues to do its job; figure-8 calibration's min/max windowing is also robust to per-sample noise (a few sigma off the actual extreme moves the bias estimate by a fraction of a degree). No expected user-visible degradation. Mitigation: revisit if calibration accuracy regresses noticeably; can step OSR back up to 256 (CTRL1 = `0x91`) if needed.
- **QMC continuous-mode current is still nonzero.** Even at ODR=10 Hz the part draws a few-µA continuous current. The "right" answer is to standby it when not in use; that's deferred (see Non-goals). Mitigation: if Pass A doesn't close the brown-out margin enough, do Pass B (on-demand wake/sleep) as a follow-up change.
- **LIS3DH low-power-mode 8-bit resolution affects tilt comp.** 8-bit gravity-vector estimation has roughly 30 mg resolution; at typical tilt angles the heading impact is sub-degree. Heading users won't notice. Mitigation: if a future use case (e.g. a step counter) needs higher accel resolution, that consumer can call `setPerformanceMode` itself when it activates.

## Open Questions

*(none — all values traceable to datasheet bits, library headers, and existing source.)*
