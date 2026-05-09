#!/usr/bin/env python3
"""Apply build-environment workarounds to a pristine LeapYeet/firmware tree
so it builds on Linux for the heltec-mesh-node-t114 target.

Four independent issues this script works around:

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

All workarounds can be retired when upstream variant.h / MagnetometerModule.h
/ FriendFinderModule.cpp / lib_deps are fixed; remove the corresponding block
and/or delete this script.

Run from the firmware source root.
"""
import sys

VARIANT_INI = "variants/nrf52840/heltec_mesh_node_t114/platformio.ini"
FRIEND_FINDER_CPP = "src/modules/FriendFinderModule.cpp"
MENU_HANDLER_CPP = "src/graphics/draw/MenuHandler.cpp"
MAG_CPP = "src/modules/MagnetometerModule.cpp"
MARKER = "# ff-builder patches"
PERSIST_MARKER = "// ff-builder: persist friends to LittleFS"
MENU_ORDERING_MARKER = "// ff-builder: menu ordering"
MAG_POWER_MARKER = "// ff-builder: magnetometer power profile"

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


# --- Friends-list persistence (issue #25) ---------------------------------
#
# Upstream FriendFinderModule.cpp gates saveFriends()/loadFriends() behind
# FF_HAVE_NVS (= 1 only when ARDUINO_ARCH_ESP32). On the nRF52 T114 this means
# the friends list dies on every reboot. This block replaces the NVS-backed
# implementation with a LittleFS-backed one that compiles unconditionally on
# both platforms, using a small versioned binary blob at /prefs/friends.proto.
#
# Persisted fields per friend: node (uint32), session_id (uint32),
# secret[16]. Runtime-only fields (last_data, last_heard_time) are dropped.
# File format: 12-byte header (magic 'FFRD', version, entry_size, count) +
# 24 bytes per used entry. Worst case = 12 + 24*8 = 204 bytes — fits two
# copies on LittleFS comfortably so writes can be fullAtomic=true.
#
# Composes with the brick-fix P0/P1 safeToWrite() gate when that lands —
# routes through SafeFile (the same atomic-write primitive saveProto uses).
#
# See openspec/changes/persist-friends-list/{proposal,design}.md.

PERSIST_NVS_BLOCK_OLD = """#if defined(ARDUINO_ARCH_ESP32)
  #include <Preferences.h>
  static Preferences g_prefs;
  #define FF_HAVE_NVS 1
#else
  #define FF_HAVE_NVS 0
#endif"""

PERSIST_NVS_BLOCK_NEW = """{marker} (replaces FF_HAVE_NVS / Preferences gate)
// Friends are persisted to /prefs/friends.proto on LittleFS via SafeFile.
// FF_HAVE_NVS is left defined as 0 in case any other code path queries it.
#define FF_HAVE_NVS 0
#include "FSCommon.h"
#include "SafeFile.h"

namespace {{
struct PersistedFriendsHeader {{
    uint32_t magic;
    uint16_t version;
    uint16_t entry_size;
    uint8_t  count;
    uint8_t  reserved[3];
}};
struct PersistedFriend {{
    uint32_t node;
    uint32_t session_id;
    uint8_t  secret[16];
}};
static_assert(sizeof(PersistedFriendsHeader) == 12, "header size drift");
static_assert(sizeof(PersistedFriend) == 24, "entry size drift");

constexpr uint32_t FRIENDS_PERSIST_MAGIC   = 0x46465244u; // 'FFRD'
constexpr uint16_t FRIENDS_PERSIST_VERSION = 1;
constexpr const char *FRIENDS_PERSIST_FILE = "/prefs/friends.proto";
}} // namespace""".format(marker=PERSIST_MARKER)

PERSIST_LOAD_OLD = """void FriendFinderModule::loadFriends() {
    for (auto &f : friends_) {
        f = {};
        f.last_data = meshtastic_FriendFinder_init_default;
    }

#if FF_HAVE_NVS
    if (!g_prefs.begin("ffinder", true)) {
        LOG_WARN("[FriendFinder] NVS open failed; friends in RAM only");
        return;
    }
    size_t sz = g_prefs.getBytesLength("friends");
    if (sz == sizeof(friends_)) {
        g_prefs.getBytes("friends", friends_, sizeof(friends_));
        LOG_INFO("[FriendFinder] Loaded %u bytes of friends", (unsigned)sz);
    } else if (sz != 0) {
        LOG_WARN("[FriendFinder] Unexpected friends blob size=%u (expected %u), resetting", (unsigned)sz, sizeof(friends_));
    }
    g_prefs.end();
#endif
}"""

