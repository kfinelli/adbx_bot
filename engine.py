"""
Game engine for the async dungeon crawler.

All functions operate on GameState in-place and return an EngineResult.
No I/O, no platform dependencies.

Convention:
  - Functions that succeed set EngineResult.ok = True and populate .message
  - Functions that fail set EngineResult.ok = False and populate .error
  - .state always points to the (possibly mutated) GameState
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, UTC
from uuid import UUID

from models import (
    NPC,
    AbilityScores,
    Character,
    CharacterClass,
    CharacterStatus,
    DoorState,
    Dungeon,
    Exit,
    GameState,
    InventoryItem,
    LightSource,
    PlayerTurnSubmission,
    Room,
    SessionMode,
    SpellBook,
    TurnRecord,
    TurnStatus,
)
from tables import (
    ABILITY_MODIFIERS,
    CON_HP_MODIFIER,
    CREATION_RULES,
    EQUIPMENT_PACKAGES,
    PACK_BONUS_DEFAULT,
    get_saving_throws,
    get_spell_slots,
)
from validation import (
    validate_hp_value,
    validate_non_empty_string,
    validate_description,
    validate_turn_hours,
    validate_door_state,
    validate_uuid_string,
    validate_positive_int,
)

# ---------------------------------------------------------------------------
# Timezone-aware UTC now (replaces deprecated _now())
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# EngineResult — the return type for all engine functions
# ---------------------------------------------------------------------------

@dataclass
class EngineResult:
    ok:        bool             = True
    message:   str              = ""   # narrative / confirmation text for the platform to display
    error:     str              = ""   # human-readable error if ok=False
    state:     GameState | None = None
    notify_dm: bool             = False  # platform should notify DM to resolve
    data:      any              = None  # optional additional data (e.g., Oracle object for platform layer)


def _ok(state: GameState, message: str = "", notify_dm: bool = False) -> EngineResult:
    return EngineResult(ok=True, message=message, state=state, notify_dm=notify_dm)

def _err(state: GameState, error: str) -> EngineResult:
    return EngineResult(ok=False, error=error, state=state)


# ---------------------------------------------------------------------------
# Dice helpers
# ---------------------------------------------------------------------------

def print_dice_results(results):
  diceOutput = ""
  for die in results['dice']:
    diceOutput += str(die)+", "
  diceOutput= diceOutput.rstrip(", ")
  print("Dice: ", diceOutput)
  print("Bonus: ", results['bonus'])
  print("Total: ", results['total'])

#  Rolls a XdY+Z expression (strict order)
#  or returns a number if a number is given
#    Returns a dictionary with the following keys:
#    'bonus': bonus applied to the roll
#    'dice': list of all rolled dice
#    'total': total of all rolled dice
def roll_dice_expr(expr):
    xyz = re.split(r'd|\+', expr)
    x = int(xyz[0])
    if len(xyz) == 1:
        return {'dice':{x},'total':x, 'bonus':0}
    if "d" not in expr:
        z = int(xyz[1])
        return {'dice': {x}, 'total': x + z, 'bonus': z}
    y = int(xyz[1])
    if len(xyz) == 2 and y != 0:
        return roll_expr(x, y)
    z = 0
    if len(xyz) > 2:
        z = int(xyz[2])
    if y == 0:
        return {'dice': {x}, 'total': x+z, 'bonus': z}
    return roll_expr(x,y,z)

def roll_expr(dCount, dSize, bonus=0):
    result = {'dice':[],'bonus':bonus,'total':bonus}
    list = [0] * dCount
    for i in range(int(dCount)):
        list[i] = d(dSize)
        result['total'] += list[i]
    result['dice'] = list
    return result

def d(x):
    return random.randint(1, int(x))

def roll(n: int, sides: int) -> list[int]:
    """Roll n dice of `sides` sides, return individual results."""
    return [random.randint(1, sides) for _ in range(n)]

def roll_sum(n: int, sides: int) -> int:
    return sum(roll(n, sides))

def roll_3d6() -> int:
    return roll_sum(3, 6)

def roll_stat_block() -> AbilityScores:
    """Roll 3d6 straight down the line."""
    return AbilityScores(
        strength=roll_3d6(),
        intelligence=roll_3d6(),
        wisdom=roll_3d6(),
        dexterity=roll_3d6(),
        constitution=roll_3d6(),
        charisma=roll_3d6(),
    )

def roll_stats() -> dict:
    """
    Public helper: roll 3d6 stats and return as a plain dict.
    Used by the /arrive DM conversation before character creation.
    """
    block = roll_stat_block()
    return {
        "strength":     block.strength,
        "intelligence": block.intelligence,
        "wisdom":       block.wisdom,
        "dexterity":    block.dexterity,
        "constitution": block.constitution,
        "charisma":     block.charisma,
    }


def _con_modifier(con: int) -> int:
    return CON_HP_MODIFIER.get(con, 0)

def _dex_ac_modifier(dex: int) -> int:
    """DEX modifier to AC (subtract from descending AC)."""
    return ABILITY_MODIFIERS.get(dex, 0)


# ---------------------------------------------------------------------------
# Character creation
# ---------------------------------------------------------------------------

def create_character(
    state:           GameState,
    name:            str,
    character_class: CharacterClass,
    equipment_package: str,
    owner_id:        str | None = None,
    ability_scores:  AbilityScores | None = None,   # pre-rolled, or we roll
    prerolled_stats: dict | None = None,             # from roll_stats() dict
) -> EngineResult:
    """
    Create a new level-1 character, add them to the session, and return the result.
    If prerolled_stats (dict) is provided, converts to AbilityScores and uses those.
    If ability_scores is provided directly, uses that.
    Otherwise rolls 3d6 straight.
    """
    if prerolled_stats is not None:
        ability_scores = AbilityScores(**prerolled_stats)
    if not name.strip():
        return EngineResult(ok=False, error="Character name cannot be empty.", state=state)

    if equipment_package not in EQUIPMENT_PACKAGES:
        return EngineResult(
            ok=False,
            error=f"Unknown equipment package '{equipment_package}'. "
                  f"Valid options: {list(EQUIPMENT_PACKAGES.keys())}",
            state=state,
        )

    # --- Ability scores
    scores = ability_scores if ability_scores is not None else roll_stat_block()

    # --- Look up class rules — all class-specific values come from here
    rules = CREATION_RULES[character_class]

    # --- HP: roll hit die, add CON modifier, minimum 1
    hp_roll = roll_sum(1, rules.hit_die)
    con_mod = _con_modifier(scores.constitution)
    hp_max  = max(1, hp_roll + con_mod)

    # --- AC: class base AC, DEX modifier applied on top
    dex_mod = _dex_ac_modifier(scores.dexterity)
    base_ac = rules.base_ac - dex_mod

    # --- Saving throws from class rules
    saves = get_saving_throws(character_class, 1)

    # --- Spell book (spellcasters only, determined by class rules)
    spellbook: SpellBook | None = None
    if rules.is_spellcaster:
        slots = get_spell_slots(character_class, 1)
        spellbook = SpellBook(
            max_slots=slots,
            prepared=[[] for _ in range(6)],
            known_spells=[],
        )

    # --- Inventory from equipment package
    raw_items = list(EQUIPMENT_PACKAGES[equipment_package])
    # Add class-specific bonus item for this pack if defined, else use default
    bonus = rules.pack_bonus_items.get(equipment_package) \
        or PACK_BONUS_DEFAULT.get(equipment_package)
    if bonus:
        raw_items.append(bonus)
    inventory = []
    for item_name, qty, enc in raw_items:
        inventory.append(InventoryItem(
            name=item_name,
            quantity=qty,
            encumbrance=enc,
        ))

    character = Character(
        owner_id=owner_id,
        name=name,
        character_class=character_class,
        level=1,
        experience=0,
        ability_scores=scores,
        hp_max=hp_max,
        hp_current=hp_max,
        armor_class=base_ac,
        movement_speed=rules.base_movement,
        saving_throws=saves,
        spellbook=spellbook,
        inventory=inventory,
    )

    state.characters[character.character_id] = character

    # Add to party if one exists
    if state.party is not None:
        state.party.member_ids.append(character.character_id)

    state.updated_at = _now()

    msg = (
        f"{name} the {character_class.value} joins the party!\n"
        f"HP: {hp_max}  AC: {base_ac}  STR {scores.strength} INT {scores.intelligence} "
        f"WIS {scores.wisdom} DEX {scores.dexterity} CON {scores.constitution} "
        f"CHA {scores.charisma}"
    )
    return _ok(state, msg)


# ---------------------------------------------------------------------------
# Turn management
# ---------------------------------------------------------------------------

def open_turn(
    state:  GameState,
    due_at: datetime | None = None,
) -> EngineResult:
    """
    Open a new dungeon turn (or combat round).
    If due_at is not provided, uses state.default_turn_hours from now.
    Fails if a turn is already open.
    """
    if state.current_turn is not None and state.current_turn.status == TurnStatus.OPEN:
        return _err(state, "A turn is already open. Close or resolve it first.")

    if due_at is None:
        due_at = _now() + timedelta(hours=state.default_turn_hours)

    turn = TurnRecord(
        turn_number=state.turn_number,
        mode=state.mode,
        status=TurnStatus.OPEN,
        opened_at=_now(),
        due_at=due_at,
    )
    state.current_turn = turn
    state.updated_at = _now()
    return _ok(state, f"Turn {state.turn_number} is now open.")


def submit_turn(
    state:        GameState,
    character_id: UUID,
    action_text:  str,
) -> EngineResult:
    """
    Submit (or resubmit) a player's action for the current open turn.
    Previous submissions by this character are marked superseded.
    """
    if not state.session_active:
        return _err(state, "The session is on hold.")
    if state.mode == SessionMode.PRE_START:
        return _err(state, "The session has not started yet.")
    if state.current_turn is None or state.current_turn.status != TurnStatus.OPEN:
        mode_str = "round" if state.mode == SessionMode.ROUNDS else "turn"
        return _err(state, f"No open {mode_str} to submit to. The DM needs to resolve the previous {mode_str} first.")

    char = state.characters.get(character_id)
    if char is None:
        return _err(state, f"Character {character_id} not found.")
    if char.status != CharacterStatus.ACTIVE:
        return _err(state, f"{char.name} is not active and cannot submit a turn.")

    # Supersede any prior submissions from this character
    for sub in state.current_turn.submissions:
        if sub.character_id == character_id:
            sub.is_latest = False

    state.current_turn.submissions.append(PlayerTurnSubmission(
        character_id=character_id,
        submitted_at=_now(),
        action_text=action_text,
        is_latest=True,
    ))
    state.updated_at = _now()

    # Auto-close if all active party members have submitted
    if state.party is not None:
        submitted_ids = {
            s.character_id for s in state.current_turn.submissions if s.is_latest
        }
        active_ids = {
            cid for cid in state.party.member_ids
            if state.characters.get(cid) and
               state.characters[cid].status == CharacterStatus.ACTIVE
        }
        if active_ids and active_ids.issubset(submitted_ids):
            state.current_turn.status = TurnStatus.CLOSED
            state.current_turn.closed_at = _now()
            return _ok(state, f"{char.name}: \"{action_text}\"", notify_dm=True)

    return _ok(state, f"{char.name}: \"{action_text}\"")


def close_turn(state: GameState) -> EngineResult:
    """
    Close the current turn for submissions (DM is now arbitrating).
    Does not yet resolve or advance the turn counter.
    """
    if state.current_turn is None:
        return _err(state, "No current turn to close.")
    if state.current_turn.status != TurnStatus.OPEN:
        return _err(state, "Current turn is not open.")

    state.current_turn.status = TurnStatus.CLOSED
    state.current_turn.closed_at = _now()
    state.updated_at = _now()
    return _ok(state, f"Turn {state.turn_number} closed. Awaiting DM resolution.")


def resolve_turn(
    state:      GameState,
    resolution: str,
) -> EngineResult:
    """
    DM resolves the current turn with a narrative description.
    Snapshots state, moves turn to history, advances turn counter,
    and ticks down the active light source.
    """
    if state.current_turn is None:
        return _err(state, "No current turn to resolve.")
    if state.current_turn.status not in (TurnStatus.OPEN, TurnStatus.CLOSED):
        return _err(state, "Current turn is already resolved.")

    turn = state.current_turn

    # Snapshot state before mutation (shallow-ish; good enough for history log)
    turn.state_snapshot = _snapshot(state)
    turn.resolution     = resolution
    turn.status         = TurnStatus.RESOLVED
    turn.resolved_at    = _now()

    # Move to history
    state.turn_history.append(turn)
    state.current_turn = None

    # Clear say log and reset oracle counter for next turn
    state.say_log = []
    state.oracle_counter = 0

    # Advance turn counter and tick light source (exploration mode only)
    state.turn_number += 1
    if state.mode == SessionMode.EXPLORATION:
        _tick_light(state)

    state.updated_at = _now()
    return _ok(state, resolution)


def _snapshot(state: GameState) -> dict:
    """Produce a lightweight serializable snapshot of key state for history."""
    room = state.current_room
    return {
        "turn_number":  state.turn_number,
        "mode":         state.mode.value,
        "room_name":    room.name if room else None,
        "party_gold":   state.party.gold if state.party else 0,
        "characters":   {
            str(cid): {
                "name":       c.name,
                "hp_current": c.hp_current,
                "hp_max":     c.hp_max,
                "status":     c.status.value,
            }
            for cid, c in state.characters.items()
        },
        "npcs": [
            {"name": n.name, "hp_current": n.hp_current, "status": n.status}
            for n in state.npcs
        ],
    }


# ---------------------------------------------------------------------------
# Light source management
# ---------------------------------------------------------------------------

def _tick_light(state: GameState) -> None:
    """Decrement the active light source by one turn. Called by resolve_turn."""
    if state.party is None:
        return
    light = state.party.active_light
    if light is None or light.turns_remaining is None:
        return  # no light, or permanent/magical source
    light.turns_remaining = max(0, light.turns_remaining - 1)


def set_light_source(
    state:           GameState,
    label:           str,
    turns_remaining: int | None,
) -> EngineResult:
    """
    DM command: set the active light source.
    Deactivates all previous sources and creates a new active one.
    """
    if state.party is None:
        return _err(state, "No active party.")
    
    # Validate label
    label_result = validate_non_empty_string(label, "Light source label", max_length=50)
    if not label_result:
        return _err(state, label_result.error)
    
    # Validate turns_remaining if provided (allow 0 for exhausted lights)
    if turns_remaining is not None and turns_remaining >= 0:
        if turns_remaining < 0:
            return _err(state, "Turns remaining cannot be negative.")
        if turns_remaining > 1000:
            return _err(state, "Turns remaining cannot exceed 1000.")
    
    for ls in state.party.light_sources:
        ls.is_active = False
    new_light = LightSource(
        label=label_result.value,
        turns_remaining=turns_remaining,
        is_active=True,
    )
    state.party.light_sources.append(new_light)
    state.updated_at = _now()
    duration = f"{turns_remaining} turns" if turns_remaining is not None else "permanent"
    return _ok(state, f"Light source set: {label_result.value} ({duration}).")


# ---------------------------------------------------------------------------
# DM mutations — characters
# ---------------------------------------------------------------------------

def set_character_hp(
    state:        GameState,
    character_id: UUID,
    new_hp:       int,
) -> EngineResult:
    char = state.characters.get(character_id)
    if char is None:
        return _err(state, f"Character {character_id} not found.")
    
    # Validate HP value
    hp_result = validate_hp_value(new_hp, max_hp=char.hp_max)
    if not hp_result:
        return _err(state, hp_result.error)
    
    old = char.hp_current
    char.hp_current = hp_result.value
    if char.hp_current == 0:
        char.status = CharacterStatus.DEAD
    state.updated_at = _now()
    return _ok(state, f"{char.name} HP: {old} → {char.hp_current}/{char.hp_max}.")


def set_character_status(
    state:        GameState,
    character_id: UUID,
    status:       CharacterStatus,
    notes:        str = "",
) -> EngineResult:
    char = state.characters.get(character_id)
    if char is None:
        return _err(state, f"Character {character_id} not found.")
    
    # Validate notes length
    notes_result = validate_description(notes, "Status notes", max_length=200, allow_empty=True)
    if not notes_result:
        return _err(state, notes_result.error)
    
    char.status = status
    char.status_notes = notes_result.value
    state.updated_at = _now()
    return _ok(state, f"{char.name} status → {status.value}. {notes_result.value}".strip())


# ---------------------------------------------------------------------------
# DM mutations — NPCs
# ---------------------------------------------------------------------------

def add_npc(state: GameState, npc: NPC) -> EngineResult:
    """Add an NPC to the current room. NPC should already be validated before calling."""
    state.npcs.append(npc)
    state.updated_at = _now()
    return _ok(state, f"{npc.name} appears.")


def set_npc_hp(
    state:  GameState,
    npc_id: UUID,
    new_hp: int,
) -> EngineResult:
    npc = _find_npc(state, npc_id)
    if npc is None:
        return _err(state, f"NPC {npc_id} not found.")
    
    # Validate HP value
    hp_result = validate_hp_value(new_hp)
    if not hp_result:
        return _err(state, hp_result.error)
    
    old = npc.hp_current
    npc.hp_current = hp_result.value
    if npc.hp_current == 0:
        npc.status = "dead"
    state.updated_at = _now()
    return _ok(state, f"{npc.name} HP: {old} → {npc.hp_current}/{npc.hp_max}.")


def set_npc_status(
    state:  GameState,
    npc_id: UUID,
    status: str,
) -> EngineResult:
    npc = _find_npc(state, npc_id)
    if npc is None:
        return _err(state, f"NPC {npc_id} not found.")
    
    # Validate status string
    status_result = validate_non_empty_string(status, "NPC status", max_length=50)
    if not status_result:
        return _err(state, status_result.error)
    
    npc.status = status_result.value
    state.updated_at = _now()
    return _ok(state, f"{npc.name} status → {status_result.value}.")


def remove_npc(state: GameState, npc_id: UUID) -> EngineResult:
    npc = _find_npc(state, npc_id)
    if npc is None:
        return _err(state, f"NPC {npc_id} not found.")
    state.npcs = [n for n in state.npcs if n.npc_id != npc_id]
    state.updated_at = _now()
    return _ok(state, f"{npc.name} removed from room.")


def _find_npc(state: GameState, npc_id: UUID) -> NPC | None:
    for n in state.npcs:
        if n.npc_id == npc_id:
            return n
    return None


# ---------------------------------------------------------------------------
# DM mutations — rooms and features
# ---------------------------------------------------------------------------

def set_turn_number(state: GameState, turn_number: int) -> EngineResult:
    """Directly set the session turn counter. DM correction tool."""
    if turn_number < 0:
        return _err(state, "Turn number cannot be negative.")
    state.turn_number = turn_number
    if state.current_turn:
        state.current_turn.turn_number = turn_number
    state.updated_at = _now()
    return _ok(state, f"Turn number set to {turn_number}.")


def update_room(
    state:       GameState,
    room_id:     UUID,
    name:        str,
    description: str,
    notes:       str = "",
) -> EngineResult:
    """Edit the name, description, and DM notes of an existing room."""
    if not name.strip():
        return _err(state, "Room name cannot be empty.")
    room = _resolve_room(state, room_id)
    if room is None:
        return _err(state, f"Room {room_id} not found.")
    room.name        = name.strip()
    room.description = description
    room.notes       = notes
    state.updated_at = _now()
    return _ok(state, f"Room updated: {room.name}.")


def delete_feature(
    state:      GameState,
    feature_id: UUID,
    room_id:    UUID | None = None,
) -> EngineResult:
    room = _resolve_room(state, room_id)
    if room is None:
        return _err(state, "No current room.")
    before = len(room.features)
    room.features = [f for f in room.features if f.feature_id != feature_id]
    if len(room.features) == before:
        return _err(state, f"Feature {feature_id} not found.")
    state.updated_at = _now()
    return _ok(state, "Feature deleted.")


def update_feature(
    state:       GameState,
    feature_id:  UUID,
    name:        str,
    description: str,
    state_str:   str,
    notes:       str = "",
    room_id:     UUID | None = None,
) -> EngineResult:
    room = _resolve_room(state, room_id)
    if room is None:
        return _err(state, "No current room.")
    feat = next((f for f in room.features if f.feature_id == feature_id), None)
    if feat is None:
        return _err(state, f"Feature {feature_id} not found.")
    if not name.strip():
        return _err(state, "Feature name cannot be empty.")
    feat.name        = name.strip()
    feat.description = description
    feat.state       = state_str
    feat.notes       = notes
    state.updated_at = _now()
    return _ok(state, f"Feature updated: {feat.name}.")


def delete_exit(
    state:   GameState,
    exit_id: UUID,
    room_id: UUID | None = None,
) -> EngineResult:
    room = _resolve_room(state, room_id)
    if room is None:
        return _err(state, "No current room.")
    before = len(room.exits)
    room.exits = [e for e in room.exits if e.exit_id != exit_id]
    if len(room.exits) == before:
        return _err(state, f"Exit {exit_id} not found.")
    state.updated_at = _now()
    return _ok(state, "Exit deleted.")


def update_exit(
    state:          GameState,
    exit_id:        UUID,
    label:          str,
    description:    str,
    door_state,                       # DoorState
    destination_id: UUID | None = None,
    notes:          str = "",
    room_id:        UUID | None = None,
) -> EngineResult:
    room = _resolve_room(state, room_id)
    if room is None:
        return _err(state, "No current room.")
    ex = next((e for e in room.exits if e.exit_id == exit_id), None)
    if ex is None:
        return _err(state, f"Exit {exit_id} not found.")
    if not label.strip():
        return _err(state, "Exit label cannot be empty.")
    ex.label          = label.strip()
    ex.description    = description
    ex.door_state     = door_state
    ex.destination_id = destination_id
    ex.notes          = notes
    state.updated_at  = _now()
    return _ok(state, f"Exit updated: {ex.label}.")


def update_npc(
    state:       GameState,
    npc_id:      UUID,
    name:        str,
    description: str,
    hp_max:      int,
    hp_current:  int,
    armor_class: int,
    notes:       str = "",
) -> EngineResult:
    npc = _find_npc(state, npc_id)
    if npc is None:
        return _err(state, f"NPC {npc_id} not found.")
    
    # Validate name
    name_result = validate_non_empty_string(name, "NPC name", max_length=50)
    if not name_result:
        return _err(state, name_result.error)
    
    # Validate HP values
    hp_max_result = validate_hp_value(hp_max)
    if not hp_max_result:
        return _err(state, hp_max_result.error)
    
    hp_current_result = validate_hp_value(hp_current, max_hp=hp_max_result.value)
    if not hp_current_result:
        return _err(state, hp_current_result.error)
    
    # Validate AC
    ac_result = validate_positive_int(armor_class, "Armor class", min_value=1, max_value=20)
    if not ac_result:
        return _err(state, ac_result.error)
    
    # Validate description and notes
    desc_result = validate_description(description, "NPC description", max_length=500, allow_empty=True)
    if not desc_result:
        return _err(state, desc_result.error)
    
    notes_result = validate_description(notes, "NPC notes", max_length=500, allow_empty=True)
    if not notes_result:
        return _err(state, notes_result.error)
    
    npc.name        = name_result.value
    npc.description = desc_result.value
    npc.hp_max      = hp_max_result.value
    npc.hp_current  = hp_current_result.value
    npc.armor_class = ac_result.value
    npc.notes       = notes_result.value
    state.updated_at = _now()
    return _ok(state, f"NPC updated: {npc.name}.")

def register_room(state: GameState, room: Room) -> EngineResult:
    """
    Add a room to the dungeon graph without moving the party into it.
    Used by the web UI when authoring rooms before or during a session.
    To also move the party in, call move_party_to_room() afterwards.
    """
    if state.dungeon is None:
        state.dungeon = Dungeon(name="The Dungeon")
    
    # Validate room name
    if not room.name or not room.name.strip():
        return _err(state, "Room name cannot be empty.")
    
    # Validate room description length
    if room.description and len(room.description) > 1000:
        return _err(state, "Room description exceeds maximum length of 1000 characters.")
    
    # Validate room notes length
    if room.notes and len(room.notes) > 2000:
        return _err(state, "Room notes exceed maximum length of 2000 characters.")
    
    state.dungeon.rooms[room.room_id] = room
    state.updated_at = _now()
    return _ok(state, f"Room '{room.name}' added to dungeon.")


def set_room(state: GameState, room: Room) -> EngineResult:
    """
    DM creates a new room on the fly and immediately moves the party in.
    Adds it to the dungeon graph, sets it as current, and clears NPCs.
    Used by the /dm_setroom slash command (no room_id) path.
    For web UI room creation, use register_room() instead.
    """
    result = register_room(state, room)
    if not result.ok:
        return result
    
    state.current_room_id = room.room_id
    room.visited = True
    state.npcs = []
    state.updated_at = _now()
    return _ok(state, f"Entered: {room.name}.")


def move_party_to_room(state: GameState, room_id: UUID) -> EngineResult:
    """
    Move the party into an already-authored room in the dungeon graph.

    - Looks up the room by ID; fails if not found.
    - Marks the room visited.
    - Clears state.npcs (session-transient; DM repopulates as needed).
    - Does NOT modify the room's features, exits, or any other authored data.
    """
    if state.dungeon is None:
        return _err(state, "No dungeon loaded.")
    room = state.dungeon.rooms.get(room_id)
    if room is None:
        return _err(state, f"Room {room_id} not found in dungeon.")
    state.current_room_id = room_id
    room.visited = True
    state.npcs = []
    state.updated_at = _now()
    return _ok(state, f"Entered: {room.name}.")


def _resolve_room(state: GameState, room_id: UUID | None) -> object | None:
    """Return the room for room_id if given, else the party's current room."""
    if room_id is not None:
        if state.dungeon is None:
            return None
        return state.dungeon.rooms.get(room_id)
    return state.current_room


