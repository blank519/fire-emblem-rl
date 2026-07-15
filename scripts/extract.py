#!/usr/bin/env python3
"""Extraction tool for building a data-driven FE8 simulator.

Three subcommands across two data sources (ROM file vs live emulator):

  * ``tables`` -- parses static data tables straight out of a FE8 (U) ROM file
    (no emulator needed): ClassData (with each class's resolved terrain
    movement/def/avoid/res tables), ItemData, and CharacterData. Encoded byte
    ids are annotated with decomp names via ``fe8_names``.

  * ``all-chapters`` -- statically reconstructs every chapter's terrain grid
    (LZ77-decompressed map layer + tileset terrain lookup) and initial unit
    placements (UnitDefinition arrays loaded by the opening event), writing one
    JSON per chapter. No emulator needed.

  * ``chapter`` -- snapshots the *currently loaded* chapter over the live
    ``fe8_state.lua`` bridge: terrain-type grid, dimensions, and initial unit
    placements for every faction. Run it once per chapter, at turn 1 before
    moving, to build a per-chapter corpus.

ROM addresses default to vanilla FE8 US ([BE8E]); override with --*-addr flags
for other regions or ROM hacks. See scripts/README.md for the workflow.
"""

from __future__ import annotations

import argparse
import glob
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

# Chapter data (vanilla FE8 US). `gChapterDataTable` is an array of
# `struct ROMChapterData` (stride 0x94); `gChapterDataAssetTable` is a flat
# array of asset pointers that the chapter's ChapterMap ids index into.
#
# Count is 0x3F, not 44: the story block (0x00-0x23), Tower of Valni (0x24-0x2B),
# Lagdou Ruins (0x2E-0x37), and the two "in-transit" Chapter 11s are all real
# maps. Both Chapter 11s (Eirika "Creeping Darkness" @0x3D, Ephraim "Phantom
# Ship" @0x3E) sit at the END of the table -- they have no world-map node and
# occur while traveling, so the ROM stores them out of sequence. Their ROM
# `internalName` strings are "E10x"/"I10x". Reading only 44 entries misses them.
DEFAULT_CHAPTERS = (0x088B0890, 0x94, 0x3F)
CHAPTER_ASSET_TABLE = 0x088B363C

