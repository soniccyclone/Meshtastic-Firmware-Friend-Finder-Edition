# Containerized Build Devloop — Design Document

## Problem

`.github/workflows/build-t114.yml` is the only way to currently produce `firmware/heltec_t114/firmware.uf2`, and it is failing. Each run has to:

1. Shallow-clone `LeapYeet/firmware` with recursive submodules (~1–2 GB of history + submodule data).
2. Resolve the nRF52 PlatformIO platform and pull down the ARM GCC toolchain (~500 MB) unless a `~/.platformio` cache hit occurs.
3. Patch `variants/nrf52840/heltec_mesh_node_t114/platformio.ini` to define `-DSS=0` (the SdFat workaround).
4. Run `pio run --environment heltec-mesh-node-t114` on a shared runner inside a 60-minute budget.

Any one of those steps can stall or fail: flaky submodule fetches, cache misses when `platformio.ini` hashes change, disk pressure on the runner, transient package-registry outages. The feedback loop is also poisonous for iteration — a one-line change to the variant patch costs 20–40 minutes of CI wall time and tells you essentially nothing when it times out mid-fetch.

The real problem is not "the workflow is flaky." It is that there is no local equivalent at all. Every experiment has to go through CI, so every experiment pays the full cold-cache cost.

## Goal

Reproduce the CI build on a developer workstation (linux/amd64 WSL2) with:

- One command to build.
- No network I/O on the hot path once the environment is set up.
- The same `firmware.uf2` output the CI job produces.
- A clean path to reuse the same environment inside GitHub Actions so local success implies CI success.

Non-goal for v1: ESP32-S3 targets (Heltec V3/V4, T3-S3). They use a different toolchain (Xtensa) and can be added as a second image.

## Key Insight

The workflow has three kinds of cost, and only one of them actually changes between runs:

| Cost | Size | Changes when |
| --- | --- | --- |
| ARM GCC toolchain + nRF52 platform | ~500 MB | `platformio.ini` pins change (rare) |
| `LeapYeet/firmware` source + submodules | ~1–2 GB | Upstream commits (occasional) |
| Variant patch + firmware edits | bytes | Every iteration |

CI treats all three as per-run work. The devloop should treat them by their actual change frequency: bake the toolchain into an image, keep the source as a long-lived host checkout, mount only the thing you're actually editing.

## Architecture

Host is linux/amd64 (x86_64 WSL2). The container image is also linux/amd64 — no emulation. `arm-none-eabi-gcc` is a cross-compiler, so the *container* architecture and the *firmware* architecture are independent; running an arm64 container via QEMU would add overhead for no benefit. This also matches GitHub's `ubuntu-latest` runners, so the same image works in CI.

```
Host (linux/amd64, WSL2)
  ~/code-stuff/
    LeapYeet-firmware/                      ← long-lived source checkout
    Meshtastic-Firmware-Friend-Finder-Edition/
      Containerfile
      entrypoint.sh
      patch-t114.py
      build.sh
      firmware/heltec_t114/firmware.uf2     ← artifact lands here
      .github/workflows/build-t114.yml      ← updated to use the image

Container (ff-builder:latest)
  /firmware-src        ← bind-mount ← host LeapYeet-firmware/
  /output              ← bind-mount ← host firmware/heltec_t114/
  /root/.platformio    ← baked into image layer (toolchain)
  /usr/local/bin/
    entrypoint.sh
    patch-t114.py
```

## Containerfile

```dockerfile
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PATH="/opt/pio-venv/bin:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
      git python3 python3-pip python3-venv \
      libusb-1.0-0 curl ca-certificates unzip \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/pio-venv \
    && /opt/pio-venv/bin/pip install --upgrade pip platformio

# Pre-install the nRF52 platform + ARM GCC toolchain by seeding from
# a throwaway clone of upstream. This bakes the ~500 MB download into
# an image layer, so no per-run fetches happen.
RUN git clone --depth=1 --recurse-submodules --shallow-submodules \
      https://github.com/LeapYeet/firmware.git /tmp/fw-seed \
    && cd /tmp/fw-seed \
    && pio pkg install --environment heltec-mesh-node-t114 \
    && rm -rf /tmp/fw-seed

COPY patch-t114.py      /usr/local/bin/patch-t114.py
COPY entrypoint.sh      /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/patch-t114.py

WORKDIR /firmware-src
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
```

## Supporting files

### patch-t114.py

The existing inline Python from the workflow, extracted so it can be reused unchanged by CI and by the container.

```python
#!/usr/bin/env python3
"""Ensure -DSS=0 is present in the T114 variant platformio.ini."""
import sys

path = "variants/nrf52840/heltec_mesh_node_t114/platformio.ini"
try:
    with open(path) as f:
        content = f.read()
except FileNotFoundError:
    sys.exit(f"ERROR: {path} not found. Is /firmware-src mounted correctly?")

if "-DSS=" in content:
    print("Skipped: -DSS already defined")
else:
    content = content.replace("-DHELTEC_T114", "-DHELTEC_T114\n  -DSS=0")
    with open(path, "w") as f:
        f.write(content)
    print("Patched: added -DSS=0")
```

### entrypoint.sh

```bash
#!/bin/bash
set -euo pipefail

echo "=== Patching T114 variant ==="
python3 /usr/local/bin/patch-t114.py

echo "=== Building heltec-mesh-node-t114 ==="
pio run --environment heltec-mesh-node-t114

echo "=== Copying artifact ==="
cp .pio/build/heltec-mesh-node-t114/firmware.uf2 /output/firmware.uf2
echo "Done: /output/firmware.uf2"
```