def set_feature_state(
    state:      GameState,
    feature_id: UUID,
    new_state:  str,
    room_id:    UUID | None = None,
) -> EngineResult:
    """Update the state string of a room feature.
    If room_id is provided, operates on that room; otherwise uses the current room."""
    room = _resolve_room(state, room_id)
    if room is None:
        return _err(state, "No current room.")
    
    # Validate new state
    state_result = validate_non_empty_string(new_state, "Feature state", max_length=100)
    if not state_result:
        return _err(state, state_result.error)
    
    for feat in room.features:
        if feat.feature_id == feature_id:
            feat.state = state_result.value
            state.updated_at = _now()
            return _ok(state, f"{feat.name} → {state_result.value}.")
    return _err(state, f"Feature {feature_id} not found.")


def set_exit_state(
    state:    GameState,
    exit_id:  UUID,
    new_state,            # DoorState
    room_id:  UUID | None = None,
) -> EngineResult:
    """If room_id is provided, operates on that room; otherwise uses the current room."""
    room = _resolve_room(state, room_id)
    if room is None:
        return _err(state, "No current room.")
    
    # Validate door state
    state_result = validate_door_state(new_state)
    if not state_result:
        return _err(state, state_result.error)
    
    for ex in room.exits:
        if ex.exit_id == exit_id:
            ex.door_state = state_result.value
            state.updated_at = _now()
            return _ok(state, f"Exit '{ex.label}' → {state_result.value.value}.")
    return _err(state, f"Exit {exit_id} not found.")


