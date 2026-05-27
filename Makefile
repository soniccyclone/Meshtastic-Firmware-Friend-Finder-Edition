# ff-builder — containerized T114 firmware devloop.
# See docs/design/containerized-devloop.md for the design.

REPO_DIR      := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
FIRMWARE_SRC  ?= $(abspath $(REPO_DIR)/../LeapYeet-firmware)
IMAGE         ?= ff-builder
OUTPUT_DIR    := $(REPO_DIR)/firmware/heltec_t114
OUTPUT        := $(OUTPUT_DIR)/firmware.uf2

# Local devloop scratch — gitignored. patched-src and compile both land here so
# nothing host-side needs installing beyond podman.
OUT_DIR         := $(REPO_DIR)/output
PATCHED_SRC_DIR := $(OUT_DIR)/patched-src
COMPILED_FW     := $(OUT_DIR)/firmware.uf2

# Host-side Python testbed for talking to a flashed device over USB serial.
TESTBED_DIR  := $(REPO_DIR)/testbed
TESTBED_REQS := $(TESTBED_DIR)/requirements.txt
VENV         := $(TESTBED_DIR)/.venv
VENV_PIP     := $(VENV)/bin/pip
VENV_MESH    := $(VENV)/bin/meshtastic
LOG_DIR      := $(OUT_DIR)/logs
PORT         ?= /dev/ttyACM0

.PHONY: help setup image rebuild build shell clean patched-src compile testbed-venv meshtastic-log

help:
	@echo "ff-builder — T114 firmware devloop"
	@echo
	@echo "Targets:"
	@echo "  make build       — compile T114 firmware via build.sh → $(OUTPUT)"
	@echo "  make compile     — compile T114 firmware → $(COMPILED_FW)"
	@echo "  make patched-src — dump the post-patch source tree to $(PATCHED_SRC_DIR)"
	@echo "  make setup       — clone LeapYeet/firmware into \$$FIRMWARE_SRC if missing"
	@echo "  make image       — build the $(IMAGE) container image if missing"
	@echo "  make rebuild     — force-rebuild the container image (--no-cache)"
	@echo "  make shell       — drop into a shell inside the image"
	@echo "  make clean         — remove the built firmware.uf2 artifact + output/ scratch"
	@echo
	@echo "Testbed (host-side, talks to a flashed device):"
	@echo "  make testbed-venv  — create/refresh $(VENV) from $(TESTBED_REQS)"
	@echo "  make meshtastic-log — stream $(PORT) debug serial → $(LOG_DIR)/meshtastic-<ts>.log"
	@echo "                       (latest.log symlink updated; override with PORT=/dev/ttyXXX)"
	@echo
	@echo "Env:"
	@echo "  FIRMWARE_SRC = $(FIRMWARE_SRC)"
	@echo "  IMAGE        = $(IMAGE)"
	@echo "  OUTPUT       = $(OUTPUT)"
	@echo "  OUT_DIR      = $(OUT_DIR)"
	@echo "  PORT         = $(PORT)"
	@echo "  LOG_DIR      = $(LOG_DIR)"

setup:
	@if [ ! -d "$(FIRMWARE_SRC)" ]; then \
	  echo "Cloning LeapYeet/firmware to $(FIRMWARE_SRC)..."; \
	  git clone --recurse-submodules https://github.com/LeapYeet/firmware.git "$(FIRMWARE_SRC)"; \
	else \
	  echo "[setup] firmware source present: $(FIRMWARE_SRC)"; \
	fi

image:
	@if podman image exists $(IMAGE); then \
	  echo "[image] $(IMAGE) already built; use 'make rebuild' to force"; \
	else \
	  echo "[image] building $(IMAGE)..."; \
	  podman build -t $(IMAGE) "$(REPO_DIR)"; \
	fi

rebuild:
	podman build --no-cache -t $(IMAGE) "$(REPO_DIR)"

build: setup image
	@mkdir -p "$(OUTPUT_DIR)"
	FIRMWARE_SRC="$(FIRMWARE_SRC)" IMAGE="$(IMAGE)" "$(REPO_DIR)/build.sh"

shell:
	@mkdir -p "$(OUTPUT_DIR)"
	podman run --rm -it \
	  -v "$(FIRMWARE_SRC)":/firmware-src:Z \
	  -v "$(OUTPUT_DIR)":/output:Z \
	  --entrypoint /bin/bash \
	  $(IMAGE)

