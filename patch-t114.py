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
MARKER = "# ff-builder patches"
PERSIST_MARKER = "// ff-builder: persist friends to LittleFS"

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


if __name__ == "__main__":
    patch_variant_ini()
    patch_friend_finder_include()
    patch_friend_finder_persistence()
