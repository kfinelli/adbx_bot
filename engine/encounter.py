"""
encounter.py — Random encounter check logic.

Called after each exploration turn resolution and after DM turn number edits.
Pure engine module: no I/O, no Discord imports.
"""

from __future__ import annotations

import copy
import math
import random
from uuid import uuid4

from models import GameState

from .dice import roll_dice_expr
from .helpers import _ok
from .npc import NPCManager
from .strings import fmt_string


def check_random_encounter(state: GameState):
    """Check whether a random encounter occurs this turn.

    Called after turn_number has already been incremented. Returns an
    EngineResult (with DM-facing message and spawned group) if an encounter
    fires, or None if the interval hasn't been reached or the roll failed.

    Side effects when encounter fires:
      - A deep copy of the chosen NPCGroup template is added to state.npc_roster
        in the party's current room.
      - state.last_encounter_check_turn is always updated when the interval is
        reached, regardless of whether the encounter actually fires.
    """
    dungeon = state.dungeon
    if dungeon is None or not dungeon.random_encounter_roster:
        return None

    turns_since = state.turn_number - state.last_encounter_check_turn
    if turns_since < dungeon.random_encounter_interval:
        return None

    # Interval reached — advance the checkpoint whether the roll succeeds or not.
    state.last_encounter_check_turn = state.turn_number

    # Check room modifier.
    room = state.current_room
    modifier = room.random_encounter_modifier if room is not None else 1.0
    if modifier == 0.0:
        return None  # safe room

    # Thinking in terms of dice math: we normally trigger an encounter on a
    # roll of 1, but modifier can multiply that value so other rolls trigger
    # the encounter.
    threshold = int(math.floor(1 * modifier))
    if threshold < 1:
        return None  # modifier < 1.0 rounds down to no encounters

    roll_result = roll_dice_expr(dungeon.random_encounter_roll)
    roll_total = roll_result["total"]

    if roll_total > threshold:
        return None  # no encounter this interval

    # Weighted-random selection from roster.
    roster = dungeon.random_encounter_roster
    total_weight = sum(e.weight for e in roster)
    pick = random.randint(1, total_weight)
    cumulative = 0
    chosen_entry = roster[0]
    for entry in roster:
        cumulative += entry.weight
        if pick <= cumulative:
            chosen_entry = entry
            break

    # Deep-copy so repeated encounters each get independent instances.
    group_copy = copy.deepcopy(chosen_entry.npc_group)
    group_copy.group_id = uuid4()
    for npc in group_copy.npcs:
        npc.npc_id = uuid4()

    group_copy.current_room_id = state.current_room_id

    NPCManager().add_npc_group(state, group_copy)

    group_name = group_copy.name or "A monster group"
    msg = fmt_string("combat.encounter.appears", group_name=group_name, roll_total=roll_total, encounter_roll=dungeon.random_encounter_roll, threshold=threshold)
    return _ok(state, msg)
