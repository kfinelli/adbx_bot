"""
engine/combat_hooks.py — Combat hook system for ROUNDS mode.

This module is the extension point for combat effects.  Every active effect in
the game — damage, conditions, movement, gear — is implemented as a hook here.

Hook system overview
--------------------
Actions (data/actions/*.json) and conditions (data/conditions/*.json) declare
``effect_tags`` / ``hooks`` entries.  Each entry is resolved at runtime via
``_dispatch_hook``, which looks up the tag in ``_HOOK_DISPATCH`` and calls the
registered handler.

A hook entry is either:

  • A plain string tag (no parameters):
        "check_death"
        "skip_action"

  • A tag object with extra parameters:
        {"tag": "deal_damage", "dice": "1d4", "type": "poison"}
        {"tag": "apply_condition", "condition": "stunned", "duration": 2}

Every handler shares the same signature::

    def _hook_<name>(
        state:    GameState,
        actor_id: UUID,
        action:   CombatAction | None,
        log:      list[str],
        params:   dict,
    ) -> None:

``actor_id`` is the combatant whose action (or condition) triggered the hook.
``action`` is the CombatAction being resolved, or None for condition lifecycle
hooks (on_turn_start, on_turn_end, on_move).
``params`` is always a dict — empty ``{}`` for plain-string tags.

Condition lifecycle points
--------------------------
``on_turn_start``  — fired before the action loop (sets skip_action / movement_blocked)
``on_turn_end``    — fired inside ``_tick_conditions`` after all actions resolve
``on_move``        — fired inside ``_hook_move_to_band`` before the move executes

Adding a new hook
-----------------
1. Write a ``_hook_<name>`` function in the "Hook handlers" section below.
   Copy any existing handler as a template.  Document the ``params`` keys.
2. Add one entry to ``_HOOK_DISPATCH`` at the bottom of this file:
       "tag_name": _hook_<name>
3. Reference the tag in ``data/conditions/<id>.json`` or
   ``data/actions/<id>.json``.
4. Add a test in ``tests/test_combat_engine.py``.
See ``CONTRIBUTING.md`` for the full step-by-step walkthrough.
"""

from __future__ import annotations

import random
from uuid import UUID

from engine.azure_constants import POWER_LEVEL
from engine.item import ChargeWeapon
from models import (
    NPC,
    ActiveCondition,
    GameState,
    RangeBand,
)

from .data_loader import CONDITION_REGISTRY
from .dice import roll_dice_expr

# ---------------------------------------------------------------------------
# State lookup utilities
# ---------------------------------------------------------------------------
# These are used throughout the hooks and are also imported by combat.py for
# use in the round-resolution pipeline and public API.

def _combatant_name(state: GameState, cid: UUID) -> str:
    char = state.characters.get(cid)
    if char:
        return char.name
    npc = _find_npc(state, cid)
    return npc.name if npc else str(cid)


def _find_npc(state: GameState, npc_id: UUID) -> NPC | None:
    for group in state.npc_roster.groups.values():
        for npc in group.npcs:
            if npc.npc_id == npc_id:
                return npc
    return None


# ---------------------------------------------------------------------------
# Condition stat modifier helpers
# ---------------------------------------------------------------------------
# These compute effective combat stats for a combatant, folding in any
# stat_modifiers granted or penalised by active conditions.

def _effective_stat_mod(state: GameState, actor_id: UUID, stat: str) -> int:
    """
    Return the effective value for ``stat`` including active condition bonuses.
    ``stat`` is an AzureStats field name: "physique", "finesse", "reason", or "savvy".
    Returns 0 for NPCs (no ability scores).
    """
    actor_char = state.characters.get(actor_id)
    if actor_char is None:
        return 0

    base_mod = getattr(actor_char.ability_scores, stat, 0)
    bonus = sum(
        CONDITION_REGISTRY[c.condition_id].stat_modifiers.get(stat, 0) * c.stacks
        for c in actor_char.active_conditions
        if c.condition_id in CONDITION_REGISTRY
    )
    return base_mod + bonus


