"""
Helper utilities for the dungeon crawler engine.
"""

from uuid import UUID

from models import GameState, Room


def _ok(state: GameState, message: str = "", notify_dm: bool = False):
    """Create a successful EngineResult."""
    from . import EngineResult
    return EngineResult(ok=True, message=message, state=state, notify_dm=notify_dm)


def _err(state: GameState, error: str):
    """Create an error EngineResult."""
    from . import EngineResult
    return EngineResult(ok=False, error=error, state=state)


def _find_npc_in_roster(state: GameState, npc_id: UUID):
    """Find an NPC by ID in the NPC roster (searches all groups)."""
    for group in state.npc_roster.groups.values():
        for npc in group.npcs:
            if npc.npc_id == npc_id:
                return npc
    return None


def _resolve_room(state: GameState, room_id: UUID | None) -> Room | None:
    """Return the room for room_id if given, else the party's current room."""
    if room_id is not None:
        if state.dungeon is None:
            return None
        return state.dungeon.rooms.get(room_id)
    return state.current_room


def _snapshot(state: GameState) -> dict:
    """Produce a lightweight serializable snapshot of key state for history."""
    room = state.current_room
    # Use npcs_in_current_room to get NPCs from the roster
    npcs_in_room = state.npcs_in_current_room
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
            for n in npcs_in_room
        ],
    }
