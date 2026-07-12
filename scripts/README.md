# FE8 live state bridge (mGBA)

Reads the complete battle state (every unit's stats and position, for all
factions, regardless of selection) from **Fire Emblem: The Sacred Stones (U)**
running in **mGBA**, and serves it to a Python client over TCP.

Unlike the FE Fates/Citra approach, GBA FE stores fully-computed current stats
directly in a fixed unit struct, so there is no on-the-fly stat formula to
reconstruct.

## Files

- `fe8_state.lua` — mGBA script. Enumerates all four faction unit arrays and
  serves a JSON snapshot on `127.0.0.1:8888`.
- `../src/fe8_bridge.py` — stdlib-only Python client (`FE8Bridge`).

## Setup

1. Open the FE8 (U) ROM in mGBA.
2. `Tools > Scripting...`, then `File > Load script...` and choose
   `scripts/fe8_state.lua`. The log should print:
   `fe8_state: listening on 127.0.0.1:8888`.
3. From Python:

   ```bash
   python src/fe8_bridge.py
   ```

   or in code:

   ```python
   from fe8_bridge import FE8Bridge
   with FE8Bridge() as bridge:
       state = bridge.read_state()   # send any line -> one JSON snapshot
   ```

The response is one newline-terminated JSON object:

```json
{
  "frame": 12345,
  "active_unit_id": 1,
  "map": {
    "w": 15, "h": 10,
    "terrain": [[1,1,5,...], ...],   // [y][x] terrain ids
    "unit":    [[0,0,1,...], ...]    // [y][x] occupant unit index (0 = empty)
  },
  "terrain_heal": [0,0,...],          // heal amount per terrain id (65 entries)
  "classes": [
    {"class_id": 61, "move_cost": [1,1,2,...], "def": [0,...],
     "avo": [0,...], "res": [0,...]}  // per terrain id (65 entries each)
  ],
  "units": {
    "blue":  [ {"char_id": 1, "class_id": 61, "level": 5, "exp": 0,
                "cur_hp": 24, "max_hp": 24, "x": 4, "y": 8,
                "move": 5, "mov_bonus": 0, "con": 7, "con_bonus": 0,
                "str": 10, "skl": 9, "spd": 12, "def": 7, "res": 3,
                "luk": 6, "dead": false, "deployed": true,
                "items": [{"id": 1, "uses": 46, "slot": 0, "type": 0,
                           "might": 5, "hit": 90, "weight": 5, "crit": 0,
                           "range_min": 1, "range_max": 1, "is_weapon": true}],
                "weapon": { ...same shape as an item... }}, ... ],
    "red":   [ ... ], "green": [ ... ], "purple": [ ... ]
  }
}
```

Empty unit slots (character pointer == 0) are skipped. Notes:

- `move` and `con` are **totals** (class base + the per-unit bonus); the raw
  bonuses are also exposed as `mov_bonus` / `con_bonus`. All other stats
  (`str/skl/spd/def/res/luk`, HP) are full current values.
- Every inventory item carries its ROM `gItemData` fields plus decoded
  `range_min`/`range_max` (`range_max == 0` = dynamic magic range 1..Mag/2)
  and `is_weapon`.
- `classes` is deduplicated by `class_id` — resolve a unit's terrain tables by
  looking up its `class_id`.
- `map`, `classes`, and `terrain_heal` are `null`/empty off-map (menus, world
  map).

## Map / terrain observation & action masking

`src/fe8_bridge.py` turns the raw signals into the autoregressive action
structure (choose destination -> action -> target+weapon):

- `reachable_tiles(state, unit)` — weighted BFS (Dijkstra) over the unit's
  class movement costs; enemies block pathing, allies may be passed but not
  stopped on. Returns `{(x,y): move_spent}`.
- `attackable_targets(state, unit, dest)` — `(target, weapon)` pairs hittable
  from a destination tile, across all inventory weapons' ranges.
- `action_mask(state, unit)` — `{"move": {...}, "attacks": {dest: [...]}}`;
  index `attacks` by the chosen destination for stage-2 target selection.
- `tile_defense_bonus` / `tile_avoid_bonus` / `tile_heal_amount` — terrain
  effects for observation channels or combat resolution.
- `is_hostile`, `living_units`, `occupancy`, `build_class_tables` — helpers.

Faction sides for hostility/blocking: `{blue, green}` vs `{red, purple}`.

## Derived combat stats

`src/fe8_bridge.py` exposes `combat_stats(unit)`, which computes the standard
FE8 formulas from the unit's stats and equipped weapon:

- **Attack** = Str/Mag + weapon Might (Str and Mag share one stat in GBA FE).
- **Attack Speed (AS)** = Spd − max(0, Weight − Con), clamped to >= 0.
- **Hit** = weapon Hit + Skl×2 + Luck/2.
- **Avoid** = AS×2 + Luck.
- **Crit** = weapon Crit + Skl/2.
- **Crit Avoid** = Luck.

Situational modifiers (weapon triangle, supports, terrain, and class/S-rank
crit bonuses) are omitted since they depend on the opponent and tile. Returns
`None` when no weapon is equipped (e.g. staff-only or itemless units).

## Extraction tool (`src/extract.py`)

Dumps the raw data needed to build a data-driven FE8 simulator. Two subcommands
with two different sources.

### `tables` — static ROM parse (no emulator)

Reads a FE8 (U) `.gba` file directly and writes `classes.json`, `items.json`,
and `characters.json`:

```bash
python src/extract.py tables --rom data/fe8.gba --out outputs/tables
```

Each output file records the `address`, `stride`, and `count` it used plus an
`entries` array. Every encoded id is annotated with its decomp name (from
`src/fe8_names.py`):

