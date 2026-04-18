"""
Core data model for the async dungeon crawler engine.
No I/O, no platform dependencies — pure game state.

Ruleset-agnostic: CharacterClass is defined in tables.py and generated
from _CLASS_DEFINITIONS, so adding/removing classes only requires editing
tables.py. Nothing here changes when the ruleset changes.

Any update here requires a parallel update in serialization.py!
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from uuid import UUID, uuid4

from engine.data_loader import CONDITION_REGISTRY, ITEM_REGISTRY, CharacterClass
from engine.item import ContainerItem, EquipItem, Gear, Weapon

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Enumerations (non-ruleset — these don't change between game systems)
# ---------------------------------------------------------------------------


@dataclass
class JobExperience:
    """Tracks a character's progression in a single job (class).

    Serialized per-character so leveling history is fully reconstructable
    even if job definitions change later.
    """
    job_id: str
    level: int = 1
    hp_bonus: int = 0       # cumulative HP (POWER_LEVEL units) from all level-ups in this job
    stat_bonuses: dict[str, int] = field(default_factory=lambda: {
        "physique": 0, "finesse": 0, "reason": 0, "savvy": 0,
    })


@dataclass
class LevelUpResult:
    """Returned by engine.check_level_up / award_xp when a level-up occurs."""
    character_id: UUID
    character_name: str
    job_id: str
    new_level: int
    hp_gained: int              # POWER_LEVEL units
    stat_changes: dict[str, int]  # POWER_LEVEL units, non-zero entries only
    skills_granted: list = field(default_factory=list)  # list[SkillDef]


class CharacterStatus(Enum):
    ACTIVE    = "active"
    DEAD      = "dead"
    FLED      = "fled"       # unused
    PETRIFIED = "petrified"


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


class RangeBand(Enum):
    """
    Five-band positional system used during combat (ROUNDS mode).

    Players start on the minus side; NPCs start on the plus side.
    Adjacent bands are considered neighbouring for melee/reach weapons.

        FAR_MINUS  ←→  CLOSE_MINUS  ←→  ENGAGE  ←→  CLOSE_PLUS  ←→  FAR_PLUS
            -far           -close        engage        +close           +far
    """
    FAR_MINUS   = "far_minus"
    CLOSE_MINUS = "close_minus"
    ENGAGE      = "engage"
    CLOSE_PLUS  = "close_plus"
    FAR_PLUS    = "far_plus"


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
class AzureStats:
    """
    The four core stats of the Azure ruleset, stored as integers scaled by
    POWER_LEVEL (100).  A value of 200 means a stat of 2.00.
    """
    physique: int = 0
    finesse:  int = 0
    reason:   int = 0
    savvy:    int = 0

# Backward-compatibility alias — removed once all call sites are updated.
AbilityScores = AzureStats


@dataclass
class InventoryItem:
    item_id:      str
    quantity:     int    = 1
    equipped:     bool   = False
    broken:       bool   = False
    charges:      int | None = None
    notes:        str    = ""
    container_id: str | None = None   # instance_id of owning ContainerItem, if any
    instance_id:  str    = field(default_factory=lambda: __import__("uuid").uuid4().hex)

    @property
    def definition(self):
        # Returns Item: No return type hint to avoid imports
        return ITEM_REGISTRY[self.item_id]


@dataclass
class Character:
    character_id:    UUID              = field(default_factory=uuid4)
    owner_id:        str | None     = None   # platform user ID (opaque string)
    name:            str               = ""
    jobs:            dict[str, JobExperience] = field(default_factory=dict)
    level:           int               = 1   # mirrors primary job's level; kept in sync by level_up
    experience:      int               = 0

    @property
    def character_class(self) -> CharacterClass:
        """Derive the primary class from the jobs dict. Backward-compat for all existing readers."""
        if self.jobs:
            return CharacterClass[next(iter(self.jobs)).upper()]
        return CharacterClass.KNIGHT  # unreachable in normal flow

    ability_scores:  AzureStats        = field(default_factory=AzureStats)

    hp_max:          int               = 1
    hp_current:      int               = 1
    movement_speed:  int               = 120   # set by create_character from CharacterCreationRules

    saving_throws: dict = field(default_factory=dict)
    # Keys and values are ruleset-defined; populated by create_character from
    # CharacterCreationRules.default_saves. No key names are enforced here.

    status:          CharacterStatus   = CharacterStatus.ACTIVE
    status_notes:    str               = ""    # e.g. "fatigued", "poisoned (saves at -2)"

    inventory:       list[InventoryItem] = field(default_factory=list)
    gold:            int               = 0

    # Maps equipment slot keys (opaque strings defined by the ruleset engine)
    # to the item_id of the equipped InventoryItem, or None if empty.
    # The default empty dict is intentional: engine/character.py populates the
    # correct slot keys for the active ruleset when creating a character.
    equipped_slots:  dict[str, str | None] = field(default_factory=dict)

    active_conditions: list[ActiveCondition] = field(default_factory=list)

    # Metadata
    created_at:      datetime          = field(default_factory=lambda: datetime.now(UTC))
    is_pregenerated: bool              = False

    @property
    def defense(self) -> int:
        """Sum DEF from equipped Gear items plus active condition modifiers, floored at 0."""
        total = 0
        for item_id in self.equipped_slots.values():
            if item_id is None:
                continue
            definition = ITEM_REGISTRY.get(item_id)
            if isinstance(definition, Gear):
                total += definition.defense
        total += sum(
            CONDITION_REGISTRY[c.condition_id].stat_modifiers.get("defense", 0) * c.stacks
            for c in self.active_conditions
            if c.condition_id in CONDITION_REGISTRY
        )
        return max(0, total)

    @property
    def resistance(self) -> int:
        """Sum RST from equipped Gear items plus active condition modifiers, floored at 0."""
        total = 0
        for item_id in self.equipped_slots.values():
            if item_id is None:
                continue
            definition = ITEM_REGISTRY.get(item_id)
            if isinstance(definition, Gear):
                total += definition.resistance
        total += sum(
            CONDITION_REGISTRY[c.condition_id].stat_modifiers.get("resistance", 0) * c.stacks
            for c in self.active_conditions
            if c.condition_id in CONDITION_REGISTRY
        )
        return max(0, total)

    def effective_stat(self, stat: str) -> int:
        """Return ability score for `stat` including any active condition modifiers."""
        base = getattr(self.ability_scores, stat, 0)
        bonus = sum(
            CONDITION_REGISTRY[c.condition_id].stat_modifiers.get(stat, 0) * c.stacks
            for c in self.active_conditions
            if c.condition_id in CONDITION_REGISTRY
        )
        return base + bonus

    @property
    def dodge(self) -> int:
        """Dodge target number (base = finesse). Capped at POWER_LEVEL when any Heavy item is equipped."""
        from engine.azure_constants import POWER_LEVEL
        base = self.ability_scores.finesse
        for item_id in self.equipped_slots.values():
            if item_id is None:
                continue
            definition = ITEM_REGISTRY.get(item_id)
            if isinstance(definition, EquipItem) and "Heavy" in definition.getTags():
                return min(base, POWER_LEVEL)
        return base

    def equipped_weapon(self) -> InventoryItem | None:
        """
        Return the InventoryItem in the main-hand slot, or None.
        Uses the hard-coded slot key 'main_hand'; if the ruleset changes this
        key the engine should override this method.
        """
        item_id = self.equipped_slots.get("main_hand")
        if item_id is None:
            return None
        return next((i for i in self.inventory if i.item_id == item_id), None)

    def equipped_weapons(self) -> list[tuple[InventoryItem, Weapon]]:
        """
        Return all accessible weapons as (inv_item, definition) pairs.

        - Regular Weapon/ChargeWeapon in main_hand → one or two entries:
            - Always the base entry (Physique stat).
            - If the weapon has the "Agile" tag, also a synthetic finesse-variant
              entry with item_id "<id>__agile" and name "<name> [Agile]".
        - ContainerItem in main_hand → one entry per contained spell,
          each sharing the container's InventoryItem.
        - No weapon equipped → empty list.

        The first entry is used for attacks when no explicit weapon is chosen.
        Synthetic Agile entries are never in char.inventory; they are produced
        here and identified by their virtual item_id in CombatAction.weapon_id.
        """
        import copy as _copy
        inv_item = self.equipped_weapon()
        if inv_item is None:
            return []
        definition = ITEM_REGISTRY.get(inv_item.item_id)
        if definition is None:
            return []
        if isinstance(definition, ContainerItem):
            container_item_id = inv_item.item_id
            results = []
            for spell_id in definition.contained_item_ids:
                spell_def = ITEM_REGISTRY.get(spell_id)
                if spell_def is None or not isinstance(spell_def, Weapon):
                    continue
                spell_inv = next(
                    (i for i in self.inventory
                     if i.item_id == spell_id and i.container_id == container_item_id),
                    None,
                )
                if spell_inv is not None:
                    results.append((spell_inv, spell_def))
            return results
        if isinstance(definition, Weapon):
            results = [(inv_item, definition)]
            if "Agile" in definition.getTags() and getattr(definition, "stat", "physique") == "physique":
                agile_def = _copy.copy(definition)
                agile_def.stat = "finesse"
                agile_def.name = f"{definition.name} [Agile]"
                agile_inv = _copy.copy(inv_item)
                agile_inv.item_id = f"{inv_item.item_id}__agile"
                results.append((agile_inv, agile_def))
            for tag in definition.getTags():
                if tag.startswith("Throwable "):
                    try:
                        throw_range = int(tag.split()[-1])
                    except ValueError:
                        continue
                    throwable_def = _copy.copy(definition)
                    throwable_def.range = throw_range
                    throwable_def.name = f"{definition.name} [Throwable]"
                    throwable_inv = _copy.copy(inv_item)
                    throwable_inv.item_id = f"{inv_item.item_id}__throwable"
                    results.append((throwable_inv, throwable_def))
                    break  # one throwable variant per weapon
            return results
        return []

    def items_in_slot(self, slot_key: str) -> list[InventoryItem]:
        """Return InventoryItem(s) occupying the given slot key (0 or 1 items)."""
        item_id = self.equipped_slots.get(slot_key)
        if item_id is None:
            return []
        return [i for i in self.inventory if i.item_id == item_id]

    @property
    def inventory_size(self) -> int:
        """Maximum inventory slots: BASE_INVENTORY_SIZE + floor(PHY / POWER_LEVEL)."""
        from engine.azure_constants import BASE_INVENTORY_SIZE, POWER_LEVEL
        return BASE_INVENTORY_SIZE + (self.ability_scores.physique // POWER_LEVEL)

    @property
    def slots_used(self) -> int:
        """Slots currently occupied by unequipped items.

        Non-light items cost slot_cost * quantity slots each.
        Light items (is_light=True) are bundled: all light item quantities are
        summed across types and divided by BUNDLE_SIZE, rounded up.
        """
        from math import ceil

        from engine.azure_constants import BUNDLE_SIZE
        non_light = 0
        light_qty = 0
        for inv_item in self.inventory:
            if inv_item.equipped:
                continue
            if inv_item.container_id is not None:
                continue  # contained items don't occupy their own inventory slots
            defn = ITEM_REGISTRY.get(inv_item.item_id)
            if defn is not None and defn.isLight:
                light_qty += inv_item.quantity
            else:
                non_light += (defn.slot_cost if defn is not None else 1) * inv_item.quantity
        return non_light + (ceil(light_qty / BUNDLE_SIZE) if light_qty > 0 else 0)


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
    auto_move:       bool           = False         # skip DM approval on /abscond
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
    visited:       bool               = False
    authored:      bool               = True   # False = placeholder node in graph
    exploration_xp: int               = 0      # XP per character on first visit (0 = use DEFAULT_ROOM_XP)

    # Encounter modifier — multiplies the global random encounter threshold.
    # 0.0 = safe room (no encounters). 2.0 = double the encounter chance.
    random_encounter_modifier: float  = 1.0


@dataclass
class EncounterEntry:
    """A weighted entry in a dungeon's random encounter roster."""
    npc_group: NPCGroup
    weight:    int = 1


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

    # Random encounter system
    random_encounter_interval: int                  = 6      # turns between checks
    random_encounter_roll:     str                  = "1d6"  # dice expression
    random_encounter_roster:   list[EncounterEntry] = field(default_factory=list)