def add_exit(
    state:       GameState,
    label:       str,
    description: str,
    door_state=DoorState.OPEN,
    notes:       str = "",
    room_id:     UUID | None = None,
) -> EngineResult:
    """DM adds a new exit. If room_id is provided, adds to that room; else current room."""
    room = _resolve_room(state, room_id)
    if room is None:
        return _err(state, "No current room.")
    
    # Validate label
    label_result = validate_non_empty_string(label, "Exit label", max_length=50)
    if not label_result:
        return _err(state, label_result.error)
    
    # Validate description
    desc_result = validate_description(description, "Exit description", max_length=200)
    if not desc_result:
        return _err(state, desc_result.error)
    
    # Validate door state
    door_result = validate_door_state(door_state)
    if not door_result:
        return _err(state, door_result.error)
    
    # Validate notes
    notes_result = validate_description(notes, "Exit notes", max_length=500, allow_empty=True)
    if not notes_result:
        return _err(state, notes_result.error)
    
    exit_ = Exit(
        label=label_result.value,
        description=desc_result.value,
        door_state=door_result.value,
        notes=notes_result.value,
    )
    room.exits.append(exit_)
    n = len(room.exits)
    state.updated_at = _now()
    return _ok(state, f"Exit {n} added: {label_result.value}.")