PERSIST_LOAD_NEW = """void FriendFinderModule::loadFriends() {{
    {marker}
    for (auto &f : friends_) {{
        f = {{}};
        f.last_data = meshtastic_FriendFinder_init_default;
    }}
#ifdef FSCom
    auto file = FSCom.open(FRIENDS_PERSIST_FILE, FILE_O_READ);
    if (!file) {{
        LOG_INFO("[FriendFinder] No persisted friends (%s missing)", FRIENDS_PERSIST_FILE);
        return;
    }}
    PersistedFriendsHeader hdr{{}};
    if (file.read(reinterpret_cast<uint8_t *>(&hdr), sizeof(hdr)) != sizeof(hdr)) {{
        LOG_WARN("[FriendFinder] friends file truncated header; ignoring");
        file.close();
        return;
    }}
    if (hdr.magic != FRIENDS_PERSIST_MAGIC) {{
        LOG_WARN("[FriendFinder] friends file bad magic 0x%08x; ignoring", (unsigned)hdr.magic);
        file.close();
        return;
    }}
    if (hdr.version != FRIENDS_PERSIST_VERSION || hdr.entry_size != sizeof(PersistedFriend)) {{
        LOG_WARN("[FriendFinder] friends file version/entry_size mismatch (v=%u sz=%u); booting empty",
                 (unsigned)hdr.version, (unsigned)hdr.entry_size);
        file.close();
        return;
    }}
    if (hdr.count > MAX_FRIENDS) {{
        LOG_WARN("[FriendFinder] friends file count=%u exceeds MAX_FRIENDS=%d; clamping",
                 (unsigned)hdr.count, MAX_FRIENDS);
        hdr.count = MAX_FRIENDS;
    }}
    int loaded = 0;
    for (uint8_t i = 0; i < hdr.count; ++i) {{
        PersistedFriend pf{{}};
        if (file.read(reinterpret_cast<uint8_t *>(&pf), sizeof(pf)) != sizeof(pf)) {{
            LOG_WARN("[FriendFinder] friends file truncated at entry %u", (unsigned)i);
            break;
        }}
        friends_[i].node       = pf.node;
        friends_[i].session_id = pf.session_id;
        memcpy(friends_[i].secret, pf.secret, 16);
        friends_[i].used       = true;
        ++loaded;
    }}
    file.close();
    LOG_INFO("[FriendFinder] Loaded %d friends from %s", loaded, FRIENDS_PERSIST_FILE);
#else
    LOG_WARN("[FriendFinder] No filesystem; friends in RAM only");
#endif
}}""".format(marker=PERSIST_MARKER)

PERSIST_SAVE_OLD = """void FriendFinderModule::saveFriends() {
#if FF_HAVE_NVS
    if (!g_prefs.begin("ffinder", false)) return;
    g_prefs.putBytes("friends", friends_, sizeof(friends_));
    g_prefs.end();
#endif
}"""

PERSIST_SAVE_NEW = """void FriendFinderModule::saveFriends() {{
    {marker}
    // TODO(brick-fix-P0): when safeToWrite() lands, route this through it
    // (or via NodeDB::saveProto, which the gate will wrap). Until then the
    // SafeFile path is still atomic — temp-file-plus-rename — so this is
    // safe in isolation; we just don't yet defer on TX-idle / low-voltage.
#ifdef FSCom
    {{
        concurrency::LockGuard g(spiLock);
        FSCom.mkdir("/prefs");
    }}
    PersistedFriendsHeader hdr{{}};
    hdr.magic      = FRIENDS_PERSIST_MAGIC;
    hdr.version    = FRIENDS_PERSIST_VERSION;
    hdr.entry_size = sizeof(PersistedFriend);
    hdr.count      = 0;
    for (const auto &f : friends_) if (f.used) ++hdr.count;

    SafeFile sf(FRIENDS_PERSIST_FILE, /*fullAtomic=*/true);
    sf.write(reinterpret_cast<const uint8_t *>(&hdr), sizeof(hdr));
    for (const auto &f : friends_) {{
        if (!f.used) continue;
        PersistedFriend pf{{}};
        pf.node       = f.node;
        pf.session_id = f.session_id;
        memcpy(pf.secret, f.secret, 16);
        sf.write(reinterpret_cast<const uint8_t *>(&pf), sizeof(pf));
    }}
    if (!sf.close()) {{
        LOG_ERROR("[FriendFinder] Failed to persist friends to %s", FRIENDS_PERSIST_FILE);
    }} else {{
        LOG_INFO("[FriendFinder] Persisted %u friends to %s", (unsigned)hdr.count, FRIENDS_PERSIST_FILE);
    }}
#endif
}}""".format(marker=PERSIST_MARKER)


