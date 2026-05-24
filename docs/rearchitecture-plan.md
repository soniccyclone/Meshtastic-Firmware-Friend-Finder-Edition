# Rearchitecture Plan: Python Patcher → Owned Firmware Fork

## Problem Statement

The current repo is not a firmware project — it's a patching system for someone else's firmware project.
`patch-t114.py` contains ~1500 lines of C++ embedded as Python multiline strings that get `str.replace()`'d
onto a fresh LeapYeet clone at build time. This is wrong on every axis:

- No IDE support, no syntax highlighting, no compiler errors pointing to real lines
- String matching is fragile — one upstream whitespace change breaks the build silently
- Framework-level fixes (Wire_nRF52.cpp) require patching files by absolute container path, which is absurd
- The source of truth is a Python script, not C++
- It conflates "our work" with "LeapYeet's work" in a way that makes it impossible to evolve independently

The correct model: this repo IS the firmware. C++ lives here as C++. LeapYeet is a reference, not an upstream.

---

## Target Architecture

### What We Own

This repo becomes a proper firmware project — a fork of the Meshtastic codebase with T114 support and
our Friend Finder features built in as first-class C++. No patching step. No Python glue.

```
Meshtastic-Firmware-Friend-Finder-Edition/
├── src/                              # Meshtastic core source (vendored from upstream)
│   ├── modules/
│   │   ├── FriendFinderModule.cpp    # our code, owned C++
│   │   ├── FriendFinderModule.h
│   │   ├── MagnetometerModule.cpp    # our code, owned C++
│   │   ├── MagnetometerModule.h
│   │   └── ...                       # rest of Meshtastic modules unchanged
│   └── ...
├── lib/                              # PlatformIO project lib dir
│   └── Wire/
│       └── Wire_nRF52.cpp            # framework override: timeout-safe Wire
├── variants/
│   └── nrf52840/
│       └── heltec_mesh_node_t114/    # T114 board variant (from LeapYeet, owned by us)
│           └── platformio.ini
├── platformio.ini                    # top-level PIO config
├── Dockerfile                        # dead simple: clone this repo, pio run
├── build.sh
├── .github/workflows/build.yml
└── docs/
```

### What LeapYeet Becomes

A reference only. We look at it when we need to understand how they handled something
T114-specific. We do not pull from it, do not pin it, do not clone it in CI.
If LeapYeet fixes a T114 hardware bug we want, we read their diff and port it manually.

### What Meshtastic Upstream Becomes

The base we track for protocol and core feature updates. When Meshtastic cuts a release
we care about, we merge it in deliberately — not automatically. We own the merge decision.

---

## How PlatformIO `lib/` Works for Framework Overrides

PlatformIO's project `lib/` directory is scanned before framework libraries. If `lib/Wire/`
contains `Wire_nRF52.cpp`, PlatformIO compiles that file instead of the one in the
`framework-arduinoadafruitnrf52` package. No Dockerfile tricks, no absolute paths — it just works,
and the patched file is visible in this repo as real C++.

This is the right mechanism for any fix that touches the Arduino/nRF52 framework layer.

---

## Migration Plan

### Phase 0: Close the wrong PR, stop the bleeding

- Close PR #41 (the `qmcFailCount` fix — wrong approach, wrong layer)
- Close or archive the beads issue for it
- Do not merge any more changes to `patch-t114.py`

### Phase 1: Stand up the owned firmware repo

Goal: CI builds from our C++ source with no patching step.

1. **Seed the repo from LeapYeet at the pinned SHA** (`f49f9b7967311a08c7bf1c1af6e8f28671182cd1`)
   - `git clone` LeapYeet locally, then repoint the remote to our GitHub repo
   - This gives us T114 board support, Meshtastic core, and all submodules as a clean starting point
   - First commit message: "seed: LeapYeet firmware at f49f9b7 (Meshtastic T114 base)"

2. **Strip the patcher artifacts from this repo**
   - Delete `patch-t114.py`, `patch-native.py`
   - Rewrite `Dockerfile` — no LeapYeet clone, no patch step, just:
     ```dockerfile
     RUN git clone --recurse-submodules https://github.com/soniccyclone/Meshtastic-Firmware-Friend-Finder-Edition.git /firmware-src
     RUN cd /firmware-src && pio pkg install --environment heltec-mesh-node-t114
     ```
   - Rewrite `entrypoint.sh` — just `pio run --environment heltec-mesh-node-t114`
   - Rewrite `build.sh` accordingly
   - Delete `entrypoint-smoke.sh` if it references the patcher

