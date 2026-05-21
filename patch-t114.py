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
REDIRECTABLE_PRINT_CPP = "src/RedirectablePrint.cpp"
MAIN_CPP = "src/main.cpp"
CRASHLOG_H = "src/CrashLog.h"
CRASHLOG_CPP = "src/CrashLog.cpp"
MARKER = "# ff-builder patches"
PERSIST_MARKER = "// ff-builder: persist friends to LittleFS"
MENU_ORDERING_MARKER = "// ff-builder: menu ordering"
CRASHLOG_MARKER = "// ff-builder: crashlog persistence"

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


# --- Crash-log persistence (issue ff-gh8) --------------------------------
#
# The T114 has a crash that only reproduces when the device is not tethered
# to a computer — by definition the serial console is unavailable at crash
# time. To recover the lead-up to the crash, we add a 2 KiB in-RAM ring
# buffer in a new src/CrashLog.{h,cpp} module that the logger appends every
# log line into, plus a 2 s periodic that snapshots the ring to
# /prefs/crashlog.bin on LittleFS / InternalFS via SafeFile. On the next
# boot, fsInit() is followed by a dump of that file to serial (prefixed
# [CRASHLOG-BEGIN] / [CRASHLOG-END]) and the file is removed.
#
# Three edits to upstream:
#   1. Create src/CrashLog.h + src/CrashLog.cpp (owns ring + flush + dump).
#   2. Patch src/RedirectablePrint.cpp to call CrashLog::append() right
#      after log_to_ble() inside the existing inDebugPrint critical section
#      (line ~344). The append routine is pure memcpy — no FS, no LOG_* —
#      because the inDebugPrint mutex is non-recursive.
#   3. Patch src/main.cpp to call CrashLog::dumpIfPresent() right after
#      fsInit() and register a concurrency::Periodic for the flusher,
#      mirroring the ledPeriodic registration two lines above fsInit().
#
# Notes:
#   - SafeFile is used with fullAtomic=false; a torn write of a debug
#     breadcrumb is preferable to doubling the on-disk footprint on the
#     ~28 KiB InternalFS partition. The file is rewritten in full each
#     flush (LittleFS handles wear-leveling).
#   - flush() reads ring/head/wrapped without holding inDebugPrint (the
#     mutex is non-recursive and the periodic runs on a different task);
#     the worst case is a torn line on the last few bytes, which is fine
#     for a debug aid.
#   - No new protobuf config knob — feature is always on. We can yank the
#     whole patch once the crash is found.

CRASHLOG_H_BODY = """#pragma once
// ff-builder: crashlog persistence (issue ff-gh8)
//
// In-RAM ring buffer of recent LOG_* lines, periodically flushed to
// /prefs/crashlog.bin so a crash that occurs while the device is not
// connected to a computer can still be diagnosed on the next boot.

#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>

namespace CrashLog {

// Sized to fit comfortably alongside friends.proto on the nRF52 InternalFS
// (~28 KiB usable). At ~80 chars/line this holds ~25 recent log lines.
constexpr size_t RING_SIZE = 2048;

// Called from RedirectablePrint::log() while the inDebugPrint mutex is
// held. Must NOT call any LOG_* macro and must NOT touch the filesystem;
// the mutex is non-recursive and a recursive log call would deadlock.
void append(const char *logLevel, const char *format, va_list arg);

// Snapshot the ring and write it to flash. Intended to be driven by a
// concurrency::Periodic at ~2 s cadence; safe to call from any task.
void flush();

// Read /prefs/crashlog.bin if present, emit it to the serial console
// (between [CRASHLOG-BEGIN] / [CRASHLOG-END] markers), then unlink it.
// Call once at boot, after fsInit().
void dumpIfPresent();

// Periodic-task callback wrapping flush(). Returns the next interval in ms.
int32_t periodicFlush();

} // namespace CrashLog
"""

