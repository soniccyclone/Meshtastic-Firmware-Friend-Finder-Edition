## ADDED Requirements

### Requirement: "Friend Finder" SHALL be the first actionable item in the Home Action menu

In the menu shown by `menuHandler::homeBaseMenu` (entered from the device's Home → Action UI), the option labeled `"Friend Finder"` SHALL appear immediately after the `"Back"` option — at array index 1 — regardless of which other conditional entries (`"Toggle Backlight"`/`"Sleep Screen"`, `"Send Position"`/`"Send Node Info"`, `"New Preset"`/`"New Preset Msg"`, `"New Freetext Msg"`, `"Bluetooth Toggle"`) are compiled in for the current target.

#### Scenario: Default T114 build

- **WHEN** the device is built for the `heltec-mesh-node-t114` PlatformIO environment with `kb_found = true` and the default GPS configuration
- **THEN** the Home Action menu SHALL display, in order: `"Back"`, `"Friend Finder"`, then the remaining conditionally-included items (sleep/backlight, position/node-info, preset, freetext, bluetooth) in their pre-existing relative order

#### Scenario: Build without keyboard

- **WHEN** the device is built for a target where `kb_found = false`
- **THEN** the Home Action menu SHALL display, in order: `"Back"`, `"Friend Finder"`, then the remaining conditionally-included items minus `"New Freetext Msg"`, in their pre-existing relative order

#### Scenario: Selecting Friend Finder still routes to the FriendFinder action

- **WHEN** the user selects `"Friend Finder"` from the Home Action menu
- **THEN** the existing callback path SHALL execute (transitioning to the Friend Finder base menu), unchanged from the prior behavior — only the visual position of the option moved, not its action

### Requirement: "Track a Friend" SHALL be the first actionable item in the Friend Finder base menu

In the menu shown by `menuHandler::friendFinderBaseMenu`, the option labeled `"Track a Friend"` SHALL appear immediately after the `"Back"` option — at array index 1 — with the remaining options (`"Start Pairing"`, `"Saved Places"`, `"Compass Cal"`, `"Dev Tools"`) following in their pre-existing relative order.

#### Scenario: Friend Finder base menu order

- **WHEN** the user opens the Friend Finder base menu (e.g. by selecting `"Friend Finder"` from the Home Action menu)
- **THEN** the menu SHALL display, in order: `"Back"`, `"Track a Friend"`, `"Start Pairing"`, `"Saved Places"`, `"Compass Cal"`, `"Dev Tools"`

#### Scenario: Track a Friend with no friends saved still shows the existing banner

- **WHEN** the user selects `"Track a Friend"` and `friendFinderModule->getUsedFriendsCount() == 0` and `spoofModeEnabled` is false
- **THEN** the existing `"No friends saved"` banner SHALL appear for ~1200 ms before returning the user to the Friend Finder base menu — behavior unchanged from the prior implementation

#### Scenario: Each menu option still routes to its original action

- **WHEN** the user selects any option in the Friend Finder base menu
- **THEN** the action that runs SHALL be the one that ran for that label before the reorder (so the case-branch renumbering in the callback must track the new option order, not preserve the old `selected == N` literal mappings)

### Requirement: Both menu reorders SHALL ship as marker-guarded patch blocks

The reorder SHALL be implemented as a new idempotent, marker-guarded block in `patch-t114.py` and a matching block in `patch-native.py`. The blocks SHALL anchor on stable strings in `MenuHandler.cpp` such that running either patch script twice on a fresh upstream clone produces the same result as running it once.

#### Scenario: Patch is idempotent

- **WHEN** `patch-t114.py` (or `patch-native.py`) is run twice in succession against a freshly-cloned upstream firmware tree
- **THEN** the second run SHALL produce no further file modifications and SHALL print a "skipped" log line consistent with the existing patch blocks' idempotency pattern