# ---------------------------------------------------------------------------
# NPCs
# ---------------------------------------------------------------------------


class NPCMovementLogic(Enum):
    """Movement behavior for NPC groups."""
    STATIONARY = "stationary"       # stays in one room
    WANDERING = "wandering"         # moves between adjacent rooms
    RANDOM_PLACEMENT = "random"     # can appear in any of its designated rooms
    PATROL = "patrol"               # follows a fixed route between rooms


@dataclass
class NPCGroup:
    """
    A group of NPCs that move together as a unit.

    Attributes:
        group_id: Unique identifier for this group
        name: Optional display name for the group
        npcs: List of NPCs belonging to this group
        possible_rooms: List of room IDs where this group may be found
        movement_logic: How the group moves between rooms
        current_room_id: The room where this group is currently located (None if not yet placed)
        patrol_route: Optional ordered list of room IDs for patrol movement
    """
    group_id: UUID                  = field(default_factory=uuid4)
    name: str | None                = None
    npcs: list[NPC]                 = field(default_factory=list)
    possible_rooms: list[UUID]      = field(default_factory=list)
    movement_logic: NPCMovementLogic = NPCMovementLogic.STATIONARY
    current_room_id: UUID | None    = None
    patrol_route: list[UUID]        = field(default_factory=list)

    def remove_npc(self, npc_id: UUID) -> bool:
        """Remove an NPC from the group. Returns True if removed, False if not found."""
        for i in range(len(self.npcs)):
            if self.npcs[i].npc_id == npc_id:
                del self.npcs[i]
            return True
        return False

