# Boot-time crash diagnostics

## Why

We are investigating intermittent field crashes on T114 devices (issue [#32](https://github.com/soniccyclone/Meshtastic-Firmware-Friend-Finder-Edition/issues/32)). The leading hypothesis is brown-out under transient load (LoRa TX, BLE advertising) through an aged-battery's rising internal resistance — but we cannot confirm because the firmware does not surface enough information about reset causes in the field log. Without that, every "the device crashed" report is unfalsifiable: brown-out, watchdog, hard-fault, and physical-button-press all look the same from outside.

Upstream Meshtastic already reads the nRF52 `POWER->RESETREAS` register at boot in [main-nrf52.cpp:242-245](../../../code-stuff/LeapYeet-firmware/src/platform/nrf52/main-nrf52.cpp#L242-L245), but the implementation has three usability problems for field diagnostics:

1. **Logged at `LOG_DEBUG`** — invisible at production log levels. A user pulling a serial log from a crashing device sees nothing.
2. **Raw hex dump, no bit decode.** The reader has to grep the Nordic header to interpret each bit (`DOG`, `LOCKUP`, `RESETPIN`, etc.).
3. **Never cleared.** `RESETREAS` is write-1-to-clear hardware; if you don't reset it after reading, every boot accumulates bits from the entire reset history since the last battery removal. After three different crash types in a row, the value is the OR of all three — informative once you understand that semantic, confusing without it.

Additionally, the brown-out detector threshold is set to `POWER_POFCON_THRESHOLD_V24` (2.4V) at [main-nrf52.cpp:58](../../../code-stuff/LeapYeet-firmware/src/platform/nrf52/main-nrf52.cpp#L58) inside `initBrownout()`. That's substantial headroom above the nRF52840's actual operating floor (~1.7V — also the minimum for flash erase). Surfacing this threshold in the same boot log gives any future reader instant context for whether brown-out reports are happening at a high configured threshold or a low one.

This change is **pure diagnostic** — no behavior modification. It turns an invisible log line into a human-readable boot banner so the next field crash report tells us what we are actually fighting. If the diagnostic confirms brown-out (the expected outcome), a follow-up change will lower the POFCON threshold from 2.4V toward 1.8V; that change is intentionally **out of scope here** so the diagnostic-vs-behavior boundary is unambiguous in the commit history.

## What Changes

- **`src/platform/nrf52/main-nrf52.cpp` (`nrf52Setup()`, lines 242-245)**: Replace the single `LOG_DEBUG("Reset reason: 0x%x", why);` with a block that:
  1. Logs the raw `RESETREAS` value at `LOG_INFO` so it appears at production log levels.
  2. Emits one decoded `LOG_INFO` line per bit set (`RESETPIN`, `DOG`, `SREQ`, `LOCKUP`, `OFF`, `LPCOMP`, `DIF`, `NFC`) using the Nordic-provided `POWER_RESETREAS_*_Msk` macros.
  3. If the register is zero, logs a single line explicitly noting "POWER-ON or BROWN-OUT" — the two reset causes the nRF52840's `RESETREAS` register cannot distinguish (both produce a fully cleared register).
  4. Logs the configured POFCON threshold name (`POWER_POFCON_THRESHOLD_V24`) and a human-readable voltage so the threshold is visible without grepping source.
  5. Clears `RESETREAS` by writing `0xFFFFFFFFu`, so the next boot's log reflects only fresh reset causes — not the historical union.
- Ships as a new marker-guarded patch block in `patch-t114.py`. `patch-native.py` is **not** touched: the native Portduino build doesn't compile `main-nrf52.cpp` at all (no nRF52 SDK headers, no `NRF_POWER` symbol). Same single-target shape as the existing `patch_variant_ini` block.

## Capabilities

### New Capabilities

- `boot-crash-diagnostics`: normative requirements for what the firmware must log at boot about reset cause. Encodes "the reset reason is a load-bearing diagnostic; do not let it disappear back to DEBUG-level or back to raw-hex-only" so future upstream syncs do not silently regress.

### Modified Capabilities

*(none)*

## Impact

- **Patch infrastructure**: one new marker-guarded block in `patch-t114.py`. Same shape as `patch_friend_finder_include` / `patch_friend_finder_persistence` — anchor on the upstream `LOG_DEBUG("Reset reason: 0x%x", why);` substring, replace with the expanded block, fail loudly if the anchor disappears.
- **Build**: no new dependencies. The `POWER_RESETREAS_*_Msk` macros come from `<nrf.h>`, which `main-nrf52.cpp` already includes transitively via its existing nRF52 SDK usage in the same file (`NRF_POWER`, `NVIC_SystemReset`, `sd_power_pof_enable`, etc.).
- **Runtime cost**: one boot-time block of ~10 conditional `LOG_INFO` lines. Microseconds of execution, fewer than 1 KB of `.rodata` for the format strings. Zero ongoing cost — this code only runs once per boot in `nrf52Setup()`.
- **Test surface**: existing `entrypoint-smoke.sh` build verifies the patch applies and compiles. There is no native-build path for `main-nrf52.cpp` to exercise; verification is by reading the boot log from a flashed T114.
- **Risk**: very low. Diagnostic-only — no register writes that change device behavior (the `RESETREAS = 0xFFFFFFFFu` write is a *clear* of a read-only-by-default status register, not a configuration change). Worst realistic failure is a typo in a `POWER_RESETREAS_*_Msk` constant name; that would produce a compile-time error from the patched file, caught by the build before flashing.
