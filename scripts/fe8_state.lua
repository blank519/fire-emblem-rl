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

-- ---------------------------------------------------------------------------
-- Memory helpers
-- ---------------------------------------------------------------------------
local function u8(a)  return emu:read8(a)  end
local function u16(a) return emu:read16(a) end
local function u32(a) return emu:read32(a) end

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
local function read_items(base)
  local items = {}
  for i = 0, 4 do
    local v = u16(base + O.items + i * 2)
    local id = v & 0xFF
    if id ~= 0 then
      items[#items + 1] = { id = id, uses = (v >> 8) & 0xFF, slot = i }
    end
  end
  return items
 end

local function read_weapon(itemId)
  local p = ITEM_TABLE + itemId * ITEM_SIZE
  return {
    id     = itemId,
    type   = u8(p + IO.weaponType),
    might  = u8(p + IO.might),
    hit    = u8(p + IO.hit),
    weight = u8(p + IO.weight),
    crit   = u8(p + IO.crit),
  }
end

-- The equipped weapon is the first inventory item flagged as a weapon.
local function resolve_weapon(items)
  for _, it in ipairs(items) do
    local attr = u32(ITEM_TABLE + it.id * ITEM_SIZE + IO.attributes)
    if (attr & IA_WEAPON) ~= 0 then
      return read_weapon(it.id)
    end
  end
  return nil
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
  if pChar >= ROM_BASE  then charId  = u8(pChar  + ID_OFFSET) end
  if pClass >= ROM_BASE then classId = u8(pClass + ID_OFFSET) end

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
    con      = u8(base + O.con),
    move     = u8(base + O.move),
    items    = items,
    weapon   = resolve_weapon(items),
    state    = state,
    dead     = (state & STATE_DEAD) ~= 0,
    deployed = (state & STATE_NOT_DEPLOYED) == 0,
  }
end

local function read_all()
  local snapshot = { units = {} }
  local ok, frame = pcall(function() return emu:currentFrame() end)
  if ok then snapshot.frame = frame end
  for _, f in ipairs(FACTIONS) do
    local units = {}
    for i = 0, f.count - 1 do
      local u = read_unit(f.base + i * UNIT_SIZE)
      if u then
        u.faction = f.name
        units[#units + 1] = u
      end
    end
    snapshot.units[f.name] = units
  end
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
