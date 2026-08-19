# AGENTS.md

Discord bot for async Azure-ruleset dungeon crawling. Players act via slash commands;
the DM adjudicates via a FastAPI web panel. `CONTRIBUTING.md` is the combat-hook
authoring guide.

## Commands

- `venv/bin/python -m pytest` — full suite (~750 tests, <5s). `pytest.ini` addopts
  already deselect `discord_integration` (needs live Discord + `DISCORD_TEST_CHANNEL_ID`;
  run explicitly with `pytest -s -m discord_integration`).
- `venv/bin/python -m pytest tests/test_equip.py -q` — single file.
- `ruff check .` — lint (config `ruff.toml`; line-length 100, E501 off).
- `python bot.py` — run the bot; requires `.env` (`DISCORD_TOKEN`, `DISCORD_GUILD_ID`).
  Also serves the DM panel on `localhost:8080` in the same event loop.

## Layers

- `engine/` — pure game logic, no Discord imports. All state mutation lives here.
- `cogs/` — discord.py handlers: call engine, format results, update status messages.
  `/turn` and `/round` are in `cogs/slash_commands.py` (there is no `cogs/session.py`);
  cogs are loaded in `bot.py:main()`.
- `models.py` — dataclasses/enums only, no I/O or game logic. `SessionMode`:
  PRE_START → EXPLORATION → ROUNDS.
- `store.py` — in-memory session registry keyed by `channel_id`; lazy-loads from DB.
- `persistence.py` — SQLite (`dungeon.db`), shared by bot and webui.
- `webui/` — FastAPI DM panel; `webui/auth.py` middleware gates every route except
  `PUBLIC_PATHS` (`/login`, `/logout`, `/auth`). scrypt password hash from
  `DM_PANEL_PASSWORD_HASH`, HMAC-SHA256 signed session cookie.
- `data/` — JSON game content (classes, actions, conditions, items, jobskills).

Features typically span models → serialization → engine → cogs/webui → tests; check
all five layers before editing. Function-level imports inside `bot.py` and `engine/`
are deliberate (circular-import avoidance) — follow the pattern.

## Hard constraints (enforced by tests / CI, easy to violate)

- **Never import `discord`, `store`, or `bot` at module level in tests.** discord.py is
  deliberately absent from `requirements-dev.txt`; `conftest.py` skips
  `tests/discord_integration/` when it's missing. Use function-level imports instead.
- **`models.py` must not import `engine.*` at module level** — `engine/__init__.py`
  imports `models`, so a module-level engine import in models is a circular import
  whenever models is loaded first (#188). Registry/class access there
  (`ITEM_REGISTRY`, `CONDITION_REGISTRY`, item types) is function-level, and
  `CharacterClass` is imported directly from `engine.data_loader` by its consumers
  (it is no longer re-exported from models). Guarded by `tests/test_import_order.py`.
- **New `Character` fields must be added in three places in `persistence.py`:**
  `_save_character_sync` INSERT, `_char_dict_from_row`, and a migration in `_migrate()`.
  Missing any one = silent data loss on reload. Add a save→reload→assert test.

## Turn flow / combat

- `/turn` → `TurnManager.submit_turn` (`engine/core.py`) → save session → update the
  channel's pinned status message → DM resolves via the web panel (which calls
  `engine.resolve_turn`; there is no `/dm_resolve` command). Status messages are
  restored on startup (`bot.py:on_ready` → `store.restore_status_message`) — keep the
  stored status message current or status updates break after a restart.
- The pinned status message is an **embed**: `store._build_message` =
  `render_status_header` (message content) + `cogs/status_embed.build_status_embed`.
  Section extraction and budget packing (6000-char total / 1024-per-field, with
  per-field prose caps and explicit `… +N more` overflow markers) live in the
  engine: `render_status_sections` + `pack_sections`. `render_status` is the
  legacy plain-text rendering of the same sections, kept for tests/debug — never
  slice the assembled string to fit limits (that was the old `_build_content` bug).

- **ROUNDS mode:** `submit_turn` auto-resolves the round internally once all players
  submit structured combat actions. A test that calls `submit_turn` for all players
  *and then* `auto_resolve_round` resolves **two** rounds, not one. A free-form
  (Affect) action is skipped by auto-resolution and adjudicated by the DM instead.
- Initiative order is randomized once at `enter_rounds` — pin initiatives explicitly in
  tests that depend on actor ordering.
- Combat effects are data-driven: actions declare `effect_tags`, conditions declare
  `hooks` by lifecycle point. Handlers and `_HOOK_DISPATCH` live in
  **`engine/combat_hooks.py`**, not `engine/combat.py` (`combat.py` re-exports
  `_hook_weapon_attack` / `_tick_conditions` for tests). See `CONTRIBUTING.md`.
- Ability scores are the Azure set — **physique, finesse, reason, savvy** — not D&D stats.

## Items

- `data/items/items.json` → `ITEM_REGISTRY` (`engine/data_loader.py`). Hierarchy:
  `Item → EquipItem → {Weapon → ChargeWeapon, Gear, ContainerItem}`.
- Slot routing via `GEAR_SLOT_MAP` (`engine/azure_constants.py`); rank enforcement in
  `_resolve_target_slot` (`engine/character.py`). Class ranks: `weapon_rank` /
  `armor_rank` are E–A; arcane `spell_rank` is V–Z.
- `ContainerItem` (spellbooks): contained items become their own `InventoryItem`s with
  charges, take no inventory slots, aren't independently equippable, and are purged
  when the container is removed.
- `Room.items` holds `RoomItem` instances with tri-state visibility (`accessible`,
  `inaccessible`, `hidden`). Mutations live in `engine/room.py` and the DM panel;
  serialized inside the dungeon blob with no version bump. Players pick up / drop
  accessible items via the "Items" button on the exploration status message —
  DM views in `cogs/character_views.py`, engine `pick_up_item` / `drop_item`.
  Pickup merges plain stackables into existing stacks (matching `give_item`);
  charge-bearing items, containers, and items carrying charges stay separate.
  Capacity math is shared with `give_item` via `_exceeds_inventory_capacity`
  (`engine/character.py`).
- `data/*.json` is synced from a Google Sheet (ground truth) by
  `scripts/google_sheets_sync.py` (`GOOGLE_API_KEY` + `GOOGLE_SHEET_KEY` env). Sync
  overwrites files matching sheet rows — check with the user before hand-editing
  existing entries.

## Testing conventions

- `create_character` rolls random stats — set `char.ability_scores = AzureStats(...)`
  explicitly in tests that assert check math, or results are flaky.
- Equip tests: rank enforcement blocks rank-B+ items for most test classes (KNIGHT:
  weapon C / armor B; QUESTANT, THIEF: D/D). Pick an item rank the class can reach.
- `tests/test_combat_engine.py` is the reference for hooks/conditions/actions. The
  Azure ruleset is partially implemented and in flux — check with the user before
  changing combat rules.
- Run the full suite before considering a feature done; add a persistence round-trip
  test for every new model field.
