#!/usr/bin/env python3
"""Apply build-environment workarounds to a pristine LeapYeet/firmware tree
so it builds under the `native` (Portduino) PlatformIO environment on Linux
for CI/integration tests of the FriendFinder module.

Two independent issues this script works around:

1. src/modules/MagnetometerModule.h:21-32 expects `I2C0_SDA_PIN` /
   `I2C0_SCL_PIN` / `I2C1_SDA_PIN` / `I2C1_SCL_PIN` to be predefined by
   the platform (ESP32-Arduino convention). The T114 build works around
   this the same way (see patch-t114.py). Under Portduino there are no
   real pins and the header has Heltec-V3 integer fallbacks anyway, but
   we inject explicit build flags so the Portduino definition is
   independent of future changes to those fallback defaults. Values are
   0 because Portduino's I2C shim ignores them.

2. src/modules/FriendFinderModule.cpp:5 includes "Power.h" (capital P),
   but the actual header is src/power.h (lowercase). Case-sensitive
   Linux filesystems fail to resolve this; rewrite the include to match
   the filename. Same fix as patch-t114.py — duplicated so each patcher
   is self-contained and either can be run standalone.

Further native-specific issues (MagnetometerModule's unconditional
`extern TwoWire Wire1` and Adafruit_LIS3DH/Adafruit_AHRS Arduino-only
dependencies) are expected to surface on the first `pio run -e native`
attempt and will be handled in a follow-up patch.

Run from the firmware source root.
"""
import sys

NATIVE_INI = "variants/native/portduino/platformio.ini"
FRIEND_FINDER_CPP = "src/modules/FriendFinderModule.cpp"
MARKER = "# ff-builder native patches"

ANCHOR = "-I variants/native/portduino"
INJECTED_FLAGS = """{anchor}
  {marker}
  -DI2C0_SDA_PIN=0
  -DI2C0_SCL_PIN=0
  -DI2C1_SDA_PIN=0
  -DI2C1_SCL_PIN=0""".format(anchor=ANCHOR, marker=MARKER)


def patch_native_ini():
    try:
        with open(NATIVE_INI) as f:
            content = f.read()
    except FileNotFoundError:
        sys.exit(f"ERROR: {NATIVE_INI} not found. Run from firmware source root.")

    if MARKER in content:
        print(f"Skipped {NATIVE_INI}: already patched")
        return
    if ANCHOR not in content:
        sys.exit(f"ERROR: expected '{ANCHOR}' anchor in {NATIVE_INI} not found")
    content = content.replace(ANCHOR, INJECTED_FLAGS, 1)
    with open(NATIVE_INI, "w") as f:
        f.write(content)
    print(f"Patched {NATIVE_INI}: native_base build_flags")


def patch_friend_finder_include():
    try:
        with open(FRIEND_FINDER_CPP) as f:
            content = f.read()
    except FileNotFoundError:
        sys.exit(f"ERROR: {FRIEND_FINDER_CPP} not found. Run from firmware source root.")

    original = '#include "Power.h"'
    replacement = '#include "power.h"'
    if original not in content:
        if replacement in content:
            print(f"Skipped {FRIEND_FINDER_CPP}: already patched")
        else:
            print(f"Warning: {FRIEND_FINDER_CPP} has no Power.h include to patch")
        return
    content = content.replace(original, replacement)
    with open(FRIEND_FINDER_CPP, "w") as f:
        f.write(content)
    print(f"Patched {FRIEND_FINDER_CPP}: Power.h -> power.h")


if __name__ == "__main__":
    patch_native_ini()
    patch_friend_finder_include()
