# Contributing Game Logic for Combat Using Conditions

This guide explains how to develop hooks which can be used to add effects and
actions to the combat engine.
---

## How hooks work

A **hook** is a named effect that fires at a specific point during combat
resolution. Every hook has:

- A **tag** — a short string that identifies what the hook does
  (e.g. `"deal_damage"`, `"skip_action"`)
- Optional **parameters** — a dict that configures the effect without
  requiring new Python code (e.g. `{"dice": "1d8", "type": "fire"}`)

Hook entries appear in two places:

**Condition files** (`data/conditions/<id>.json`) — fire at lifecycle points:

```json
"hooks": {
  "on_turn_end": {"tag": "deal_damage", "dice": "1d6", "type": "fire"}
}
```

**Action files** (`data/actions/<id>.json`) — fire when the action executes:

```json
"effect_tags": [
  {"tag": "melee_attack", "dice": "1d8"},
  "check_death"
]
```

A hook entry is either a plain string (tag, no params) or a hook object
(`{"tag": "...", ...params}`). Both forms are always valid.

---

## Adding a new hook: step-by-step

### Step 1 — Write the handler in `engine/combat.py`

Find the `# Hook handlers` section and add a new function.
Every handler has the same signature:

```python
def _hook_my_effect(
    state:    GameState,
    actor_id: UUID,
    action:   CombatAction | None,
    log:      list[str],
    params:   dict,
) -> None:
    """
    Brief description of what this hook does.

    params:
      param_name  (type, default X) — what it controls
    """
    # Read params with safe defaults
    value = params.get("param_name", default_value)

    # Mutate state in-place
    ...

    # Append a plain-English narrative line
    log.append(f"{actor_name} does something! [{target_name}: {hp_str}]")
```

Key rules:
- **Never raise exceptions** — use `log.append("[warning message]")` and return early
- **Defaults for all params** — `params.get("dice", "1d6")` not `params["dice"]`
- **One log line per meaningful event** — the log becomes the round narrative
- `actor_id` is the combatant *causing* the effect (may be None for condition ticks)
- For damage hooks fired by conditions, `actor_id` is the *affected* combatant

### Step 2 — Register the tag in `_HOOK_DISPATCH`

Still in `engine/combat.py`, find `_HOOK_DISPATCH` near the bottom:

```python
_HOOK_DISPATCH: dict[str, object] = {
    # Attack
    "melee_attack":    _hook_melee_attack,
    ...
    # Your new hook:
    "my_effect":       _hook_my_effect,
}
```

### Step 3 — Use the tag in a data file

**New condition** (`data/conditions/burning.json`):

```json
{
  "condition_id": "burning",
  "label": "Burning",
  "duration_type": "rounds",
  "hooks": {
    "on_turn_end": {"tag": "deal_damage", "dice": "1d6", "type": "fire"}
  },
  "stat_modifiers": {},
  "grants_actions": []
}
```

**New action** (`data/actions/smite.json`):

```json
{
  "action_id": "smite",
  "label": "Smite",
  "button_style": "danger",
  "action_type": "attack",
  "description": "A holy strike that deals bonus radiant damage.",
  "requires_target": true,
  "requires_destination": false,
  "range_requirement": ["engage", "close_minus", "close_plus"],
  "effect_tags": [
    {"tag": "melee_attack", "dice": "1d6"},
    {"tag": "deal_damage", "dice": "1d8", "type": "radiant"},
    "check_death"
  ]
}
```

The file name (stem) must match `condition_id` / `action_id`.

### Step 4 — (Optional) Write a test

Add a test in `tests/test_combat_engine.py`.
For a condition, add to `TestConditions`; for an action, add a new class.
At minimum, test:

- The data file loads and the registry entry has the expected hook/tag
- The hook fires and produces the intended state change
- Edge cases: missing target, already-dead combatant, zero-hp kill

```python
def test_burning_deals_fire_damage(self):
    state, char_id, npc = self._state_in_rounds()
    apply_condition(state, char_id, "burning", duration=2)
    hp_before = state.characters[char_id].hp_current

    from engine.combat import _tick_conditions
    log: list[str] = []
    _tick_conditions(state, log)

    assert state.characters[char_id].hp_current < hp_before
    assert any("fire" in e for e in log)
```

---

## Available hook points

| Hook name      | When it fires                                      | `actor_id` is    |
|----------------|----------------------------------------------------|-----------------|
| `on_turn_start`| Before the actor's action each round               | the affected combatant |
| `on_turn_end`  | After all actions, during condition tick           | the affected combatant |
| `on_move`      | Inside `move_to_band`, before position changes     | the moving combatant |
| `on_attack`    | When the combatant makes an attack roll *(stub)*   | the attacker |
| `on_hit`       | When a hit lands *(stub)*                          | the attacker |
| `on_take_damage` | When damage is applied *(stub)*                  | the target |
| `on_death`     | When a combatant reaches 0 HP *(stub)*             | the dying combatant |

Stubs are recognised by the loader but have no wiring yet in
`auto_resolve_round`. To activate one, follow the pattern for
`on_turn_start` in `_fire_turn_start_hooks`.

---

## Available hook tags (built-in)

| Tag               | Params                          | What it does                              |
|-------------------|---------------------------------|-------------------------------------------|
| `melee_attack`    | `dice` (str, "1d6")             | Roll attack + str mod vs AC; deal damage  |
| `check_death`     | *(none)*                        | Mark target dead if HP ≤ 0                |
| `deal_damage`     | `dice` (str), `type` (str)      | Deal damage to `actor_id` (condition tick)|
| `apply_condition` | `condition` (str), `duration` (int) | Apply a condition to the action target|
| `move_to_band`    | *(none)*                        | Move actor toward `action.destination`    |
| `block_movement`  | *(none)*                        | Set `movement_blocked` flag (entangle)    |
| `skip_action`     | *(none)*                        | Set `skip_action` flag (stun)             |

---

## Dice expression format

All `dice` params use standard notation parsed by `engine/dice.py`:

| Expression | Meaning                  |
|------------|--------------------------|
| `"1d6"`    | One six-sided die        |
| `"2d8"`    | Two eight-sided dice     |
| `"1d6+2"`  | One d6 plus a flat +2    |
| `"3"`      | Flat value of 3          |

Use `roll_dice_expr(expr)["total"]` to roll a dice string in your handler.

---

## Stat modifiers (no hook needed)

For passive bonuses and penalties — things that modify ability scores
rather than triggering an effect — use `stat_modifiers` in the condition
JSON instead of a hook:

```json
"stat_modifiers": {
  "strength": -2,
  "dexterity": 1
}
```

The engine reads these via `_effective_stat_mod(state, actor_id, "strength")`
whenever an ability score is needed for a calculation.
