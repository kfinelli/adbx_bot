"""
engine/combat.py — Core combat logic for ROUNDS mode.

Public API (all exported via engine/__init__.py):
    CombatAction         — typed structured action submitted by a player
    initialize_battlefield(state) → EngineResult
    auto_resolve_round(state)     → EngineResult
    apply_condition(state, target_id, condition_id, duration) → EngineResult

Internal helpers (used only within this module and engine/__init__.py):
    _npc_decide(state, npc_id, cs) → CombatAction | None
    _execute_action(state, actor_id, action, log) → None
    _dispatch_hook(tag, state, actor_id, action, log) → None
    _tick_conditions(state, log) → None
    _fire_turn_start_hooks(state, log) → None

Design notes
------------
- All functions take GameState and mutate it in-place, returning EngineResult.
- Effect logic is driven by string tags from ActionDef/ConditionDef.
  _dispatch_hook() maps tags to handler functions; adding a new effect
  requires only a new handler + one entry in _HOOK_DISPATCH.
- Condition hooks fire at defined points in the round pipeline:
    on_turn_start  — before actions are executed (step 2b)
    on_turn_end    — after actions, inside _tick_conditions (step 5)
    on_move        — inside _hook_move_to_band, before movement executes
- NPC AI is intentionally simple: move toward players if far, attack
  lowest-HP active character if in range.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from models import (
    NPC,
    ActiveCondition,
    CombatantState,
    CombatBattlefield,
    GameState,
    RangeBand,
)
from tables import ABILITY_MODIFIERS

from .data_loader import ACTION_REGISTRY, CONDITION_REGISTRY
from .helpers import _err, _ok

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

    action_id     : Key into ACTION_REGISTRY (e.g. "attack", "move", "affect").
    target_id     : UUID of the target combatant (required when ActionDef.requires_target).
    destination   : RangeBand to move to (required when ActionDef.requires_destination).
    free_text     : Player-supplied description for "affect" actions and for
                    display in the round narrative.
    is_affect     : True when this is a free-text Affect submission; suppresses
                    auto-resolution and hands off to DM.
    """
    action_id:   str             = "affect"
    target_id:   UUID | None  = None
    destination: RangeBand | None = None
    free_text:   str             = ""

    @property
    def is_affect(self) -> bool:
        return self.action_id == "affect"

    def to_dict(self) -> dict:
        """Serialise to plain dict for storage in PlayerTurnSubmission.combat_action."""
        return {
            "action_id":   self.action_id,
            "target_id":   str(self.target_id) if self.target_id else None,
            "destination": self.destination.value if self.destination else None,
            "free_text":   self.free_text,
        }

    @classmethod
    def from_dict(cls, d: dict) -> CombatAction:
        """Reconstruct from the dict stored in PlayerTurnSubmission.combat_action."""
        dest_raw = d.get("destination")
        tid_raw  = d.get("target_id")
        return cls(
            action_id=d.get("action_id", "affect"),
            target_id=UUID(tid_raw) if tid_raw else None,
            destination=RangeBand(dest_raw) if dest_raw else None,
            free_text=d.get("free_text", ""),
        )


# ---------------------------------------------------------------------------
# initialize_battlefield
# ---------------------------------------------------------------------------

def initialize_battlefield(state: GameState) -> CombatBattlefield:
    """
    Build a fresh CombatBattlefield for the current room encounter.

    - Active player characters start at FAR_MINUS.
    - NPCs currently in the party's room start at FAR_PLUS.
    - Each combatant receives a random initiative roll (1d10 + DEX modifier
      for players, 1d10 for NPCs).

    Returns the new CombatBattlefield (also stored on state.battlefield).
    """
    bf = CombatBattlefield()

    for char_id, char in state.characters.items():
        if char.status.value != "active":
            continue
        dex_mod = ABILITY_MODIFIERS.get(char.ability_scores.dexterity, 0)
        initiative = random.randint(1, 10) + dex_mod
        bf.combatants[char_id] = CombatantState(
            combatant_id=char_id,
            is_player=True,
            range_band=RangeBand.FAR_MINUS,
            initiative=initiative,
        )

    for npc in state.npcs_in_current_room:
        if npc.status == "dead":
            continue
        initiative = random.randint(1, 10)
        bf.combatants[npc.npc_id] = CombatantState(
            combatant_id=npc.npc_id,
            is_player=False,
            range_band=RangeBand.FAR_PLUS,
            initiative=initiative,
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
) -> object:  # EngineResult — imported at call site to avoid circular
    """
    Apply a status condition to a combatant by ID.
    target_id must be the character_id or npc_id of an active combatant.
    duration=None means permanent (removed only by explicit dispel).
    """
    if condition_id not in CONDITION_REGISTRY:
        return _err(state, f"Unknown condition '{condition_id}'.")

    if state.battlefield is None:
        return _err(state, "No active battlefield — conditions can only be applied in ROUNDS mode.")

    cs = state.battlefield.combatants.get(target_id)
    if cs is None:
        return _err(state, f"Combatant {target_id} not found on battlefield.")

    # Remove any existing instance of the same condition (re-applying refreshes it)
    cs.active_conditions = [
        c for c in cs.active_conditions if c.condition_id != condition_id
    ]
    cs.active_conditions.append(ActiveCondition(
        condition_id=condition_id,
        duration_rounds=duration,
        source_id=source_id,
    ))

    cond_def    = CONDITION_REGISTRY[condition_id]
    target_name = _combatant_name(state, target_id)
    state.updated_at = _now()
    return _ok(state, f"{target_name} is now {cond_def.label}.")


