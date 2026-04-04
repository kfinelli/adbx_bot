"""
engine/combat.py — Core combat logic for ROUNDS mode.

Public API (all exported via engine/__init__.py):
    CombatAction         — typed structured action submitted by a player
    initialize_battlefield(state) → EngineResult
    auto_resolve_round(state)     → EngineResult
    apply_condition(state, target_id, condition_id, duration) → EngineResult

Internal helpers (used only within this module):
    _npc_decide(state, npc_id, cs) → CombatAction | None
    _execute_action(state, actor_id, action, log) → None
    _dispatch_hook(hook_entry, state, actor_id, action, log) → None
    _tick_conditions(state, log) → None
    _fire_turn_start_hooks(state, log) → None

Hook system
-----------
Effect logic is driven by hook entries from ActionDef.effect_tags and
ConditionDef.hooks.  A hook entry is either:

  • A plain string — tag name, no parameters:
        "check_death"
        "skip_action"

  • A hook object — tag name + params dict:
        {"tag": "deal_damage", "dice": "1d4", "type": "poison"}
        {"tag": "melee_attack", "dice": "1d6"}

_dispatch_hook() unwraps both forms and calls:
    handler(state, actor_id, action, log, params)

where `params` is always a dict (empty {} for plain-string tags).

To add a new hook, see CONTRIBUTING.md.

Condition hooks fire at defined points in the round pipeline:
    on_turn_start  — step 2b, before the action loop
    on_turn_end    — step 5, inside _tick_conditions
    on_move        — inside _hook_move_to_band, before movement executes

NPC AI is intentionally simple: move toward players if far; attack the
lowest-HP active character if in range.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from uuid import UUID

from engine.azure_constants import POWER_LEVEL
from engine.azure_helpers import get_stat_modifier
from engine.item import ChargeWeapon
from models import (
    NPC,
    ActiveCondition,
    CombatantState,
    CombatBattlefield,
    GameState,
    RangeBand,
)

from .data_loader import ACTION_REGISTRY, CONDITION_REGISTRY
from .dice import roll_dice_expr
from .helpers import _err, _now, _ok

# ---------------------------------------------------------------------------
# Range band ordering — used for movement and adjacency checks
# ---------------------------------------------------------------------------

_BAND_ORDER: list[RangeBand] = [
    RangeBand.FAR_MINUS,
    RangeBand.CLOSE_MINUS,
    RangeBand.ENGAGE,
    RangeBand.CLOSE_PLUS,
    RangeBand.FAR_PLUS,
]

_BAND_INDEX: dict[RangeBand, int] = {b: i for i, b in enumerate(_BAND_ORDER)}


def _adjacent_bands(band: RangeBand) -> list[RangeBand]:
    """Return the bands directly adjacent (one step either side) to band."""
    idx = _BAND_INDEX[band]
    result = []
    if idx > 0:
        result.append(_BAND_ORDER[idx - 1])
    if idx < len(_BAND_ORDER) - 1:
        result.append(_BAND_ORDER[idx + 1])
    return result


def _step_toward(current: RangeBand, target: RangeBand) -> RangeBand:
    """Return the band one step from current toward target."""
    ci = _BAND_INDEX[current]
    ti = _BAND_INDEX[target]
    if ci < ti:
        return _BAND_ORDER[ci + 1]
    if ci > ti:
        return _BAND_ORDER[ci - 1]
    return current


# ---------------------------------------------------------------------------
# CombatAction — typed structured action from a player or NPC
# ---------------------------------------------------------------------------

@dataclass
class CombatAction:
    """
    A fully specified combat action ready for resolution.

    action_id     : Key into ACTION_REGISTRY.
    target_id     : UUID of the target combatant (when ActionDef.requires_target).
    destination   : RangeBand to move to (when ActionDef.requires_destination).
    free_text     : Player-supplied description for "affect" actions.
    is_affect     : True when this is a free-text Affect submission.
    """
    action_id:   str              = "affect"
    target_id:   UUID | None      = None
    destination: RangeBand | None = None
    free_text:   str              = ""
    weapon_id:   str | None       = None

    @property
    def is_affect(self) -> bool:
        return self.action_id == "affect"

    def to_dict(self) -> dict:
        return {
            "action_id":   self.action_id,
            "target_id":   str(self.target_id) if self.target_id else None,
            "destination": self.destination.value if self.destination else None,
            "free_text":   self.free_text,
            "weapon_id":   self.weapon_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> CombatAction:
        dest_raw = d.get("destination")
        tid_raw  = d.get("target_id")
        return cls(
            action_id=d.get("action_id", "affect"),
            target_id=UUID(tid_raw) if tid_raw else None,
            destination=RangeBand(dest_raw) if dest_raw else None,
            free_text=d.get("free_text", ""),
            weapon_id=d.get("weapon_id"),
        )


# ---------------------------------------------------------------------------
# initialize_battlefield
# ---------------------------------------------------------------------------

def initialize_battlefield(state: GameState) -> CombatBattlefield:
    """
    Build a fresh CombatBattlefield for the current room encounter.
    Players start at FAR_MINUS; NPCs start at FAR_PLUS.
    Initiative: 1d10 + DEX modifier for players, 1d10 for NPCs.
    """
    bf = CombatBattlefield()

    for char_id, char in state.characters.items():
        if char.status.value != "active":
            continue
        dex_mod = get_stat_modifier(char.ability_scores.finesse)
        bf.combatants[char_id] = CombatantState(
            combatant_id=char_id,
            is_player=True,
            range_band=RangeBand.FAR_MINUS,
            initiative=random.randint(1, 10) + dex_mod,
        )

    for npc in state.npcs_in_current_room:
        if npc.status == "dead":
            continue
        bf.combatants[npc.npc_id] = CombatantState(
            combatant_id=npc.npc_id,
            is_player=False,
            range_band=RangeBand.FAR_PLUS,
            initiative=random.randint(1, 10),
        )

    return bf


# ---------------------------------------------------------------------------
# apply_condition
# ---------------------------------------------------------------------------

def apply_condition(
    state:        GameState,
    target_id:    UUID,
    condition_id: str,
    duration:     int | None = None,
    source_id:    UUID | None = None,
) -> object:  # EngineResult
    """
    Apply a status condition to a combatant by ID.
    duration=None means permanent (removed only by explicit dispel).
    Re-applying an existing condition refreshes its duration.
    """
    if condition_id not in CONDITION_REGISTRY:
        return _err(state, f"Unknown condition '{condition_id}'.")

    target_char = state.characters.get(target_id)
    target_npc  = _find_npc(state, target_id)
    combatant   = target_char if target_char else target_npc
    if combatant is None:
        return _err(state, f"Combatant {target_id} not found.")

    cond_def    = CONDITION_REGISTRY[condition_id]
    target_name = _combatant_name(state, target_id)

    # Stackable conditions accumulate stacks rather than being replaced.
    if cond_def.stackable:
        existing = next(
            (c for c in combatant.active_conditions if c.condition_id == condition_id), None
        )
        if existing is not None:
            existing.stacks += 1
            state.updated_at = _now()
            return _ok(state, f"{target_name} is now {cond_def.label} ×{existing.stacks}.")

    combatant.active_conditions = [
        c for c in combatant.active_conditions if c.condition_id != condition_id
    ]
    combatant.active_conditions.append(ActiveCondition(
        condition_id=condition_id,
        duration_rounds=duration,
        source_id=source_id,
    ))
    state.updated_at = _now()
    return _ok(state, f"{target_name} is now {cond_def.label}.")


# ---------------------------------------------------------------------------
# instant_move
# ---------------------------------------------------------------------------

def instant_move(state: GameState, char_id: UUID, destination: RangeBand) -> object:  # EngineResult
    """
    Resolve a player's Move action immediately, outside the round submission
    queue.  The range_band update is visible to subsequent Act submissions this
    round.  Appends a narrative line to battlefield.round_log.
    """
    if state.battlefield is None:
        return _err(state, "Not in combat.")
    cs = state.battlefield.combatants.get(char_id)
    if cs is None:
        return _err(state, "Not in combat.")
    if cs.used_move:
        return _err(state, "You've already moved this round.")

    action = CombatAction(action_id="move", destination=destination)
    log: list[str] = []
    _hook_move_to_band(state, char_id, action, log, {})

    cs.used_move = True
    state.battlefield.round_log.extend(log)
    state.updated_at = _now()
    return _ok(state, "\n".join(log) if log else "Move resolved.")


# ---------------------------------------------------------------------------
# auto_resolve_round
# ---------------------------------------------------------------------------

def auto_resolve_round(state: GameState) -> object:  # EngineResult
    """
    Resolve all player submissions and NPC actions for the current round.

    Pipeline:
      1.  Collect player actions from current_turn.submissions.
      2.  NPC decisions.
      2b. Fire on_turn_start hooks (sets skip_action / movement_blocked).
      3.  Sort by initiative (descending).
      4.  Execute each action; skip stunned combatants.
      5.  Tick conditions (fires on_turn_end hooks, decrements durations).
      6.  Reset single-round flags.
      7.  Build narrative.
    """
    if state.battlefield is None:
        return _err(state, "No active battlefield.")
    if state.current_turn is None:
        return _err(state, "No current turn to resolve.")

    log: list[str] = []
    bf  = state.battlefield

    # --- 1. Player actions
    player_actions: dict[UUID, CombatAction] = {}
    for sub in state.current_turn.submissions:
        if not sub.is_latest or not sub.combat_action:
            continue
        player_actions[sub.character_id] = CombatAction.from_dict(sub.combat_action)

    # --- 2. NPC decisions
    npc_actions: dict[UUID, CombatAction] = {}
    for npc in state.npcs_in_current_room:
        if npc.status == "dead":
            continue
        cs = bf.combatants.get(npc.npc_id)
        if cs is None:
            continue
        action = _npc_decide(state, npc.npc_id, cs)
        if action:
            npc_actions[npc.npc_id] = action

    # --- 2b. on_turn_start hooks (e.g. stunned → skip_action flag)
    _fire_turn_start_hooks(state, log)

    # --- 3. Sort by initiative (descending)
    all_actors: list[tuple[UUID, CombatAction]] = [
        *player_actions.items(),
        *npc_actions.items(),
    ]
    all_actors.sort(
        key=lambda pair: bf.combatants[pair[0]].initiative if pair[0] in bf.combatants else 0,
        reverse=True,
    )

    # --- 4. Execute actions
    for actor_id, action in all_actors:
        cs = bf.combatants.get(actor_id)
        if cs is None or not _is_alive(state, actor_id):
            continue
        if cs.skip_action:
            log.append(f"{_combatant_name(state, actor_id)} is stunned and cannot act this round!")
            continue
        _execute_action(state, actor_id, action, log)
        cs.acted_this_round = True

    # --- 5. Tick conditions
    _tick_conditions(state, log)

    # --- 6. Reset single-round flags
    for cs in bf.combatants.values():
        cs.acted_this_round = False
        cs.skip_action      = False
        cs.movement_blocked = False
        cs.used_move        = False
        cs.used_oracle      = False

    # --- 7. Build narrative
    narrative   = "\n".join(log) if log else "The round passes without incident."
    bf.round_log = log[:]
    state.updated_at = _now()

    # If Abscond succeeded this round, exit combat now (after all actions resolved).
    if bf.abscond_succeeded:
        from engine.session import SessionManager  # local import avoids circular dep
        SessionManager().exit_rounds(state)

    return _ok(state, narrative)


# ---------------------------------------------------------------------------
# _execute_action
# ---------------------------------------------------------------------------

def _execute_action(
    state:    GameState,
    actor_id: UUID,
    action:   CombatAction,
    log:      list[str],
) -> None:
    """Execute one CombatAction, dispatching each effect tag in order."""
    action_def = ACTION_REGISTRY.get(action.action_id)
    if action_def is None:
        log.append(f"[Unknown action '{action.action_id}' — skipped]")
        return

    if action_def.range_requirement:
        cs = state.battlefield.combatants.get(actor_id)
        if cs and cs.range_band.value not in action_def.range_requirement:
            log.append(
                f"{_combatant_name(state, actor_id)} cannot use {action_def.label} "
                f"from {cs.range_band.value}."
            )
            return

    for hook_entry in action_def.effect_tags:
        _dispatch_hook(hook_entry, state, actor_id, action, log)


# ---------------------------------------------------------------------------
# _npc_decide
# ---------------------------------------------------------------------------

def _npc_decide(
    state:  GameState,
    npc_id: UUID,
    cs:     CombatantState,
) -> CombatAction | None:
    """Simple NPC AI: move toward players if far; attack lowest-HP player if in range."""
    attack_def = ACTION_REGISTRY.get("attack")

    living_players = [
        (cid, pcs) for cid, pcs in state.battlefield.combatants.items()
        if pcs.is_player and _is_alive(state, cid)
    ]
    if not living_players:
        return None

    if attack_def and (
        not attack_def.range_requirement
        or cs.range_band.value in attack_def.range_requirement
    ):
        target_id = _lowest_hp_player(state, living_players)
        if target_id:
            return CombatAction(action_id="attack", target_id=target_id)

    destination = _step_toward(cs.range_band, RangeBand.ENGAGE)
    if destination != cs.range_band:
        return CombatAction(action_id="move", destination=destination)
    return None


# ---------------------------------------------------------------------------
# Condition stat modifier helpers
# ---------------------------------------------------------------------------

def _effective_stat_mod(state: GameState, actor_id: UUID, stat: str) -> int:
    """
    Return the effective modifier for `stat` including active condition bonuses.
    `stat` is an AzureStats field name: "physique", "finesse", "reason", or "savvy".
    Returns 0 for NPCs (no ability scores).
    """
    actor_char = state.characters.get(actor_id)
    if actor_char is None:
        return 0

    base_val = getattr(actor_char.ability_scores, stat, 0)
    base_mod = get_stat_modifier(base_val)

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
    Finesse (dodge target number) after condition stat_modifiers {"finesse": N}, floored at 0.
    "abjuring" uses +200 so attacks need a much higher roll to hit.
    """
    char = state.characters.get(combatant_id)
    npc  = _find_npc(state, combatant_id)
    base = char.ability_scores.finesse if char else (npc.ability_scores.finesse if npc else 0)
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
    action:   CombatAction | None,
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
# `params` is always a dict (empty {} when the hook was a plain string tag).
# See CONTRIBUTING.md for a step-by-step guide to adding a new hook.
# ---------------------------------------------------------------------------

