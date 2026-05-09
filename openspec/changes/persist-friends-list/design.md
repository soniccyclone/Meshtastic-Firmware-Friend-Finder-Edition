## Context

This repo is a build-time wrapper around upstream `LeapYeet/firmware`. The actual `FriendFinderModule.cpp` lives in that upstream tree; this repo cherry-picks build-environment workarounds and behavior patches via `patch-t114.py` and `patch-native.py` against a pristine clone, then compiles in `ff-builder` containers. The architecture is deliberate: we do not maintain a fork of the firmware source, only a small, idempotent, marker-guarded set of textual patches. Any change to module behavior ships as a new patch block.

The friends-list state today (per `docs/design/t114-brick-fix.md`, H3): `FriendFinderModule` declares persistence helpers — call them `saveFriends()` / `loadFriends()` for this design — guarded by `#if defined(ARDUINO_ARCH_ESP32)` and backed by ESP32 `Preferences` (NVS). On the nRF52840 T114 the guards exclude the bodies entirely, so on that platform the friends table is RAM-only and dies on every reboot. The brick-fix doc explicitly punts the T114 friends-persistence story as out of scope, referencing back to whichever change picks it up — that change is this one.

The codebase's standard persistence pattern (per H5 of the same doc) is LittleFS protobuf files under `/prefs/`, written through `NodeDB::saveProto(filename, fields, src, fullAtomic)`. Atomic writes are temp-file-plus-rename and survive interrupted writes; non-atomic writes are reserved for files too large to fit two copies on the partition. `nodes.proto` (~17 KB) is the only non-atomic file in the system and it's the confirmed corruption surface.

The brick-fix design also defines a `safeToWrite()` predicate (P0/P1) that gates writes on radio-TX-idle and battery voltage. That work has not yet landed at the time of this proposal (the brick-fix doc is the design-of-record but no patch block exists in `patch-t114.py` yet for it). This design assumes either ordering: friends persistence is atomic-on-a-tiny-file, so it is safe in isolation; when `safeToWrite()` lands, friends writes route through it for additional defense-in-depth.

Stakeholders: Nathan (this repo), TezlaKid (the user who originally hit the brick path; will benefit from never re-pairing again), the LeapYeet/firmware upstream maintainers (no direct dependency, but the patch shape is what they would land if they accepted it).

## Goals / Non-Goals

**Goals:**

- Friends paired on a T114 survive power-off, battery swap, and intentional reboot.
- The same persistence mechanism works on ESP32 builds, replacing the existing NVS-Preferences path with the LittleFS protobuf path so both platforms share one file format.
- Persistence is crash-safe: an interrupted write never corrupts a previously-written friends list.
- The change ships as a single, idempotent, marker-guarded patch block in `patch-t114.py`, in the same shape as existing blocks.
- The persistence path composes cleanly with the P0/P1 brick-fix gate when that gate lands, with no rework of the friends path required at that point.
- The on-disk format is forward-compatible: adding fields to the friend record in the future does not invalidate files written by older firmware.

**Non-Goals:**

- Migrating data from the ESP32 NVS-Preferences key into the new LittleFS file. (Today's nRF52 friends are already lost every boot; today's ESP32 users start from a clean Preferences state on the new firmware. Migration would solve a problem fewer than a handful of users have.)
- Persisting *every* FriendFinder state — places, calibration, last-seen timestamps. Those are separate state buckets with separate sizing and write-rate properties; out of scope here.
- Cross-device sync of friends lists over the mesh.
- Encryption of the on-disk file. The mesh credentials and node DB are not encrypted at rest today; friends shouldn't be either, in this change. Scope creep.
- Implementing the P0/P1 `safeToWrite()` gate itself — that's the brick-fix change. We define the integration point and stub it cleanly so the gate slots in without rework.

## Decisions

### D1 — File format: protobuf via nanopb at `/prefs/friends.proto`

