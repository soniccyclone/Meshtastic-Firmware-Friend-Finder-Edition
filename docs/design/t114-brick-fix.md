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

Neither patch touches `/prefs/nodes.proto` or the `saveProto(..., fullAtomic=false)` call path. Neither addresses the power-sag trigger. PR #15 alone cannot fix issue #12.

## Remediation Plan

Four additional patches, ranked by leverage. Each ships as a new block in `patch-t114.py` with the same idempotency-marker pattern as the existing patches.

### P0 — Gate non-atomic writes on safe conditions

**Root-cause fix.** Wrap `NodeDB::saveProto` (and any other `fullAtomic=false` caller) with a precondition check. Before writing, require all of:

1. **Radio is TX-idle** and has been for at least N ms (target: 200 ms, tunable after measurement).
2. **Battery voltage above safe write threshold** — composes with P1 below; same check, same wrapper.
3. **No higher-priority save is queued** — simple single-writer gate.

If any condition fails, mark the segment dirty in a deferred-write queue and return without touching flash. A low-rate drain (e.g., from the main loop idle path) re-attempts queued writes whenever the conditions become satisfiable.

This closes the "non-atomic write overlaps a TX-induced voltage sag" window that is the confirmed primary failure mode. It preserves all user data — no forced reformat, no lost friends/places/calibration. The device simply defers writes until it is safe to perform them.

Implementation location: a new static guard function in `NodeDB.cpp` that every `saveProto` caller routes through, plus a small queue structure for deferred segments. The radio-idle predicate requires a hook into `RadioInterface` (it already exposes TX state for other purposes — exact API needs verification during implementation).

Illustrative:

```cpp
bool NodeDB::safeToWrite() {
    if (powerStatus && powerStatus->getBatteryVoltageMv() < 3500) return false;
    if (radioInterface && radioInterface->msSinceTx() < 200)      return false;
    if (saveInProgress)                                           return false;
    return true;
}

bool NodeDB::saveProto(const char *filename, ..., bool fullAtomic = true) {
    if (!fullAtomic && !safeToWrite()) {
        LOG_WARN("Defer non-atomic save of %s: unsafe conditions", filename);
        pendingSaves |= segmentForFilename(filename);
        return false;
    }
    // ... existing saveProto body
}
```

### P1 — Low-voltage write guard (applies to all writes)

The voltage threshold used in P0 should apply to **all** `saveProto` calls, not just non-atomic ones. Atomic writes still consume time and current; a BOD event mid-atomic-write truncates the temp file rather than the target, so the file survives — but we lose the pending change and burn a flash-page cycle for nothing. Refuse the write below threshold regardless of atomicity.

Effectively this is the same code as P0's voltage check, hoisted to apply before the `fullAtomic` branch. Ship as part of the same patch block as P0 — they share the wrapper.

### P2 — Node DB save debouncing

Rate-limit `/prefs/nodes.proto` writes to at most once per 60s. Cuts exposure proportionally — one write every ~30s → one every 60s halves the overlap probability even with P0 and P1 in place. Lower leverage than P0 because it reduces the window rather than closing it. Ship only if P0+P1 don't pull the failure rate to zero in QA.

Implementation: introduce a `nodeDBDirty` flag set at every update site, drain it from a low-rate timer in `NodeDB::runOnce` (if it has one) or from Meshtastic's main loop idle path.

### P3 — Magnetometer poll-rate gating

Currently `MagnetometerModule::runOnce()` returns `50` unconditionally → 20 Hz QMC + 20 Hz LIS3DH + 20 Hz Madgwick forever, whether the heading is consumed or not. Gate the rate on whether any consumer actually needs the heading:

```cpp
bool consumerActive =
    (screen && screen->onCompassOrFriendFinderFrame()) ||
    isCalibrating() || isFlatCalibrating() ||
    (friendFinderModule && friendFinderModule->isTrackingActive());
return consumerActive ? 50 : 1000;   // 20 Hz active, 1 Hz idle
```

Consequences:

- Wire1 traffic drops from ~40 transactions/sec to ~2 transactions/sec when idle — ~20× less exposure for the H2 bus-hang path.
- Smaller sustained current draw → smaller voltage sags on battery → proportionally less BOD risk during TX bursts.
- First heading on opening the compass has up to 1 s latency; mitigated by triggering an immediate read on consumer-active state transitions.

Same category as P2: **reduces exposure, does not close the failure mode**. General-hygiene improvement with power and responsiveness benefits beyond the brick investigation. Ship alongside P0+P1 rather than waiting for QA signal — it is cheap and its benefits are independent of the bricking outcome.

## Rejected Alternatives

### Auto-format LittleFS on failed mount

Considered and rejected. The proposal was: if `FSCom.begin()` cannot mount `/prefs`, call `FSCom.format()` + `installDefaultConfig()` and boot with defaults. This would make every corruption event recoverable without a laptop.

**Why rejected:** trades a hard brick for silent data loss. A user whose friends, places, and magnetometer calibration vanish on reboot experiences a different kind of failure, not a fix. "The device boots with everything you entered gone" is not a recovery — it is a better-mannered crash. The firmware's job is to preserve user-entered state across every reboot, including post-corruption ones. Auto-format is a band-aid over the real problem and masks it from future investigation by destroying the evidence.