def abscond(
    state:        GameState,
    character_id: UUID,
    exit_number:  int,
) -> EngineResult:
    """
    Party leader moves the group through a numbered exit.

    - Only the party leader may call this.
    - Exit must not be locked or stuck.
    - Clears all existing turn submissions and replaces them with a
      single movement submission, then closes the turn so the DM
      sees it as ready to resolve.
    - Does NOT resolve the turn — DM still uses /dm_resolve.
    """
    if state.party is None:
        return _err(state, "No active party.")
    if state.mode == SessionMode.PRE_START:
        return _err(state, "The session has not started yet.")
    if state.party.leader_id != character_id:
        return _err(state, "Only the party leader can use /abscond.")

    room = state.current_room
    if room is None:
        return _err(state, "No current room.")
    if not room.exits:
        return _err(state, "No exits in this room.")

    idx = exit_number - 1
    if idx < 0 or idx >= len(room.exits):
        return _err(state, f"Exit {exit_number} does not exist. There are {len(room.exits)} exit(s).")

    exit_ = room.exits[idx]
    if exit_.door_state in (DoorState.LOCKED, DoorState.STUCK):
        return _err(state, f"The {exit_.label} exit is {exit_.door_state.value} and cannot be used.")

    if state.current_turn is None:
        open_turn(state)

    # Clear existing submissions — leader overrides
    for sub in state.current_turn.submissions:
        sub.is_latest = False

    leader = state.characters.get(character_id)
    leader_name = leader.name if leader else "Party leader"
    action = f"leads the party through exit {exit_number}: {exit_.label} ({exit_.description})"

    state.current_turn.submissions.append(PlayerTurnSubmission(
        character_id=character_id,
        submitted_at=_now(),
        action_text=action,
        is_latest=True,
    ))

    # Close turn — ready for DM resolution
    state.current_turn.status = TurnStatus.CLOSED
    state.current_turn.closed_at = _now()
    state.updated_at = _now()

    return _ok(state, f"{leader_name} {action}.", notify_dm=True)