### build.sh

```bash
#!/bin/bash
# Local T114 build using Podman.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FIRMWARE_SRC="${FIRMWARE_SRC:-$SCRIPT_DIR/../LeapYeet-firmware}"
OUTPUT_DIR="$SCRIPT_DIR/firmware/heltec_t114"

if [[ ! -d "$FIRMWARE_SRC" ]]; then
  echo "ERROR: firmware source not found at $FIRMWARE_SRC" >&2
  echo "Clone it first:" >&2
  echo "  git clone --recurse-submodules https://github.com/LeapYeet/firmware.git $FIRMWARE_SRC" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"

podman run --rm \
  -v "$FIRMWARE_SRC":/firmware-src:Z \
  -v "$OUTPUT_DIR":/output:Z \
  ff-builder
```

The `:Z` label is required on SELinux-enabled hosts and harmless elsewhere.

## Devloop

One-time setup:

```bash
# Clone firmware source outside this repo to avoid committing gigabytes of submodule data.
git clone --recurse-submodules https://github.com/LeapYeet/firmware.git \
  ~/code-stuff/LeapYeet-firmware

# Build the image (one expensive ~15-minute build; rare thereafter).
cd ~/code-stuff/Meshtastic-Firmware-Friend-Finder-Edition
podman build -t ff-builder .
```

Per-iteration:

```bash
# Edit firmware, variant, or the patch script, then:
./build.sh
# → firmware/heltec_t114/firmware.uf2 updated
```

Expected hot-path time: 3–8 minutes, bounded almost entirely by compilation.

## GitHub Actions Integration

Two migration options, in order of risk:

**Option A — container locally only; leave CI alone.**  
Add `Containerfile`, `build.sh`, `entrypoint.sh`, `patch-t114.py` to the repo. CI workflow is untouched. Lowest blast radius; proves the container out first. Downside: CI remains flaky.

**Option B — publish the image and have CI use it.**  
Push to `ghcr.io/<owner>/ff-builder:latest` from a separate image-build workflow that only runs when the `Containerfile` or `patch-t114.py` change. Update `build-t114.yml` to run inside that container:

```yaml
jobs:
  build:
    runs-on: ubuntu-latest
    container:
      image: ghcr.io/<owner>/ff-builder:latest
      credentials:
        username: ${{ github.actor }}
        password: ${{ secrets.GITHUB_TOKEN }}
    steps:
      - uses: actions/checkout@v4
        with: { path: flasher }
      - uses: actions/checkout@v4
        with:
          repository: LeapYeet/firmware
          path: firmware-src
          submodules: recursive
      - name: Build
        working-directory: firmware-src
        run: /usr/local/bin/entrypoint.sh
      # existing artifact copy + commit + release steps unchanged
```

This eliminates toolchain downloads from CI entirely; the image layer *is* the cache, versioned independently of `platformio.ini`. Recommend adopting Option A first, validating local builds reproduce CI output byte-for-byte, then moving to B.

## Design Decisions

**Bake the toolchain, mount the source.**  
Toolchain changes rarely; source changes constantly. Baking matches the real change frequency and makes the image a single, reproducible cache artifact instead of a bind-mounted `~/.platformio` that is slow on first run and silently drifts after that.

**Patch script as a file, not an inline heredoc.**  
Lets both CI and the container use identical logic. Also makes it trivially upstreamable — if `LeapYeet/firmware` ever accepts a PR to define `SS` in the variant, the script becomes a no-op and can be deleted without touching anything else.

**Source checkout lives outside this repo.**  
Avoids anyone accidentally `git add`ing the firmware submodule tree. Also makes the firmware source reusable across multiple target images (future ESP32-S3 variant).

**Podman over Docker.**  
Rootless by default on WSL2; no daemon; same command surface. If a contributor has Docker, `alias podman=docker` works for the subset this doc uses.

## Image Rebuild Triggers

Rebuild the image (`podman build --no-cache -t ff-builder .`) when:

- `LeapYeet/firmware`'s `platformio.ini` pins a new toolchain or platform version.
- Upstream PlatformIO nRF52 platform ships a breaking update.
- The base Ubuntu image needs a security update you actually care about.

Everything else — firmware edits, variant patches, build-script changes — is a hot-path iteration and should not require rebuilding the image.

## Validation

Before claiming parity with CI, verify:

1. `sha256sum firmware/heltec_t114/firmware.uf2` from the local container matches the CI-produced artifact for the same source SHA.
2. `pio run` output is free of warnings that CI is not free of, or vice versa.
3. Flashing the local artifact to a real T114 yields identical boot behavior.

If (1) diverges, inspect build flags and library versions before trusting the devloop.

## Open Questions

1. **Source freshness.** The long-lived `LeapYeet-firmware` checkout drifts from upstream. Pin to a SHA in `build.sh` (reproducible, requires manual bumps) or `git pull --recurse-submodules` before each build (always fresh, occasionally surprising)?
2. **Submodule auth.** Does `LeapYeet/firmware` have any private submodules? If so, the baked seed clone in the Containerfile fails; the toolchain-install step would need to be reworked to avoid cloning the full tree (install packages via a minimal synthetic `platformio.ini` instead).
3. **Image hosting.** GHCR under the repo owner is the obvious default, but if the image ever embeds proprietary toolchains, revisit licensing before publishing publicly.
4. **Multi-target expansion.** When ESP32-S3 support is added, decide between one fat image (~1.5 GB, simpler) or `ff-builder-nrf52` + `ff-builder-esp32` (leaner, two things to maintain). Defer until the second target actually needs a devloop.