def patch_friend_finder_persistence():
    try:
        with open(FRIEND_FINDER_CPP) as f:
            content = f.read()
    except FileNotFoundError:
        sys.exit(f"ERROR: {FRIEND_FINDER_CPP} not found. Run from firmware source root.")

    if PERSIST_MARKER in content:
        print(f"Skipped {FRIEND_FINDER_CPP}: persistence already patched")
        return

    if PERSIST_NVS_BLOCK_OLD not in content:
        sys.exit(f"ERROR: expected FF_HAVE_NVS gate block in {FRIEND_FINDER_CPP} not found")
    if PERSIST_LOAD_OLD not in content:
        sys.exit(f"ERROR: expected loadFriends() body in {FRIEND_FINDER_CPP} not found")
    if PERSIST_SAVE_OLD not in content:
        sys.exit(f"ERROR: expected saveFriends() body in {FRIEND_FINDER_CPP} not found")

    content = content.replace(PERSIST_NVS_BLOCK_OLD, PERSIST_NVS_BLOCK_NEW, 1)
    content = content.replace(PERSIST_LOAD_OLD, PERSIST_LOAD_NEW, 1)
    content = content.replace(PERSIST_SAVE_OLD, PERSIST_SAVE_NEW, 1)

    with open(FRIEND_FINDER_CPP, "w") as f:
        f.write(content)
    print(f"Patched {FRIEND_FINDER_CPP}: friends persistence -> LittleFS")


# --- Menu ordering (issue #27) -------------------------------------------
#
# Friend Finder is the headline feature of this fork but sits ~6th in
# the Home Action menu, behind backlight/position/preset/freetext.
# Track a Friend is the daily-driver action but sits below Start
# Pairing (a one-time setup step). Both move to index 1 (immediately
# after Back) per the spec at openspec/changes/reorder-friend-finder-menus/.
#
# Two menus, two patch shapes:
#   - homeBaseMenu's callback dispatches on enum values, so the visual
#     reorder is a pure block move; the callback is unchanged.
#   - friendFinderBaseMenu's callback dispatches on literal selected==N
#     indices, so reordering also requires renumbering the case branches
#     in lockstep. The OLD/NEW span covers BOTH the push_back block and
#     the callback body so the move is reviewable as one diff.

# Anchored on homeBaseMenu's enum line — unique signature in the source.
# Insert the Friend Finder option right after "int options = 1;".
MENU_HOME_INSERT_OLD = """    enum optionsNumbers { Back, Backlight, Position, Preset, Freetext, FriendFinder, Bluetooth, Sleep, enumEnd };

    static const char *optionsArray[enumEnd] = {"Back"};
    static int optionsEnumArray[enumEnd] = {Back};
    int options = 1;
"""

MENU_HOME_INSERT_NEW = """    enum optionsNumbers {{ Back, Backlight, Position, Preset, Freetext, FriendFinder, Bluetooth, Sleep, enumEnd }};

    static const char *optionsArray[enumEnd] = {{"Back"}};
    static int optionsEnumArray[enumEnd] = {{Back}};
    int options = 1;

    {marker} — Friend Finder hoisted to first actionable position (issue #27)
    optionsArray[options] = "Friend Finder";
    optionsEnumArray[options++] = FriendFinder;
""".format(marker=MENU_ORDERING_MARKER)

# Remove the original Friend Finder block (sits between Freetext block and
# Bluetooth Toggle in upstream). Anchor on the Friend Finder + Bluetooth
# pair, which is unique in the file.
MENU_HOME_REMOVE_OLD = """    optionsArray[options] = "Friend Finder";
    optionsEnumArray[options++] = FriendFinder;

    optionsArray[options] = "Bluetooth Toggle";
"""

MENU_HOME_REMOVE_NEW = """    optionsArray[options] = "Bluetooth Toggle";
"""

# friendFinderBaseMenu push_back reorder: swap Start Pairing and Track a
# Friend. Anchored on the two specific lines so we don't span the
# trailing-whitespace lines below them.
MENU_FRIEND_PUSHBACK_OLD = """    options.push_back("Start Pairing");
    options.push_back("Track a Friend");
"""