def _effective_defense(state: GameState, combatant_id: UUID) -> int:
    """
    Defense floored at 0. For Characters, .defense already includes condition
    modifiers. For NPCs (plain int field), condition modifiers are applied here.
    """
    char = state.characters.get(combatant_id)
    if char:
        return char.defense  # already includes conditions + floor
    npc = _find_npc(state, combatant_id)
    if npc is None:
        return 0
    base = npc.defense + sum(
        CONDITION_REGISTRY[c.condition_id].stat_modifiers.get("defense", 0) * c.stacks
        for c in npc.active_conditions
        if c.condition_id in CONDITION_REGISTRY
    )
    return max(0, base)


def _effective_resistance(state: GameState, combatant_id: UUID) -> int:
    """
    Resistance floored at 0. For Characters, .resistance already includes
    condition modifiers. For NPCs, condition modifiers are applied here.
    """
    char = state.characters.get(combatant_id)
    if char:
        return char.resistance  # already includes conditions + floor
    npc = _find_npc(state, combatant_id)
    if npc is None:
        return 0
    base = npc.resistance + sum(
        CONDITION_REGISTRY[c.condition_id].stat_modifiers.get("resistance", 0) * c.stacks
        for c in npc.active_conditions
        if c.condition_id in CONDITION_REGISTRY
    )
    return max(0, base)


def _effective_finesse(state: GameState, combatant_id: UUID) -> int:
    """
    Dodge (finesse) after condition stat_modifiers {"finesse": N}, floored at 0.
    Base is char.dodge / npc.dodge, which already applies the Heavy tag cap.
    "abjuring" uses +200 so attacks need a much higher roll to hit.
    """
    char = state.characters.get(combatant_id)
    npc  = _find_npc(state, combatant_id)
    base = char.dodge if char else (npc.dodge if npc else 0)
    combatant = char if char else npc
    if combatant:
        base += sum(
            CONDITION_REGISTRY[c.condition_id].stat_modifiers.get("finesse", 0) * c.stacks
            for c in combatant.active_conditions
            if c.condition_id in CONDITION_REGISTRY
        )
    return max(0, base)


# ---------------------------------------------------------------------------
# _dispatch_hook
# ---------------------------------------------------------------------------

def _dispatch_hook(
    hook_entry,          # str | dict — plain tag or {"tag": ..., ...params}
    state:    GameState,
    actor_id: UUID,
    action,              # CombatAction | None
    log:      list[str],
) -> None:
    """
    Unwrap a hook entry and call the registered handler.

    Accepts both forms:
      "skip_action"                              → params = {}
      {"tag": "deal_damage", "dice": "1d4", ...} → params = {"dice": "1d4", ...}

    Unknown tags are logged as warnings and skipped (never raise), so a
    new tag in a data file doesn't crash a live session.
    """
    if isinstance(hook_entry, dict):
        tag    = hook_entry.get("tag", "")
        params = {k: v for k, v in hook_entry.items() if k != "tag"}
    else:
        tag    = hook_entry or ""
        params = {}

    if not tag:
        log.append("[Warning: hook entry has empty tag — skipped]")
        return

    handler = _HOOK_DISPATCH.get(tag)
    if handler is None:
        log.append(f"[Warning: unknown hook tag '{tag}' — skipped]")
        return

    handler(state, actor_id, action, log, params)


# ---------------------------------------------------------------------------
# Hook handlers
# ---------------------------------------------------------------------------
#
# Every handler has the same signature:
#
#   def _hook_<name>(
#       state:    GameState,
#       actor_id: UUID,
#       action:   CombatAction | None,
#       log:      list[str],
#       params:   dict,
#   ) -> None:
#
# actor_id  — the combatant whose action/condition triggered the hook
# action    — the CombatAction being resolved, or None for lifecycle hooks
# params    — always a dict; empty {} when the hook was a plain string tag
#
# See the module docstring and CONTRIBUTING.md for a full walkthrough.
# ---------------------------------------------------------------------------

