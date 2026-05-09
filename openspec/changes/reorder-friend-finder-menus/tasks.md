## 1. Patch — `patch-t114.py`

- [ ] 1.1 Add a `MENU_ORDERING_MARKER` constant to the script header (e.g. `// ff-builder: menu ordering`)
- [ ] 1.2 Add `patch_menu_ordering()` function with an early-return idempotency check (`if MENU_ORDERING_MARKER in content: return`)
- [ ] 1.3 Implement the `homeBaseMenu` reorder per design.md D3: move the two-line `"Friend Finder"` assignment block from its current position (between Freetext and Bluetooth) to immediately after `int options = 1;`. Old/New strings span enough context to make the move unique and review-able
- [ ] 1.4 Implement the `friendFinderBaseMenu` reorder per design.md D2: in a single old/new replacement, move `options.push_back("Track a Friend");` from line 1563 → between `"Back"` and `"Start Pairing"`, AND swap the case-branch logic so `selected == 1` runs the Track-a-Friend action and `selected == 2` runs the Start-Pairing action
- [ ] 1.5 Hook `patch_menu_ordering()` into `if __name__ == "__main__":`
- [ ] 1.6 Verify idempotency: run `patch-t114.py` twice on a fresh `LeapYeet/firmware` clone @ `f49f9b7` (or current pinned SHA); second run prints "skipped" and produces no further file modifications

## 2. Patch — `patch-native.py`

- [ ] 2.1 Mirror the same `MENU_ORDERING_MARKER` + `patch_menu_ordering()` block in `patch-native.py`. Native and T114 share the same `MenuHandler.cpp`, so the patch text is identical
- [ ] 2.2 Hook into `if __name__ == "__main__":` after the existing patch functions
- [ ] 2.3 Verify idempotency on a fresh clone

## 3. Build verification

- [ ] 3.1 `make build` produces a clean T114 `firmware.uf2` with the patch applied (no patch-anchor failures, no compile errors)
- [ ] 3.2 `podman build -t ff-builder-native:local -f Dockerfile.native .` + `podman run --entrypoint entrypoint-smoke.sh` still passes the existing smoke suite — no regression in the persistence test or pairing test

## 4. On-device QA

- [ ] 4.1 Flash the RC UF2 onto a T114. Open Home → Action menu. Confirm order: `Back`, `Friend Finder`, then the existing remaining items
- [ ] 4.2 Select `Friend Finder`. Confirm the Friend Finder base menu opens correctly
- [ ] 4.3 In the Friend Finder menu, confirm order: `Back`, `Track a Friend`, `Start Pairing`, `Saved Places`, `Compass Cal`, `Dev Tools`
- [ ] 4.4 Tap each menu option and confirm it runs the action its label describes (especially the swapped pair: `Track a Friend` opens the friend list, `Start Pairing` initiates pairing — NOT the other way around)

## 5. PR + release

- [ ] 5.1 PR title references issue #27 with `Closes #27`
- [ ] 5.2 No release-note required (UI tweak; visible at first glance)
