# T114 Brick Fix — Design Document

## Problem

[Issue #12](https://github.com/soniccyclone/Meshtastic-Firmware-Friend-Finder-Edition/issues/12) (TezlaKid): Heltec Mesh Node T114s running `v2.7-FF-t114-1` enter an unrecoverable state during Friend Finder use. A plain `firmware.uf2` reflash does not recover; the device only boots after flashing stock Meshtastic first (to format LittleFS), then flashing Friend Finder on top. Stock Meshtastic alone on the same hardware does not exhibit this behavior.

The reporter has a soldered QMC5883L magnetometer on Wire1 (P0.16 SDA, P0.13 SCL), which is how anyone running this firmware in the field will eventually wire it — jumper leads are not reliable in a pocket at a festival. With the sensor permanently attached, the "unplug the magnetometer to unbrick" workaround is not available. The only recovery path is a laptop and two UF2 files.

Reproduction triggers reported on battery power:
- Entering `Friend Finder → Track` (either tracking or being tracked)
- Running `Friend Finder → Compass Cal` (Figure-8 or Flat-Spin)
- Randomly during idle use with the magnetometer active

The bricking does **not** reproduce when the device is connected to USB for serial logging. Several multi-minute sessions on battery brick; the same repro steps on USB do not.

## Investigation Summary

Audited the LeapYeet/firmware source directly. Several hypotheses were tested and discarded before reaching the current root-cause understanding.

**H1 — "FriendFinderModule writes persistent state during tracking → LFS churn → corruption."** Partially true but insufficient. `FriendFinderModule::activateHighGpsMode()` and `restoreNormalGpsMode()` call `service->reloadConfig(SEGMENT_CONFIG)`, which persists `LocalConfig` to `/prefs/config.proto` — two writes per tracking session. However, [NodeDB.h:239](https://github.com/LeapYeet/firmware) declares `saveProto` with `fullAtomic = true` by default, so those writes are atomic (temp-file-plus-rename) and cannot corrupt LFS even if interrupted. Cutting this write rate is defense-in-depth, not the fix.

**H2 — "MagnetometerModule::qmcReadRaw stalls Wire1 → watchdog resets mid-flash-write."** Plausible and partially validated. TezlaKid's COM11 serial log shows `[Magnetometer] QMC read failed; will retry.` at uptime 1208s — a real I2C transaction failure. The upstream driver has no timeout and no bus-recovery, so a slave holding SDA low after an EMI glitch is a real risk. Adding SCL-clock-pulse recovery is correct defensive code but does not, by itself, explain the observed brick pattern.

**H3 — "Persistence on nRF52 is different from ESP32, and the Friend Finder code doesn't know."** Confirmed but unrelated to the brick. Both `MagnetometerModule` and `FriendFinderModule` guard their `Preferences`-based persistence behind `#if defined(ARDUINO_ARCH_ESP32)`, so on the T114 none of the in-module `save*()` functions write anything. Friends, places, and calibration are RAM-only on nRF52 — a separate UX bug, not this one.

**H4 — "Power-triggered BOD during radio TX on battery corrupts a flash write in flight."** Strongly supported. The device does not brick on USB (stable 3.3V rail through SX1262 TX peaks). It bricks on battery (voltage sags during ~120mA TX bursts). The nRF52840's brown-out detector fires during the sag, and if a LittleFS write is in flight, the write truncates. This matches the upstream bug [meshtastic/firmware#5839](https://github.com/meshtastic/firmware/issues/5839) ("Critical fault #12 flash filesystem corruption and format on nrf52 platform") exactly.

**H5 — "There's a specific non-atomic write that is the corruption surface."** Confirmed from TezlaKid's log. Line 51:

```
[Router] Save to disk 16
[Router] Opening /prefs/nodes.proto, fullAtomic=0
[Router] Save /prefs/nodes.proto
```

`SEGMENT_NODEDATABASE = 16` ([NodeDB.h:83](https://github.com/LeapYeet/firmware)) maps to `saveNodeDatabaseToDisk()`, which at [NodeDB.cpp:1413](https://github.com/LeapYeet/firmware) calls `saveProto(nodeDatabaseFileName, ..., false)` — explicit `fullAtomic=false`. The code comment at [NodeDB.cpp:1400](https://github.com/LeapYeet/firmware) acknowledges this is intentional:

> Because so huge we _must_ not use fullAtomic, because the filesystem is probably too small to hold two copies of this

The file is ~17KB (MAX_NUM_NODES × meshtastic_NodeInfoLite_size). A non-atomic write to a file that size takes multiple flash-page erase+program cycles — easily 50-200ms of write activity. During that window, any reset (BOD, WDT, hardfault) leaves the file partially written, and LittleFS refuses to mount a corrupted metadata block on next boot. The device hard-bricks until the filesystem is formatted.

The mesh triggers `saveToDisk(SEGMENT_NODEDATABASE)` on every new-node discovery. On a busy mesh, that's one ~17KB non-atomic write every ~30 seconds.

## Root Cause

Two failure modes compound, both of which produce the same symptom:

1. **Voltage sag during SX1262 TX coincides with a `nodes.proto` write.** On battery, a TX burst draws >100mA peak. The regulator cannot hold 3.3V through the dip on a less-than-fresh cell. The nRF52840 BOD fires during the ~17KB non-atomic write. LFS commits a torn block. Boot-time mount fails.

2. **(Less common) Wire1 TWIM peripheral stalls on a QMC5883L NAK, thread hangs, Meshtastic's watchdog resets the device.** If the reset lands mid-`nodes.proto` write, same outcome.

The common thread is the non-atomic write to `/prefs/nodes.proto` as the fragile surface, plus a trigger (BOD or WDT) that can reset the MCU while that write is in flight. Every known repro from issue #12 overlaps one of these windows.

## Why PR #15 Is Insufficient

The existing PR ships two patch blocks in `patch-t114.py`:

- **Patch A** replaces `service->reloadConfig(SEGMENT_CONFIG)` with `service->configChanged.notifyObservers(nullptr)` in `FriendFinderModule`. Correctly eliminates two atomic writes per tracking session. These writes were not the corruption surface, so this does not close the brick path. It reduces radio-thread-plus-flash overlap density slightly. Keep as defense-in-depth.

- **Patch B** wraps `MagnetometerModule::qmcReadRaw` with a counter-based SCL-clock-pulse bus-recovery helper after three consecutive NAKs. Addresses the H2 failure path, which TezlaKid's COM11 log confirmed is real. Keep as defense-in-depth.

Neither patch touches `/prefs/nodes.proto`, the `saveProto(..., fullAtomic=false)` call path, or the boot-time LFS mount. Neither addresses the power-sag trigger. PR #15 alone cannot fix issue #12.

## Remediation Plan

Three additional patches, ranked by leverage. Each ships as a new block in `patch-t114.py` with the same idempotency-marker pattern as the existing patches.

### P0 — Auto-format LittleFS on failed mount

**The single highest-leverage change in this whole effort.** On nRF52, if `FSCom.begin()` fails to mount `/prefs`, the current upstream behavior is to halt or boot-loop. Replace that with: format the partition, call `installDefaultConfig()`, continue. The user loses friends/places/calibration state — but the device boots, pairs, and is usable in the field again without a laptop.

This does not prevent corruption. It makes every corruption event recoverable by any user in the field. Given the "festival" use case described in issue #12, this changes the impact from "hard brick requiring two UF2 files and a computer" to "device reboots with factory defaults and a warning banner." That is the actual fix to the issue as reported.

Implementation location: the Meshtastic boot-time `FSCom.begin()` call site in `src/platform/nrf52/main-nrf52.cpp` (exact anchor TBD during implementation). Logic: `FSCom.begin(); if (!mounted) { FSCom.format(); FSCom.begin(); installDefaultConfig(); }`.

### P1 — Low-voltage write guard

Before any `saveProto` call, check `powerStatus->getBatteryVoltageMv()`. If below a safe threshold (initial value: 3500mV, can tune based on measured TX-sag depth), refuse the write and log a warning. A deferred-write flag retries on the next save opportunity once voltage recovers.

This closes the BOD-during-write window on depleted batteries. At 3500mV a Li-ion cell has ~30% charge remaining — plenty of runway for the device to keep operating, just not to risk flash writes. On USB the check trivially passes since `getBatteryVoltageMv()` returns the rail voltage.

Implementation location: wrap `NodeDB::saveProto` itself, so every caller gets the guard without per-call-site changes. Illustrative:

```cpp
bool NodeDB::saveProto(const char *filename, ...) {
    if (powerStatus && powerStatus->getBatteryVoltageMv() < 3500) {
        LOG_WARN("Skip %s: battery %dmV below safe write threshold",
                 filename, powerStatus->getBatteryVoltageMv());
        pendingSaves |= segmentForFilename(filename);
        return false;
    }
    // ... existing saveProto body
}
```

### P2 — Node DB save debouncing

Rate-limit `/prefs/nodes.proto` writes to at most once per 60s. Cuts exposure proportionally (one write every ~30s → one every 60s halves the BOD-overlap probability even without P0/P1). Lower leverage than either P0 or P1 because it reduces the window rather than closing it. Ship only if P0+P1 don't pull the failure rate to zero in QA.

Implementation: introduce a `nodeDBDirty` flag set at every update site, drain it from a low-rate timer in `NodeDB::runOnce` (if it has one) or from Meshtastic's main loop idle path.

## Out of Scope

- **Raising the LFS partition size so `nodes.proto` can be written atomically.** The comment in `NodeDB.cpp` ("filesystem is probably too small to hold two copies") points at an upstream architectural decision that predates Friend Finder. Fixing this requires changes to the nRF52840 linker script and variant definitions — much larger blast radius than patch-t114.py can carry cleanly. Correct long-term fix; wrong fit for this PR cycle.
- **Upstream `meshtastic/firmware#5839` fix.** That's a Meshtastic-wide concern, not Friend Finder. P0 makes us resilient to it regardless of whether upstream lands a fix.
- **Hardware-side mitigations** (larger bulk cap on Vbat, higher-quality cell). Real fixes, but not ours to ship in firmware patches.
- **Persisting Friend Finder state on nRF52** (friends, places, calibration). Genuine UX bug — currently all RAM-only on T114 — but orthogonal to the brick. Separate issue.

## Validation

Each remediation patch ships as its own PR, referenced back to this doc. The existing PR-triggered CI (`pr-build-t114.yml`) builds an RC UF2 and publishes it as a GitHub pre-release per push, so QA can flash without a local toolchain.

Per-patch success criterion is a log-verified QA reproduction against the corresponding RC build:

- **P0:** Induce LFS corruption (flash a bad UF2 that writes garbage to `/prefs/`, or cut power mid-save on battery). On reboot, device must format and come up with defaults — no brick.
- **P1:** Drain a test battery to just below 3500mV. Run a tracking session. Expect `LOG_WARN("Skip /prefs/...: battery ... below safe write threshold")` and no brick.
- **P2:** Mesh-churn stress test. Observe `nodes.proto` write cadence in logs. Expect writes rate-limited to ≤1 per 60s.

Overall success criterion is TezlaKid running his pre-existing brick repro on battery against the combined P0+P1 firmware and failing to brick the device over a session at least 3× longer than his previous time-to-brick. If a brick still occurs, P0 guarantees it recovers to defaults on boot rather than requiring the two-step UF2 reflash.

## Patch Landing Order

1. **PR #15** (this branch's predecessor) — Patch A + Patch B + per-PR CI workflow. Defense-in-depth; does not close the brick. Lands first.
2. **P0 PR** — auto-format on mount failure. Top priority; unblocks the field-recovery story regardless of remaining corruption rate.
3. **P1 PR** — low-voltage write guard. Closes the primary power-sag window.
4. **P2 PR** — nodedb save debouncing. Only if P0+P1 don't zero out observed bricks in QA.

All reference [#12](https://github.com/soniccyclone/Meshtastic-Firmware-Friend-Finder-Edition/issues/12) with `Refs`, not `Closes`, until field validation confirms the combined stack fixes the reported behavior.
