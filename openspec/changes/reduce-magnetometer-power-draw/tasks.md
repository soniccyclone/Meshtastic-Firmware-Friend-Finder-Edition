# Tasks: reduce-magnetometer-power-draw

## 1. Patch infrastructure

- [x] 1.1 Add `MAG_POWER_MARKER = "// ff-builder: magnetometer power profile"` near the existing markers in `patch-t114.py`.
- [x] 1.2 Add a CTRL1-block patch in `patch-t114.py` that anchors on the `if (!qmcWriteReg(bus, addr, QMC_REG_CTRL1, 0x1D)) {` block and the adjacent post-config `LOG_INFO`, rewriting `0x1D` → `0xD1` (with marker comment above) and `200Hz/2G/OSR512` → `10Hz/2G/OSR64` in the log string. (Implemented as part of `patch_magnetometer_power_profile()` — kept as one function rather than three so the four anchor checks fail loudly together if upstream drifts.) Idempotent on the marker.
- [x] 1.3 In the same `patch_magnetometer_power_profile()`, anchor on the `if (haveAccel) {` body containing `filter.begin(20);`, and rewrite to call `lis.setDataRate(LIS3DH_DATARATE_25_HZ);` and `lis.setPerformanceMode(LIS3DH_MODE_LOW_POWER);` before `filter.begin(10);`. LOG_INFO text updated.
- [x] 1.4 Same function: anchor on the `lastLogMs = now;\n    }\n\n    return 50;\n}` runOnce-tail block (verified `return 50;` is unique in the file) and replace `return 50;` with `return 100;` plus the marker comment.
- [x] 1.5 Wire `patch_magnetometer_power_profile()` into `main()` in `patch-t114.py`, after the existing patch calls.
- [x] 1.6 Mirror tasks 1.1–1.5 in `patch-native.py`.
- [x] 1.7 From the firmware tree at `$FIRMWARE_SRC`, ran `python3 patch-t114.py` from a clean state: all five blocks (variant.ini, FF include, FF persistence, menu ordering, mag power profile) printed "Patched" lines. Confirmed `MagnetometerModule.cpp` now contains `0xD1`, `LIS3DH_DATARATE_25_HZ`, `LIS3DH_MODE_LOW_POWER`, `filter.begin(10)`, `return 100;` and zero `0x1D` / zero `return 50;` instances. Re-running printed five "Skipped" lines with no further file modifications.
- [x] 1.8 Same dance for `patch-native.py`: from clean, all eight blocks patched (native ini, FF include, mag header, mag cpp wrap, FF auto-pair, FF persistence, menu ordering, mag power profile). Re-run printed eight "Skipped" lines.

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

- [x] 4.1 `openspec/changes/reduce-magnetometer-power-draw/specs/magnetometer-power-profile/spec.md` exists with five ADDED requirements covering CTRL1 value, LIS3DH config, poll period, fusion rate, and patch idempotency.
- [ ] 4.2 After the change archives, the spec content moves to `openspec/specs/magnetometer-power-profile/spec.md` (handled by the archive workflow; not a manual step here).