We follow the existing convention. Meshtastic already links nanopb for `NodeInfoLite`, `LocalConfig`, etc. A new `meshtastic_FriendList` message with a repeated `Friend` submessage is the smallest possible addition — no new dependency, no new serialization surface, full forward-compat by virtue of protobuf's field-number semantics.

**Alternatives considered:**

- *JSON.* Rejected: no existing dependency, parser code-bloat, no forward-compat story without manual versioning.
- *Raw struct dump.* Rejected: no forward-compat at all. Adding a field invalidates every existing file. We would be inventing a worse protobuf.
- *Reuse `LocalConfig`.* Rejected: `LocalConfig` is the device's main config blob and is written at much higher frequency. Coupling friend mutations to that file means every pair/rename triggers a `LocalConfig` write — wrong granularity.

### D2 — Atomic writes only (`fullAtomic=true`)

The encoded file at the maximum supported friend count (default 16, conservative upper bound 32) is well under 1 KB even with generous fields (32-byte name, 4-byte ID, a couple of flags = ~40 bytes per friend × 32 = 1280 bytes worst case; realistic case under 512 bytes). LittleFS easily holds two copies. There is no reason to use the non-atomic write path that caused the brick.

Atomic-only is also the cleanest composition with the P0/P1 gate: P1 (low-voltage) applies to atomic writes too, but the failure mode is "the temp file is truncated" — the existing file survives. So even if the P1 gate ships later, the friends file is structurally safe in the meantime.

### D3 — Event-driven writes, not periodic

Write only on mutation — pair complete, unpair, rename. No polling timer, no shadow-state diffing. Rationale:

- Mutations are infrequent (humans pair friends in seconds, not milliseconds).
- An event-driven model has zero idle-write cost — important on battery.
- A timer-based "save dirty state every N seconds" model is strictly worse: it adds idle flash wear, adds complexity (dirty flag, drain function), and has higher worst-case latency to disk than event-driven for the same correctness guarantee.

The tradeoff is that a crash *between* a mutation and the immediately-following write loses that one mutation. With atomic writes that window is well under 50 ms. We accept this — it is the same correctness boundary every other event-driven persister in Meshtastic operates under.

### D4 — Patch as a `patch-t114.py` block, not a fork

Existing repo discipline. The block follows the same pattern as the existing two:

1. Anchor on a unique string in `FriendFinderModule.cpp` (e.g. the existing `#if defined(ARDUINO_ARCH_ESP32)` guard around the friends save).
2. Emit a marker-guarded replacement with the new LittleFS-based body.
3. Idempotent: if the marker is already in the file, skip.

The patch additionally injects a `friends.proto` nanopb schema file (or extends the existing meshtastic protobuf set) into the firmware tree. If the upstream protobuf set is generated rather than checked in, the patch instead inlines the schema as a hand-written C struct + nanopb encode/decode shim, bypassing the codegen step. Final form depends on inspection of the upstream tree at implementation time — both shapes are textually patchable.

### D5 — Integration point for `safeToWrite()`

The friends-save path calls a single function — illustratively `nodeDB->saveProto("/prefs/friends.proto", FriendList_fields, &friendList, /*fullAtomic=*/true)`. When the brick-fix P0/P1 patch lands, `saveProto` itself gains the `safeToWrite()` gate as its precondition. No change to the friends-save call site is required at that point — the gate composes by virtue of routing through the standard wrapper.

If P0/P1 lands *before* this change, we benefit immediately. If P0/P1 lands *after*, friends writes are still atomic and safe in isolation; they just don't yet defer on TX-idle / voltage conditions. Either ordering is acceptable.

### D6 — Removal of the ESP32-only guard, not a sibling code path

We do not keep the existing `Preferences`-based ESP32 path alongside a new LittleFS path. Both platforms get the LittleFS path. Rationale:

