"""
tests/test_hidden_npc_combat.py — Hidden NPCs are excluded from combat.

Covers issue #152:
  - NPC.hidden NPCs are not placed on the battlefield at combat start.
  - Hidden NPCs do not appear in the player-facing battlefield map.
  - Hidden NPCs do not act in auto-resolved rounds.
  - Combat ends when visible NPCs are defeated, ignoring hidden NPCs.
  - Revealing a hidden NPC during ROUNDS adds it to the battlefield.
  - Hiding a visible NPC during ROUNDS removes it from the battlefield.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine import (
    add_npc,
    auto_resolve_round,
    create_character,
    enter_rounds,
    open_turn,
    register_room,
    set_npc_visibility,
    start_session,
)
from engine.combat import CombatAction
from models import (
    NPC,
    CharacterClass,
    GameState,
    Party,
    PlayerTurnSubmission,
    RangeBand,
    Room,
    SessionMode,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state(n_chars: int = 1) -> GameState:
    """EXPLORATION session with n_chars KNIGHT characters."""
    state = GameState(platform_channel_id="ch", dm_user_id="dm")
    state.party = Party(name="P")
    for i in range(n_chars):
        create_character(state, f"Hero{i}", CharacterClass.KNIGHT, "", owner_id=f"u{i}")
    start_session(state)
    return state


def _make_combat_state_with_hidden_npc():
    """
    One character, one visible NPC, one hidden NPC.
    Returns (state, char_id, visible_npc_id, hidden_npc_id).
    """
    state = _make_state()
    room = Room(name="Hall", description="A dark hall.")
    register_room(state, room)
    state.current_room_id = room.room_id

    visible = NPC(
        name="Goblin", hp_current=1, hp_max=1,
        defense=0, damage_dice="1d4", hit_dice=1,
        hidden=False,
    )
    hidden = NPC(
        name="Ghost", hp_current=50, hp_max=50,
        defense=0, damage_dice="1d4", hit_dice=2,
        hidden=True,
    )
    add_npc(state, visible)
    add_npc(state, hidden)

    # Ensure the character can survive a counter-attack.
    for char in state.characters.values():
        char.hp_current = 50
        char.hp_max = 50

    enter_rounds(state)
    open_turn(state)

    char_id = list(state.characters.keys())[0]
    # Place everyone at melee range for deterministic attacks.
    state.battlefield.combatants[char_id].range_band = RangeBand.ENGAGE
    state.battlefield.combatants[visible.npc_id].range_band = RangeBand.ENGAGE

    return state, char_id, visible.npc_id, hidden.npc_id


def _attack_submission(char_id, target_id) -> PlayerTurnSubmission:
    action = CombatAction(action_id="attack", target_id=target_id)
    return PlayerTurnSubmission(
        character_id=char_id,
        action_text="Attack",
        is_latest=True,
        combat_action=action.to_dict(),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestHiddenNpcCombatExclusion:

    def test_hidden_npc_excluded_from_battlefield_at_enter_rounds(self):
        state, char_id, visible_id, hidden_id = _make_combat_state_with_hidden_npc()

        assert visible_id in state.battlefield.combatants
        assert hidden_id not in state.battlefield.combatants

    def test_hidden_npc_not_shown_in_battlefield_map(self):
        pytest = __import__("pytest")
        pytest.importorskip("discord")
        from cogs.action_buttons import render_battlefield_section

        state, char_id, visible_id, hidden_id = _make_combat_state_with_hidden_npc()
        section = render_battlefield_section(state)

        assert "Goblin" in section
        assert "Ghost" not in section

    def test_hidden_npc_does_not_act_in_auto_resolve(self):
        state, char_id, visible_id, hidden_id = _make_combat_state_with_hidden_npc()
        state.battlefield.combatants[char_id].initiative = 100
        state.current_turn.submissions = [_attack_submission(char_id, visible_id)]

        result = auto_resolve_round(state)

        hidden_npc = next(n for n in state.npcs_in_current_room if n.npc_id == hidden_id)
        assert "Ghost" not in result.message
        assert hidden_npc.hp_current == 50

    def test_combat_ends_when_only_hidden_npcs_remain(self):
        state, char_id, visible_id, hidden_id = _make_combat_state_with_hidden_npc()
        state.battlefield.combatants[char_id].initiative = 100
        state.current_turn.submissions = [_attack_submission(char_id, visible_id)]

        auto_resolve_round(state)

        # Combat should exit because the only battlefield NPC was defeated.
        assert state.mode == SessionMode.EXPLORATION
        assert state.battlefield is None
        # Hidden NPC remains alive in the room.
        hidden_npc = next(n for n in state.npcs_in_current_room if n.npc_id == hidden_id)
        assert hidden_npc.hp_current == 50

    def test_reveal_hidden_npc_during_combat_adds_to_battlefield(self):
        state, char_id, visible_id, hidden_id = _make_combat_state_with_hidden_npc()
        assert hidden_id not in state.battlefield.combatants

        result = set_npc_visibility(state, hidden_id, hidden=False)

        assert result.ok
        assert hidden_id in state.battlefield.combatants
        cs = state.battlefield.combatants[hidden_id]
        assert not cs.is_player
        assert cs.range_band == RangeBand.FAR_PLUS

    def test_hide_visible_npc_during_combat_removes_from_battlefield(self):
        state, char_id, visible_id, hidden_id = _make_combat_state_with_hidden_npc()
        assert visible_id in state.battlefield.combatants

        result = set_npc_visibility(state, visible_id, hidden=True)

        assert result.ok
        assert visible_id not in state.battlefield.combatants
