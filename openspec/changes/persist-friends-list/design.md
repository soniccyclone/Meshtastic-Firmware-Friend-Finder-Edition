## Context

This repo is a build-time wrapper around upstream `LeapYeet/firmware`. The actual `FriendFinderModule.{h,cpp}` lives in that upstream tree (read at SHA `f49f9b7`); this repo cherry-picks build-environment workarounds and behavior patches via `patch-t114.py` and `patch-native.py` against a pristine clone, then compiles in `ff-builder` containers. The architecture is deliberate: we do not maintain a fork of the firmware source, only a small, idempotent, marker-guarded set of textual patches. Any change to module behavior ships as a new patch block.

The friends-list state today, verified by reading the upstream tree:

[`src/modules/FriendFinderModule.h:43-51`](../../../../code-stuff/LeapYeet-firmware/src/modules/FriendFinderModule.h):
```cpp
struct FriendRecord {
    uint32_t node;
    uint32_t session_id;
    uint8_t  secret[16];
    bool     used;
    meshtastic_FriendFinder last_data;     // runtime telemetry, not load-bearing
    uint32_t last_heard_time;              // millis(), runtime-only
};
static constexpr int MAX_FRIENDS = 8;
```

[`src/modules/FriendFinderModule.cpp:30-36`](../../../../code-stuff/LeapYeet-firmware/src/modules/FriendFinderModule.cpp):
```cpp
#if defined(ARDUINO_ARCH_ESP32)
  #include <Preferences.h>
  static Preferences g_prefs;
  #define FF_HAVE_NVS 1
#else
  #define FF_HAVE_NVS 0
#endif
```

The actual platform guard is `FF_HAVE_NVS`, derived from `ARDUINO_ARCH_ESP32`. On nRF52 the `saveFriends()` / `loadFriends()` bodies (cpp:164-192) compile to no-ops. The upstream save is a raw `g_prefs.putBytes("friends", friends_, sizeof(friends_))` — a byte-for-byte dump of the entire `friends_` array (192 bytes for 8 entries × 24 bytes each before alignment padding). That includes `last_data` and `last_heard_time`, which are runtime-only and have no business on disk.

The codebase's standard persistence pattern (per `docs/design/t114-brick-fix.md` H5) is LittleFS files written through `NodeDB::saveProto` ([`src/mesh/NodeDB.h:238-239`](../../../../code-stuff/LeapYeet-firmware/src/mesh/NodeDB.h)):
```cpp
bool saveProto(const char *filename, size_t protoSize, const pb_msgdesc_t *fields,
               const void *dest_struct, bool fullAtomic = true);
```
`fullAtomic=true` is the default. Atomic writes are temp-file-plus-rename and survive interrupted writes. Non-atomic writes are reserved for `nodes.proto` (~17 KB), the file too large to hold two copies on the partition — it is the confirmed corruption surface from the brick investigation.

The upstream `friendfinder.proto` source is **not** in the `meshtastic/protobufs` submodule (verified: only the generated `friendfinder.pb.{h,cpp}` are present in `src/mesh/generated/meshtastic/`). Adding a new `.proto` would require either forking that submodule or hand-writing the generated nanopb output — both significant.

The brick-fix design also defines a `safeToWrite()` predicate (P0/P1) that gates writes on radio-TX-idle and battery voltage. That work has not landed at the time of this change. This design composes with whichever ordering — friends persistence is atomic-on-a-tiny-file and safe in isolation; once the gate is added inside `NodeDB::saveProto`, friends writes pick it up automatically with zero call-site changes.

Stakeholders: Nathan (this repo), TezlaKid (the user who hit the brick path; benefits from never re-pairing again), the LeapYeet/firmware upstream maintainers (no direct dependency, but the patch shape is what they would land if they accepted it).

## Goals / Non-Goals

**Goals:**

- Friends paired on a T114 survive power-off, battery swap, and intentional reboot. The shared `secret[16]` is preserved byte-for-byte so post-reboot mutual authentication continues working without re-pairing.
- The same persistence mechanism works on ESP32 builds, replacing the existing NVS-Preferences path with the LittleFS path so both platforms share one file format.
- Persistence is crash-safe: an interrupted write never corrupts a previously-written friends list.
- The change ships as a single, idempotent, marker-guarded patch block in `patch-t114.py` and the equivalent block in `patch-native.py`, in the same shape as existing blocks.
- The persistence path composes cleanly with the P0/P1 brick-fix gate when that gate lands, with zero rework of the friends path.
- The on-disk format is versioned: incompatible format changes are detectable at load time and cause a clean drop-and-empty-boot rather than a misinterpretation.

