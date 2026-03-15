"""
conftest.py — shared pytest fixtures for the dungeon bot test suite.

All fixtures operate purely on the engine + models layer.
No Discord, no FastAPI, no SQLite — those are tested separately.
"""

import os
import sys

# Make the project root importable regardless of where pytest is invoked from.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from engine import create_character, start_session
from models import CharacterClass, GameState, Party

# ---------------------------------------------------------------------------
# Bare state — no party, no characters
# ---------------------------------------------------------------------------

@pytest.fixture
def bare_state():
    """Minimal GameState: channel + DM set, nothing else."""
    state = GameState(platform_channel_id="ch_test", dm_user_id="dm_001")
    state.party = Party(name="Test Party")
    return state


# ---------------------------------------------------------------------------
# State with one fighter character
# ---------------------------------------------------------------------------

@pytest.fixture
def state_with_fighter(bare_state):
    """PRE_START state with a single Fighter added."""
    create_character(
        bare_state,
        name="Aldric",
        character_class=CharacterClass.FIGHTER,
        equipment_package="Pack A",
        owner_id="user_001",
    )
    return bare_state


# ---------------------------------------------------------------------------
# Fully started exploration session
# ---------------------------------------------------------------------------

@pytest.fixture
def active_state(state_with_fighter):
    """EXPLORATION state with Turn 1 open and one Fighter."""
    start_session(state_with_fighter)
    return state_with_fighter


# ---------------------------------------------------------------------------
# State with a full party (Fighter + Magic-User + Cleric)
# ---------------------------------------------------------------------------

@pytest.fixture
def party_state(bare_state):
    """PRE_START state with three characters from different classes."""
    for name, cls, owner in [
        ("Aldric",  CharacterClass.FIGHTER,     "user_001"),
        ("Mira",    CharacterClass.MAGIC_USER,  "user_002"),
        ("Brother Tomas", CharacterClass.CLERIC, "user_003"),
    ]:
        create_character(
            bare_state,
            name=name,
            character_class=cls,
            equipment_package="Pack A",
            owner_id=owner,
        )
    return bare_state


@pytest.fixture
def active_party_state(party_state):
    """EXPLORATION session with three characters, Turn 1 open."""
    start_session(party_state)
    return party_state
