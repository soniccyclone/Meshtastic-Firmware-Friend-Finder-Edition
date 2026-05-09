# Tasks: reduce-magnetometer-power-draw

## 1. Patch infrastructure

- [ ] 1.1 Add `MAG_POWER_MARKER = "// ff-builder: magnetometer power profile"` near the existing markers in `patch-t114.py`.
- [ ] 1.2 Add a `patch_magnetometer_qmc_ctrl1()` function in `patch-t114.py` that opens `src/modules/MagnetometerModule.cpp`, anchors on `if (!qmcWriteReg(bus, addr, QMC_REG_CTRL1, 0x1D)) {`, rewrites the `0x1D` literal to `0xD1` (with the marker comment on the line above), and rewrites the adjacent `LOG_INFO("[Magnetometer] QMC configured (CONT mode, 200Hz, 2G, OSR512).")` to `LOG_INFO("[Magnetometer] QMC configured (CONT mode, 10Hz, 2G, OSR64).")`. Idempotent: if the marker is already present, print "skipped" and return.
- [ ] 1.3 Add a `patch_magnetometer_lis3dh_lowpower()` function in `patch-t114.py` that anchors on the `if (haveAccel) {` body containing `filter.begin(20);`, and rewrites the body to call `lis.setDataRate(LIS3DH_DATARATE_25_HZ);` and `lis.setPerformanceMode(LIS3DH_MODE_LOW_POWER);` before `filter.begin(10);`. Update the adjacent `LOG_INFO` string to reflect the new rate. Idempotent on the marker.
- [ ] 1.4 Add a `patch_magnetometer_poll_period()` function in `patch-t114.py` that anchors on `return 50;` paired with a unique surrounding line (the preceding `lastLogMs = now;\n        }\n` block) to disambiguate from any other `return 50;` in the file, and replaces it with `return 100;` plus the marker. Idempotent on the marker.
- [ ] 1.5 Wire the three new patch functions into `main()` in `patch-t114.py`, after the existing patch calls.
- [ ] 1.6 Mirror tasks 1.1–1.5 in `patch-native.py` so the Portduino smoke build sees the same source tree.
- [ ] 1.7 From a freshly-cloned upstream tree at `$FIRMWARE_SRC`, run `python3 patch-t114.py` once and confirm: (a) the three new blocks each print a "Patched" line; (b) `MagnetometerModule.cpp` now contains `0xD1`, `LIS3DH_DATARATE_25_HZ`, `LIS3DH_MODE_LOW_POWER`, `filter.begin(10)`, and `return 100;`; (c) running it a second time prints three "skipped" lines and produces no further file modifications (`git diff` is empty).
- [ ] 1.8 Same idempotency check for `patch-native.py` against a freshly-cloned upstream tree.

## 2. Build verification

- [ ] 2.1 `make clean && make build` succeeds end-to-end and produces `firmware/heltec_t114/firmware.uf2`.
- [ ] 2.2 Run the existing native smoke build (`entrypoint-smoke.sh` path) and confirm it compiles cleanly with the patches applied.
- [ ] 2.3 Inspect the build log for the new `LOG_INFO` lines (`QMC configured (CONT mode, 10Hz, 2G, OSR64)` and the updated LIS3DH line) — confirm they reach the compiler unmangled.

## 3. On-device verification (heltec T114, 2000 mAh Maker Nova LiPo)

- [ ] 3.1 Flash the patched UF2. Boot the device and confirm the `[Magnetometer]` log lines on serial show the new configuration strings (10 Hz QMC, LIS3DH at 25 Hz low-power).
- [ ] 3.2 Open FriendFinder → Track a Friend with at least one paired peer. Confirm the compass needle still tracks heading smoothly as the device is rotated. The needle should look sluggish-but-stable, not jittery.
- [ ] 3.3 Run figure-8 calibration once. Confirm it completes in 15 s and the post-cal `[Magnetometer] Calibration DONE` line shows non-degenerate bias/scale values (similar to a pre-change run).
- [ ] 3.4 Run flat-spin calibration once. Confirm it completes in 12 s and the `Flat-spin DONE. n=...` line reports `n >= 100` samples (well above the `nXY >= 25` threshold).
- [ ] 3.5 Power-burn test: charge the 2000 mAh Maker Nova pack to full, leave the device idle (screen off, no FriendFinder activity) for at least 12 hours, and confirm it survives without the brown-out crash pattern that motivated the change.

## 4. Capability spec

- [ ] 4.1 Verify that `openspec/changes/reduce-magnetometer-power-draw/specs/magnetometer-power-profile/spec.md` exists with the ADDED requirements covering CTRL1 value, LIS3DH config, poll period, fusion rate, and patch idempotency.
- [ ] 4.2 After the change archives, the spec content moves to `openspec/specs/magnetometer-power-profile/spec.md` (handled by the archive workflow; not a manual step here).
