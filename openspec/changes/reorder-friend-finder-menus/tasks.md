## 1. Patch — `patch-t114.py`

- [x] 1.1 Added `MENU_ORDERING_MARKER = "// ff-builder: menu ordering"` and `MENU_HANDLER_CPP` path constants
- [x] 1.2 Added `patch_menu_ordering()` with early-return idempotency check on `MENU_ORDERING_MARKER in content`
- [x] 1.3 Implemented `homeBaseMenu` reorder via two surgical replacements (`MENU_HOME_INSERT_*` + `MENU_HOME_REMOVE_*`) instead of one giant span — avoids the trailing-whitespace-line trap on line 502 of upstream `MenuHandler.cpp`. Anchor on the unique `enum optionsNumbers { Back, Backlight, ... };` line
- [x] 1.4 Implemented `friendFinderBaseMenu` reorder via two surgical replacements (`MENU_FRIEND_PUSHBACK_*` + `MENU_FRIEND_CALLBACK_*`) — push_back swap is independent from the callback swap, both anchored on whitespace-clean spans. Same surgical approach as 1.3 for the same reason (trailing spaces on the "Saved Places"/"Compass Cal" lines just below)
- [x] 1.5 Hooked `patch_menu_ordering()` into `if __name__ == "__main__":` after `patch_friend_finder_persistence()`
- [x] 1.6 Verified idempotency: ran `patch-t114.py` twice on a fresh upstream `LeapYeet/firmware` clone, second run prints `Skipped src/graphics/draw/MenuHandler.cpp: menu ordering already patched`

## 2. Patch — `patch-native.py`

- [x] 2.1 Mirrored the same `MENU_ORDERING_MARKER`, `MENU_HANDLER_CPP`, and `patch_menu_ordering()` block in `patch-native.py`. Native and T114 share the same `MenuHandler.cpp`, so the patch text is identical (just bare strings, no `.format()` for the parts that don't need substitution)
- [x] 2.2 Hooked `patch_menu_ordering()` into `patch-native.py`'s `if __name__ == "__main__":` after `patch_friend_finder_persistence()`
- [x] 2.3 Verified idempotency on a fresh clone (after resetting `MenuHandler.cpp` to upstream)

## 3. Build verification

- [x] 3.1 `make build` succeeded — clean T114 `firmware.uf2` produced with the menu-reorder patch applied. No patch-anchor failures, no compile errors
- [x] 3.2 Native image rebuilt + full smoke suite (two_node_smoke, pairing_test, persistence_test) passed end-to-end. Note: `pairing_test` is intermittently flaky (~20% on a sample of 5 runs) due to a pre-existing race in the pairing protocol when both nodes happen to broadcast REQUEST simultaneously and one node ends up interpreting the other's ACCEPT as a proposal. Unrelated to this change — the menu reorder doesn't touch any radio/protocol code path. Logged for awareness; not blocking

## 4. On-device QA

- [ ] 4.1 Flash the RC UF2 onto a T114. Open Home → Action menu. Confirm order: `Back`, `Friend Finder`, then the existing remaining items
- [ ] 4.2 Select `Friend Finder`. Confirm the Friend Finder base menu opens correctly
- [ ] 4.3 In the Friend Finder menu, confirm order: `Back`, `Track a Friend`, `Start Pairing`, `Saved Places`, `Compass Cal`, `Dev Tools`
- [ ] 4.4 Tap each menu option and confirm it runs the action its label describes (especially the swapped pair: `Track a Friend` opens the friend list, `Start Pairing` initiates pairing — NOT the other way around)

## 5. PR + release

- [ ] 5.1 PR title references issue #27 with `Closes #27`
- [ ] 5.2 No release-note required (UI tweak; visible at first glance)
