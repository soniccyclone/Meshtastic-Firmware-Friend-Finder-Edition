## ADDED Requirements

### Requirement: Friends list survives reboot

The system SHALL persist the FriendFinder paired-friends table to non-volatile storage and restore it on boot, such that a user who pairs a friend, then power-cycles the device, finds that friend still present in the friends list with all of its attributes (node ID, display name, any per-friend flags) intact.

#### Scenario: Pair, reboot, friend remains

- **WHEN** a user completes a pairing handshake with another node, then powers the device off and back on
- **THEN** the paired node SHALL appear in the friends list on boot, with the same node ID and display name it had before reboot, and with no further user action required

#### Scenario: Multiple friends survive a reboot

- **WHEN** a user pairs N distinct nodes (N up to the configured max, e.g. 16) over multiple sessions, then power-cycles the device
- **THEN** all N paired nodes SHALL be present in the friends list on boot, in any order, with no entries lost or corrupted

#### Scenario: Reboot with empty friends list

- **WHEN** a user has never paired with any node and power-cycles the device
- **THEN** the friends list on boot SHALL be empty, and the absence of the persistence file SHALL NOT produce a fault, error log at ERROR level, or boot delay beyond the I/O cost of one failed file open

### Requirement: Friend removals persist

The system SHALL persist removals from the friends list immediately, such that a friend the user explicitly removes does not reappear after a power cycle.

#### Scenario: Remove a friend, reboot, friend is gone

- **WHEN** a user removes a paired friend through the device UI, then power-cycles the device
- **THEN** the removed friend SHALL NOT appear in the friends list on boot

### Requirement: Friend renames persist

The system SHALL persist user-edited friend display names such that a renamed friend keeps its new name across reboots.

#### Scenario: Rename a friend, reboot, name persists

- **WHEN** a user changes the display name of a paired friend through the device UI, then power-cycles the device
- **THEN** the friend SHALL appear in the friends list on boot with the new display name, not the original mesh-advertised name

### Requirement: Persistence writes are crash-safe

The system SHALL write the friends-list persistence file atomically (write-temp-then-rename), such that an interrupted write — including BOD reset, watchdog reset, or power loss mid-write — does not corrupt the existing on-disk friends list.

#### Scenario: Interrupted write does not corrupt prior state

- **WHEN** a write to the friends-list persistence file is interrupted before completion (e.g., by a forced reset)
- **THEN** the prior, last-successfully-written friends list SHALL still load correctly on the next boot, and at most the single in-flight change SHALL be lost

#### Scenario: Persistence file is opened with full-atomic semantics

- **WHEN** the persistence layer writes the friends file
- **THEN** it SHALL invoke the underlying save call with `fullAtomic = true` (or the platform-equivalent atomic semantics), so that the write completes via temp-file-plus-rename rather than in-place mutation

### Requirement: Persistence writes compose with the T114 write-policy gate

On nRF52840 (T114) builds, the system SHALL route friends-list writes through the same safe-to-write predicate used by other persistent state — specifically the P0/P1 gate defined in `docs/design/t114-brick-fix.md` (radio TX-idle for at least the configured cooldown, battery voltage above the configured low-voltage threshold). When the predicate is unsatisfied, the write SHALL be deferred and re-attempted from the deferred-write drain path rather than executed immediately.

#### Scenario: Write deferred during TX burst

- **WHEN** the friends list is mutated while the radio has been in or recently exited a TX burst within the cooldown window
- **THEN** the system SHALL defer the write, log a single WARN-level line indicating deferral with reason `radio not idle`, and queue the friends segment as dirty
- **WHEN** the TX-idle and voltage conditions later become satisfied
- **THEN** the deferred-write drain SHALL flush the friends segment to flash with full-atomic semantics

#### Scenario: Write deferred at low battery

- **WHEN** the friends list is mutated while battery voltage is below the configured low-voltage threshold
- **THEN** the system SHALL defer the write rather than execute it, log a WARN-level line indicating deferral with reason `voltage below threshold`, and the in-RAM friends list SHALL still reflect the mutation so the user sees no functional regression in the current session

### Requirement: Persistence is platform-portable

The persistence path SHALL operate on both ESP32 and nRF52840 (T114) builds — it SHALL NOT be gated behind `#if defined(ARDUINO_ARCH_ESP32)` or any equivalent platform guard that excludes nRF52.

#### Scenario: nRF52 build persists friends

- **WHEN** the firmware is built for the `heltec-mesh-node-t114` PlatformIO environment
- **THEN** the friends-list persistence code path SHALL be compiled in and active, with no platform guard excluding it

#### Scenario: ESP32 build persists friends

- **WHEN** the firmware is built for any ESP32-based environment that ships FriendFinder
- **THEN** the friends-list persistence code path SHALL be compiled in and active, using the same on-disk LittleFS file format as the nRF52 build, so a friends file is portable across platforms in principle

### Requirement: Forward-compatible serialization

The friends-list persistence file SHALL use a versioned, schema-evolvable serialization (protobuf via nanopb) so that future additions to the friend record (e.g. last-seen timestamp, custom icon, per-friend mute flag) can be added without invalidating existing on-disk files.

#### Scenario: Older firmware reads newer file

- **WHEN** a firmware version that does not know about a newly-added friend field reads a persistence file written by a newer firmware that does
- **THEN** the older firmware SHALL load the known fields successfully and ignore the unknown field, with no fault, no data loss for known fields, and a single INFO-level log line noting that unknown fields were skipped

#### Scenario: Newer firmware reads older file

- **WHEN** a firmware version with newly-added friend fields reads a persistence file written by an older firmware
- **THEN** the newer firmware SHALL load all fields present in the older file, default the missing fields to documented sensible defaults, and not require any migration step from the user

### Requirement: Persistence file size is bounded

The persistence implementation SHALL bound the maximum on-disk size of `/prefs/friends.proto` to a value small enough to safely write atomically on the T114's LittleFS partition (target: under 1 KB for the maximum supported friend count).

#### Scenario: Maximum friend count fits the budget

- **WHEN** the friends list is full (at the configured maximum count)
- **THEN** the encoded file size SHALL be under 1024 bytes, and `fullAtomic = true` writes SHALL succeed without LittleFS reporting insufficient free space for the temp-file-plus-rename strategy