def _hook_weapon_attack(
    state:    GameState,
    actor_id: UUID,
    action,              # CombatAction | None
    log:      list[str],
    params:   dict,
) -> None:
    """
    Roll a weapon attack against the target's FNS, on a hit deal damage.
    Mitigation is routed via the weapon's targets_stat field ("defense" or "resistance").

    params:
      dice  (str, default "1d6") — damage dice expression, e.g. "1d8", "2d6"

    Attack roll: 1d(10 x POWER_LEVEL) vs target FNS (roll >= FNS to hit)
    Damage:      roll `dice` + weapon stat, subtract target DEF or RST
    """
    if action is None or action.target_id is None:
        log.append("[melee_attack: no target specified]")
        return

    dice        = params.get("dice", "1d6")
    target_id   = action.target_id
    actor_name  = _combatant_name(state, actor_id)
    target_name = _combatant_name(state, target_id)

    # Range check: determine weapon range then compare to band distance
    actor_cs  = state.battlefield.combatants.get(actor_id)
    target_cs = state.battlefield.combatants.get(target_id)
    if actor_cs and target_cs:
        weapon_range = 0  # default: melee (also used for NPCs without equipped weapons)
        maybe_char = state.characters.get(actor_id)
        if maybe_char:
            weapons = maybe_char.equipped_weapons()
            if weapons:
                _, w_def = weapons[0]
                if action.weapon_id:
                    w_pair = next(
                        ((i, d) for i, d in weapons if i.item_id == action.weapon_id),
                        None,
                    )
                    if w_pair:
                        _, w_def = w_pair
                weapon_range = getattr(w_def, "range", 0)
        from engine.combat import _band_distance
        dist = _band_distance(actor_cs.range_band, target_cs.range_band)
        if dist > weapon_range:
            log.append(
                f"{actor_name} cannot reach {target_name} "
                f"— {dist} band(s) away, weapon range is {weapon_range}."
            )
            return

    actor_char = state.characters.get(actor_id)
    actor_npc  = _find_npc(state, actor_id)
    targets_stat = "defense"
    if actor_char:
        # Use the equipped weapon matching weapon_id, or fall back to the first.
        weapons = actor_char.equipped_weapons()
        stat_name = "physique"
        if weapons:
            if action and action.weapon_id:
                weapon_inv, weapon_def = next(
                    ((inv, defn) for inv, defn in weapons if inv.item_id == action.weapon_id),
                    weapons[0],
                )
            else:
                weapon_inv, weapon_def = weapons[0]
            # Override dice with weapon's damage expression if it's set.
            weapon_damage = getattr(weapon_def, "damage", None)
            if weapon_damage and weapon_damage != "0":
                dice = weapon_damage
            weapon_stat = getattr(weapon_def, "stat", None)
            if weapon_stat:
                stat_name = weapon_stat
            targets_stat = getattr(weapon_def, "targets_stat", "defense")
            # Consume a charge for ChargeWeapons (standalone or contained in a spellbook).
            if isinstance(weapon_def, ChargeWeapon):
                current = weapon_inv.charges if weapon_inv.charges is not None else -1
                if current == 0:
                    log.append(
                        f"{actor_name} tries to use {weapon_def.name} "
                        f"but it has no charges left!"
                    )
                    return
                if current > 0:
                    weapon_inv.charges = current - 1
            # Consume the weapon on a throwable attack (thrown regardless of hit/miss).
            if action and action.weapon_id and action.weapon_id.endswith("__throwable"):
                real_id = action.weapon_id.removesuffix("__throwable")
                actor_char.inventory = [i for i in actor_char.inventory if i.item_id != real_id]
                if actor_char.equipped_slots.get("main_hand") == real_id:
                    actor_char.equipped_slots["main_hand"] = None
        str_mod      = _effective_stat_mod(state, actor_id, stat_name)
        attack_bonus = 0
    elif actor_npc:
        str_mod      = 0
        attack_bonus = actor_npc.ability_scores.physique
    else:
        return

    target_char = state.characters.get(target_id)
    target_npc  = _find_npc(state, target_id)
    if target_char is None and target_npc is None:
        log.append(f"{actor_name} swings at nothing.")
        return

    target_ac = _effective_finesse(state, target_id)

    roll = random.randint(1, 10*POWER_LEVEL) + attack_bonus
    if roll < target_ac:
        log.append(f"{actor_name} attacks {target_name} — misses! (rolled {roll} vs AC {target_ac})")
        return

    base_roll = roll_dice_expr(dice)["total"]
    is_crit = random.randint(1, 10) == 1
    crit_bonus = roll_dice_expr(dice)["total"] if is_crit else 0
    stat_bonus = str_mod if params.get("add_stat_bonus") and actor_char else 0
    min_damage = 1
    damage = max(min_damage, base_roll + crit_bonus + stat_bonus)
    mitigation = (
        _effective_resistance(state, target_id)
        if targets_stat == "resistance"
        else _effective_defense(state, target_id)
    )
    damage = max(damage - mitigation, 0)
    if target_char:
        target_char.hp_current = max(0, target_char.hp_current - damage)
        hp_str = f"{target_char.hp_current}/{target_char.hp_max}"
    else:
        target_npc.hp_current = max(0, target_npc.hp_current - damage)
        hp_str = f"{target_npc.hp_current}/{target_npc.hp_max}"

    crit_tag = " [CRIT!]" if is_crit else ""
    magic_tag = " [magical]" if targets_stat == "resistance" else ""
    log.append(
        f"{actor_name} attacks {target_name} — hits!{crit_tag}{magic_tag} (rolled {roll} vs Dodge {target_ac}) "
        f"Deals {damage} damage. [{target_name}: {hp_str}]"
    )


