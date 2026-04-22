#!/usr/bin/env python3
"""Apply build-environment workarounds to a pristine LeapYeet/firmware tree
so it builds on Linux for the heltec-mesh-node-t114 target.

Six independent issues this script works around:

1. SdFat (pulled transitively via Adafruit TinyUSB MSC) uses `SS` as a default
   parameter in SdFat.h:57, but the heltec_mesh_node_t114 variant does not
   define SS. Defining it as 0 satisfies the compiler; the value is never
   used at runtime because Meshtastic does not use SD cards on this target.

2. src/modules/MagnetometerModule.h:21-32 falls back to `I2C_SDA` / `I2C_SCL`
   / `I2C_SDA1` / `I2C_SCL1` when `I2C{0,1}_{SDA,SCL}_PIN` are not predefined.
   That fallback is ESP32-Arduino convention; the nRF52 Adafruit core uses
   `PIN_WIRE_SDA` / `PIN_WIRE_SCL` / `PIN_WIRE1_SDA` / `PIN_WIRE1_SCL`, so
   the T114 variant never defines the ESP32 names. Map the expected names
   onto the variant's PIN_WIRE_* pins via build flags.

3. src/modules/FriendFinderModule.cpp:5 includes "Power.h" (capital P), but
   the actual header is src/power.h (lowercase). Case-insensitive filesystems
   (macOS, Windows) silently resolve this; Linux builds fail. Rewrite the
   include to match the filename.

4. The Adafruit BluefruitLE nRF51 library (architectures=*) is pulled in
   transitively even though nothing in src/ includes Adafruit_BLE.h, and it
   redeclares err_t in a way that conflicts with the nRF52 Arduino core's
   `typedef uint32_t err_t`. Add lib_ignore to the T114 environment so it
   never enters the build.

5. FriendFinderModule.cpp calls `service->reloadConfig(SEGMENT_CONFIG)` on
   every tracking-session start and stop to propagate the temporary high-
   power GPS interval. reloadConfig persists the entire LocalConfig proto
   to /prefs/config.proto on LittleFS. Combined with the unrecovered I2C
   hangs on Wire1 (see #6), two writes-per-session amplifies the upstream
   LFS filesystem-corruption bug (meshtastic/firmware#5839), bricking T114s
   during normal Friend Finder use. Replace reloadConfig with a runtime-only
   observer notify so the GPS subsystem still picks up the change without
   flashing the proto.

6. MagnetometerModule.cpp::qmcReadRaw performs bus transactions on Wire1
   with no timeout and no bus recovery. When the QMC5883L holds SDA low
   after an EMI glitch or missed STOP, the thread stalls long enough to
   coincide with a LittleFS write from elsewhere, corrupting /prefs. Inject
   a canonical SCL-clock-pulse bus-recovery routine and invoke it on N
   consecutive read failures.

All workarounds can be retired when upstream variant.h / MagnetometerModule.*
/ FriendFinderModule.cpp / lib_deps are fixed; remove the corresponding block
and/or delete this script.

Run from the firmware source root.
"""
import sys

VARIANT_INI = "variants/nrf52840/heltec_mesh_node_t114/platformio.ini"
FRIEND_FINDER_CPP = "src/modules/FriendFinderModule.cpp"
MAGNETOMETER_CPP = "src/modules/MagnetometerModule.cpp"
MARKER = "# ff-builder patches"
FF_SESSION_MARKER = "// ff-builder: runtime-only GPS interval propagation"
MAG_RECOVERY_MARKER = "// ff-builder: I2C bus recovery"

INJECTED_FLAGS = """-DHELTEC_T114
  {marker}
  -DSS=0
  -DI2C0_SDA_PIN=PIN_WIRE_SDA
  -DI2C0_SCL_PIN=PIN_WIRE_SCL
  -DI2C1_SDA_PIN=PIN_WIRE1_SDA
  -DI2C1_SCL_PIN=PIN_WIRE1_SCL""".format(marker=MARKER)

ENV_HEADER = "[env:heltec-mesh-node-t114]"
INJECTED_LIB_IGNORE = """{header}
{marker} lib_ignore block
lib_ignore = Adafruit BluefruitLE nRF51""".format(header=ENV_HEADER, marker=MARKER)


def patch_variant_ini():
    try:
        with open(VARIANT_INI) as f:
            content = f.read()
    except FileNotFoundError:
        sys.exit(f"ERROR: {VARIANT_INI} not found. Run from firmware source root.")

    if MARKER in content:
        print(f"Skipped {VARIANT_INI}: already patched")
        return
    if "-DHELTEC_T114" not in content:
        sys.exit(f"ERROR: expected '-DHELTEC_T114' anchor in {VARIANT_INI} not found")
    if ENV_HEADER not in content:
        sys.exit(f"ERROR: expected '{ENV_HEADER}' header in {VARIANT_INI} not found")
    content = content.replace("-DHELTEC_T114", INJECTED_FLAGS)
    content = content.replace(ENV_HEADER, INJECTED_LIB_IGNORE, 1)
    with open(VARIANT_INI, "w") as f:
        f.write(content)
    print(f"Patched {VARIANT_INI}: build_flags + lib_ignore")


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


FF_RELOAD_CALL = "service->reloadConfig(SEGMENT_CONFIG);"
FF_RELOAD_REPLACEMENT = (
    "service->configChanged.notifyObservers(nullptr); "
    + FF_SESSION_MARKER
)


