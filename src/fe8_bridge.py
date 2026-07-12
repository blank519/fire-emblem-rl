"""Client bridge for the mGBA `scripts/fe8_state.lua` state server.

Connect to the running mGBA instance and pull a full snapshot of every
active unit (stats, position, faction) as plain Python dicts. Uses only the
standard library.

Example:
    from fe8_bridge import FE8Bridge

    with FE8Bridge() as bridge:
        state = bridge.read_state()
        for faction, units in state["units"].items():
            for u in units:
                print(faction, u["char_id"], u["cur_hp"], u["x"], u["y"])
"""

from __future__ import annotations

import heapq
import json
import socket
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

Tile = Tuple[int, int]

# Movement-cost tables use a large value for tiles a class cannot traverse
# (walls, deep sea for non-flyers). Real costs are small (1-6), so anything at
# or above this threshold - or non-positive - is treated as impassable.
IMPASSABLE_COST = 31

# Factions on the same side never block each other's movement and are not
# valid attack targets for one another. Player controls blue (green is a
# neutral ally in most chapters); red/purple are hostile.
_SIDE = {"blue": 0, "green": 0, "red": 1, "purple": 1}


def combat_stats(unit: Dict[str, Any]) -> Optional[Dict[str, int]]:
    """Derive FE8 combat stats from a unit's base stats + equipped weapon.

    Returns None if the unit has no weapon equipped. Situational modifiers
    (weapon triangle, supports, terrain, class/S-rank crit bonuses) are not
    applied, since they depend on the opponent and tile.

    Note: In GBA FE, Str and Magic share the same stat (``str`` here), so
    ``atk`` is correct for both physical and magical weapons.
    """
    w = unit.get("weapon")
    if not w:
        return None

    spd, luk, skl = unit["spd"], unit["luk"], unit["skl"]
    attack_speed = spd - max(0, w["weight"] - unit["con"])
    attack_speed = max(0, attack_speed)

    return {
        "atk": unit["str"] + w["might"],
        "attack_speed": attack_speed,
        "hit": w["hit"] + skl * 2 + luk // 2,
        "avoid": attack_speed * 2 + luk,
        "crit": w["crit"] + skl // 2,
        "crit_avoid": luk,
    }


