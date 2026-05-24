# Wire I2C Timeout Fix — Plan & Architecture Decision

## The Actual Bug

`Wire_nRF52.cpp` (from `framework-arduinoadafruitnrf52`) uses bare spin loops with no timeout:

```cpp
// endTransmission()
while(!_p_twim->EVENTS_TXSTARTED && !_p_twim->EVENTS_ERROR);
while(!_p_twim->EVENTS_LASTTX    && !_p_twim->EVENTS_ERROR);
while(!_p_twim->EVENTS_STOPPED);   // ← no ERROR check here either

// requestFrom()
while(!_p_twim->EVENTS_RXSTARTED && !_p_twim->EVENTS_ERROR);
while(!_p_twim->EVENTS_LASTRX   && !_p_twim->EVENTS_ERROR);
while(!_p_twim->EVENTS_STOPPED);
```

When the QMC5883L magnetometer's TWIM peripheral gets stuck (LoRa RF noise is the likely trigger), no events fire. The CPU spins forever. The device freezes. That's the crash TezlaKid reported — the log ends exactly there.

The PR #41 fix (`qmcFailCount` in `MagnetometerModule.cpp`) **does not fix this**. It only runs if `qmcReadRaw` returns, which it never does once the peripheral is stuck. It was the wrong approach.

## The Correct Fix

Add `millis()`-based timeouts to every TWIM spin loop in `Wire_nRF52.cpp`. On timeout:
1. Trigger `TASKS_STOP` to request a clean TWIM stop
2. Spin on `EVENTS_STOPPED` with a second short timeout
3. If still stuck, force-disable the TWIM peripheral (`ENABLE = 0`) and re-enable it
4. Return an error code so the caller (MagnetometerModule) can handle it gracefully

The magnetometer already retries on read failure — once the Wire layer stops hanging, the QMC can reinitialize on the next `runOnce()` cycle.

## Architecture Decision: Python Patcher vs. C++ Fork

### Current approach: Python patcher (`patch-t114.py`)

C++ source lives in multiline Python strings and gets `str.replace()`'d onto a clean LeapYeet clone at build time. This was a misread of what "apply a patch" should mean.

**Problems:**
- C++ in Python strings: no syntax highlighting, no compiler errors pointing to real lines, no IDE support
- String-match fragility: any whitespace or upstream edit breaks the patch silently at build time
- `Wire_nRF52.cpp` is a framework file, not in the LeapYeet tree — patching it means even more convoluted absolute-path rewrites
- The whole thing is inside-out: the source of truth is a Python script, not C++

### Better approach: Fork the upstream, own the C++

Pin a specific LeapYeet SHA (we already pin `f49f9b7967311a08c7bf1c1af6e8f28671182cd1`) and check the actual modified C++ files into this repo. Build system copies them (or the Docker image starts from our fork directly).

For `Wire_nRF52.cpp` specifically: the Arduino-nRF52 framework is itself a pinned PlatformIO package. Options:
- **Override via `lib/` directory**: PlatformIO's `lib_deps` can point at a local path. Copy `Wire_nRF52.cpp` into a local `lib/Wire/` directory and PlatformIO will prefer it.
- **Framework overlay**: `framework-arduinoadafruitnrf52` is a Git repo — we can fork it, patch it, and pin the fork SHA in `platformio.ini`.
- **Direct file copy in Dockerfile**: Brutally simple — `COPY patched/Wire_nRF52.cpp /path/in/container/`. No string replacement, actual C++.

The `lib/` override is the cleanest path: it's a standard PlatformIO mechanism, keeps the patched file visible in the repo as real C++, and doesn't require forking a separate framework repo.

## Recommended Plan

1. Close PR #41 (wrong fix, wrong approach)
2. Decide on the architecture: migrate away from Python patcher toward actual C++ files checked into this repo, using PlatformIO's `lib/` override for framework-level fixes
3. For the Wire fix specifically: copy the patched `Wire_nRF52.cpp` into `lib/Wire/` with timeout logic written directly in C++
4. Longer term: migrate the rest of the Friend Finder and Magnetometer patches out of `patch-t114.py` and into checked-in `.cpp`/`.h` files

## Open Questions

- Do we migrate all of `patch-t114.py` at once, or incrementally (Wire fix first, then the rest)?
- Does the LeapYeet submodule go into this repo directly, or do we keep a separate clone + overlay model?
