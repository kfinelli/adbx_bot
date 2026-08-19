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

from dataclasses import dataclass
from datetime import UTC
from typing import Any

from engine.azure_constants import DEFAULT_ROOM_XP
from models import (
    DoorState,
    GameState,
    PlayerTurnSubmission,
    RoomItemVisibility,
    SessionMode,
    TurnStatus,
)

# Import managers and utilities from submodules
from .character import CharacterManager
from .combat import (
    CombatAction,
    apply_condition,
    auto_resolve_round,
    initialize_battlefield,
    instant_move,
    partial_auto_resolve_round,
)
from .core import TurnManager
from .data_loader import (
    ACTION_REGISTRY,
    CLASS_DEFINITIONS,
    CONDITION_REGISTRY,
    ITEM_REGISTRY,
    SKILL_REGISTRY,
    ActionDef,
    ConditionDef,
    JobDef,
    SkillDef,
)
from .dice import (
    d,
    print_dice_results,
    roll,
    roll_azure_stat,
    roll_dice_expr,
    roll_expr,
    roll_stat_block,
    roll_stats,
    roll_sum,
)
from .encounter import check_random_encounter
from .helpers import _err, _find_npc_in_roster, _now, _ok, _resolve_room, _snapshot
from .light import _tick_light
from .npc import NPCManager
from .oracle import OracleManager
from .room import RoomManager
from .session import SessionManager
from .strings import fmt_string, get_string


@dataclass
class EngineResult:
    ok:            bool             = True
    message:       str              = ""   # narrative / confirmation text for the platform to display
    error:         str              = ""   # human-readable error if ok=False
    state:         GameState | None = None
    notify_dm:     bool             = False  # platform should notify DM to resolve
    auto_resolved: bool             = False  # round auto-resolved; platform should post narrative + fresh status
    data:          Any              = None  # optional additional data (e.g., Oracle object for platform layer)


# Convenience functions for backward compatibility
def create_character(
    state:           GameState,
    name:            str,
    character_class,
    equipment_package: str,
    owner_id:        str | None = None,
    ability_scores = None,
    prerolled_stats: dict | None = None,
):
    """Create a new character."""
    cm = CharacterManager()
    return cm.create_character(
        state, name, character_class, equipment_package,
        owner_id, ability_scores, prerolled_stats,
    )


def set_character_hp(state: GameState, character_id, new_hp: int):
    """Set character HP."""
    cm = CharacterManager()
    return cm.set_character_hp(state, character_id, new_hp)


def set_character_status(state: GameState, character_id, status, notes: str = ""):
    """Set character status."""
    cm = CharacterManager()
    return cm.set_character_status(state, character_id, status, notes)


def equip_item(state: GameState, character_id, item_id: str, slot=None):
    """
    Equip an item from the character's inventory into the appropriate slot.

    ``slot`` may be an ItemSlot enum value or None (auto-detect).
    For weapons it is always MAIN_HAND; for gear the slot is read from
    the item definition.  For accessories the caller may pass
    ItemSlot.ACCESSORY1 or ItemSlot.ACCESSORY2 explicitly; if omitted
    the first free accessory slot is used.
    """
    cm = CharacterManager()
    return cm.equip_item(state, character_id, item_id, slot)


def unequip_item(state: GameState, character_id, slot):
    """
    Unequip whatever item is in the given slot (an ItemSlot enum value).
    """
    cm = CharacterManager()
    return cm.unequip_item(state, character_id, slot)


def set_familiar_weapon(state: GameState, character_id, instance_id: str | None):
    """
    Set (or clear) the Weapon Forte familiar weapon.

    instance_id=None clears the selection (DM reset).
    Returns EngineResult with ok=False if the character lacks the skill or already
    has a familiar weapon selected.
    """
    cm = CharacterManager()
    return cm.set_familiar_weapon(state, character_id, instance_id)


def remove_item(state: GameState, character_id, item_id: str, quantity: int = 1):
    """
    Remove item(s) from a character's inventory.

    Equipped items must be unequipped first. For stacked entries, decrements
    quantity; removes the entry entirely when quantity reaches zero.
    Returns EngineResult with ok=False if the item isn't found or is equipped.
    """
    cm = CharacterManager()
    return cm.remove_item(state, character_id, item_id, quantity)


def award_xp(state: GameState, character_id, amount: int):
    """Award XP to a character and trigger level-up checks.

    Returns EngineResult with .data = list[LevelUpResult].
    """
    cm = CharacterManager()
    return cm.award_xp(state, character_id, amount)


def check_level_up(state: GameState, character_id):
    """Check if a character has enough XP to level up; apply all pending levels.

    Returns list[LevelUpResult] — empty if no level-up occurred.
    """
    cm = CharacterManager()
    return cm.check_level_up(state, character_id)


def distribute_xp(state: GameState, total: int) -> list:
    """Award total XP split evenly among all ACTIVE characters.

    Returns flat list of LevelUpResult objects across all characters.
    """
    cm = CharacterManager()
    return cm.distribute_xp(state, total)


