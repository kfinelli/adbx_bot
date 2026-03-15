"""
Core data model for the async dungeon crawler engine.
No I/O, no platform dependencies — pure game state.

Ruleset-agnostic: CharacterClass is defined in tables.py and generated
from _CLASS_DEFINITIONS, so adding/removing classes only requires editing
tables.py. Nothing here changes when the ruleset changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

# CharacterClass is generated in tables.py from _CLASS_DEFINITIONS.
# Import it here so the rest of the codebase can import from models as before.
from tables import CharacterClass

# ---------------------------------------------------------------------------
# Enumerations (non-ruleset — these don't change between game systems)
# ---------------------------------------------------------------------------


class CharacterStatus(Enum):
    ACTIVE    = "active"
    DEAD      = "dead"
    FLED      = "fled"       # left the dungeon mid-session
    PETRIFIED = "petrified"
    PARALYZED = "paralyzed"


class SessionMode(Enum):
    PRE_START   = "pre_start"     # lobby — players arriving, not yet in dungeon
    EXPLORATION = "exploration"   # dungeon turn (10-min) time scale
    ROUNDS      = "rounds"        # combat, 6-second intervals


class TurnStatus(Enum):
    OPEN     = "open"      # accepting player submissions
    CLOSED   = "closed"    # timer expired or DM closed it; awaiting resolution
    RESOLVED = "resolved"  # DM has processed and committed results


class DoorState(Enum):
    OPEN   = "open"
    CLOSED = "closed"
    LOCKED = "locked"
    STUCK  = "stuck"
    SECRET = "secret"


class ExitDirection(Enum):
    NORTH = "north"
    SOUTH = "south"
    EAST  = "east"
    WEST  = "west"
    UP    = "up"
    DOWN  = "down"
    # Named/arbitrary exits are handled by free-form label strings


# ---------------------------------------------------------------------------
# Character
# ---------------------------------------------------------------------------

@dataclass
class AbilityScores:
    strength:     int = 10
    intelligence: int = 10
    wisdom:       int = 10
    dexterity:    int = 10
    constitution: int = 10
    charisma:     int = 10


@dataclass
class PreparedSpell:
    """One prepared spell occupying a slot."""
    spell_name: str  = ""
    expended:   bool = False   # True once the spell has been cast


@dataclass
class SpellBook:
    """
    B/X Vancian spell preparation.

    `max_slots` defines how many spells the character may prepare at each
    spell level (index 0 = level 1, ..., index 5 = level 6).
    `prepared` is a parallel list of lists: each inner list holds the
    specific PreparedSpell entries for that level. The DM or engine
    enforces that len(prepared[i]) <= max_slots[i].

    Example — a 3rd-level Magic-User with Sleep and Charm Person prepared
    at level 1, and Web at level 2:
        max_slots = [2, 1, 0, 0, 0, 0]
        prepared  = [
            [PreparedSpell("Sleep"), PreparedSpell("Charm Person")],
            [PreparedSpell("Web")],
            [], [], [], []
        ]
    """
    max_slots: list[int]              = field(default_factory=lambda: [0, 0, 0, 0, 0, 0])
    prepared:  list[list[PreparedSpell]] = field(
        default_factory=lambda: [[], [], [], [], [], []]
    )
    # Spells the character knows and can prepare from (their spellbook)
    known_spells: list[str]           = field(default_factory=list)

    def available(self, spell_level: int) -> list[PreparedSpell]:
        """Return prepared, unexpended spells at the given level."""
        return [s for s in self.prepared[spell_level - 1] if not s.expended]

    def expend(self, spell_level: int, spell_name: str) -> None:
        """Mark a specific prepared spell as expended."""
        for spell in self.prepared[spell_level - 1]:
            if spell.spell_name == spell_name and not spell.expended:
                spell.expended = True
                return
        raise ValueError(f"{spell_name} is not available at level {spell_level}")

    def restore_all(self) -> None:
        """Re-set all expended spells (after rest/re-memorization)."""
        for level_list in self.prepared:
            for spell in level_list:
                spell.expended = False


@dataclass
class InventoryItem:
    item_id:     UUID   = field(default_factory=uuid4)
    name:        str    = ""
    description: str    = ""
    quantity:    int    = 1
    encumbrance: float  = 1.0   # slots or stone, DM's choice of unit
    is_equipped: bool   = False


@dataclass
class Character:
    character_id:    UUID              = field(default_factory=uuid4)
    owner_id:        str | None     = None   # platform user ID (opaque string)
    name:            str               = ""
    character_class: CharacterClass    = CharacterClass.FIGHTER
    level:           int               = 1
    experience:      int               = 0

    ability_scores:  AbilityScores     = field(default_factory=AbilityScores)

    hp_max:          int               = 1
    hp_current:      int               = 1
    armor_class:     int               = 9     # set by create_character from CharacterCreationRules
    movement_speed:  int               = 120   # set by create_character from CharacterCreationRules

    saving_throws: dict = field(default_factory=dict)
    # Keys and values are ruleset-defined; populated by create_character from
    # CharacterCreationRules.default_saves. No key names are enforced here.

    status:          CharacterStatus   = CharacterStatus.ACTIVE
    status_notes:    str               = ""    # e.g. "fatigued", "poisoned (saves at -2)"

    inventory:       list[InventoryItem] = field(default_factory=list)
    gold:            int               = 0

    # Spellcasters only; None for non-casters
    spellbook:       SpellBook | None = None

    # Metadata
    created_at:      datetime          = field(default_factory=datetime.utcnow)
    is_pregenerated: bool              = False


# ---------------------------------------------------------------------------
# Dungeon map
# ---------------------------------------------------------------------------

@dataclass
class Exit:
    """A directional connection from one room to another."""
    exit_id:         UUID           = field(default_factory=uuid4)
    label:           str            = ""            # e.g. "north", "west", "ladder down"
    direction:       ExitDirection | None = None
    destination_id:  UUID | None = None          # None if room not yet authored
    door_state:      DoorState      = DoorState.OPEN
    description:     str            = ""            # "A wooden door reinforced with iron bars"
    notes:           str            = ""            # DM-facing notes (traps, keys, etc.)


@dataclass
class RoomFeature:
    """
    An interactive object or environmental detail in a room.
    State is a free-form string so the DM can track arbitrary changes
    (e.g. "intact" → "smashed", "locked" → "unlocked") without schema changes.
    """
    feature_id:  UUID = field(default_factory=uuid4)
    name:        str  = ""          # e.g. "Brass chandelier", "Pile of furs"
    description: str  = ""          # player-visible text
    state:       str  = "intact"    # current state, DM-mutable
    notes:       str  = ""          # DM-facing notes


@dataclass
class Room:
    room_id:     UUID               = field(default_factory=uuid4)
    name:        str                = ""
    description: str                = ""    # player-visible room description
    notes:       str                = ""    # DM-facing notes

    features:    list[RoomFeature]  = field(default_factory=list)
    exits:       list[Exit]         = field(default_factory=list)

    # Bookkeeping
    visited:     bool               = False
    authored:    bool               = True   # False = placeholder node in graph


@dataclass
class Dungeon:
    """
    The dungeon is a graph of rooms keyed by room_id.
    The DM may reveal rooms one at a time; unvisited rooms
    are authored=False placeholder nodes until the DM fills them in.
    """
    dungeon_id:   UUID                  = field(default_factory=uuid4)
    name:         str                   = ""
    description:  str                   = ""
    rooms:        dict[UUID, Room]      = field(default_factory=dict)
    entrance_id:  UUID | None        = None   # starting room


# ---------------------------------------------------------------------------
# NPCs
# ---------------------------------------------------------------------------

@dataclass
class NPC:
    """
    Ad-hoc NPC / monster entry. Structured enough to support a
    monster library later, but the DM can create entries on the fly.
    """
    npc_id:         UUID              = field(default_factory=uuid4)
    name:           str               = ""          # e.g. "Goblin A"
    description:    str               = ""
    hp_max:         int               = 1
    hp_current:     int               = 1
    armor_class:    int               = 9
    movement_speed: int               = 90
    attack_bonus:   int               = 0
    damage_dice:    str               = "1d6"       # e.g. "1d6", "2d4+1"
    morale:         int               = 7           # B/X morale score
    saving_throw:   int               = 15          # single value for simplicity
    xp_value:       int               = 0
    status:         str               = "active"    # free-form: active/dead/fled/charmed/etc.
    notes:          str               = ""          # DM-facing


# ---------------------------------------------------------------------------
# Turn system
# ---------------------------------------------------------------------------

@dataclass
class PlayerTurnSubmission:
    """One player's submitted action for the current turn."""
    character_id:  UUID               = field(default_factory=uuid4)
    submitted_at:  datetime           = field(default_factory=datetime.utcnow)
    action_text:   str                = ""      # free-form description
    is_latest:     bool               = True    # False if superseded by a resubmission