MENU_FRIEND_PUSHBACK_NEW = """    options.push_back("Track a Friend");
    options.push_back("Start Pairing");
"""

# friendFinderBaseMenu callback reorder: swap the bodies of selected==1
# (was Start Pairing → now Track a Friend) and selected==2 (was Track a
# Friend → now Start Pairing). Anchor span covers exactly those two
# branches.
MENU_FRIEND_CALLBACK_OLD = """        } else if (selected == 1) { // Start Pairing
            if (friendFinderModule) friendFinderModule->beginPairing();
        } else if (selected == 2) { // Track a Friend
            if (friendFinderModule) {
                if (!friendFinderModule->spoofModeEnabled && friendFinderModule->getUsedFriendsCount() == 0) {
                    screen->showSimpleBanner("No friends saved", 1200);
                } else {
                    menuQueue = friend_finder_list_menu;
                    screen->runNow();
                }
            }
        } else if (selected == 3) { // Saved Places"""

MENU_FRIEND_CALLBACK_NEW = """        }} else if (selected == 1) {{ // Track a Friend
            if (friendFinderModule) {{
                if (!friendFinderModule->spoofModeEnabled && friendFinderModule->getUsedFriendsCount() == 0) {{
                    screen->showSimpleBanner("No friends saved", 1200);
                }} else {{
                    menuQueue = friend_finder_list_menu;
                    screen->runNow();
                }}
            }}
        }} else if (selected == 2) {{ // Start Pairing
            if (friendFinderModule) friendFinderModule->beginPairing();
        }} else if (selected == 3) {{ // Saved Places""".format()


def patch_menu_ordering():
    try:
        with open(MENU_HANDLER_CPP) as f:
            content = f.read()
    except FileNotFoundError:
        sys.exit(f"ERROR: {MENU_HANDLER_CPP} not found. Run from firmware source root.")

    if MENU_ORDERING_MARKER in content:
        print(f"Skipped {MENU_HANDLER_CPP}: menu ordering already patched")
        return

    if MENU_HOME_INSERT_OLD not in content:
        sys.exit(f"ERROR: expected homeBaseMenu enum/options preamble in {MENU_HANDLER_CPP} not found")
    if MENU_HOME_REMOVE_OLD not in content:
        sys.exit(f"ERROR: expected original Friend Finder block in {MENU_HANDLER_CPP} not found")
    if MENU_FRIEND_PUSHBACK_OLD not in content:
        sys.exit(f"ERROR: expected friendFinderBaseMenu push_back pair in {MENU_HANDLER_CPP} not found")
    if MENU_FRIEND_CALLBACK_OLD not in content:
        sys.exit(f"ERROR: expected friendFinderBaseMenu callback span in {MENU_HANDLER_CPP} not found")

    content = content.replace(MENU_HOME_INSERT_OLD, MENU_HOME_INSERT_NEW, 1)
    content = content.replace(MENU_HOME_REMOVE_OLD, MENU_HOME_REMOVE_NEW, 1)
    content = content.replace(MENU_FRIEND_PUSHBACK_OLD, MENU_FRIEND_PUSHBACK_NEW, 1)
    content = content.replace(MENU_FRIEND_CALLBACK_OLD, MENU_FRIEND_CALLBACK_NEW, 1)

    with open(MENU_HANDLER_CPP, "w") as f:
        f.write(content)
    print(f"Patched {MENU_HANDLER_CPP}: Friend Finder + Track a Friend hoisted to top")


# --- Magnetometer power profile (issue #32) ------------------------------
#
# Upstream MagnetometerModule.cpp configures the QMC5883L at the highest-
# current operating point (CTRL1 = 0x1D: OSR=512, ODR=200Hz, MODE=continuous)
# and lets the Adafruit LIS3DH driver fall through to its 400Hz / normal-power
# default (Adafruit_LIS3DH.cpp:138 — setDataRate(LIS3DH_DATARATE_400_HZ) is
# the last line of begin()). Both rates are wildly above what the heading
# pipeline consumes — Madgwick runs at 20Hz and the FriendFinder compass
# redraws at display-frame rates well below that. On a 2000 mAh Maker Nova
# pack the resulting continuous draw eats brown-out margin and correlates
# with field crashes (issue #32).
#
# Drop both knobs to the lowest setting that still feeds a 10Hz fusion path
# cleanly:
#   - QMC5883L CTRL1: 0x1D -> 0xD1 (OSR=64, ODR=10Hz, RNG=2G, MODE=continuous)
#   - LIS3DH: setDataRate(LIS3DH_DATARATE_25_HZ) +
#             setPerformanceMode(LIS3DH_MODE_LOW_POWER) after begin()
#   - Madgwick: filter.begin(20) -> filter.begin(10)
#   - runOnce poll: return 50 -> return 100
#
# See openspec/changes/reduce-magnetometer-power-draw/{proposal,design}.md.

