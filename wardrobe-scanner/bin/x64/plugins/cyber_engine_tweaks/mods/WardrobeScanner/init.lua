-- Wardrobe Scanner: scan any NPC with the Kiroshi scanner and take the clothes they are wearing into V's inventory.
--
-- How it works (Cyber Engine Tweaks, Lua only, no other dependencies):
--   * NPC clothing is equipped through the game's TransactionSystem in attachment slots (AttachmentSlots.Chest,
--     Torso, Legs, Feet, Head, Eyes, ...), the same system V's clothes use.
--   * On the hotkey, the mod finds the NPC the scanner (or the crosshair) is on, reads the item in every clothing
--     slot of that NPC, and gives V a copy of each clothing item record. Optionally it also registers the item in the
--     wardrobe (transmog) and strips the NPC.
--   * Not every NPC yields items: crowd NPCs and some story characters have their outfit baked into the appearance
--     file instead of equipped items. The Dump hotkey prints what the mod could see so you can tell which case it is.

local Mod = {
    version = '0.1.0',
    settingsFile = 'settings.json',
    settings = {
        requireScanner = true,   -- only act while the scanner (focus mode) is up; off = works on whatever the crosshair is on
        addToWardrobe = true,    -- also register the clothes in the wardrobe so they can be used as transmog
        stripNpc = false,        -- unequip the clothes from the NPC after taking them
        skipOwned = true,        -- do not give a piece V already owns
        autoOnScan = false,      -- take the wardrobe automatically after the scanner rests on an NPC for autoDelay seconds
        autoDelay = 2.0,
        showMessages = true,     -- on-screen confirmation
        debug = false,           -- extra console output
    },
    -- Slots looked at on every NPC, in addition to whatever the NPC's own Character record lists.
    clothingSlots = {
        'AttachmentSlots.Head', 'AttachmentSlots.Eyes', 'AttachmentSlots.Face',
        'AttachmentSlots.Chest', 'AttachmentSlots.Torso', 'AttachmentSlots.Outfit',
        'AttachmentSlots.Legs', 'AttachmentSlots.Feet', 'AttachmentSlots.Hands',
        'AttachmentSlots.UnderwearTop', 'AttachmentSlots.UnderwearBottom',
    },
    -- Item types that count as clothing (gamedataItemType names).
    clothingTypes = {
        Clo_Head = true, Clo_Face = true, Clo_InnerChest = true, Clo_OuterChest = true,
        Clo_Legs = true, Clo_Feet = true, Clo_Outfit = true,
    },
    overlayOpen = false,
    taken = {},          -- entity hash -> true, so auto mode acts once per NPC per session
    autoTarget = nil,    -- {hash = ..., since = seconds}
    clock = 0,
    lastPoll = 0,
}

------------------------------------------------------------------------------------------------------------------------
-- Small helpers
------------------------------------------------------------------------------------------------------------------------

local function log(fmt, ...)
    local text = select('#', ...) > 0 and string.format(fmt, ...) or fmt
    print('[WardrobeScanner] ' .. text)
    if spdlog then spdlog.info('[WardrobeScanner] ' .. text) end
end

local function debug(fmt, ...)
    if Mod.settings.debug then log(fmt, ...) end
end

local function notify(text)
    log(text)
    if not Mod.settings.showMessages then return end
    pcall(function() Game.GetPlayer():SetWarningMessage(text) end)
end

-- CET enum userdata exposes the member name as .value; fall back to tostring for older builds.
local function enumName(value)
    if value == nil then return nil end
    local ok, name = pcall(function() return value.value end)
    if ok and type(name) == 'string' then return name end
    local text = tostring(value)
    return text:match('value%s*=%s*([%w_]+)') or text
end

local function tdbidString(tdbid)
    local ok, text = pcall(TDBID.ToStringDEBUG, tdbid)
    if ok and type(text) == 'string' then return text end
    return tostring(tdbid)
end

local function entityHash(entity)
    local ok, hash = pcall(function() return tostring(entity:GetEntityID().hash) end)
    return ok and hash or nil
end

------------------------------------------------------------------------------------------------------------------------
-- Settings
------------------------------------------------------------------------------------------------------------------------

