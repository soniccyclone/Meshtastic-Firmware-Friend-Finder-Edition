#!/usr/bin/env python3
"""Apply build-environment workarounds to a pristine LeapYeet/firmware tree
so it builds under the `native` (Portduino) PlatformIO environment on Linux
for CI/integration tests of the FriendFinder module.

Four independent issues this script works around:

1. variants/native/portduino/platformio.ini does not predefine
   `I2C0_SDA_PIN` / `I2C0_SCL_PIN` / `I2C1_SDA_PIN` / `I2C1_SCL_PIN`.
   MagnetometerModule.h:18-32 expects them (ESP32-Arduino convention)
   and falls back to Heltec-V3 integers when they are not provided.
   Inject explicit build flags so the Portduino definition is
   independent of future changes to those fallback defaults. Values are
   0 because Portduino's I2C shim ignores them. Same trick patch-t114.py
   uses for the nRF52 variant.

2. src/modules/FriendFinderModule.cpp:5 includes "Power.h" (capital P)
   but the actual header is src/power.h (lowercase). Case-sensitive
   Linux filesystems fail to resolve this; rewrite the include to match
   the filename. Same fix as patch-t114.py — duplicated so each patcher
   is self-contained and either can be run standalone.

3. src/modules/MagnetometerModule.h includes Adafruit_LIS3DH and
   Adafruit_AHRS unconditionally and declares a class whose members
   reference those types and `TwoWire`. None of that is satisfiable
   under Portduino (Adafruit_AHRS is not in env:native lib_deps; the
   Portduino Arduino shim provides Wire but not Wire1, and no LIS3DH
   driver exists for the simulator). Wrap the Arduino-only includes and
   the entire class declaration in `#if !defined(ARCH_PORTDUINO)`, then
   provide a minimal Portduino stub class with no-op method bodies so
   that MenuHandler.cpp / FriendFinderModule.cpp / Modules.cpp continue
   to compile and link without behavior changes (the simulated node has
   no magnetometer hardware to drive).

4. src/modules/MagnetometerModule.cpp pulls in the same Arduino-only
   types and references `Wire1`. Wrap the entire file in
   `#if !defined(ARCH_PORTDUINO)` so it is a no-op on native, but keep
   the `MagnetometerModule *magnetometerModule = nullptr;` global
   definition outside the guard so the symbol the header declares
   `extern` always exists at link time.

Run from the firmware source root.
"""
import sys

NATIVE_INI = "variants/native/portduino/platformio.ini"
FRIEND_FINDER_CPP = "src/modules/FriendFinderModule.cpp"
MAG_HEADER = "src/modules/MagnetometerModule.h"
MAG_CPP = "src/modules/MagnetometerModule.cpp"

INI_MARKER = "# ff-builder native patches"
MAG_HEADER_MARKER = "// ff-builder native magnetometer guards"
MAG_CPP_MARKER = "// ff-builder native magnetometer guard"

INI_ANCHOR = "-I variants/native/portduino"
INI_INJECTED = """{anchor}
  {marker}
  -DI2C0_SDA_PIN=0
  -DI2C0_SCL_PIN=0
  -DI2C1_SDA_PIN=0
  -DI2C1_SCL_PIN=0""".format(anchor=INI_ANCHOR, marker=INI_MARKER)

MAG_HEADER_INCLUDES_ANCHOR = """#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_LIS3DH.h>
#include <Adafruit_AHRS.h>"""

MAG_HEADER_INCLUDES_REPLACEMENT = """#if !defined(ARCH_PORTDUINO)  {marker}
#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_LIS3DH.h>
#include <Adafruit_AHRS.h>
#endif""".format(marker=MAG_HEADER_MARKER)

MAG_HEADER_CLASS_OPEN_ANCHOR = "class MagnetometerModule"
MAG_HEADER_CLASS_OPEN_REPLACEMENT = (
    "#if !defined(ARCH_PORTDUINO)  " + MAG_HEADER_MARKER + "\n"
    "class MagnetometerModule"
)

MAG_HEADER_POST_CLASS_ANCHOR = "// Global pointer so other modules"
MAG_HEADER_POST_CLASS_REPLACEMENT = """#else  {marker}
// Portduino native build: no magnetometer hardware. Stub class so that
// MenuHandler / FriendFinder / Modules continue to compile and link
// without conditionals at every callsite.
class MagnetometerModule {{
public:
    bool    hasHeading() {{ return false; }}
    float   getHeading() {{ return 0.0f; }}
    void    startFigure8Calibration(uint32_t = 15000) {{}}
    bool    isCalibrating() const {{ return false; }}
    uint8_t getCalibrationPercent() const {{ return 0; }}
    void    startFlatSpinCalibration(uint32_t = 12000) {{}}
    bool    isFlatCalibrating() const {{ return false; }}
    uint8_t getFlatCalPercent() const {{ return 0; }}
    void    setNorthHere() {{}}
    void    clearNorthOffset() {{}}
    void    clearAllCalibration() {{}}
    void    dumpCalToLog() {{}}
    void    toggleFlipNorth() {{}}
    bool    isNorthFlipped() const {{ return false; }}
}};
#endif  {marker}

// Global pointer so other modules""".format(marker=MAG_HEADER_MARKER)


