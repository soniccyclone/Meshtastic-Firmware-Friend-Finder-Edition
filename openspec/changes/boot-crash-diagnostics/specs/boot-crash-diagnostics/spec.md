## ADDED Requirements

### Requirement: Reset reason SHALL be logged at production log level

On every boot, the nRF52 platform setup ([main-nrf52.cpp:236](../../../../code-stuff/LeapYeet-firmware/src/platform/nrf52/main-nrf52.cpp#L236) `nrf52Setup()`) SHALL log the value of `NRF_POWER->RESETREAS` using `LOG_INFO` (not `LOG_DEBUG`), so the diagnostic appears in serial logs at production log levels without requiring a debug build.

#### Scenario: Boot log visible at default verbosity

- **WHEN** a T114 device boots with the default firmware log level
- **THEN** the serial log SHALL contain a line of the form `Reset reason: 0xNN` at `INFO` level, emitted from `nrf52Setup()` before any module initialization runs

### Requirement: Reset reason bits SHALL be decoded to human-readable names

The boot log SHALL emit one `LOG_INFO` line per set bit in `RESETREAS`, using the bit's symbolic name and a short English explanation. Decoded bits SHALL cover: `RESETPIN`, `DOG`, `SREQ`, `LOCKUP`, `OFF`, `LPCOMP`, `DIF`, `NFC`. When `RESETREAS == 0`, the log SHALL emit exactly one line explicitly noting "POWER-ON or BROWN-OUT" (both produce all-cleared bits on the nRF52840).

#### Scenario: Software-reset boot

- **WHEN** the device reboots after a call to `NVIC_SystemReset()` (e.g., user-triggered reboot from settings)
- **THEN** the boot log SHALL contain exactly one decoded line — `-> SREQ (software NVIC_SystemReset)` — and SHALL NOT contain decoded lines for any other reset cause from the same boot

#### Scenario: Cold-start or brown-out boot

- **WHEN** the device reboots with `RESETREAS == 0` (true power-on after battery removal, or brown-out reset)
- **THEN** the boot log SHALL emit a single line `-> POWER-ON or BROWN-OUT (no bits set; RESETREAS clears on power-loss)` so the operator can recognize the ambiguous-but-named case

### Requirement: RESETREAS SHALL be cleared after reading

After logging the decoded reset reason, the boot code SHALL clear `RESETREAS` by writing `0xFFFFFFFFu` to it (the nRF52 write-1-to-clear convention), so the next boot's log reflects only the most recent reset cause rather than the historical OR of all causes since the last power-off.

#### Scenario: Successive distinct resets each show their own cause

- **WHEN** the device experiences a watchdog timeout, reboots cleanly, then later experiences a hard-fault-induced lockup
- **THEN** the boot log after the watchdog SHALL show only `-> DOG`, and the boot log after the lockup SHALL show only `-> LOCKUP` — neither boot log SHALL show both

### Requirement: Brown-out detector threshold SHALL be reported in the boot log

The boot log SHALL include a `LOG_INFO` line naming the configured POFCON threshold (the symbolic constant passed to `sd_power_pof_threshold_set` inside `initBrownout()`), along with the corresponding voltage in human-readable form. This lets a future reader of any boot log immediately see the brown-out configuration without grepping source.

#### Scenario: Threshold visible on every boot

- **WHEN** the device boots
- **THEN** the boot log SHALL contain a line of the form `Brown-out detector: configured later in initBrownout() to POWER_POFCON_THRESHOLD_V24 (~2.4V)` (or the corresponding string if the threshold constant changes)

### Requirement: Patch SHALL ship as a marker-guarded block in patch-t114.py

The diagnostic injection SHALL be implemented as an idempotent, marker-guarded block in `patch-t114.py`. The block SHALL anchor on a stable substring in upstream `main-nrf52.cpp` such that running the script twice on a fresh upstream clone produces the same result as running it once. `patch-native.py` SHALL NOT be modified — `main-nrf52.cpp` is not in the native build tree.

#### Scenario: Patch is idempotent

- **WHEN** `patch-t114.py` is run twice in succession against a freshly-cloned upstream firmware tree
- **THEN** the second run SHALL print a "Skipped" line for the boot-crash-diagnostics block and SHALL produce no further file modifications

#### Scenario: Anchor disappears upstream

- **WHEN** a future upstream commit changes the `LOG_DEBUG("Reset reason: 0x%x", why);` line so the anchor substring no longer matches
- **THEN** the patch script SHALL exit non-zero with an explicit `ERROR:` message referencing the missing anchor — matching the existing `sys.exit(...)` discipline in `patch-t114.py`