- Two persistence paths means two formats to maintain, two sets of bugs, two code paths to test.
- ESP32 LittleFS is a first-class platform in Meshtastic; the standard persisters (`nodes.proto`, `config.proto`) already use LittleFS on ESP32.
- The format becomes platform-portable as a side benefit (D1's forward-compat applies cross-platform).

The cost is that ESP32 users on the new firmware do not see their old NVS-stored friends. As called out in non-goals, this is a few-user / one-time cost, weighed against the simpler architecture.

## Risks / Trade-offs

- **Risk:** P0/P1 lands later, and during the gap, a friends write coincides with a TX burst on a low battery and the temp-file write truncates. **Mitigation:** atomic writes mean the previous file is intact — at worst, the most-recent pair/rename is lost on next boot. The user re-pairs once. This is strictly better than today (lose everything every boot) and bounded.
- **Risk:** LittleFS partition is too small for `friends.proto` + `nodes.proto` + `config.proto` + temp copies under worst-case fragmentation. **Mitigation:** the new file is under 1 KB. The brick-fix backburner item A (larger LFS partition) addresses the broader version of this risk if it ever fires; this change does not depend on it.
- **Risk:** Upstream `LeapYeet/firmware` reshuffles `FriendFinderModule.cpp` enough that the patch anchor breaks. **Mitigation:** the patch follows the same idempotency / marker pattern as existing blocks, fails loudly on missing anchor (matches `patch-t114.py`'s existing `sys.exit` style), and is small enough to re-anchor in minutes when an upstream rev moves. Same risk every existing patch carries.
- **Risk:** ESP32 users expect their NVS-stored friends to carry over and report the loss as a regression. **Mitigation:** release-note the format change. Population is small enough this is acceptable. We can add a one-shot NVS-read-and-migrate code path in a follow-up if real users complain — the migration code is contained and removable later.
- **Trade-off:** event-driven writes lose the most-recent mutation on a crash inside the write window (under 50 ms with atomic writes). Accepted because the alternative — periodic timer flushes — costs idle flash wear and is no better for crashes that happen mid-write.
- **Trade-off:** keeping the friends format separate from `nodes.proto` rather than folding friends into the node DB. Folding would inherit the non-atomic write path, which is the corruption surface. Separation is correct.

## Migration Plan

**Deploy:**

1. Land this change as a single patch block in `patch-t114.py` plus the corresponding `patch-native.py` change for the native simulator (so smoke tests cover the new path).
2. Existing CI workflow (`pr-build-t114.yml`) builds an RC UF2 per push. QA flashes onto a T114, pairs two nodes, power-cycles, verifies friends list is intact.
3. Smoke test extension: `tests/smoke/pairing_test.py` gains a power-cycle leg that restarts the simulated node and asserts friend persistence post-restart.
4. Tag the firmware release once the smoke test plus a manual on-device verification both pass. Reference issue #25 in the release notes.

**Rollback:**

- The patch block is a single textual mutation guarded by a unique marker. Removing the block from `patch-t114.py` and rebuilding restores prior behavior. No on-disk state from the new firmware causes problems for the old firmware: the old firmware does not look for `/prefs/friends.proto`, so the file simply sits unused. (Optional: add a one-line cleanup in the rolled-back firmware to delete the file, but not required.)

## Open Questions

- **Q1:** Does the upstream `FriendFinderModule.cpp` already declare a `Friend` struct with stable layout, or do we define one in this patch? Answer requires reading the upstream tree at implementation start. Affects whether the patch defines a new nanopb message inline or extends an existing schema.
- **Q2:** Where does the patch live in `patch-t114.py` relative to the existing blocks — same file, separate file, separate runner script? Likely same file with a clear comment delineating the block; revisit if the file grows past ~300 lines.
- **Q3:** Maximum friend count. Today's RAM-only table presumably has a compile-time `MAX_FRIENDS`. Implementation should surface that constant in the persistence size budget calculation; if it's larger than ~32, revisit the under-1-KB target in D2.
- **Q4:** Native simulator (`patch-native.py`) needs the same persistence path so smoke tests cover it. Is the native build's filesystem path identical (`/prefs/`) or simulator-rooted? Resolve during implementation; affects nothing else in this design.