def _hook_check_death(
    state:    GameState,
    actor_id: UUID,
    action,              # CombatAction | None
    log:      list[str],
    params:   dict,
) -> None:
    """
    After an attack, mark the target dead if they have reached 0 HP and
    remove them from the battlefield.

    params: (none)
    """
    if action is None or action.target_id is None:
        return

    target_id   = action.target_id
    target_name = _combatant_name(state, target_id)

    target_char = state.characters.get(target_id)
    if target_char and target_char.hp_current <= 0:
        from models import CharacterStatus
        target_char.status = CharacterStatus.DEAD
        log.append(f"{target_name} has fallen!")
        state.battlefield.combatants.pop(target_id, None)
        return

    target_npc = _find_npc(state, target_id)
    if target_npc and target_npc.hp_current <= 0:
        target_npc.status = "dead"
        log.append(f"{target_name} has been slain!")
        state.battlefield.combatants.pop(target_id, None)
        xp_total = target_npc.hit_dice * 100
        if xp_total > 0:
            from engine.character import CharacterManager
            from models import CharacterStatus
            active = [c for c in state.characters.values()
                      if c.status == CharacterStatus.ACTIVE]
            if active:
                cm = CharacterManager()
                cm.distribute_xp(state, xp_total)
                log.append(
                    f"The party gains {xp_total} XP "
                    f"({xp_total // len(active)} each)."
                )


