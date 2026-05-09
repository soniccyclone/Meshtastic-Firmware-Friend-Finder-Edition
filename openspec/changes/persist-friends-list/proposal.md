## Why

GitHub issue [#25](https://github.com/soniccyclone/Meshtastic-Firmware-Friend-Finder-Edition/issues/25): the friends list disappears every time the T114 powers off. `FriendFinderModule`'s in-module `save*()` / `load*()` calls are gated behind `#if defined(ARDUINO_ARCH_ESP32)`, so on nRF52840 the paired-friends table lives in RAM only — every reboot wipes it and forces users to re-pair every device, every session. This is the dominant friction point for field use; pairing at a festival is supposed to happen once, not every time a battery is swapped or the device is power-cycled.

The brick-fix design doc (`docs/design/t114-brick-fix.md`, "Out of Scope") explicitly carves this out as a separate UX bug to fix on its own. With the P0/P1 write-policy gate now defined as the safe path for any flash write on T114, we have the substrate to add friends persistence without reintroducing the corruption surface that motivated those gates.

## What Changes

- Replace `FriendFinderModule`'s ESP32-only Preferences-based persistence with a LittleFS protobuf file at `/prefs/friends.proto`, available on both ESP32 and nRF52.
- Load the friends list during module init (after FS mount) and re-populate the in-RAM table from disk before the module starts servicing pair/track requests.
- Save on every state transition that mutates the friends table: successful pairing completion, friend rename, friend removal. No periodic writes — event-driven only.
- All writes go through `NodeDB::saveProto` with `fullAtomic = true` (the file is small enough — see design.md), and route through the P0/P1 `safeToWrite()` gate when that gate lands.
- Ship the changes as a new patch block in `patch-t114.py` so the LeapYeet/firmware tree is mutated at build time, matching the existing patch architecture. No fork of upstream.
- **BREAKING (storage-layout only):** new `/prefs/friends.proto` file appears on disk. No backwards-compat concerns — there is no prior on-disk format for friends on nRF52 (RAM-only today), and ESP32 NVS-Preferences keys are independent of LittleFS files, so neither platform loses existing user data.

## Capabilities

### New Capabilities
- `friends-persistence`: persist the FriendFinder paired-friends table to flash and restore it across reboots, on both ESP32 and nRF52, in a way that composes with the T114 write-policy safety gates.

### Modified Capabilities
*(none — `friends-persistence` is net-new; `FriendFinderModule` itself is upstream code being patched, not a capability owned by this repo's spec set)*

## Impact

- **Patch infrastructure:** new patch block in `patch-t114.py` (idempotent, marker-guarded, same shape as existing blocks). Touches `src/modules/FriendFinderModule.cpp` in the cloned tree.
- **On-disk layout:** new file `/prefs/friends.proto` on LittleFS. Sized at most a few hundred bytes for a realistic friend count (see design).
- **Build:** no new dependencies. Uses `pb_encode` / `pb_decode` from nanopb, already linked by Meshtastic.
- **Runtime cost:** one read at boot (microseconds), one atomic write per pair/unpair/rename event. Atomic writes on a small file are well under 50 ms; the file is small enough to fit two copies on LFS comfortably.
- **Composes with P0/P1 brick-fix:** writes are atomic by construction, and additionally route through `safeToWrite()` once that wrapper lands so they defer cleanly during TX bursts and low-voltage windows.
- **Test surface:** existing `tests/smoke/pairing_test.py` extended with a power-cycle leg — pair, reboot the simulated node, assert the paired friend is still in the table.
- **Docs:** brief addition to `docs/design/t114-brick-fix.md` "Out of Scope" entry pointing at this change as the resolution.