@dataclass
class TurnRecord:
    """
    Represents one dungeon turn (or one round if in combat).
    Open turns collect submissions; resolved turns are historical record.
    """
    turn_id:        UUID                          = field(default_factory=uuid4)
    turn_number:    int                           = 1
    mode:           SessionMode                   = SessionMode.EXPLORATION
    status:         TurnStatus                    = TurnStatus.OPEN
    opened_at:      datetime                      = field(default_factory=datetime.utcnow)
    due_at:         datetime | None            = None
    closed_at:      datetime | None            = None
    resolved_at:    datetime | None            = None

    # All submissions for this turn (including superseded ones)
    submissions:    list[PlayerTurnSubmission]    = field(default_factory=list)

    # DM's narrative resolution — written after processing
    resolution:     str                           = ""

    # Snapshot of game state at resolution time for the history log
    state_snapshot: dict | None                = None  # serialized GameState


# ---------------------------------------------------------------------------
# Party and session
# ---------------------------------------------------------------------------

@dataclass
class LightSource:
    """
    Tracks a single active light source.
    The DM sets the label and duration; the engine just counts down.

    Examples:
        LightSource(label="Torch", turns_remaining=6)
        LightSource(label="Lantern (oil flask 1)", turns_remaining=24)
        LightSource(label="Continual Light (Celes's shield)", turns_remaining=None)
    """
    label:           str           = ""
    turns_remaining: int | None = None   # None = permanent/magical, no countdown
    is_active:       bool          = True