**Non-Goals:**

- Migrating data from the ESP32 NVS-Preferences key into the new LittleFS file. (Today's nRF52 friends are already lost every boot; today's ESP32 users start from a clean state on the new firmware. Migration would solve a problem fewer than a handful of users have.)
- Persisting *every* FriendFinder state — places (`SavedPlace places_[MAX_PLACES]`), calibration, last-seen timestamps. Places persistence has the same shape as friends and could be added in a follow-up; including it here doubles the patch surface for marginal benefit. Calibration is its own design conversation.
- Forking the `meshtastic/protobufs` submodule to add a new `.proto`. Out of scope; we use a different serialization (see D1).
- Cross-device sync of friends lists over the mesh.
- Encryption of the on-disk file. The mesh credentials and node DB are not encrypted at rest today; friends shouldn't be either, in this change.
- Implementing the P0/P1 `safeToWrite()` gate itself — that's the brick-fix change. We define the integration point and route through the standard wrapper so the gate slots in without rework.

## Decisions

### D1 — On-disk format: versioned binary blob, NOT a new protobuf

The persistable surface is three fields per entry: `node` (4 bytes), `session_id` (4 bytes), `secret[16]` (16 bytes) = 24 bytes per entry × 8 entries = 192 bytes worst case. With a small header that's well under 1 KB.

Format:
```
struct PersistedFriendsHeader {
    uint32_t magic;         // 'FFRD' = 0x46465244
    uint16_t version;       // 1
    uint16_t entry_size;    // sizeof(PersistedFriend) — guards struct drift
    uint8_t  count;         // number of valid entries [0..MAX_FRIENDS]
    uint8_t  reserved[3];   // alignment + future flags
};                          // 12 bytes

struct PersistedFriend {
    uint32_t node;
    uint32_t session_id;
    uint8_t  secret[16];
};                          // 24 bytes

// Total worst case: 12 + 24 * 8 = 204 bytes
```