- **`classes.json`** — bases, maxes, growths, `promotion`, `attributes`,
  `weapon_ranks`, and each class's **resolved per-terrain-type tables**
  (`move_cost`, `terrain_defense`, `terrain_avoid`, `terrain_resistance`;
  65 entries, `-1` = impassable/none).
- **`items.json`** — weapon type (+name), decoded `attribute_flags`, `might`,
  `hit`, `weight`, `crit`, `max_uses`, `range_min`/`range_max`, `is_weapon` /
  `is_staff` / `is_magic`, cost, and `required_rank` (+`required_rank_letter`).
- **`characters.json`** — default class (+name), affinity (+name), base level,
  bases, growths, `attributes`, and `weapon_ranks`.

**Id alignment.** `gClassData`/`gCharacterData` use the FEGBA `table[id - 1]`
convention (there is no stored entry for id 0), so each entry's real id is read
from its own `number` field (offset `0x04`) rather than the loop index — this is
what previously caused names to be off by one. `gItemData` is indexed directly
(entry 0 = `ITEM_NONE`).

**Field meanings.**
- `promotion` (class, offset `0x05`): FE7-legacy *single* promotion target. Only
  reported (`promotion_name`) for unpromoted classes, and even then it lists only
  ONE branch — FE8's real branching promotions live in a separate data table.
  Promoted classes store a stale back-reference, so `promotion_name` is `null`.
- `attributes` (class & character, offset `0x28`): a 32-bit `CA_*` flag bitfield.
  A unit's effective attributes are its class flags OR'd with its character
  flags. Decoded into `attribute_flags` (e.g. `PROMOTED`, `MOUNTED`, `FLYER`
  components, `BOSS`, `FEMALE`, `LORD`, `STEAL`, `CANTO`, lethality/lock flags).
- `weapon_ranks`: usable weapon types keyed by weapon type, each with the raw
  weapon-exp (`wexp`) and its letter `rank`. A unit's real usable weapons combine
  its class ranks with its character rank bonuses. An item is wieldable when the
  unit's wexp for that weapon type >= the item's `required_rank`
  (both use the same `E/D/C/B/A/S` = `1/31/71/121/181/251` threshold encoding).

**Addresses default to vanilla FE8 US.** For another region or a ROM hack,
override per table with `--classes-addr ADDR:STRIDE:COUNT` (likewise
`--items-addr`, `--characters-addr`), e.g. `--classes-addr 0x8807164:0x54:128`.
Verify the printed `address/stride/count` produces sane names/stats; the
class table sits immediately before `gItemData`, so if your `count` overruns it
the last entry(ies) will be garbage — trim the count if so.

### `chapter` — live snapshot via the bridge

With the ROM running in mGBA (bridge loaded) and stopped **at turn 1 of a
chapter before moving**, capture that chapter's terrain grid + initial unit
placements:

```bash
python src/extract.py chapter --name ch01_border_mulan --objective Rout \
    --out outputs/chapters
```

Output `outputs/chapters/<name>.json` contains `map` (`w`, `h`, and the
`[y][x]` terrain-type grid), and `units` (per-faction placements with character
/ class / item names, level, HP, position). `--objective` is free text you
supply — FE8 win conditions live in each chapter's **event scripts**, not a
single RAM field, so there is no clean enum to read. Repeat once per chapter to
build a corpus under `outputs/chapters/`.

## FE8 (U) reference

Unit arrays in EWRAM (from the `fireemblem8u` decomp symbol map), struct stride
`0x48`:

| Faction | Symbol            | Address      | Slots |
| ------- | ----------------- | ------------ | ----- |
| blue    | `gUnitArrayBlue`  | `0x0202BE4C` | 62    |
| red     | `gUnitArrayRed`   | `0x0202CFBC` | 50    |
| green   | `gUnitArrayGreen` | `0x0202DDCC` | 20    |
| purple  | `gUnitArrayPurple`| `0x0202E36C` | 5     |

Key `struct Unit` offsets: `0x00` character ptr, `0x04` class ptr, `0x08`
level, `0x09` exp, `0x0B` index, `0x0C` state (u32), `0x10/0x11` x/y,
`0x12/0x13` max/cur HP, `0x14` str, `0x15` skl, `0x16` spd, `0x17` def,
`0x18` res, `0x19` luck, `0x1A` con, `0x1D` movement.

State bits: `0x04` = dead, `0x08` = not deployed.

Inventory: `0x1E` holds 5 x `u16` (low byte = item id, high byte = uses).

Item table `gItemData` = `0x08809B10`, stride `0x24`. `struct ItemData`
offsets: `0x07` weapon type, `0x08` attributes (u32; bit 0 = `IA_WEAPON`),
`0x14` max uses, `0x15` might, `0x16` hit, `0x17` weight, `0x18` crit,
`0x1C` required weapon-exp (rank), `0x20` wexp gained per use.
`baseRanks[8]` live at `0x14` (`CharacterData`) and `0x2C` (`ClassData`).
(Related tables: `gCharacterData` `0x08803D64`, `gClassData` `0x08807164`.)

## Notes / tuning

- Change the `PORT` constant at the top of `fe8_state.lua` if 8888 is taken.
- These addresses are for the **US** ROM. FE7 or other regions use different
  bases; update `FACTIONS` and `ITEM_TABLE` accordingly.
- Verify the item table quickly: an Iron Sword (id `1`) should report
  `might=5, hit=90, weight=5, crit=0`.
- For an RL step loop: advance frames / send inputs via mGBA, then call
  `read_state()` to get the resulting observation.