@dataclass
class Oracle:
    """A player question to the DM, posted as a persistent Discord message."""
    oracle_id:       UUID           = field(default_factory=uuid4)
    number:          int            = 1       # resets each turn
    asker_name:      str            = ""
    asker_owner_id:  str | None  = None    # Discord user ID for DM notification
    question:        str            = ""
    answer:          str | None  = None
    message_id:      int | None  = None    # Discord message ID for in-place editing

    @property
    def question_text(self) -> str:
        """Formatted Discord message when the oracle is first posted."""
        return f"**Oracle #{self.number}** — {self.asker_name} asks: \"{self.question}\""

    @property
    def answer_text(self) -> str:
        """Formatted Discord message after the DM answers (replaces question_text)."""
        return f"{self.question_text}\n> {self.answer}"

    @property
    def player_dm_text(self) -> str:
        """Text sent privately to the player when their oracle is answered."""
        return (
            f"**Oracle #{self.number}** — "
            f"The DM answered your question: \"{self.question}\"\n"
            f"> {self.answer}"
        )


@dataclass
class Party:
    party_id:       UUID               = field(default_factory=uuid4)
    name:           str                = ""
    leader_id:      UUID | None     = None   # character_id of party leader
    member_ids:     list[UUID]         = field(default_factory=list)
    gold:           int                = 0
    experience:     int                = 0
    light_sources:  list[LightSource]  = field(default_factory=list)

    @property
    def active_light(self) -> LightSource | None:
        """Return the current active light source, if any."""
        for ls in self.light_sources:
            if ls.is_active:
                return ls
        return None


@dataclass
class GameState:
    """
    The complete, serializable state of one active session.
    This is the single source of truth the engine reads and writes.
    """
    session_id:      UUID                    = field(default_factory=uuid4)
    dungeon:         Dungeon | None       = None
    current_room_id: UUID | None          = None

    party:           Party | None         = None
    characters:      dict[UUID, Character]   = field(default_factory=dict)
    npcs:            list[NPC]               = field(default_factory=list)  # in current room

    mode:            SessionMode             = SessionMode.PRE_START
    turn_number:     int                     = 1
    rounds_started_at_turn: int | None    = None  # exploration turn when combat began
    current_turn:    TurnRecord | None    = None
    turn_history:    list[TurnRecord]        = field(default_factory=list)

    # In-channel log (clears each turn)
    say_log:         list[str]               = field(default_factory=list)

    # Oracle posts (persist across turns, number resets each turn)
    oracles:         list[Oracle]           = field(default_factory=list)
    oracle_counter:  int                     = 0  # increments per turn, resets on resolve

    # Session control
    session_active:      bool                 = True   # False = on hold

    # Turn timer config
    default_turn_hours: float                = 24.0  # default turn length in hours

    # Metadata
    created_at:      datetime                = field(default_factory=datetime.utcnow)
    updated_at:      datetime                = field(default_factory=datetime.utcnow)

    # Platform context (opaque — the engine doesn't interpret these)
    platform_channel_id: str | None      = None
    dm_user_id:          str | None      = None


    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    @property
    def current_room(self) -> Room | None:
        if self.dungeon is None or self.current_room_id is None:
            return None
        return self.dungeon.rooms.get(self.current_room_id)

    @property
    def active_characters(self) -> list[Character]:
        return [
            c for c in self.characters.values()
            if c.status == CharacterStatus.ACTIVE
        ]

    def get_character(self, character_id: UUID) -> Character | None:
        return self.characters.get(character_id)

    def latest_submission(self, character_id: UUID) -> PlayerTurnSubmission | None:
        """Return the most recent active submission for a character in the current turn."""
        if self.current_turn is None:
            return None
        active = [
            s for s in self.current_turn.submissions
            if s.character_id == character_id and s.is_latest
        ]
        return active[0] if active else None