CRASHLOG_CPP_BODY = """// ff-builder: crashlog persistence (issue ff-gh8) — implementation
//
// See CrashLog.h for the contract. The ring buffer is appended to by
// RedirectablePrint::log() inside the inDebugPrint critical section, so
// append() does not need its own mutex. flush() runs on a different task
// (the periodic) and reads the ring racily — a torn line on the tail is
// acceptable for a debug aid; the alternative (acquiring inDebugPrint here)
// would risk deadlock if SafeFile ever logged during the write.

#include "CrashLog.h"

#include "FSCommon.h"
#include "RedirectablePrint.h"
#include "SPILock.h"
#include "SafeFile.h"
#include "SerialConsole.h"
#include "concurrency/LockGuard.h"
#include "configuration.h"

#include <Arduino.h>
#include <cstdio>
#include <cstring>

extern SerialConsole *console;

namespace CrashLog {
namespace {

constexpr uint32_t FILE_MAGIC = 0x434c4f47u; // 'CLOG'
constexpr uint16_t FILE_VERSION = 1;
constexpr const char *FILE_PATH = "/prefs/crashlog.bin";

struct FileHeader {
    uint32_t magic;
    uint16_t version;
    uint16_t len;          // bytes of valid payload after this header
    uint32_t boot_millis;  // millis() at the time of the write
};
static_assert(sizeof(FileHeader) == 12, "crashlog header size drift");

char ring[RING_SIZE];
size_t head = 0;
bool wrapped = false;
volatile bool dirty = false;

inline void putc_ring(char c)
{
    ring[head++] = c;
    if (head >= RING_SIZE) {
        head = 0;
        wrapped = true;
    }
}

inline void put_ring(const char *s, size_t n)
{
    for (size_t i = 0; i < n; ++i)
        putc_ring(s[i]);
}

} // namespace

void append(const char *logLevel, const char *format, va_list arg)
{
    // One-character level prefix (D/I/W/E/C/T) + space, then fixed-width
    // millis() in hex so columns line up across lines.
    char prefix[2];
    prefix[0] = (logLevel && logLevel[0]) ? logLevel[0] : '?';
    prefix[1] = ' ';

    char tbuf[12];
    int tlen = snprintf(tbuf, sizeof(tbuf), "%08lx ", (unsigned long)millis());
    if (tlen < 0 || (size_t)tlen >= sizeof(tbuf))
        tlen = 0;

    // Stack buffer for the formatted line. Sized to match upstream
    // RedirectablePrint::vprintf's printBuf (160) plus headroom; lines
    // longer than this are truncated with a trailing newline.
    char line[224];
    va_list copy;
    va_copy(copy, arg);
    int n = vsnprintf(line, sizeof(line), format, copy);
    va_end(copy);
    if (n < 0)
        return;
    if ((size_t)n >= sizeof(line)) {
        n = (int)sizeof(line) - 1;
        line[sizeof(line) - 2] = '\\n';
    }

    put_ring(prefix, 2);
    if (tlen > 0)
        put_ring(tbuf, (size_t)tlen);
    put_ring(line, (size_t)n);
    dirty = true;
}

void flush()
{
#ifdef FSCom
    if (!dirty)
        return;

    // Snapshot the ring into a local buffer. We deliberately do not hold
    // RedirectablePrint::inDebugPrint here: the mutex is non-recursive and
    // SafeFile may call into code paths that LOG_* on error, which would
    // re-enter the logger and deadlock. Torn-tail risk is acceptable.
    char snap[RING_SIZE];
    size_t snap_len;
    if (wrapped) {
        size_t a = RING_SIZE - head;
        memcpy(snap, ring + head, a);
        memcpy(snap + a, ring, head);
        snap_len = RING_SIZE;
    } else {
        snap_len = head;
        memcpy(snap, ring, snap_len);
    }
    dirty = false;

    if (snap_len == 0)
        return;

    {
        concurrency::LockGuard g(spiLock);
        FSCom.mkdir("/prefs");
    }

    FileHeader hdr{};
    hdr.magic = FILE_MAGIC;
    hdr.version = FILE_VERSION;
    hdr.len = (uint16_t)snap_len;
    hdr.boot_millis = (uint32_t)millis();

    SafeFile sf(FILE_PATH, /*fullAtomic=*/false);
    sf.write(reinterpret_cast<const uint8_t *>(&hdr), sizeof(hdr));
    sf.write(reinterpret_cast<const uint8_t *>(snap), snap_len);
    if (!sf.close()) {
        // Don't LOG_ here — would re-enter the logger. Mark dirty so the
        // next periodic retries.
        dirty = true;
    }
#endif
}

void dumpIfPresent()
{
#ifdef FSCom
    auto file = FSCom.open(FILE_PATH, FILE_O_READ);
    if (!file)
        return;
    FileHeader hdr{};
    if (file.read(reinterpret_cast<uint8_t *>(&hdr), sizeof(hdr)) != sizeof(hdr) ||
        hdr.magic != FILE_MAGIC || hdr.version != FILE_VERSION) {
        file.close();
        FSCom.remove(FILE_PATH);
        return;
    }

    if (console) {
        console->println();
        console->println("[CRASHLOG-BEGIN] recovered from prior boot");
    }
    char buf[64];
    size_t remaining = hdr.len;
    while (remaining > 0) {
        size_t want = remaining < sizeof(buf) ? remaining : sizeof(buf);
        int got = file.read(reinterpret_cast<uint8_t *>(buf), want);
        if (got <= 0)
            break;
        if (console) {
            // SerialConsole overrides only write(uint8_t) and hides the
            // base bulk-write overload; cast to Print* to reach it.
            static_cast<Print *>(console)->write(reinterpret_cast<const uint8_t *>(buf), (size_t)got);
        }
        remaining -= (size_t)got;
    }
    file.close();
    if (console) {
        console->println();
        console->println("[CRASHLOG-END]");
    }
    FSCom.remove(FILE_PATH);
#endif
}

int32_t periodicFlush()
{
    flush();
    return 2000; // ms
}

} // namespace CrashLog
"""

