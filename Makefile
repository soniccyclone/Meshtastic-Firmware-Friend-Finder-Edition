# ff-builder — containerized T114 firmware devloop.
# See docs/design/containerized-devloop.md for the design.

REPO_DIR      := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
FIRMWARE_SRC  ?= $(abspath $(REPO_DIR)/../LeapYeet-firmware)
IMAGE         ?= ff-builder
OUTPUT_DIR    := $(REPO_DIR)/firmware/heltec_t114
OUTPUT        := $(OUTPUT_DIR)/firmware.uf2

.PHONY: help setup image rebuild build shell clean

help:
	@echo "ff-builder — T114 firmware devloop"
	@echo
	@echo "Targets:"
	@echo "  make build    — compile T114 firmware (runs setup + image if needed)"
	@echo "  make setup    — clone LeapYeet/firmware into \$$FIRMWARE_SRC if missing"
	@echo "  make image    — build the $(IMAGE) container image if missing"
	@echo "  make rebuild  — force-rebuild the container image (--no-cache)"
	@echo "  make shell    — drop into a shell inside the image"
	@echo "  make clean    — remove the built firmware.uf2 artifact"
	@echo
	@echo "Env:"
	@echo "  FIRMWARE_SRC = $(FIRMWARE_SRC)"
	@echo "  IMAGE        = $(IMAGE)"
	@echo "  OUTPUT       = $(OUTPUT)"

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

clean:
	rm -f "$(OUTPUT)"
