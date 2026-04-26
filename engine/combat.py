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

Hook system
-----------
Combat effects are data-driven. Actions (data/actions/*.json) and conditions
(data/conditions/*.json) declare effect tags that are dispatched at runtime
to handler functions.  All hook handlers, the dispatch table, and the
condition lifecycle orchestration live in engine/combat_hooks.py — that is
the file to edit when adding new combat effects.

Turn flow
---------
auto_resolve_round pipeline:
  1.  Collect player actions from current_turn.submissions.
  2.  NPC decisions.
  2b. Fire on_turn_start hooks (sets skip_action / movement_blocked).
  3.  Sort by initiative (descending).
  4.  Execute each action; skip stunned combatants.
  5.  Tick conditions (fires on_turn_end hooks, decrements durations).
  6.  Reset single-round flags.
  7.  Build narrative.

NPC AI is intentionally simple: move toward players if far; attack the
lowest-HP active character if in range.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from uuid import UUID

from models import (
    ActiveCondition,
    CombatantState,
    CombatBattlefield,
    GameState,
    RangeBand,
)

from .combat_hooks import (
    _combatant_name,
    _dispatch_hook,
    _effective_finesse,  # noqa: F401 — re-exported for tests
    _effective_stat_mod,  # noqa: F401 — re-exported for tests
    _find_npc,
    _fire_on_attack_hooks,
    _fire_turn_start_hooks,
    _hook_apply_condition,  # noqa: F401 — re-exported for tests
    _hook_move_to_band,
    _hook_weapon_attack,  # noqa: F401 — re-exported for tests
    _opportunity_attacks,  # noqa: F401 — re-exported for tests
    _tick_actor_conditions,
    _tick_conditions,  # noqa: F401 — kept for callers that use bulk tick
)
from .data_loader import ACTION_REGISTRY, CONDITION_REGISTRY
from .helpers import _err, _now, _ok
from .strings import fmt_string, get_string

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


def _band_distance(a: RangeBand, b: RangeBand) -> int:
    """Absolute number of band steps between two range bands."""
    return abs(_BAND_INDEX[a] - _BAND_INDEX[b])


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
    target_id     : UUID of the target combatant (when ActionDef.requires_target is not "none").
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
        action_def = ACTION_REGISTRY.get(self.action_id)
        return action_def is not None and action_def.action_type == "affect"

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
        dex_mod = char.ability_scores.finesse
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
    stacks:       int = 1,
) -> object:  # EngineResult
    """
    Apply a status condition to a combatant by ID.
    duration=None means permanent (removed only by explicit dispel).
    Re-applying an existing condition refreshes its duration.
    stacks sets the initial stack count for stackable conditions.
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
    # Only increment the existing instance if the source matches (same source reapplying).
    if cond_def.stackable:
        existing = next(
            (c for c in combatant.active_conditions
             if c.condition_id == condition_id and c.source_id == source_id),
            None,
        )
        if existing is not None:
            existing.stacks += 1
            state.updated_at = _now()
            return _ok(state, fmt_string("combat.condition.stacked", target_name=target_name, label=cond_def.label, stacks=existing.stacks))

    if cond_def.stackable:
        # For stackable conditions with multiple sources, preserve other sources' instances.
        # Remove only this source's existing instance (if any) before re-adding.
        combatant.active_conditions = [
            c for c in combatant.active_conditions
            if not (c.condition_id == condition_id and c.source_id == source_id)
        ]
    else:
        # Non-stackable: replace any existing instance regardless of source.
        combatant.active_conditions = [
            c for c in combatant.active_conditions if c.condition_id != condition_id
        ]
    combatant.active_conditions.append(ActiveCondition(
        condition_id=condition_id,
        duration_rounds=duration,
        source_id=source_id,
        stacks=stacks,
    ))
    state.updated_at = _now()
    return _ok(state, fmt_string("combat.condition.applied", target_name=target_name, label=cond_def.label))


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

    bf  = state.battlefield
    log: list[str] = list(bf.round_log)  # carry forward instant_move entries from this round

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

    # --- 3. Sort ALL combatants by initiative (descending).
    # We iterate every combatant in the battlefield — not just those with
    # submissions — so that conditions are ticked in initiative order even for
    # combatants who had no action this round.
    all_combatant_ids: list[UUID] = sorted(
        bf.combatants.keys(),
        key=lambda cid: bf.combatants[cid].initiative,
        reverse=True,
    )
    # Build a lookup of actions keyed by actor_id for O(1) access.
    action_map: dict[UUID, CombatAction] = {**player_actions, **npc_actions}

    # --- 4. Execute actions + per-actor condition tick
    for actor_id in all_combatant_ids:
        cs = bf.combatants.get(actor_id)
        if cs is None or not _is_alive(state, actor_id):
            continue
        action = action_map.get(actor_id)
        if action is not None:
            if cs.skip_action:
                log.append(fmt_string("combat.log.stunned", actor_name=_combatant_name(state, actor_id)))
            else:
                _execute_action(state, actor_id, action, log)
                cs.acted_this_round = True
        # Tick this combatant's conditions after their turn (or skipped turn).
        # Per-actor ticking ensures 1-round conditions last a full turn rather
        # than expiring immediately at end of round (issue #80).
        _tick_actor_conditions(state, actor_id, log)

    # --- 5. (No bulk _tick_conditions — handled per-actor above.)

    # --- 6. Reset single-round flags
    for cs in bf.combatants.values():
        cs.acted_this_round = False
        cs.skip_action      = False
        cs.movement_blocked = False
        cs.used_move        = False
        cs.used_oracle      = False

    # --- 7. Build narrative
    narrative   = "\n".join(log) if log else get_string("combat.log.no_action")
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

    # Gate oracle-consuming actions
    cs = state.battlefield.combatants.get(actor_id) if state.battlefield else None
    if action_def.consumes_oracle and cs is not None and cs.used_oracle:
        log.append(fmt_string(
            "combat.log.oracle_already_used",
            actor_name=_combatant_name(state, actor_id),
        ))
        return

    if isinstance(action_def.range_requirement, int):
        actor_cs  = state.battlefield.combatants.get(actor_id)
        target_cs = state.battlefield.combatants.get(action.target_id) if action.target_id else None
        if actor_cs and target_cs:
            dist = _band_distance(actor_cs.range_band, target_cs.range_band)
            if dist > action_def.range_requirement:
                log.append(fmt_string(
                    "combat.log.action_out_of_range",
                    actor_name=_combatant_name(state, actor_id),
                    label=action_def.label,
                    band=actor_cs.range_band.value,
                    dist=dist,
                    max_range=action_def.range_requirement,
                ))
                return

    for hook_entry in action_def.effect_tags:
        _dispatch_hook(hook_entry, state, actor_id, action, log)

    # Fire on_attack condition hooks after attack actions resolve
    if action_def.action_type == "attack":
        _fire_on_attack_hooks(state, actor_id, action, log)

    # Mark resource consumption flags after successful execution
    if cs is not None:
        if action_def.consumes_oracle:
            cs.used_oracle = True
        if action_def.consumes_move:
            cs.used_move = True


# ---------------------------------------------------------------------------
# _npc_decide
# ---------------------------------------------------------------------------

def _has_condition(state: GameState, cid: UUID, condition_id: str) -> bool:
    """Return True if the combatant has an active instance of the given condition."""
    char = state.characters.get(cid)
    if char:
        return any(c.condition_id == condition_id for c in char.active_conditions)
    npc = _find_npc(state, cid)
    if npc:
        return any(c.condition_id == condition_id for c in npc.active_conditions)
    return False


def _npc_decide(
    state:  GameState,
    npc_id: UUID,
    cs:     CombatantState,
) -> CombatAction | None:
    """Simple NPC AI: move toward players if far; attack lowest-HP player if in range."""
    living_players = [
        (cid, pcs) for cid, pcs in state.battlefield.combatants.items()
        if pcs.is_player and _is_alive(state, cid)
        and not _has_condition(state, cid, "hidden")
    ]
    if not living_players:
        return None

    npc_obj       = _find_npc(state, npc_id)
    npc_range     = npc_obj.weapon_range if npc_obj else 0
    target_id = _lowest_hp_player(state, living_players)
    if target_id:
        target_cs = state.battlefield.combatants.get(target_id)
        if target_cs and _band_distance(cs.range_band, target_cs.range_band) <= npc_range:
            return CombatAction(action_id="attack", target_id=target_id)

    destination = _step_toward(cs.range_band, RangeBand.ENGAGE)
    if destination != cs.range_band:
        return CombatAction(action_id="move", destination=destination)
    return None


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

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