Forward-compat story: incompatible changes bump `version`. Reader rejects unknown versions or `entry_size` mismatches by logging WARN and booting empty (consistent with the spec's "absence-of-file is not a fault" requirement). One device's friends list is lost in that scenario — acceptable for a format-bump event in a small-population project.

This goes through `NodeDB::saveProto` / `NodeDB::loadProto` by passing a degenerate `pb_msgdesc_t` for a single `bytes` field that wraps the whole blob — or, if that turns out to be friction at implementation time, via `FSCom`-direct file I/O with the same temp-file-plus-rename atomicity discipline. Both options keep the gate-composition story (D5) intact because the deferred-write queue lives at the segment level, not the API level.

**Alternatives considered:**

- *New `friendpersist.proto` in the protobufs submodule.* Rejected: forking `meshtastic/protobufs` for one tiny message is disproportionate maintenance burden, and it complicates protobuf-submodule version pins.
- *Hand-written `friendpersist.pb.{h,cpp}` injected by the patch script.* Rejected: nanopb-generated files have boilerplate that's easy to mismatch by hand, and any bug there is a memory-safety bug in mesh code.
- *Reuse `LocalConfig` or a sub-message of an existing protobuf.* Rejected: wrong granularity. `LocalConfig` is the device's main config blob, written at much higher frequency for unrelated reasons. Coupling friend mutations to that file means every pair/rename triggers a `LocalConfig` write.
- *Raw `g_prefs.putBytes`-style byte dump with no header.* This is what upstream does today. Rejected because it has zero forward-compat story — adding any field to `FriendRecord` invalidates every existing file silently. The header-plus-version approach costs 12 bytes and gains a real format-evolution story.

### D2 — Atomic writes only (`fullAtomic=true`)

The whole file is ~200 bytes. LittleFS easily holds two copies. There is no reason to use the non-atomic write path that caused the brick. Atomic-only is also the cleanest composition with P0/P1 (D5).

### D3 — Event-driven writes, not periodic

Write only on mutation — `upsertFriend` (already calls `saveFriends()` at cpp:289), `removeFriendByListIndex` (already calls `saveFriends()` at cpp:160). No polling timer, no shadow-state diffing. Rationale:

- Mutations are infrequent (humans pair friends in seconds, not milliseconds).
- An event-driven model has zero idle-write cost — important on battery.
- A timer-based "save dirty state every N seconds" model is strictly worse: it adds idle flash wear and has higher worst-case latency to disk than event-driven for the same correctness guarantee.

The tradeoff is that a crash *between* a mutation and the immediately-following write loses that one mutation. With atomic writes that window is well under 50 ms. We accept this — it is the same correctness boundary every other event-driven persister in Meshtastic operates under. Upstream already wires `saveFriends()` calls at every mutation site; this change preserves that discipline.

### D4 — Patch as a `patch-t114.py` block (and a matching `patch-native.py` block)

Existing repo discipline. The block follows the same pattern as the existing two:

1. Anchor on a unique string in `FriendFinderModule.cpp` — specifically the `FF_HAVE_NVS` `#define` block at cpp:30-36 and the `loadFriends()` / `saveFriends()` bodies at cpp:164-192.
2. Emit a marker-guarded replacement that:
   - Removes the `FF_HAVE_NVS` guard (or redefines it to always-true)
   - Replaces the body of `loadFriends()` with a call that reads `/prefs/friends.proto` via `NodeDB::loadProto` (or equivalent), parses the versioned blob, and populates `friends_` — leaves `friends_` zero-initialized on FS-not-mounted, file-missing, version-mismatch, or entry-size-mismatch
   - Replaces the body of `saveFriends()` with a call that serializes `friends_` (skipping `!used` slots) into the versioned blob and writes via `NodeDB::saveProto(..., /*fullAtomic=*/true)`
3. Idempotent: if the marker is already in the file, skip — same `MARKER` discipline as existing blocks.

Optionally, the patch may also inject a small helper `.cpp/.h` pair into the cloned tree to keep the encode/decode logic out of `FriendFinderModule.cpp`. Final placement decided at implementation time based on file size and readability.

### D5 — Integration point for `safeToWrite()`

The friends-save path calls `nodeDB->saveProto(...)`. When the brick-fix P0/P1 patch lands, that call gains the `safeToWrite()` gate as its precondition inside the wrapper. No change to the friends-save call site is required at that point — the gate composes by virtue of routing through the standard wrapper.

If P0/P1 lands *before* this change, we benefit immediately. If P0/P1 lands *after*, friends writes are still atomic and safe in isolation; they just don't yet defer on TX-idle / voltage conditions. Either ordering is acceptable.

### D6 — Removal of the `FF_HAVE_NVS` guard, not a sibling code path

We do not keep the existing `Preferences`-based ESP32 path alongside a new LittleFS path. Both platforms get the LittleFS path. Rationale:

- Two persistence paths means two formats, two sets of bugs, two code paths to test.
- ESP32 LittleFS is a first-class platform in Meshtastic; the standard persisters (`nodes.proto`, `config.proto`) already use LittleFS on ESP32.
- The format becomes platform-portable as a side benefit (D1's versioned format applies cross-platform).

The cost is that ESP32 users on the new firmware do not see their old NVS-stored friends. As called out in non-goals, this is a few-user / one-time cost weighed against the simpler architecture.

### D7 — Persisted record excludes runtime-only fields

`FriendRecord` contains `last_data` (a `meshtastic_FriendFinder` protobuf with the most-recent received telemetry — ephemeral) and `last_heard_time` (a `millis()` value — meaningless across reboot). Upstream's raw byte-dump persists these by accident; we drop them. This shrinks the on-disk footprint and removes the (otherwise harmless) noise of stale telemetry on disk.

The persisted record also excludes the `used` flag — we encode presence/absence directly via the `count` field in the header and pack only used entries into the file. On load, the in-RAM table is zeroed first, then the loaded entries' `used` flags are set as they're filled in.

### D8 — Display name is NOT persisted (because it doesn't exist in the struct)

Earlier draft of this design assumed friends had a per-friend display name. They do not — the struct has no name field. Display names are pulled from `NodeDB` via `FriendFinderModule::getNodeName(uint32_t nodeNum)` at render time. Persisting `node` is sufficient to recover the display name on next render via the standard mesh node DB.

## Risks / Trade-offs

- **Risk:** P0/P1 lands later, and during the gap, a friends write coincides with a TX burst on a low battery and the temp-file write truncates. **Mitigation:** atomic writes mean the previous file is intact — at worst, the most-recent pair/rename is lost on next boot. The user re-pairs once. Strictly better than today (lose everything every boot) and bounded.
- **Risk:** LittleFS partition is too small for `friends.proto` (~200 bytes) + `nodes.proto` (~17 KB) + `config.proto` + temp copies under worst-case fragmentation. **Mitigation:** the friends file is tiny. The brick-fix backburner item A (larger LFS partition) addresses the broader version of this risk if it ever fires; this change does not depend on it.
- **Risk:** Upstream `LeapYeet/firmware` reshuffles `FriendFinderModule.cpp` enough that the patch anchors break. **Mitigation:** the patch follows the same idempotency / marker pattern as existing blocks, fails loudly on missing anchor (`patch-t114.py`'s existing `sys.exit` style), and is small enough to re-anchor in minutes when an upstream rev moves. Same risk every existing patch carries.
- **Risk:** The `secret[16]` field changes meaning or moves in upstream `FriendRecord`. **Mitigation:** the patch's serialization helper sits next to the struct definition in source — any change to the struct that affects the persisted fields will fail to compile (struct-init mismatch) rather than silently misbehave. The header's `entry_size` field also catches struct drift at load time on already-deployed devices.
- **Risk:** ESP32 users expect their NVS-stored friends to carry over and report the loss as a regression. **Mitigation:** release-note the format change. Population is small enough this is acceptable. We can add a one-shot NVS-read-and-migrate path in a follow-up if real users complain.
- **Trade-off:** event-driven writes lose the most-recent mutation on a crash inside the write window (under 50 ms with atomic writes). Accepted because the alternative — periodic timer flushes — costs idle flash wear and is no better for crashes that happen mid-write.
- **Trade-off:** versioned binary blob vs. nanopb. Protobuf would have given us field-number-based forward-compat for free (add a new field, old readers skip it). Binary blob requires a version bump + drop-the-file for incompatible changes. We accept this because the friend record's persistable surface is unlikely to grow — `node`, `session_id`, `secret[16]` is a complete description of a paired peer's authentication state. Anything else is rendering metadata, derivable from elsewhere (NodeDB).

## Migration Plan

**Deploy:**

1. Land this change as a single patch block in `patch-t114.py` plus a matching block in `patch-native.py` (so smoke tests cover the new path).
2. Existing CI workflow (`pr-build-t114.yml`) builds an RC UF2 per push. QA flashes onto a T114, pairs two nodes, power-cycles, verifies friends list is intact and tracking still works without re-pairing (proves `secret[16]` survived).
3. Smoke test extension: `tests/smoke/pairing_test.py` gains a power-cycle leg that restarts the simulated node and asserts friend persistence post-restart.
4. Tag the firmware release once the smoke test plus a manual on-device verification both pass. Reference issue #25 in the release notes.

**Rollback:**

- The patch block is a single textual mutation guarded by a unique marker. Removing the block from `patch-t114.py` / `patch-native.py` and rebuilding restores prior behavior. No on-disk state from the new firmware causes problems for the old firmware: the old firmware does not look for `/prefs/friends.proto`, so the file simply sits unused.

## Open Questions

- **Q1:** Does writing a versioned binary blob through `NodeDB::saveProto` (using a degenerate `pb_msgdesc_t` for a single `bytes` field) work cleanly, or is direct `FSCom` I/O simpler? Both satisfy the atomicity requirement; pick at implementation time based on whichever produces a smaller patch and keeps the P0/P1 gate composition intact.
- **Q2:** Native simulator (`patch-native.py`) — confirm the LittleFS path is mounted at the same `/prefs/` virtual root in the native build, so the patch is genuinely cross-target. If the native build uses a different FS root, the patch needs a small per-target tweak.
- **Q3:** Should `places_[MAX_PLACES]` be persisted in the same patch (parallel concern, identical shape) or punted to a follow-up change? Punting unless the patch surface for places turns out to be < 30 lines additional.
