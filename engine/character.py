"""
Character management for the dungeon crawler engine.
"""

from engine.azure_constants import (
    ACCESSORY_SLOTS,
    DEFAULT_EQUIPPED_SLOTS,
    GEAR_SLOT_MAP,
    ItemSlot,
)
from engine.azure_engine import CREATION_RULES, POWER_LEVEL
from engine.azure_helpers import getLowerWeaponRanks
from engine.data_loader import CLASS_DEFINITIONS, ITEM_REGISTRY
from engine.item import ChargeWeapon, ContainerItem, EquipItem, Weapon
from models import (
    AzureStats,
    Character,
    CharacterClass,
    CharacterStatus,
    GameState,
    InventoryItem,
    JobExperience,
    LevelUpResult,
)
from validation import validate_hp_value

from .dice import roll_stat_block
from .helpers import _err, _ok


class CharacterManager:
    """Manages character creation and mutations."""

    def create_character(
        self,
        state:             GameState,
        name:              str,
        character_class:   CharacterClass,
        equipment_package: str = "",        # unused in Azure; kept for API compat
        owner_id:          str | None = None,
        ability_scores:    AzureStats | None = None,
        prerolled_stats:   dict | None = None,
    ):
        """
        Create a new level-1 character and add them to the session.

        Azure creation flow:
          1. Roll or accept base stats (four Azure stats, scaled by POWER_LEVEL).
          2. Look up job rules from CREATION_RULES.
          3. HP = hit_die * POWER_LEVEL (full HP at level 1).
          4. base_save = job.baseSave * POWER_LEVEL.
          5. Empty inventory — items are assigned separately.
          6. No spellbook — spells come from skill progression.

        prerolled_stats, if provided, must be a dict with keys
        physique / finesse / reason / savvy (as returned by roll_stats()).
        """
        from engine import _now

        if prerolled_stats is not None:
            ability_scores = AzureStats(
                physique=prerolled_stats.get("physique", 0),
                finesse=prerolled_stats.get("finesse",  0),
                reason=prerolled_stats.get("reason",   0),
                savvy=prerolled_stats.get("savvy",     0),
            )

        if not name.strip():
            return _err(state, "Character name cannot be empty.")

        scores = ability_scores if ability_scores is not None else roll_stat_block()
        rules  = CREATION_RULES[character_class]

        hp_max    = rules.hitDie   * POWER_LEVEL
        base_save = rules.baseSave * POWER_LEVEL

        job_key = character_class.name.lower()  # e.g. "knight"
        character = Character(
            owner_id=owner_id,
            name=name,
            jobs={job_key: JobExperience(job_id=job_key, level=1)},
            level=1,
            experience=0,
            ability_scores=scores,
            hp_max=hp_max,
            hp_current=hp_max,
            movement_speed=120,
            saving_throws={"save": base_save},
            gold=100, #Placeholder value to test arrive shopping
            inventory=[],
            equipped_slots=dict(DEFAULT_EQUIPPED_SLOTS),  # fresh copy per character
        )

        state.characters[character.character_id] = character

        if state.party is not None:
            state.party.member_ids.append(character.character_id)

        state.updated_at = _now()

        msg = (
            f"{name} the {character_class.value} joins the party! "
            f"HP: {hp_max}  Save: {base_save}"
        )
        return _ok(state, msg)

    def set_character_hp(self, state: GameState, character_id, new_hp: int):
        from engine import _now
        char = state.characters.get(character_id)
        if char is None:
            return _err(state, f"Character {character_id} not found.")
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
        self, state: GameState, character_id, status: CharacterStatus, notes: str = ""
    ):
        from engine import _now
        from validation import validate_description
        char = state.characters.get(character_id)
        if char is None:
            return _err(state, f"Character {character_id} not found.")
        notes_result = validate_description(notes, "Status notes", max_length=200, allow_empty=True)
        if not notes_result:
            return _err(state, notes_result.error)
        char.status = status
        char.status_notes = notes_result.value
        state.updated_at = _now()
        return _ok(state, f"{char.name} status → {status.value}. {notes_result.value}".strip())

    # ------------------------------------------------------------------
    # Equip / Unequip
    # ------------------------------------------------------------------

    _PHYSICAL_RANKS = {"E", "D", "C", "B", "A"}
    _ARCANE_RANKS   = {"V", "W", "X", "Y", "Z"}

    def _char_allowed_ranks(self, char: Character, rank_type: str) -> set:
        """
        Return the set of item ranks the character is allowed to equip for the
        given rank_type ("weapon", "armor", or "spell").

        Unions across all jobs so multi-class characters get the best rank.
        Returns an empty set if no rank is available for that category.
        """
        allowed: set[str] = set()
        for job_key in char.jobs:
            job_def = CLASS_DEFINITIONS.get(job_key.upper())
            if job_def is None:
                continue
            if rank_type == "weapon":
                allowed |= getLowerWeaponRanks(job_def.weapon_rank)
            elif rank_type == "armor":
                allowed |= getLowerWeaponRanks(job_def.armor_rank)
            elif rank_type == "spell" and job_def.spell_rank is not None:
                allowed |= getLowerWeaponRanks(job_def.spell_rank)
        return allowed

    def _resolve_target_slot(
        self,
        char: Character,
        inv_item,
        requested_slot: ItemSlot | None,
    ) -> "tuple[ItemSlot, str] | tuple[None, str]":
        """
        Work out which ItemSlot the item should go into.

        Returns (slot, error_message).  On success error_message is "".
        On failure slot is None.
        """
        definition = ITEM_REGISTRY.get(inv_item.item_id)
        if definition is None:
            return None, f"Unknown item '{inv_item.item_id}'."

        if not isinstance(definition, EquipItem):
            return None, f"'{definition.name}' cannot be equipped (not a weapon or gear piece)."

        slot_str: str | None = getattr(definition, "slot", None)
        if not slot_str:
            return None, f"'{definition.name}' cannot be equipped (no equipment slot defined)."

        # --- Accessories: special auto-pick logic ---
        if slot_str == "accessory":
            item_rank = definition.rank
            if item_rank in self._PHYSICAL_RANKS:
                allowed = self._char_allowed_ranks(char, "armor")
                if item_rank not in allowed:
                    return None, (
                        f"{char.name} cannot equip '{definition.name}' "
                        f"(requires armor rank {item_rank})."
                    )
            if requested_slot in ACCESSORY_SLOTS:
                return requested_slot, ""
            if requested_slot is not None:
                return None, "Accessories can only go into accessory1 or accessory2 slots."
            for slot in ACCESSORY_SLOTS:
                if char.equipped_slots.get(slot.value) is None:
                    return slot, ""
            return None, "Both accessory slots are already filled."

        # --- All other slots: map string → ItemSlot ---
        mapped = GEAR_SLOT_MAP.get(slot_str)
        if mapped is None:
            return None, f"'{definition.name}' has an unrecognised slot '{slot_str}'."

        if requested_slot is not None and requested_slot != mapped:
            return None, (
                f"'{definition.name}' belongs in the {mapped.value} slot, "
                f"not {requested_slot.value}."
            )

        # --- Rank check ---
        # Rank category: arcane (V–Z) → spell_rank; physical (E–A) → weapon_rank
        # for Weapons, armor_rank for Gear/Containers.  isinstance is still the
        # correct discriminator here because a shield (Gear) in OFF_HAND uses
        # armor_rank while an off-hand weapon (Weapon) uses weapon_rank.
        item_rank = definition.rank
        if item_rank in self._ARCANE_RANKS:
            allowed = self._char_allowed_ranks(char, "spell")
            if item_rank not in allowed:
                return None, (
                    f"{char.name} cannot equip '{definition.name}' "
                    f"(requires spell rank {item_rank})."
                )
        elif item_rank in self._PHYSICAL_RANKS:
            if isinstance(definition, Weapon):
                allowed = self._char_allowed_ranks(char, "weapon")
                rank_label = "weapon"
            else:
                allowed = self._char_allowed_ranks(char, "armor")
                rank_label = "armor"
            if item_rank not in allowed:
                return None, (
                    f"{char.name} cannot equip '{definition.name}' "
                    f"(requires {rank_label} rank {item_rank})."
                )

        return mapped, ""

    def equip_item(
        self,
        state: GameState,
        character_id,
        item_id: str,
        slot: ItemSlot | None = None,
    ):
        """
        Equip an item from the character's inventory.

        - If the target slot is already occupied the previous item is
          automatically unequipped first (its equipped flag is cleared
          and the slot is freed before the new item goes in).
        - For accessory slots the caller may specify slot=ACCESSORY1 or
          ACCESSORY2 explicitly; if omitted the first free slot is used.
        - Returns EngineResult with .ok=True on success.
        """
        from engine import _now
        char = state.characters.get(character_id)
        if char is None:
            return _err(state, f"Character {character_id} not found.")

        inv_item = next((i for i in char.inventory if i.item_id == item_id), None)
        if inv_item is None:
            return _err(state, f"Item '{item_id}' not in {char.name}'s inventory.")

        target_slot, err = self._resolve_target_slot(char, inv_item, slot)
        if target_slot is None:
            return _err(state, err)

        # Unequip whatever is currently in that slot
        existing_id = char.equipped_slots.get(target_slot.value)
        if existing_id is not None:
            existing = next((i for i in char.inventory if i.item_id == existing_id), None)
            if existing:
                existing.equipped = False
            char.equipped_slots[target_slot.value] = None

        # Equip the new item
        inv_item.equipped = True
        char.equipped_slots[target_slot.value] = item_id

        definition = ITEM_REGISTRY.get(item_id)
        item_name = definition.name if definition else item_id
        state.updated_at = _now()
        return _ok(state, f"{char.name} equipped {item_name} in the {target_slot.value} slot.")

    def give_item(
        self,
        state: GameState,
        character_id,
        item_id: str,
        quantity: int = 1,
    ):
        """
        Add one or more copies of an item to a character's inventory,
        enforcing the inventory slot limit.

        Stacking rules:
        - Light items (is_light=True) are bundled BUNDLE_SIZE-per-slot across
          all light item types; the capacity check uses bundle math.
        - ChargeWeapons are never stacked (each has independent charge state);
          a new InventoryItem entry is always created.
        - All other items stack onto an existing unequipped entry if one
          exists; each unit consumes slot_cost slots.
        """
        from math import ceil

        from engine import _now
        from engine.azure_constants import BUNDLE_SIZE

        char = state.characters.get(character_id)
        if char is None:
            return _err(state, f"Character {character_id} not found.")

        defn = ITEM_REGISTRY.get(item_id)
        if defn is None:
            return _err(state, f"Unknown item '{item_id}'.")

        slot_cost = defn.slot_cost

        if defn.isLight:
            # Bundle-aware capacity check: all light items share bundle slots.
            current_light = sum(
                i.quantity for i in char.inventory
                if not i.equipped
                and (d := ITEM_REGISTRY.get(i.item_id)) is not None
                and d.isLight
            )
            current_light_slots = ceil(current_light / BUNDLE_SIZE) if current_light else 0
            new_light_slots = ceil((current_light + quantity) / BUNDLE_SIZE)
            non_light_slots = char.slots_used - current_light_slots
            if non_light_slots + new_light_slots > char.inventory_size:
                return _err(
                    state,
                    f"{char.name}'s inventory is full "
                    f"({char.slots_used}/{char.inventory_size} slots used).",
                )
            existing = next(
                (i for i in char.inventory if i.item_id == item_id and not i.equipped),
                None,
            )
            if existing is not None:
                existing.quantity += quantity
            else:
                char.inventory.append(InventoryItem(item_id=item_id, quantity=quantity))

        elif isinstance(defn, ChargeWeapon):
            # Never stack charged items — each needs its own charge counter.
            if slot_cost > 0 and char.slots_used + slot_cost * quantity > char.inventory_size:
                return _err(
                    state,
                    f"{char.name}'s inventory is full "
                    f"({char.slots_used}/{char.inventory_size} slots used).",
                )
            char.inventory.append(InventoryItem(
                item_id=item_id,
                quantity=quantity,
                charges=defn.maxCharges,
            ))

        elif isinstance(defn, ContainerItem):
            # Containers are never stacked — each is its own InventoryItem.
            if slot_cost > 0 and char.slots_used + slot_cost * quantity > char.inventory_size:
                return _err(
                    state,
                    f"{char.name}'s inventory is full "
                    f"({char.slots_used}/{char.inventory_size} slots used).",
                )
            for _ in range(quantity):
                char.inventory.append(InventoryItem(item_id=item_id, quantity=1))

        else:
            if slot_cost > 0 and char.slots_used + slot_cost * quantity > char.inventory_size:
                return _err(
                    state,
                    f"{char.name}'s inventory is full "
                    f"({char.slots_used}/{char.inventory_size} slots used).",
                )
            existing = next(
                (i for i in char.inventory if i.item_id == item_id and not i.equipped),
                None,
            )
            if existing is not None:
                existing.quantity += quantity
            else:
                char.inventory.append(InventoryItem(item_id=item_id, quantity=quantity))

        state.updated_at = _now()
        qty_str = f"{quantity}x " if quantity > 1 else ""
        return _ok(state, f"{char.name} received {qty_str}{defn.name}.")

    def remove_item(
        self,
        state: GameState,
        character_id,
        item_id: str,
        quantity: int = 1,
    ):
        """
        Remove one or more copies of an item from a character's inventory.

        - Equipped items must be unequipped before removal.
        - For stacked entries, decrements quantity; removes the entry when
          quantity reaches zero.
        - quantity must be >= 1 and <= the entry's current quantity.
        """
        from engine import _now
        char = state.characters.get(character_id)
        if char is None:
            return _err(state, f"Character {character_id} not found.")

        inv_item = next((i for i in char.inventory if i.item_id == item_id and not i.equipped), None)
        if inv_item is None:
            # Give a more specific error if the item exists but is equipped
            if any(i.item_id == item_id and i.equipped for i in char.inventory):
                defn = ITEM_REGISTRY.get(item_id)
                name = defn.name if defn else item_id
                return _err(state, f"{name} is equipped and must be unequipped first.")
            return _err(state, f"Item '{item_id}' not in {char.name}'s inventory.")

        if quantity < 1 or quantity > inv_item.quantity:
            return _err(
                state,
                f"Cannot remove {quantity}x '{item_id}': only {inv_item.quantity} available.",
            )

        defn = ITEM_REGISTRY.get(item_id)
        name = defn.name if defn else item_id

        inv_item.quantity -= quantity
        if inv_item.quantity == 0:
            char.inventory.remove(inv_item)

        state.updated_at = _now()
        qty_str = f"{quantity}x " if quantity > 1 else ""
        return _ok(state, f"{qty_str}{name} removed from {char.name}'s inventory.")

    def unequip_item(
        self,
        state: GameState,
        character_id,
        slot: ItemSlot,
    ):
        """
        Unequip whatever item is in the given slot.

        Returns EngineResult with .ok=True on success.
        """
        from engine import _now
        char = state.characters.get(character_id)
        if char is None:
            return _err(state, f"Character {character_id} not found.")

        item_id = char.equipped_slots.get(slot.value)
        if item_id is None:
            return _err(state, f"{char.name} has nothing equipped in the {slot.value} slot.")

        inv_item = next((i for i in char.inventory if i.item_id == item_id), None)
        if inv_item:
            inv_item.equipped = False

        char.equipped_slots[slot.value] = None

        definition = ITEM_REGISTRY.get(item_id)
        item_name = definition.name if definition else item_id
        state.updated_at = _now()
        return _ok(state, f"{char.name} unequipped {item_name} from the {slot.value} slot.")

    # ------------------------------------------------------------------
    # XP and level-up
    # ------------------------------------------------------------------

    def award_xp(self, state: GameState, character_id, amount: int):
        """Award XP to a character and trigger any resulting level-ups.

        Returns an EngineResult whose .data is a list[LevelUpResult]
        (empty if no level-up occurred).
        """
        from engine import _now
        char = state.characters.get(character_id)
        if char is None:
            return _err(state, f"Character {character_id} not found.")
        if amount < 1:
            return _err(state, "XP amount must be at least 1.")
        char.experience += amount
        state.updated_at = _now()
        level_ups = self.check_level_up(state, character_id)
        msg = f"{char.name} gained {amount} XP."
        if level_ups:
            msg += " " + " ".join(f"Level {r.new_level}!" for r in level_ups)
        result = _ok(state, msg)
        result.data = level_ups
        return result

    def distribute_xp(self, state: GameState, total: int) -> list[LevelUpResult]:
        """Award total XP split evenly among all ACTIVE characters.

        Remainder is discarded (integer floor division). Returns flat list of
        all LevelUpResult objects across all characters.
        """
        from models import CharacterStatus
        active = [c for c in state.characters.values()
                  if c.status == CharacterStatus.ACTIVE]
        if not active or total < 1:
            return []
        each = total // len(active)
        if each < 1:
            return []
        all_level_ups: list[LevelUpResult] = []
        for char in active:
            result = self.award_xp(state, char.character_id, each)
            all_level_ups.extend(result.data or [])
        return all_level_ups

    def check_level_up(self, state: GameState, character_id) -> list[LevelUpResult]:
        """Check whether char has enough XP to level up; apply all pending levels.

        Safe to call at any time (e.g. after manual XP edits).
        Returns a list of LevelUpResult — one per level gained, empty if none.
        """
        from engine.azure_constants import XP_THRESHOLDS
        char = state.characters.get(character_id)
        if char is None or not char.jobs:
            return []
        job_id  = next(iter(char.jobs))
        job_exp = char.jobs[job_id]
        job_def = CLASS_DEFINITIONS.get(job_id.upper())
        if job_def is None:
            return []
        results: list[LevelUpResult] = []
        while True:
            next_level = job_exp.level + 1
            if next_level > job_def.max_level:
                break
            if next_level - 1 >= len(XP_THRESHOLDS):
                break
            if char.experience < XP_THRESHOLDS[next_level - 1]:
                break
            results.append(self._do_level_up(char, job_exp, job_def))
        return results

    def _do_level_up(self, char, job_exp: JobExperience, job_def) -> LevelUpResult:
        """Apply one level-up to char. Mutates char and job_exp in place."""
        import random

        from engine.azure_constants import POWER_LEVEL, StatPriority

        _STAT_MAP = {"PHY": "physique", "FNS": "finesse", "RSN": "reason", "SVY": "savvy"}

        # HP gain: roll d(hit_die * POWER_LEVEL), matching hero.py formula
        hp_gain = random.randint(1, job_def.hit_die * POWER_LEVEL)
        job_exp.hp_bonus += hp_gain
        char.hp_max += hp_gain

        # Stat gain: primary stat only, up to StatPriority.GREATEST POWER_LEVEL units
        primary_attr = _STAT_MAP.get(job_def.primary_stat, "physique")
        stat_gain = random.randint(1, StatPriority.GREATEST)
        job_exp.stat_bonuses[primary_attr] += stat_gain
        setattr(char.ability_scores, primary_attr,
                getattr(char.ability_scores, primary_attr) + stat_gain)

        # Increment level
        job_exp.level += 1
        char.level = job_exp.level

        # Heal to full (per hero.py refreshSheet(heal=True) behaviour)
        char.hp_current = char.hp_max

        return LevelUpResult(
            character_id=char.character_id,
            character_name=char.name,
            job_id=job_exp.job_id,
            new_level=job_exp.level,
            hp_gained=hp_gain,
            stat_changes={primary_attr: stat_gain},
        )
