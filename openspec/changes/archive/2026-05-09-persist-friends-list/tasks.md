## 1. Investigation (DONE during apply prep — kept for traceability)

- [x] 1.1 Clone `LeapYeet/firmware` and read `src/modules/FriendFinderModule.{h,cpp}` end-to-end. Recorded: `FriendRecord` = `{ uint32_t node, uint32_t session_id, uint8_t secret[16], bool used, meshtastic_FriendFinder last_data, uint32_t last_heard_time }`; `MAX_FRIENDS = 8`; persistence guard is `FF_HAVE_NVS` (= 1 only when `ARDUINO_ARCH_ESP32`); upstream `saveFriends()` is `g_prefs.putBytes("friends", friends_, sizeof(friends_))`; mutation sites are `upsertFriend` (cpp:289) and `removeFriendByListIndex` (cpp:160); load is in the constructor at cpp:112
- [x] 1.2 Confirmed: `friendfinder.proto` source is NOT in the `meshtastic/protobufs` submodule. Only generated `.pb.{h,cpp}` are checked into `src/mesh/generated/meshtastic/`. Adding a new `.proto` would require forking the submodule. Decision: skip protobuf, use a versioned binary blob (see design.md D1)
- [x] 1.3 Confirmed `NodeDB::saveProto` signature: `bool saveProto(const char *filename, size_t protoSize, const pb_msgdesc_t *fields, const void *dest_struct, bool fullAtomic = true)` ([NodeDB.h:238](../../../code-stuff/LeapYeet-firmware/src/mesh/NodeDB.h#L238)). `fullAtomic = true` is the default
- [x] 1.4 Verified: `fsInit()` runs at [main.cpp:524](../../../code-stuff/LeapYeet-firmware/src/main.cpp#L524), `setupModules()` (where `friendFinderModule = new FriendFinderModule()` lives at Modules.cpp:169) at line 965. FS is mounted ~440 lines of setup before the module is constructed. Existing constructor-time `loadFriends()` call is safe; no deferred-load hook needed

## 2. Serialization

- [x] 2.1 Defined `PersistedFriendsHeader` and `PersistedFriend` structs **inline in the patch script** rather than as a separate helper file (the structs are <30 lines total — a separate file is overkill). Layout matches design.md D1: magic `'FFRD'` (`0x46465244`), version `1`, entry_size, count, reserved[3]. `static_assert`s lock the sizes at 12 + 24 bytes
- [x] 2.2 Implemented inline in the patched `saveFriends()` body — packs only `used` slots into the blob with `node` / `session_id` / `secret[16]` per entry. Worst-case file size: 12 + 24×8 = 204 bytes (well under 1 KB)
- [x] 2.3 Implemented inline in the patched `loadFriends()` body — validates magic, version, entry_size, count clamp; on mismatch logs WARN and leaves `friends_` zero-initialized
- [x] 2.4 Decided: direct `FSCom.open(.., FILE_O_READ)` for load, `SafeFile(.., fullAtomic=true)` for save. Skipped the `NodeDB::saveProto` wrapper because it requires a `pb_msgdesc_t` and our blob isn't a protobuf. SafeFile is the same atomic-write primitive `saveProto` uses internally — composes identically with the future P0/P1 gate when that gate is added at the SafeFile / FSCom layer

## 3. Patch block in `patch-t114.py`

- [x] 3.1 Added `patch_friend_finder_persistence()` to `patch-t114.py`, anchoring on the `FF_HAVE_NVS` `#define` block at `FriendFinderModule.cpp:30-36` and rewriting it so persistence compiles unconditionally on both ESP32 and nRF52 (`#define FF_HAVE_NVS 0` retained as a no-op for any code path that queries it)
- [x] 3.2 Replaced the body of `loadFriends()` in the same block with code that reads `/prefs/friends.proto` via `FSCom.open` + `file.read`, validates header, unpacks entries. FS-not-mounted falls through `#ifdef FSCom`; file-missing logs INFO and returns with empty list
- [x] 3.3 Replaced the body of `saveFriends()` in the same block with code that builds the blob, opens a `SafeFile(.., fullAtomic=true)`, writes header + packed entries, and closes. `concurrency::LockGuard g(spiLock); FSCom.mkdir("/prefs");` ensures the prefs dir exists (matches NodeDB convention)
- [x] 3.4 No separate helper file needed — see 2.1
- [x] 3.5 Confirmed idempotency: running `patch-t114.py` twice on a fresh clone of LeapYeet/firmware @ `f49f9b7` produces identical output. The persistence block uses its own `PERSIST_MARKER = "// ff-builder: persist friends to LittleFS"` and skips when present
- [x] 3.6 Added the matching `patch_friend_finder_persistence()` block to `patch-native.py`. PortduinoFS abstraction at `FSCommon.h:11` means the same code works unchanged on the native simulator. Verified: applies cleanly + idempotent on a fresh clone

## 4. Mutation site verification

- [x] 4.1 Confirmed: `upsertFriend` at [FriendFinderModule.cpp:289](../../../code-stuff/LeapYeet-firmware/src/modules/FriendFinderModule.cpp#L289) calls `saveFriends()`. No patch needed
- [x] 4.2 Confirmed: `removeFriendByListIndex` at [FriendFinderModule.cpp:160](../../../code-stuff/LeapYeet-firmware/src/modules/FriendFinderModule.cpp#L160) calls `saveFriends()`. No patch needed
- [x] 4.3 Confirmed via 1.4 that `loadFriends()` at constructor cpp:112 runs after FS mount. No additional defer-to-runOnce hook needed

## 5. Smoke test extension

- [x] 5.1 Implemented as `tests/smoke/persistence_test.py` Phase 1+2: pair both nodes (reusing FF_NATIVE_AUTO_PAIR), verify `[FriendFinder] Persisted N friends to /prefs/friends.proto` log + file presence under each VFS root, SIGTERM both, restart with the same fsdirs, verify `[FriendFinder] Loaded 1 friends from /prefs/friends.proto` on each node. Local run PASSED end-to-end (file size 36 bytes = 12-byte header + 24-byte entry, matches design D1)
- [ ] 5.2 SIGKILL crash-safety sub-test deferred: on the host filesystem, pkill mid-write would only catch a window of microseconds, so reproducing the failure mode reliably needs either fault injection at the syscall layer or driving the kill from inside the firmware via a debug pin — both higher-effort than the value they add given Phase 3 already exercises the validation-rejection path. Re-evaluate if a real corruption is observed in the field
- [x] 5.3 Implemented as `persistence_test.py` Phase 3: write a `friends.proto` with a header version of 9999 into a fresh VFS root, boot, assert one of `bad magic` / `version/entry_size mismatch` / `truncated` WARN lines AND that no `Loaded N friends` line appears. Local run PASSED
- [x] 5.4 Wired into `entrypoint-smoke.sh` (added `=== Running FriendFinder persistence integration test (issue #25) ===` block plus per-phase log copy-out to `/output/persistence-*.log`) and added to `Dockerfile.native` (`COPY tests/smoke/persistence_test.py /usr/local/bin/persistence_test.py` + `chmod +x`). Verified by rebuilding the image and running `entrypoint-smoke.sh` end-to-end; all three suites pass

## 6. CI and release

- [x] 6.1 PR #28 built clean via `pr-build-t114.yml` and `make build` locally (T114 firmware.uf2 produced, 96.5% flash usage — tight margin flagged in PR description for future patch-budget awareness)
- [x] 6.2 On-device QA: pair two T114 nodes, power-cycle, friends list intact AND tracking works without re-pairing (`secret[16]` confirmed surviving). Reported by Nathan
- [x] 6.3 On-device QA: pair, remove friend, power-cycle, removed friend does not reappear. Reported by Nathan
- [x] 6.4 Updated `docs/design/t114-brick-fix.md:166` "Out of Scope" entry to reference this change as the resolution path for friends persistence
- [x] 6.5 PR #28 referenced issue #25 with `Closes #25`; PR merged 2026-05-09 (`b878018`)
- [ ] 6.6 Release-note: explicitly call out that ESP32 users on the new firmware will not see friends previously stored in NVS-Preferences (one-time loss; new format is LittleFS) — to be included in the next firmware release

## 7. Compose with brick-fix gate (deferred — no-op if P0/P1 not yet landed)

- [ ] 7.1 If P0/P1 has landed before this change ships: confirm `NodeDB::saveProto` already routes through `safeToWrite()`; no action needed at the friends call site
- [x] 7.2 Confirmed P0/P1 had NOT landed at ship time. `// TODO(brick-fix-P0)` comment is in `patch-t114.py` and `patch-native.py` `PERSIST_SAVE_NEW` block, injected into `saveFriends()` body. Close when the gate lands
- [ ] 7.3 Add an integration-test scenario once P0/P1 has landed: simulate radio-not-idle at the moment of friend mutation, assert the write is deferred and later drained successfully
