## 1. Investigation

- [ ] 1.1 Clone `LeapYeet/firmware` at the SHA pinned by the build and read `src/modules/FriendFinderModule.{h,cpp}` end-to-end; record the exact `Friend` struct shape, `MAX_FRIENDS` constant, and current ESP32-only `saveFriends()` / `loadFriends()` bodies in a short notes file (`docs/notes/friend-persist-upstream.md`)
- [ ] 1.2 Confirm whether Meshtastic's protobuf set is committed source or generated at build; record the answer — this decides whether the patch adds a `friends.proto` schema or inlines a hand-written nanopb shim
- [ ] 1.3 Inspect `NodeDB::saveProto` signature in the cloned tree and confirm `fullAtomic` parameter name and default; capture a verbatim call site for use as the patch template
- [ ] 1.4 Verify the LittleFS `/prefs/` path is mounted before `FriendFinderModule::setup()` runs; if the ordering is unsafe, design.md's load-on-init plan needs a deferred-load hook — record findings

## 2. Schema and serialization

- [ ] 2.1 Define `meshtastic_FriendList` and `meshtastic_Friend` messages (or hand-rolled equivalents per 1.2) with fields: node ID (uint32), display name (string, 32 chars), reserved flags field for future use
- [ ] 2.2 Add the schema to the firmware build (either as a new `.proto` in the protobuf submodule or as a generated `.pb.{c,h}` checked into the patched tree)
- [ ] 2.3 Verify serialized worst-case size at `MAX_FRIENDS` is under 1 KB (per spec requirement); fail the build with a `static_assert` on `meshtastic_FriendList_size` if it isn't
- [ ] 2.4 Add encode/decode helpers `friendListToProto` / `friendListFromProto` in a new file under `src/modules/` that the patch injects into the cloned tree

## 3. Patch block in `patch-t114.py`

- [ ] 3.1 Add a new marker-guarded block to `patch-t114.py` that strips the `#if defined(ARDUINO_ARCH_ESP32)` guards around `FriendFinderModule::saveFriends()` and `loadFriends()` (or whatever the upstream names are per 1.1)
- [ ] 3.2 In the same block, replace the Preferences-based body of `saveFriends()` with a call to `nodeDB->saveProto("/prefs/friends.proto", meshtastic_FriendList_fields, &friendList, /*fullAtomic=*/true)`
- [ ] 3.3 In the same block, replace the Preferences-based body of `loadFriends()` with a call that reads `/prefs/friends.proto` via the same path Meshtastic uses for `nodes.proto` (likely `loadProto`); on FS-not-mounted or file-missing, leave the in-RAM list empty and return cleanly
- [ ] 3.4 Inject the new helpers from 2.4 into the cloned tree (either as a new file added by the patch script, or appended to `FriendFinderModule.cpp` with marker guards)
- [ ] 3.5 Confirm idempotency: running `patch-t114.py` twice on a fresh clone produces identical output to running it once (re-use the `MARKER` pattern and "already patched" early returns from existing blocks)
- [ ] 3.6 Add the equivalent block to `patch-native.py` so the native simulator gets the same code path — required for smoke tests

## 4. Mutation site wiring

- [ ] 4.1 Confirm every mutation site in `FriendFinderModule.cpp` (pair complete, unpair, rename) calls `saveFriends()`; if any does not, extend the patch to add the call (with the same marker discipline)
- [ ] 4.2 Confirm `loadFriends()` is called from `FriendFinderModule::setup()` (or equivalent init) after FS mount; if not, extend the patch
- [ ] 4.3 Verify there is no path that mutates the in-RAM friend list and exits without calling `saveFriends()` (e.g. error-recovery branches); audit and patch if found

## 5. Smoke test extension

- [ ] 5.1 Extend `tests/smoke/pairing_test.py` with a new sub-test: complete a pairing, restart the simulated node process, assert the persisted friend is visible in the post-restart friends list (via log markers or a friends-list dump command)
- [ ] 5.2 Add a sub-test: start with no friends file, boot, pair, write the friends file, force-kill the simulator before clean shutdown, restart, assert the friends list is intact (covers crash-safety / atomic write requirement)
- [ ] 5.3 Add a sub-test: write a `friends.proto` file with an unknown future field, boot, assert load succeeds with the known fields populated and a single INFO-level log line about the unknown field (covers forward-compat requirement)
- [ ] 5.4 Wire the new sub-tests into `entrypoint-smoke.sh` so CI runs them

## 6. CI and release

- [ ] 6.1 Verify `pr-build-t114.yml` produces an RC UF2 with the new patch block applied (no patch-anchor failures)
- [ ] 6.2 On-device QA: flash the RC onto a T114, pair two nodes, power-cycle, verify friends list is intact; record results in the PR description
- [ ] 6.3 On-device QA: pair, edit display name, power-cycle, verify renamed name persists
- [ ] 6.4 On-device QA: pair, remove friend, power-cycle, verify removed friend does not reappear
- [ ] 6.5 Update `docs/design/t114-brick-fix.md` "Out of Scope" entry for friends persistence: replace "Separate issue." with a link to this change / its PR
- [ ] 6.6 Reference issue #25 in the PR with `Closes #25` once on-device QA passes
- [ ] 6.7 Release-note: explicitly call out that ESP32 users on the new firmware will not see friends previously stored in NVS-Preferences (one-time loss; new format is LittleFS)

## 7. Compose with brick-fix gate (deferred — no-op if P0/P1 not yet landed)

- [ ] 7.1 If P0/P1 has landed before this change ships: confirm `NodeDB::saveProto` already routes through `safeToWrite()`; no action needed at the friends call site
- [ ] 7.2 If P0/P1 has NOT landed when this change ships: leave a `TODO(brick-fix-P0)` comment at the friends-save call site noting the future composition point; close the TODO when the gate lands
- [ ] 7.3 Add an integration-test scenario (in `pairing_test.py` or a sibling file) once P0/P1 has landed: simulate radio-not-idle at the moment of friend mutation, assert the write is deferred and later drained successfully
