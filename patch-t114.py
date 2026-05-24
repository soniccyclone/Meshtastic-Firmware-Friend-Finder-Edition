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
import os
import sys

VARIANT_INI = "variants/nrf52840/heltec_mesh_node_t114/platformio.ini"
FRIEND_FINDER_CPP = "src/modules/FriendFinderModule.cpp"
MENU_HANDLER_CPP = "src/graphics/draw/MenuHandler.cpp"
MARKER = "# ff-builder patches"
PERSIST_MARKER = "// ff-builder: persist friends to LittleFS"
MENU_ORDERING_MARKER = "// ff-builder: menu ordering"

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



# --- Compass Redesign (gh38) -------------------------------------------------
#
# Replace the custom packet-based pairing + tracking protocol with direct
# NodeDB position reads. Changes:
#
#  FriendFinderModule.h:
#   - Remove PAIRING_DISCOVERY, AWAITING_RESPONSE, AWAITING_CONFIRMATION,
#     AWAITING_FINAL_ACCEPTANCE, BEING_TRACKED from the FSM enum
#   - Expose startTracking() as public; remove beginPairing(),
#     requestMutualTracking(), acceptPairingRequest(), rejectPairingRequest()
#     from public API
#   - Remove completePairing(), showConfirmationPrompt() from private;
#     remove startTracking() from private Helpers (now public)
#
#  FriendFinderModule.cpp:
#   - Drop beginPairing/showConfirmationPrompt/acceptPairingRequest/
#     rejectPairingRequest/completePairing/requestMutualTracking bodies
#   - Rewrite startTracking(): enter TRACKING_TARGET directly, no packets
#   - Rewrite endSession(): no peer notification (NodeDB needs none)
#   - Gut runOnce(): remove pairing rebroadcast, IDLE background packets,
#     pairing timeout, BEING_TRACKED packet sending
#   - Gut handleInputEvent(): remove pairing state block, BEING_TRACKED
#   - Rewrite shouldDraw(): remove pairing states and BEING_TRACKED
#   - Rewrite drawFrame(): remove pairing draw blocks; TRACKING_TARGET reads
#     position from nodeDB->getMeshNode(targetNodeNum)
#   - Gut handleReceivedProtobuf(): collapse to NONE handler only
#
#  MenuHandler.cpp:
#   - Add Track action to favoriteBaseMenu (uses currentFavoriteNodeNum)
#   - Remove Start Pairing from friendFinderBaseMenu; renumber callback
#   - friendFinderListActionMenu: requestMutualTracking -> startTracking
#   - friendFinderSessionMenu: endSession(true) -> endSession(false)
#
# See gh issue #38.

FRIEND_FINDER_H = "src/modules/FriendFinderModule.h"
COMPASS_REDESIGN_MARKER = "// ff-builder: captains-compass-redesign (gh38)"

# ---- Header patch strings ----

HEADER_FSM_OLD = \
"""enum class FriendFinderState : uint8_t {
    IDLE = 0,
    PAIRING_DISCOVERY,
    AWAITING_RESPONSE,
    AWAITING_CONFIRMATION,
    AWAITING_FINAL_ACCEPTANCE,
    TRACKING_TARGET,
    BEING_TRACKED,
    FRIEND_MAP,
    COMPASS_SCREEN,
    TRACKING_SPOOFED_TARGET,
    TRACKING_PLACE
};"""

HEADER_FSM_NEW = \
    COMPASS_REDESIGN_MARKER + "\n" + \
"""enum class FriendFinderState : uint8_t {
    IDLE = 0,
    TRACKING_TARGET,
    FRIEND_MAP,
    COMPASS_SCREEN,
    TRACKING_SPOOFED_TARGET,
    TRACKING_PLACE
};"""

HEADER_PUBLIC_OLD = \
"""    void launchMenu();
    void beginPairing();
    void requestMutualTracking(uint32_t nodeNum);
    void endSession(bool notifyPeer);
    void startSpoofedTracking(int direction);

    // Public for banner callback access
    void acceptPairingRequest();
    void rejectPairingRequest();"""

HEADER_PUBLIC_NEW = \
"""    void launchMenu();
    void startTracking(uint32_t nodeNum);
    void endSession(bool notifyPeer);
    void startSpoofedTracking(int direction);"""

HEADER_PRIV_OLD = \
"""    void completePairing(uint32_t nodeNum);
    void showConfirmationPrompt(uint32_t fromNode);

    // Distance trend tracking
    float previousDistance = -1.0f;

    // Input handling
    CallbackObserver<FriendFinderModule, const InputEvent *> inputObserver {
        this, &FriendFinderModule::handleInputEvent };

    int  handleInputEvent(const InputEvent *ev);

    // Helpers
    void sendFriendFinderPacket(uint32_t dst,
                                meshtastic_FriendFinder_RequestType type,
                                uint8_t hopLimit = 0);
    void startTracking(uint32_t nodeNum);
    void raiseUIEvent(UIFrameEvent::Action a, bool focus = false);"""

HEADER_PRIV_NEW = \
"""    // Distance trend tracking
    float previousDistance = -1.0f;

    // Input handling
    CallbackObserver<FriendFinderModule, const InputEvent *> inputObserver {
        this, &FriendFinderModule::handleInputEvent };

    int  handleInputEvent(const InputEvent *ev);

    // Helpers
    void sendFriendFinderPacket(uint32_t dst,
                                meshtastic_FriendFinder_RequestType type,
                                uint8_t hopLimit = 0);
    void raiseUIEvent(UIFrameEvent::Action a, bool focus = false);"""

# ---- CPP patch strings ----

# C1-C7: replace everything from beginPairing() through end of old startTracking()
CPP_PAIRING_FUNS_OLD = \
"""void FriendFinderModule::beginPairing()
{
    pairingWindowOpen = true;
    pairingWindowExpiresAt = millis() + PAIRING_WINDOW_MS;
    currentState = FriendFinderState::PAIRING_DISCOVERY;
    pairingCandidateNodeNum = 0;
    rejectedPeers.clear();

    raiseUIEvent(UIFrameEvent::Action::REGENERATE_FRAMESET, true);

    sendFriendFinderPacket(NODENUM_BROADCAST, meshtastic_FriendFinder_RequestType_REQUEST, 1);
    lastSentPacketTime = millis();
}

void FriendFinderModule::showConfirmationPrompt(uint32_t fromNode)
{
    if (currentState != FriendFinderState::PAIRING_DISCOVERY) return;

    for (uint32_t rejectedNode : rejectedPeers) {
        if (fromNode == rejectedNode) {
            LOG_DEBUG("[FriendFinder] Ignoring request from already rejected peer 0x%08x", (unsigned)fromNode);
            return;
        }
    }

    LOG_INFO("[FriendFinder] Proposing pair with candidate 0x%08x", (unsigned)fromNode);
    pairingCandidateNodeNum = fromNode;
    currentState = FriendFinderState::AWAITING_CONFIRMATION;

    char msg[64];
    snprintf(msg, sizeof(msg), "Pair with %s?", getShortName(fromNode));

    static const char *options[] = {"No", "Yes"};
    graphics::BannerOverlayOptions bannerOptions;
    bannerOptions.message = msg;
    bannerOptions.optionsArrayPtr = options;
    bannerOptions.optionsCount = 2;
    bannerOptions.durationMs = 15000;
    bannerOptions.bannerCallback = [](int selected) {
        if (!friendFinderModule) return;
        if (selected == 1) { // Yes
            friendFinderModule->acceptPairingRequest();
        } else { // No or timeout
            friendFinderModule->rejectPairingRequest();
        }
    };
    screen->showOverlayBanner(bannerOptions);
}

void FriendFinderModule::acceptPairingRequest()
{
    if (pairingCandidateNodeNum == 0) return;

    LOG_INFO("[FriendFinder] User accepted initial pairing with 0x%08x. Sending ACCEPT and waiting for their ACCEPT.", (unsigned)pairingCandidateNodeNum);

    sendFriendFinderPacket(pairingCandidateNodeNum, meshtastic_FriendFinder_RequestType_ACCEPT);
    currentState = FriendFinderState::AWAITING_FINAL_ACCEPTANCE;
    raiseUIEvent(UIFrameEvent::Action::REDRAW_ONLY);
}

void FriendFinderModule::rejectPairingRequest()
{
    LOG_INFO("[FriendFinder] User rejected pairing or request timed out.");

    if (pairingCandidateNodeNum != 0) {
        sendFriendFinderPacket(pairingCandidateNodeNum, meshtastic_FriendFinder_RequestType_REJECT);
        rejectedPeers.push_back(pairingCandidateNodeNum);
    }

    pairingCandidateNodeNum = 0;
    if (pairingWindowOpen && (int32_t)(millis() - pairingWindowExpiresAt) < 0) {
        currentState = FriendFinderState::PAIRING_DISCOVERY;
        raiseUIEvent(UIFrameEvent::Action::REDRAW_ONLY);
    } else {
        currentState = FriendFinderState::IDLE;
        pairingWindowOpen = false;
        raiseUIEvent(UIFrameEvent::Action::REGENERATE_FRAMESET_BACKGROUND, false);
        screen->showSimpleBanner("Pairing cancelled", 1200);
    }
}

void FriendFinderModule::completePairing(uint32_t nodeNum)
{
    if (findFriend(nodeNum) < 0) {
        uint32_t sess = (uint32_t)random(1, 0x7fffffff);
        uint8_t  sec[16]; for (int i = 0; i < 16; ++i) sec[i] = random(0, 255);
        upsertFriend(nodeNum, sess, sec);
    }

    sendFriendFinderPacket(nodeNum, meshtastic_FriendFinder_RequestType_NONE);

    pairingWindowOpen = false;
    pairingCandidateNodeNum = 0;
    currentState = FriendFinderState::IDLE;

    char msg[64];
    snprintf(msg, sizeof(msg), "%s Paired!", getShortName(nodeNum));
    screen->showSimpleBanner(msg, 2500);

    raiseUIEvent(UIFrameEvent::Action::REGENERATE_FRAMESET_BACKGROUND, false);

    graphics::menuHandler::menuQueue = graphics::menuHandler::friend_finder_base_menu;
    screen->runNow();
}


void FriendFinderModule::requestMutualTracking(uint32_t nodeNum)
{
    if (nodeNum == 0 || nodeNum == nodeDB->getNodeNum()) return;

    targetNodeNum = nodeNum;
    currentState  = FriendFinderState::AWAITING_RESPONSE;
    pairingWindowOpen = true;
    pairingWindowExpiresAt = millis() + PAIRING_WINDOW_MS;

#if HAS_SCREEN
    screen->showSimpleBanner("Requesting session...", 1500);
#endif
    sendFriendFinderPacket(nodeNum, meshtastic_FriendFinder_RequestType_REQUEST, 0);
    lastSentPacketTime = millis();
}

void FriendFinderModule::startTracking(uint32_t nodeNum)
{
    if (nodeNum == 0 || nodeNum == nodeDB->getNodeNum()) return;

    targetNodeNum = nodeNum;
    const int friend_idx = findFriend(nodeNum);

    if (friend_idx >= 0) {
        LOG_INFO("[FriendFinder] startTracking(): already friends with 0x%08x -> start immediately", nodeNum);
        currentState = FriendFinderState::TRACKING_TARGET;
        previousDistance = -1.0f;

        lastFriendData = friends_[friend_idx].last_data;
        lastFriendPacketTime = friends_[friend_idx].last_heard_time;

        activateHighGpsMode();
        lastSentPacketTime = 0;
#if HAS_SCREEN
        raiseUIEvent(UIFrameEvent::Action::REGENERATE_FRAMESET, true);
#endif
        sendFriendFinderPacket(nodeNum, meshtastic_FriendFinder_RequestType_NONE, 0);
        return;
    }
}"""