def give_item(state: GameState, character_id, item_id: str, quantity: int = 1):
    """
    Add item(s) to a character's inventory, enforcing slot limits.

    ChargeWeapons always get a new entry (independent charge state).
    All other items stack onto an existing unequipped entry when possible.
    Returns EngineResult with ok=False if inventory is full.
    """
    cm = CharacterManager()
    return cm.give_item(state, character_id, item_id, quantity)


def adjust_spell_charges(state: GameState, character_id, item_id: str, delta: int):
    """Adjust a spell's current charges by delta, clamped to [0, maxCharges]."""
    cm = CharacterManager()
    return cm.adjust_spell_charges(state, character_id, item_id, delta)


def adjust_light_charges(state: GameState, character_id, item_id: str, delta: int, equipped: bool):
    """Adjust a light item's current charges by delta, clamped to [0, max_light_turns]."""
    cm = CharacterManager()
    return cm.adjust_light_charges(state, character_id, item_id, delta, equipped)


def adjust_skill_uses(state: GameState, character_id, skill_id: str, delta: int):
    """Adjust a skill's current uses by delta, clamped to [0, max_uses]."""
    cm = CharacterManager()
    return cm.adjust_skill_uses(state, character_id, skill_id, delta)


def recharge_day_spells(state: GameState, character_id):
    """Restore all DAY-period spells to full charges for the given character."""
    cm = CharacterManager()
    return cm.recharge_day_spells(state, character_id)


def add_npc(state: GameState, npc, room_id=None):
    """Add an NPC."""
    nm = NPCManager()
    return nm.add_npc_to_room(state, npc, room_id=room_id)


def set_npc_hp(state: GameState, npc_id, new_hp: int):
    """Set NPC HP."""
    nm = NPCManager()
    return nm.set_npc_hp(state, npc_id, new_hp)


def set_npc_status(state: GameState, npc_id, status: str):
    """Set NPC status."""
    nm = NPCManager()
    return nm.set_npc_status(state, npc_id, status)


def set_npc_visibility(state: GameState, npc_id, hidden: bool):
    """Show or hide an NPC from player views."""
    nm = NPCManager()
    return nm.set_npc_visibility(state, npc_id, hidden)


def remove_npc_condition(state: GameState, npc_id, condition_id: str):
    """Remove a named condition from an NPC in the roster."""
    nm = NPCManager()
    return nm.remove_npc_condition(state, npc_id, condition_id)


def remove_npc_group(state: GameState, npc_id):
    """Remove an NPC by removing its group."""
    nm = NPCManager()
    # Find the group containing this NPC
    for group in state.npc_roster.groups.values():
        for n in group.npcs:
            if n.npc_id == npc_id:
                return nm.remove_npc_group(state, group.group_id)
    return _err(state, f"NPC {npc_id} not found.")

def remove_npc(state: GameState, npc_id):
    """Remove an NPC from its parent group."""
    nm = NPCManager()
    # Find the group containing this NPC
    return nm.remove_npc(state, npc_id)


def update_npc(
    state: GameState,
    npc_id,
    name: str,
    description: str,
    hp_max: int,
    hp_current: int,
    defense: int,
    notes: str = "",
    hit_dice: int = 1,
    resistance: int = 0,
    weapon_range: int = 0,
    damage_dice: str = "1d6",
    behavior: str = "simple",
):
    """Update an NPC."""
    nm = NPCManager()
    return nm.update_npc(
        state, npc_id, name, description, hp_max,
        hp_current, defense, notes, hit_dice,
        resistance, weapon_range, damage_dice,
        behavior,
    )


def copy_npc(state: GameState, npc_id, room_id=None):
    """Copy an NPC, placing the duplicate in the same room."""
    nm = NPCManager()
    return nm.copy_npc(state, npc_id, room_id=room_id)


def add_npc_to_group(state: GameState, group_id, npc):
    """Add an NPC directly to a specific group."""
    nm = NPCManager()
    return nm.add_npc_to_group(state, group_id, npc)


def update_group(state: GameState, group_id, name, movement_logic, current_room_id, possible_rooms):
    """Update group-level properties."""
    nm = NPCManager()
    return nm.update_group(state, group_id, name, movement_logic, current_room_id, possible_rooms)


def remove_npc_group_by_id(state: GameState, group_id):
    """Remove an NPC group by its group_id."""
    nm = NPCManager()
    return nm.remove_npc_group(state, group_id)


def add_encounter_entry(state: GameState, npc_group_template, weight: int):
    """Add an encounter entry to the dungeon's random encounter roster."""
    nm = NPCManager()
    return nm.add_encounter_entry(state, npc_group_template, weight)


def remove_encounter_entry(state: GameState, group_id):
    """Remove an encounter entry by its template group_id."""
    nm = NPCManager()
    return nm.remove_encounter_entry(state, group_id)


