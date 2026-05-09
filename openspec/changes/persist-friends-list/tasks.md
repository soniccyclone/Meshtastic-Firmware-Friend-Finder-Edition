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

- [ ] 5.1 Extend `tests/smoke/pairing_test.py` with a sub-test: complete a pairing, restart the simulated node process, assert the persisted friend is visible in the post-restart friends list (via log markers like `[FriendFinder] Loaded N friends from disk` or a friends-list dump command)
- [ ] 5.2 Add a sub-test: pair → kill simulator before clean shutdown → restart → assert friends list intact (covers crash-safety / atomic write requirement)
- [ ] 5.3 Add a sub-test: write a `friends.proto` file with a deliberately-bumped header `version`, boot, assert load logs WARN about version mismatch and starts with empty friends list (covers version-mismatch graceful drop)
- [ ] 5.4 Wire the new sub-tests into `entrypoint-smoke.sh` so CI runs them

## 6. CI and release

- [ ] 6.1 Verify `pr-build-t114.yml` produces an RC UF2 with the new patch block applied (no patch-anchor failures)
- [ ] 6.2 On-device QA: flash the RC onto a T114, pair two nodes, power-cycle, verify friends list is intact AND tracking still works without re-pairing (proves `secret[16]` survived)
- [ ] 6.3 On-device QA: pair, remove friend, power-cycle, verify removed friend does not reappear
- [ ] 6.4 Update `docs/design/t114-brick-fix.md` "Out of Scope" entry for friends persistence: replace "Separate issue." with a link to this change / its PR
- [ ] 6.5 Reference issue #25 in the PR with `Closes #25` once on-device QA passes
- [ ] 6.6 Release-note: explicitly call out that ESP32 users on the new firmware will not see friends previously stored in NVS-Preferences (one-time loss; new format is LittleFS)

## 7. Compose with brick-fix gate (deferred — no-op if P0/P1 not yet landed)

- [ ] 7.1 If P0/P1 has landed before this change ships: confirm `NodeDB::saveProto` already routes through `safeToWrite()`; no action needed at the friends call site
- [ ] 7.2 If P0/P1 has NOT landed when this change ships: leave a `// TODO(brick-fix-P0)` comment at the friends-save call site noting the future composition point; close the TODO when the gate lands
- [ ] 7.3 Add an integration-test scenario once P0/P1 has landed: simulate radio-not-idle at the moment of friend mutation, assert the write is deferred and later drained successfully