CPP_PAIRING_FUNS_NEW = \
"""void FriendFinderModule::startTracking(uint32_t nodeNum)
{
    // ff-builder: captains-compass-redesign (gh38) — direct NodeDB tracking, no pairing
    if (nodeNum == 0 || nodeNum == nodeDB->getNodeNum()) return;

    targetNodeNum = nodeNum;
    currentState = FriendFinderState::TRACKING_TARGET;
    previousDistance = -1.0f;
    activateHighGpsMode();
#if HAS_SCREEN
    raiseUIEvent(UIFrameEvent::Action::REGENERATE_FRAMESET, true);
#endif
}"""

# C8: rewrite endSession() — remove peer notification
CPP_END_SESSION_OLD = \
"""void FriendFinderModule::endSession(bool notifyPeer)
{
    if (notifyPeer && targetNodeNum) {
        sendFriendFinderPacket(targetNodeNum, meshtastic_FriendFinder_RequestType_END_SESSION);
    }
    targetNodeNum = 0;
    pairingWindowOpen = false;
    currentState = FriendFinderState::IDLE;
    previousDistance = -1.0f;
    restoreNormalGpsMode();
    screen->setFrames(graphics::Screen::FOCUS_DEFAULT);
}"""

CPP_END_SESSION_NEW = \
"""void FriendFinderModule::endSession(bool notifyPeer)
{
    // ff-builder: captains-compass-redesign (gh38) — NodeDB tracking needs no peer notification
    (void)notifyPeer;
    targetNodeNum = 0;
    pairingWindowOpen = false;
    currentState = FriendFinderState::IDLE;
    previousDistance = -1.0f;
    restoreNormalGpsMode();
    screen->setFrames(graphics::Screen::FOCUS_DEFAULT);
}"""

# C9: gut runOnce() — remove pairing rebroadcast, background updates,
#     pairing timeout, BEING_TRACKED + TRACKING_TARGET packet sending
CPP_RUNONCE_OLD = \
"""    if (currentState == FriendFinderState::PAIRING_DISCOVERY) {
        if ((now - lastSentPacketTime) > 5000UL) { // Re-broadcast every 5 seconds
            LOG_DEBUG("[FriendFinder] Re-broadcasting pairing request");
            sendFriendFinderPacket(NODENUM_BROADCAST, meshtastic_FriendFinder_RequestType_REQUEST, 1);
        }
    }

    if (currentState == FriendFinderState::FRIEND_MAP) {
#if HAS_SCREEN
        raiseUIEvent(UIFrameEvent::Action::REDRAW_ONLY);
#endif
    }

    if (currentState == FriendFinderState::IDLE && getUsedFriendsCount() > 0 && gpsStatus->getHasLock()) {
        if (!lastBackgroundUpdateTime || (now - lastBackgroundUpdateTime) > (BACKGROUND_UPDATE_INTERVAL * 1000UL)) {
            LOG_INFO("[FriendFinder] Sending background location updates to %d friends.", getUsedFriendsCount());
            for (int i = 0; i < MAX_FRIENDS; ++i) {
                if (friends_[i].used) {
                    sendFriendFinderPacket(friends_[i].node, meshtastic_FriendFinder_RequestType_NONE);
                }
            }
            lastBackgroundUpdateTime = now;
        }
    }

    if (pairingWindowOpen && (int32_t)(now - pairingWindowExpiresAt) >= 0) {
        pairingWindowOpen = false;
        if (currentState == FriendFinderState::AWAITING_RESPONSE ||
            currentState == FriendFinderState::PAIRING_DISCOVERY ||
            currentState == FriendFinderState::AWAITING_CONFIRMATION ||
            currentState == FriendFinderState::AWAITING_FINAL_ACCEPTANCE) {
            currentState = FriendFinderState::IDLE;
            pairingCandidateNodeNum = 0;
            raiseUIEvent(UIFrameEvent::Action::REGENERATE_FRAMESET_BACKGROUND, false);
#if HAS_SCREEN
            screen->showSimpleBanner("Pairing timed out", 1200);
#endif
        }
    }

#if HAS_SCREEN
    if (magnetometerModule) {
        const bool calNow = magnetometerModule->isCalibrating();
        if (calNow && !calWasActive) {
            calWasActive = true;
        }
        if (!calNow && calWasActive) {
            calWasActive = false;
            screen->showSimpleBanner("Calibration done", 1200);
        }

        const bool flatNow = magnetometerModule->isFlatCalibrating();
        if (flatNow && !flatCalWasActive) {
            flatCalWasActive = true;
        }
        if (!flatNow && flatCalWasActive) {
            flatCalWasActive = false;
            screen->showSimpleBanner("Calibration done", 1200);
        }
    }
#endif

    switch (currentState) {
    case FriendFinderState::COMPASS_SCREEN:
    case FriendFinderState::TRACKING_SPOOFED_TARGET:
    case FriendFinderState::TRACKING_PLACE:
#if HAS_SCREEN
        if (shouldDraw()) raiseUIEvent(UIFrameEvent::Action::REDRAW_ONLY);
#endif
        break;
    case FriendFinderState::BEING_TRACKED:
        if ((now - lastSentPacketTime) > UPDATE_INTERVAL * 1000UL && targetNodeNum) {
            sendFriendFinderPacket(targetNodeNum, meshtastic_FriendFinder_RequestType_NONE);
        }
        [[fallthrough]];
    case FriendFinderState::TRACKING_TARGET:
#if HAS_SCREEN
        if (shouldDraw()) raiseUIEvent(UIFrameEvent::Action::REDRAW_ONLY);
#endif
        if ((now - lastSentPacketTime) > UPDATE_INTERVAL * 1000UL && targetNodeNum) {
            sendFriendFinderPacket(targetNodeNum, meshtastic_FriendFinder_RequestType_NONE);
        }
        break;
    default: break;
    }"""

CPP_RUNONCE_NEW = \
"""    if (currentState == FriendFinderState::FRIEND_MAP) {
#if HAS_SCREEN
        raiseUIEvent(UIFrameEvent::Action::REDRAW_ONLY);
#endif
    }

#if HAS_SCREEN
    if (magnetometerModule) {
        const bool calNow = magnetometerModule->isCalibrating();
        if (calNow && !calWasActive) {
            calWasActive = true;
        }
        if (!calNow && calWasActive) {
            calWasActive = false;
            screen->showSimpleBanner("Calibration done", 1200);
        }

        const bool flatNow = magnetometerModule->isFlatCalibrating();
        if (flatNow && !flatCalWasActive) {
            flatCalWasActive = true;
        }
        if (!flatNow && flatCalWasActive) {
            flatCalWasActive = false;
            screen->showSimpleBanner("Calibration done", 1200);
        }
    }
#endif

    // ff-builder: captains-compass-redesign (gh38) — no periodic packets; redraw only
    switch (currentState) {
    case FriendFinderState::COMPASS_SCREEN:
    case FriendFinderState::TRACKING_SPOOFED_TARGET:
    case FriendFinderState::TRACKING_PLACE:
    case FriendFinderState::TRACKING_TARGET:
#if HAS_SCREEN
        if (shouldDraw()) raiseUIEvent(UIFrameEvent::Action::REDRAW_ONLY);
#endif
        break;
    default: break;
    }"""