local function loadSettings()
    local file = io.open(Mod.settingsFile, 'r')
    if not file then return end
    local raw = file:read('*a'); file:close()
    local ok, saved = pcall(json.decode, raw)
    if not ok or type(saved) ~= 'table' then return end
    for key, value in pairs(saved) do
        if Mod.settings[key] ~= nil and type(value) == type(Mod.settings[key]) then Mod.settings[key] = value end
    end
end

local function saveSettings()
    local file = io.open(Mod.settingsFile, 'w')
    if not file then log('Could not write ' .. Mod.settingsFile); return end
    file:write(json.encode(Mod.settings)); file:close()
end

------------------------------------------------------------------------------------------------------------------------
-- Finding the NPC
------------------------------------------------------------------------------------------------------------------------

-- True while the scanner (focus mode) is up. PlayerStateMachine.Vision: 0 Default, 1 Focus.
local function scannerActive(player)
    local ok, active = pcall(function()
        local defs = Game.GetAllBlackboardDefs()
        local psm = Game.GetBlackboardSystem():GetLocalInstanced(player:GetEntityID(), defs.PlayerStateMachine)
        return psm:GetInt(defs.PlayerStateMachine.Vision) == 1
    end)
    return ok and active == true
end

-- The object the scanner is locked on, when the UI_Scanner blackboard exposes it.
local function scannerTarget()
    local ok, entity = pcall(function()
        local defs = Game.GetAllBlackboardDefs()
        local bb = Game.GetBlackboardSystem():Get(defs.UI_Scanner)
        local id = bb:GetEntityID(defs.UI_Scanner.ScannedObject)
        if not id or not EntityID.IsDefined(id) then return nil end
        return Game.FindEntityByID(id)
    end)
    return ok and entity or nil
end

local function isNpc(entity)
    if not entity then return false end
    local ok, result = pcall(function()
        if entity.IsPlayer and entity:IsPlayer() then return false end
        return entity.IsNPC ~= nil and entity:IsNPC()
    end)
    return ok and result == true
end

local function findTargetNpc(player)
    local candidate = scannerTarget()
    if isNpc(candidate) then return candidate, 'scanner' end
    local targeting = Game.GetTargetingSystem()
    candidate = targeting:GetLookAtObject(player, true, false) or targeting:GetLookAtObject(player, false, false)
    if isNpc(candidate) then return candidate, 'crosshair' end
    return nil
end

local function npcName(npc)
    local ok, name = pcall(function()
        local display = npc:GetDisplayName()
        if display and display ~= '' then return display end
        return Game.NameToString(npc:GetTweakDBFullDisplayName())
    end)
    if ok and type(name) == 'string' and name ~= '' then return name end
    return 'NPC'
end

------------------------------------------------------------------------------------------------------------------------
-- Reading the wardrobe
------------------------------------------------------------------------------------------------------------------------