# Anchor + hook in RedirectablePrint::log(). The three existing sinks fan
# out at line 342-344 of upstream RedirectablePrint.cpp; we insert a fourth
# call right after log_to_ble. The anchor span includes the va_end below to
# uniquely identify the location and to make the diff easy to review.
CRASHLOG_HOOK_OLD = """        log_to_serial(logLevel, newFormat, arg);
        log_to_syslog(logLevel, newFormat, arg);
        log_to_ble(logLevel, newFormat, arg);

        va_end(arg);"""

CRASHLOG_HOOK_NEW = """        log_to_serial(logLevel, newFormat, arg);
        log_to_syslog(logLevel, newFormat, arg);
        log_to_ble(logLevel, newFormat, arg);
        CrashLog::append(logLevel, newFormat, arg); {marker}

        va_end(arg);""".format(marker=CRASHLOG_MARKER)

CRASHLOG_INCLUDE_OLD = """#include "RedirectablePrint.h"
#include "NodeDB.h"
#include "RTC.h"
#include "concurrency/OSThread.h"
#include "configuration.h\""""

CRASHLOG_INCLUDE_NEW = """#include "RedirectablePrint.h"
#include "CrashLog.h" {marker}
#include "NodeDB.h"
#include "RTC.h"
#include "concurrency/OSThread.h"
#include "configuration.h\"""".format(marker=CRASHLOG_MARKER)

# main.cpp wiring. The fsInit() call lives at upstream line 524; we hook
# dumpIfPresent() right after it and register the Periodic right after
# OSThread::setup() finishes (mirroring how ledPeriodic is created).
CRASHLOG_MAIN_INCLUDE_OLD = """#include \"concurrency/OSThread.h\"
#include \"concurrency/Periodic.h\""""

CRASHLOG_MAIN_INCLUDE_NEW = """#include \"concurrency/OSThread.h\"
#include \"concurrency/Periodic.h\"
#include \"CrashLog.h\" {marker}""".format(marker=CRASHLOG_MARKER)