def _hook_weapon_attack(
    state:    GameState,
    actor_id: UUID,
    action:   CombatAction | None,
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
    min_damage = max(1, str_mod)
    damage = max(min_damage, base_roll + crit_bonus + str_mod)
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
    action:   CombatAction | None,
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


def _hook_move_to_band(
    state:    GameState,
    actor_id: UUID,
    action:   CombatAction | None,
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

    actor_name = _combatant_name(state, actor_id)
    old_band   = cs.range_band

    adjacent = _adjacent_bands(old_band)
    if action.destination in adjacent:
        cs.range_band = action.destination
        log.append(f"{actor_name} moves from {old_band.value} to {action.destination.value}.")
    elif action.destination == old_band:
        log.append(f"{actor_name} holds position at {old_band.value}.")
    else:
        one_step = _step_toward(old_band, action.destination)
        cs.range_band = one_step
        log.append(
            f"{actor_name} moves toward {action.destination.value} "
            f"(now at {one_step.value})."
        )


def _hook_deal_damage(
    state:    GameState,
    actor_id: UUID,
    action:   CombatAction | None,
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
    action:   CombatAction | None,
    log:      list[str],
    params:   dict,
) -> None:
    """
    Apply a condition to action.target_id.

    params:
      condition  (str, required) — condition_id to apply
      duration   (int, default 3) — duration in rounds; omit for permanent
    """
    condition_id = params.get("condition")
    if not condition_id:
        log.append("[apply_condition: 'condition' param is required]")
        return

    if action is None or action.target_id is None:
        log.append(f"[apply_condition({condition_id}): no target specified]")
        return

    target_id   = action.target_id
    actor_name  = _combatant_name(state, actor_id)
    target_name = _combatant_name(state, target_id)

    cs = state.battlefield.combatants.get(target_id) if state.battlefield else None
    if cs is None:
        log.append(f"[apply_condition({condition_id}): target not on battlefield]")
        return

    duration = params.get("duration", 3)
    apply_condition(state, target_id, condition_id, duration=duration, source_id=actor_id)

    cond_def = CONDITION_REGISTRY.get(condition_id)
    label    = cond_def.label if cond_def else condition_id
    log.append(f"{actor_name} applies {label} to {target_name}! ({duration} rounds)")


def _hook_skip_action(
    state:    GameState,
    actor_id: UUID,
    action:   CombatAction | None,
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
    action:   CombatAction | None,
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
    action:   CombatAction | None,
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

    # Positional block: any enemy at ENGAGE prevents escape
    for cid, cs in bf.combatants.items():
        if not cs.is_player and cs.range_band == RangeBand.ENGAGE:
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
}


# ---------------------------------------------------------------------------
# _fire_turn_start_hooks
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# _tick_conditions
# ---------------------------------------------------------------------------

def _tick_conditions(state: GameState, log: list[str]) -> None:
    """
    End-of-round condition processing:
      - Fire on_turn_end hooks.
      - Decrement duration_rounds; remove expired conditions.
    """
    if state.battlefield is None:
        return

    for combatant_id in list(state.battlefield.combatants):
        char      = state.characters.get(combatant_id)
        npc       = _find_npc(state, combatant_id)
        combatant = char if char else npc
        if combatant is None:
            continue

        still_active: list[ActiveCondition] = []
        for cond in combatant.active_conditions:
            cond_def = CONDITION_REGISTRY.get(cond.condition_id)
            if cond_def:
                entry = cond_def.hooks.get("on_turn_end")
                if entry:
                    _dispatch_hook(entry, state, combatant_id, None, log)

            if cond.duration_rounds is None:
                still_active.append(cond)
            elif cond.duration_rounds > 1:
                cond.duration_rounds -= 1
                still_active.append(cond)
            else:
                cond_name = cond_def.label if cond_def else cond.condition_id
                name = _combatant_name(state, combatant_id)
                log.append(f"{name}'s {cond_name} condition has expired.")

        combatant.active_conditions = still_active


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

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


def _is_alive(state: GameState, cid: UUID) -> bool:
    char = state.characters.get(cid)
    if char:
        return char.status.value == "active" and char.hp_current > 0
    npc = _find_npc(state, cid)
    if npc:
        return npc.status != "dead" and npc.hp_current > 0
    return False


def _lowest_hp_player(
    state:          GameState,
    living_players: list[tuple[UUID, CombatantState]],
) -> UUID | None:
    best_id, best_hp = None, float("inf")
    for cid, _ in living_players:
        char = state.characters.get(cid)
        if char and char.hp_current < best_hp:
            best_hp = char.hp_current
            best_id = cid
    return best_id