def update_encounter_entry_weight(state: GameState, group_id, weight: int):
    """Update an encounter entry's weight."""
    nm = NPCManager()
    return nm.update_encounter_entry_weight(state, group_id, weight)


def promote_group_to_encounter(state: GameState, group_id, weight: int):
    """Promote a live NPC group to the random encounter roster."""
    nm = NPCManager()
    return nm.promote_group_to_encounter(state, group_id, weight)


def update_encounter_npc(state: GameState, encounter_group_id, npc_id, name, description, hp_max,
                         defense, notes="", hit_dice=1, resistance=0, weapon_range=0, damage_dice="1d6",
                         behavior="simple"):
    """Update an NPC in an encounter template group."""
    nm = NPCManager()
    return nm.update_encounter_npc(state, encounter_group_id, npc_id, name, description, hp_max,
                                   defense, notes, hit_dice, resistance, weapon_range, damage_dice,
                                   behavior)


def remove_encounter_npc(state: GameState, encounter_group_id, npc_id):
    """Remove an NPC from an encounter template group."""
    nm = NPCManager()
    return nm.remove_encounter_npc(state, encounter_group_id, npc_id)


def add_npc_to_encounter_group(state: GameState, encounter_group_id, npc):
    """Add an NPC to an encounter template group."""
    nm = NPCManager()
    return nm.add_npc_to_encounter_group(state, encounter_group_id, npc)


def open_turn(state: GameState, due_at=None):
    """Open a new turn."""
    tm = TurnManager()
    return tm.open_turn(state, due_at)


def submit_turn(state: GameState, character_id, action_text: str, combat_action: dict | None = None):
    """Submit a turn."""
    tm = TurnManager()
    return tm.submit_turn(state, character_id, action_text, combat_action=combat_action)

def unsubmit_turn(state: GameState, character_id,):
    """Un-submit a turn."""
    tm = TurnManager()
    return tm.unsubmit_turn(state, character_id)

def close_turn(state: GameState):
    """Close a turn."""
    tm = TurnManager()
    return tm.close_turn(state)


def reopen_turn(state: GameState, hours: float):
    """Reopen a closed turn, extending the deadline by `hours` from now."""
    tm = TurnManager()
    return tm.reopen_turn(state, hours)


def resolve_turn(state: GameState, resolution: str, free_move: bool = False):
    """Resolve a turn."""
    tm = TurnManager()
    return tm.resolve_turn(state, resolution, free_move=free_move)


def set_turn_number(state: GameState, turn_number: int):
    """Set turn number."""
    tm = TurnManager()
    return tm.set_turn_number(state, turn_number)


def register_room(state: GameState, room):
    """Register a room."""
    rm = RoomManager()
    return rm.register_room(state, room)


def set_room(state: GameState, room):
    """Set current room (always awards exploration XP — room is always new)."""
    rm = RoomManager()
    result = rm.set_room(state, room)
    if result.ok:
        xp = getattr(room, "exploration_xp", 0) or DEFAULT_ROOM_XP
        cm = CharacterManager()
        cm.distribute_xp(state, xp)
        result.message += fmt_string("explore.xp_note", xp=xp)
    return result


def move_party_to_room(state: GameState, room_id):
    """Move party to room, awarding exploration XP on first visit."""
    dungeon = state.dungeon
    room = dungeon.rooms.get(room_id) if dungeon else None
    was_unvisited = room is not None and not room.visited
    rm = RoomManager()
    result = rm.move_party_to_room(state, room_id)
    if result.ok and was_unvisited:
        xp = room.exploration_xp or DEFAULT_ROOM_XP
        cm = CharacterManager()
        cm.distribute_xp(state, xp)
        result.message += fmt_string("explore.xp_note", xp=xp)
    return result


def update_room(state: GameState, room_id, name: str, description: str, notes: str = ""):
    """Update a room."""
    rm = RoomManager()
    return rm.update_room(state, room_id, name, description, notes)


def delete_feature(state: GameState, feature_id, room_id=None):
    """Delete a feature."""
    rm = RoomManager()
    return rm.delete_feature(state, feature_id, room_id)


def update_feature(
    state: GameState,
    feature_id,
    name: str,
    description: str,
    state_str: str,
    notes: str = "",
    room_id=None,
):
    """Update a feature."""
    rm = RoomManager()
    return rm.update_feature(
        state, feature_id, name, description, state_str, notes, room_id,
    )


def delete_exit(state: GameState, exit_id, room_id=None):
    """Delete an exit."""
    rm = RoomManager()
    return rm.delete_exit(state, exit_id, room_id)


def update_exit(
    state: GameState,
    exit_id,
    label: str,
    description: str,
    door_state,
    destination_id=None,
    notes: str = "",
    auto_move: bool = False,
    hidden: bool = False,
    room_id=None,
):
    """Update an exit."""
    rm = RoomManager()
    return rm.update_exit(
        state, exit_id, label, description, door_state,
        destination_id, notes, auto_move, hidden, room_id,
    )