# ---------------------------------------------------------------------------
# auto_resolve_round
# ---------------------------------------------------------------------------

def auto_resolve_round(state: GameState) -> object:  # EngineResult
    """
    Resolve all player submissions and NPC actions for the current round.

    Called automatically by TurnManager.submit_turn() when all active
    players have submitted structured (non-Affect) CombatActions.

    Pipeline:
      1. Collect player actions from current_turn.submissions.
      2. Add NPC decisions.
      2b. Fire on_turn_start condition hooks (sets skip_action / movement_blocked).
      3. Sort all actions by initiative (descending).
      4. Execute each action via _execute_action(), skipping stunned combatants.
      5. Tick status condition durations (fires on_turn_end hooks).
      6. Reset single-round flags (acted, skip_action, movement_blocked).
      7. Build narrative from round_log, set as TurnRecord.resolution.

    Returns an EngineResult whose .message is the full round narrative.
    The caller (TurnManager) is responsible for moving the turn to history.
    """
    if state.battlefield is None:
        return _err(state, "No active battlefield.")
    if state.current_turn is None:
        return _err(state, "No current turn to resolve.")

    log: list[str] = []
    bf  = state.battlefield

    # --- 1. Collect player actions -----------------------------------------
    player_actions: dict[UUID, CombatAction] = {}
    for sub in state.current_turn.submissions:
        if not sub.is_latest:
            continue
        if sub.combat_action:
            player_actions[sub.character_id] = CombatAction.from_dict(sub.combat_action)

    # --- 2. NPC decisions ---------------------------------------------------
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

    # --- 2b. Fire on_turn_start hooks ---------------------------------------
    # Must happen after actions are collected but before execution, so that
    # stunned combatants have their skip_action flag set before the action loop.
    _fire_turn_start_hooks(state, log)

    # --- 3. Sort by initiative (descending) ---------------------------------
    all_actors: list[tuple[UUID, CombatAction]] = []
    for cid, action in player_actions.items():
        all_actors.append((cid, action))
    for nid, action in npc_actions.items():
        all_actors.append((nid, action))

    all_actors.sort(
        key=lambda pair: bf.combatants[pair[0]].initiative
        if pair[0] in bf.combatants else 0,
        reverse=True,
    )

    # --- 4. Execute actions -------------------------------------------------
    for actor_id, action in all_actors:
        cs = bf.combatants.get(actor_id)
        if cs is None:
            continue
        # Skip dead combatants (may have died earlier this round)
        if not _is_alive(state, actor_id):
            continue
        # Skip stunned combatants (skip_action set by on_turn_start hook)
        if cs.skip_action:
            actor_name = _combatant_name(state, actor_id)
            log.append(f"{actor_name} is stunned and cannot act this round!")
            continue
        _execute_action(state, actor_id, action, log)
        cs.acted_this_round = True

    # --- 5. Tick conditions (fires on_turn_end hooks, decrements durations) -
    _tick_conditions(state, log)

    # --- 6. Reset single-round flags ----------------------------------------
    for cs in bf.combatants.values():
        cs.acted_this_round  = False
        cs.skip_action       = False
        cs.movement_blocked  = False

    # --- 7. Build narrative -------------------------------------------------
    narrative = "\n".join(log) if log else "The round passes without incident."
    bf.round_log = log[:]

    state.updated_at = _now()
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
    """
    Execute one CombatAction for actor_id, appending narrative to log.
    Dispatches each effect_tag in the action's ActionDef via _dispatch_hook.
    """
    action_def = ACTION_REGISTRY.get(action.action_id)
    if action_def is None:
        log.append(f"[Unknown action '{action.action_id}' — skipped]")
        return

    # Range check (empty list = no restriction)
    if action_def.range_requirement:
        cs = state.battlefield.combatants.get(actor_id)
        if cs and cs.range_band.value not in action_def.range_requirement:
            actor_name = _combatant_name(state, actor_id)
            log.append(f"{actor_name} cannot use {action_def.label} from {cs.range_band.value}.")
            return

    for tag in action_def.effect_tags:
        _dispatch_hook(tag, state, actor_id, action, log)


