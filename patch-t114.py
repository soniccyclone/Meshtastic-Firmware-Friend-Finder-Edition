#!/usr/bin/env python3
"""Ensure -DSS=0 is present in the T114 variant platformio.ini.

SdFat (pulled transitively via Adafruit TinyUSB MSC) uses `SS` as a default
parameter in SdFat.h:57, but the heltec_mesh_node_t114 variant does not define
SS. Defining it as 0 satisfies the compiler; the value is never used at runtime
because Meshtastic does not use SD cards on this target.

Run from the firmware source root.
"""
import sys

PATH = "variants/nrf52840/heltec_mesh_node_t114/platformio.ini"

try:
    with open(PATH) as f:
        content = f.read()
except FileNotFoundError:
    sys.exit(f"ERROR: {PATH} not found. Run from firmware source root.")

if "-DSS=" in content:
    print("Skipped: -DSS already defined")
else:
    content = content.replace("-DHELTEC_T114", "-DHELTEC_T114\n  -DSS=0")
    with open(PATH, "w") as f:
        f.write(content)
    print("Patched: added -DSS=0")
