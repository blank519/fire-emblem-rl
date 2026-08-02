"""FE8 terrain-type names, colors, and grid visualization.

Terrain grids store the raw FE8 terrain-type id (0x00-0x40, 65 types) from the
fireemblem8u `enum` in `include/constants/terrains.h`. This module maps each id
to a human-readable name and a themed color, and renders a grid as a colored
image with a legend -- far more readable than the raw integer array.

Matplotlib is imported lazily inside the plotting helpers, so the name/color
tables and `ascii_render` work even without it installed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

# All rendered images are written here (created on demand). Callers pass just a
# filename (or any path); only its basename is used, so every image lands here.
OUTPUT_DIR = Path("outputs/grids/grid_visuals")

# Value used by the generator for a not-yet-chosen tile.
UNCHOSEN = -1

# id -> short name (authoritative: fireemblem8u include/constants/terrains.h).
# A trailing '*' marks types that are unused in vanilla FE8 (FE6/FE7 leftovers).
# 0x2C / 0x2E have no in-game name: GetTerrainName maps both to a blank string
# (0x7F 0x7F, identical to TERRAIN_NONE), verified by decoding the ROM's
# terrain-name text. They are the *bulk* of a building's footprint, while the
# named, interactable entrance sits on a neighbouring tile:
#   0x2C = "Large Building" -- castles, pyramids, etc.; impassable to all. The
#          adjacent Gate (castle) tile 0x0B is the single interaction square.
#   0x2E = "Small Building" -- villages, vendors, armories; blocks mounted/
#          armored classes but is walkable by foot. The named door tile
#          (0x03/0x04 Village, 0x06 Armory, 0x07 Vendor) is the interaction
#          square. Neither 0x2C nor 0x2E grants avoid/def.
TERRAIN_NAMES: Dict[int, str] = {
    0x00: "None",            0x01: "Plains",         0x02: "Road",
    0x03: "Village",         0x04: "Village (shut)", 0x05: "House",
    0x06: "Armory",          0x07: "Vendor",         0x08: "Arena",
    0x09: "C Room",          0x0A: "Fort",           0x0B: "Gate (castle)",
    0x0C: "Forest",          0x0D: "Thicket",        0x0E: "Sand",
    0x0F: "Desert",          0x10: "River",          0x11: "Mountain",
    0x12: "Peak",            0x13: "Bridge",         0x14: "Bridge (14)",
    0x15: "Sea",             0x16: "Lake",           0x17: "Floor",
    0x18: "Floor (magic)*",  0x19: "Fence",          0x1A: "Wall",
    0x1B: "Wall (damaged)",  0x1C: "Rubble",         0x1D: "Pillar",
    0x1E: "Door",            0x1F: "Throne",         0x20: "Chest (empty)",
    0x21: "Chest",           0x22: "Roof",           0x23: "Gate",
    0x24: "Church",          0x25: "Ruins",          0x26: "Cliff",
    0x27: "Ballista",        0x28: "Ballista (long)", 0x29: "Ballista (killer)",
    0x2A: "Ship (flat)",     0x2B: "Ship (wreck)",   0x2C: "Large Building",
    0x2D: "Stairs",          0x2E: "Small Building",  0x2F: "Glacier",
    0x30: "Arena (30)",      0x31: "Valley",         0x32: "Fence (32)",
    0x33: "Snag",            0x34: "Bridge (snag)",  0x35: "Sky",
    0x36: "Deeps",           0x37: "Ruins village*", 0x38: "Inn",
    0x39: "Barrel",          0x3A: "Bone",           0x3B: "Dark",
    0x3C: "Water",           0x3D: "Gunnels",        0x3E: "Deck",
    0x3F: "Brace",           0x40: "Mast",
}

# id -> hex color, grouped by theme so categories read at a glance:
# greens = open/vegetation, blues = water, browns/grays = rock & walls,
# tans = indoor/roads, warm = buildings, purple/red = strategic, wood = ships.
_MAGENTA = "#ff00ff"        # unchosen / out-of-range sentinel (stands out)
TERRAIN_COLORS: Dict[int, str] = {
    0x00: "#101014",  # None (void)
    0x01: "#a8d08d",  # Plains
    0x02: "#d9c9a3",  # Road
    0x03: "#e8952f",  # Village
    0x04: "#b5791f",  # Village (shut)
    0x05: "#e0a44a",  # House
    0x06: "#c98b3a",  # Armory
    0x07: "#c85c9c",  # Vendor
    0x08: "#c0392b",  # Arena
    0x09: "#7f8c8d",  # C Room
    0x0A: "#8e6f4e",  # Fort
    0x0B: "#6d4c9f",  # Gate (castle)
    0x0C: "#2e7d32",  # Forest
    0x0D: "#1b5e20",  # Thicket
    0x0E: "#e6d59a",  # Sand
    0x0F: "#d9b466",  # Desert
    0x10: "#4a90d9",  # River
    0x11: "#8b6f47",  # Mountain
    0x12: "#6b5335",  # Peak
    0x13: "#b98a4a",  # Bridge
    0x14: "#a87c40",  # Bridge (14)
    0x15: "#1f6fb2",  # Sea
    0x16: "#3d8ec9",  # Lake
    0x17: "#cfc6b0",  # Floor
    0x18: "#b8a9d0",  # Floor (magic)*
    0x19: "#8d6e63",  # Fence
    0x1A: "#5d5d5d",  # Wall
    0x1B: "#7a7a7a",  # Wall (damaged)
    0x1C: "#9c8f7a",  # Rubble
    0x1D: "#b0a999",  # Pillar
    0x1E: "#8d5a2b",  # Door
    0x1F: "#7b1fa2",  # Throne
    0x20: "#b89a5a",  # Chest (empty)
    0x21: "#f1c40f",  # Chest
    0x22: "#a0522d",  # Roof
    0x23: "#9575cd",  # Gate
    0x24: "#d1c4e9",  # Church
    0x25: "#9e8b6c",  # Ruins
    0x26: "#6e5b45",  # Cliff
    0x27: "#c0392b",  # Ballista
    0x28: "#a93226",  # Ballista (long)
    0x29: "#922b21",  # Ballista (killer)
    0x2A: "#a97444",  # Ship (flat)
    0x2B: "#7d5230",  # Ship (wreck)
    0x2C: "#d35400",  # Large Building (reddish orange)
    0x2D: "#c2b280",  # Stairs
    0x2E: "#e67e22",  # Small Building (reddish orange, lighter)
    0x2F: "#cfe8f0",  # Glacier
    0x30: "#b03a2e",  # Arena (30)
    0x31: "#7cb342",  # Valley
    0x32: "#7d6058",  # Fence (32)
    0x33: "#4e6b3a",  # Snag
    0x34: "#9a7b45",  # Bridge (snag)
    0x35: "#b3e5fc",  # Sky
    0x36: "#0d3b66",  # Deeps
    0x37: "#8a7a5c",  # Ruins village*
    0x38: "#e07b39",  # Inn
    0x39: "#8d5a2b",  # Barrel
    0x3A: "#e0dcc8",  # Bone
    0x3B: "#311b4f",  # Dark
    0x3C: "#2e86c1",  # Water
    0x3D: "#8d6238",  # Gunnels
    0x3E: "#a97c50",  # Deck
    0x3F: "#7a5230",  # Brace
    0x40: "#5d3d20",  # Mast
}

# Compact single-char glyphs for terminal rendering, keyed by category.
_GLYPHS: Dict[int, str] = {}
for _i in (0x01, 0x02, 0x17, 0x2D, 0x13, 0x14, 0x34):
    _GLYPHS[_i] = "."   # passable ground
for _i in (0x0C, 0x0D, 0x31, 0x33):
    _GLYPHS[_i] = "T"   # vegetation
for _i in (0x11, 0x12, 0x26):
    _GLYPHS[_i] = "^"   # high ground
for _i in (0x10, 0x15, 0x16, 0x3C, 0x36, 0x2A, 0x2B):
    _GLYPHS[_i] = "~"   # water/ship-water
for _i in (0x0E, 0x0F):
    _GLYPHS[_i] = ":"   # sand/desert
for _i in (0x1A, 0x1B, 0x19, 0x1D, 0x1C, 0x32, 0x22):
    _GLYPHS[_i] = "#"   # walls/structure
for _i in (0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x24, 0x38, 0x25):
    _GLYPHS[_i] = "H"   # buildings
for _i in (0x0A, 0x0B, 0x23, 0x1F):
    _GLYPHS[_i] = "&"   # strategic (fort/gate/throne)
for _i in (0x1E, 0x20, 0x21, 0x39):
    _GLYPHS[_i] = "+"   # door/chest
for _i in (0x3D, 0x3E, 0x3F, 0x40):
    _GLYPHS[_i] = "S"   # ship structure
_GLYPHS[0x00] = " "     # void
del _i


def terrain_name(tid: int) -> str:
    """Human-readable name for a terrain id (or a labeled fallback)."""
    if tid == UNCHOSEN:
        return "Unchosen"
    return TERRAIN_NAMES.get(int(tid), f"Unknown {int(tid):#04x}")


def _hex_to_rgb(h: str) -> Tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore


def _palette() -> np.ndarray:
    """(66, 3) RGB lookup; row 0 = UNCHOSEN, row (id+1) = terrain id."""
    pal = np.zeros((66, 3), dtype=float)
    pal[0] = _hex_to_rgb(_MAGENTA)
    for tid in range(0x41):
        pal[tid + 1] = _hex_to_rgb(TERRAIN_COLORS.get(tid, _MAGENTA))
    return pal


def grid_to_rgb(grid: np.ndarray) -> np.ndarray:
    """Map a (H, W) id grid to an (H, W, 3) float RGB image."""
    g = np.clip(np.asarray(grid, dtype=int), UNCHOSEN, 0x40)
    return _palette()[g + 1]


def ascii_render(grid: np.ndarray) -> str:
    """Compact single-char rendering for quick terminal inspection."""
    g = np.asarray(grid, dtype=int)
    lines = []
    for row in g:
        lines.append("".join("?" if v == UNCHOSEN else _GLYPHS.get(int(v), "*")
                             for v in row))
    return "\n".join(lines)


def render_grid(grid, ax=None, title: Optional[str] = None,
                show_legend: bool = True, gridlines: bool = True):
    """Draw one terrain grid onto a matplotlib Axes (creates one if needed).

    Returns the Axes. Use `save_grid_png` for a one-call file export.
    """
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    g = np.asarray(grid, dtype=int)
    if ax is None:
        h, w = g.shape
        _, ax = plt.subplots(figsize=(max(4, w / 3), max(4, h / 3)))

    ax.imshow(grid_to_rgb(g), interpolation="nearest", aspect="equal")
    if title:
        ax.set_title(title, fontsize=11)
    ax.set_xticks([])
    ax.set_yticks([])

    if gridlines and max(g.shape) <= 60:
        ax.set_xticks(np.arange(-0.5, g.shape[1], 1), minor=True)
        ax.set_yticks(np.arange(-0.5, g.shape[0], 1), minor=True)
        ax.grid(which="minor", color="#00000022", linewidth=0.5)
        ax.tick_params(which="minor", length=0)

    if show_legend:
        present = sorted(int(v) for v in np.unique(g))
        handles = [Patch(facecolor=(_MAGENTA if v == UNCHOSEN
                                    else TERRAIN_COLORS.get(v, _MAGENTA)),
                         edgecolor="#333333",
                         label=("-1 Unchosen" if v == UNCHOSEN
                                else f"{v:#04x} {terrain_name(v)}"))
                   for v in present]
        ax.legend(handles=handles, bbox_to_anchor=(1.02, 1), loc="upper left",
                  fontsize=8, frameon=False, borderaxespad=0)
    return ax


def _resolve_out(path) -> Path:
    """Route every rendered image into OUTPUT_DIR (by filename), creating it."""
    out = OUTPUT_DIR / Path(path).name
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def save_grid_png(grid, path: str, title: Optional[str] = None) -> str:
    """Render a single grid to OUTPUT_DIR/<filename>.png. Returns the written path."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out = _resolve_out(path)
    ax = render_grid(grid, title=title)
    ax.figure.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(ax.figure)
    return str(out)