# ---------------------------------------------------------------------------
# _npc_decide
# ---------------------------------------------------------------------------

def _npc_decide(
    state:  GameState,
    npc_id: UUID,
    cs:     CombatantState,
) -> CombatAction | None:
    """
    Simple NPC AI: move toward players if far; attack lowest-HP player if
    in a valid attack range band.

    Returns a CombatAction or None if the NPC has no valid action.
    """
    attack_def = ACTION_REGISTRY.get("attack")

    # Find all living player combatants
    living_players = [
        (cid, pcs) for cid, pcs in state.battlefield.combatants.items()
        if pcs.is_player and _is_alive(state, cid)
    ]
    if not living_players:
        return None

    # Can we attack from here?
    if attack_def and (
        not attack_def.range_requirement
        or cs.range_band.value in attack_def.range_requirement
    ):
        target_id = _lowest_hp_player(state, living_players)
        if target_id:
            return CombatAction(action_id="attack", target_id=target_id)

    # Otherwise move toward the nearest player
    destination = _step_toward(cs.range_band, RangeBand.ENGAGE)
    if destination != cs.range_band:
        return CombatAction(action_id="move", destination=destination)

    return None


# ---------------------------------------------------------------------------
# Condition stat modifier helper
# ---------------------------------------------------------------------------

def _effective_str_mod(state: GameState, actor_id: UUID) -> int:
    """
    Return the effective STR modifier for actor_id, including any bonus
    or penalty from active conditions with a 'strength' stat_modifier.

    For NPCs there is no ability score, so condition modifiers are still
    applied as a flat bonus to their attack_bonus (handled in the attack hook).
    """
    actor_char = state.characters.get(actor_id)
    if actor_char is None:
        return 0

    base_mod = ABILITY_MODIFIERS.get(actor_char.ability_scores.strength, 0)

    # Sum any condition-based strength modifiers
    cs = state.battlefield.combatants.get(actor_id) if state.battlefield else None
    if cs is None:
        return base_mod

    condition_bonus = 0
    for active_cond in cs.active_conditions:
        cond_def = CONDITION_REGISTRY.get(active_cond.condition_id)
        if cond_def:
            condition_bonus += cond_def.stat_modifiers.get("strength", 0)

    return base_mod + condition_bonus


# ---------------------------------------------------------------------------
# Effect tag handlers
# ---------------------------------------------------------------------------

def _hook_melee_damage_str_mod(
    state:    GameState,
    actor_id: UUID,
    action:   CombatAction,
    log:      list[str],
) -> None:
    """
    Roll a melee attack against target's AC; on hit, deal weapon or base
    damage plus effective STR modifier (includes condition bonuses/penalties).

    Attack roll: 1d20 vs target AC (B/X: roll >= AC to hit, descending AC).
    Damage:      1d6 + effective STR modifier (base; Phase 5 reads weapon).
    """
    if action.target_id is None:
        log.append("[melee_damage_str_mod: no target specified]")
        return

    target_id   = action.target_id
    actor_name  = _combatant_name(state, actor_id)
    target_name = _combatant_name(state, target_id)

    # Attacker modifiers
    actor_char = state.characters.get(actor_id)
    actor_npc  = _find_npc(state, actor_id)
    if actor_char:
        str_mod      = _effective_str_mod(state, actor_id)
        attack_bonus = 0  # Base; Phase 5 adds THAC0/attack bonus
    elif actor_npc:
        str_mod      = 0
        attack_bonus = actor_npc.attack_bonus
    else:
        return

    # Target AC
    target_char = state.characters.get(target_id)
    target_npc  = _find_npc(state, target_id)
    if target_char:
        target_ac = target_char.armor_class
    elif target_npc:
        target_ac = target_npc.armor_class
    else:
        log.append(f"{actor_name} swings at nothing.")
        return

    # Attack roll (B/X descending AC: need roll >= AC to hit)
    roll = random.randint(1, 20) + attack_bonus
    if roll < target_ac:
        log.append(f"{actor_name} attacks {target_name} — misses! (rolled {roll} vs AC {target_ac})")
        return

    # Damage
    damage = max(1, random.randint(1, 6) + str_mod)
    if target_char:
        target_char.hp_current = max(0, target_char.hp_current - damage)
        hp_str = f"{target_char.hp_current}/{target_char.hp_max}"
    else:
        target_npc.hp_current = max(0, target_npc.hp_current - damage)
        hp_str = f"{target_npc.hp_current}/{target_npc.hp_max}"

    log.append(
        f"{actor_name} attacks {target_name} — hits! (rolled {roll} vs AC {target_ac}) "
        f"Deals {damage} damage. [{target_name}: {hp_str}]"
    )