def set_feature_state(state: GameState, feature_id, new_state: str, room_id=None):
    """Set feature state."""
    rm = RoomManager()
    return rm.set_feature_state(state, feature_id, new_state, room_id)


def set_exit_state(state: GameState, exit_id, new_state, room_id=None):
    """Set exit state."""
    rm = RoomManager()
    return rm.set_exit_state(state, exit_id, new_state, room_id)


def set_exit_visibility(state: GameState, exit_id, hidden: bool, room_id=None):
    """Show or hide an exit from player views."""
    rm = RoomManager()
    return rm.set_exit_visibility(state, exit_id, hidden, room_id)


def add_exit(
    state: GameState,
    label: str,
    description: str,
    door_state=DoorState.OPEN,
    notes: str = "",
    room_id=None,
    destination_id=None,
):
    """Add an exit."""
    rm = RoomManager()
    return rm.add_exit(state, label, description, door_state, notes, room_id, destination_id)


def add_room_item(
    state: GameState,
    item_id: str,
    quantity: int = 1,
    visibility: RoomItemVisibility = RoomItemVisibility.ACCESSIBLE,
    notes: str = "",
    room_id=None,
):
    """Add an item to a room's floor inventory."""
    rm = RoomManager()
    return rm.add_room_item(state, item_id, quantity, visibility, notes, room_id)


def remove_room_item(state: GameState, instance_id: str, room_id=None):
    """Remove an item from a room's floor inventory."""
    rm = RoomManager()
    return rm.remove_room_item(state, instance_id, room_id)


def set_room_item_visibility(
    state: GameState,
    instance_id: str,
    visibility: RoomItemVisibility,
    room_id=None,
):
    """Change the visibility of a room item."""
    rm = RoomManager()
    return rm.set_room_item_visibility(state, instance_id, visibility, room_id)


def pick_up_item(state: GameState, character_id, instance_id: str, quantity: int | None = None):
    """Player picks up an accessible room item into their inventory."""
    rm = RoomManager()
    return rm.pick_up_item(state, character_id, instance_id, quantity)


def drop_item(state: GameState, character_id, instance_id: str, quantity: int | None = None):
    """Player drops an inventory item into the current room."""
    rm = RoomManager()
    return rm.drop_item(state, character_id, instance_id, quantity)


def say(state: GameState, speaker: str, text: str):
    """Say something."""
    om = OracleManager()
    return om.say(state, speaker, text)


def emote(state: GameState, speaker: str, text: str):
    """Emote something."""
    om = OracleManager()
    return om.emote(state, speaker, text)


def ask_oracle(
    state: GameState,
    asker_name: str,
    question: str,
    asker_owner_id: str = None,
):
    """Ask the oracle."""
    om = OracleManager()
    return om.ask_oracle(state, asker_name, question, asker_owner_id)


def answer_oracle(state: GameState, number: int, answer: str):
    """Answer the oracle."""
    om = OracleManager()
    return om.answer_oracle(state, number, answer)


def start_session(state: GameState):
    """Start the session."""
    sm = SessionManager()
    return sm.start_session(state)


def hold_session(state: GameState):
    """Hold the session."""
    sm = SessionManager()
    return sm.hold_session(state)


def resume_session(state: GameState):
    """Resume the session."""
    sm = SessionManager()
    return sm.resume_session(state)


def enter_rounds(state: GameState):
    """Enter rounds mode."""
    sm = SessionManager()
    return sm.enter_rounds(state)


def exit_rounds(state: GameState):
    """Exit rounds mode."""
    sm = SessionManager()
    return sm.exit_rounds(state)


def import_dungeon(state: GameState, dungeon, npc_roster=None):
    """Import a dungeon and optionally an NPC roster."""
    sm = SessionManager()
    return sm.import_dungeon(state, dungeon, npc_roster)


def update_dungeon(
    state: GameState,
    name: str,
    description: str = "",
    random_encounter_interval: int = 6,
    random_encounter_roll: str = "1d6",
):
    """Update dungeon metadata (name, description, encounter settings)."""
    from validation import validate_non_empty_string
    dungeon = state.dungeon
    if dungeon is None:
        return _err(state, "No dungeon loaded.")
    name_result = validate_non_empty_string(name, "Dungeon name", max_length=100)
    if not name_result:
        return _err(state, name_result.error)
    dungeon.name = name_result.value
    dungeon.description = description.strip()
    dungeon.random_encounter_interval = max(1, int(random_encounter_interval))
    roll = random_encounter_roll.strip()
    if roll:
        dungeon.random_encounter_roll = roll
    state.updated_at = _now()
    return _ok(state, f"Dungeon '{dungeon.name}' updated.")


