"""
Character management for the dungeon crawler engine.
"""

from models import (
    AbilityScores,
    Character,
    CharacterClass,
    CharacterStatus,
    GameState,
    InventoryItem,
    SpellBook,
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
from validation import validate_hp_value

from .dice import roll_sum
from .helpers import _err, _ok


def _con_modifier(con: int) -> int:
    """Get Constitution modifier."""
    return CON_HP_MODIFIER.get(con, 0)


def _dex_ac_modifier(dex: int) -> int:
    """DEX modifier to AC (subtract from descending AC)."""
    return ABILITY_MODIFIERS.get(dex, 0)


class CharacterManager:
    """Manages character creation and mutations."""

    def create_character(
        self,
        state:           GameState,
        name:            str,
        character_class: CharacterClass,
        equipment_package: str,
        owner_id:        str | None = None,
        ability_scores:  AbilityScores | None = None,   # pre-rolled, or we roll
        prerolled_stats: dict | None = None,             # from roll_stats() dict
    ):
        """
        Create a new level-1 character, add them to the session, and return the result.
        If prerolled_stats (dict) is provided, converts to AbilityScores and uses those.
        If ability_scores is provided directly, uses that.
        Otherwise rolls 3d6 straight.
        """
        from engine import _now

        if prerolled_stats is not None:
            ability_scores = AbilityScores(**prerolled_stats)
        if not name.strip():
            return _err(state, "Character name cannot be empty.")

        if equipment_package not in EQUIPMENT_PACKAGES:
            return _err(
                state,
                f"Unknown equipment package '{equipment_package}'. "
                      f"Valid options: {list(EQUIPMENT_PACKAGES.keys())}",
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

    def set_character_hp(
        self,
        state:        GameState,
        character_id,
        new_hp:       int,
    ):
        """Set a character's current HP."""
        from engine import _now
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
        self,
        state:        GameState,
        character_id,
        status:       CharacterStatus,
        notes:        str = "",
    ):
        """Set a character's status."""
        from engine import _now
        from validation import validate_description

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


def roll_stat_block() -> AbilityScores:
    """Roll 3d6 straight down the line for ability scores."""
    from .dice import roll_3d6

    return AbilityScores(
        strength=roll_3d6(),
        intelligence=roll_3d6(),
        wisdom=roll_3d6(),
        dexterity=roll_3d6(),
        constitution=roll_3d6(),
        charisma=roll_3d6(),
    )