# C10a: remove pairing state block from handleInputEvent
CPP_INPUT_PAIRING_OLD = \
"""    if (currentState == FriendFinderState::PAIRING_DISCOVERY ||
        currentState == FriendFinderState::AWAITING_CONFIRMATION ||
        currentState == FriendFinderState::AWAITING_FINAL_ACCEPTANCE) {
        if (isBack) {
            LOG_INFO("[FriendFinder] User cancelled pairing via back button.");
            currentState = FriendFinderState::IDLE;
            pairingWindowOpen = false;
            pairingCandidateNodeNum = 0;
            raiseUIEvent(UIFrameEvent::Action::REGENERATE_FRAMESET_BACKGROUND, false);
            screen->showSimpleBanner("Pairing cancelled", 1200);
            return 1;
        }
        return 0;
    }

    if (currentState == FriendFinderState::COMPASS_SCREEN) {"""

CPP_INPUT_PAIRING_NEW = \
"""    if (currentState == FriendFinderState::COMPASS_SCREEN) {"""

# C10b: remove BEING_TRACKED from tracking check, fix endSession call
CPP_INPUT_TRACKING_OLD = \
"""   if (currentState == FriendFinderState::TRACKING_TARGET ||
        currentState == FriendFinderState::BEING_TRACKED ||
        currentState == FriendFinderState::TRACKING_SPOOFED_TARGET ||
        currentState == FriendFinderState::TRACKING_PLACE)
    {
        if (isSelect) {
            if (currentState == FriendFinderState::TRACKING_SPOOFED_TARGET) {
                graphics::menuHandler::menuQueue = graphics::menuHandler::friend_finder_spoof_session_menu;
            } else if (currentState == FriendFinderState::TRACKING_PLACE) {
                // For now, use the same simple menu as the spoof test
                graphics::menuHandler::menuQueue = graphics::menuHandler::friend_finder_spoof_session_menu;
            }
             else {
                previousState = currentState;
                graphics::menuHandler::menuQueue = graphics::menuHandler::friend_finder_session_menu;
            }
            screen->runNow();
            return 1;
        }
        if (isBack) {
            endSession(currentState != FriendFinderState::TRACKING_SPOOFED_TARGET && currentState != FriendFinderState::TRACKING_PLACE);
            return 1;
        }
        return 0;
    }"""

CPP_INPUT_TRACKING_NEW = \
"""   if (currentState == FriendFinderState::TRACKING_TARGET ||
        currentState == FriendFinderState::TRACKING_SPOOFED_TARGET ||
        currentState == FriendFinderState::TRACKING_PLACE)
    {
        if (isSelect) {
            if (currentState == FriendFinderState::TRACKING_SPOOFED_TARGET) {
                graphics::menuHandler::menuQueue = graphics::menuHandler::friend_finder_spoof_session_menu;
            } else if (currentState == FriendFinderState::TRACKING_PLACE) {
                graphics::menuHandler::menuQueue = graphics::menuHandler::friend_finder_spoof_session_menu;
            } else {
                previousState = currentState;
                graphics::menuHandler::menuQueue = graphics::menuHandler::friend_finder_session_menu;
            }
            screen->runNow();
            return 1;
        }
        if (isBack) {
            endSession(false);
            return 1;
        }
        return 0;
    }"""

# C11: rewrite shouldDraw() — remove pairing states and BEING_TRACKED
# PAIRING_DISCOVERY line has a trailing space in source; use explicit concat.
CPP_SHOULD_DRAW_OLD = (
    "bool FriendFinderModule::shouldDraw()\n"
    "{\n"
    "    return currentState == FriendFinderState::TRACKING_TARGET ||\n"
    "           currentState == FriendFinderState::BEING_TRACKED ||\n"
    "           currentState == FriendFinderState::AWAITING_RESPONSE ||\n"
    "           currentState == FriendFinderState::FRIEND_MAP ||\n"
    "           currentState == FriendFinderState::PAIRING_DISCOVERY || \n"  # trailing space
    "           currentState == FriendFinderState::AWAITING_CONFIRMATION ||\n"
    "           currentState == FriendFinderState::AWAITING_FINAL_ACCEPTANCE ||\n"
    "           currentState == FriendFinderState::COMPASS_SCREEN ||\n"
    "           currentState == FriendFinderState::TRACKING_SPOOFED_TARGET ||\n"
    "           currentState == FriendFinderState::TRACKING_PLACE;\n"
    "}"
)

CPP_SHOULD_DRAW_NEW = \
"""bool FriendFinderModule::shouldDraw()
{
    return currentState == FriendFinderState::TRACKING_TARGET ||
           currentState == FriendFinderState::FRIEND_MAP ||
           currentState == FriendFinderState::COMPASS_SCREEN ||
           currentState == FriendFinderState::TRACKING_SPOOFED_TARGET ||
           currentState == FriendFinderState::TRACKING_PLACE;
}"""

# C12: rewrite drawFrame() pairing blocks + TRACKING_TARGET reads NodeDB
CPP_DRAWFRAME_PAIRING_OLD = \
"""    if (currentState == FriendFinderState::PAIRING_DISCOVERY ||
        currentState == FriendFinderState::AWAITING_CONFIRMATION ||
        currentState == FriendFinderState::AWAITING_FINAL_ACCEPTANCE) {
        display->setFont(FONT_SMALL);
        display->setTextAlignment(TEXT_ALIGN_CENTER);
        display->drawString(x + W / 2, y, "Pairing");

        const int32_t remainMs = (int32_t)(pairingWindowOpen ? (pairingWindowExpiresAt - millis()) : 0);
        const int remain = remainMs > 0 ? (remainMs + 999) / 1000 : 0;
        char buf[32];
        snprintf(buf, sizeof(buf), "%ds left", remain);
        display->setTextAlignment(TEXT_ALIGN_RIGHT);
        display->drawString(x + W - 2, y, buf);

        display->setTextAlignment(TEXT_ALIGN_CENTER);
        if (currentState == FriendFinderState::PAIRING_DISCOVERY) {
            display->drawString(x + W / 2, y + H / 2, "Looking for peers...");
        } else if (currentState == FriendFinderState::AWAITING_CONFIRMATION) {
            display->drawString(x + W / 2, y + H / 2 - (FONT_HEIGHT_SMALL/2), "Found peer!");
            display->drawString(x + W / 2, y + H / 2 + (FONT_HEIGHT_SMALL/2), "Awaiting confirmation...");
        } else {
             display->drawString(x + W / 2, y + H / 2, "Waiting for peer to accept...");
        }
        return;
    }

    if (currentState == FriendFinderState::FRIEND_MAP) {
        drawFriendMap(display, x, y, W, H);
        return;
    }

    if (currentState == FriendFinderState::AWAITING_RESPONSE) {
        display->setFont(FONT_SMALL);
        display->drawString(x + 2, y, "Friend Finder");
        const int line0 = y + FONT_HEIGHT_SMALL + 2;
        const int32_t remainMs = (int32_t)(pairingWindowOpen ? (pairingWindowExpiresAt - millis()) : 0);
        const int remain = remainMs > 0 ? (remainMs + 999) / 1000 : 0;
        char buf[48];
        snprintf(buf, sizeof(buf), "Requesting… %ds left", remain);
        display->drawString(x + 2, line0, buf);
        display->drawString(x + 2, line0 + FONT_HEIGHT_SMALL + 2, "Waiting for response...");
        return;
    }

    if (currentState == FriendFinderState::TRACKING_TARGET ||
        currentState == FriendFinderState::BEING_TRACKED)
    {
        const char* peerName = getNodeName(targetNodeNum);
        int32_t myLat = gpsStatus->getLatitude();
        int32_t myLon = gpsStatus->getLongitude();
        bool haveFix  = gpsStatus->getHasLock();

        uint32_t ageSec = 0;
        if (lastFriendPacketTime) {
            uint32_t now = millis();
            ageSec = (now >= lastFriendPacketTime) ? ((now - lastFriendPacketTime) / 1000U) : 0;
        }

        this->drawSessionPage(display, x, y, W, H, peerName, lastFriendData,
                        haveFix, myLat, myLon, ageSec, this->lastFriendPacketTime);
    }
}"""

CPP_DRAWFRAME_PAIRING_NEW = \
"""    if (currentState == FriendFinderState::FRIEND_MAP) {
        drawFriendMap(display, x, y, W, H);
        return;
    }

    if (currentState == FriendFinderState::TRACKING_TARGET) {
        // ff-builder: captains-compass-redesign (gh38) — read position from NodeDB, no packets
        const char* peerName = getNodeName(targetNodeNum);
        int32_t myLat = gpsStatus->getLatitude();
        int32_t myLon = gpsStatus->getLongitude();
        bool haveFix  = gpsStatus->getHasLock();

        meshtastic_FriendFinder peerData = meshtastic_FriendFinder_init_default;
        uint32_t ageSec = 0;
        uint32_t haveData = 0;
        meshtastic_NodeInfoLite *info = nodeDB->getMeshNode(targetNodeNum);
        if (info && info->has_position) {
            peerData.latitude_i  = info->position.latitude_i;
            peerData.longitude_i = info->position.longitude_i;
            if (info->position.time > 0) {
                haveData = 1;
                uint32_t nowSec = getValidTime(RTCQualityFromNet);
                ageSec = (nowSec > info->position.time) ? (nowSec - info->position.time) : 0;
                if (ageSec > 99999) ageSec = 99999;
            }
        }
        this->drawSessionPage(display, x, y, W, H, peerName, peerData,
                        haveFix, myLat, myLon, ageSec, haveData);
    }
}"""