def _opportunity_attacks(
    state:    GameState,
    actor_id: UUID,
    old_band: RangeBand,
    log:      list[str],
) -> None:
    """
    Fire a free weapon attack from every enemy sharing old_band with actor_id.

    Skipped entirely if the moving actor has any condition tagged
    "opportunity-attack-immune" (e.g. abdication-immunity).
    """
    actor_char = state.characters.get(actor_id)
    actor_npc  = _find_npc(state, actor_id)
    actor = actor_char or actor_npc
    if actor and any(
        (cond_def := CONDITION_REGISTRY.get(c.condition_id)) is not None
        and "opportunity-attack-immune" in cond_def.tags
        for c in actor.active_conditions
    ):
        return

    actor_is_player = actor_char is not None

    for enemy_id, cs in list(state.battlefield.combatants.items()):
        if enemy_id == actor_id:
            continue
        if cs.range_band != old_band:
            continue
        enemy_char = state.characters.get(enemy_id)
        enemy_npc  = _find_npc(state, enemy_id)
        if actor_is_player and enemy_npc is None:
            continue
        if not actor_is_player and enemy_char is None:
            continue
        if enemy_npc and (enemy_npc.hp_current <= 0 or enemy_npc.status != "active"):
            continue
        if enemy_char and enemy_char.hp_current <= 0:
            continue

        enemy_name = _combatant_name(state, enemy_id)
        log.append(f"{enemy_name} gets an opportunity attack!")
        from engine.combat import CombatAction
        free_action = CombatAction(action_id="attack", target_id=actor_id)
        _hook_weapon_attack(state, enemy_id, free_action, log, {})
        _hook_check_death(state, enemy_id, free_action, log, {})


def _hook_move_to_band(
    state:    GameState,
    actor_id: UUID,
    action,              # CombatAction | None
    log:      list[str],
    params:   dict,
) -> None:
    """
    Move the actor toward action.destination (one step at a time).
    Fires on_move condition hooks before executing; if any set
    cs.movement_blocked the move is cancelled.

    params: (none)
    """
    cs = state.battlefield.combatants.get(actor_id)
    if cs is None:
        return

    actor_char = state.characters.get(actor_id)
    actor_npc  = _find_npc(state, actor_id)
    combatant  = actor_char if actor_char else actor_npc
    for active_cond in (combatant.active_conditions if combatant else []):
        cond_def = CONDITION_REGISTRY.get(active_cond.condition_id)
        if cond_def:
            move_entry = cond_def.hooks.get("on_move")
            if move_entry:
                _dispatch_hook(move_entry, state, actor_id, action, log)

    if cs.movement_blocked:
        return

    if action is None or action.destination is None:
        log.append("[move_to_band: no destination specified]")
        return

    from engine.combat import _adjacent_bands, _step_toward
    actor_name = _combatant_name(state, actor_id)
    old_band   = cs.range_band
    adjacent   = _adjacent_bands(old_band)

    if action.destination == old_band:
        log.append(f"{actor_name} holds position at {old_band.value}.")
        return

    if action.destination in adjacent:
        new_band = action.destination
    else:
        new_band = _step_toward(old_band, action.destination)

    _opportunity_attacks(state, actor_id, old_band, log)

    cs.range_band = new_band
    log.append(f"{actor_name} moves from {old_band.value} to {new_band.value}.")


def _hook_deal_damage(
    state:    GameState,
    actor_id: UUID,
    action,              # CombatAction | None
    log:      list[str],
    params:   dict,
) -> None:
    """
    Deal damage to actor_id (the combatant carrying this condition).
    Used for periodic damage effects such as poison, burning, bleeding.

    params:
      dice  (str, default "1d6") — damage dice expression
      type  (str, default "physical") — damage type label shown in the log
    """
    dice         = params.get("dice", "1d6")
    damage_type  = params.get("type", "physical")
    damage       = roll_dice_expr(dice)["total"]
    actor_name   = _combatant_name(state, actor_id)

    actor_char = state.characters.get(actor_id)
    actor_npc  = _find_npc(state, actor_id)

    if actor_char:
        mitigation = (
            _effective_resistance(state, actor_id)
            if damage_type != "physical"
            else _effective_defense(state, actor_id)
        )
        damage = max(damage - mitigation, 0)
        actor_char.hp_current = max(0, actor_char.hp_current - damage)
        hp_str = f"{actor_char.hp_current}/{actor_char.hp_max}"
        if actor_char.hp_current <= 0:
            from models import CharacterStatus
            actor_char.status = CharacterStatus.DEAD
            state.battlefield.combatants.pop(actor_id, None)
            log.append(f"{actor_name} takes {damage} {damage_type} damage and falls!")
            return
    elif actor_npc:
        mitigation = (
            _effective_resistance(state, actor_id)
            if damage_type != "physical"
            else _effective_defense(state, actor_id)
        )
        damage = max(damage - mitigation, 0)
        actor_npc.hp_current = max(0, actor_npc.hp_current - damage)
        hp_str = f"{actor_npc.hp_current}/{actor_npc.hp_max}"
        if actor_npc.hp_current <= 0:
            actor_npc.status = "dead"
            state.battlefield.combatants.pop(actor_id, None)
            log.append(f"{actor_name} takes {damage} {damage_type} damage and is slain!")
            xp_total = actor_npc.hit_dice * 100
            if xp_total > 0:
                from engine.character import CharacterManager
                from models import CharacterStatus
                active = [c for c in state.characters.values()
                          if c.status == CharacterStatus.ACTIVE]
                if active:
                    cm = CharacterManager()
                    cm.distribute_xp(state, xp_total)
                    log.append(
                        f"The party gains {xp_total} XP "
                        f"({xp_total // len(active)} each)."
                    )
            return
    else:
        return

    log.append(f"{actor_name} takes {damage} {damage_type} damage. [{actor_name}: {hp_str}]")