# Canonical chapter identity per gChapterDataTable index, from the fireemblem8u
# `enum chapter_idx`. This is authoritative and fixes a subtle trap: the ROM's
# `internalName` strings are naively sequential ("E09,E10,E11,E12,...") and DO
# NOT skip Chapter 11, so from index 0x0C on every string is off by one vs the
# real chapter (the entry the ROM labels "E11" is actually Ch12; "E20"/"E20B"
# are actually Ch21/Ch21x, the two-part finale). The true Chapter 11s live at
# 0x3D/0x3E. Indices absent here (0x2C, 0x2D, 0x3A, gaps) are unused slots.
CANONICAL_CHAPTERS = {
    0x00: ("Prologue", "Prologue: The Fall of Renais"),
    0x01: ("Ch1", "Ch1: Escape!"),
    0x02: ("Ch2", "Ch2: The Protected"),
    0x03: ("Ch3", "Ch3: The Bandits of Borgo"),
    0x04: ("Ch4", "Ch4: Ancient Horrors"),
    0x05: ("Ch5x", "Ch5x: Unbroken Heart"),
    0x06: ("Ch5", "Ch5: The Empire's Reach"),
    0x07: ("Ch6", "Ch6: Victims of War"),
    0x08: ("Ch7", "Ch7: Waterside Renvall"),
    0x09: ("Ch8", "Ch8: It's a Trap!"),
    0x0A: ("E9", "Eirika Ch9: Distant Blade"),
    0x0B: ("E10", "Eirika Ch10: Revolt at Carcino"),
    0x0C: ("E12", "Eirika Ch12: Village of Silence"),
    0x0D: ("E13", "Eirika Ch13: Hamill Canyon"),
    0x0E: ("E14", "Eirika Ch14: Queen of White Dunes"),
    0x0F: ("E15", "Eirika Ch15: Scorched Sand"),
    0x10: ("E16", "Eirika Ch16: Ruled by Madness"),
    0x11: ("E17", "Eirika Ch17: River of Regrets"),
    0x12: ("E18", "Eirika Ch18: Two Faces of Evil"),
    0x13: ("E19", "Eirika Ch19: Last Hope"),
    0x14: ("E20", "Eirika Ch20: Darkling Woods"),
    0x15: ("E21", "Eirika Ch21: Sacred Stone (Finale pt1)"),
    0x16: ("E21x", "Eirika Ch21x: Sacred Stone (Finale pt2)"),
    0x17: ("I9", "Ephraim Ch9: Fort Rigwald"),
    0x18: ("I10", "Ephraim Ch10: Turning Traitor"),
    0x19: ("I12", "Ephraim Ch12: Landing at Taizel"),
    0x1A: ("I13", "Ephraim Ch13: Fluorspar's Oath"),
    0x1B: ("I14", "Ephraim Ch14: Father and Son"),
    0x1C: ("I15", "Ephraim Ch15: Scorched Sand"),
    0x1D: ("I16", "Ephraim Ch16: Ruled by Madness"),
    0x1E: ("I17", "Ephraim Ch17: River of Regrets"),
    0x1F: ("I18", "Ephraim Ch18: Two Faces of Evil"),
    0x20: ("I19", "Ephraim Ch19: Last Hope"),
    0x21: ("I20", "Ephraim Ch20: Darkling Woods"),
    0x22: ("I21", "Ephraim Ch21: Sacred Stone (Finale pt1)"),
    0x23: ("I21x", "Ephraim Ch21x: Sacred Stone (Finale pt2)"),
    0x24: ("T1", "Tower of Valni 1"),
    0x25: ("T2", "Tower of Valni 2"),
    0x26: ("T3", "Tower of Valni 3"),
    0x27: ("T4", "Tower of Valni 4"),
    0x28: ("T5", "Tower of Valni 5"),
    0x29: ("T6", "Tower of Valni 6"),
    0x2A: ("T7", "Tower of Valni 7"),
    0x2B: ("T8", "Tower of Valni 8"),
    0x2E: ("R1", "Lagdou Ruins 1"),
    0x2F: ("R2", "Lagdou Ruins 2"),
    0x30: ("R3", "Lagdou Ruins 3"),
    0x31: ("R4", "Lagdou Ruins 4"),
    0x32: ("R5", "Lagdou Ruins 5"),
    0x33: ("R6", "Lagdou Ruins 6"),
    0x34: ("R7", "Lagdou Ruins 7"),
    0x35: ("R8", "Lagdou Ruins 8"),
    0x36: ("R9", "Lagdou Ruins 9"),
    0x37: ("R10", "Lagdou Ruins 10"),
    0x38: ("CastleFrelia", "Castle Frelia"),
    0x39: ("MalkaenCoast", "Malkaen Coast"),
    0x3D: ("E11", "Eirika Ch11: Creeping Darkness"),
    0x3E: ("I11", "Ephraim Ch11: Phantom Ship"),
}

# struct ROMChapterData offsets.
CH_INTERNAL_NAME = 0x00   # const char*
CH_TILE_CONFIG_ID = 0x07  # -> asset table (compressed tileset config)
CH_MAIN_LAYER_ID = 0x08   # -> asset table (compressed map tile layer)
CH_EVENT_DATA_ID = 0x74   # -> asset table (ChapterEventGroup*)
CH_TITLE_TEXT_ID = 0x70   # u16 text id

# struct ChapterEventGroup: script pointers; beginningSceneEvents loads the
# chapter's initial units (player/enemy/ally) via LOU commands.
CEG_PLAYER_UNITS = 0x28
CEG_BEGINNING_SCENE = 0x48
CEG_FIELD_COUNT = 20      # number of pointer fields (used to bound scripts)

# Event-script LOU (load-units) command: u16 code (0x2C40/0x2C41), u16 arg,
# then a u32 UnitDefinition* pointer. Command length in bytes = 2 * (LSB >> 4).
LOU_CODES = (0x2C40, 0x2C41)
UNITDEF_STRIDE = 0x14     # struct UnitDefinition size

