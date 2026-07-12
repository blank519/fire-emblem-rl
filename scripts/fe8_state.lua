-- fe8_state.lua
-- Live state reader for Fire Emblem: The Sacred Stones (U) running in mGBA.
--
-- Enumerates every faction's unit array and exposes fully-computed unit
-- stats/positions over a TCP socket, so a reinforcement-learning client can
-- pull the complete battle state on demand without selecting any unit.
--
-- Usage in mGBA:
--   1. Load the FE8 (U) ROM.
--   2. Tools > Scripting...  then  File > Load script...  and pick this file.
--   3. Connect a client to 127.0.0.1:<PORT> (default 8888) and send any line;
--      the script replies with one newline-terminated JSON snapshot.
--
-- Addresses are from the fireemblem8u decomp symbol map (US build).

local PORT = 8888

-- EWRAM unit arrays: { name, base address, slot count }.
local FACTIONS = {
  { name = "blue",   base = 0x0202BE4C, count = 62 }, -- player units
  { name = "red",    base = 0x0202CFBC, count = 50 }, -- enemy units
  { name = "green",  base = 0x0202DDCC, count = 20 }, -- allied NPC units
  { name = "purple", base = 0x0202E36C, count = 5  }, -- misc / 4th faction
}

local UNIT_SIZE = 0x48 -- bytes per struct Unit

-- struct Unit field offsets (bytes).
local O = {
  pChar  = 0x00, -- u32 -> ROM CharacterData
  pClass = 0x04, -- u32 -> ROM ClassData
  level  = 0x08,
  exp    = 0x09,
  index  = 0x0B, -- deployment / roster index
  state  = 0x0C, -- u32 state bitfield
  x      = 0x10,
  y      = 0x11,
  maxHP  = 0x12,
  curHP  = 0x13,
  str    = 0x14,
  skl    = 0x15,
  spd    = 0x16,
  def    = 0x17,
  res    = 0x18,
  luk    = 0x19,
  con    = 0x1A, -- constitution bonus
  move   = 0x1D, -- current movement
  items  = 0x1E, -- 5 x u16 (low byte = item id, high byte = uses)
}

-- State flag bits (well-documented subset).
local STATE_DEAD          = 0x04
local STATE_NOT_DEPLOYED  = 0x08

-- CharacterData / ClassData store their id as a u8 at offset 4.
local ID_OFFSET = 0x04
local ROM_BASE  = 0x08000000

-- GBA EWRAM range. Heap-allocated buffers (e.g. the map's row-pointer
-- tables) live here, as opposed to ROM_BASE-relative static data tables.
local EWRAM_BASE = 0x02000000
local EWRAM_END  = 0x02040000

-- Item table (gItemData) and struct ItemData layout (fireemblem8u, US build).
local ITEM_TABLE = 0x08809B10
local ITEM_SIZE  = 0x24
local IO = {
  weaponType = 0x07,
  attributes = 0x08, -- u32
  maxUses    = 0x14,
  might      = 0x15,
  hit        = 0x16,
  weight     = 0x17,
  crit       = 0x18,
}
local IA_WEAPON = 0x01 -- IA_WEAPON attribute bit
local IO_RANGE  = 0x19 -- ItemData.encodedRange (min = hi nibble, max = lo nibble)

-- struct ClassData terrain-table pointers (fireemblem8u, US build). Each field
-- is a pointer to an s8 table indexed by terrain id.
local CD = {
  baseCon = 0x11, -- s8 base constitution (unit stores only a bonus)
  baseMov = 0x12, -- s8 base movement    (unit stores only a bonus)
  movCost = 0x38, -- pMovCostTable[0] (standard weather; [1]=rain, [2]=snow)
  avo     = 0x44, -- pTerrainAvoidLookup
  def     = 0x48, -- pTerrainDefenseLookup
  res     = 0x4C, -- pTerrainResistanceLookup
}

-- Map buffers (EWRAM). Vec2 gBmMapSize {s16 x, s16 y}; the others are u8**
-- (pointer to an array of per-row u8* pointers), indexed [y][x].
local MAP = {
  size    = 0x0202E4D4,
  unit    = 0x0202E4D8,
  terrain = 0x0202E4DC,
}
local ACTIVE_UNIT_ID    = 0x0202BE44 -- gActiveUnitId (u8)
local TERRAIN_COUNT     = 0x41       -- 65 terrain ids
local TERRAIN_HEAL_ADDR = 0x0880C744 -- TerrainTable_HealAmount (u8[terrain])

-- ---------------------------------------------------------------------------
-- Memory helpers
-- ---------------------------------------------------------------------------
local function u8(a)  return emu:read8(a)  end
local function u16(a) return emu:read16(a) end
local function u32(a) return emu:read32(a) end
local function s8(a)
  local v = emu:read8(a)
  if v >= 0x80 then v = v - 0x100 end
  return v
end

-- ---------------------------------------------------------------------------
-- Minimal JSON encoder (numbers, booleans, strings, arrays, objects)
-- ---------------------------------------------------------------------------
local function json_str(s)
  s = s:gsub("\\", "\\\\"):gsub('"', '\\"')
  return '"' .. s .. '"'