# C13a: gut REQUEST case
CPP_RX_REQUEST_OLD = \
"""    case meshtastic_FriendFinder_RequestType_REQUEST: {
        if (currentState == FriendFinderState::PAIRING_DISCOVERY) {
            showConfirmationPrompt(from);
            return true;
        }

        bool isDirectedRequestToMe = (mp.to == nodeDB->getNodeNum());
        if (isDirectedRequestToMe && (findFriend(from) >= 0 || currentState == FriendFinderState::AWAITING_RESPONSE)) {
            targetNodeNum = from;
            currentState  = FriendFinderState::BEING_TRACKED;
            activateHighGpsMode();
            pairingWindowOpen = false;

            if (findFriend(from) < 0) {
                 uint32_t sess = (uint32_t)random(1, 0x7fffffff);
                 uint8_t sec[16]; for (int i = 0; i < 16; ++i) sec[i] = random(0, 255);
                 upsertFriend(from, sess, sec);
            }
            LOG_INFO("[FriendFinder] Directed request from 0x%08x -> ACCEPT", from);
            sendFriendFinderPacket(from, meshtastic_FriendFinder_RequestType_ACCEPT);
            sendFriendFinderPacket(from, meshtastic_FriendFinder_RequestType_NONE); // Immediately send our location
#if HAS_SCREEN
            raiseUIEvent(UIFrameEvent::Action::REGENERATE_FRAMESET, true);
#endif
            return true;
        }
        return true;
    }"""

CPP_RX_REQUEST_NEW = \
"""    case meshtastic_FriendFinder_RequestType_REQUEST:
        break;"""

# C13b: gut ACCEPT case
CPP_RX_ACCEPT_OLD = \
"""    case meshtastic_FriendFinder_RequestType_ACCEPT: {
        if (currentState == FriendFinderState::AWAITING_RESPONSE && from == targetNodeNum) {
            LOG_INFO("[FriendFinder] Tracking request to 0x%08x was accepted.", (unsigned)from);
            currentState = FriendFinderState::TRACKING_TARGET;
            activateHighGpsMode();
            pairingWindowOpen = false;
            sendFriendFinderPacket(from, meshtastic_FriendFinder_RequestType_NONE); // Immediately send our location
            raiseUIEvent(UIFrameEvent::Action::REGENERATE_FRAMESET, true);
            return true;
        }

        if (currentState == FriendFinderState::AWAITING_FINAL_ACCEPTANCE && from == pairingCandidateNodeNum) {
            LOG_INFO("[FriendFinder] Received final acceptance from 0x%08x. Pairing complete!", (unsigned)from);
            completePairing(from);
            return true;
        }
        else if (currentState == FriendFinderState::PAIRING_DISCOVERY) {
            LOG_INFO("[FriendFinder] Received an ACCEPT from 0x%08x while in discovery, treating as proposal.", (unsigned)from);
            showConfirmationPrompt(from);
            return true;
        }
        break;
    }"""

CPP_RX_ACCEPT_NEW = \
"""    case meshtastic_FriendFinder_RequestType_ACCEPT:
        break;"""

# C13c: gut REJECT case — uses explicit string with trailing whitespace on blank line
#   (line 873 in original file is "            " — 12 spaces — due to editor artifact)
CPP_RX_REJECT_OLD = (
    "    case meshtastic_FriendFinder_RequestType_REJECT: {\n"
    "        if (currentState == FriendFinderState::AWAITING_FINAL_ACCEPTANCE && from == pairingCandidateNodeNum) {\n"
    "            LOG_INFO(\"[FriendFinder] Peer 0x%08x rejected the pairing request.\", (unsigned)from);\n"
    "            \n"
    "            if (pairingWindowOpen && (int32_t)(millis() - pairingWindowExpiresAt) < 0) {\n"
    "                currentState = FriendFinderState::PAIRING_DISCOVERY;\n"
    "                pairingCandidateNodeNum = 0;\n"
    "                screen->showSimpleBanner(\"Peer rejected pair\", 1500);\n"
    "                raiseUIEvent(UIFrameEvent::Action::REDRAW_ONLY);\n"
    "            } else {\n"
    "                currentState = FriendFinderState::IDLE;\n"
    "                pairingWindowOpen = false;\n"
    "                pairingCandidateNodeNum = 0;\n"
    "                screen->showSimpleBanner(\"Pairing failed\", 1200);\n"
    "                raiseUIEvent(UIFrameEvent::Action::REGENERATE_FRAMESET_BACKGROUND, false);\n"
    "            }\n"
    "        }\n"
    "        break;\n"
    "    }"
)

CPP_RX_REJECT_NEW = \
"""    case meshtastic_FriendFinder_RequestType_REJECT:
        break;"""

# C13d: gut END_SESSION + NONE cases and close function
CPP_RX_TAIL_OLD = \
"""    case meshtastic_FriendFinder_RequestType_END_SESSION: {
        if (from == targetNodeNum &&
            (currentState == FriendFinderState::TRACKING_TARGET ||
             currentState == FriendFinderState::BEING_TRACKED)) {
#if HAS_SCREEN
            screen->showSimpleBanner("Session ended by peer", 1200);
#endif
            endSession(false);
        }
        break;
    }

    case meshtastic_FriendFinder_RequestType_NONE: {
        if (currentState == FriendFinderState::AWAITING_FINAL_ACCEPTANCE && from == pairingCandidateNodeNum) {
            LOG_INFO("[FriendFinder] Received final pairing confirmation from 0x%08x.", (unsigned)from);
            completePairing(from);
            return true;
        }

        const int friend_idx = findFriend(from);
        if (friend_idx >= 0) {
            friends_[friend_idx].last_data = *ff;
            friends_[friend_idx].last_heard_time = millis();
            LOG_DEBUG("[FriendFinder] Stored background update from friend 0x%08x", from);
        }

        if (from == targetNodeNum) {
            lastFriendData = *ff;
            lastFriendPacketTime = millis();
            LOG_DEBUG("[FriendFinder] Update from 0x%08x: batt=%u sats=%u",
                      from, (unsigned)ff->battery_level, (unsigned)ff->sats_in_view);
        }
        break;
    }

    default:
        break;
    }
    return true;
}"""

CPP_RX_TAIL_NEW = \
"""    case meshtastic_FriendFinder_RequestType_NONE: {
        // ff-builder: captains-compass-redesign (gh38) — update friend cache only; no packet replies
        const int friend_idx = findFriend(from);
        if (friend_idx >= 0) {
            friends_[friend_idx].last_data = *ff;
            friends_[friend_idx].last_heard_time = millis();
            LOG_DEBUG("[FriendFinder] Stored background update from friend 0x%08x", from);
        }
        break;
    }

    default:
        break;
    }
    return true;
}"""

# ---- MenuHandler.cpp patch strings ----

# M1a: add Track to favoriteBaseMenu enum
MENU_FAV_ENUM_OLD = \
"""    enum optionsNumbers { Back, Preset, Freetext, Remove, TraceRoute, enumEnd };"""

MENU_FAV_ENUM_NEW = \
"""    enum optionsNumbers { Back, Preset, Freetext, Remove, TraceRoute, Track, enumEnd };"""

# M1b: insert Track option before Remove Favorite
MENU_FAV_OPTS_OLD = \
"""    optionsArray[options] = "Remove Favorite";
    optionsEnumArray[options++] = Remove;"""

MENU_FAV_OPTS_NEW = \
"""    optionsArray[options] = "Track (Friend Finder)";
    optionsEnumArray[options++] = Track;
    optionsArray[options] = "Remove Favorite";
    optionsEnumArray[options++] = Remove;"""

# M1c: add Track callback branch
MENU_FAV_CB_OLD = \
"""        } else if (selected == TraceRoute) {
            if (traceRouteModule) {
                traceRouteModule->launch(graphics::UIRenderer::currentFavoriteNodeNum);
            }
        }
    };
    screen->showOverlayBanner(bannerOptions);
}

void menuHandler::positionBaseMenu()"""

MENU_FAV_CB_NEW = \
"""        } else if (selected == TraceRoute) {
            if (traceRouteModule) {
                traceRouteModule->launch(graphics::UIRenderer::currentFavoriteNodeNum);
            }
        } else if (selected == Track) {
            // ff-builder: captains-compass-redesign (gh38)
            if (friendFinderModule) {
                friendFinderModule->startTracking(graphics::UIRenderer::currentFavoriteNodeNum);
            }
        }
    };
    screen->showOverlayBanner(bannerOptions);
}

void menuHandler::positionBaseMenu()"""