# Apply patch-t114.py to the baked-in /firmware-src inside the container, then
# dump three artifacts to $(OUT_DIR):
#   patched-src/   — full post-patch tree (minus .git and .pio)
#   patches.diff   — `git diff` of exactly what patch-t114.py changed
#   patches.stat   — `git diff --stat` summary (one line per touched file)
# Wiped first so output always reflects the current patch script.
patched-src: image
	@mkdir -p "$(OUT_DIR)"
	@rm -rf "$(PATCHED_SRC_DIR)" "$(OUT_DIR)/patches.diff" "$(OUT_DIR)/patches.stat"
	@mkdir -p "$(PATCHED_SRC_DIR)"
	@echo "[patched-src] applying patches and dumping tree + diff..."
	podman run --rm \
	  -v "$(OUT_DIR)":/output:Z \
	  --entrypoint /bin/bash \
	  $(IMAGE) -c 'set -euo pipefail; \
	    cd /firmware-src; \
	    python3 /usr/local/bin/patch-t114.py; \
	    git add -N . >/dev/null; \
	    git -c core.pager=cat diff --no-color --submodule=diff > /output/patches.diff; \
	    git -c core.pager=cat diff --stat --no-color > /output/patches.stat; \
	    echo "[patched-src] copying tree (excluding .git, .pio)..."; \
	    tar --exclude=./.git --exclude=./.pio -cf - -C /firmware-src . \
	      | tar -xf - -C /output/patched-src; \
	  '
	@echo "[patched-src] done:"
	@echo "  tree: $(PATCHED_SRC_DIR)"
	@echo "  diff: $(OUT_DIR)/patches.diff   (full unified diff)"
	@echo "  stat: $(OUT_DIR)/patches.stat   (per-file change summary)"

# Compile firmware using the baked-in pinned LeapYeet source. Same payload as
# `make build` (which routes through build.sh into firmware/heltec_t114/), but
# writes to the gitignored $(OUT_DIR) for ad-hoc devloop use.
compile: image
	@mkdir -p "$(OUT_DIR)"
	podman run --rm \
	  -v "$(OUT_DIR)":/output:Z \
	  $(IMAGE)
	@echo "[compile] done: $(COMPILED_FW)"

# Create / refresh the host-side Python venv. Re-runs pip install whenever
# requirements.txt is newer than the meshtastic entry-point binary.
$(VENV_MESH): $(TESTBED_REQS)
	@if [ ! -x "$(VENV)/bin/python" ]; then \
	  echo "[testbed-venv] creating $(VENV)..."; \
	  python3 -m venv "$(VENV)"; \
	fi
	@echo "[testbed-venv] installing $(TESTBED_REQS)..."
	"$(VENV_PIP)" install -r "$(TESTBED_REQS)"
	@touch "$(VENV_MESH)"

testbed-venv: $(VENV_MESH)

# Capture the firmware's SerialConsole debug stream to a timestamped logfile
# under $(LOG_DIR), and update $(LOG_DIR)/latest.log → that file. Runs until
# Ctrl-C. --noproto tells the firmware "no protobuf client here, just dump
# the human-readable debug console."
#
# `sg dialout -c` runs the capture inside the dialout group regardless of
# whether the calling shell inherited it — needed because adding yourself to
# dialout via usermod doesn't propagate into already-open shells. This is
# transparent if you already have dialout active.
meshtastic-log: $(VENV_MESH)
	@mkdir -p "$(LOG_DIR)"
	@if [ ! -e "$(PORT)" ]; then \
	  echo "[meshtastic-log] no device at $(PORT) — plug it in or pass PORT=/dev/ttyXXX"; \
	  exit 1; \
	fi
	@if ! getent group dialout | grep -qw "$$(id -un)"; then \
	  echo "[meshtastic-log] $$(id -un) is not in the dialout group system-wide."; \
	  echo "[meshtastic-log] Fix: sudo usermod -aG dialout $$(id -un)  (then re-login once)"; \
	  exit 1; \
	fi
	@ts=$$(date +%Y-%m-%dT%H-%M-%S); \
	logname="meshtastic-$$ts.log"; \
	logfile="$(LOG_DIR)/$$logname"; \
	ln -sfn "$$logname" "$(LOG_DIR)/latest.log"; \
	echo "[meshtastic-log] $(PORT) → $$logfile"; \
	echo "[meshtastic-log] tail with: tail -f $(LOG_DIR)/latest.log    (Ctrl-C this run to stop)"; \
	sg dialout -c "PYTHONUNBUFFERED=1 '$(VENV_MESH)' --port '$(PORT)' --noproto 2>&1 | tee '$$logfile'"

clean:
	rm -f "$(OUTPUT)"
	rm -rf "$(OUT_DIR)"
