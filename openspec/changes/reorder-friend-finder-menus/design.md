## Context

This repo wraps upstream `LeapYeet/firmware` via `patch-t114.py` / `patch-native.py`. Menu UI lives in [`src/graphics/draw/MenuHandler.cpp`](../../../code-stuff/LeapYeet-firmware/src/graphics/draw/MenuHandler.cpp) at upstream SHA `f49f9b7`, read directly during this proposal.

Two distinct menu structures need a reorder, with two distinct patch shapes.

**Menu 1 — `homeBaseMenu` (cpp:471-507).** Builds its options array via an enum + counter pattern:

```cpp
enum optionsNumbers { Back, Backlight, Position, Preset, Freetext, FriendFinder, Bluetooth, Sleep, enumEnd };
static const char *optionsArray[enumEnd] = {"Back"};
static int optionsEnumArray[enumEnd] = {Back};
int options = 1;
// ... conditional fills ...
optionsArray[options] = "Friend Finder";
optionsEnumArray[options++] = FriendFinder;
optionsArray[options] = "Bluetooth Toggle";
optionsEnumArray[options++] = Bluetooth;
```

The callback (cpp:518+) dispatches on enum values (`selected == FriendFinder`), not array indices. **Reordering visually = moving the assignment block earlier; callback is untouched.**

**Menu 2 — `friendFinderBaseMenu` (cpp:1554-1601).** Builds via a vector with literal `push_back`:

```cpp
options.push_back("Back");
options.push_back("Start Pairing");
options.push_back("Track a Friend");
options.push_back("Saved Places");
options.push_back("Compass Cal");
options.push_back("Dev Tools");
```

The callback dispatches on literal indices (`selected == 0` = Back, `selected == 1` = Start Pairing, etc.). **Reordering visually requires renumbering the case-branch literals to match new indices.** Different code shape, slightly more invasive than menu 1.

The codebase has a clear discipline: every behavior change ships as a marker-guarded textual patch block in `patch-t114.py` and `patch-native.py`. We follow that pattern.

## Goals / Non-Goals

**Goals:**
- "Friend Finder" appears immediately after "Back" in `homeBaseMenu`, on every conditional combination of build flags.
- "Track a Friend" appears immediately after "Back" in `friendFinderBaseMenu`, with all other items keeping their pre-existing relative order.
- Each option's selection still triggers its original action — no behavioral change beyond visual position.
- Single, idempotent, marker-guarded patch block per script (`patch-t114.py`, `patch-native.py`).

**Non-Goals:**
- Refactoring `friendFinderBaseMenu` to use named enum dispatch like `homeBaseMenu`. Tempting (would prevent future reorder bugs) but scope creep — this is a two-string move.
- Reordering anything else in either menu.
- Reordering inside the other Friend Finder menus (`friendFinderListMenu`, `friendFinderListActionMenu`, `friendFinderPlacesMenu`, `friendFinderDevToolsMenu`). Out of scope.
- Touching the home menu's Bluetooth, Sleep, etc. relative positions. They keep their pre-existing order; only "Friend Finder" moves.
- A new smoke test. The reorder is visually-verifiable in 30 seconds on hardware.

## Decisions

### D1 — Move target: index 1 (immediately after Back), not index 0

"Back" stays at index 0 across every menu in this codebase — it's the universal nav primitive. Moving "Friend Finder" / "Track a Friend" *above* "Back" would require either dropping "Back" entirely from these two menus (breaks navigation) or making "Back" the only option that doesn't appear at idx 0 (inconsistency with every other menu). User explicitly confirmed this interpretation when prompted during /opsx:propose.

**Alternatives considered:**
- *Index 0, before Back.* Rejected per above.
- *Replace Back with the target option, move Back to bottom.* Rejected — no other menu in this codebase does this; would surprise users mid-session.

### D2 — `friendFinderBaseMenu` renumber strategy: shift all callbacks by one slot

After moving "Track a Friend" from index 2 → index 1, the new index → action mapping is:

| New idx | Option            | Old idx |
|---------|-------------------|---------|
| 0       | Back              | 0       |
| 1       | Track a Friend    | 2       |
| 2       | Start Pairing     | 1       |
| 3       | Saved Places      | 3       |
| 4       | Compass Cal       | 4       |
| 5       | Dev Tools         | 5       |

The patch must update `if (selected == 1)` (was Start Pairing → now Track a Friend) and `if (selected == 2)` (was Track a Friend → now Start Pairing). Other branches (3, 4, 5) keep their actions because their options didn't move relative to each other.

The cleanest way to express this in a patch is a single old/new string replacement covering both the `push_back` block AND the callback body — anchor on a span that includes both, so we can't accidentally apply one half without the other. Patch fails loudly on missing anchor.

### D3 — `homeBaseMenu` reorder: move only the "Friend Finder" assignment block

Because `homeBaseMenu`'s callback dispatches on enum values, the visual order is independent of the callback. We just move the two-line block:

```cpp
optionsArray[options] = "Friend Finder";
optionsEnumArray[options++] = FriendFinder;
```

…from its current position (between Freetext and Bluetooth Toggle) to immediately after the initial `int options = 1;` line, before any conditional or unconditional fills. The conditional fills below it now target `options` starting at 2 (or whatever the post-FriendFinder counter value is) — still correct because they all use `options++`.

Alternative: swap `enum optionsNumbers` ordering. Rejected — doesn't change anything, since callback uses enum values, not enum integer values; and reordering an enum changes its underlying integer values, which could subtly affect anything else that hardcodes them (does not appear to be the case here, but unnecessary risk).

### D4 — One marker, two replacements per script

The new patch function `patch_menu_ordering()` runs two `content.replace` calls (one per menu) inside a single `MARKER`-guarded block. If either anchor is missing, fail with a clear error pointing at the offending menu — same `sys.exit` pattern used by existing patch functions. Idempotent via `MENU_ORDERING_MARKER in content` early return.

### D5 — No on-disk persistence, no protobuf, no smoke test

This is a pure source reorder with zero runtime state. Existing CI build verification (`pr-build-t114.yml`) is sufficient to catch syntax errors. Manual visual verification on a T114 (or in the native simulator's banner output, if logged) is sufficient to catch a callback-renumber typo. Adding a smoke test for menu order would require parsing the banner UI, which has no existing harness.

## Risks / Trade-offs

- **Risk:** Callback-renumber typo in `friendFinderBaseMenu` (e.g. swapping the actions for Start Pairing and Track a Friend). **Mitigation:** the patch's `OLD` / `NEW` strings cover BOTH the `push_back` block and the callback body in one replacement, making it visually obvious when reviewing the patch. On-device QA catches it in seconds.
- **Risk:** Upstream renames "Friend Finder" / "Track a Friend" / "Back" or restructures the menu builder. **Mitigation:** the patch fails loudly on missing anchor (same as existing patches). Re-anchor when an upstream rev moves; small surface, easy to fix.
- **Risk:** Future menu addition (e.g. a new Friend Finder option) is added at the wrong position because the spec requirement is unclear about where new options go. **Mitigation:** the spec specifies the relative order of *named* options; new options have no defined position and a follow-up is expected to update the spec when one is added. Acceptable.
- **Trade-off:** Not refactoring `friendFinderBaseMenu` to use enum dispatch. Cost: future reorders will hit the same renumbering trap. Benefit: tiny patch surface, no churn. Acceptable for a one-line move; revisit if a third reorder happens.

## Migration Plan

**Deploy:**
1. Land the patch in `patch-t114.py` and `patch-native.py`.
2. CI (`pr-build-t114.yml`) builds an RC UF2 per push.
3. Visual verification: flash on a T114, open the Home Action menu, confirm "Friend Finder" appears just below "Back". Open Friend Finder, confirm "Track a Friend" appears just below "Back". Tap each option and confirm it does what the label says.
4. Tag the firmware release.

**Rollback:** Remove the patch block from the two scripts and rebuild. No on-disk state migration required.

## Open Questions

*(none — change scope is small enough that the design is fully determined)*