@dataclass
class NPCRoster:
    """
    A roster of NPC groups referenced by GameState.

    The roster maintains all NPC groups in the dungeon, with each group
    tracking its current location. This allows NPCs to persist across
    room changes and enables future features like wandering monsters.

    Attributes:
        groups: Dictionary mapping group_id to NPCGroup
    """
    groups: dict[UUID, NPCGroup]    = field(default_factory=dict)

    def add_group(self, group: NPCGroup) -> None:
        """Add an NPC group to the roster."""
        self.groups[group.group_id] = group

    def remove_group(self, group_id: UUID) -> bool:
        """Remove a group from the roster. Returns True if removed, False if not found."""
        if group_id in self.groups:
            del self.groups[group_id]
            return True
        return False

    def get_group(self, group_id: UUID) -> NPCGroup | None:
        """Get an NPC group by ID."""
        return self.groups.get(group_id)

    def get_groups_in_room(self, room_id: UUID) -> list[NPCGroup]:
        """Get all NPC groups currently in a specific room."""
        return [g for g in self.groups.values() if g.current_room_id == room_id]

    def get_group_in_room(self, room_id: UUID) -> NPCGroup | None:
        """Get the first NPC group found in a specific room, or None if no groups exist there."""
        groups = self.get_groups_in_room(room_id)
        return groups[0] if groups else None

    def get_npcs_in_room(self, room_id: UUID) -> list[NPC]:
        """Get all NPCs from groups currently in a specific room."""
        npcs = []
        for group in self.get_groups_in_room(room_id):
            npcs.extend(group.npcs)
        return npcs

    def move_group_to_room(self, group_id: UUID, room_id: UUID) -> bool:
        """Move an NPC group to a new room. Returns True if successful."""
        group = self.get_group(group_id)
        if group is None:
            return False
        # Verify the room is in the group's possible rooms (if any are specified)
        if group.possible_rooms and room_id not in group.possible_rooms:
            return False
        group.current_room_id = room_id
        return True

    def all_npcs(self) -> list[NPC]:
        """Get all NPCs from all groups in the roster."""
        npcs = []
        for group in self.groups.values():
            npcs.extend(group.npcs)
        return npcs


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
    defense:        int               = 0
    resistance:     int               = 0
    ability_scores: AzureStats        = field(default_factory=AzureStats)
    movement_speed: int               = 90
    attack_bonus:   int               = 0
    damage_dice:    str               = "1d6"       # e.g. "1d6", "2d4+1"
    morale:         int               = 7           # B/X morale score
    saving_throw:   int               = 15          # single value for simplicity
    hit_dice:       int               = 1           # used to compute XP on kill (hit_dice * 100)
    weapon_range:   int               = 0           # max band-step distance for attacks (0 = ENGAGE only)
    status:            str               = "active"    # free-form: active/dead/fled/charmed/etc.
    notes:             str               = ""          # DM-facing
    active_conditions: list[ActiveCondition] = field(default_factory=list)
    hidden:            bool              = False       # not shown to players until DM reveals

    @property
    def dodge(self) -> int:
        """Dodge target number for NPCs — equals finesse (no equipment)."""
        return self.ability_scores.finesse