def _hook_check_death(
    state:    GameState,
    actor_id: UUID,
    action:   CombatAction,
    log:      list[str],
) -> None:
    """
    After an attack, check whether the target has reached 0 HP and mark
    them dead if so.
    """
    if action.target_id is None:
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


def _hook_move_to_band(
    state:    GameState,
    actor_id: UUID,
    action:   CombatAction,
    log:      list[str],
) -> None:
    """
    Move the actor to action.destination (one step at a time).
    If the actor's movement_blocked flag is set (e.g. entangled), the move
    is cancelled and the on_move hook has already logged the reason.
    """
    cs = state.battlefield.combatants.get(actor_id)
    if cs is None:
        return

    # Fire on_move condition hooks before executing movement.
    # Hooks may set cs.movement_blocked to cancel the move.
    for active_cond in cs.active_conditions:
        cond_def = CONDITION_REGISTRY.get(active_cond.condition_id)
        if cond_def:
            move_tag = cond_def.hooks.get("on_move")
            if move_tag:
                _dispatch_hook(move_tag, state, actor_id, action, log)

    if cs.movement_blocked:
        return  # hook already logged the reason

    if action.destination is None:
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


def _hook_deal_1d4_poison_damage(
    state:    GameState,
    actor_id: UUID,
    action:   CombatAction | None,
    log:      list[str],
) -> None:
    """
    Poison tick: deal 1d4 damage to the poisoned combatant at end of turn.
    actor_id is the poisoned combatant (the hook is fired on their state).
    """
    damage      = random.randint(1, 4)
    actor_name  = _combatant_name(state, actor_id)

    actor_char = state.characters.get(actor_id)
    actor_npc  = _find_npc(state, actor_id)

    if actor_char:
        actor_char.hp_current = max(0, actor_char.hp_current - damage)
        hp_str = f"{actor_char.hp_current}/{actor_char.hp_max}"
        if actor_char.hp_current <= 0:
            from models import CharacterStatus
            actor_char.status = CharacterStatus.DEAD
            state.battlefield.combatants.pop(actor_id, None)
            log.append(f"{actor_name} takes {damage} poison damage and falls!")
            return
    elif actor_npc:
        actor_npc.hp_current = max(0, actor_npc.hp_current - damage)
        hp_str = f"{actor_npc.hp_current}/{actor_npc.hp_max}"
        if actor_npc.hp_current <= 0:
            actor_npc.status = "dead"
            state.battlefield.combatants.pop(actor_id, None)
            log.append(f"{actor_name} takes {damage} poison damage and is slain!")
            return
    else:
        return

    log.append(f"{actor_name} takes {damage} poison damage. [{actor_name}: {hp_str}]")


def _hook_skip_action(
    state:    GameState,
    actor_id: UUID,
    action:   CombatAction | None,
    log:      list[str],
) -> None:
    """
    Stun: set skip_action on the combatant so the action loop bypasses them.
    The log message is emitted by the action loop itself when it sees the flag.
    """
    cs = state.battlefield.combatants.get(actor_id)
    if cs is not None:
        cs.skip_action = True


def _hook_apply_poison_condition(
    state:    GameState,
    actor_id: UUID,
    action:   CombatAction,
    log:      list[str],
) -> None:
    """
    Apply the poisoned condition to the action's target for 3 rounds.
    Works at any range — no range_requirement on the poison action.
    """
    if action is None or action.target_id is None:
        log.append("[apply_poison_condition: no target specified]")
        return

    target_id   = action.target_id
    actor_name  = _combatant_name(state, actor_id)
    target_name = _combatant_name(state, target_id)

    cs = state.battlefield.combatants.get(target_id) if state.battlefield else None
    if cs is None:
        log.append(f"[apply_poison_condition: target {target_id} not on battlefield]")
        return

    # Re-applying refreshes the duration (handled inside apply_condition)
    apply_condition(state, target_id, "poisoned", duration=3, source_id=actor_id)
    log.append(f"{actor_name} poisons {target_name}! ({target_name} will take 1d4 damage per round for 3 rounds.)")