# M2: remove Start Pairing from friendFinderBaseMenu, renumber callback
# Note: "Saved Places" and "Compass Cal" lines have trailing spaces in the source.
MENU_FF_BASE_OLD = (
    '    options.push_back("Back");\n'
    '    options.push_back("Track a Friend");\n'
    '    options.push_back("Start Pairing");\n'
    '    options.push_back("Saved Places"); \n'  # trailing space in source
    '    options.push_back("Compass Cal"); \n'   # trailing space in source
    '    options.push_back("Dev Tools");\n'
    '\n'
    '    for(const auto& option : options) {\n'
    '        pointers.push_back(option.c_str());\n'
    '    }\n'
    '\n'
    '    BannerOverlayOptions bannerOptions;\n'
    '    bannerOptions.message = "Friend Finder";\n'
    '    bannerOptions.optionsArrayPtr = pointers.data();\n'
    '    bannerOptions.optionsCount = pointers.size();\n'
    '    bannerOptions.bannerCallback = [](int selected) -> void {\n'
    '        if (selected == 0) { // Back\n'
    '            if (friendFinderModule) friendFinderModule->setState(FriendFinderState::IDLE);\n'
    '        } else if (selected == 1) { // Track a Friend\n'
    '            if (friendFinderModule) {\n'
    '                if (!friendFinderModule->spoofModeEnabled && friendFinderModule->getUsedFriendsCount() == 0) {\n'
    '                    screen->showSimpleBanner("No friends saved", 1200);\n'
    '                } else {\n'
    '                    menuQueue = friend_finder_list_menu;\n'
    '                    screen->runNow();\n'
    '                }\n'
    '            }\n'
    '        } else if (selected == 2) { // Start Pairing\n'
    '            if (friendFinderModule) friendFinderModule->beginPairing();\n'
    '        } else if (selected == 3) { // Saved Places\n'
    '            menuQueue = friend_finder_places_menu;\n'
    '            screen->runNow();\n'
    '        } else if (selected == 4) { // Compass Cal\n'
    '            if (friendFinderModule) friendFinderModule->setState(FriendFinderState::COMPASS_SCREEN);\n'
    '        } else if (selected == 5) { // Dev Tools\n'
    '            menuQueue = friend_finder_dev_tools_menu;\n'
    '            screen->runNow();\n'
    '        }\n'
    '    };'
)
MENU_FF_BASE_NEW = \
"""    options.push_back("Back");
    options.push_back("Track a Friend");
    options.push_back("Saved Places");
    options.push_back("Compass Cal");
    options.push_back("Dev Tools");

    for(const auto& option : options) {
        pointers.push_back(option.c_str());
    }

    BannerOverlayOptions bannerOptions;
    bannerOptions.message = "Friend Finder";
    bannerOptions.optionsArrayPtr = pointers.data();
    bannerOptions.optionsCount = pointers.size();
    bannerOptions.bannerCallback = [](int selected) -> void {
        if (selected == 0) { // Back
            if (friendFinderModule) friendFinderModule->setState(FriendFinderState::IDLE);
        } else if (selected == 1) { // Track a Friend
            if (friendFinderModule) {
                if (!friendFinderModule->spoofModeEnabled && friendFinderModule->getUsedFriendsCount() == 0) {
                    screen->showSimpleBanner("No friends saved", 1200);
                } else {
                    menuQueue = friend_finder_list_menu;
                    screen->runNow();
                }
            }
        } else if (selected == 2) { // Saved Places
            menuQueue = friend_finder_places_menu;
            screen->runNow();
        } else if (selected == 3) { // Compass Cal
            if (friendFinderModule) friendFinderModule->setState(FriendFinderState::COMPASS_SCREEN);
        } else if (selected == 4) { // Dev Tools
            menuQueue = friend_finder_dev_tools_menu;
            screen->runNow();
        }
    };"""

# M3: friendFinderListActionMenu — requestMutualTracking -> startTracking
MENU_LIST_ACTION_OLD = \
"""                friendFinderModule->requestMutualTracking(friendRec->node);"""

MENU_LIST_ACTION_NEW = \
"""                friendFinderModule->startTracking(friendRec->node);"""

# M4: friendFinderSessionMenu — endSession(true) -> endSession(false)
MENU_SESSION_OLD = \
"""            friendFinderModule->endSession(true);"""

MENU_SESSION_NEW = \
"""            friendFinderModule->endSession(false);"""

# M5: friendFinderPlacesMenu — empty slot should be a no-op, not open action menu
MENU_PLACES_OLD = \
"""        } else { // An existing place was selected
            selectedFriendListIndex = selected - 2; // Store the 0-based index
            menuQueue = friend_finder_place_action_menu;
            screen->runNow();
        }"""

MENU_PLACES_NEW = \
"""        } else { // An existing place was selected
            selectedFriendListIndex = selected - 2; // Store the 0-based index
            const auto &place = friendFinderModule->getSavedPlace(selectedFriendListIndex);
            if (place.used) {
                menuQueue = friend_finder_place_action_menu;
                screen->runNow();
            }
            // empty slot: no-op
        }"""


def patch_compass_redesign():
    # ---- Header ----
    try:
        with open(FRIEND_FINDER_H) as f:
            h = f.read()
    except FileNotFoundError:
        sys.exit(f"ERROR: {FRIEND_FINDER_H} not found. Run from firmware source root.")

    if COMPASS_REDESIGN_MARKER in h:
        print(f"Skipped {FRIEND_FINDER_H}: compass redesign already patched")
    else:
        if HEADER_FSM_OLD not in h:
            sys.exit(f"ERROR: expected FSM enum in {FRIEND_FINDER_H} not found")
        if HEADER_PUBLIC_OLD not in h:
            sys.exit(f"ERROR: expected public API block in {FRIEND_FINDER_H} not found")
        if HEADER_PRIV_OLD not in h:
            sys.exit(f"ERROR: expected private methods block in {FRIEND_FINDER_H} not found")
        h = h.replace(HEADER_FSM_OLD, HEADER_FSM_NEW, 1)
        h = h.replace(HEADER_PUBLIC_OLD, HEADER_PUBLIC_NEW, 1)
        h = h.replace(HEADER_PRIV_OLD, HEADER_PRIV_NEW, 1)
        with open(FRIEND_FINDER_H, "w") as f:
            f.write(h)
        print(f"Patched {FRIEND_FINDER_H}: compass redesign (FSM, public API, private methods)")

    # ---- CPP ----
    try:
        with open(FRIEND_FINDER_CPP) as f:
            cpp = f.read()
    except FileNotFoundError:
        sys.exit(f"ERROR: {FRIEND_FINDER_CPP} not found. Run from firmware source root.")

    if COMPASS_REDESIGN_MARKER in cpp:
        print(f"Skipped {FRIEND_FINDER_CPP}: compass redesign already patched")
    else:
        checks = [
            (CPP_PAIRING_FUNS_OLD,      "pairing functions + startTracking body"),
            (CPP_END_SESSION_OLD,       "endSession() body"),
            (CPP_RUNONCE_OLD,           "runOnce() mid-section"),
            (CPP_INPUT_PAIRING_OLD,     "handleInputEvent() pairing block"),
            (CPP_INPUT_TRACKING_OLD,    "handleInputEvent() tracking block"),
            (CPP_SHOULD_DRAW_OLD,       "shouldDraw() body"),
            (CPP_DRAWFRAME_PAIRING_OLD, "drawFrame() pairing+tracking blocks"),
            (CPP_RX_REQUEST_OLD,        "handleReceivedProtobuf() REQUEST case"),
            (CPP_RX_ACCEPT_OLD,         "handleReceivedProtobuf() ACCEPT case"),
            (CPP_RX_REJECT_OLD,         "handleReceivedProtobuf() REJECT case"),
            (CPP_RX_TAIL_OLD,           "handleReceivedProtobuf() END_SESSION+NONE tail"),
        ]
        for old, label in checks:
            if old not in cpp:
                sys.exit(f"ERROR: expected {label} in {FRIEND_FINDER_CPP} not found")
        cpp = cpp.replace(CPP_PAIRING_FUNS_OLD,     CPP_PAIRING_FUNS_NEW,     1)
        cpp = cpp.replace(CPP_END_SESSION_OLD,       CPP_END_SESSION_NEW,      1)
        cpp = cpp.replace(CPP_RUNONCE_OLD,           CPP_RUNONCE_NEW,          1)
        cpp = cpp.replace(CPP_INPUT_PAIRING_OLD,     CPP_INPUT_PAIRING_NEW,    1)
        cpp = cpp.replace(CPP_INPUT_TRACKING_OLD,    CPP_INPUT_TRACKING_NEW,   1)
        cpp = cpp.replace(CPP_SHOULD_DRAW_OLD,       CPP_SHOULD_DRAW_NEW,      1)
        cpp = cpp.replace(CPP_DRAWFRAME_PAIRING_OLD, CPP_DRAWFRAME_PAIRING_NEW, 1)
        cpp = cpp.replace(CPP_RX_REQUEST_OLD,        CPP_RX_REQUEST_NEW,       1)
        cpp = cpp.replace(CPP_RX_ACCEPT_OLD,         CPP_RX_ACCEPT_NEW,        1)
        cpp = cpp.replace(CPP_RX_REJECT_OLD,         CPP_RX_REJECT_NEW,        1)
        cpp = cpp.replace(CPP_RX_TAIL_OLD,           CPP_RX_TAIL_NEW,          1)
        with open(FRIEND_FINDER_CPP, "w") as f:
            f.write(cpp)
        print(f"Patched {FRIEND_FINDER_CPP}: compass redesign ({len(checks)} patches)")

    # ---- MenuHandler ----
    try:
        with open(MENU_HANDLER_CPP) as f:
            menu = f.read()
    except FileNotFoundError:
        sys.exit(f"ERROR: {MENU_HANDLER_CPP} not found. Run from firmware source root.")

    if COMPASS_REDESIGN_MARKER in menu:
        print(f"Skipped {MENU_HANDLER_CPP}: compass redesign already patched")
    else:
        checks = [
            (MENU_FAV_ENUM_OLD,    "favoriteBaseMenu enum"),
            (MENU_FAV_OPTS_OLD,    "favoriteBaseMenu Remove Favorite option"),
            (MENU_FAV_CB_OLD,      "favoriteBaseMenu TraceRoute callback"),
            (MENU_FF_BASE_OLD,     "friendFinderBaseMenu push_back + callback"),
            (MENU_LIST_ACTION_OLD, "friendFinderListActionMenu requestMutualTracking"),
            (MENU_SESSION_OLD,     "friendFinderSessionMenu endSession(true)"),
            (MENU_PLACES_OLD,      "friendFinderPlacesMenu empty slot"),
        ]
        for old, label in checks:
            if old not in menu:
                sys.exit(f"ERROR: expected {label} in {MENU_HANDLER_CPP} not found")
        menu = menu.replace(MENU_FAV_ENUM_OLD,    MENU_FAV_ENUM_NEW,    1)
        menu = menu.replace(MENU_FAV_OPTS_OLD,    MENU_FAV_OPTS_NEW,    1)
        menu = menu.replace(MENU_FAV_CB_OLD,      MENU_FAV_CB_NEW,      1)
        menu = menu.replace(MENU_FF_BASE_OLD,     MENU_FF_BASE_NEW,     1)
        menu = menu.replace(MENU_LIST_ACTION_OLD, MENU_LIST_ACTION_NEW, 1)
        menu = menu.replace(MENU_SESSION_OLD,     MENU_SESSION_NEW,     1)
        menu = menu.replace(MENU_PLACES_OLD,      MENU_PLACES_NEW,      1)
        with open(MENU_HANDLER_CPP, "w") as f:
            f.write(menu)
        print(f"Patched {MENU_HANDLER_CPP}: compass redesign ({len(checks)} patches)")