def _hook_apply_condition(
    state:    GameState,
    actor_id: UUID,
    action,              # CombatAction | None
    log:      list[str],
    params:   dict,
) -> None:
    """
    Apply a condition to action.target_id, or to actor_id if params["target"] == "self".

    params:
      condition  (str, required) — condition_id to apply
      duration   (int, default 3) — duration in rounds; omit for permanent
      target     (str, optional) — "self" to apply to the actor instead of action.target_id
    """
    condition_id = params.get("condition")
    if not condition_id:
        log.append("[apply_condition: 'condition' param is required]")
        return

    if params.get("target") == "self":
        target_id = actor_id
    else:
        if action is None or action.target_id is None:
            log.append(f"[apply_condition({condition_id}): no target specified]")
            return
        target_id = action.target_id

    actor_name  = _combatant_name(state, actor_id)
    target_name = _combatant_name(state, target_id)

    cs = state.battlefield.combatants.get(target_id) if state.battlefield else None
    if cs is None:
        log.append(f"[apply_condition({condition_id}): target not on battlefield]")
        return

    duration = params.get("duration", 3)
    from engine.combat import apply_condition
    apply_condition(state, target_id, condition_id, duration=duration, source_id=actor_id)

    cond_def = CONDITION_REGISTRY.get(condition_id)
    label    = cond_def.label if cond_def else condition_id
    if target_id == actor_id:
        log.append(f"{actor_name} is now {label}! ({duration} rounds)")
    else:
        log.append(f"{actor_name} applies {label} to {target_name}! ({duration} rounds)")


def _hook_skip_action(
    state:    GameState,
    actor_id: UUID,
    action,              # CombatAction | None
    log:      list[str],
    params:   dict,
) -> None:
    """
    Set skip_action on the combatant so the action loop bypasses them.
    The narrative is emitted by the action loop when it sees the flag.

    params: (none)
    """
    cs = state.battlefield.combatants.get(actor_id)
    if cs is not None:
        cs.skip_action = True


def _hook_block_movement(
    state:    GameState,
    actor_id: UUID,
    action,              # CombatAction | None
    log:      list[str],
    params:   dict,
) -> None:
    """
    Set movement_blocked so _hook_move_to_band cancels the move.

    params: (none)
    """
    cs = state.battlefield.combatants.get(actor_id)
    if cs is not None:
        cs.movement_blocked = True
        log.append(f"{_combatant_name(state, actor_id)} is entangled and cannot move!")


