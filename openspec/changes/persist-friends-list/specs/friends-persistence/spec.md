## ADDED Requirements

### Requirement: Friends list survives reboot

The system SHALL persist the FriendFinder paired-friends table to non-volatile storage and restore it on boot, such that a user who pairs a friend, then power-cycles the device, finds that friend still present in the friends list with all of its load-bearing fields (mesh node number, pairing session ID, shared secret) intact and usable for continued mutual authentication without re-pairing.

#### Scenario: Pair, reboot, friend remains usable

- **WHEN** a user completes a pairing handshake with another node, then powers the device off and back on
- **THEN** the paired node SHALL appear in the friends list on boot with the same `node`, `session_id`, and `secret[16]` it had before reboot, and the user SHALL be able to track that friend / receive tracking from that friend without performing the pairing handshake again

#### Scenario: Multiple friends survive a reboot

- **WHEN** a user pairs N distinct nodes (N up to `MAX_FRIENDS = 8`) over multiple sessions, then power-cycles the device
- **THEN** all N paired nodes SHALL be present in the friends list on boot, in any order, with no entries lost or corrupted, and each entry's `secret[16]` SHALL match the value that was in RAM before reboot byte-for-byte

#### Scenario: Reboot with empty friends list

- **WHEN** a user has never paired with any node and power-cycles the device
- **THEN** the friends list on boot SHALL be empty, and the absence of the persistence file SHALL NOT produce a fault, ERROR-level log line, or boot delay beyond the I/O cost of one failed file open

### Requirement: Friend removals persist

The system SHALL persist removals from the friends list immediately, such that a friend the user explicitly removes does not reappear after a power cycle.

#### Scenario: Remove a friend, reboot, friend is gone

- **WHEN** a user removes a paired friend through the device UI (`removeFriendByListIndex` or equivalent), then power-cycles the device
- **THEN** the removed friend SHALL NOT appear in the friends list on boot

### Requirement: Persistence writes are crash-safe (best-effort on nRF52)