# ---------------------------------------------------------------------------
# Dungeon import
# ---------------------------------------------------------------------------

def import_dungeon(state: GameState, dungeon: Dungeon) -> EngineResult:
    """
    Load a pre-authored dungeon into the session.

    Only permitted in PRE_START — the dungeon must be set before the
    session begins so players arrive into a known map. Replaces any
    previously loaded dungeon wholesale.

    If the dungeon has an entrance_id, the current room is set to that
    room so the DM can immediately see it in the status block. The room
    is NOT marked visited — that happens when the party actually enters
    via /embark + /dm_setroom or move_party_to_room.
    """
    if state.mode != SessionMode.PRE_START:
        return _err(state, "Dungeons can only be imported before the session starts.")
    state.dungeon = dungeon
    # Point current_room_id at the entrance so the web UI has something
    # to show, but leave visited=False until the party actually enters.
    if dungeon.entrance_id and dungeon.entrance_id in dungeon.rooms:
        state.current_room_id = dungeon.entrance_id
    state.updated_at = _now()
    room_count = len(dungeon.rooms)
    return _ok(state, f"Dungeon '{dungeon.name}' loaded ({room_count} room(s)).")


# ---------------------------------------------------------------------------
# Say log and oracles
# ---------------------------------------------------------------------------