CRASHLOG_MAIN_FSINIT_OLD = """    fsInit();

#if !MESHTASTIC_EXCLUDE_I2C"""

CRASHLOG_MAIN_FSINIT_NEW = """    fsInit();
    CrashLog::dumpIfPresent(); {marker}
    static concurrency::Periodic *crashLogPeriodic = new concurrency::Periodic("CrashLogFlush", CrashLog::periodicFlush);
    (void)crashLogPeriodic;

#if !MESHTASTIC_EXCLUDE_I2C""".format(marker=CRASHLOG_MARKER)


def _write_if_missing(path, body, label):
    try:
        with open(path) as f:
            existing = f.read()
        if CRASHLOG_MARKER in existing:
            print(f"Skipped {path}: already created")
            return
        # Refuse to clobber an unexpected pre-existing file.
        sys.exit(f"ERROR: {path} already exists and is not a ff-builder file")
    except FileNotFoundError:
        pass
    with open(path, "w") as f:
        f.write(body)
    print(f"Created {path}: {label}")


def patch_crashlog():
    # 1. Drop in the two new source files.
    _write_if_missing(CRASHLOG_H, CRASHLOG_H_BODY, "CrashLog header")
    _write_if_missing(CRASHLOG_CPP, CRASHLOG_CPP_BODY, "CrashLog implementation")

    # 2. Hook the logger to call CrashLog::append() and pull in the header.
    try:
        with open(REDIRECTABLE_PRINT_CPP) as f:
            rp = f.read()
    except FileNotFoundError:
        sys.exit(f"ERROR: {REDIRECTABLE_PRINT_CPP} not found. Run from firmware source root.")
    if CRASHLOG_MARKER in rp:
        print(f"Skipped {REDIRECTABLE_PRINT_CPP}: crashlog hook already patched")
    else:
        if CRASHLOG_INCLUDE_OLD not in rp:
            sys.exit(f"ERROR: expected include preamble in {REDIRECTABLE_PRINT_CPP} not found")
        if CRASHLOG_HOOK_OLD not in rp:
            sys.exit(f"ERROR: expected log_to_* fan-out block in {REDIRECTABLE_PRINT_CPP} not found")
        rp = rp.replace(CRASHLOG_INCLUDE_OLD, CRASHLOG_INCLUDE_NEW, 1)
        rp = rp.replace(CRASHLOG_HOOK_OLD, CRASHLOG_HOOK_NEW, 1)
        with open(REDIRECTABLE_PRINT_CPP, "w") as f:
            f.write(rp)
        print(f"Patched {REDIRECTABLE_PRINT_CPP}: CrashLog::append() hook")

    # 3. Wire dumpIfPresent() + periodic flush into main.cpp.
    try:
        with open(MAIN_CPP) as f:
            mc = f.read()
    except FileNotFoundError:
        sys.exit(f"ERROR: {MAIN_CPP} not found. Run from firmware source root.")
    if CRASHLOG_MARKER in mc:
        print(f"Skipped {MAIN_CPP}: crashlog wiring already patched")
        return
    if CRASHLOG_MAIN_INCLUDE_OLD not in mc:
        sys.exit(f"ERROR: expected concurrency include pair in {MAIN_CPP} not found")
    if CRASHLOG_MAIN_FSINIT_OLD not in mc:
        sys.exit(f"ERROR: expected fsInit()/I2C anchor in {MAIN_CPP} not found")
    mc = mc.replace(CRASHLOG_MAIN_INCLUDE_OLD, CRASHLOG_MAIN_INCLUDE_NEW, 1)
    mc = mc.replace(CRASHLOG_MAIN_FSINIT_OLD, CRASHLOG_MAIN_FSINIT_NEW, 1)
    with open(MAIN_CPP, "w") as f:
        f.write(mc)
    print(f"Patched {MAIN_CPP}: CrashLog dump + periodic flush wired")


if __name__ == "__main__":
    patch_variant_ini()
    patch_friend_finder_include()
    patch_friend_finder_persistence()
    patch_menu_ordering()
    patch_crashlog()