def _hook_block_movement(
    state:    GameState,
    actor_id: UUID,
    action:   CombatAction | None,
    log:      list[str],
) -> None:
    """
    Entangle: set movement_blocked so _hook_move_to_band cancels the move.
    """
    cs = state.battlefield.combatants.get(actor_id)
    if cs is not None:
        cs.movement_blocked = True
        actor_name = _combatant_name(state, actor_id)
        log.append(f"{actor_name} is entangled and cannot move!")


# ---------------------------------------------------------------------------
# _fire_turn_start_hooks
# ---------------------------------------------------------------------------

def _fire_turn_start_hooks(state: GameState, log: list[str]) -> None:
    """
    Fire on_turn_start condition hooks for every combatant with active
    conditions that define that hook.  Called before the action loop so
    that flags like skip_action are set before execution begins.
    """
    if state.battlefield is None:
        return
    for combatant_id, cs in list(state.battlefield.combatants.items()):
        for cond in cs.active_conditions:
            cond_def = CONDITION_REGISTRY.get(cond.condition_id)
            if cond_def:
                start_tag = cond_def.hooks.get("on_turn_start")
                if start_tag:
                    _dispatch_hook(start_tag, state, combatant_id, None, log)


# ---------------------------------------------------------------------------
# _tick_conditions
# ---------------------------------------------------------------------------

def _tick_conditions(state: GameState, log: list[str]) -> None:
    """
    End-of-round condition processing:
      - Fire on_turn_end hooks for each active condition.
      - Decrement duration_rounds; remove expired conditions.
    """
    if state.battlefield is None:
        return

    for combatant_id, cs in list(state.battlefield.combatants.items()):
        still_active: list[ActiveCondition] = []
        for cond in cs.active_conditions:
            cond_def = CONDITION_REGISTRY.get(cond.condition_id)
            if cond_def:
                end_tag = cond_def.hooks.get("on_turn_end")
                if end_tag:
                    _dispatch_hook(end_tag, state, combatant_id, None, log)

            # Tick duration
            if cond.duration_rounds is None:
                still_active.append(cond)   # permanent
            elif cond.duration_rounds > 1:
                cond.duration_rounds -= 1
                still_active.append(cond)
            else:
                cond_name = cond_def.label if cond_def else cond.condition_id
                name = _combatant_name(state, combatant_id)
                log.append(f"{name}'s {cond_name} condition has expired.")

        cs.active_conditions = still_active


# ---------------------------------------------------------------------------
# _dispatch_hook
# ---------------------------------------------------------------------------

def _dispatch_hook(
    tag:      str,
    state:    GameState,
    actor_id: UUID,
    action:   CombatAction | None,
    log:      list[str],
) -> None:
    """
    Map an effect tag string to its handler function and call it.
    Unknown tags are logged as warnings rather than raising, so a
    future tag in a data file doesn't crash an ongoing session.
    """
    handler = _HOOK_DISPATCH.get(tag)
    if handler is None:
        log.append(f"[Warning: unknown effect tag '{tag}' — skipped]")
        return
    handler(state, actor_id, action, log)


# Registry of tag → handler.  Add one entry here for each new effect.
_HOOK_DISPATCH: dict[str, object] = {
    # Attack / damage
    "melee_damage_str_mod":    _hook_melee_damage_str_mod,
    "check_death":             _hook_check_death,
    # Movement
    "move_to_band":            _hook_move_to_band,
    "block_movement":          _hook_block_movement,
    # Conditions — application
    "apply_poison_condition":  _hook_apply_poison_condition,
    # Conditions — per-round effects
    "deal_1d4_poison_damage":  _hook_deal_1d4_poison_damage,
    "skip_action":             _hook_skip_action,
}


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _combatant_name(state: GameState, cid: UUID) -> str:
    char = state.characters.get(cid)
    if char:
        return char.name
    npc = _find_npc(state, cid)
    if npc:
        return npc.name
    return str(cid)


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
    best_id  = None
    best_hp  = float("inf")
    for cid, _ in living_players:
        char = state.characters.get(cid)
        if char and char.hp_current < best_hp:
            best_hp = char.hp_current
            best_id = cid
    return best_id


def _now() -> datetime:
    return datetime.now(UTC)
