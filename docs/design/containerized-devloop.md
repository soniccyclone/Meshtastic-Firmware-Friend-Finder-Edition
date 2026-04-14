# Containerized Build Devloop — Design Document

## Problem

The GitHub Actions workflow crashes intermittently because each run must:

1. Clone `LeapYeet/firmware` with recursive submodules (~1–2 GB of git history + submodule data)
2. Download the nRF52 PlatformIO platform + ARM GCC toolchain (~500 MB)
3. Do both within a 60-minute timeout window on a shared runner

Any one of these can fail or timeout: flaky submodule fetches, cold cache misses when `platformio.ini` changes, disk pressure on the runner. The devloop is also prohibitively slow — a single experimental build takes 20–40 minutes of CI time with no local iteration possible.

## Goal

A Podman-based build environment that:

- Runs locally with a single command
- Pre-bakes the nRF52 toolchain into the image so no network I/O happens at build time
- Mounts the firmware source directory from the host for fast iteration
- Produces the same `firmware.uf2` artifact as the current CI
- Can optionally replace the GitHub Actions runner (container-based CI)

## Architecture

```
Host filesystem
  firmware-src/          ← git clone of LeapYeet/firmware (done once)
  Meshtastic-Firmware-Friend-Finder-Edition/
    Containerfile        ← image definition
    build.sh             ← local build script
    docs/
    firmware/heltec_t114/firmware.uf2  ← artifact lands here

Podman container (ff-builder image)
  /firmware-src/         ← bind-mount from host (read-write)
  /output/               ← bind-mount → host firmware/heltec_t114/
  ~/.platformio/         ← baked into image layer (toolchain pre-installed)
```

The image bakes in the expensive part (toolchain). The source stays on the host so edits are immediately visible to the container without a rebuild.

## Containerfile

```dockerfile
FROM ubuntu:24.04

# Avoid interactive prompts during package install
ENV DEBIAN_FRONTEND=noninteractive

# System dependencies
# git: submodule operations inside the build
# python3-pip: PlatformIO install
# libusb-1.0-0: required by some nRF52 tools
# curl, ca-certificates: package fetches
RUN apt-get update && apt-get install -y --no-install-recommends \
      git \
      python3 \
      python3-pip \
      python3-venv \
      libusb-1.0-0 \
      curl \
      ca-certificates \
      unzip \
    && rm -rf /var/lib/apt/lists/*

# Install PlatformIO into a venv to avoid the system pip warning
RUN python3 -m venv /opt/pio-venv \
    && /opt/pio-venv/bin/pip install --upgrade pip \
    && /opt/pio-venv/bin/pip install platformio
ENV PATH="/opt/pio-venv/bin:${PATH}"

# Pre-install the nRF52 platform + ARM GCC toolchain.
# This is the ~500 MB layer that eliminates the per-run download.
# We clone the minimum of the firmware repo needed just to resolve
# the exact package versions, then throw away the source.
#
# NOTE: If LeapYeet/firmware updates its platformio.ini to require a
# newer toolchain version, rebuild the image with:
#   podman build --no-cache -t ff-builder .
RUN git clone --depth=1 --recurse-submodules --shallow-submodules \
      https://github.com/LeapYeet/firmware.git /tmp/fw-seed \
    && cd /tmp/fw-seed \
    && pio pkg install --environment heltec-mesh-node-t114 \
    && rm -rf /tmp/fw-seed

# Apply the SS=0 patch as a reusable script so the container
# can patch any mounted firmware-src at run time.
COPY patch-t114.py /usr/local/bin/patch-t114.py

WORKDIR /firmware-src

# Default command: patch + build + copy artifact
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
```

## Supporting files

### patch-t114.py

The existing CI patch script, extracted to a standalone file:

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

if "-DSS=" not in content:
    content = content.replace("-DHELTEC_T114", "-DHELTEC_T114\n  -DSS=0")
    with open(path, "w") as f:
        f.write(content)
    print("Patched: added -DSS=0")
else:
    print("Skipped: -DSS already defined")
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

### build.sh (local invocation)