FACTION_NAMES = {0: "blue", 1: "green", 2: "red", 3: "purple"}

ITYPE_NAMES = {
    0: "SWORD", 1: "LANCE", 2: "AXE", 3: "BOW", 4: "STAFF", 5: "ANIMA",
    6: "LIGHT", 7: "DARK", 8: "BALLISTA", 9: "ITEM", 10: "DRAGON",
    11: "MONSTER", 12: "DANCE",
}

# ItemData.attributes bits (include/bmitem.h). The LOCK_n bits mark a weapon as
# restricted: only a unit whose class/character carries the matching CA_LOCK_n
# attribute may wield it (e.g. the Wo Dao is IA_LOCK_2, usable by myrmidon-line
# classes; monster weapons are IA_LOCK_3).
IA_FLAGS = [
    (1 << 0, "WEAPON"), (1 << 1, "MAGIC"), (1 << 2, "STAFF"),
    (1 << 3, "UNBREAKABLE"), (1 << 4, "UNSELLABLE"), (1 << 5, "BRAVE"),
    (1 << 6, "MAGICDAMAGE"), (1 << 7, "UNCOUNTERABLE"), (1 << 8, "REVERTTRIANGLE"),
    (1 << 9, "HAMMERNE"), (1 << 10, "LOCK_3"), (1 << 11, "LOCK_1"), (1 << 12, "LOCK_2"),
    (1 << 13, "LOCK_0"), (1 << 14, "NEGATE_FLYING"), (1 << 15, "NEGATE_CRIT"),
    (1 << 16, "UNUSABLE"), (1 << 17, "NEGATE_DEFENSE"), (1 << 18, "LOCK_4"),
    (1 << 19, "LOCK_5"), (1 << 20, "LOCK_6"), (1 << 21, "LOCK_7"),
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

# Class combat skills. Most FE8 class abilities are data-driven via CA_* bits
# (mapped here to readable skill names); the rest (Great Shield, Pierce) are
# hardcoded by class id in the battle code with no ROM-table representation, so
# they are supplied from this curated map keyed by class name.
CA_SKILL_NAMES = [
    (1 << 6, "Critical +15"),   # CA_CRITBONUS (swordmaster, berserker)
    (1 << 25, "Silencer"),      # CA_ASSASSIN
    (1 << 27, "Summon"),        # CA_SUMMON
]
CLASS_SKILLS = {
    "GENERAL": ["Great Shield"], "GENERAL_F": ["Great Shield"],
    "WYVERN_KNIGHT": ["Pierce"], "WYVERN_KNIGHT_F": ["Pierce"],
}


def _class_skills(class_name: str, attributes: int) -> List[str]:
    skills = list(CLASS_SKILLS.get(class_name, []))
    for bit, name in CA_SKILL_NAMES:
        if attributes & bit and name not in skills:
            skills.append(name)
    return skills


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

    def lz77(self, addr: int) -> Optional[bytes]:
        """Decompress GBA BIOS LZ77 (LZ10) data at a ROM address.

        Header: byte 0 = 0x10, bytes 1-3 = decompressed size (LE). Then flag
        bytes (MSB first): a 1-bit means a (length, disp) back-reference pair.
        """
        if not self.in_rom(addr):
            return None
        o = self._off(addr)
        if self.data[o] != 0x10:
            return None
        size = self.u32(addr) >> 8
        out = bytearray()
        i = o + 4
        n = len(self.data)
        while len(out) < size and i < n:
            flags = self.data[i]
            i += 1
            for bit in range(8):
                if len(out) >= size:
                    break
                if flags & (0x80 >> bit):
                    if i + 1 >= n:
                        return bytes(out)
                    b0, b1 = self.data[i], self.data[i + 1]
                    i += 2
                    count = (b0 >> 4) + 3
                    disp = (((b0 & 0xF) << 8) | b1) + 1
                    if disp > len(out):
                        return bytes(out)
                    for _ in range(count):
                        out.append(out[-disp])
                else:
                    if i >= n:
                        break
                    out.append(self.data[i])
                    i += 1
        return bytes(out)


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
        attrs = rom.u32(b + 0x28)
        cname = names.class_name(cid)
        # NOTE: ClassData offset 0x05 is an FE7-legacy single-promotion pointer.
        # It is unreliable in FE8 (lists only one branch, and holds garbage for
        # promoted/non-promoting classes), so it is intentionally NOT emitted.
        # FE8's real branching promotions live in the separate `gPromoJidLut`
        # table (u8[class][2]); extract that if promotion actions are needed.
        out.append({
            "id": cid,
            "name": cname,
            "name_text_id": rom.u16(b + 0x00),
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
            "skills": _class_skills(cname, attrs),
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
# Subcommand: all-chapters (static ROM parse of terrain + unit placement)
# ---------------------------------------------------------------------------
def _asset_ptr(rom: Rom, table: int, index: int) -> int:
    return rom.u32(table + index * 4)


def decode_terrain(rom: Rom, ch_base: int, asset_table: int) -> Optional[Dict[str, Any]]:
    """Rebuild a chapter's terrain grid the way the game does at load time.

    The main layer decompresses to [w, h] followed by w*h u16 base-tile values;
    terrain[y][x] = tilesetTerrainLookup[baseTile >> 2], where the lookup is the
    u8[0x400] block at byte offset 0x2000 of the decompressed tileset config.
    """
    layer = rom.lz77(_asset_ptr(rom, asset_table, rom.u8(ch_base + CH_MAIN_LAYER_ID)))
    cfg = rom.lz77(_asset_ptr(rom, asset_table, rom.u8(ch_base + CH_TILE_CONFIG_ID)))
    if not layer or not cfg or len(layer) < 2:
        return None
    w, h = layer[0], layer[1]
    need = 2 + 2 * w * h
    if w == 0 or h == 0 or len(layer) < need or len(cfg) < 0x2400:
        return None
    lookup = cfg[0x2000:0x2400]
    grid = []
    for y in range(h):
        row = []
        for x in range(w):
            off = 2 + (y * w + x) * 2
            tile = layer[off] | (layer[off + 1] << 8)
            row.append(lookup[(tile >> 2) & 0x3FF])
        grid.append(row)
    return {"w": w, "h": h, "terrain": grid}


def _parse_unit_def(rom: Rom, addr: int) -> Dict[str, Any]:
    b = rom._off(addr)
    d = rom.data
    char_id, class_id = d[b], d[b + 1]
    packed = d[b + 3]
    pos = d[b + 4] | (d[b + 5] << 8)
    faction = (packed >> 1) & 0x3
    items = [it for it in (d[b + 0x0C + i] for i in range(4)) if it]
    return {
        "faction": FACTION_NAMES[faction],
        "char_id": char_id,
        "char": names.character_name(char_id),
        "class_id": class_id,
        "class": names.class_name(class_id),
        "x": pos & 0x3F,
        "y": (pos >> 6) & 0x3F,
        "level": (packed >> 3) & 0x1F,
        "autolevel": bool(packed & 0x1),
        "gen_monster": bool((pos >> 12) & 0x1),
        "item_drop": bool((pos >> 13) & 0x1),
        "items": [{"id": it, "name": names.item_name(it)} for it in items],
        # UnitDefinition.ai[4] (offset 0x10): the unit's starting AI setup.
        #   a       -> gAi1ScriptTable index: primary doctrine (stationary,
        #              attack-in-range, charge, guard throne/boss, ...).
        #   b       -> gAi2ScriptTable index: secondary behavior (heal, steal,
        #              flee, item use, ...).
        #   config  -> [low, high] bytes of the ai_config bitmask (movement
        #              restriction zones, aggression triggers, etc.).
        "ai": {
            "a": d[b + 0x10],
            "b": d[b + 0x11],
            "config": [d[b + 0x12], d[b + 0x13]],
        },
    }


def _valid_unit_array(rom: Rom, addr: int, valid_classes: set,
                      map_w: int, map_h: int, max_units: int = 64) -> int:
    """Return the unit count if addr is a well-formed UnitDefinition array.

    An array is a run of 0x14-byte entries ending in a charIndex-0 terminator;
    every entry's classIndex must be a real class id AND its (x, y) must fall
    inside the chapter's map bounds. Returns 0 (falsy) when the data does not
    look like a unit array. The map-bounds test is essential: several non-load
    event commands (e.g. 0x0A40) carry a u32 that happens to satisfy the class
    check, but their "positions" land far outside the map (e.g. (36, 61)), so
    bounding by (map_w, map_h) rejects that garbage.
    """
    count = 0
    while count < max_units:
        entry = addr + count * UNITDEF_STRIDE
        if not rom.in_rom(entry) or rom.in_rom(entry + UNITDEF_STRIDE) is False:
            return 0
        char_id = rom.u8(entry)
        if char_id == 0:
            break
        class_id = rom.u8(entry + 1)
        if class_id == 0 or class_id not in valid_classes:
            return 0
        pos = rom.u8(entry + 4) | (rom.u8(entry + 5) << 8)
        if (pos & 0x3F) >= map_w or ((pos >> 6) & 0x3F) >= map_h:
            return 0
        count += 1
    return count


def collect_chapter_units(rom: Rom, event_group: int, script_ptrs: List[int],
                          valid_classes: set, map_w: int, map_h: int) -> List[Dict[str, Any]]:
    """Parse the chapter's opening-event unit loads.

    Walks beginningSceneEvents command-by-command (each command spans
    2 * (LSB >> 4) bytes) up to the next event-script pointer in ROM, and treats
    any 8-byte command whose u32 argument points at an in-bounds, well-formed
    UnitDefinition array as a unit load. This captures every LOU variant
    (0x2C40/0x2C41/0x0540/...) without following CALLs (which would pull in
    unrelated scene units), while the map-bounds check in `_valid_unit_array`
    rejects the false positives from non-load commands.
    """
    if not rom.in_rom(event_group):
        return []
    begin = rom.u32(event_group + CEG_BEGINNING_SCENE)
    if not rom.in_rom(begin):
        return []
    import bisect
    j = bisect.bisect_right(script_ptrs, begin)
    bound = script_ptrs[j] if j < len(script_ptrs) else begin + 0x2000

    units: List[Dict[str, Any]] = []
    seen = set()
    addr = begin
    while addr < bound:
        code = rom.u16(addr)
        length = 2 * ((code & 0xFF) >> 4)
        if length < 4:
            break
        if length == 8:
            ptr = rom.u32(addr + 4)
            if rom.in_rom(ptr) and ptr not in seen:
                n = _valid_unit_array(rom, ptr, valid_classes, map_w, map_h)
                if n:
                    seen.add(ptr)
                    for k in range(n):
                        units.append(_parse_unit_def(rom, ptr + k * UNITDEF_STRIDE))
        addr += length
    return units


def _stacked_tile_count(units: List[Dict[str, Any]]) -> int:
    """Number of unit entries that share a tile with another entry.

    A real turn-1 deployment places one unit per tile, so any stacking means
    those entries are staging positions (cutscene tableau) or reinforcement
    spawns rather than distinct turn-1 tiles. Reported per chapter as a data
    caveat; it is a signal, not a clean classifier (a scripted opening can still
    place its tableau on distinct tiles, e.g. the prologue).
    """
    from collections import Counter
    counts = Counter((u["x"], u["y"]) for u in units)
    return sum(c for c in counts.values() if c > 1)


def _build_script_bounds(rom: Rom, addr: int, stride: int, count: int,
                         asset_table: int) -> List[int]:
    """Sorted set of every event-script pointer across all chapters.

    Scripts are laid out contiguously, so the next pointer above a script's
    start is a tight, reliable upper bound for walking it.
    """
    ptrs = set()
    for i in range(count):
        eg = _asset_ptr(rom, asset_table, rom.u8(addr + i * stride + CH_EVENT_DATA_ID))
        if not rom.in_rom(eg):
            continue
        for k in range(CEG_FIELD_COUNT):
            p = rom.u32(eg + k * 4)
            if rom.in_rom(p):
                ptrs.add(p)
    return sorted(ptrs)


def cmd_all_chapters(args: argparse.Namespace) -> None:
    with open(args.rom, "rb") as f:
        rom = Rom(f.read())
    addr, stride, count = args.chapters_addr
    asset_table = args.asset_table

    # Precompute the set of real class ids (for validating unit arrays) and the
    # sorted list of every event-script pointer (for bounding script walks).
    ca_addr, ca_stride, ca_count = DEFAULT_CLASSES
    valid_classes = {rom.u8(ca_addr + j * ca_stride + 0x04) for j in range(ca_count)}
    valid_classes.discard(0)
    script_ptrs = _build_script_bounds(rom, addr, stride, count, asset_table)

    # Regenerate cleanly: drop any stale chapter JSON from a previous run so the
    # relabeled/renamed files don't leave duplicates behind (the filename scheme
    # now uses canonical chapter tags, not the ROM's off-by-one internal names).
    if os.path.isdir(args.out):
        for stale in glob.glob(os.path.join(args.out, "*.json")):
            os.remove(stale)

    written = 0
    for i in range(count):
        b = addr + i * stride
        name_ptr = rom.u32(b + CH_INTERNAL_NAME)
        internal = ""
        if rom.in_rom(name_ptr):
            o = rom._off(name_ptr)
            end = rom.data.find(b"\x00", o)
            internal = rom.data[o:end].decode("latin1", "replace")

        # Skip unused table slots (gaps between the story/tower/ruins blocks):
        # they have no internalName and aren't in the canonical map.
        if not internal and i not in CANONICAL_CHAPTERS:
            continue

        terrain = decode_terrain(rom, b, asset_table)
        if terrain is None:
            continue  # non-map chapter slot (e.g. cutscene-only entries)

        canon_tag, canon_name = CANONICAL_CHAPTERS.get(i, (internal, internal))

        event_group = _asset_ptr(rom, asset_table, rom.u8(b + CH_EVENT_DATA_ID))
        units = collect_chapter_units(rom, event_group, script_ptrs, valid_classes,
                                      terrain["w"], terrain["h"])
        stacked = _stacked_tile_count(units)

        chapter = {
            "index": i,
            # `chapter` is the authoritative identity (fireemblem8u enum);
            # `internal_name` is the ROM's raw label string, which is off by one
            # from the real chapter number for indices >= 0x0C (see CANONICAL_CHAPTERS).
            "chapter": canon_name,
            "internal_name": internal,
            "title_text_id": rom.u16(b + CH_TITLE_TEXT_ID),
            "map": terrain,
            # `units` is the roster loaded by the opening event: each entry's
            # char/class/level/items/ai are reliable, but POSITION is not always
            # the turn-1 tile. Openings mix real battle placements with cutscene
            # tableaus (units stacked on a staging tile, then moved by in-event
            # MOVE commands) and reinforcement groups (shared spawn tiles). When
            # `stacked_units` > 0, at least that many entries share a tile with
            # another and cannot all be turn-1 occupants; for exact turn-1 state
            # use the live `chapter` bridge snapshot. Terrain is exact.
            "stacked_units": stacked,
            "units": units,
        }
        label = canon_tag or internal or f"chapter_{i:02d}"
        _write_json(os.path.join(args.out, f"{i:02d}_{label}.json"), chapter)
        flag = f"  [{stacked} stacked]" if stacked else ""
        print(f"  {i:#04x} {label:14} {canon_name}: "
              f"{terrain['w']}x{terrain['h']}, {len(units)} unit(s){flag}")
        written += 1
    print(f"wrote {written} chapter file(s) to {args.out}")


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

    a = sub.add_parser("all-chapters",
                       help="statically dump terrain + unit placement for every chapter from a ROM file")
    a.add_argument("--rom", required=True, help="path to the FE8 (U) .gba ROM")
    a.add_argument("--out", default="outputs/chapters", help="output directory")
    a.add_argument("--chapters-addr", type=_addr_triple, default=DEFAULT_CHAPTERS,
                   metavar="ADDR:STRIDE:COUNT")
    a.add_argument("--asset-table", type=lambda s: int(s, 0), default=CHAPTER_ASSET_TABLE,
                   metavar="ADDR", help="gChapterDataAssetTable address")
    a.set_defaults(func=cmd_all_chapters)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