-- Every slot worth checking on this NPC: the fixed clothing list plus the NPC record's own attachment slots.
local function slotsFor(npc)
    local seen, slots = {}, {}
    local function add(id)
        local key = tdbidString(id)
        if key ~= '' and not seen[key] then seen[key] = true; slots[#slots + 1] = {id = id, name = key} end
    end
    for _, name in ipairs(Mod.clothingSlots) do add(TweakDBID.new(name)) end
    pcall(function()
        local record = TweakDB:GetRecord(npc:GetRecordID())
        if not record then return end
        for _, slot in ipairs(record:AttachmentSlots() or {}) do add(slot:GetID()) end
    end)
    return slots
end

-- {tdbid, name, itemType, isClothing} for the record of one item, or nil when the record cannot be read.
local function describeItem(tdbid)
    local info = {tdbid = tdbid, name = tdbidString(tdbid), itemType = '?', isClothing = false, label = nil}
    pcall(function()
        local record = TweakDB:GetRecord(tdbid)
        if not record then return end
        info.itemType = enumName(record:ItemType():Type()) or '?'
        info.isClothing = Mod.clothingTypes[info.itemType] == true
        local ok, label = pcall(function() return Game.GetLocalizedTextByKey(record:DisplayName()) end)
        if ok and type(label) == 'string' and label ~= '' and label ~= '!OBSOLETE' then info.label = label end
    end)
    if info.itemType == '?' then
        -- Record not readable: judge by the slot's name later; a chest/legs/feet slot item is clothing in practice.
        info.unknownType = true
    end
    return info
end

-- When an item's record cannot be read, the slot it sits in decides whether it counts as clothing.
local CLOTHING_SLOT_WORDS = {'Chest', 'Torso', 'Legs', 'Feet', 'Head', 'Outfit', 'Underwear', 'Eyes', 'Face'}
local function clothingSlotName(name)
    for _, word in ipairs(CLOTHING_SLOT_WORDS) do
        if name:find(word, 1, true) then return true end
    end
    return false
end

-- Everything the NPC wears: list of {slot = name, item = describeItem(...)}.
local function readWardrobe(npc)
    local ts = Game.GetTransactionSystem()
    local worn = {}
    for _, slot in ipairs(slotsFor(npc)) do
        local ok, itemObject = pcall(function() return ts:GetItemInSlot(npc, slot.id) end)
        if ok and itemObject then
            local okId, tdbid = pcall(function() return itemObject:GetItemID().tdbid end)
            if okId and tdbid then
                local item = describeItem(tdbid)
                if item.unknownType then item.isClothing = clothingSlotName(slot.name) end
                worn[#worn + 1] = {slot = slot.name, slotId = slot.id, item = item}
            end
        end
    end
    return worn
end

------------------------------------------------------------------------------------------------------------------------
-- Taking it
------------------------------------------------------------------------------------------------------------------------

local function alreadyOwned(player, tdbid)
    local ok, data = pcall(function() return Game.GetTransactionSystem():GetItemDataByTDBID(player, tdbid) end)
    return ok and data ~= nil
end

local function giveClothing(player, tdbid)
    local ts = Game.GetTransactionSystem()
    local itemID = ItemID.FromTDBID(tdbid)
    local ok, given = pcall(function() return ts:GiveItem(player, itemID, 1) end)
    if not (ok and given) then return false end
    if Mod.settings.addToWardrobe then
        pcall(function() Game.GetWardrobeSystem():StoreItemID(itemID) end)
    end
    return true
end

local function stripSlot(npc, slotId)
    pcall(function() Game.GetTransactionSystem():RemoveItemFromSlot(npc, slotId, true, true, true) end)
end

function Mod.takeWardrobe(npc, source)
    local player = Game.GetPlayer()
    local worn = readWardrobe(npc)
    local name = npcName(npc)
    local given, skipped, notClothing = {}, 0, 0
    for _, entry in ipairs(worn) do
        local item = entry.item
        debug('%s: slot %s -> %s (%s)', name, entry.slot, item.name, item.itemType)
        if not item.isClothing then
            notClothing = notClothing + 1
        elseif Mod.settings.skipOwned and alreadyOwned(player, item.tdbid) then
            skipped = skipped + 1
        elseif giveClothing(player, item.tdbid) then
            given[#given + 1] = item.label or item.name
            if Mod.settings.stripNpc then stripSlot(npc, entry.slotId) end
        else
            log('Could not give %s', item.name)
        end
    end
    if #worn == 0 then
        notify(string.format("%s: nothing equipped in clothing slots (outfit is baked into the appearance).", name))
    elseif #given == 0 then
        if skipped > 0 then notify(string.format('%s: you already own all %d piece(s).', name, skipped))
        else notify(string.format('%s: no clothing items found (%d non-clothing item(s) seen).', name, notClothing)) end
    else
        notify(string.format('Took %d piece(s) from %s: %s', #given, name, table.concat(given, ', ')))
    end
    debug('source: %s, worn %d, given %d, skipped %d, not clothing %d', source or '?', #worn, #given, skipped, notClothing)
    return #given
end

function Mod.dumpTarget()
    local player = Game.GetPlayer()
    local npc = findTargetNpc(player)
    if not npc then log('Dump: no NPC under the scanner or crosshair.'); return end
    local name = npcName(npc)
    local okRecord, record = pcall(function() return tdbidString(npc:GetRecordID()) end)
    log('Dump for %s (%s):', name, okRecord and record or 'record unknown')
    local worn = readWardrobe(npc)
    if #worn == 0 then log('  no items in any attachment slot'); return end
    for _, entry in ipairs(worn) do
        log('  %-32s %-50s type=%s clothing=%s %s', entry.slot, entry.item.name, entry.item.itemType,
            tostring(entry.item.isClothing), entry.item.label or '')
    end
end

function Mod.takeFromTarget()
    local player = Game.GetPlayer()
    if not player then return end
    if Mod.settings.requireScanner and not scannerActive(player) then
        notify('Open the scanner on an NPC first.')
        return
    end
    local npc, source = findTargetNpc(player)
    if not npc then notify('No NPC under the scanner.'); return end
    Mod.takeWardrobe(npc, source)
    local hash = entityHash(npc)
    if hash then Mod.taken[hash] = true end
end

------------------------------------------------------------------------------------------------------------------------
-- Auto mode: take once the scanner has rested on the same NPC for autoDelay seconds
------------------------------------------------------------------------------------------------------------------------

local function updateAuto(delta)
    Mod.clock = Mod.clock + delta
    if Mod.clock - Mod.lastPoll < 0.25 then return end
    Mod.lastPoll = Mod.clock
    local player = Game.GetPlayer()
    if not player or not scannerActive(player) then Mod.autoTarget = nil; return end
    local npc = findTargetNpc(player)
    local hash = npc and entityHash(npc) or nil
    if not hash then Mod.autoTarget = nil; return end
    if not Mod.autoTarget or Mod.autoTarget.hash ~= hash then
        Mod.autoTarget = {hash = hash, since = Mod.clock}
        return
    end
    if Mod.taken[hash] then return end
    if Mod.clock - Mod.autoTarget.since >= Mod.settings.autoDelay then
        Mod.taken[hash] = true
        Mod.takeWardrobe(npc, 'auto')
    end
end

------------------------------------------------------------------------------------------------------------------------
-- CET wiring
------------------------------------------------------------------------------------------------------------------------

registerForEvent('onInit', function()
    loadSettings()
    log('v%s loaded', Mod.version)
end)

registerHotkey('ws_take_wardrobe', "Wardrobe Scanner: take scanned NPC's clothes", function() Mod.takeFromTarget() end)
registerHotkey('ws_dump_target', 'Wardrobe Scanner: dump target slots to console', function() Mod.dumpTarget() end)

registerForEvent('onUpdate', function(delta)
    if Mod.settings.autoOnScan then updateAuto(delta) end
end)

registerForEvent('onOverlayOpen', function() Mod.overlayOpen = true end)
registerForEvent('onOverlayClose', function() Mod.overlayOpen = false end)

registerForEvent('onDraw', function()
    if not Mod.overlayOpen then return end
    if not ImGui.Begin('Wardrobe Scanner', ImGuiWindowFlags.AlwaysAutoResize) then ImGui.End(); return end
    local s, changed = Mod.settings, false
    local function checkbox(label, key, tip)
        local value, pressed = ImGui.Checkbox(label, s[key])
        if pressed then s[key] = value; changed = true end
        if tip and ImGui.IsItemHovered() then ImGui.SetTooltip(tip) end
    end
    checkbox('Require scanner', 'requireScanner', 'Only act while the Kiroshi scanner is up. Off: whatever the crosshair is on.')
    checkbox('Add to wardrobe (transmog)', 'addToWardrobe', 'Register taken pieces in the wardrobe as well as the backpack.')
    checkbox('Strip the NPC', 'stripNpc', 'Unequip the pieces from the NPC after taking them.')
    checkbox('Skip pieces already owned', 'skipOwned')
    checkbox('Automatic while scanning', 'autoOnScan', 'Take the wardrobe once the scanner rests on an NPC for the delay below.')
    if s.autoOnScan then
        local value, pressed = ImGui.SliderFloat('Delay (s)', s.autoDelay, 0.5, 6.0, '%.1f')
        if pressed then s.autoDelay = value; changed = true end
    end
    checkbox('On-screen messages', 'showMessages')
    checkbox('Debug logging', 'debug')
    ImGui.Separator()
    if ImGui.Button('Take now') then Mod.takeFromTarget() end
    ImGui.SameLine()
    if ImGui.Button('Dump target') then Mod.dumpTarget() end
    ImGui.TextDisabled('Bind the hotkeys in CET > Bindings.')
    ImGui.End()
    if changed then saveSettings() end
end)

return Mod
