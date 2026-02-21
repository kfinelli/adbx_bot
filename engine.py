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

import copy
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID, uuid4

from models import (
    AbilityScores,
    Character,
    CharacterClass,
    CharacterStatus,
    Dungeon,
    DoorState,
    Exit,
    GameState,
    InventoryItem,
    LightSource,
    NPC,
    Party,
    PlayerTurnSubmission,
    PreparedSpell,
    Room,
    RoomFeature,
    SessionMode,
    SpellBook,
    TurnRecord,
    TurnStatus,
)
from tables import (
    ABILITY_MODIFIERS,
    CON_HP_MODIFIER,
    EQUIPMENT_PACKAGES,
    HIT_DIE,
    NON_CASTERS,
    get_saving_throws,
    get_spell_slots,
)


# ---------------------------------------------------------------------------
# Timezone-aware UTC now (replaces deprecated _now())
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# EngineResult — the return type for all engine functions
# ---------------------------------------------------------------------------

@dataclass
class EngineResult:
    ok:      bool       = True
    message: str        = ""   # narrative / confirmation text for the platform to display
    error:   str        = ""   # human-readable error if ok=False
    state:   Optional[GameState] = None


def _ok(state: GameState, message: str = "") -> EngineResult:
    return EngineResult(ok=True, message=message, state=state)

def _err(state: GameState, error: str) -> EngineResult:
    return EngineResult(ok=False, error=error, state=state)


# ---------------------------------------------------------------------------
# Dice helpers
# ---------------------------------------------------------------------------

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
    owner_id:        Optional[str] = None,
    ability_scores:  Optional[AbilityScores] = None,   # pre-rolled, or we roll
) -> EngineResult:
    """
    Create a new level-1 character, add them to the session, and return the result.
    If ability_scores is None, rolls 3d6 straight.
    """
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

    # --- HP: roll hit die, add CON modifier, minimum 1
    hit_die    = HIT_DIE[character_class]
    hp_roll    = roll_sum(1, hit_die)
    con_mod    = _con_modifier(scores.constitution)
    hp_max     = max(1, hp_roll + con_mod)

    # --- AC: base 9 (descending), DEX modifier lowers AC (better)
    dex_mod    = _dex_ac_modifier(scores.dexterity)
    base_ac    = 9 - dex_mod  # unarmored; equipment will further modify this

    # --- Saving throws
    saves = get_saving_throws(character_class, 1)

    # --- Spell book (spellcasters only)
    spellbook: Optional[SpellBook] = None
    if character_class not in NON_CASTERS:
        slots = get_spell_slots(character_class, 1)
        spellbook = SpellBook(
            max_slots=slots,
            prepared=[[] for _ in range(6)],
            known_spells=[],
        )

    # --- Inventory from equipment package
    inventory = []
    for item_name, qty, enc in EQUIPMENT_PACKAGES[equipment_package]:
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
    due_at: Optional[datetime] = None,
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
    if state.current_turn is None or state.current_turn.status != TurnStatus.OPEN:
        return _err(state, "No open turn to submit to.")

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
    turns_remaining: Optional[int],
) -> EngineResult:
    """
    DM command: set the active light source.
    Deactivates all previous sources and creates a new active one.
    """
    if state.party is None:
        return _err(state, "No active party.")
    for ls in state.party.light_sources:
        ls.is_active = False
    new_light = LightSource(
        label=label,
        turns_remaining=turns_remaining,
        is_active=True,
    )
    state.party.light_sources.append(new_light)
    state.updated_at = _now()
    duration = f"{turns_remaining} turns" if turns_remaining is not None else "permanent"
    return _ok(state, f"Light source set: {label} ({duration}).")


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
    old = char.hp_current
    char.hp_current = max(0, min(new_hp, char.hp_max))
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
    char.status = status
    char.status_notes = notes
    state.updated_at = _now()
    return _ok(state, f"{char.name} status → {status.value}. {notes}".strip())


# ---------------------------------------------------------------------------
# DM mutations — NPCs
# ---------------------------------------------------------------------------

def add_npc(state: GameState, npc: NPC) -> EngineResult:
    """Add an NPC to the current room."""
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
    old = npc.hp_current
    npc.hp_current = max(0, new_hp)
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
    npc.status = status
    state.updated_at = _now()
    return _ok(state, f"{npc.name} status → {status}.")