The system SHALL write the friends-list persistence file via `SafeFile` with `fullAtomic = true`. On platforms where `SafeFile` honors the flag (ESP32 family), the underlying write SHALL use temp-file-plus-rename so that an interrupted write does not corrupt the previously-written friends list. On nRF52 (T114), `SafeFile` collapses to in-place write (per [SafeFile.cpp:10-13](../../../../code-stuff/LeapYeet-firmware/src/SafeFile.cpp#L10-L13)); on this platform the crash-safety guarantee is best-effort, bounded by the small file size (~200 bytes worst case = one or two flash-page operations). The brick-fix P0 gate, when it lands, will close the underlying reset-during-write window for nRF52.

#### Scenario: Interrupted write does not corrupt prior state (ESP32)

- **WHEN** a write to the friends-list persistence file is interrupted before completion on an ESP32 build
- **THEN** the prior, last-successfully-written friends list SHALL still load correctly on the next boot, and at most the single in-flight change SHALL be lost

#### Scenario: Interrupted write may truncate the live file (nRF52)

- **WHEN** a write to the friends-list persistence file is interrupted before completion on an nRF52/T114 build
- **THEN** the live file MAY be left truncated (because `SafeFile` writes in place on this platform); on next boot the firmware SHALL detect the truncation via the magic / version / count header checks and boot with an empty friends list rather than crashing or refusing to mount the filesystem
- **AND** the firmware SHALL log a single WARN-level line indicating the file failed validation, so the user is informed why the friends list reset

#### Scenario: Persistence file is opened with full-atomic semantics

- **WHEN** the persistence layer writes the friends file
- **THEN** it SHALL invoke `SafeFile(filename, /*fullAtomic=*/true)` (the platform's atomic-or-best-effort path), so that on platforms that honor the flag the write completes via temp-file-plus-rename

### Requirement: Persistence writes compose with the T114 write-policy gate

On nRF52840 (T114) builds, the system SHALL route friends-list writes through the same safe-to-write predicate used by other persistent state — specifically the P0/P1 gate defined in `docs/design/t114-brick-fix.md` (radio TX-idle for at least the configured cooldown, battery voltage above the configured low-voltage threshold). When the predicate is unsatisfied, the write SHALL be deferred and re-attempted from the deferred-write drain path rather than executed immediately. If the P0/P1 gate has not yet landed, this requirement is satisfied trivially because all friends writes still go through `NodeDB::saveProto` — they pick up the gate the moment it is added to that wrapper.

#### Scenario: Write deferred during TX burst (post-P0/P1)

- **WHEN** the friends list is mutated while the radio has been in or recently exited a TX burst within the cooldown window, on a build where P0/P1 has landed
- **THEN** `NodeDB::saveProto` SHALL defer the write, log a single WARN-level line indicating deferral with reason `radio not idle`, and queue the friends segment as dirty
- **WHEN** the TX-idle and voltage conditions later become satisfied
- **THEN** the deferred-write drain SHALL flush the friends segment to flash with full-atomic semantics

#### Scenario: Write deferred at low battery (post-P0/P1)

- **WHEN** the friends list is mutated while battery voltage is below the configured low-voltage threshold, on a build where P0/P1 has landed
- **THEN** the system SHALL defer the write rather than execute it, log a WARN-level line indicating deferral with reason `voltage below threshold`, and the in-RAM friends list SHALL still reflect the mutation so the user sees no functional regression in the current session

### Requirement: Persistence is platform-portable

The persistence path SHALL operate on both ESP32 and nRF52840 (T114) builds. The upstream `FF_HAVE_NVS` guard (which is `1` on `ARDUINO_ARCH_ESP32` and `0` elsewhere) SHALL be replaced by an unconditional code path that uses LittleFS via `NodeDB::saveProto` / `NodeDB::loadProto` on every platform that compiles `FriendFinderModule`.

#### Scenario: nRF52 build persists friends

- **WHEN** the firmware is built for the `heltec-mesh-node-t114` PlatformIO environment
- **THEN** the friends-list persistence code path SHALL be compiled in and active, with no platform guard excluding it

#### Scenario: ESP32 build persists friends

- **WHEN** the firmware is built for any ESP32-based environment that ships FriendFinder
- **THEN** the friends-list persistence code path SHALL be compiled in and active, using the same on-disk file format as the nRF52 build, so a friends file is portable across platforms in principle

### Requirement: On-disk format is versioned and bounded

The friends-list persistence file SHALL begin with a small fixed-shape header containing a magic value, a version number, and an entry-size field, so that incompatible format changes can be detected at load time without misinterpreting old data. The file's worst-case size at `MAX_FRIENDS` SHALL fit comfortably under 1 KB to ensure atomic writes succeed on the T114's LittleFS partition.

The persisted per-friend record SHALL contain exactly the load-bearing fields from `FriendRecord` — specifically `node` (uint32), `session_id` (uint32), and `secret[16]` (16 bytes). Runtime-only fields (`last_data`, `last_heard_time`, `used` flag — the last derivable from presence in the file) SHALL NOT be serialized. The chosen serialization (versioned binary blob, optionally a nanopb message — see design.md) SHALL be implemented in a way that allows the implementer to satisfy this requirement without forking the upstream `meshtastic/protobufs` submodule.

#### Scenario: Version-mismatched file is dropped cleanly

- **WHEN** the firmware reads a friends file whose header `version` field is greater than the version it understands, or whose `entry_size` does not match the compiled-in record size
- **THEN** the firmware SHALL log a single WARN-level line naming the mismatch, leave the in-RAM friends list empty, and continue booting normally; it SHALL NOT crash, fault, or refuse to mount the filesystem

#### Scenario: Maximum friend count fits the size budget

- **WHEN** the friends list is full at `MAX_FRIENDS`
- **THEN** the encoded file size SHALL be under 1024 bytes, and `fullAtomic = true` writes SHALL succeed without LittleFS reporting insufficient free space for the temp-file-plus-rename strategy

### Requirement: Mutation sites all save

The system SHALL invoke the persistence write on every code path that mutates the in-RAM friends table — specifically `upsertFriend` (which already calls `saveFriends()` upstream), `removeFriendByListIndex` (which already calls `saveFriends()` upstream), and any future mutation site added by this change or downstream changes.

#### Scenario: Pair completes, friend is persisted

- **WHEN** the pairing handshake completes successfully and `upsertFriend` is invoked with the new friend's `node`, `session_id`, and `secret[16]`
- **THEN** `saveFriends()` SHALL be called as part of the same code path, before control returns to the caller

#### Scenario: Remove completes, removal is persisted

- **WHEN** `removeFriendByListIndex` clears the `used` flag on a slot
- **THEN** `saveFriends()` SHALL be called as part of the same code path, before control returns to the caller

### Requirement: Load happens once at module init, after FS is mounted

The system SHALL invoke `loadFriends()` exactly once during `FriendFinderModule` initialization, after the LittleFS `/prefs/` mount is available, and before the module begins servicing any pair/track requests.

#### Scenario: Load runs after filesystem mount

- **WHEN** `FriendFinderModule` is constructed during boot
- **THEN** `loadFriends()` SHALL run after the filesystem is mounted (i.e. `FSCom` is initialized to a usable state); if the filesystem is not yet mounted at that point, the load SHALL be deferred to the first `runOnce()` invocation that observes a mounted filesystem, rather than blocking init or silently skipping the load