def _hook_abscond_roll(
    state:    GameState,
    actor_id: UUID,
    action,              # CombatAction | None
    log:      list[str],
    params:   dict,
) -> None:
    """
    Flee attempt: 1d1000 + Finesse vs 500 + highest enemy Finesse.
    Difficulty reduced by the actor's accumulated 'abscond_bonus' condition modifier.
    Enemies at ENGAGE range block escape entirely.
    Applies the stacking 'absconding' condition to all player combatants on every
    attempt (win or lose), so each subsequent try gets easier.
    Sets bf.abscond_succeeded = True on success; auto_resolve_round calls exit_rounds.

    params: (none)
    """
    bf = state.battlefield
    if bf is None:
        return

    actor_name = _combatant_name(state, actor_id)
    actor_cs   = bf.combatants.get(actor_id)

    # Positional block: enemy blocks only if they're between the actor and the
    # exit at FAR_MINUS — i.e., their band index is <= the actor's band index.
    # An enemy "ahead" of the actor (higher band index, deeper into FAR_PLUS)
    # has already been passed and cannot block.
    from engine.combat import _BAND_INDEX
    for cid, cs in bf.combatants.items():
        if cs.is_player:
            continue
        if actor_cs and _BAND_INDEX[cs.range_band] <= _BAND_INDEX[actor_cs.range_band]:
            npc = _find_npc(state, cid)
            blocker = npc.name if npc else "An enemy"
            log.append(f"{actor_name} tries to flee but {blocker} is blocking the way!")
            return

    # Roll
    roll    = random.randint(1, 1000)
    finesse = _effective_finesse(state, actor_id)
    total   = roll + finesse

    # Threshold: 500 + highest enemy Finesse − accumulated abscond_bonus
    enemy_finesse = max(
        (npc.ability_scores.finesse
         for cid2, cs2 in bf.combatants.items()
         if not cs2.is_player
         for npc in [_find_npc(state, cid2)]
         if npc is not None),
        default=0,
    )
    actor_char = state.characters.get(actor_id)
    bonus = 0
    if actor_char:
        bonus = sum(
            CONDITION_REGISTRY[c.condition_id].stat_modifiers.get("abscond_bonus", 0) * c.stacks
            for c in actor_char.active_conditions
            if c.condition_id in CONDITION_REGISTRY
        )
    threshold = max(0, 500 + enemy_finesse - bonus)

    # Apply stacking absconding condition to all allies (before logging outcome)
    from engine.combat import apply_condition
    for cid3, cs3 in bf.combatants.items():
        if cs3.is_player:
            apply_condition(state, cid3, "absconding", duration=999)

    # Outcome
    if total >= threshold:
        log.append(
            f"{actor_name} rolls {roll} + {finesse} = **{total}** vs {threshold} — "
            f"the party flees!"
        )
        bf.abscond_succeeded = True
    else:
        log.append(
            f"{actor_name} rolls {roll} + {finesse} = {total} vs {threshold} — "
            f"failed to abscond."
        )


def _hook_resolve_equip(
    state:    GameState,
    actor_id: UUID,
    action,              # CombatAction | None
    log:      list[str],
    params:   dict,
) -> None:
    """
    Equip an item during round resolution.
    action.weapon_id = item_id to equip
    action.free_text = ItemSlot.value string for target slot, or "" for auto-detect

    params: (none)
    """
    from engine.azure_constants import ItemSlot
    from engine.character import CharacterManager

    if action is None or not action.weapon_id:
        log.append(f"[equip_item: no item_id for {_combatant_name(state, actor_id)}]")
        return
    slot = None
    if action.free_text:
        try:
            slot = ItemSlot(action.free_text)
        except ValueError:
            log.append(f"[equip_item: unknown slot '{action.free_text}' — using auto]")
    result = CharacterManager().equip_item(state, actor_id, action.weapon_id, slot=slot)
    log.append(result.message if result.ok else f"[equip failed: {result.error}]")


def _hook_resolve_unequip(
    state:    GameState,
    actor_id: UUID,
    action,              # CombatAction | None
    log:      list[str],
    params:   dict,
) -> None:
    """
    Unequip an item during round resolution.
    action.free_text = ItemSlot.value string (e.g. "main_hand")

    params: (none)
    """
    from engine.azure_constants import ItemSlot
    from engine.character import CharacterManager

    if action is None or not action.free_text:
        log.append(f"[unequip_item: no slot for {_combatant_name(state, actor_id)}]")
        return
    try:
        slot = ItemSlot(action.free_text)
    except ValueError:
        log.append(f"[unequip_item: unknown slot '{action.free_text}']")
        return
    result = CharacterManager().unequip_item(state, actor_id, slot)
    log.append(result.message if result.ok else f"[unequip failed: {result.error}]")