def say(state: GameState, speaker: str, text: str) -> EngineResult:
    """Add a speech entry to the say log. Shown in status block, clears each turn."""
    entry = f'{speaker} says "{text}"'
    state.say_log.append(entry)
    state.updated_at = _now()
    return _ok(state, entry)


def emote(state: GameState, speaker: str, text: str) -> EngineResult:
    """Add an emote entry to the say log. Like say but no quotes."""
    entry = f"{speaker} {text}"
    state.say_log.append(entry)
    state.updated_at = _now()
    return _ok(state, entry)


def ask_oracle(
    state:          GameState,
    asker_name:     str,
    question:       str,
    asker_owner_id: str = None,
) -> EngineResult:
    """
    Create a new oracle entry. Returns EngineResult with .data containing the Oracle object
    so the platform layer can post the Discord message and store the message_id back.
    """
    from models import Oracle
    state.oracle_counter += 1
    oracle = Oracle(
        number=state.oracle_counter,
        asker_name=asker_name,
        asker_owner_id=asker_owner_id,
        question=question,
    )
    state.oracles.append(oracle)
    state.updated_at = _now()
    result = _ok(state, f"Oracle #{oracle.number} posted.")
    result.data = oracle
    return result


def answer_oracle(
    state:     GameState,
    number:    int,
    answer:    str,
) -> EngineResult:
    """
    DM answers an oracle by number. Returns EngineResult with .data containing the Oracle object
    so the platform layer can edit the Discord message in place.
    """
    # Match the *last* oracle with this number — oracles reset to #1 each turn
    # so there may be multiple oracles with the same number across turns.
    matches = [o for o in state.oracles if o.number == number]
    oracle = matches[-1] if matches else None
    if oracle is None:
        result = _err(state, f"Oracle #{number} not found.")
        result.data = None
        return result
    oracle.answer = answer
    state.updated_at = _now()
    result = _ok(state, f"Oracle #{number} answered.")
    result.data = oracle
    return result


