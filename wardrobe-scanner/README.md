# Wardrobe Scanner (Cyberpunk 2077)

Scan any NPC with the Kiroshi scanner, press a key, and the clothes they are wearing land in V's backpack
(and, optionally, in the wardrobe for transmog). Works on living and dead NPCs.

A Cyber Engine Tweaks (CET) mod written in Lua. No other dependencies.

## Install

1. Install [Cyber Engine Tweaks](https://www.nexusmods.com/cyberpunk2077/mods/107).
2. Copy the `bin` folder from this repository into your `Cyberpunk 2077` game folder, so you end up with
   `Cyberpunk 2077\bin\x64\plugins\cyber_engine_tweaks\mods\WardrobeScanner\init.lua`.
3. Start the game, open the CET overlay (default key: `~`), go to **Bindings** and bind:
   - **Wardrobe Scanner: take scanned NPC's clothes** (the main key)
   - **Wardrobe Scanner: dump target slots to console** (optional, for troubleshooting)

## Use

1. Hold the scanner on an NPC.
2. Press the take key. A message lists what you got, for example `Took 3 piece(s) from Tyger Claw: Jacket, Pants, Boots`.
3. Find the items in your backpack under Clothing. With *Add to wardrobe* on (default) they are also available
   in the wardrobe for transmog.

Settings live in the CET overlay window **Wardrobe Scanner** and are saved to `settings.json` next to `init.lua`:

| Setting | Default | What it does |
| --- | --- | --- |
| Require scanner | on | Only works while the scanner is up. Off: works on whatever the crosshair is on. |
| Add to wardrobe | on | Also registers the pieces in the wardrobe (transmog). |
| Strip the NPC | off | Unequips the pieces from the NPC after taking them. |
| Skip pieces already owned | on | Does not give a duplicate of a piece V already has. |
| Automatic while scanning | off | Takes the wardrobe by itself once the scanner has rested on an NPC for the delay (default 2 s). Once per NPC per session. |

## How it works

NPC clothing is equipped through the game's `TransactionSystem` in attachment slots (`AttachmentSlots.Chest`,
`Torso`, `Legs`, `Feet`, `Head`, `Eyes`, ...), the same mechanism V's own clothes use. The mod:

1. Finds the NPC from the scanner's `UI_Scanner.ScannedObject` blackboard entry, falling back to the targeting system's
   look-at object.
2. Builds the list of slots to check: a fixed clothing list plus the `attachmentSlots` of the NPC's own `Character`
   TweakDB record.
3. Calls `GetItemInSlot(npc, slot)` for each slot, reads the item's TweakDB record and keeps those whose item type is
   `Clo_*`.
4. Gives V a fresh item of each record with `GiveItem`, and optionally `WardrobeSystem.StoreItemID` and
   `RemoveItemFromSlot` on the NPC.

## Limitations

- **Baked outfits.** Crowd NPCs and some story characters do not have item-based clothing; their outfit is part of the
  appearance file. For them the mod reports "nothing equipped in clothing slots". Use the dump hotkey to see what the
  game exposes for a given NPC.
- **NPC-only items.** The pieces you receive are the NPC's own item records. Many render correctly on V, some have
  no icon, odd stats, or a mesh that was never fitted to V's body and may clip or be invisible. Items like that are
  what the [Wear NPC Clothing](https://www.nexusmods.com/cyberpunk2077/mods/37938) mod fixes properly; this mod does
  not try to remap records.
- Tested only against the CET API as used by Appearance Menu Mod and Equipment-EX. If a call fails on your game
  version, turn on *Debug logging* and check the CET console.

## License

MIT, see `LICENSE`.
