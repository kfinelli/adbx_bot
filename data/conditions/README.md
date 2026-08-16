# Status Conditions

Each `.json` file in this directory defines one status condition.
Conditions are loaded at startup by `engine/data_loader.py` and exposed
via `CONDITION_REGISTRY`.

## Schema

```json
{
  "condition_id":   "burning",
  "label":          "Burning",
  "duration_type":  "rounds",
  "stackable":      false,
  "hooks": {
    "on_turn_end": {"tag": "deal_damage", "dice": "1d6", "type": "fire"}
  },
  "stat_modifiers": {},
  "grants_actions": []
}
```

The file name (stem) must match `condition_id`. `stackable` defaults to
`false`; when `true`, multiple instances stack instead of replacing.

### Hook tags

Only include hooks that do something — there are no `null` placeholders.
Hook values are string tags (or `{"tag": ..., ...params}` objects) dispatched
by `engine/combat_hooks.py:_dispatch_hook()`. `stat_modifiers` is a dict of
`ability_name → integer` using the Azure stats (e.g. `{"physique": -2}`).
`grants_actions` is a list of action IDs from `data/actions/` that are added
to the combatant's available actions while this condition is active.