3. **Move CI to build from this repo directly**
   - `build.yml` clones this repo (not LeapYeet), runs `pio run`
   - No patching step in CI either

4. **Verify clean build** — same `firmware.uf2` as the current patched build

### Phase 2: Wire fix the right way

Goal: Fix the actual device freeze in `Wire_nRF52.cpp` as owned C++.

1. Copy `Wire_nRF52.cpp` from the vendored framework package into `lib/Wire/`
2. Add `millis()`-based timeouts to all six TWIM spin loops in `endTransmission()` and `requestFrom()`:
   - On timeout: trigger `TASKS_STOP`, wait briefly for `EVENTS_STOPPED`, force-disable TWIM peripheral if still stuck, return error
3. Commit it as `lib/Wire/Wire_nRF52.cpp` — a real C++ file in this repo
4. Build and verify, then PR and close the gh39 issue properly

The timeout implementation:
```cpp
// instead of:
while(!_p_twim->EVENTS_TXSTARTED && !_p_twim->EVENTS_ERROR);

// do:
const uint32_t _deadline = millis() + 50;
while (!_p_twim->EVENTS_TXSTARTED && !_p_twim->EVENTS_ERROR) {
    if (millis() > _deadline) {
        _p_twim->TASKS_STOP = 1;
        // wait up to 10ms for clean stop
        const uint32_t _stop_deadline = millis() + 10;
        while (!_p_twim->EVENTS_STOPPED && millis() < _stop_deadline) {}
        if (!_p_twim->EVENTS_STOPPED) {
            // peripheral truly stuck — force disable/re-enable
            _p_twim->ENABLE = 0;
            _p_twim->ENABLE = 6; // TWIM enable value
        }
        return -1; // propagate error to caller
    }
}
```

### Phase 3: Migrate remaining patches as real C++

All the changes currently encoded in `patch-t114.py` need to become clean diffs on the source.
Since Phase 1 seeds from LeapYeet, many of these are already present in the seed. For the ones
that aren't, apply them as normal commits to the owned source:

- `patch_variant_ini()` → edit `variants/nrf52840/heltec_mesh_node_t114/platformio.ini` directly
- `patch_friend_finder_include()` → edit `src/modules/FriendFinderModule.h` directly
- `patch_friend_finder_persistence()` → edit `FriendFinderModule.cpp` directly
- `patch_menu_ordering()` → edit `src/graphics/draw/MenuHandler.cpp` directly
- `patch_compass_redesign()` → edit the relevant CPP/H files directly

Each patch becomes a git commit. The commit history IS the patch record. No Python needed.

### Phase 4: Establish merge discipline with Meshtastic upstream

Set up a git remote pointing at Meshtastic upstream:
```bash
git remote add meshtastic https://github.com/meshtastic/firmware.git
```

When we want to absorb a Meshtastic release:
1. `git fetch meshtastic`
2. Review the diff manually — what changed in core? Anything that touches our modules?
3. Cherry-pick or merge selectively
4. Resolve conflicts in our owned files

This is a deliberate, human-gated operation. No automated upstream tracking.

---

## What We're NOT Doing

- We are not forking Meshtastic upstream from scratch and manually porting T114 support.
  LeapYeet already did that work correctly. We take their T114 base, then own it forward.
- We are not keeping `patch-t114.py` as a "migration shim." Once Phase 1 is done it's deleted.
- We are not using git submodules for LeapYeet. We vendor the source at the seed commit.
  A submodule would recreate the upstream-dependency problem in a different form.

---

## Open Questions Before Starting

1. **Repo identity**: Does this repo get renamed / repointed, or do we push the seeded firmware
   source to the existing `soniccyclone/Meshtastic-Firmware-Friend-Finder-Edition` remote?
   The latter is simpler — same GitHub URLs, same CI secrets.

2. **Friend Finder protobuf**: The `meshtastic/friendfinder.pb.h` generated file — is that
   vendored in LeapYeet already, or does it need to be generated from a `.proto` source?
   Need to confirm before Phase 1.

3. **`patch-native.py`**: There's also a native build patcher. Does that follow the same
   migration path, or is the native build being dropped?

4. **Submodule depth**: LeapYeet uses submodules (RadioLib, etc.). When we seed from LeapYeet,
   do we commit those submodules as-is, or convert them to vendored copies?
   Keeping submodules is standard; vendoring is more reproducible but heavier.

---

## Why Not Earlier?

The Python patcher was a reasonable first-pass interpretation of "apply patches to Meshtastic."
It was wrong — `git format-patch` and owning the source are the correct interpretations.
The mistake was recognized in issue #40. This plan is the correction.