def save_grid_panels(items: Sequence[Tuple[str, np.ndarray]], path: str,
                     suptitle: Optional[str] = None) -> str:
    """Render several (title, grid) pairs side by side into OUTPUT_DIR/<filename>."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    n = len(items)
    fig, axes = plt.subplots(1, n, figsize=(6.5 * n, 6.5), squeeze=False)
    for ax, (title, grid) in zip(axes[0], items):
        # Legend only on the last panel to avoid repetition.
        render_grid(grid, ax=ax, title=title, show_legend=(ax is axes[0][-1]))
    if suptitle:
        fig.suptitle(suptitle, fontsize=13)
    out = _resolve_out(path)
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return str(out)


def _load_grid(path: str) -> np.ndarray:
    if path.endswith(".npy"):
        return np.load(path)
    return np.loadtxt(path, dtype=int)  # whitespace-separated int text


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(description="Visualize an FE8 terrain grid (.npy or .txt).")
    p.add_argument("grid", help="path to a .npy or whitespace-int .txt grid")
    p.add_argument("-o", "--out",
                   help="output PNG filename (written into outputs/grids/grid_visuals/)")
    p.add_argument("--ascii", action="store_true", help="also print an ASCII rendering")
    args = p.parse_args()

    grid = _load_grid(args.grid)
    out_name = args.out or (Path(args.grid).stem + ".png")
    written = save_grid_png(grid, out_name, title=args.grid)
    print(f"wrote {written}  ({grid.shape[1]}x{grid.shape[0]})")
    if args.ascii:
        print(ascii_render(grid))


if __name__ == "__main__":
    main()
