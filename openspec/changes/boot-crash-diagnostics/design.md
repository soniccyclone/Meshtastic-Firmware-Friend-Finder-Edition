# Design: boot-crash-diagnostics

## Context

The nRF52840 reset cause register, `POWER->RESETREAS`, is a write-1-to-clear status register that records *why* the last reset happened. Its bits (per Nordic infocenter docs):

| Bit | Macro | Meaning |
|----:|------|---------|
| 0   | `POWER_RESETREAS_RESETPIN_Msk` | Physical reset pin asserted |
| 1   | `POWER_RESETREAS_DOG_Msk`      | Watchdog timeout |
| 2   | `POWER_RESETREAS_SREQ_Msk`     | Software reset (`NVIC_SystemReset()`) |
| 3   | `POWER_RESETREAS_LOCKUP_Msk`   | CPU lockup (typically a hard fault) |
| 16  | `POWER_RESETREAS_OFF_Msk`      | Wake from `SystemOFF` via GPIO |
| 17  | `POWER_RESETREAS_LPCOMP_Msk`   | Wake from `SystemOFF` via LPCOMP |
| 18  | `POWER_RESETREAS_DIF_Msk`      | Debug interface mode entered |
| 19  | `POWER_RESETREAS_NFC_Msk`      | Wake from `SystemOFF` via NFC field |

A power-on reset, *and* a brown-out reset, both produce **all zero** bits — the nRF52840 does not have a dedicated brown-out bit (the POF — Power Failure Comparator — fires a reset that looks identical to a true power-on). This is annoying but fine for our purposes: combined with knowing whether the user pulled the battery, an all-zero `RESETREAS` on a device that was running is the brown-out fingerprint by exclusion.

The register accumulates across resets: each reset *sets* its bit but does not clear previously-set bits. Only writing all-ones to the register (or losing power) clears it. So if you read it without clearing, a device that has had a watchdog timeout *and then* a hard fault will read `DOG | LOCKUP` even though those were two distinct events. The current upstream code reads but does not clear, which means production logs over time become uninterpretable — you cannot tell whether `DOG | LOCKUP` means "the most recent crash was both" (impossible) or "this device has crashed in both ways at some point since the battery was last unplugged."

## Goals

- Make the reset cause visible at production log levels (`LOG_INFO`).
- Decode each bit to a human-readable name in the same boot block, so the field-debugging workflow is: pull serial log, search for "Reset reason," read English.
- Clear `RESETREAS` after reading so each boot's log reflects exactly one event.
- Surface the configured brown-out threshold in the same block so any future reader of the log can see it without grepping source.

## Non-goals

- **Lowering the POFCON threshold from 2.4V toward 1.8V.** That is the most likely actual fix once we confirm brown-out is the cause, but it is a *behavior* change with a different risk profile (allowing operation closer to the flash-erase minimum voltage) and should ship as a separate PR with its own before/after test. Keeping diagnostic and fix in separate commits makes the cause-and-effect provable.
- **Persisting reset history across boots.** Writing each cause to flash on every boot would build a multi-reset history but adds a flash-write transient (worse for the very brown-out we are trying to characterize) and burns flash cycles. A single fresh value per boot is enough — the user can collect a sequence of boot logs across multiple crashes if they need a longitudinal view.
- **Reading the live POFCON setting from `NRF_POWER->POFCON`.** The brown-out detector is configured later in boot (inside `initBrownout()` called from `setBluetoothEnable()`); at the time `nrf52Setup()` runs the value is still the hardware default. Logging a static string keyed to the constant in `initBrownout()` is correct *and* doesn't lie about timing.

## Decisions

### D1 — Single patch block, expand-in-place

The upstream code is a single statement:

```cpp
LOG_DEBUG("Reset reason: 0x%x", why);
```

Anchor on that exact line (it is unique in `main-nrf52.cpp`) and replace it with the expanded block. Three smaller patches (decode, clear, threshold-log) would each need their own anchor and marker, multiplying failure modes if upstream drifts. One patch, one anchor, one marker, one fail-loudly path.

### D2 — `LOG_INFO`, not `LOG_WARN`

`LOG_WARN` is reserved for things that indicate a real problem. A clean boot after a clean shutdown also goes through this code path and will report `OFF` (wake from SystemOFF) or zero (cold boot) — neither is a warning. `LOG_INFO` is the right level: visible at production, but doesn't crowd `WARN`/`ERROR` channels that the rest of the firmware uses for actionable issues.

### D3 — One log line per decoded bit

A single concatenated message would be more compact but harder to grep ("did this device ever lock up?" becomes a substring search rather than a line match). One line per cause keeps log-grep ergonomics. The format is `"  -> NAME (one-sentence explanation)"` so the boot block reads top-down as a story.

### D4 — Clear via direct `NRF_POWER->RESETREAS = 0xFFFFFFFFu`

The Nordic SDK exposes `sd_power_reset_reason_clr(uint32_t)` for SoftDevice builds, but using the direct register write avoids a SoftDevice-vs-no-SoftDevice branch (the file's existing code mixes both styles — see [main-nrf52.cpp:124](../../../code-stuff/LeapYeet-firmware/src/platform/nrf52/BLEDfuScure.cpp#L124) writing `NRF_POWER->GPREGRET` directly while [main-nrf52.cpp:60](../../../code-stuff/LeapYeet-firmware/src/platform/nrf52/main-nrf52.cpp#L60) uses `sd_power_pof_enable`). The direct write works whether or not the SoftDevice is up, and `nrf52Setup()` runs before SoftDevice is enabled anyway.

### D5 — POFCON threshold logged as a hardcoded string

At the moment `nrf52Setup()` runs, `initBrownout()` has not yet been called, so `NRF_POWER->POFCON` still reflects the hardware default. Reading it would produce a misleading log line. Instead, log a static string naming the symbolic constant the firmware *will* configure later: `"Brown-out detector: configured later in initBrownout() to POWER_POFCON_THRESHOLD_V24 (~2.4V)"`. If a future change moves the threshold, the diagnostic string needs to follow — which is fine because it's right next to the change site in the same file.

### D6 — `patch-t114.py` only

`main-nrf52.cpp` is gated under the nRF52 platform build only. The native Portduino build does not compile this file, has no `NRF_POWER` symbol, and has no `POWER_RESETREAS_*_Msk` macros. Adding the patch to `patch-native.py` would either need a guard-and-skip (pointless since the file isn't in the native tree) or would fail loudly during the native patch run (worse). Skip native entirely.

## Risks & Mitigations

- **Upstream might rename the `LOG_DEBUG` line in a future sync.** The anchor string is exact; if it drifts, the patch script fails with an explicit `ERROR:` and the build halts. We notice immediately and update the anchor — the same failure mode every other patch block in the script already relies on.
- **POFCON threshold name in the log message could go stale if someone changes `initBrownout()` without touching the log line.** The log line and the `initBrownout()` constant live in the same file, ~190 lines apart. A reviewer touching the threshold will see the diagnostic comment immediately above the changed constant if the patch block is anchored carefully. We rely on the reviewer-discipline already established for this codebase rather than building a fragile cross-file lint.

## Open Questions

*(none — all symbols (`POWER_RESETREAS_*_Msk`, `POWER_POFCON_THRESHOLD_V24`) verified to exist in the upstream firmware tree at `src/platform/nrf52/softdevice/nrf_soc.h` and the Nordic SDK headers it transitively includes.)*
