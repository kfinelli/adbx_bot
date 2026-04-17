"""
Character management for the dungeon crawler engine.
"""

from engine.azure_constants import (
    ACCESSORY_SLOTS,
    DEFAULT_EQUIPPED_SLOTS,
    GEAR_SLOT_MAP,
    POWER_LEVEL,
    STARTING_GOLD,
    ItemSlot,
    SkillType,
    getLowerWeaponRanks,
)
from engine.data_loader import CLASS_DEFINITIONS, ITEM_REGISTRY, SkillDef
from engine.dice import max_dice_expr, roll_dice_expr
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


def _projected_slots_used_after_equip(
    char,
    equip_item_id: str,
    displace_item_ids: list,
) -> int:
    """
    Return the hypothetical ``slots_used`` after ``equip_item_id`` moves from
    unequipped→equipped and every ID in ``displace_item_ids`` moves from
    equipped→unequipped.

    Uses bundle-aware math so light-item stacks are accounted for correctly.
    """
    from math import ceil

    from engine.azure_constants import BUNDLE_SIZE

    # Current unequipped light total and derived non-light slots
    L = sum(
        i.quantity
        for i in char.inventory
        if not i.equipped
        and i.container_id is None
        and (d := ITEM_REGISTRY.get(i.item_id)) is not None
        and d.isLight
    )
    non_light = char.slots_used - (ceil(L / BUNDLE_SIZE) if L > 0 else 0)

    # Remove the newly-equipped item from the unequipped pool
    equip_defn = ITEM_REGISTRY.get(equip_item_id)
    equip_inv = next(
        (i for i in char.inventory if i.item_id == equip_item_id and not i.equipped), None
    )
    if equip_defn is not None and equip_defn.isLight:
        L -= equip_inv.quantity if equip_inv else 1
    else:
        non_light -= equip_defn.slot_cost if equip_defn else 1

    # Add each displaced item back to the unequipped pool
    for did in displace_item_ids:
        displace_defn = ITEM_REGISTRY.get(did)
        displace_inv = next((i for i in char.inventory if i.item_id == did), None)
        if displace_defn is not None and displace_defn.isLight:
            L += displace_inv.quantity if displace_inv else 1
        else:
            non_light += displace_defn.slot_cost if displace_defn else 1

    return non_light + (ceil(L / BUNDLE_SIZE) if L > 0 else 0)


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
          3. HP = max_dice_expr(hit_die) (full HP at level 1).
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
        job_def   = CLASS_DEFINITIONS[character_class.name]

        hp_max    = max(max_dice_expr(job_def.hit_die) + scores.physique, 100) # Minimum level 1 hit points
        base_save = job_def.base_save * POWER_LEVEL

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
            gold=STARTING_GOLD,
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

    @staticmethod
    def get_active_skills(char: Character) -> list[SkillDef]:
        """
        Return all skills unlocked by the character's current job levels.
        A skill is active when skill.level <= the character's level in that job.
        """
        active: list[SkillDef] = []
        for job_key, job_exp in char.jobs.items():
            job_def = CLASS_DEFINITIONS.get(job_key.upper())
            if job_def is None:
                continue
            for skill in job_def.skills.values():
                if skill.level <= job_exp.level:
                    active.append(skill)
        return active

    def _char_allowed_ranks(self, char: Character, rank_type: str) -> set:
        """
        Return the set of item ranks the character is allowed to equip for the
        given rank_type ("weapon", "armor", or "spell").

        Derived from WEAPON_RANK (type 6) skills active at the character's level.
        Physical rank skills (E–A) apply to both weapons and armor.
        Arcane rank skills (V–Z) apply to spells.
        """
        allowed: set[str] = set()
        for skill in self.get_active_skills(char):
            if skill.skill_type != SkillType.WEAPON_RANK.value or skill.rank is None:
                continue
            if (rank_type in ("weapon", "armor") and skill.rank in self._PHYSICAL_RANKS) or (
                rank_type == "spell" and skill.rank in self._ARCANE_RANKS
            ):
                allowed |= getLowerWeaponRanks(skill.rank)
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

        # --- Unwieldy check ---
        if "Unwieldy" in definition.getTags() and char.ability_scores.physique < 400:
            return None, (
                f"{char.name} cannot equip '{definition.name}' "
                f"(Unwieldy weapons require 400 Physique; "
                f"{char.name} has {char.ability_scores.physique})."
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

        definition = ITEM_REGISTRY.get(item_id)
        item_name = definition.name if definition else item_id
        new_tags = definition.getTags() if isinstance(definition, EquipItem) else []

        # --- Unified inventory pre-flight: all displaced items at once ---
        # Collects every item that will land back in inventory (the current occupant of
        # the target slot, plus the off-hand if a Two-Handed weapon auto-unequips it),
        # then uses bundle-aware math to verify there is room for all of them.
        displace_ids: list[str] = []
        existing_id_pre = char.equipped_slots.get(target_slot.value)
        if existing_id_pre and existing_id_pre != item_id:
            displace_ids.append(existing_id_pre)
        if "Two-Handed" in new_tags and target_slot == ItemSlot.MAIN_HAND:
            oh_id_pre = char.equipped_slots.get(ItemSlot.OFF_HAND.value)
            if oh_id_pre and oh_id_pre not in displace_ids:
                displace_ids.append(oh_id_pre)

        if displace_ids:
            projected = _projected_slots_used_after_equip(char, item_id, displace_ids)
            if projected > char.inventory_size:
                names = " and ".join(
                    (ITEM_REGISTRY.get(did).name if ITEM_REGISTRY.get(did) else did)
                    for did in displace_ids
                )
                return _err(
                    state,
                    f"Not enough inventory space to equip {item_name}: unequipping "
                    f"{names} would exceed your inventory limit "
                    f"({char.slots_used}/{char.inventory_size} slots used). Free a slot first.",
                )

        # --- Two-Handed: block off-hand equip if main_hand holds a two-handed weapon ---
        if target_slot == ItemSlot.OFF_HAND:
            mh_id = char.equipped_slots.get(ItemSlot.MAIN_HAND.value)
            if mh_id:
                mh_def = ITEM_REGISTRY.get(mh_id)
                mh_tags = mh_def.getTags() if isinstance(mh_def, EquipItem) else []
                if "Two-Handed" in mh_tags:
                    mh_name = mh_def.name if mh_def else mh_id
                    return _err(
                        state,
                        f"{char.name} cannot equip to the off-hand slot while wielding "
                        f"the two-handed weapon {mh_name}.",
                    )

        # Unequip whatever is currently in that slot
        existing_id = char.equipped_slots.get(target_slot.value)
        if existing_id is not None:
            existing = next((i for i in char.inventory if i.item_id == existing_id), None)
            if existing:
                existing.equipped = False
            char.equipped_slots[target_slot.value] = None

        # --- Two-Handed: auto-unequip off-hand when equipping a two-handed weapon ---
        extra_msg = ""
        if "Two-Handed" in new_tags and target_slot == ItemSlot.MAIN_HAND:
            oh_id = char.equipped_slots.get(ItemSlot.OFF_HAND.value)
            if oh_id:
                oh_item = next((i for i in char.inventory if i.item_id == oh_id), None)
                if oh_item:
                    oh_item.equipped = False
                oh_def = ITEM_REGISTRY.get(oh_id)
                oh_name = oh_def.name if oh_def else oh_id
                char.equipped_slots[ItemSlot.OFF_HAND.value] = None
                extra_msg = f" (Two-Handed: {oh_name} unequipped from off-hand.)"

        # Initialise light charges at equip time
        if getattr(definition, "max_light_turns", None) is not None:
            if definition.isLight and inv_item.quantity > 1:
                # Split one torch out of the bundle
                inv_item.quantity -= 1
                from models import InventoryItem as _InvItem
                split = _InvItem(item_id=item_id, quantity=1, charges=definition.max_light_turns)
                char.inventory.append(split)
                inv_item = split
            elif inv_item.charges is None or inv_item.charges == 0:
                fuel_id = getattr(definition, "fuel_item_id", None)
                if fuel_id:
                    fuel = next(
                        (i for i in char.inventory if i.item_id == fuel_id and not i.equipped),
                        None,
                    )
                    if fuel:
                        fuel.quantity -= 1
                        if fuel.quantity <= 0:
                            char.inventory.remove(fuel)
                        inv_item.charges = definition.max_light_turns
                    else:
                        inv_item.charges = 0  # equip dark lantern (no oil available)
                else:
                    inv_item.charges = definition.max_light_turns  # torch: set full charges
            # else charges > 0: mid-burn item re-equipped → keep existing charges

        # Equip the new item
        inv_item.equipped = True
        char.equipped_slots[target_slot.value] = item_id

        state.updated_at = _now()
        return _ok(state, f"{char.name} equipped {item_name} in the {target_slot.value} slot.{extra_msg}")

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
                # Add each contained spell as its own InventoryItem with per-character charges.
                for spell_id in defn.contained_item_ids:
                    spell_def = ITEM_REGISTRY.get(spell_id)
                    spell_charges = spell_def.maxCharges if hasattr(spell_def, "maxCharges") else None
                    char.inventory.append(InventoryItem(
                        item_id=spell_id,
                        quantity=1,
                        container_id=item_id,
                        charges=spell_charges,
                    ))

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

    def adjust_spell_charges(
        self,
        state: GameState,
        character_id,
        item_id: str,
        delta: int,
    ):
        """
        Adjust a spell's current charges by delta (positive or negative).

        Works for ChargeWeapon and UtilitySpell items with finite maxCharges.
        Result is clamped to [0, maxCharges]. Infinite spells (maxCharges < 0)
        are rejected.
        """
        from engine import _now

        char = state.characters.get(character_id)
        if char is None:
            return _err(state, f"Character {character_id} not found.")

        from engine.item import UtilitySpell
        inv_item = next(
            (i for i in char.inventory if i.item_id == item_id and i.charges is not None),
            None,
        )
        if inv_item is None:
            return _err(state, f"No chargeable spell '{item_id}' found on {char.name}.")

        defn = ITEM_REGISTRY.get(item_id)
        if defn is None or not isinstance(defn, (ChargeWeapon, UtilitySpell)):
            return _err(state, f"'{item_id}' is not a spell.")
        if defn.maxCharges < 0:
            return _err(state, f"'{defn.name}' has infinite charges and cannot be adjusted.")

        inv_item.charges = max(0, min(defn.maxCharges, inv_item.charges + delta))
        state.updated_at = _now()
        return _ok(state, f"{defn.name} charges: {inv_item.charges}/{defn.maxCharges}.")

    def adjust_light_charges(
        self,
        state: GameState,
        character_id,
        item_id: str,
        delta: int,
        equipped: bool,
    ):
        """
        Adjust a light-emitting item's current charges by delta (positive or negative).

        ``equipped`` disambiguates between the burning (equipped) item and any
        unequipped partially-used items with the same item_id.
        Result is clamped to [0, max_light_turns].
        """
        from engine import _now

        char = state.characters.get(character_id)
        if char is None:
            return _err(state, f"Character {character_id} not found.")

        inv_item = next(
            (
                i for i in char.inventory
                if i.item_id == item_id
                and i.charges is not None
                and i.equipped == equipped
            ),
            None,
        )
        if inv_item is None:
            return _err(state, f"No light item '{item_id}' with charges found on {char.name}.")

        defn = ITEM_REGISTRY.get(item_id)
        if defn is None or getattr(defn, "max_light_turns", None) is None:
            return _err(state, f"'{item_id}' is not a light-emitting item.")

        inv_item.charges = max(0, min(defn.max_light_turns, inv_item.charges + delta))
        state.updated_at = _now()
        return _ok(state, f"{defn.name} light charges: {inv_item.charges}/{defn.max_light_turns}.")

    def recharge_day_spells(
        self,
        state: GameState,
        character_id,
    ):
        """
        Restore all DAY-period spells to full charges for the given character.

        Returns an EngineResult with the count of spells recharged.
        """
        from engine import _now
        from engine.azure_constants import RechargePeriod
        from engine.item import UtilitySpell

        char = state.characters.get(character_id)
        if char is None:
            return _err(state, f"Character {character_id} not found.")

        count = 0
        for inv_item in char.inventory:
            if inv_item.charges is None:
                continue
            defn = ITEM_REGISTRY.get(inv_item.item_id)
            if defn is None:
                continue
            if not isinstance(defn, (ChargeWeapon, UtilitySpell)):
                continue
            if getattr(defn, "rechargePeriod", None) != RechargePeriod.DAY:
                continue
            if defn.maxCharges < 0:
                continue
            inv_item.charges = defn.maxCharges
            count += 1

        state.updated_at = _now()
        if count == 0:
            return _ok(state, f"{char.name} has no daily spells to recharge.")
        return _ok(state, f"Recharged {count} daily spell(s) for {char.name}.")

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
            # Purge any items that were contained inside this container.
            if isinstance(defn, ContainerItem):
                char.inventory = [i for i in char.inventory if i.container_id != item_id]

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
        definition = ITEM_REGISTRY.get(item_id)
        item_name = definition.name if definition else item_id

        # Bundle-aware pre-flight: unequipping a light item may push the light pool
        # across a BUNDLE_SIZE boundary, costing an extra inventory slot.
        if inv_item and definition is not None and definition.isLight:
            from math import ceil

            from engine.azure_constants import BUNDLE_SIZE
            L_current = sum(
                i.quantity for i in char.inventory
                if not i.equipped
                and i.container_id is None
                and (d := ITEM_REGISTRY.get(i.item_id)) is not None
                and d.isLight
            )
            L_after = L_current + inv_item.quantity
            extra_slots = ceil(L_after / BUNDLE_SIZE) - (ceil(L_current / BUNDLE_SIZE) if L_current > 0 else 0)
            if char.slots_used + extra_slots > char.inventory_size:
                return _err(
                    state,
                    f"Not enough inventory space to unequip {item_name}: returning it to "
                    f"inventory would exceed your inventory limit "
                    f"({char.slots_used}/{char.inventory_size} slots used). Free a slot first.",
                )

        if inv_item:
            inv_item.equipped = False

        char.equipped_slots[slot.value] = None

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
            state.pending_level_ups.extend(level_ups)
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

        _STAT_MAP = {"PHY": "physique", "FNS": "finesse", "RSN": "reason", "SVY": "savvy"}
        _VALID_STATS = {"PHY", "FNS", "RSN", "SVY"}

        # HP gain: roll the hit_die dice expression directly
        hp_gain = roll_dice_expr(job_def.hit_die)["total"]
        job_exp.hp_bonus += hp_gain
        char.hp_max += hp_gain

        # Stat gains: roll every stat's dice expression from stat_rolls
        stat_gains: dict[str, int] = {}
        for stat_key, attr in _STAT_MAP.items():
            dice_expr = job_def.stat_rolls.get(stat_key)
            if not dice_expr:
                continue
            gain = roll_dice_expr(dice_expr)["total"]
            stat_gains[attr] = gain
            job_exp.stat_bonuses[attr] += gain
            setattr(char.ability_scores, attr, getattr(char.ability_scores, attr) + gain)

        # Increment level
        job_exp.level += 1
        char.level = job_exp.level

        # Heal to full (per hero.py refreshSheet(heal=True) behaviour)
        char.hp_current = char.hp_max

        # Apply PASSIVE_BONUS skills unlocked at the new level
        skill_stat_changes: dict[str, int] = {}
        skills_granted: list[SkillDef] = []
        for skill in job_def.skills.values():
            if skill.level != job_exp.level:
                continue
            skills_granted.append(skill)
            if skill.skill_type == SkillType.PASSIVE_BONUS.value and skill.bonus:
                stat_key = skill.stat
                if stat_key in _VALID_STATS:
                    attr = _STAT_MAP[stat_key]
                elif stat_key == "ANY":
                    attr = random.choice(list(_STAT_MAP.values()))
                else:
                    continue
                bonus = skill.bonus
                job_exp.stat_bonuses[attr] += bonus
                setattr(char.ability_scores, attr,
                        getattr(char.ability_scores, attr) + bonus)
                skill_stat_changes[attr] = skill_stat_changes.get(attr, 0) + bonus

        combined_stat_changes = dict(stat_gains)
        for attr, bonus in skill_stat_changes.items():
            combined_stat_changes[attr] = combined_stat_changes.get(attr, 0) + bonus

        return LevelUpResult(
            character_id=char.character_id,
            character_name=char.name,
            job_id=job_exp.job_id,
            new_level=job_exp.level,
            hp_gained=hp_gain,
            stat_changes=combined_stat_changes,
            skills_granted=skills_granted,
        )