# ---------------------------------------------------------------------------
# Turn system
# ---------------------------------------------------------------------------

@dataclass
class PlayerTurnSubmission:
    """One player's submitted action for the current turn."""
    character_id:  UUID               = field(default_factory=uuid4)
    submitted_at:  datetime           = field(default_factory=lambda: datetime.now(UTC))
    action_text:   str                = ""      # free-form description
    is_latest:     bool               = True    # False if superseded by a resubmission
    # Structured combat action — populated in ROUNDS mode when the player
    # uses an action button rather than free-text Affect.  None in exploration
    # mode and for Affect submissions.  Stored as a plain dict so that
    # serialization requires no special handling beyond json.dumps/loads;
    # Phase 2 will introduce a typed CombatAction dataclass that wraps this.
    combat_action: dict | None     = None


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
    opened_at:      datetime                      = field(default_factory=lambda: datetime.now(UTC))
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


# ---------------------------------------------------------------------------
# Combat
# ---------------------------------------------------------------------------

@dataclass
class ActiveCondition:
    """
    An instance of a status condition currently affecting a combatant.

    condition_id references a key in the CONDITION_REGISTRY loaded by
    engine/data_loader.py.  duration_rounds=None means permanent (lasts
    until explicitly removed).  source_id is the combatant that applied it,
    if any (used for some condition-removal rules).
    """
    condition_id:    str         = ""
    duration_rounds: int | None = None
    source_id:       UUID | None = None
    stacks:          int         = 1