# ---------------------------------------------------------------------------
# Session start / hold / resume
# ---------------------------------------------------------------------------

def start_session(state: GameState) -> EngineResult:
    """
    DM command: transition from PRE_START to EXPLORATION.
    Opens the first dungeon turn.
    """
    if state.mode != SessionMode.PRE_START:
        return _err(state, "Session is already started.")
    if not state.characters:
        return _err(state, "No characters have arrived yet.")
    state.mode = SessionMode.EXPLORATION
    state.session_active = True
    state.updated_at = _now()
    open_turn(state)
    return _ok(state, "Session started. The adventure begins!")

def hold_session(state: GameState) -> EngineResult:
    """Put the session on hold. No player turns or DM commands accepted until resumed."""
    if not state.session_active:
        return _err(state, "Session is already on hold.")
    state.session_active = False
    state.updated_at = _now()
    return _ok(state, "Session is now on hold.")


def resume_session(state: GameState) -> EngineResult:
    """Resume a session that was put on hold."""
    if state.session_active:
        return _err(state, "Session is not on hold.")
    state.session_active = True
    state.updated_at = _now()
    return _ok(state, "Session resumed.")


# ---------------------------------------------------------------------------
# Mode switching
# ---------------------------------------------------------------------------

def enter_rounds(state: GameState) -> EngineResult:
    """
    Switch to combat rounds mode.
    Saves the current exploration turn number and resets the counter to 1
    so rounds are counted from Round 1.
    """
    if state.mode == SessionMode.ROUNDS:
        return _err(state, "Already in rounds.")
    state.rounds_started_at_turn = state.turn_number
    state.mode = SessionMode.ROUNDS
    state.turn_number = 1
    if state.current_turn:
        state.current_turn.mode = SessionMode.ROUNDS
        state.current_turn.turn_number = 1
    state.updated_at = _now()
    return _ok(state, "Entering rounds!")


