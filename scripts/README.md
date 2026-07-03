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
  "units": {
    "blue":  [ {"char_id": 1, "class_id": 61, "level": 5, "exp": 0,
                "cur_hp": 24, "max_hp": 24, "x": 4, "y": 8, "move": 5,
                "str": 10, "skl": 9, "spd": 12, "def": 7, "res": 3,
                "luk": 6, "con": 7, "dead": false, "deployed": true,
                "items": [{"id": 1, "uses": 46, "slot": 0}],
                "weapon": {"id": 1, "type": 0, "might": 5, "hit": 90,
                           "weight": 5, "crit": 0}}, ... ],
    "red":   [ ... ],
    "green": [ ... ],
    "purple":[ ... ]
  }
}
```

Empty slots (character pointer == 0) are skipped. Each unit also carries the
raw `state` bitfield plus decoded `dead` / `deployed` booleans, its `items`
(id + remaining uses), and the resolved equipped `weapon` (first inventory
item flagged as a weapon), read from the ROM item table `gItemData`.

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
`0x14` max uses, `0x15` might, `0x16` hit, `0x17` weight, `0x18` crit.
(Related tables: `gCharacterData` `0x08803D64`, `gClassData` `0x08807164`.)

## Notes / tuning

- Change the `PORT` constant at the top of `fe8_state.lua` if 8888 is taken.
- These addresses are for the **US** ROM. FE7 or other regions use different
  bases; update `FACTIONS` and `ITEM_TABLE` accordingly.
- Verify the item table quickly: an Iron Sword (id `1`) should report
  `might=5, hit=90, weight=5, crit=0`.
- For an RL step loop: advance frames / send inputs via mGBA, then call
  `read_state()` to get the resulting observation.
