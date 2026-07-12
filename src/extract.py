#!/usr/bin/env python3
"""Extraction tool for building a data-driven FE8 simulator.

Two data sources, two subcommands:

  * ``tables`` -- parses static data tables straight out of a FE8 (U) ROM file
    (no emulator needed): ClassData (with each class's resolved terrain
    movement/def/avoid/res tables), ItemData, and CharacterData. Encoded byte
    ids are annotated with decomp names via ``fe8_names``.

  * ``chapter`` -- snapshots the *currently loaded* chapter over the live
    ``fe8_state.lua`` bridge: terrain-type grid, dimensions, and initial unit
    placements for every faction. Run it once per chapter, at turn 1 before
    moving, to build a per-chapter corpus.

ROM addresses default to vanilla FE8 US ([BE8E]); override with --*-addr flags
for other regions or ROM hacks. See scripts/README.md for the workflow.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any, Callable, Dict, List, Optional

import fe8_names as names

ROM_BASE = 0x08000000
TERRAIN_COUNT = 0x41  # 65 terrain types

# (address, stride, count) for vanilla FE8 (U). ClassData and CharacterData use
# the FEGBA `table[id - 1]` convention (entry j holds data for id j+1; there is
# no stored entry for id 0), so their counts are one less than the id maxima
# and each entry's real id is read from its `number` field. ItemData is indexed
# directly (`table[id]`, entry 0 = ITEM_NONE). Counts match the exact table gaps.
DEFAULT_CHARACTERS = (0x08803D64, 0x34, 0x100)
DEFAULT_CLASSES = (0x08807164, 0x54, 0x7F)
DEFAULT_ITEMS = (0x08809B10, 0x24, 0xCE)

ITYPE_NAMES = {
    0: "SWORD", 1: "LANCE", 2: "AXE", 3: "BOW", 4: "STAFF", 5: "ANIMA",
    6: "LIGHT", 7: "DARK", 8: "BALLISTA", 9: "ITEM", 10: "DRAGON",
    11: "MONSTER", 12: "DANCE",
}

# ItemData.attributes bits (include/bmitem.h)
IA_FLAGS = [
    (1 << 0, "WEAPON"), (1 << 1, "MAGIC"), (1 << 2, "STAFF"),
    (1 << 3, "UNBREAKABLE"), (1 << 4, "UNSELLABLE"), (1 << 5, "BRAVE"),
    (1 << 6, "MAGICDAMAGE"), (1 << 7, "UNCOUNTERABLE"), (1 << 8, "REVERTTRIANGLE"),
    (1 << 14, "NEGATE_FLYING"), (1 << 15, "NEGATE_CRIT"), (1 << 16, "UNUSABLE"),
    (1 << 17, "NEGATE_DEFENSE"),
]

# Character/Class attributes (CA_* bits, include/bmunit.h). Shared flag set:
# a unit's effective attributes are its class's OR'd with its character's.
CA_FLAGS = [
    (1 << 0, "MOUNTEDAID"), (1 << 1, "CANTO"), (1 << 2, "STEAL"), (1 << 3, "THIEF"),
    (1 << 4, "DANCE"), (1 << 5, "PLAY"), (1 << 6, "CRITBONUS"), (1 << 7, "BALLISTAE"),
    (1 << 8, "PROMOTED"), (1 << 9, "SUPPLY"), (1 << 10, "MOUNTED"), (1 << 11, "WYVERN"),
    (1 << 12, "PEGASUS"), (1 << 13, "LORD"), (1 << 14, "FEMALE"), (1 << 15, "BOSS"),
    (1 << 16, "LOCK_1"), (1 << 17, "LOCK_2"), (1 << 18, "LOCK_3"), (1 << 19, "MAXLEVEL10"),
    (1 << 20, "UNSELECTABLE"), (1 << 21, "TRIANGLEATTACK_PEGASI"),
    (1 << 22, "TRIANGLEATTACK_ARMORS"), (1 << 24, "NEGATE_LETHALITY"), (1 << 25, "ASSASSIN"),
    (1 << 26, "MAGICSEAL"), (1 << 27, "SUMMON"), (1 << 28, "LOCK_4"), (1 << 29, "LOCK_5"),
    (1 << 30, "LOCK_6"), (1 << 31, "LOCK_7"),
]

# baseRanks[8] are indexed by weapon type (ITYPE order).
WEAPON_TYPE_ORDER = ["sword", "lance", "axe", "bow", "staff", "anima", "light", "dark"]

# Weapon-exp point thresholds (WPN_EXP_*) map an accumulated wexp value to the
# usable weapon rank. baseRanks and ItemData.weaponRank both use this encoding
# (descending so the first match wins).
WEXP_THRESHOLDS = [(251, "S"), (181, "A"), (121, "B"), (71, "C"), (31, "D"), (1, "E")]

AFFINITY_NAMES = {
    1: "FIRE", 2: "THUNDER", 3: "WIND", 4: "ICE", 5: "DARK", 6: "LIGHT", 7: "ANIMA",
}


# ---------------------------------------------------------------------------
# ROM reader
# ---------------------------------------------------------------------------
class Rom:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def _off(self, addr: int) -> int:
        off = addr - ROM_BASE
        if off < 0 or off >= len(self.data):
            raise IndexError(f"address {addr:#010x} outside ROM (size {len(self.data)})")
        return off

    def u8(self, addr: int) -> int:
        return self.data[self._off(addr)]

    def s8(self, addr: int) -> int:
        v = self.u8(addr)
        return v - 0x100 if v >= 0x80 else v

    def u16(self, addr: int) -> int:
        o = self._off(addr)
        return self.data[o] | (self.data[o + 1] << 8)

    def u32(self, addr: int) -> int:
        o = self._off(addr)
        return int.from_bytes(self.data[o:o + 4], "little")

    def in_rom(self, addr: int) -> bool:
        return ROM_BASE <= addr < ROM_BASE + len(self.data)

    def s8_table(self, ptr_addr: int, n: int = TERRAIN_COUNT) -> Optional[List[int]]:
        """Follow a pointer stored at ptr_addr and read n signed bytes."""
        ptr = self.u32(ptr_addr)
        if not self.in_rom(ptr):
            return None
        return [self.s8(ptr + i) for i in range(n)]


def _decode_flags(value: int) -> List[str]:
    return [name for bit, name in IA_FLAGS if value & bit]


def _decode_ca(value: int) -> List[str]:
    return [name for bit, name in CA_FLAGS if value & bit]


def _wexp_letter(wexp: int) -> str:
    for threshold, letter in WEXP_THRESHOLDS:
        if wexp >= threshold:
            return letter
    return "-"


def _base_ranks(rom: "Rom", addr: int) -> Dict[str, Dict[str, int]]:
    """Read baseRanks[8] as {weapon_type: {wexp, rank}} keyed by weapon type."""
    out: Dict[str, Dict[str, int]] = {}
    for i, wtype in enumerate(WEAPON_TYPE_ORDER):
        wexp = rom.u8(addr + i)
        if wexp:
            out[wtype] = {"wexp": wexp, "rank": _wexp_letter(wexp)}
    return out


def _decode_range(encoded: int) -> Dict[str, int]:
    # min = high nibble, max = low nibble; low nibble 0 => dynamic magic range.
    return {"range_min": (encoded >> 4) & 0xF, "range_max": encoded & 0xF}


# ---------------------------------------------------------------------------
# Table parsers
# ---------------------------------------------------------------------------
def parse_classes(rom: Rom, addr: int, stride: int, count: int) -> List[Dict[str, Any]]:
    out = []
    for i in range(count):
        b = addr + i * stride
        cid = rom.u8(b + 0x04)  # authoritative id (table uses the id-1 convention)
        promo = rom.u8(b + 0x05)
        attrs = rom.u32(b + 0x28)
        is_promoted = bool(attrs & (1 << 8))  # CA_PROMOTED
        # `promotion` is the FE7-legacy single promotion target. It is only
        # meaningful for unpromoted classes (and even then only lists ONE of the
        # possible branches -- FE8's real promotion branching lives in a
        # separate data table). Promoted classes store a stale back-reference,
        # so we surface a target only for unpromoted classes.
        promo_valid = promo if (promo and not is_promoted) else 0
        out.append({
            "id": cid,
            "name": names.class_name(cid),
            "name_text_id": rom.u16(b + 0x00),
            "promotion": promo,
            "promotion_name": names.class_name(promo_valid) if promo_valid else None,
            "base_mov": rom.s8(b + 0x12),
            "base_con": rom.s8(b + 0x11),
            "bases": {
                "hp": rom.s8(b + 0x0B), "pow": rom.s8(b + 0x0C), "skl": rom.s8(b + 0x0D),
                "spd": rom.s8(b + 0x0E), "def": rom.s8(b + 0x0F), "res": rom.s8(b + 0x10),
            },
            "maxes": {
                "hp": rom.s8(b + 0x13), "pow": rom.s8(b + 0x14), "skl": rom.s8(b + 0x15),
                "spd": rom.s8(b + 0x16), "def": rom.s8(b + 0x17), "res": rom.s8(b + 0x18),
                "con": rom.s8(b + 0x19),
            },
            "growths": {
                "hp": rom.s8(b + 0x1B), "pow": rom.s8(b + 0x1C), "skl": rom.s8(b + 0x1D),
                "spd": rom.s8(b + 0x1E), "def": rom.s8(b + 0x1F), "res": rom.s8(b + 0x20),
                "lck": rom.s8(b + 0x21),
            },
            "attributes": attrs,
            "attribute_flags": _decode_ca(attrs),
            # Usable weapon ranks for this class (baseRanks[8] at 0x2C).
            "weapon_ranks": _base_ranks(rom, b + 0x2C),
            # Resolved per-terrain-type tables (65 entries; -1 = impassable/none)
            "move_cost": rom.s8_table(b + 0x38),
            "terrain_avoid": rom.s8_table(b + 0x44),
            "terrain_defense": rom.s8_table(b + 0x48),
            "terrain_resistance": rom.s8_table(b + 0x4C),
        })
    return out


def parse_items(rom: Rom, addr: int, stride: int, count: int) -> List[Dict[str, Any]]:
    out = []
    for i in range(count):
        b = addr + i * stride
        attrs = rom.u32(b + 0x08)
        wtype = rom.u8(b + 0x07)
        entry = {
            "id": i,
            "name": names.item_name(i),
            "name_text_id": rom.u16(b + 0x00),
            "desc_text_id": rom.u16(b + 0x02),
            "weapon_type": wtype,
            "weapon_type_name": ITYPE_NAMES.get(wtype, str(wtype)),
            "attributes": attrs,
            "attribute_flags": _decode_flags(attrs),
            "is_weapon": bool(attrs & (1 << 0)),
            "is_staff": bool(attrs & (1 << 2)),
            "is_magic": bool(attrs & (1 << 1)),
            "max_uses": rom.u8(b + 0x14),
            "might": rom.u8(b + 0x15),
            "hit": rom.u8(b + 0x16),
            "weight": rom.u8(b + 0x17),
            "crit": rom.u8(b + 0x18),
            "cost_per_use": rom.u16(b + 0x1A),
            # weapon-exp points required to wield (same encoding as baseRanks);
            # compare against a unit's accumulated wexp for that weapon type.
            "required_rank": rom.u8(b + 0x1C),
            "required_rank_letter": _wexp_letter(rom.u8(b + 0x1C)),
            "weapon_exp_gain": rom.u8(b + 0x20),
        }
        entry.update(_decode_range(rom.u8(b + 0x19)))
        out.append(entry)
    return out


def parse_characters(rom: Rom, addr: int, stride: int, count: int) -> List[Dict[str, Any]]:
    out = []
    for i in range(count):
        b = addr + i * stride
        chid = rom.u8(b + 0x04)  # authoritative id (table uses the id-1 convention)
        default_class = rom.u8(b + 0x05)
        affinity = rom.u8(b + 0x09)
        out.append({
            "id": chid,
            "name": names.character_name(chid),
            "name_text_id": rom.u16(b + 0x00),
            "default_class": default_class,
            "default_class_name": names.class_name(default_class),
            "affinity": affinity,
            "affinity_name": AFFINITY_NAMES.get(affinity),
            "base_level": rom.s8(b + 0x0B),
            "bases": {
                "hp": rom.s8(b + 0x0C), "pow": rom.s8(b + 0x0D), "skl": rom.s8(b + 0x0E),
                "spd": rom.s8(b + 0x0F), "def": rom.s8(b + 0x10), "res": rom.s8(b + 0x11),
                "lck": rom.s8(b + 0x12), "con": rom.s8(b + 0x13),
            },
            "growths": {
                "hp": rom.u8(b + 0x1C), "pow": rom.u8(b + 0x1D), "skl": rom.u8(b + 0x1E),
                "spd": rom.u8(b + 0x1F), "def": rom.u8(b + 0x20), "res": rom.u8(b + 0x21),
                "lck": rom.u8(b + 0x22),
            },
            "attributes": rom.u32(b + 0x28),
            "attribute_flags": _decode_ca(rom.u32(b + 0x28)),
            # Personal weapon-rank bonuses (baseRanks[8] at 0x14), added on top
            # of the unit's class ranks to determine usable weapons.
            "weapon_ranks": _base_ranks(rom, b + 0x14),
        })
    return out


# ---------------------------------------------------------------------------
# Subcommand: tables
# ---------------------------------------------------------------------------
def _write_json(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
    print(f"wrote {path}")


def cmd_tables(args: argparse.Namespace) -> None:
    with open(args.rom, "rb") as f:
        rom = Rom(f.read())

    jobs: List[tuple] = [
        ("classes", parse_classes, args.classes_addr),
        ("items", parse_items, args.items_addr),
        ("characters", parse_characters, args.characters_addr),
    ]
    for name, parser, (addr, stride, count) in jobs:
        entries = parser(rom, addr, stride, count)
        _write_json(os.path.join(args.out, f"{name}.json"), {
            "source": os.path.basename(args.rom),
            "address": f"{addr:#010x}",
            "stride": stride,
            "count": count,
            "entries": entries,
        })


# ---------------------------------------------------------------------------
# Subcommand: chapter (live bridge)
# ---------------------------------------------------------------------------
def _annotate_unit(u: Dict[str, Any]) -> Dict[str, Any]:
    items = [
        {"id": it["id"], "name": names.item_name(it["id"]), "uses": it.get("uses")}
        for it in u.get("items", [])
    ]
    return {
        "faction": u.get("faction"),
        "char_id": u["char_id"],
        "char": names.character_name(u["char_id"]),
        "class_id": u["class_id"],
        "class": names.class_name(u["class_id"]),
        "x": u["x"], "y": u["y"],
        "level": u["level"], "cur_hp": u["cur_hp"], "max_hp": u["max_hp"],
        "items": items,
    }


def cmd_chapter(args: argparse.Namespace) -> None:
    from fe8_bridge import FE8Bridge

    with FE8Bridge(host=args.host, port=args.port) as bridge:
        state = bridge.read_state()

    m = state.get("map")
    if not m:
        dbg = state.get("map_debug", {})
        raise SystemExit(
            "No chapter map is loaded (map is null). Be in a chapter at turn 1.\n"
            f"map_debug: {dbg}"
        )

    units = []
    for faction, group in state.get("units", {}).items():
        for u in group:
            if u.get("dead") or not u.get("deployed", True):
                continue
            u.setdefault("faction", faction)
            units.append(_annotate_unit(u))

    chapter = {
        "name": args.name,
        "objective": args.objective,  # FE8 objectives are event-driven; user-supplied
        "source_frame": state.get("frame"),
        "map": {"w": m["w"], "h": m["h"], "terrain": m["terrain"]},
        "units": units,
    }
    _write_json(os.path.join(args.out, f"{args.name}.json"), chapter)
    print(f"  {m['w']}x{m['h']} map, {len(units)} unit placement(s)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _addr_triple(s: str) -> tuple:
    parts = s.split(":")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("expected addr:stride:count (e.g. 0x8807164:0x54:128)")
    return (int(parts[0], 0), int(parts[1], 0), int(parts[2], 0))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("tables", help="parse static data tables from a ROM file")
    t.add_argument("--rom", required=True, help="path to the FE8 (U) .gba ROM")
    t.add_argument("--out", default="outputs/tables", help="output directory")
    t.add_argument("--classes-addr", type=_addr_triple, default=DEFAULT_CLASSES,
                   metavar="ADDR:STRIDE:COUNT")
    t.add_argument("--items-addr", type=_addr_triple, default=DEFAULT_ITEMS,
                   metavar="ADDR:STRIDE:COUNT")
    t.add_argument("--characters-addr", type=_addr_triple, default=DEFAULT_CHARACTERS,
                   metavar="ADDR:STRIDE:COUNT")
    t.set_defaults(func=cmd_tables)

    c = sub.add_parser("chapter", help="snapshot the currently loaded chapter via the bridge")
    c.add_argument("--name", required=True, help="chapter label, used as the output filename")
    c.add_argument("--objective", default=None, help="objective text, e.g. 'Seize' (optional)")
    c.add_argument("--out", default="outputs/chapters", help="output directory")
    c.add_argument("--host", default="127.0.0.1")
    c.add_argument("--port", type=int, default=8888)
    c.set_defaults(func=cmd_chapter)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