end

local function json_encode(v)
  local t = type(v)
  if t == "number" then
    return tostring(v)
  elseif t == "boolean" then
    return v and "true" or "false"
  elseif t == "nil" then
    return "null"
  elseif t == "string" then
    return json_str(v)
  elseif t == "table" then
    local n = 0
    local isArray = true
    for k in pairs(v) do
      n = n + 1
      if type(k) ~= "number" then isArray = false end
    end
    if isArray and n == #v then
      local parts = {}
      for i = 1, #v do parts[i] = json_encode(v[i]) end
      return "[" .. table.concat(parts, ",") .. "]"
    end
    local parts = {}
    for k, val in pairs(v) do
      parts[#parts + 1] = json_str(tostring(k)) .. ":" .. json_encode(val)
    end
    return "{" .. table.concat(parts, ",") .. "}"
  end
  return "null"
end

-- ---------------------------------------------------------------------------
-- Item / weapon reading
-- ---------------------------------------------------------------------------
-- Read a single item's static data from gItemData. The `encodedRange` byte
-- packs range as (min = high nibble, max = low nibble); a low nibble of 0 marks
-- a dynamic magic range (1..Mag/2), reported here as range_max = 0.
local function read_item_data(itemId)
  local p = ITEM_TABLE + itemId * ITEM_SIZE
  local attr = u32(p + IO.attributes)
  local rng  = u8(p + IO_RANGE)
  return {
    id        = itemId,
    type      = u8(p + IO.weaponType),
    might     = u8(p + IO.might),
    hit       = u8(p + IO.hit),
    weight    = u8(p + IO.weight),
    crit      = u8(p + IO.crit),
    range_min = (rng >> 4) & 0xF,
    range_max = rng & 0xF,
    is_weapon = (attr & IA_WEAPON) ~= 0,
  }
end

local function read_items(base)
  local items = {}
  for i = 0, 4 do
    local v = u16(base + O.items + i * 2)
    local id = v & 0xFF
    if id ~= 0 then
      local it = read_item_data(id)
      it.uses = (v >> 8) & 0xFF
      it.slot = i
      items[#items + 1] = it
    end
  end
  return items
end

-- The equipped weapon is the first inventory item flagged as a weapon.
local function resolve_weapon(items)
  for _, it in ipairs(items) do
    if it.is_weapon then return it end
  end
  return nil
end

-- ---------------------------------------------------------------------------
-- Map / terrain reading
-- ---------------------------------------------------------------------------
-- Read an s8 terrain lookup (indexed by terrain id) pointed to by a ClassData
-- field. Returns nil when the pointer is not in ROM (class not loaded).
local function read_terrain_vector(ptr)
  if ptr == nil or ptr < ROM_BASE then return nil end
  local t = {}
  for i = 0, TERRAIN_COUNT - 1 do t[i + 1] = s8(ptr + i) end
  return t
end

-- Per-class terrain tables: movement cost, defense/avoid/res bonuses.
local function read_class_terrain(pClass)
  return {
    move_cost = read_terrain_vector(u32(pClass + CD.movCost)),
    def       = read_terrain_vector(u32(pClass + CD.def)),
    avo       = read_terrain_vector(u32(pClass + CD.avo)),
    res       = read_terrain_vector(u32(pClass + CD.res)),
  }
end

-- Global heal amount per terrain id (e.g. fort/throne regen).
local function read_terrain_heal()
  local t = {}
  for i = 0, TERRAIN_COUNT - 1 do t[i + 1] = u8(TERRAIN_HEAL_ADDR + i) end
  return t
end

-- Full map: dimensions plus [y][x] grids of terrain ids and tile occupants
-- (gBmMapUnit stores each occupant's unit index; 0 = empty). Returns nil when
-- no map is loaded (e.g. on menus / world map).
local function read_map()
  local w = u16(MAP.size)
  local h = u16(MAP.size + 2)
  local tBase = u32(MAP.terrain)
  local uBase = u32(MAP.unit)
  -- gBmMapTerrain/gBmMapUnit are u8** into heap-allocated EWRAM buffers, not
  -- ROM, so they must be validated against EWRAM's range (not ROM_BASE).
  if w <= 0 or h <= 0 or w > 64 or h > 64
      or tBase < EWRAM_BASE or tBase >= EWRAM_END
      or uBase < EWRAM_BASE or uBase >= EWRAM_END then
    return nil
  end
  local terrain, occ = {}, {}
  for y = 0, h - 1 do
    local tRow = u32(tBase + y * 4)
    local uRow = u32(uBase + y * 4)
    local tl, ul = {}, {}
    for x = 0, w - 1 do
      tl[x + 1] = u8(tRow + x)
      ul[x + 1] = u8(uRow + x)
    end
    terrain[y + 1] = tl
    occ[y + 1] = ul
  end
  return { w = w, h = h, terrain = terrain, unit = occ }
end

-- ---------------------------------------------------------------------------
-- Unit reading
-- ---------------------------------------------------------------------------
local function read_unit(base)
  local pChar = u32(base + O.pChar)
  if pChar == 0 then return nil end -- empty slot

  local state = u32(base + O.state)
  local pClass = u32(base + O.pClass)

  local charId, classId = 0, 0
  local baseCon, baseMov = 0, 0
  if pChar >= ROM_BASE  then charId  = u8(pChar  + ID_OFFSET) end
  if pClass >= ROM_BASE then
    classId = u8(pClass + ID_OFFSET)
    baseCon = s8(pClass + CD.baseCon)
    baseMov = s8(pClass + CD.baseMov)
  end

  local items = read_items(base)

  return {
    addr     = string.format("0x%08X", base),
    char_id  = charId,
    class_id = classId,
    index    = u8(base + O.index),
    level    = u8(base + O.level),
    exp      = u8(base + O.exp),
    x        = u8(base + O.x),
    y        = u8(base + O.y),
    max_hp   = u8(base + O.maxHP),
    cur_hp   = u8(base + O.curHP),
    str      = u8(base + O.str),
    skl      = u8(base + O.skl),
    spd      = u8(base + O.spd),
    def      = u8(base + O.def),
    res      = u8(base + O.res),
    luk      = u8(base + O.luk),
    con      = baseCon + s8(base + O.con),  -- class base + per-unit bonus
    con_bonus = s8(base + O.con),
    move     = baseMov + s8(base + O.move), -- class base + per-unit bonus
    mov_bonus = s8(base + O.move),
    items    = items,
    weapon   = resolve_weapon(items),
    class_ptr = pClass,
    state    = state,
    dead     = (state & STATE_DEAD) ~= 0,
    deployed = (state & STATE_NOT_DEPLOYED) == 0,
  }
end

local function read_all()
  local snapshot = { units = {} }
  local ok, frame = pcall(function() return emu:currentFrame() end)
  if ok then snapshot.frame = frame end

  snapshot.active_unit_id = u8(ACTIVE_UNIT_ID)
  snapshot.map = read_map()
  -- Diagnostic: raw map globals so a nil `map` can be explained (a zeroed
  -- gBmMapTerrain pointer or 0x0 size means no chapter map is loaded).
  snapshot.map_debug = {
    size_w        = u16(MAP.size),
    size_h        = u16(MAP.size + 2),
    gBmMapTerrain = string.format("0x%08X", u32(MAP.terrain)),
    gBmMapUnit    = string.format("0x%08X", u32(MAP.unit)),
  }
  snapshot.terrain_heal = read_terrain_heal()

  -- Per-class terrain tables, keyed by class_id and deduplicated across units
  -- (many units share a class, so we resolve each class only once).
  local classes = {}
  local seen = {}

  for _, f in ipairs(FACTIONS) do
    local units = {}
    for i = 0, f.count - 1 do
      local u = read_unit(f.base + i * UNIT_SIZE)
      if u then
        u.faction = f.name
        if u.class_ptr and u.class_ptr >= ROM_BASE and not seen[u.class_id] then
          seen[u.class_id] = true
          local ct = read_class_terrain(u.class_ptr)
          ct.class_id = u.class_id
          classes[#classes + 1] = ct
        end
        u.class_ptr = nil -- internal only; keep it out of the JSON
        units[#units + 1] = u
      end
    end
    snapshot.units[f.name] = units
  end
  snapshot.classes = classes
  return snapshot
end

-- Console helper: `fe8_dump()` prints the current snapshot to the log.
function fe8_dump()
  console:log(json_encode(read_all()))
end

-- ---------------------------------------------------------------------------
-- TCP server (request/response: any inbound line -> one JSON snapshot line)
-- ---------------------------------------------------------------------------
local server = nil
local clients = {}
local nextID = 1

local function stop(id)
  local s = clients[id]
  clients[id] = nil
  if s then s:close() end
end

local function onReceived(id)
  local sock = clients[id]
  if not sock then return end
  while true do
    local data, err = sock:receive(1024)
    if data and #data > 0 then
      sock:send(json_encode(read_all()) .. "\n")
    else
      if err and err ~= socket.ERRORS.AGAIN then
        console:error("fe8_state: recv error on #" .. id .. ": " .. tostring(err))
        stop(id)
      end
      return
    end
  end
end

local function onError(id, err)
  console:error("fe8_state: socket error on #" .. id .. ": " .. tostring(err))
  stop(id)
end

local function onAccept()
  local sock, err = server:accept()
  if err then
    console:error("fe8_state: accept error: " .. tostring(err))
    return
  end
  local id = nextID
  nextID = nextID + 1
  clients[id] = sock
  sock:add("received", function() onReceived(id) end)
  sock:add("error", function(e) onError(id, e) end)
  console:log("fe8_state: client #" .. id .. " connected")
end

server = socket.bind(nil, PORT)
if server == nil then
  console:error("fe8_state: failed to bind port " .. PORT ..
    " (another script/instance may already own it)")
else
  server:listen()
  server:add("received", onAccept)
  console:log("fe8_state: listening on 127.0.0.1:" .. PORT ..
    " - send any line to receive a JSON snapshot")
end