def abscond(
    state:        GameState,
    character_id,
    exit_number:  int,
):
    """
    Party leader moves the group through a numbered exit.

    - Only the party leader may call this.
    - Exit must not be locked or stuck.
    - Clears all existing turn submissions and replaces them with a
      single movement submission, then closes the turn so the DM
      sees it as ready to resolve.
    - Does NOT resolve the turn unless passing through an automove exit or
      moving to a previously-explored room
    """
    if state.party is None:
        return _err(state, "No active party.")
    if state.mode == SessionMode.PRE_START:
        return _err(state, get_string("errors.session_not_started"))
    if state.party.leader_id != character_id:
        return _err(state, get_string("errors.abscond_permission"))

    room = state.current_room
    if room is None:
        return _err(state, get_string("room.errors.no_current"))
    visible_exits = [e for e in room.exits if not e.hidden]
    if not visible_exits:
        return _err(state, get_string("explore.errors.no_exits"))

    idx = exit_number - 1
    if idx < 0 or idx >= len(visible_exits):
        exit_count = len(visible_exits)
        return _err(state, fmt_string("explore.errors.exit_not_found", exit_number=exit_number, exit_count=exit_count))

    exit_ = visible_exits[idx]
    if exit_.door_state in (DoorState.LOCKED, DoorState.STUCK):
        return _err(state, fmt_string("explore.errors.exit_blocked", label=exit_.label, door_state=exit_.door_state.value))

    # Determine auto-move and free-move conditions.
    # free_move: destination already explored → no turn cost.
    # auto_move: either the exit flag is set OR the destination is explored → skip DM approval.
    #            block auto move if there are any active NPCs present in the room
    dest_room = (
        state.dungeon.rooms.get(exit_.destination_id)
        if exit_.destination_id and state.dungeon
        else None
    )
    npcs_present = any(npc.status == "active" for npc in state.npcs_in_current_room)
    is_free_move = dest_room is not None and dest_room.visited
    is_auto_move = (exit_.auto_move or is_free_move) and not npcs_present

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

    if is_auto_move:
        # Resolve immediately — no DM input needed.
        # Capture whether this is a first visit before moving (for XP note).
        xp_note = ""
        if dest_room and not dest_room.visited:
            xp = dest_room.exploration_xp or DEFAULT_ROOM_XP
            xp_note = fmt_string("explore.xp_note", xp=xp)
        # Move the party first so the room is marked visited before the snapshot.
        if exit_.destination_id:
            move_party_to_room(state, exit_.destination_id)
        dest_name = dest_room.name if dest_room else exit_.label
        resolution = fmt_string("explore.auto_travel", leader_name=leader_name, action=action, dest_name=dest_name, xp_note=xp_note)
        result = resolve_turn(state, resolution, free_move=is_free_move)
        if result.ok:
            open_turn(state)
            result.auto_resolved = True
        return result

    # Otherwise: close turn and wait for DM resolution.
    state.current_turn.status = TurnStatus.CLOSED
    state.current_turn.closed_at = _now()
    state.updated_at = _now()

    return _ok(state, fmt_string("explore.travel_submitted", leader_name=leader_name, action=action), notify_dm=True)


def render_status_header(state: GameState) -> str:
    """
    Produce the plain-text header line shown above the code block.
    Includes a Discord timestamp tag so clients render the deadline
    in local time.
    """
    if state.mode == SessionMode.PRE_START:
        return "**Awaiting players**: session not yet started"
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


# ---------------------------------------------------------------------------
# Status sections + budget packing
#
# The status message is rendered from StatusSection blocks. The Discord layer
# packs them into embed fields (see cogs/status_embed.py) under two budgets:
# 6000 total chars per message, 1024 per field value. Section builders apply
# per-field prose caps so no single field can dominate; the packer fills the
# remaining budget by priority and degrades itemized sections (features, say
# log) with explicit overflow markers — content is never cut silently.
# ---------------------------------------------------------------------------

STATUS_TOTAL_BUDGET = 5900  # margin under Discord's 6000-char embed total
STATUS_FIELD_CAP = 1024     # Discord per-field-value limit
_FENCE_OVERHEAD = len("```\n\n```")

# Per-field prose caps
CAP_ROOM_DESC = 600
CAP_FEATURE_DESC = 150
CAP_SAY_ENTRY = 250
CAP_SUBMISSION = 120
CAP_NPC_DESC = 100
CAP_EXIT_DESC = 100


def _cap(text: str, limit: int) -> str:
    """Hard cap with ellipsis, applied to a single prose field."""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


@dataclass
class StatusSection:
    """
    One block of the status message (party, room, features, …).

    `priority` orders budget packing (lower packs first; gameplay-critical
    sections have the lowest values). `order` is the display order in the
    legacy text rendering. `legacy_header` is the "Header:" line prepended
    in the text rendering (embed rendering uses the field name instead).
    Degradable sections drop items when over budget, leaving a marker line
    formatted from `marker_fmt` (say log drops oldest first).
    """
    key:           str
    title:         str
    lines:         list[str]
    priority:      int
    order:         int
    legacy_header: str | None = None
    degradable:    bool = False
    drop_oldest:   bool = False
    marker_fmt:    str = "… +{n} more"