def remove_npc(state: GameState, npc_id: UUID) -> EngineResult:
    npc = _find_npc(state, npc_id)
    if npc is None:
        return _err(state, f"NPC {npc_id} not found.")
    state.npcs = [n for n in state.npcs if n.npc_id != npc_id]
    state.updated_at = _now()
    return _ok(state, f"{npc.name} removed from room.")


def _find_npc(state: GameState, npc_id: UUID) -> Optional[NPC]:
    for n in state.npcs:
        if n.npc_id == npc_id:
            return n
    return None


# ---------------------------------------------------------------------------
# DM mutations — rooms and features
# ---------------------------------------------------------------------------

def set_room(state: GameState, room: Room) -> EngineResult:
    """
    DM sets the current room. Adds it to the dungeon graph if not present.
    Clears NPCs (new room, new NPC list).
    """
    if state.dungeon is None:
        state.dungeon = Dungeon(name="The Dungeon")
    state.dungeon.rooms[room.room_id] = room
    state.current_room_id = room.room_id
    room.visited = True
    state.npcs = []
    state.updated_at = _now()
    return _ok(state, f"Entered: {room.name}.")


def set_feature_state(
    state:      GameState,
    feature_id: UUID,
    new_state:  str,
) -> EngineResult:
    """Update the state string of a room feature."""
    room = state.current_room
    if room is None:
        return _err(state, "No current room.")
    for feat in room.features:
        if feat.feature_id == feature_id:
            feat.state = new_state
            state.updated_at = _now()
            return _ok(state, f"{feat.name} → {new_state}.")
    return _err(state, f"Feature {feature_id} not found in current room.")


def set_exit_state(
    state:    GameState,
    exit_id:  UUID,
    new_state,   # DoorState
) -> EngineResult:
    room = state.current_room
    if room is None:
        return _err(state, "No current room.")
    for ex in room.exits:
        if ex.exit_id == exit_id:
            ex.door_state = new_state
            state.updated_at = _now()
            return _ok(state, f"Exit '{ex.label}' → {new_state.value}.")
    return _err(state, f"Exit {exit_id} not found in current room.")


def add_exit(
    state:       GameState,
    label:       str,
    description: str,
    door_state=DoorState.OPEN,
    notes:       str = "",
) -> EngineResult:
    """DM adds a new exit to the current room."""
    room = state.current_room
    if room is None:
        return _err(state, "No current room.")
    exit_ = Exit(
        label=label,
        description=description,
        door_state=door_state,
        notes=notes,
    )
    room.exits.append(exit_)
    n = len(room.exits)
    state.updated_at = _now()
    return _ok(state, f"Exit {n} added: {label}.")


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

    return _ok(state, f"{leader_name} {action}.")


# ---------------------------------------------------------------------------
# Mode switching
# ---------------------------------------------------------------------------

def enter_rounds(state: GameState) -> EngineResult:
    """Switch to combat rounds mode."""
    if state.mode == SessionMode.ROUNDS:
        return _err(state, "Already in rounds.")
    state.mode = SessionMode.ROUNDS
    if state.current_turn:
        state.current_turn.mode = SessionMode.ROUNDS
    state.updated_at = _now()
    return _ok(state, "Entering rounds! Initiative order to be set by DM.")


def exit_rounds(state: GameState) -> EngineResult:
    """Return to exploration mode."""
    if state.mode == SessionMode.EXPLORATION:
        return _err(state, "Already in exploration mode.")
    state.mode = SessionMode.EXPLORATION
    if state.current_turn:
        state.current_turn.mode = SessionMode.EXPLORATION
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
    turn_label = f"**Turn {state.turn_number}**"
    if state.mode == SessionMode.ROUNDS:
        turn_label += " — ROUNDS"
    if state.current_turn and state.current_turn.due_at:
        due = state.current_turn.due_at
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
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

    # Mode line (turn number now lives in the header above the code block)
    lines.append("In rounds" if state.mode == SessionMode.ROUNDS else "Exploration")

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

            lines.append(
                f"{leader_mark}{char.name}: {char.hp_current}/{char.hp_max}"
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

    lines.append(sep)

    return "\n".join(lines)