# --- Wire_nRF52 TWIM timeouts (T114 freeze fix) ---------------------------
#
# framework-arduinoadafruitnrf52/libraries/Wire/Wire_nRF52.cpp uses bare
# spin loops on TWIM EVENTS_* with no timeout. When the QMC5883L on Wire1
# stalls the peripheral (LoRa RF noise is the likely trigger), no event
# ever fires and the CPU spins forever — the device freezes hard.
# TezlaKid's log ends mid-operation with no error: that's the symptom.
#
# A failure counter in MagnetometerModule cannot fix this: qmcReadRaw
# never returns once the peripheral is stuck, so nothing above the Wire
# layer ever runs. The fix has to live inside Wire_nRF52.cpp itself.
#
# This block patches the framework file in PlatformIO's package cache
# (it does not live in the firmware-src tree). For each spin loop, wrap
# the wait with a deadline; on timeout, call a static helper that aborts
# the TWIM peripheral (TASKS_STOP → ENABLE bounce → clear events) and
# return the Wire-convention error code (0 from requestFrom, 4 from
# endTransmission).
#
# Upstream anchored at SHA e13f5820002a4fb2a5e6754b42ace185277e5adf of
# meshtastic/Adafruit_nRF52_Arduino, which LeapYeet/Meshtastic pin via
# framework-arduinoadafruitnrf52. Anchors are exact upstream text.
#
# See docs/wire-i2c-timeout-plan.md.

WIRE_NRF52_RELPATH = (
    "packages/framework-arduinoadafruitnrf52/libraries/Wire/Wire_nRF52.cpp"
)
WIRE_NRF52_MARKER = "// ff-builder: TWIM timeout guards"

WIRE_NRF52_HELPER = """// ff-builder: TWIM timeout guards
// Spin loops in requestFrom/endTransmission have no timeout upstream; a
// stuck peripheral (RF glitch + slave holding SDA) freezes the CPU
// forever. Bound each wait by a deadline; on timeout abort the TWIM.
#ifndef FFB_TWIM_TIMEOUT_MS
#define FFB_TWIM_TIMEOUT_MS 50
#endif
static void ffb_twim_force_reset(NRF_TWIM_Type *twim) {
  twim->TASKS_STOP = 0x1UL;
  uint32_t _ffb_d = millis() + 10;
  while (!twim->EVENTS_STOPPED && (int32_t)(millis() - _ffb_d) < 0) {}
  if (!twim->EVENTS_STOPPED) {
    twim->ENABLE = (TWIM_ENABLE_ENABLE_Disabled << TWIM_ENABLE_ENABLE_Pos);
    twim->ENABLE = (TWIM_ENABLE_ENABLE_Enabled  << TWIM_ENABLE_ENABLE_Pos);
  }
  twim->EVENTS_STOPPED   = 0x0UL;
  twim->EVENTS_ERROR     = 0x0UL;
  twim->EVENTS_TXSTARTED = 0x0UL;
  twim->EVENTS_RXSTARTED = 0x0UL;
  twim->EVENTS_LASTTX    = 0x0UL;
  twim->EVENTS_LASTRX    = 0x0UL;
  twim->EVENTS_SUSPENDED = 0x0UL;
}

"""

# requestFrom: 4 spin loops. Returns uint8_t byte count; 0 on timeout.
WIRE_NRF52_REQUEST_FROM_OLD = """uint8_t TwoWire::requestFrom(uint8_t address, size_t quantity, bool stopBit)
{
  if(quantity == 0)
  {
    return 0;
  }

  size_t byteRead = 0;
  rxBuffer.clear();

  _p_twim->ADDRESS = address;

  _p_twim->TASKS_RESUME = 0x1UL;
  _p_twim->RXD.PTR = (uint32_t)rxBuffer._aucBuffer;
  _p_twim->RXD.MAXCNT = quantity;
  _p_twim->TASKS_STARTRX = 0x1UL;

  while(!_p_twim->EVENTS_RXSTARTED && !_p_twim->EVENTS_ERROR);
  _p_twim->EVENTS_RXSTARTED = 0x0UL;

  while(!_p_twim->EVENTS_LASTRX && !_p_twim->EVENTS_ERROR);
  _p_twim->EVENTS_LASTRX = 0x0UL;

  if (stopBit || _p_twim->EVENTS_ERROR)
  {
    _p_twim->TASKS_STOP = 0x1UL;
    while(!_p_twim->EVENTS_STOPPED);
    _p_twim->EVENTS_STOPPED = 0x0UL;
  }
  else
  {
    _p_twim->TASKS_SUSPEND = 0x1UL;
    while(!_p_twim->EVENTS_SUSPENDED);
    _p_twim->EVENTS_SUSPENDED = 0x0UL;
  }

  if (_p_twim->EVENTS_ERROR)
  {
    _p_twim->EVENTS_ERROR = 0x0UL;
  }

  byteRead = rxBuffer._iHead = _p_twim->RXD.AMOUNT;

  return byteRead;
}"""

WIRE_NRF52_REQUEST_FROM_NEW = """uint8_t TwoWire::requestFrom(uint8_t address, size_t quantity, bool stopBit)
{
  if(quantity == 0)
  {
    return 0;
  }

  size_t byteRead = 0;
  rxBuffer.clear();

  _p_twim->ADDRESS = address;

  _p_twim->TASKS_RESUME = 0x1UL;
  _p_twim->RXD.PTR = (uint32_t)rxBuffer._aucBuffer;
  _p_twim->RXD.MAXCNT = quantity;
  _p_twim->TASKS_STARTRX = 0x1UL;

  {
    uint32_t _ffb_d = millis() + FFB_TWIM_TIMEOUT_MS;
    while (!_p_twim->EVENTS_RXSTARTED && !_p_twim->EVENTS_ERROR) {
      if ((int32_t)(millis() - _ffb_d) >= 0) { ffb_twim_force_reset(_p_twim); return 0; }
    }
  }
  _p_twim->EVENTS_RXSTARTED = 0x0UL;

  {
    uint32_t _ffb_d = millis() + FFB_TWIM_TIMEOUT_MS;
    while (!_p_twim->EVENTS_LASTRX && !_p_twim->EVENTS_ERROR) {
      if ((int32_t)(millis() - _ffb_d) >= 0) { ffb_twim_force_reset(_p_twim); return 0; }
    }
  }
  _p_twim->EVENTS_LASTRX = 0x0UL;

  if (stopBit || _p_twim->EVENTS_ERROR)
  {
    _p_twim->TASKS_STOP = 0x1UL;
    {
      uint32_t _ffb_d = millis() + FFB_TWIM_TIMEOUT_MS;
      while (!_p_twim->EVENTS_STOPPED) {
        if ((int32_t)(millis() - _ffb_d) >= 0) { ffb_twim_force_reset(_p_twim); return 0; }
      }
    }
    _p_twim->EVENTS_STOPPED = 0x0UL;
  }
  else
  {
    _p_twim->TASKS_SUSPEND = 0x1UL;
    {
      uint32_t _ffb_d = millis() + FFB_TWIM_TIMEOUT_MS;
      while (!_p_twim->EVENTS_SUSPENDED) {
        if ((int32_t)(millis() - _ffb_d) >= 0) { ffb_twim_force_reset(_p_twim); return 0; }
      }
    }
    _p_twim->EVENTS_SUSPENDED = 0x0UL;
  }

  if (_p_twim->EVENTS_ERROR)
  {
    _p_twim->EVENTS_ERROR = 0x0UL;
  }

  byteRead = rxBuffer._iHead = _p_twim->RXD.AMOUNT;

  return byteRead;
}"""

# endTransmission(bool): 4 spin loops. Returns uint8_t error code; 4 on timeout
# ("other error" per the Wire convention documented in the upstream comment).
WIRE_NRF52_END_TX_OLD = """uint8_t TwoWire::endTransmission(bool stopBit)
{
  transmissionBegun = false ;

  // Start I2C transmission
  _p_twim->ADDRESS = txAddress;

  // just in case twi is stopped by bus error such as secondary device reset/stalled without replying ACK/NACK
  _p_twim->EVENTS_STOPPED = 0x0UL;
  _p_twim->TASKS_RESUME = 0x1UL;

  _p_twim->TXD.PTR = (uint32_t)txBuffer._aucBuffer;
  _p_twim->TXD.MAXCNT = txBuffer.available();

  _p_twim->TASKS_STARTTX = 0x1UL;

  while(!_p_twim->EVENTS_TXSTARTED && !_p_twim->EVENTS_ERROR);
  _p_twim->EVENTS_TXSTARTED = 0x0UL;

  if (txBuffer.available()) {
    while(!_p_twim->EVENTS_LASTTX && !_p_twim->EVENTS_ERROR);
  }
  _p_twim->EVENTS_LASTTX = 0x0UL;

  if (stopBit || _p_twim->EVENTS_ERROR)
  {
    _p_twim->TASKS_STOP = 0x1UL;
    while(!_p_twim->EVENTS_STOPPED);
    _p_twim->EVENTS_STOPPED = 0x0UL;
  }
  else
  {
    _p_twim->TASKS_SUSPEND = 0x1UL;
    while(!_p_twim->EVENTS_SUSPENDED);
    _p_twim->EVENTS_SUSPENDED = 0x0UL;
  }"""