def render_status_sections(state: GameState) -> tuple[str, list[StatusSection]]:
    """
    Split the session status into a one-liner description (mode/turn state,
    light, gold) plus prioritized sections. Pure data — the platform layer
    decides how to render (embed fields) or pack (pack_sections).
    """
    desc_lines: list[str] = []

    # Mode and session/turn state
    if state.mode == SessionMode.PRE_START:
        desc_lines.append(get_string("status.waiting"))
    else:
        mode_str = "Rounds" if state.mode == SessionMode.ROUNDS else "Exploration"
        if not state.session_active:
            state_str = "ON HOLD"
        elif state.current_turn is None:
            state_str = "No active turn"
        elif state.current_turn.status == TurnStatus.OPEN:
            state_str = get_string("status.turn_open")
        elif state.current_turn.status == TurnStatus.CLOSED:
            state_str = get_string("status.turn_closed")
        else:
            state_str = state.current_turn.status.value
        desc_lines.append(f"{mode_str} | {state_str}")

    # Light source + gold
    if state.party:
        light_lines = []
        for char_id in state.party.member_ids:
            char = state.characters.get(char_id)
            if char is None:
                continue
            for item_id in char.equipped_slots.values():
                if not item_id:
                    continue
                defn = ITEM_REGISTRY.get(item_id)
                if defn is None or getattr(defn, "max_light_turns", None) is None:
                    continue
                inv = next(
                    (i for i in char.inventory if i.item_id == item_id and i.equipped),
                    None,
                )
                if inv and inv.charges is not None:
                    light_lines.append(f"{defn.name} ({char.name}): {inv.charges} turns")
        desc_lines.extend(light_lines if light_lines else ["No light source"])
        desc_lines.append(f"Gold: {state.party.gold}")

    sections: list[StatusSection] = []

    # Party members (sacred)
    if state.party:
        lines = []
        for cid in state.party.member_ids:
            char = state.characters.get(cid)
            if char is None:
                continue
            is_leader = (cid == state.party.leader_id)
            leader_mark = "*" if is_leader else " "

            status_tag = ""
            if char.status.value != "active":
                status_tag = f" [{char.status.value.upper()}]"
            elif char.status_notes:
                status_tag = f", {char.status_notes}"

            active_subs = [
                s for s in (state.current_turn.submissions if state.current_turn else [])
                if s.character_id == cid and s.is_latest
            ]
            sub_text = ""
            if active_subs:
                joined = "; ".join(_cap(s.action_text, CAP_SUBMISSION) for s in active_subs)
                sub_text = f' ("{joined}")'

            cls_name = char.character_class.value
            lines.append(
                f"{leader_mark}{char.name} the {cls_name}: {char.hp_current}/{char.hp_max}"
                f"{status_tag}{sub_text}"
            )
        if lines:
            sections.append(StatusSection("party", "Party", lines, priority=10, order=10))

    # Battlefield positions (sacred, ROUNDS mode only)
    if state.mode == SessionMode.ROUNDS and state.battlefield is not None:
        try:
            from cogs.action_buttons import render_battlefield_section
            body = render_battlefield_section(state)
            if body:
                sections.append(StatusSection(
                    "positions", "Positions", body.split("\n"),
                    priority=20, order=70, legacy_header="Positions",
                ))
        except ImportError:
            pass  # platform layer not loaded (e.g. during testing)

    room = state.current_room
    if room:
        # Exits (sacred — navigation)
        visible_exits = [e for e in room.exits if not e.hidden]
        if visible_exits:
            lines = []
            for i, ex in enumerate(visible_exits, 1):
                explored = (
                    state.dungeon is not None
                    and ex.destination_id is not None
                    and (dest := state.dungeon.rooms.get(ex.destination_id)) is not None
                    and dest.visited
                )
                flag_str = " (explored)" if explored else ""
                lines.append(
                    f"  {i}. {ex.label.capitalize()}: "
                    f"{_cap(ex.description, CAP_EXIT_DESC)} "
                    f"[{ex.door_state.value}]{flag_str}"
                )
            sections.append(StatusSection(
                "exits", "Exits", lines, priority=30, order=40, legacy_header="Exits",
            ))

        # Room items
        visible_items = [ri for ri in room.items if ri.visibility is not RoomItemVisibility.HIDDEN]
        if visible_items:
            lines = []
            for ri in visible_items:
                defn = ITEM_REGISTRY.get(ri.item.item_id)
                name = defn.name if defn is not None else ri.item.item_id
                qty = f" x{ri.item.quantity}" if ri.item.quantity > 1 else ""
                tag = " (inaccessible)" if ri.visibility is RoomItemVisibility.INACCESSIBLE else ""
                lines.append(f"  • {name}{qty}{tag}")
            sections.append(StatusSection(
                "items", "Items", lines, priority=50, order=50, legacy_header="Items",
            ))

        # Room name + description (flavor)
        lines = [f"✵{room.name}✵"]
        if room.description.strip():
            lines.append(_cap(room.description, CAP_ROOM_DESC))
        sections.append(StatusSection("room", "Room", lines, priority=60, order=20))

        # Features (degradable item-by-item)
        if room.features:
            lines = []
            for feat in room.features:
                state_note = f" [{feat.state}]" if feat.state and feat.state != "intact" else ""
                lines.append(
                    f" ⁃ {feat.name}{state_note}: {_cap(feat.description, CAP_FEATURE_DESC)}"
                )
            sections.append(StatusSection(
                "features", "Features", lines, priority=70, order=30,
                legacy_header="Features", degradable=True,
                marker_fmt="… +{n} more features",
            ))

    # NPCs in the current room (sacred)
    active_npcs = [n for n in state.npcs_in_current_room if n.status != "dead" and not n.hidden]
    if active_npcs:
        lines = []
        for npc in active_npcs:
            line = f"  {npc.name}: {npc.hp_current}/{npc.hp_max}"
            if npc.status != "active":
                line += f" · {npc.status}"
            if npc.description:
                line += f" ({_cap(npc.description, CAP_NPC_DESC)})"
            lines.append(line)
        sections.append(StatusSection(
            "npcs", "NPCs", lines, priority=40, order=60, legacy_header="NPCs",
        ))

    # Say log (degradable; chronological, oldest dropped first)
    if state.say_log:
        lines = [_cap(entry, CAP_SAY_ENTRY) for entry in state.say_log]
        sections.append(StatusSection(
            "say", "Say Log", lines, priority=80, order=80,
            degradable=True, drop_oldest=True,
            marker_fmt="… +{n} earlier entries",
        ))

    return "\n".join(desc_lines), sections


