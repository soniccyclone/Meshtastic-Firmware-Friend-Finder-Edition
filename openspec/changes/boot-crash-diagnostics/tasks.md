# Tasks: boot-crash-diagnostics

## 1. Patch infrastructure

- [x] 1.1 Added `MAIN_NRF52_CPP` and `BOOT_DIAG_MARKER` constants near the existing markers in `patch-t114.py`.
- [x] 1.2 Added `patch_boot_crash_diagnostics()` anchoring on the exact upstream `LOG_DEBUG("Reset reason: 0x%x", why);` substring (verified unique in `main-nrf52.cpp`) and replacing with the expanded diagnostic block (raw hex at `LOG_INFO`, eight decoded bit lines via `POWER_RESETREAS_*_Msk`, zero-case "POWER-ON or BROWN-OUT" line, POFCON threshold log, `NRF_POWER->RESETREAS = 0xFFFFFFFFu` clear). Idempotent on `BOOT_DIAG_MARKER`.
- [x] 1.3 Wired `patch_boot_crash_diagnostics()` into `main()` in `patch-t114.py` after the existing patch calls.
- [x] 1.4 Verified against the firmware tree from a clean state: all five blocks "Patched" on first run, all five "Skipped" on second run, `main-nrf52.cpp` contains `LOG_INFO("Reset reason:`, eight `POWER_RESETREAS_*_Msk` decode lines, `NRF_POWER->RESETREAS = 0xFFFFFFFFu`, and the `POWER_POFCON_THRESHOLD_V24` log line.
- [x] 1.5 `patch-native.py` intentionally left unchanged (`main-nrf52.cpp` is not in the native build tree).

## 2. Build verification

- [ ] 2.1 `make clean && make build` succeeds end-to-end and produces `firmware/heltec_t114/firmware.uf2`.
- [ ] 2.2 Inspect the build log for the new `LOG_INFO` format strings — confirm they reach the compiler unmangled (no escaping artifacts from the patch script).

## 3. On-device verification

- [ ] 3.1 Flash the patched UF2 to a known-good T114. On boot, confirm the serial log contains:
   - `Reset reason: 0x...` at `INFO` level
   - At least one decoded `-> NAME (...)` line, or the `POWER-ON or BROWN-OUT` line on a true cold start
   - The POFCON threshold log line
- [ ] 3.2 Trigger a software reset via UI (e.g., reboot from settings). On the next boot, confirm `-> SREQ (software NVIC_SystemReset)` appears and that no historical bits are also reported (validating the clear-on-read behavior).
- [ ] 3.3 Trigger a watchdog (if there is a debug path to do so) or simulate a hard fault. Confirm `-> DOG` or `-> LOCKUP` appears on the next boot.
- [ ] 3.4 Flash to one of the crashing field devices and capture a boot log after a real crash event. Share the log — that is the diagnostic payload this whole change exists to produce.

## 4. Capability spec

- [x] 4.1 `openspec/changes/boot-crash-diagnostics/specs/boot-crash-diagnostics/spec.md` exists with five ADDED requirements covering log level, bit decode, clear-after-read, POFCON threshold reporting, and patch idempotency.
- [ ] 4.2 After the change archives, the spec content moves to `openspec/specs/boot-crash-diagnostics/spec.md` (handled by the archive workflow; not a manual step here).