def patch_friend_finder_no_flash_on_session():
    try:
        with open(FRIEND_FINDER_CPP) as f:
            content = f.read()
    except FileNotFoundError:
        sys.exit(f"ERROR: {FRIEND_FINDER_CPP} not found. Run from firmware source root.")

    if FF_SESSION_MARKER in content:
        print(f"Skipped {FRIEND_FINDER_CPP}: reloadConfig already neutralized")
        return
    count = content.count(FF_RELOAD_CALL)
    if count == 0:
        print(f"Warning: {FRIEND_FINDER_CPP} has no reloadConfig(SEGMENT_CONFIG) calls to patch")
        return
    content = content.replace(FF_RELOAD_CALL, FF_RELOAD_REPLACEMENT)
    with open(FRIEND_FINDER_CPP, "w") as f:
        f.write(content)
    print(f"Patched {FRIEND_FINDER_CPP}: replaced {count} reloadConfig call(s) with runtime notify")


MAG_QMC_READ_RAW_ORIGINAL = """bool MagnetometerModule::qmcReadRaw(TwoWire &bus, uint8_t addr, int16_t &x, int16_t &y, int16_t &z) {
    uint8_t raw[6];
    if (!qmcReadRegs(bus, addr, QMC_REG_X_L, raw, sizeof(raw))) return false;
    // QMC is little-endian: X_L, X_H, Y_L, Y_H, Z_L, Z_H
    x = (int16_t)((raw[1] << 8) | raw[0]);
    y = (int16_t)((raw[3] << 8) | raw[2]);
    z = (int16_t)((raw[5] << 8) | raw[4]);
    return true;
}"""

MAG_QMC_READ_RAW_REPLACEMENT = """// """ + """ff-builder: I2C bus recovery on nRF52 Wire1 hangs.
// After N consecutive NAKs, detach the peripheral, clock SCL 9x to flush
// any slave holding SDA mid-ACK, issue a STOP, and re-attach. Unrecovered
// stalls here coincide with LittleFS writes and corrupt /prefs.
static void ff_qmcBusRecovery(TwoWire &bus, int sdaPin, int sclPin) {
    bus.end();
    pinMode(sclPin, OUTPUT);
    pinMode(sdaPin, INPUT_PULLUP);
    for (int i = 0; i < 9; ++i) {
        digitalWrite(sclPin, LOW);  delayMicroseconds(5);
        digitalWrite(sclPin, HIGH); delayMicroseconds(5);
    }
    pinMode(sdaPin, OUTPUT);
    digitalWrite(sdaPin, LOW);  delayMicroseconds(5);
    digitalWrite(sclPin, LOW);  delayMicroseconds(5);
    digitalWrite(sclPin, HIGH); delayMicroseconds(5);
    digitalWrite(sdaPin, HIGH); delayMicroseconds(5);
    bus.begin();
}

bool MagnetometerModule::qmcReadRaw(TwoWire &bus, uint8_t addr, int16_t &x, int16_t &y, int16_t &z) {
    static uint8_t ff_qmcReadFails = 0;
    uint8_t raw[6];
    if (!qmcReadRegs(bus, addr, QMC_REG_X_L, raw, sizeof(raw))) {
        if (++ff_qmcReadFails >= 3) {
            const int sda = (&bus == &Wire) ? I2C0_SDA_PIN : I2C1_SDA_PIN;
            const int scl = (&bus == &Wire) ? I2C0_SCL_PIN : I2C1_SCL_PIN;
            LOG_WARN("[Magnetometer] I2C hang on %s; attempting bus recovery",
                     (&bus == &Wire) ? "Wire" : "Wire1");
            ff_qmcBusRecovery(bus, sda, scl);
            ff_qmcReadFails = 0;
        }
        return false;
    }
    ff_qmcReadFails = 0;
    // QMC is little-endian: X_L, X_H, Y_L, Y_H, Z_L, Z_H
    x = (int16_t)((raw[1] << 8) | raw[0]);
    y = (int16_t)((raw[3] << 8) | raw[2]);
    z = (int16_t)((raw[5] << 8) | raw[4]);
    return true;
}"""


def patch_magnetometer_bus_recovery():
    try:
        with open(MAGNETOMETER_CPP) as f:
            content = f.read()
    except FileNotFoundError:
        sys.exit(f"ERROR: {MAGNETOMETER_CPP} not found. Run from firmware source root.")

    if MAG_RECOVERY_MARKER in content:
        print(f"Skipped {MAGNETOMETER_CPP}: bus recovery already injected")
        return
    if MAG_QMC_READ_RAW_ORIGINAL not in content:
        sys.exit(
            f"ERROR: expected qmcReadRaw body anchor in {MAGNETOMETER_CPP} not found "
            "(upstream source has drifted; review the function before re-patching)"
        )
    content = content.replace(MAG_QMC_READ_RAW_ORIGINAL, MAG_QMC_READ_RAW_REPLACEMENT)
    with open(MAGNETOMETER_CPP, "w") as f:
        f.write(content)
    print(f"Patched {MAGNETOMETER_CPP}: injected qmcReadRaw bus-recovery wrapper")


if __name__ == "__main__":
    patch_variant_ini()
    patch_friend_finder_include()
    patch_friend_finder_no_flash_on_session()
    patch_magnetometer_bus_recovery()