def _field_cost(title: str, body_lines: list[str]) -> int:
    """Chars charged against both the per-field cap and the total budget."""
    return len(title) + len("\n".join(body_lines)) + _FENCE_OVERHEAD


def _split_fields(title: str, lines: list[str], field_cap: int) -> list[tuple[str, list[str]]]:
    """Greedy split of lines into ≤field_cap fields ('Title', 'Title (2)', …)."""
    out: list[tuple[str, list[str]]] = []
    current: list[str] = []
    for line in lines:
        t = title if not out else f"{title} ({len(out) + 1})"
        if current and _field_cost(t, current + [line]) > field_cap:
            out.append((t, current))
            current = [line]
        else:
            current.append(line)
    if current:
        out.append((title if not out else f"{title} ({len(out) + 1})", current))
    return out


def pack_sections(
    description: str,
    sections: list[StatusSection],
    total_budget: int = STATUS_TOTAL_BUDGET,
    field_cap: int = STATUS_FIELD_CAP,
) -> list[tuple[str, list[str]]]:
    """
    Pack sections into (field title, body lines) pairs under the budget.

    Sacred sections are always included in full (line-truncated only as a
    pathological backstop, with an explicit marker). Degradable sections fill
    continuation fields until the budget runs out, then drop items — say log
    from the front, features from the end — always leaving an explicit
    overflow marker, never a silent cut.
    """
    remaining = total_budget - len(description)
    packed: list[tuple[str, list[str]]] = []

    def commit(title: str, lines: list[str]) -> bool:
        nonlocal remaining
        cost = _field_cost(title, lines)
        if cost > remaining:
            return False
        packed.append((title, lines))
        remaining -= cost
        return True

    for sec in sorted(sections, key=lambda s: s.priority):
        if not sec.degradable:
            # Sacred: commit every field; the total budget only fails here in
            # truly pathological states, so trim the last field with a marker.
            for i, (t, lines) in enumerate(_split_fields(sec.title, sec.lines, field_cap)):
                if not commit(t, lines):
                    if packed and i > 0:
                        packed[-1][1].append("… (truncated)")
                    break
            continue

        if sec.drop_oldest:
            # Trim from the front until the whole section (marker line on
            # top) fits the remaining budget.
            lines = list(sec.lines)
            while True:
                dropped = len(sec.lines) - len(lines)
                body = ([sec.marker_fmt.format(n=dropped)] if dropped else []) + lines
                fields = _split_fields(sec.title, body, field_cap)
                fits = sum(_field_cost(t, fl) for t, fl in fields) <= remaining
                if fits or not lines:
                    break
                lines.pop(0)
            if lines:
                for t, fl in fields:
                    commit(t, fl)
            continue

        # Drop-from-end (features): commit whole continuation fields while
        # they fit, then a partial final field + marker.
        fields = _split_fields(sec.title, sec.lines, field_cap)
        committed_items = 0
        failed_idx: int | None = None
        for i, (t, lines) in enumerate(fields):
            if commit(t, lines):
                committed_items += len(lines)
            else:
                failed_idx = i
                break

        rest = sec.lines[committed_items:]
        if not rest:
            continue

        # Budget ran out mid-section: keep as many remaining items as fit in
        # one final field, reserving room for the overflow marker.
        t = sec.title if not failed_idx else f"{sec.title} ({failed_idx + 1})"
        marker_room = len(sec.marker_fmt.format(n=len(rest))) + 1
        avail = min(field_cap, remaining) - len(t) - _FENCE_OVERHEAD - marker_room
        kept: list[str] = []
        used = 0
        for line in rest:
            add = len(line) + (1 if kept else 0)
            if used + add > avail:
                break
            kept.append(line)
            used += add
        dropped = len(rest) - len(kept)
        if dropped:
            kept.append(sec.marker_fmt.format(n=dropped))
        if kept:
            commit(t, kept)

    return packed