WIRE_NRF52_END_TX_NEW = """uint8_t TwoWire::endTransmission(bool stopBit)
{
  transmissionBegun = false ;

  // Start I2C transmission
  _p_twim->ADDRESS = txAddress;

  // just in case twi is stopped by bus error such as secondary device reset/stalled without replying ACK/NACK
  _p_twim->EVENTS_STOPPED = 0x0UL;
  _p_twim->TASKS_RESUME = 0x1UL;

  _p_twim->TXD.PTR = (uint32_t)txBuffer._aucBuffer;
  _p_twim->TXD.MAXCNT = txBuffer.available();

  _p_twim->TASKS_STARTTX = 0x1UL;

  {
    uint32_t _ffb_d = millis() + FFB_TWIM_TIMEOUT_MS;
    while (!_p_twim->EVENTS_TXSTARTED && !_p_twim->EVENTS_ERROR) {
      if ((int32_t)(millis() - _ffb_d) >= 0) { ffb_twim_force_reset(_p_twim); return 4; }
    }
  }
  _p_twim->EVENTS_TXSTARTED = 0x0UL;

  if (txBuffer.available()) {
    uint32_t _ffb_d = millis() + FFB_TWIM_TIMEOUT_MS;
    while (!_p_twim->EVENTS_LASTTX && !_p_twim->EVENTS_ERROR) {
      if ((int32_t)(millis() - _ffb_d) >= 0) { ffb_twim_force_reset(_p_twim); return 4; }
    }
  }
  _p_twim->EVENTS_LASTTX = 0x0UL;

  if (stopBit || _p_twim->EVENTS_ERROR)
  {
    _p_twim->TASKS_STOP = 0x1UL;
    {
      uint32_t _ffb_d = millis() + FFB_TWIM_TIMEOUT_MS;
      while (!_p_twim->EVENTS_STOPPED) {
        if ((int32_t)(millis() - _ffb_d) >= 0) { ffb_twim_force_reset(_p_twim); return 4; }
      }
    }
    _p_twim->EVENTS_STOPPED = 0x0UL;
  }
  else
  {
    _p_twim->TASKS_SUSPEND = 0x1UL;
    {
      uint32_t _ffb_d = millis() + FFB_TWIM_TIMEOUT_MS;
      while (!_p_twim->EVENTS_SUSPENDED) {
        if ((int32_t)(millis() - _ffb_d) >= 0) { ffb_twim_force_reset(_p_twim); return 4; }
      }
    }
    _p_twim->EVENTS_SUSPENDED = 0x0UL;
  }"""

# Helper goes right above the requestFrom signature (unique in the file).
WIRE_NRF52_HELPER_ANCHOR = (
    "uint8_t TwoWire::requestFrom(uint8_t address, size_t quantity, bool stopBit)"
)