@dataclass
class CombatantState:
    """
    Per-combatant runtime state that only exists during ROUNDS mode.

    Keyed by combatant_id in CombatBattlefield.combatants.
    combatant_id is a character_id (UUID) for players or an npc_id (UUID)
    for NPCs.

    skip_action and movement_blocked are single-round flags set by condition
    hooks (stunned, entangled) and cleared at the end of each round by
    auto_resolve_round. They are never persisted.
    """
    combatant_id:      UUID                    = field(default_factory=uuid4)
    is_player:         bool                    = True
    range_band:        RangeBand               = RangeBand.FAR_MINUS
    initiative:        int                     = 0
    acted_this_round:  bool                    = False
    skip_action:       bool                    = False
    movement_blocked:  bool                    = False
    used_move:         bool                    = False
    used_oracle:       bool                    = False


@dataclass
class CombatBattlefield:
    """
    Complete combat state for an ongoing ROUNDS encounter.

    Lives on GameState.battlefield; None outside of ROUNDS mode.
    combatants maps combatant_id → CombatantState for every participant
    (both player characters and NPCs) present when enter_rounds was called.
    round_log accumulates a plain-text narrative for each auto-resolved
    action within the current round.
    """
    combatants:        dict[UUID, CombatantState] = field(default_factory=dict)
    round_log:         list[str]                  = field(default_factory=list)
    abscond_succeeded: bool                       = False


@dataclass
class Party:
    party_id:       UUID               = field(default_factory=uuid4)
    name:           str                = ""
    leader_id:      UUID | None     = None   # character_id of party leader
    member_ids:     list[UUID]         = field(default_factory=list)
    gold:           int                = 0


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

    # NPC roster contains all NPCs in the dungeon, organized by group
    npc_roster:      NPCRoster               = field(default_factory=NPCRoster)

    mode:            SessionMode             = SessionMode.PRE_START
    turn_number:     int                     = 1
    rounds_started_at_turn: int | None    = None  # exploration turn when combat began
    last_encounter_check_turn: int           = 0   # tracks random encounter timer
    current_turn:    TurnRecord | None    = None
    turn_history:    list[TurnRecord]        = field(default_factory=list)

    # In-channel log (clears each turn)
    say_log:         list[str]               = field(default_factory=list)

    # Oracle posts (persist across turns, number resets each turn)
    oracles:         list[Oracle]           = field(default_factory=list)
    oracle_counter:  int                     = 0  # increments per turn, resets on resolve

    # Session control
    session_active:      bool                 = True   # False = on hold

    # Combat — populated by enter_rounds(), cleared by exit_rounds()
    battlefield:         CombatBattlefield | None = None

    # Turn timer config
    default_turn_hours: float                = 24.0  # default turn length in hours

    # Metadata
    created_at:      datetime                = field(default_factory=lambda: datetime.now(UTC))
    updated_at:      datetime                = field(default_factory=lambda: datetime.now(UTC))

    # Platform context (opaque — the engine doesn't interpret these)
    platform_channel_id: str | None      = None
    dm_user_id:          str | None      = None

    # Ephemeral notification queue — filled by award_xp, drained by dispatch layer
    pending_level_ups:   list = field(default_factory=list)  # list[LevelUpResult]


    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    @property
    def current_room(self) -> Room | None:
        if self.dungeon is None or self.current_room_id is None:
            return None
        return self.dungeon.rooms.get(self.current_room_id)

    @property
    def npcs_in_current_room(self) -> list[NPC]:
        """Get all NPCs from the roster that are currently in the party's room."""
        if self.current_room_id is None:
            return []
        return self.npc_roster.get_npcs_in_room(self.current_room_id)

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