def render_status(state: GameState) -> str:
    """
    Produce the legacy plain-text status body (pre-embed format), assembled
    from the same sections the embed renderer packs. Kept for tests and
    debugging; the Discord status message uses render_status_sections +
    pack_sections via cogs/status_embed.py.
    """
    description, sections = render_status_sections(state)
    by_key = {s.key: s for s in sections}
    sep = "─" * 32

    lines: list[str] = [sep, *description.split("\n"), sep]

    party = by_key.get("party")
    if party:
        lines.extend(party.lines)
    lines.append(sep)

    room = by_key.get("room")
    if room:
        lines.extend(room.lines)
        for key in ("features", "exits", "items"):
            sec = by_key.get(key)
            if sec:
                lines.append(f"{sec.legacy_header}:")
                lines.extend(sec.lines)
    else:
        lines.append("Room: (none)")
    lines.append(sep)

    npcs = by_key.get("npcs")
    if npcs:
        lines.append("NPCs:")
        lines.extend(npcs.lines)
    else:
        lines.append("NPCs: none")

    positions = by_key.get("positions")
    if positions:
        lines.append(sep)
        lines.append("Positions:")
        lines.extend(positions.lines)

    say = by_key.get("say")
    if say:
        lines.append(sep)
        lines.extend(say.lines)

    lines.append(sep)
    return "\n".join(lines)

__all__ = [
    # Core types
    "EngineResult",
    # Combat
    "CombatAction",
    "initialize_battlefield",
    "auto_resolve_round",
    "partial_auto_resolve_round",
    "apply_condition",
    "instant_move",
    # Data registries (read-only, loaded from data/ at startup)
    "ACTION_REGISTRY",
    "CONDITION_REGISTRY",
    "CLASS_DEFINITIONS",
    "SKILL_REGISTRY",
    "ActionDef",
    "ConditionDef",
    "JobDef",
    "SkillDef",
    # Dice functions
    "d",
    "roll",
    "roll_azure_stat",
    "roll_dice_expr",
    "roll_expr",
    "roll_stat_block",
    "roll_stats",
    "roll_sum",
    "print_dice_results",
    # Managers
    "CharacterManager",
    "NPCManager",
    "RoomManager",
    "TurnManager",
    "OracleManager",
    "SessionManager",
    # Helper functions
    "_find_npc_in_roster",
    "_find_npcgroup_with_npc",
    "_resolve_room",
    "_snapshot",
    "_tick_light",
    # Engine functions
    "create_character",
    "set_character_hp",
    "set_character_status",
    "equip_item",
    "unequip_item",
    "give_item",
    "remove_item",
    "add_npc",
    "set_npc_hp",
    "set_npc_status",
    "remove_npc",
    "remove_npc_condition",
    "update_npc",
    "open_turn",
    "submit_turn",
    "close_turn",
    "reopen_turn",
    "resolve_turn",
    "set_turn_number",
    "unsubmit_turn",
    "register_room",
    "set_room",
    "move_party_to_room",
    "update_room",
    "delete_feature",
    "update_feature",
    "delete_exit",
    "update_exit",
    "set_feature_state",
    "set_exit_state",
    "set_exit_visibility",
    "add_exit",
    "add_room_item",
    "remove_room_item",
    "set_room_item_visibility",
    "pick_up_item",
    "drop_item",
    "say",
    "emote",
    "ask_oracle",
    "answer_oracle",
    "start_session",
    "hold_session",
    "resume_session",
    "enter_rounds",
    "exit_rounds",
    "import_dungeon",
    "update_dungeon",
    "copy_npc",
    "add_npc_to_group",
    "update_group",
    "remove_npc_group_by_id",
    "add_encounter_entry",
    "remove_encounter_entry",
    "update_encounter_entry_weight",
    "promote_group_to_encounter",
    "update_encounter_npc",
    "remove_encounter_npc",
    "add_npc_to_encounter_group",
    "abscond",
    "render_status_header",
    "render_status",
    "render_status_sections",
    "pack_sections",
    "StatusSection",
    "award_xp",
    "check_level_up",
    "distribute_xp",
    "check_random_encounter",
    "adjust_spell_charges",
    "adjust_light_charges",
    "adjust_skill_uses",
    "recharge_day_spells",
]