MAG_CTRL1_OLD = """    // CTRL1: OSR=512 (00), RNG=2G (01), ODR=200Hz (11), MODE=continuous (01) -> 0x1D
    if (!qmcWriteReg(bus, addr, QMC_REG_CTRL1, 0x1D)) {
        LOG_INFO("[Magnetometer] QMC write CTRL1 failed");
        return false;
    }"""

MAG_CTRL1_NEW = """    """ + MAG_POWER_MARKER + """ (issue #32)
    // CTRL1: OSR=64 (11), RNG=2G (01), ODR=10Hz (00), MODE=continuous (01) -> 0xD1
    if (!qmcWriteReg(bus, addr, QMC_REG_CTRL1, 0xD1)) {
        LOG_INFO("[Magnetometer] QMC write CTRL1 failed");
        return false;
    }"""

MAG_CTRL1_LOG_OLD = 'LOG_INFO("[Magnetometer] QMC configured (CONT mode, 200Hz, 2G, OSR512).");'
MAG_CTRL1_LOG_NEW = 'LOG_INFO("[Magnetometer] QMC configured (CONT mode, 10Hz, 2G, OSR64).");'

MAG_LIS3DH_OLD = """    if (haveAccel) {
        LOG_INFO("[Magnetometer] LIS3DH detected on Wire1. Start Madgwick @20 Hz.");
        filter.begin(20);
    } else {"""

MAG_LIS3DH_NEW = """    if (haveAccel) {
        """ + MAG_POWER_MARKER + """ (issue #32)
        lis.setDataRate(LIS3DH_DATARATE_25_HZ);
        lis.setPerformanceMode(LIS3DH_MODE_LOW_POWER);
        LOG_INFO("[Magnetometer] LIS3DH detected on Wire1. Configured 25Hz/low-power. Start Madgwick @10 Hz.");
        filter.begin(10);
    } else {"""

# Anchor on the runOnce tail. `return 50;` appears exactly once in this file
# (verified by grep), but we span more context for review legibility.
MAG_POLL_OLD = """        lastLogMs = now;
    }

    return 50;
}"""

MAG_POLL_NEW = """        lastLogMs = now;
    }

    """ + MAG_POWER_MARKER + """ (issue #32) — match QMC ODR=10Hz
    return 100;
}"""


def patch_magnetometer_power_profile():
    try:
        with open(MAG_CPP) as f:
            content = f.read()
    except FileNotFoundError:
        sys.exit(f"ERROR: {MAG_CPP} not found. Run from firmware source root.")

    if MAG_POWER_MARKER in content:
        print(f"Skipped {MAG_CPP}: magnetometer power profile already patched")
        return

    if MAG_CTRL1_OLD not in content:
        sys.exit(f"ERROR: expected QMC CTRL1=0x1D block in {MAG_CPP} not found")
    if MAG_CTRL1_LOG_OLD not in content:
        sys.exit(f"ERROR: expected QMC configured LOG_INFO in {MAG_CPP} not found")
    if MAG_LIS3DH_OLD not in content:
        sys.exit(f"ERROR: expected LIS3DH/Madgwick init block in {MAG_CPP} not found")
    if MAG_POLL_OLD not in content:
        sys.exit(f"ERROR: expected runOnce 'return 50;' tail in {MAG_CPP} not found")

    content = content.replace(MAG_CTRL1_OLD, MAG_CTRL1_NEW, 1)
    content = content.replace(MAG_CTRL1_LOG_OLD, MAG_CTRL1_LOG_NEW, 1)
    content = content.replace(MAG_LIS3DH_OLD, MAG_LIS3DH_NEW, 1)
    content = content.replace(MAG_POLL_OLD, MAG_POLL_NEW, 1)

    with open(MAG_CPP, "w") as f:
        f.write(content)
    print(f"Patched {MAG_CPP}: QMC5883L 10Hz/OSR64 + LIS3DH 25Hz/low-power + 100ms poll")


if __name__ == "__main__":
    patch_variant_ini()
    patch_friend_finder_include()
    patch_friend_finder_persistence()
    patch_menu_ordering()
    patch_magnetometer_power_profile()
