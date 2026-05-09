# ff-builder — Meshtastic Friend Finder T114 firmware build environment.
#
# Bakes the nRF52 PlatformIO platform + ARM GCC toolchain into an image layer
# so that iterative builds incur zero network I/O. The firmware source is
# expected to be bind-mounted at /firmware-src at run time; the built
# firmware.uf2 is written to /output/firmware.uf2.
#
# Target arch: linux/amd64 (host-native on x86_64 WSL2 and GitHub
# ubuntu-latest runners). The toolchain is a cross-compiler that emits ARM
# Cortex-M4 binaries, so no arm64 container is required.

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PATH="/opt/pio-venv/bin:${PATH}"

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

RUN python3 -m venv /opt/pio-venv \
    && /opt/pio-venv/bin/pip install --upgrade pip \
    && /opt/pio-venv/bin/pip install platformio

# Clone LeapYeet/firmware into /firmware-src and install the nRF52 platform
# + ARM GCC toolchain. The clone is the default build source for CI
# (one-shot: podman build && podman run). For local iteration, bind-mount
# a host checkout over /firmware-src — the mount wins.
#
# Pin: f49f9b796731 was the upstream master tip on 2026-04-17 when the known-
# good v0.0.1 firmware was built. Floating HEAD is non-reproducible — bump
# this SHA deliberately when you want to absorb upstream changes.
ARG LEAPYEET_FIRMWARE_SHA=f49f9b7967311a08c7bf1c1af6e8f28671182cd1
RUN git clone --recurse-submodules https://github.com/LeapYeet/firmware.git /firmware-src \
    && cd /firmware-src \
    && git checkout "${LEAPYEET_FIRMWARE_SHA}" \
    && git submodule update --init --recursive \
    && pio pkg install --environment heltec-mesh-node-t114

COPY patch-t114.py /usr/local/bin/patch-t114.py
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/patch-t114.py /usr/local/bin/entrypoint.sh

WORKDIR /firmware-src
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