def find_wire_nrf52_cpp():
    """Locate Wire_nRF52.cpp in PlatformIO's framework package cache.

    PlatformIO defaults its core dir to $HOME/.platformio but honors
    $PLATFORMIO_CORE_DIR. In the ff-builder Docker image the build runs as
    root, so /root/.platformio is the practical default. Check all three.
    """
    candidates = []
    if "PLATFORMIO_CORE_DIR" in os.environ:
        candidates.append(
            os.path.join(os.environ["PLATFORMIO_CORE_DIR"], WIRE_NRF52_RELPATH)
        )
    candidates += [
        os.path.expanduser(os.path.join("~/.platformio", WIRE_NRF52_RELPATH)),
        os.path.join("/root/.platformio", WIRE_NRF52_RELPATH),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    sys.exit(
        "ERROR: Wire_nRF52.cpp not found in any of:\n  "
        + "\n  ".join(candidates)
        + "\n(has the nRF52 framework been installed via `pio pkg install`?)"
    )


def patch_wire_nrf52_timeouts():
    path = find_wire_nrf52_cpp()
    with open(path) as f:
        content = f.read()

    if WIRE_NRF52_MARKER in content:
        print(f"Skipped {path}: already patched")
        return

    missing = []
    if WIRE_NRF52_HELPER_ANCHOR not in content:
        missing.append("helper anchor (requestFrom signature)")
    if WIRE_NRF52_REQUEST_FROM_OLD not in content:
        missing.append("requestFrom body")
    if WIRE_NRF52_END_TX_OLD not in content:
        missing.append("endTransmission body")
    if missing:
        sys.exit(
            f"ERROR: {path} does not match expected upstream text "
            f"(missing: {', '.join(missing)}). "
            "Has framework-arduinoadafruitnrf52 drifted from "
            "SHA e13f5820002a4fb2a5e6754b42ace185277e5adf?"
        )

    content = content.replace(
        WIRE_NRF52_HELPER_ANCHOR,
        WIRE_NRF52_HELPER + WIRE_NRF52_HELPER_ANCHOR,
        1,
    )
    content = content.replace(WIRE_NRF52_REQUEST_FROM_OLD, WIRE_NRF52_REQUEST_FROM_NEW, 1)
    content = content.replace(WIRE_NRF52_END_TX_OLD, WIRE_NRF52_END_TX_NEW, 1)

    with open(path, "w") as f:
        f.write(content)
    print(f"Patched {path}: TWIM spin-loop timeouts (helper + 2 functions)")


MAG_MODULE_CPP = "src/modules/MagnetometerModule.cpp"
MAG_MODULE_H   = "src/modules/MagnetometerModule.h"


# --- QMC resilience (gh issue #43) ----------------------------------------
#
# Three coordinated changes that attack the *cause* of the I2C-bus-stuck
# failure mode rather than recovering from it after the fact:
#
#   ff-0w2  Lower the chip's ODR from 200 Hz to 50 Hz (CTRL1 0x1D -> 0x05).
#           At 200 Hz the chip's internal sampling state machine is
#           running constantly — every sample window is an exposure point
#           where a coincident RF glitch can wedge the slave mid-byte.
#           50 Hz is 4x less internal activity; sample staleness max 20 ms
#           is invisible to the compass UI. Also corrects RNG: upstream
#           wrote 01 commented as "2G" but per the QMC5883L datasheet 01
#           is 8G — we want true 2G (00). Earth's field is ~0.5 G so 2 G
#           full-scale gives 4x headroom and better SNR.
#
#   ff-h3y  Lower the polling cadence from 50 ms (20 Hz) to 250 ms (4 Hz).
#           The compass UI does not need 20 readings per second. 4 Hz is
#           snappier than the eye can read a heading and quartiers the
#           I2C transaction count, proportionally reducing exposure to
#           RF-coincident transactions.
#
#   ff-hfa  Disciplined recovery: increment a streak counter on each read
#           failure; at streak == 5 (~1.25 s of bad bus) attempt ONE
#           recovery (magBus->end() + brief delay + magBus->begin() +
#           qmcInit()) — no bit-bang, no Wire-side recovery. If the very
#           next read still fails (streak >= 6), set headingIsValid=false
#           so the existing if(!headingIsValid) early-exit takes over and
#           the mag stays quiet until reboot. NO preemptive recovery at
#           boot — only after observed runtime failure.
#
# Together (1)+(2) should collapse the failure rate to "occasionally needs
# recovery" instead of "needs recovery within 70 seconds, every time."
# (3) handles the residual without the band-aid stacking that PR #44 fell
# into.

QMC_RES_H_OLD = (
    "// Logging cadence\n"
    "    uint32_t lastLogMs = 0;\n"
)
QMC_RES_H_NEW = (
    "// Logging cadence\n"
    "    uint32_t lastLogMs = 0;\n"
    "    uint8_t  qmcFailCount = 0;  // ff-builder (gh #43): I2C read-failure streak\n"
)

# ODR: CTRL1 0x1D -> 0x05.
QMC_RES_CTRL1_OLD = (
    "    // CTRL1: OSR=512 (00), RNG=2G (01), ODR=200Hz (11), MODE=continuous (01) -> 0x1D\n"
    "    if (!qmcWriteReg(bus, addr, QMC_REG_CTRL1, 0x1D)) {\n"
)
QMC_RES_CTRL1_NEW = (
    "    // ff-builder (gh #43): drop ODR from 200Hz to 50Hz; correct RNG to 2G.\n"
    "    //   At 200Hz the internal state machine runs continuously and every\n"
    "    //   sample window is an exposure point for RF-coincident I2C glitches.\n"
    "    //   50Hz is 4x less activity; sample staleness max 20ms.\n"
    "    //   Upstream comment said RNG=01 was 2G but per QMC5883L datasheet 01\n"
    "    //   is 8G; use 00 for true 2G (Earth ~0.5G, 4x headroom).\n"
    "    // CTRL1: OSR=512 (00), RNG=2G (00), ODR=50Hz (01), MODE=continuous (01) -> 0x05\n"
    "    if (!qmcWriteReg(bus, addr, QMC_REG_CTRL1, 0x05)) {\n"
)

# Failure recovery block.
QMC_RES_READ_OLD = (
    "    // Read MAG (bus-agnostic)\n"
    "    int16_t rx, ry, rz;\n"
    "    if (!qmcReadRaw(*magBus, magAddr, rx, ry, rz)) {\n"
    "        LOG_INFO(\"[Magnetometer] QMC read failed; will retry.\");\n"
    "        return 100;\n"
    "    }\n"
)
QMC_RES_READ_NEW = """\
// Read MAG (bus-agnostic)
    int16_t rx, ry, rz;
    if (!qmcReadRaw(*magBus, magAddr, rx, ry, rz)) {
        qmcFailCount++;
        LOG_INFO("[Magnetometer] QMC read failed (streak %d).", (int)qmcFailCount);
        if (qmcFailCount == 5) {
            // ff-builder (gh #43): one recovery attempt — bus bounce +
            // chip reconfigure. No bit-bang, no Wire-side recovery.
            LOG_WARN("[Magnetometer] Bus stuck. Bouncing %s and reinitializing QMC...",
                     (magBus == &Wire) ? "Wire" : "Wire1");
            magBus->end();
            delay(5);
            magBus->begin();
            (void)qmcInit(*magBus, magAddr);
        } else if (qmcFailCount >= 6) {
            // Recovery did not bring the chip back. Mark disabled —
            // the existing if(!headingIsValid) early-exit in runOnce
            // takes over and we stay quiet until reboot.
            LOG_WARN("[Magnetometer] QMC unrecoverable after bus reset. "
                     "Disabling magnetometer until reboot.");
            headingIsValid = false;
        }
        return 250;
    }
    qmcFailCount = 0;
"""

# Polling cadence: bottom of runOnce, "return 50;" -> "return 250;".
# Anchored on the two lines immediately above so the match is unique.
QMC_RES_TAIL_OLD = (
    "        lastLogMs = now;\n"
    "    }\n"
    "\n"
    "    return 50;\n"
    "}\n"
)
QMC_RES_TAIL_NEW = (
    "        lastLogMs = now;\n"
    "    }\n"
    "\n"
    "    // ff-builder (gh #43): 250ms (4Hz) is plenty for compass UI and\n"
    "    // quartiers I2C transactions vs. the upstream 50ms (20Hz) cadence.\n"
    "    return 250;\n"
    "}\n"
)


def patch_qmc_resilience():
    h_existing = open(MAG_MODULE_H).read()
    if "qmcFailCount" in h_existing:
        print(f"Skipped {MAG_MODULE_H} + {MAG_MODULE_CPP}: already patched")
        return

    cpp_existing = open(MAG_MODULE_CPP).read()
    checks = [
        (MAG_MODULE_H,   QMC_RES_H_OLD,     "header: lastLogMs anchor"),
        (MAG_MODULE_CPP, QMC_RES_CTRL1_OLD, "cpp: qmcInit CTRL1 write"),
        (MAG_MODULE_CPP, QMC_RES_READ_OLD,  "cpp: runOnce read-failure block"),
        (MAG_MODULE_CPP, QMC_RES_TAIL_OLD,  "cpp: runOnce tail return"),
    ]
    for path, old, label in checks:
        src = h_existing if path == MAG_MODULE_H else cpp_existing
        if old not in src:
            sys.exit(f"ERROR: qmc_resilience anchor not found ({label}) in {path}")

    h_new = h_existing.replace(QMC_RES_H_OLD, QMC_RES_H_NEW, 1)
    with open(MAG_MODULE_H, "w") as f:
        f.write(h_new)

    cpp_new = cpp_existing
    cpp_new = cpp_new.replace(QMC_RES_CTRL1_OLD, QMC_RES_CTRL1_NEW, 1)
    cpp_new = cpp_new.replace(QMC_RES_READ_OLD,  QMC_RES_READ_NEW,  1)
    cpp_new = cpp_new.replace(QMC_RES_TAIL_OLD,  QMC_RES_TAIL_NEW,  1)
    with open(MAG_MODULE_CPP, "w") as f:
        f.write(cpp_new)

    print(f"Patched {MAG_MODULE_H} + {MAG_MODULE_CPP}: QMC resilience "
          f"(ODR 200->50Hz, polling 50->250ms, safety-net recovery)")


# --- Trim friendFinderBaseMenu (ff-iic) -----------------------------------
#
# Remove "Track a Friend" and "Dev Tools" from the Captain Compass menu.
# After this patch the menu is: Back / Saved Places / Compass Cal.
#
# The push_back list and the selected==N callback dispatch must stay in
# lockstep, so both are anchored as a single block each. OLD anchors
# match the state AFTER patch_menu_ordering + patch_compass_redesign
# have run — this patch goes LAST.
#
# Underlying handlers (friend_finder_list_menu, friend_finder_dev_tools_menu)
# are left in place: friend_finder_list_menu is still reachable from the
# favorites long-press "Track" entry, and dev tools is dead code we can
# excise in a separate sweep if/when wanted.

TRIM_PUSHBACK_OLD = """    options.push_back("Back");
    options.push_back("Track a Friend");
    options.push_back("Saved Places");
    options.push_back("Compass Cal");
    options.push_back("Dev Tools");
"""

TRIM_PUSHBACK_NEW = """    options.push_back("Back");
    options.push_back("Saved Places");
    options.push_back("Compass Cal");
"""

TRIM_CALLBACK_OLD = """        if (selected == 0) { // Back
            if (friendFinderModule) friendFinderModule->setState(FriendFinderState::IDLE);
        } else if (selected == 1) { // Track a Friend
            if (friendFinderModule) {
                if (!friendFinderModule->spoofModeEnabled && friendFinderModule->getUsedFriendsCount() == 0) {
                    screen->showSimpleBanner("No friends saved", 1200);
                } else {
                    menuQueue = friend_finder_list_menu;
                    screen->runNow();
                }
            }
        } else if (selected == 2) { // Saved Places
            menuQueue = friend_finder_places_menu;
            screen->runNow();
        } else if (selected == 3) { // Compass Cal
            if (friendFinderModule) friendFinderModule->setState(FriendFinderState::COMPASS_SCREEN);
        } else if (selected == 4) { // Dev Tools
            menuQueue = friend_finder_dev_tools_menu;
            screen->runNow();
        }
"""

TRIM_CALLBACK_NEW = """        if (selected == 0) { // Back
            if (friendFinderModule) friendFinderModule->setState(FriendFinderState::IDLE);
        } else if (selected == 1) { // Saved Places
            menuQueue = friend_finder_places_menu;
            screen->runNow();
        } else if (selected == 2) { // Compass Cal
            if (friendFinderModule) friendFinderModule->setState(FriendFinderState::COMPASS_SCREEN);
        }
"""

TRIM_MARKER = "// ff-builder: trimmed friendFinderBaseMenu"


def patch_trim_friend_finder_menu():
    src = open(MENU_HANDLER_CPP).read()
    if TRIM_MARKER in src:
        print(f"Skipped {MENU_HANDLER_CPP}: trim already applied")
        return
    missing = []
    if TRIM_PUSHBACK_OLD not in src:
        missing.append("push_back block")
    if TRIM_CALLBACK_OLD not in src:
        missing.append("callback block")
    if missing:
        sys.exit(
            f"ERROR: trim_friend_finder_menu anchor(s) not found in {MENU_HANDLER_CPP}: "
            + ", ".join(missing)
        )

    src = src.replace(
        TRIM_PUSHBACK_OLD,
        "    " + TRIM_MARKER + ": Track a Friend + Dev Tools removed\n" + TRIM_PUSHBACK_NEW,
        1,
    )
    src = src.replace(TRIM_CALLBACK_OLD, TRIM_CALLBACK_NEW, 1)
    with open(MENU_HANDLER_CPP, "w") as f:
        f.write(src)
    print(f"Patched {MENU_HANDLER_CPP}: trimmed friendFinderBaseMenu (-Track a Friend, -Dev Tools)")


# --- "Captain Compass" rename (ff-1d2) ------------------------------------
#
# User-facing relabel of the Friend Finder feature in the menu UI. Only
# the three quoted strings that the user actually reads are changed:
# the home-menu entry, the favorites-menu "Track" entry, and the banner
# title. Internal C++ identifiers (FriendFinderModule, friendFinderModule,
# friendFinderBaseMenu, file paths, comments) are untouched — they're
# code, not UX.
#
# Runs LAST in __main__ so the home-menu and favorites-menu strings are
# already in the post-patch positions established by patch_menu_ordering
# and patch_compass_redesign. The bannerOptions.message anchor is also
# unaffected by patch_trim_friend_finder_menu (which only touches the
# push_back block and selected==N callback).

CAPTAIN_RENAMES = [
    ('    optionsArray[options] = "Friend Finder";',
     '    optionsArray[options] = "Captain Compass";'),
    ('    optionsArray[options] = "Track (Friend Finder)";',
     '    optionsArray[options] = "Track (Captain Compass)";'),
    ('    bannerOptions.message = "Friend Finder";',
     '    bannerOptions.message = "Captain Compass";'),
]


def patch_captain_compass_rename():
    path = MENU_HANDLER_CPP
    src = open(path).read()
    if '"Captain Compass"' in src:
        print(f"Skipped {path}: Captain Compass rename already applied")
        return
    missing = [old for old, _ in CAPTAIN_RENAMES if old not in src]
    if missing:
        sys.exit(
            f"ERROR: captain_compass rename anchor(s) not found in {path}:\n  "
            + "\n  ".join(missing)
        )
    for old, new in CAPTAIN_RENAMES:
        src = src.replace(old, new, 1)
    with open(path, "w") as f:
        f.write(src)
    print(f"Patched {path}: 'Friend Finder' -> 'Captain Compass' ({len(CAPTAIN_RENAMES)} labels)")


if __name__ == "__main__":
    patch_variant_ini()
    patch_friend_finder_include()
    patch_friend_finder_persistence()
    patch_menu_ordering()
    patch_compass_redesign()
    patch_wire_nrf52_timeouts()
    patch_qmc_resilience()
    patch_trim_friend_finder_menu()
    patch_captain_compass_rename()