# ---------------------------------------------------------------------------
# Map / faction helpers
# ---------------------------------------------------------------------------
def iter_units(state: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    """Yield every unit across all factions (tagging faction if absent)."""
    for faction, units in state.get("units", {}).items():
        for u in units:
            u.setdefault("faction", faction)
            yield u


def living_units(state: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    for u in iter_units(state):
        if not u.get("dead") and u.get("deployed", True):
            yield u


def is_hostile(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    return _SIDE.get(a.get("faction"), 1) != _SIDE.get(b.get("faction"), 1)


def build_class_tables(state: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    """Map class_id -> its terrain tables (move_cost/def/avo/res)."""
    return {c["class_id"]: c for c in state.get("classes", [])}


def occupancy(state: Dict[str, Any]) -> Dict[Tile, Dict[str, Any]]:
    """Map (x, y) -> the living unit standing there."""
    return {(u["x"], u["y"]): u for u in living_units(state)}


# ---------------------------------------------------------------------------
# Movement (Dijkstra over per-class terrain costs)
# ---------------------------------------------------------------------------
def reachable_tiles(
    state: Dict[str, Any],
    unit: Dict[str, Any],
    class_tables: Optional[Dict[int, Dict[str, Any]]] = None,
    occ_map: Optional[Dict[Tile, Dict[str, Any]]] = None,
) -> Dict[Tile, int]:
    """Tiles the unit can *stop on*, mapped to the movement spent to reach them.

    Weighted BFS from the unit's tile using its class's per-terrain movement
    cost. Enemy-occupied tiles are impassable; allied tiles may be moved
    through but not stopped on. The unit's own tile is always included.
    """
    map = state.get("map")
    if not map:
        return {}
    if class_tables is None:
        class_tables = build_class_tables(state)
    if occ_map is None:
        # Build occupancy map if not provided
        occ_map = occupancy(state)

    class_table = class_tables.get(unit["class_id"]) or {}
    costs = class_table.get("move_cost")
    if not costs:
        return {}

    w, h, terrain = map["w"], map["h"], map["terrain"]
    start: Tile = (unit["x"], unit["y"])
    move_budget = unit["move"]

    best: Dict[Tile, int] = {start: 0} #Stores the shortest found distance to each tile
    prio_queue: List[Tuple[int, Tile]] = [(0, start)]
    while prio_queue:
        distance, (x, y) = heapq.heappop(prio_queue)
        if distance > best.get((x, y), 1 << 30):
            continue
        for new_x, new_y in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if not (0 <= new_x < w and 0 <= new_y < h):
                continue
            terrain_id = terrain[new_y][new_x]
            step_cost = costs[terrain_id] if terrain_id < len(costs) else IMPASSABLE_COST
            # Skip impassable terrain
            if step_cost is None or step_cost <= 0 or step_cost >= IMPASSABLE_COST:
                continue
            # Skip enemy-occupied tiles
            other = occ_map.get((new_x, new_y))
            if other is not None and is_hostile(unit, other):
                continue  # cannot pass through / into enemies
            new_distance = distance + step_cost
            if new_distance <= move_budget and new_distance < best.get((new_x, new_y), 1 << 30):
                best[(new_x, new_y)] = new_distance
                heapq.heappush(prio_queue, (new_distance, (new_x, new_y)))

    # Can only *stop* on a tile that is empty (or the unit's own start tile).
    return {
        tile: distance
        for tile, distance in best.items()
        if tile == start or occ_map.get(tile) is None
    }


# ---------------------------------------------------------------------------
# Weapon ranges & targeting
# ---------------------------------------------------------------------------
def weapon_ranges(unit: Dict[str, Any]) -> List[Tuple[int, int, Dict[str, Any]]]:
    """(min_range, max_range, item) for each usable weapon in inventory.

    A ``range_max`` of 0 encodes the dynamic magic range (1..Mag/2); it is
    resolved here using the unit's power stat.
    """
    out: List[Tuple[int, int, Dict[str, Any]]] = []
    for it in unit.get("items", []):
        if not it.get("is_weapon"):
            continue
        lo = it["range_min"] or 1
        hi = it["range_max"]
        if hi == 0:
            hi = max(1, unit["str"] // 2)
        out.append((lo, hi, it))
    return out


def attackable_targets(
    state: Dict[str, Any],
    unit: Dict[str, Any],
    dest: Tile,
    enemies: Optional[List[Dict[str, Any]]] = None,
) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """(target_unit, weapon_item) pairs the unit can hit if it moves to ``dest``."""
    if enemies is None:
        enemies = [u for u in living_units(state) if is_hostile(unit, u)]
    dx, dy = dest
    hits: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for lo, hi, item in weapon_ranges(unit):
        for e in enemies:
            if lo <= abs(e["x"] - dx) + abs(e["y"] - dy) <= hi:
                hits.append((e, item))
    return hits


def action_mask(
    state: Dict[str, Any], unit: Dict[str, Any]
) -> Dict[str, Any]:
    """Autoregressive action legality for one unit.

    Returns:
      - ``move``: {dest_tile: move_cost} the unit can stop on.
      - ``attacks``: {dest_tile: [(target, weapon), ...]} for dests from which
        at least one enemy is in range. Compute stage 2 (target+weapon) by
        indexing this with the chosen destination.
    """
    class_tables = build_class_tables(state)
    occ_map = occupancy(state)
    reach = reachable_tiles(state, unit, class_tables, occ_map)
    enemies = [u for u in living_units(state) if is_hostile(unit, u)]

    attacks: Dict[Tile, List[Tuple[Dict[str, Any], Dict[str, Any]]]] = {}
    for dest in reach:
        hits = attackable_targets(state, unit, dest, enemies)
        if hits:
            attacks[dest] = hits
    return {"move": reach, "attacks": attacks}


# ---------------------------------------------------------------------------
# Terrain effects (for observation channels / combat resolution)
# ---------------------------------------------------------------------------
def _class_terrain(
    state: Dict[str, Any], unit: Dict[str, Any], key: str
) -> Optional[List[int]]:
    ct = build_class_tables(state).get(unit["class_id"]) or {}
    return ct.get(key)


def tile_defense_bonus(state: Dict[str, Any], unit: Dict[str, Any], tile: Tile) -> int:
    tbl = _class_terrain(state, unit, "def")
    tid = state["map"]["terrain"][tile[1]][tile[0]]
    return tbl[tid] if tbl and tid < len(tbl) else 0


def tile_avoid_bonus(state: Dict[str, Any], unit: Dict[str, Any], tile: Tile) -> int:
    tbl = _class_terrain(state, unit, "avo")
    tid = state["map"]["terrain"][tile[1]][tile[0]]
    return tbl[tid] if tbl and tid < len(tbl) else 0


def tile_heal_amount(state: Dict[str, Any], tile: Tile) -> int:
    heal = state.get("terrain_heal")
    tid = state["map"]["terrain"][tile[1]][tile[0]]
    return heal[tid] if heal and tid < len(heal) else 0


class FE8Bridge:
    """Request/response client for the FE8 Lua state server."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8888,
        timeout: float = 5.0,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._buf = b""

    def connect(self) -> "FE8Bridge":
        self._sock = socket.create_connection(
            (self.host, self.port), timeout=self.timeout
        )
        self._buf = b""
        return self

    def read_state(self) -> Dict[str, Any]:
        """Ask the emulator for one snapshot and return it as a dict."""
        if self._sock is None:
            self.connect()
        assert self._sock is not None

        self._sock.sendall(b"state\n")
        while b"\n" not in self._buf:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise ConnectionError("mGBA closed the connection")
            self._buf += chunk

        line, _, self._buf = self._buf.partition(b"\n")
        return json.loads(line.decode("utf-8"))

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        self._buf = b""

    def __enter__(self) -> "FE8Bridge":
        return self.connect()

    def __exit__(self, *exc: object) -> None:
        self.close()


if __name__ == "__main__":
    with FE8Bridge() as bridge:
        state = bridge.read_state()
        
        import json
        with open("outputs/state.json", "w") as f:
            json.dump(state, f, indent=4, sort_keys=True)
        
        frame = state.get("frame")
        print(f"frame: {frame}")
        
        # m['w'] and m['h'] are the map dimensions
        # m['terrain'] is a 2D array of terrain IDs in the format m['terrain'][row][col]
        m = state.get("map")
        
        if m:
            print(f"map: {m['w']}x{m['h']} | classes cached: {len(state.get('classes', []))}")
            for row in m["terrain"]:
                print(row)
            blue = state["units"].get("blue", [])
            live = [u for u in blue if not u.get("dead") and u.get("deployed")]
            if live:
                u = live[0]
                mask = action_mask(state, u)
                tgts = sum(len(v) for v in mask["attacks"].values())
                print(
                    f"active-sample char={u['char_id']} @({u['x']},{u['y']}): "
                    f"{len(mask['move'])} reachable tile(s), "
                    f"{len(mask['attacks'])} attack tile(s), {tgts} (dest,target) option(s)"
                )

        for faction, units in state["units"].items():
            print(f"\n[{faction}] {len(units)} unit(s)")
            for u in units:
                items = ",".join(
                    f"{it['id']}({it['uses']})" for it in u.get("items", [])
                ) or "-"
                print(
                    f"  char={u['char_id']:>3} class={u['class_id']:>3} "
                    f"lv{u['level']:>2} exp={u['exp']:>2} "
                    f"hp={u['cur_hp']:>2}/{u['max_hp']:>2} "
                    f"pos=({u['x']},{u['y']}) mov={u['move']} "
                    f"str={u['str']} skl={u['skl']} spd={u['spd']} "
                    f"def={u['def']} res={u['res']} lck={u['luk']} con={u['con']} "
                    f"dead={u['dead']}"
                )
                print(f"      items=[{items}]")
                cs = combat_stats(u)
                if cs:
                    print(
                        f"      combat: atk={cs['atk']} hit={cs['hit']} "
                        f"avo={cs['avoid']} crit={cs['crit']} "
                        f"AS={cs['attack_speed']} critAvo={cs['crit_avoid']}"
                    )
