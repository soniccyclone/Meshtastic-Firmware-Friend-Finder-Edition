## Why

GitHub issue [#27](https://github.com/soniccyclone/Meshtastic-Firmware-Friend-Finder-Edition/issues/27): Friend Finder is the headline feature of this fork, and its discoverability today is bad. The "Friend Finder" entry sits roughly 6th in the Home Action menu, behind Backlight, position-send, preset/freetext composers — anyone using this firmware is here for Friend Finder; the menu shouldn't bury it. Same shape inside the Friend Finder submenu: "Track a Friend" is the action people actually open the menu to perform, but it sits below "Start Pairing." Pairing is a one-time setup step; tracking is the daily-driver action.

Both items get promoted to the first actionable position — second after "Back" — keeping the convention used everywhere else in this menu system that "Back" stays at index 0 as the universal nav primitive.

## What Changes

- **Home Action menu** ([MenuHandler.cpp:471-507](../../../code-stuff/LeapYeet-firmware/src/graphics/draw/MenuHandler.cpp#L471-L507) `menuHandler::homeBaseMenu`): "Friend Finder" moves to the first actionable position (immediately after "Back"). Other items keep their relative order. The callback is unchanged because it dispatches on enum values, not array indices.
- **Friend Finder menu** ([MenuHandler.cpp:1554-1601](../../../code-stuff/LeapYeet-firmware/src/graphics/draw/MenuHandler.cpp#L1554-L1601) `menuHandler::friendFinderBaseMenu`): "Track a Friend" moves to the first actionable position (immediately after "Back"). The callback dispatches on literal `selected == N` indices, so the case-branch numbering also shifts — see design.md D2 for the renumbering plan.
- Ships as a new patch block in `patch-t114.py` and `patch-native.py`, matching the existing patch-architecture discipline. No fork of upstream.

## Capabilities

### New Capabilities
- `friend-finder-menu-ordering`: normative ordering rules for the two Friend Finder-relevant menus. Encodes the "Friend Finder is the headline feature; surface it first" intent so future menu edits don't quietly regress it.

### Modified Capabilities
*(none)*

## Impact

- **Patch infrastructure**: one new marker-guarded block in `patch-t114.py`, plus the matching block in `patch-native.py` so smoke tests build the same code path. Same shape as existing patches.
- **Build**: no new dependencies. Pure source-level reorder.
- **Runtime cost**: zero. Same number of menu options, same callback semantics — only display order and case-branch numbering change.
- **Test surface**: no new unit/smoke tests required for a two-string reorder. The existing `entrypoint-smoke.sh` build verifies the patch applies and compiles. Manual visual verification post-merge is sufficient.
- **Risk**: very low. The only meaningful failure mode is a typo in the case-branch renumber for `friendFinderBaseMenu` (e.g. swapping the `Start Pairing` and `Track a Friend` callbacks). Easily caught by a 30-second on-device sanity check.
