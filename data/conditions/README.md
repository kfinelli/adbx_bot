# Status Conditions

Each `.json` file in this directory defines one status condition.
Conditions are loaded at startup by `engine/data_loader.py` and exposed
via `CONDITION_REGISTRY`.

## Schema

```json
{
  "condition_id":   "poisoned",
  "label":          "Poisoned",
  "duration_type":  "rounds",
  "hooks": {
    "on_turn_start":    "deal_1d4_poison_damage",
    "on_turn_end":      null,
    "on_attack":        null,
    "on_hit":           null,
    "on_take_damage":   null,
    "on_death":         null,
    "on_move":          null,
    "stat_modifiers":   {}
  },
  "grants_actions": []
}
```

### Hook tags

Hook values are string tags dispatched by `engine/combat.py:_dispatch_hook()`.
`null` means no effect for that hook.  `stat_modifiers` is a dict of
`ability_name → integer` (e.g. `{"strength": -2}`).  `grants_actions` is a
list of action IDs from `data/actions/` that are added to the combatant's
available actions while this condition is active.

Condition files will be added in Phase 4.