The P0 + P1 combination is the alternative that actually preserves user data — by preventing the corruption in the first place rather than formatting after the fact.

## Backburnered

### A — Atomic writes via larger LFS partition

The architecturally correct long-term fix: make `saveNodeDatabaseToDisk` use `fullAtomic=true` by enlarging the LittleFS partition enough to hold two copies of `nodes.proto` briefly. The fragile non-atomic branch becomes dead code on T114 and the corruption surface goes away entirely.

**Why not this cycle:**

- The T114 has 1 MB of internal flash split between bootloader (~28 KB, fixed), SoftDevice S140 (~152 KB, fixed), application, and LittleFS. SoftDevice and bootloader are load-bearing — LFS can only grow by taking flash from application space. Meshtastic + Friend Finder is already a big binary with limited headroom; a 20-40 KB partition grab could push the app over its slot size or eat the margin needed for future modules.
- Requires linker script / variant partition-table edits. `patch-t114.py` can do this textually, but the anchors are more fragile than the source-level patches already in place — first upstream reshuffle and we rewrite.
- **Migration story for existing T114s in the field.** Every device currently flashed with `v2.7-FF-t114-1` has LFS at the old geometry. New firmware with a different partition layout either (a) fails to recognize the partition → forced reformat with the same data loss as the rejected auto-format option, (b) reads the old partition at the wrong offset → garbage → mount fails → reformat, or (c) aligns carefully so the new partition is a strict superset and LFS mounts cleanly. Only (c) is acceptable, and it needs flash-layout care that is its own design conversation.
- Cross-variant ripple — the LeapYeet/firmware source supports multiple nRF52 variants. Scoping a partition change to T114 only requires per-variant linker fragments; more per-variant complexity.
- Partition geometry changes need flash-level testing (not just source-level build verification). Larger validation burden than a source patch.

Revisit after P0+P1+P2+P3 have been in the field long enough to confirm the write-policy approach is durable and we actually need the architecturally-correct version. If the write-policy gates fail to zero out bricks in real-world use, A becomes the next step.

## Out of Scope

- **Upstream `meshtastic/firmware#5839` fix.** That's a Meshtastic-wide concern, not Friend Finder. P0 makes us resilient to it regardless of whether upstream lands a fix.
- **Hardware-side mitigations** (larger bulk cap on Vbat, higher-quality cell). Real fixes, but not ours to ship in firmware patches.
- **Persisting Friend Finder state on nRF52** (friends, places, calibration). Genuine UX bug — currently all RAM-only on T114 — but orthogonal to the brick. Separate issue.

## Validation

Each remediation patch ships as its own PR, referenced back to this doc. The existing PR-triggered CI (`pr-build-t114.yml`) builds an RC UF2 and publishes it as a GitHub pre-release per push, so QA can flash without a local toolchain.

Per-patch success criterion is a log-verified QA reproduction against the corresponding RC build:

- **P0:** Run a sustained tracking session on battery with mag-heavy activity (Figure-8 cal loops, Flat-Spin cal loops, Track starts/stops). Expect `LOG_WARN("Defer non-atomic save of /prefs/nodes.proto: unsafe conditions")` during TX windows or low-voltage events, and no brick. On USB (where the gate trivially passes) behavior is unchanged.
- **P1:** Drain a test battery to just below 3500 mV. Run a tracking session. Expect `LOG_WARN` lines indicating saves were deferred due to voltage; device continues operating; no flash-write activity visible in the log until voltage recovers. No brick.
- **P2:** Mesh-churn stress test. Observe `nodes.proto` write cadence in logs. Expect writes rate-limited to ≤1 per 60 s.
- **P3:** Idle the device on the main frame (not compass, not Track, not calibrating). Expect `[Magnetometer]` read log cadence to drop to ~1 Hz. Open the compass frame; first reading should appear within ~50 ms via the consumer-active trigger.

Overall success criterion is TezlaKid running his pre-existing brick repro on battery against the combined P0+P1+P3 firmware and failing to brick the device over a session at least 3× longer than his previous time-to-brick, with **no loss of user-entered state** (friends, places, calibration) across any reboot during the test.

## Patch Landing Order

1. **PR #15** — Patch A (FriendFinder `reloadConfig` swap) + Patch B (SCL-pulse bus recovery) + per-PR CI workflow. Defense-in-depth; does not close the brick. Lands first.
2. **P0 + P1 PR** — combined write-policy gate (TX-idle + voltage-safe + queue-empty) wrapping `saveProto`. These share a wrapper, so they ship in one patch block. This is the root-cause fix.
3. **P3 PR** — magnetometer poll-rate gating. Cheap, user-facing benefits independent of the brick, reduces H2 exposure as a bonus. Can land in parallel with P0+P1.
4. **P2 PR** — nodedb save debouncing. Only if P0+P1+P3 don't zero out observed bricks in QA.

A (larger LFS partition for atomic writes) is backburnered, not next in line — it becomes relevant only if the write-policy approach proves insufficient in sustained field use.

All patches reference [#12](https://github.com/soniccyclone/Meshtastic-Firmware-Friend-Finder-Edition/issues/12) with `Refs`, not `Closes`, until TezlaKid's field validation confirms the combined stack fixes the reported behavior with no user-state loss.