# ---------------------------------------------------------------------------
# Hook registry
# ---------------------------------------------------------------------------
#
# To add a new hook:
#   1. Write a _hook_<name> function above (copy any existing hook as a template).
#   2. Add one line here: "tag_name": _hook_<name>
#   3. Use the tag in a data/conditions/<id>.json or data/actions/<id>.json file.
#   4. Add a test in tests/test_combat_engine.py.
# See CONTRIBUTING.md for the full walkthrough.
#
_HOOK_DISPATCH: dict[str, object] = {
    # Attack
    "melee_attack":    _hook_weapon_attack,
    "check_death":     _hook_check_death,
    # Movement
    "move_to_band":    _hook_move_to_band,
    "block_movement":  _hook_block_movement,
    # Conditions
    "apply_condition": _hook_apply_condition,
    "deal_damage":     _hook_deal_damage,
    "skip_action":     _hook_skip_action,
    # Flee
    "abscond_roll":    _hook_abscond_roll,
    # Gear management
    "resolve_equip":   _hook_resolve_equip,
    "resolve_unequip": _hook_resolve_unequip,
}


# ---------------------------------------------------------------------------
# Condition lifecycle orchestration
# ---------------------------------------------------------------------------
# These are called by auto_resolve_round in combat.py at defined pipeline steps.

def _fire_turn_start_hooks(state: GameState, log: list[str]) -> None:
    """Fire on_turn_start hooks for all combatants before the action loop."""
    if state.battlefield is None:
        return
    for combatant_id in list(state.battlefield.combatants):
        char      = state.characters.get(combatant_id)
        npc       = _find_npc(state, combatant_id)
        combatant = char if char else npc
        for cond in (combatant.active_conditions if combatant else []):
            cond_def = CONDITION_REGISTRY.get(cond.condition_id)
            if cond_def:
                entry = cond_def.hooks.get("on_turn_start")
                if entry:
                    _dispatch_hook(entry, state, combatant_id, None, log)


def _tick_actor_conditions(state: GameState, actor_id: UUID, log: list[str]) -> None:
    """
    Fire on_turn_end hooks and decrement/expire conditions for one combatant.
    Called immediately after each combatant acts (or is skipped) in initiative
    order, so that a 1-round condition applied at the start of a round is still
    active for its full turn before expiring.
    """
    char      = state.characters.get(actor_id)
    npc       = _find_npc(state, actor_id)
    combatant = char if char else npc
    if combatant is None:
        return

    still_active: list[ActiveCondition] = []
    for cond in combatant.active_conditions:
        cond_def = CONDITION_REGISTRY.get(cond.condition_id)
        if cond_def:
            entry = cond_def.hooks.get("on_turn_end")
            if entry:
                _dispatch_hook(entry, state, actor_id, None, log)

        if cond.duration_rounds is None:
            still_active.append(cond)
        elif cond.duration_rounds > 1:
            cond.duration_rounds -= 1
            still_active.append(cond)
        else:
            cond_name = cond_def.label if cond_def else cond.condition_id
            name = _combatant_name(state, actor_id)
            log.append(f"{name}'s {cond_name} condition has expired.")

    combatant.active_conditions = still_active


def _tick_conditions(state: GameState, log: list[str]) -> None:
    """
    Bulk end-of-round tick: fires on_turn_end hooks and decrements/expires
    conditions for every combatant.  Kept for reference; the round pipeline
    uses per-actor ticking via _tick_actor_conditions instead.
    """
    if state.battlefield is None:
        return
    for combatant_id in list(state.battlefield.combatants):
        _tick_actor_conditions(state, combatant_id, log)
