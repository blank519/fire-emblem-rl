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

import json
import socket
from typing import Any, Dict, Optional


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
        frame = state.get("frame")
        print(f"frame: {frame}")
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