def patch_native_ini():
    try:
        with open(NATIVE_INI) as f:
            content = f.read()
    except FileNotFoundError:
        sys.exit(f"ERROR: {NATIVE_INI} not found. Run from firmware source root.")

    if INI_MARKER in content:
        print(f"Skipped {NATIVE_INI}: already patched")
        return
    if INI_ANCHOR not in content:
        sys.exit(f"ERROR: expected '{INI_ANCHOR}' anchor in {NATIVE_INI} not found")
    content = content.replace(INI_ANCHOR, INI_INJECTED, 1)
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


def patch_magnetometer_header():
    try:
        with open(MAG_HEADER) as f:
            content = f.read()
    except FileNotFoundError:
        sys.exit(f"ERROR: {MAG_HEADER} not found. Run from firmware source root.")

    if MAG_HEADER_MARKER in content:
        print(f"Skipped {MAG_HEADER}: already patched")
        return

    if MAG_HEADER_INCLUDES_ANCHOR not in content:
        sys.exit(
            f"ERROR: expected Arduino/Wire/Adafruit include block in {MAG_HEADER} not found"
        )
    if MAG_HEADER_CLASS_OPEN_ANCHOR not in content:
        sys.exit(
            f"ERROR: expected 'class MagnetometerModule' in {MAG_HEADER} not found"
        )
    if MAG_HEADER_POST_CLASS_ANCHOR not in content:
        sys.exit(
            f"ERROR: expected '{MAG_HEADER_POST_CLASS_ANCHOR}' anchor in {MAG_HEADER} not found"
        )

    content = content.replace(
        MAG_HEADER_INCLUDES_ANCHOR, MAG_HEADER_INCLUDES_REPLACEMENT, 1
    )
    content = content.replace(
        MAG_HEADER_CLASS_OPEN_ANCHOR, MAG_HEADER_CLASS_OPEN_REPLACEMENT, 1
    )
    content = content.replace(
        MAG_HEADER_POST_CLASS_ANCHOR, MAG_HEADER_POST_CLASS_REPLACEMENT, 1
    )

    with open(MAG_HEADER, "w") as f:
        f.write(content)
    print(f"Patched {MAG_HEADER}: gated Arduino includes + Portduino stub class")


def patch_magnetometer_cpp():
    try:
        with open(MAG_CPP) as f:
            content = f.read()
    except FileNotFoundError:
        sys.exit(f"ERROR: {MAG_CPP} not found. Run from firmware source root.")

    if MAG_CPP_MARKER in content:
        print(f"Skipped {MAG_CPP}: already patched")
        return

    patched = (
        "// " + MAG_CPP_MARKER.lstrip("/ ") + ": Portduino native build skips the\n"
        "// Arduino-only sensor implementation; the global pointer is defined\n"
        "// unconditionally so the extern in the header always links.\n"
        "#include \"MagnetometerModule.h\"\n"
        "MagnetometerModule *magnetometerModule = nullptr;\n"
        "\n"
        "#if !defined(ARCH_PORTDUINO)  " + MAG_CPP_MARKER + "\n"
        + content
        + "\n#endif  " + MAG_CPP_MARKER + "\n"
    )

    # The original .cpp also declares the global pointer; under the guard
    # block above, that declaration is now duplicated when ARCH_PORTDUINO is
    # not set. Strip the original definition from inside the guarded body so
    # we keep exactly one definition either way.
    original_global = "MagnetometerModule *magnetometerModule = nullptr;"
    body_start = patched.index("#if !defined(ARCH_PORTDUINO)")
    head = patched[:body_start]
    body = patched[body_start:]
    if original_global in body:
        body = body.replace(original_global, "// (definition lifted above the ARCH_PORTDUINO guard)", 1)
    patched = head + body

    with open(MAG_CPP, "w") as f:
        f.write(patched)
    print(f"Patched {MAG_CPP}: ARCH_PORTDUINO guard around full file")


if __name__ == "__main__":
    patch_native_ini()
    patch_friend_finder_include()
    patch_magnetometer_header()
    patch_magnetometer_cpp()
