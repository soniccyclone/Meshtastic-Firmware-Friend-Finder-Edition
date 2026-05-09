## 1. Investigation (DONE during apply prep — kept for traceability)

- [x] 1.1 Clone `LeapYeet/firmware` and read `src/modules/FriendFinderModule.{h,cpp}` end-to-end. Recorded: `FriendRecord` = `{ uint32_t node, uint32_t session_id, uint8_t secret[16], bool used, meshtastic_FriendFinder last_data, uint32_t last_heard_time }`; `MAX_FRIENDS = 8`; persistence guard is `FF_HAVE_NVS` (= 1 only when `ARDUINO_ARCH_ESP32`); upstream `saveFriends()` is `g_prefs.putBytes("friends", friends_, sizeof(friends_))`; mutation sites are `upsertFriend` (cpp:289) and `removeFriendByListIndex` (cpp:160); load is in the constructor at cpp:112
- [x] 1.2 Confirmed: `friendfinder.proto` source is NOT in the `meshtastic/protobufs` submodule. Only generated `.pb.{h,cpp}` are checked into `src/mesh/generated/meshtastic/`. Adding a new `.proto` would require forking the submodule. Decision: skip protobuf, use a versioned binary blob (see design.md D1)
- [x] 1.3 Confirmed `NodeDB::saveProto` signature: `bool saveProto(const char *filename, size_t protoSize, const pb_msgdesc_t *fields, const void *dest_struct, bool fullAtomic = true)` ([NodeDB.h:238](../../../code-stuff/LeapYeet-firmware/src/mesh/NodeDB.h#L238)). `fullAtomic = true` is the default
- [ ] 1.4 Verify the LittleFS `/prefs/` path is mounted before `FriendFinderModule`'s constructor runs (where `loadFriends()` is currently called); if not, defer the load to first `runOnce()` per spec requirement "Load happens once at module init, after FS is mounted"

## 2. Serialization

- [ ] 2.1 Define `PersistedFriendsHeader` and `PersistedFriend` structs in a new helper file (e.g. `src/modules/FriendPersist.h`) that the patch script injects into the cloned firmware tree. Use the layout from design.md D1 (magic `'FFRD'` = `0x46465244`, version `1`, entry_size, count, reserved[3])
- [ ] 2.2 Implement `friendsToBlob(const FriendRecord (&friends)[MAX_FRIENDS], uint8_t *buf, size_t bufSize, size_t *outSize)` that packs only `used` slots into the blob, writing `node` / `session_id` / `secret[16]` per entry. Static-assert that the worst-case blob size is under 1024 bytes
- [ ] 2.3 Implement `friendsFromBlob(const uint8_t *buf, size_t bufSize, FriendRecord (&friends)[MAX_FRIENDS])` that validates magic, version, and entry_size; on mismatch, log WARN and return without populating; on success, zero `friends` first, then unpack entries with `used = true`
- [ ] 2.4 Choose between `NodeDB::saveProto` with a degenerate `bytes`-field message vs. direct `FSCom` file I/O for the actual disk write. Decide based on which keeps the patch smaller and routes through a wrapper that the P0/P1 gate (when it lands) will intercept

## 3. Patch block in `patch-t114.py`

- [ ] 3.1 Add a new marker-guarded block to `patch-t114.py` that anchors on the `FF_HAVE_NVS` `#define` block at `FriendFinderModule.cpp:30-36` and rewrites it so persistence compiles unconditionally on both ESP32 and nRF52
- [ ] 3.2 In the same block, replace the body of `loadFriends()` (cpp:164-184) with code that reads `/prefs/friends.proto` via the chosen API (per 2.4), validates the header, and unpacks via `friendsFromBlob`. On FS-not-mounted or file-missing, leave `friends_` zero-initialized and return cleanly with at most an INFO log line
- [ ] 3.3 In the same block, replace the body of `saveFriends()` (cpp:186-192) with code that packs `friends_` via `friendsToBlob` and writes via the chosen API with full-atomic semantics
- [ ] 3.4 Inject the helper file from 2.1 into the cloned tree (the patch script writes the new file alongside the cpp edits)
- [ ] 3.5 Confirm idempotency: running `patch-t114.py` twice on a fresh clone produces identical output. Re-use the existing `MARKER` pattern and "already patched" early returns
- [ ] 3.6 Add the equivalent block to `patch-native.py` so the native simulator gets the same code path — required for smoke tests

## 4. Mutation site verification

- [ ] 4.1 Confirm `upsertFriend` (cpp:289) calls `saveFriends()` — already true in upstream; no patch needed unless a future upstream rev removes it
- [ ] 4.2 Confirm `removeFriendByListIndex` (cpp:160) calls `saveFriends()` — already true in upstream; no patch needed
- [ ] 4.3 Audit `loadFriends()` invocation site (constructor at cpp:112). If it runs before FS mount, change the patch to defer load to first `runOnce()` instead. Spec requirement: "Load happens once at module init, after FS is mounted"

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
