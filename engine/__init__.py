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

from models import DoorState, GameState, PlayerTurnSubmission, SessionMode, TurnStatus

# Import managers and utilities from submodules
from .character import CharacterManager
from .combat import CombatAction, apply_condition, auto_resolve_round, initialize_battlefield
from .core import TurnManager
from .data_loader import (
    ACTION_REGISTRY,
    CLASS_DEFINITIONS,
    CONDITION_REGISTRY,
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
from .helpers import _err, _find_npc_in_roster, _now, _ok, _resolve_room, _snapshot
from .light import LightManager, _tick_light
from .npc import NPCManager
from .oracle import OracleManager
from .room import RoomManager
from .session import SessionManager


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


def add_npc(state: GameState, npc):
    """Add an NPC."""
    nm = NPCManager()
    return nm.add_npc_to_room(state, npc)


def set_npc_hp(state: GameState, npc_id, new_hp: int):
    """Set NPC HP."""
    nm = NPCManager()
    return nm.set_npc_hp(state, npc_id, new_hp)


def set_npc_status(state: GameState, npc_id, status: str):
    """Set NPC status."""
    nm = NPCManager()
    return nm.set_npc_status(state, npc_id, status)


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
):
    """Update an NPC."""
    nm = NPCManager()
    return nm.update_npc(
        state, npc_id, name, description, hp_max,
        hp_current, defense, notes,
    )


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


def resolve_turn(state: GameState, resolution: str):
    """Resolve a turn."""
    tm = TurnManager()
    return tm.resolve_turn(state, resolution)


def set_turn_number(state: GameState, turn_number: int):
    """Set turn number."""
    tm = TurnManager()
    return tm.set_turn_number(state, turn_number)


def set_light_source(state: GameState, label: str, turns_remaining: int | None):
    """Set light source."""
    lm = LightManager()
    return lm.set_light_source(state, label, turns_remaining)


def register_room(state: GameState, room):
    """Register a room."""
    rm = RoomManager()
    return rm.register_room(state, room)


def set_room(state: GameState, room):
    """Set current room."""
    rm = RoomManager()
    return rm.set_room(state, room)


def move_party_to_room(state: GameState, room_id):
    """Move party to room."""
    rm = RoomManager()
    return rm.move_party_to_room(state, room_id)


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
    room_id=None,
):
    """Update an exit."""
    rm = RoomManager()
    return rm.update_exit(
        state, exit_id, label, description, door_state,
        destination_id, notes, room_id,
    )


def set_feature_state(state: GameState, feature_id, new_state: str, room_id=None):
    """Set feature state."""
    rm = RoomManager()
    return rm.set_feature_state(state, feature_id, new_state, room_id)


def set_exit_state(state: GameState, exit_id, new_state, room_id=None):
    """Set exit state."""
    rm = RoomManager()
    return rm.set_exit_state(state, exit_id, new_state, room_id)


def add_exit(
    state: GameState,
    label: str,
    description: str,
    door_state=DoorState.OPEN,
    notes: str = "",
    room_id=None,
):
    """Add an exit."""
    rm = RoomManager()
    return rm.add_exit(state, label, description, door_state, notes, room_id)


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
                lines.append("Light out!")
        else:
            lines.append("No light source")

        # Gold / XP
        lines.append(f"Gold: {state.party.gold} XP: {state.party.experience}")

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
            if char.status.value != "active":
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

    # NPCs - get from roster based on current room
    active_npcs = [n for n in state.npcs_in_current_room if n.status != "dead"]
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

    # Battlefield positions (ROUNDS mode only)
    if state.mode == SessionMode.ROUNDS and state.battlefield is not None:
        lines.append(sep)
        lines.append("Positions:")
        try:
            from cogs.action_buttons import render_battlefield_section
            lines.append(render_battlefield_section(state))
        except ImportError:
            pass  # platform layer not loaded (e.g. during testing)

    # Say log — clears each turn
    if state.say_log:
        lines.append(sep)
        for entry in state.say_log:
            lines.append(entry)

    lines.append(sep)

    return "\n".join(lines)

__all__ = [
    # Core types
    "EngineResult",
    # Combat
    "CombatAction",
    "initialize_battlefield",
    "auto_resolve_round",
    "apply_condition",
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
    "LightManager",
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
    "add_npc",
    "set_npc_hp",
    "set_npc_status",
    "remove_npc",
    "update_npc",
    "open_turn",
    "submit_turn",
    "close_turn",
    "resolve_turn",
    "set_turn_number",
    "unsubmit_turn",
    "set_light_source",
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
    "add_exit",
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
    "abscond",
    "render_status_header",
    "render_status",
]