```bash
#!/bin/bash
# build.sh — local T114 build using Podman
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FIRMWARE_SRC="${FIRMWARE_SRC:-$SCRIPT_DIR/../LeapYeet-firmware}"
OUTPUT_DIR="$SCRIPT_DIR/firmware/heltec_t114"

if [[ ! -d "$FIRMWARE_SRC" ]]; then
  echo "ERROR: firmware source not found at $FIRMWARE_SRC"
  echo "Clone it first: git clone --recurse-submodules https://github.com/LeapYeet/firmware.git $FIRMWARE_SRC"
  exit 1
fi

mkdir -p "$OUTPUT_DIR"

podman run --rm \
  -v "$FIRMWARE_SRC":/firmware-src:Z \
  -v "$OUTPUT_DIR":/output:Z \
  ff-builder
```

The `:Z` label is required on SELinux-enabled systems (Fedora, RHEL, recent Ubuntu). On macOS or non-SELinux Linux it is harmless.

## Directory Layout

```
~/code-stuff/
  LeapYeet-firmware/                ← one-time clone (outside this repo)
  Meshtastic-Firmware-Friend-Finder-Edition/
    Containerfile
    entrypoint.sh
    patch-t114.py
    build.sh
    docs/
      containerized-devloop.md      ← this file
    firmware/heltec_t114/
      firmware.uf2                  ← build output
    .github/workflows/
      build-t114.yml                ← keep for releases, update to use image
```

Keeping `LeapYeet-firmware` outside this repo avoids accidentally committing gigabytes of submodule data here.

## Local Devloop

**One-time setup:**

```bash
# 1. Clone the firmware source
git clone --recurse-submodules https://github.com/LeapYeet/firmware.git \
  ~/code-stuff/LeapYeet-firmware

# 2. Build the container image (downloads toolchain into image, ~15 min once)
cd ~/code-stuff/Meshtastic-Firmware-Friend-Finder-Edition
podman build -t ff-builder .
```

**Per-build iteration:**

```bash
# Edit files in ~/code-stuff/LeapYeet-firmware/ as needed, then:
./build.sh
# → firmware/heltec_t114/firmware.uf2 is updated
```

Build time after image is built: ~3–8 minutes (no network, pure compilation).

## GitHub Actions Integration

Two options:

**Option A — Publish image to GHCR, use it in CI.**  
Push the image to `ghcr.io/<owner>/ff-builder:latest` and update the workflow to pull it:

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
        with:
          repository: LeapYeet/firmware
          path: firmware-src
          submodules: recursive
      - run: cd firmware-src && /usr/local/bin/entrypoint.sh
```

This eliminates the toolchain download from CI entirely. The image itself becomes the cache, versioned independently.

**Option B — Keep CI as-is for releases, use container locally for iteration.**  
Fewer moving parts. CI stays unchanged; the container is purely a local development tool. This is the lower-risk starting point.

## Key Design Decisions

**Why pre-bake the toolchain instead of mounting a cache volume?**  
Podman named volumes require explicit management; bind-mounting `~/.platformio` from the host means the first run is still slow. Baking into the image means the image is the cache — reproducible, shareable, version-controlled via the Containerfile.

**Why mount firmware-src rather than baking it in?**  
The source changes frequently (that's the whole point of the devloop). Baking it in would require an image rebuild on every source edit. Mounting keeps the 15-minute image build as a rare event and the 3–8 minute compile as the common case.

**Why keep the patch as a separate script rather than a git commit to LeapYeet/firmware?**  
The SS=0 fix is a build-environment workaround, not a semantic code change. Keeping it as an applier script means it can be upstreamed or retired without tangling the container definition.

**Image rebuild triggers:**  
Rebuild with `podman build --no-cache -t ff-builder .` when:
- `LeapYeet/firmware` changes `platformio.ini` (new toolchain version)
- Upstream nRF52 platform has a breaking update
- The base Ubuntu image has a critical security update

## Open Questions

1. **Multi-target support.** The ESP32-S3 targets (Heltec V3/V4, T3-S3) use a different toolchain (Xtensa). A single image could serve all targets but would be ~1.5 GB. Alternatively, separate `ff-builder-nrf52` and `ff-builder-esp32` images keep each lean.

2. **Submodule freshness.** The one-time clone of `LeapYeet/firmware` will drift from upstream. Decide whether to pin to a specific commit or run `git pull --recurse-submodules` before each build.

3. **ARM cross-compilation on non-x86 hosts.** On an ARM Mac or Raspberry Pi the image will work via emulation (slow) or need a separate `linux/arm64` image variant. Most likely not a concern for this project.