def exit_rounds(state: GameState) -> EngineResult:
    """
    Return to exploration mode.
    Restores the exploration turn counter, advancing by 1 to account
    for the turn consumed by combat (standard B/X ruling).
    """
    if state.mode == SessionMode.EXPLORATION:
        return _err(state, "Already in exploration mode.")
    state.mode = SessionMode.EXPLORATION
    # Restore exploration turn number, +1 for the turn combat consumed
    resumed_at = (state.rounds_started_at_turn or state.turn_number) + 1
    state.turn_number = resumed_at
    state.rounds_started_at_turn = None
    if state.current_turn:
        state.current_turn.mode = SessionMode.EXPLORATION
        state.current_turn.turn_number = resumed_at
    state.updated_at = _now()
    return _ok(state, "Returning to exploration.")


# ---------------------------------------------------------------------------
# Status message renderer
# ---------------------------------------------------------------------------

def render_status_header(state: GameState) -> str:
    """
    Produce the plain-text header line shown above the code block.
    Includes a Discord timestamp tag so clients render the deadline
    in local time.
    """
    if state.mode == SessionMode.PRE_START:
        return "**Awaiting players** — session not yet started"
    if state.mode == SessionMode.ROUNDS:
        turn_label = f"⚔ **Round {state.turn_number}** ⚔"
    else:
        turn_label = f"**Turn {state.turn_number}**"
    if state.current_turn and state.current_turn.due_at:
        due = state.current_turn.due_at
        if due.tzinfo is None:
            due = due.replace(tzinfo=UTC)
        unix_ts = int(due.timestamp())
        turn_label += f" (deadline <t:{unix_ts}:f>)"
    return turn_label


def render_status(state: GameState) -> str:
    """
    Produce the code-block body of the status message.
    Does not include the header line (see render_status_header).
    """
    lines: list[str] = []
    sep = "─" * 32

    lines.append(sep)

    # Mode and session/turn state
    if state.mode == SessionMode.PRE_START:
        lines.append("Waiting for players — session not yet started")
    else:
        mode_str = "Rounds" if state.mode == SessionMode.ROUNDS else "Exploration"
        if not state.session_active:
            state_str = "ON HOLD"
        elif state.current_turn is None:
            state_str = "No active turn"
        elif state.current_turn.status == TurnStatus.OPEN:
            state_str = "Open — accepting turn submissions"
        elif state.current_turn.status == TurnStatus.CLOSED:
            state_str = "Closed — awaiting DM resolution"
        else:
            state_str = state.current_turn.status.value
        lines.append(f"{mode_str} | {state_str}")

    # Light source
    if state.party:
        light = state.party.active_light
        if light:
            remaining = (
                str(light.turns_remaining) if light.turns_remaining is not None else "∞"
            )
            lines.append(f"{light.label}: {remaining} turns")
            if light.turns_remaining == 0:
                lines.append("⚠ LIGHT OUT")
        else:
            lines.append("No light source")

        # Gold
        lines.append(f"Gold: {state.party.gold}")

    lines.append(sep)

    # Party members
    if state.party:
        for cid in state.party.member_ids:
            char = state.characters.get(cid)
            if char is None:
                continue
            is_leader = (cid == state.party.leader_id)
            leader_mark = "*" if is_leader else " "

            status_tag = ""
            if char.status != CharacterStatus.ACTIVE:
                status_tag = f" [{char.status.value.upper()}]"
            elif char.status_notes:
                status_tag = f", {char.status_notes}"

            submission = state.latest_submission(cid)
            sub_text = f" (\"{submission.action_text}\")" if submission else ""

            cls_name = char.character_class.value
            lines.append(
                f"{leader_mark}{char.name} the {cls_name}: {char.hp_current}/{char.hp_max}"
                f"{status_tag}{sub_text}"
            )

    lines.append(sep)

    # Room
    room = state.current_room
    if room:
        lines.append(f"Room: {room.name} — {room.description}")
        if room.features:
            lines.append("Features:")
            for feat in room.features:
                state_note = f" [{feat.state}]" if feat.state and feat.state != "intact" else ""
                lines.append(f"  {feat.name}{state_note}: {feat.description}")
        if room.exits:
            lines.append("Exits:")
            for i, ex in enumerate(room.exits, 1):
                lines.append(f"  {i}. {ex.label.capitalize()}: {ex.description} [{ex.door_state.value}]")
    else:
        lines.append("Room: (none)")

    lines.append(sep)

    # NPCs
    active_npcs = [n for n in state.npcs if n.status != "dead"]
    if active_npcs:
        lines.append("NPCs:")
        for npc in active_npcs:
            lines.append(
                f"  {npc.name}: {npc.hp_current}/{npc.hp_max}"
                + (f" — {npc.status}" if npc.status != "active" else "")
                + (f" ({npc.description})" if npc.description else "")
            )
    else:
        lines.append("NPCs: none")

    # Say log — clears each turn
    if state.say_log:
        lines.append(sep)
        for entry in state.say_log:
            lines.append(entry)

    lines.append(sep)

    return "\n".join(lines)
