"""
scripts/google_sheets_sync.py — Sync game data between Google Sheets and data/*.json files.

Usage:
    # Sync all entity types from Google Sheets → JSON files (writes directly to data/)
    python scripts/google_sheets_sync.py

    # Export existing JSON files → CSV files in exports/ (for one-time sheet seeding)
    python scripts/google_sheets_sync.py --export-csv

Requires GOOGLE_API_KEY and GOOGLE_SHEET_KEY in the environment or .env file
(not needed for --export-csv).

Normalisation applied so data/*.json files are clean native format
and data_loader.py needs no translation layer:
  - is_light / purchaseable: "TRUE"/"FALSE" strings → booleans
  - tags: "[Shabby][Magic]" bracket string → ["Shabby", "Magic"] list
  - slot: title-case sheet value → slot enum value (e.g. "Arms" → "arms")
  - stat: title-case sheet value → stat enum value (e.g. "Physique" → "physique")
  - uses: shorthand string → {max_charges, recharge_period} fields
  - numeric fields (health, defense, resistance, damage, range, price):
    empty string → 0
  - effect_tags / hook columns: JSON string in cell → parsed structure
"""

import argparse
import csv
import json
import os
import sys
from enum import StrEnum
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_SCRIPT_DIR = Path(__file__).parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
_DATA_DIR = _PROJECT_ROOT / "data"
_EXPORTS_DIR = _PROJECT_ROOT / "exports"

# ---------------------------------------------------------------------------
# Sheet tab names
# ---------------------------------------------------------------------------

class ItemSheet(StrEnum):
    WEAPON    = "Weapon"
    MAGIC     = "Magic"
    HEAD      = "Head"
    BODY      = "Body"
    ARMS      = "Arms"
    LEGS      = "Legs"
    ACCESSORY = "Offhand/Accessory"
    CASTING   = "Casting"

ENTITY_SHEET_NAMES = {
    "actions":    "Actions",
    "conditions": "Conditions",
    "jobs":       "Jobs",
    "skills":     "Skills",
}

# Hook point columns used in conditions
_HOOK_COLUMNS = [
    "on_turn_start",
    "on_turn_end",
    "on_attack",
    "on_hit",
    "on_take_damage",
    "on_death",
    "on_move",
]

# ---------------------------------------------------------------------------
# Normalisation helpers — shared
# ---------------------------------------------------------------------------

_SLOT_MAP = {
    "head":      "head",
    "body":      "body",
    "arms":      "arms",
    "legs":      "legs",
    "accessory": "accessory",
    "main_hand": "main_hand",
    "main":      "main_hand",
    "mainhand":  "main_hand",
    "off_hand":  "off_hand",
    "offhand":   "off_hand",
}

_STAT_MAP = {
    "physique":   "physique",
    "finesse":    "finesse",
    "reason":     "reason",
    "savvy":      "savvy",
    "defense":    "defense",
    "resistance": "resistance",
}


def _bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().upper() == "TRUE"


def _int_or_zero(value) -> int:
    if value in ("", None):
        return 0
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return 0


def _float_or_zero(value) -> float:
    if value in ("", None):
        return 0
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0


def _parse_tags(tags_str: str) -> list[str]:
    """Parse "[Shabby][Magic]" → ["Shabby", "Magic"]. Empty string → []."""
    s = str(tags_str).strip()
    if not s:
        return []
    s = s.strip("[]")
    if not s:
        return []
    return [t.strip().title() for t in s.split("][") if t.strip()]


def _parse_contained_items(tags_str: str) -> list[str]:
    """Parse "fulmin1,fulmin2" → ["fulmin1", "fulmin2"]. Empty string → []."""
    s = str(tags_str).strip()
    if not s:
        return []
    return [t.strip() for t in s.split(",") if t.strip()]


def _parse_uses(uses_str: str) -> tuple[int, str]:
    """
    Parse uses shorthand into (max_charges, recharge_period).
      "-"   → (-1, "infinite")
      "3/d" → (3,  "day")
      "2/e" → (2,  "encounter")
      "3"   → (3,  "never")
    """
    s = str(uses_str).strip()
    if not s or s == "-":
        return -1, "infinite"
    if "/" in s:
        parts = s.split("/", 1)
        count = int(parts[0])
        period_char = parts[1].lower()
        period = {"d": "day", "e": "encounter"}.get(period_char, "never")
        return count, period
    try:
        return int(s), "never"
    except ValueError:
        return -1, "infinite"


def _parse_json_cell(value: str, field: str, row_id: str):
    """
    Parse a JSON string from a spreadsheet cell.
    Returns the parsed value, or None if the cell is empty.
    Prints a warning and returns None on parse error.
    """
    s = str(value).strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except json.JSONDecodeError as exc:
        print(f"  WARNING: {row_id} — invalid JSON in '{field}': {exc}", file=sys.stderr)
        return None


def _parse_hook_cell(value: str, field: str, row_id: str):
    """
    Parse a hook point cell. Returns:
      - None if empty (no hook at this lifecycle point)
      - str if plain tag name (e.g. "skip_action")
      - dict if JSON object (e.g. {"tag": "deal_damage", "dice": "1d4"})
    """
    s = str(value).strip()
    if not s:
        return None
    if s.startswith("{"):
        return _parse_json_cell(s, field, row_id)
    return s


def _compact_json_dumps(obj, indent: int = 2, _depth: int = 0) -> str:
    """
    Serialize to JSON with indentation, but keep dicts/lists whose compact
    representation fits within 80 chars on a single line. Produces stable
    output across import/export roundtrips for objects like stacks_by_level
    and stat_modifiers that are naturally expressed as one-liners.
    """
    COMPACT_MAX = 80
    pad = " " * (indent * _depth)
    inner = " " * (indent * (_depth + 1))

    if isinstance(obj, dict):
        if not obj:
            return "{}"
        compact = json.dumps(obj)
        if len(compact) <= COMPACT_MAX:
            return compact
        pairs = [
            f"{inner}{json.dumps(k)}: {_compact_json_dumps(v, indent, _depth + 1)}"
            for k, v in obj.items()
        ]
        return "{\n" + ",\n".join(pairs) + "\n" + pad + "}"

    if isinstance(obj, list):
        if not obj:
            return "[]"
        compact = json.dumps(obj)
        if len(compact) <= COMPACT_MAX:
            return compact
        items = [f"{inner}{_compact_json_dumps(v, indent, _depth + 1)}" for v in obj]
        return "[\n" + ",\n".join(items) + "\n" + pad + "]"

    return json.dumps(obj)


# ---------------------------------------------------------------------------
# Normalisation — items
# ---------------------------------------------------------------------------

def _normalise_item(row: dict) -> dict:
    item: dict = {}
    item["item_id"]      = str(row.get("item_id", "")).strip()
    item["item_type"]    = str(row.get("item_type", "item")).strip()
    item["name"]         = str(row.get("name", "")).strip()
    item["description"]  = str(row.get("description", "")).strip()
    item["rank"]         = str(row.get("rank", "")).strip()
    item["is_light"]     = _bool(row.get("is_light", False))
    item["purchaseable"] = _bool(row.get("purchaseable", False))
    item["price"]        = _int_or_zero(row.get("price", 0))
    item["tags"]         = _parse_tags(row.get("tags", ""))
    if item["item_type"] == "container":
        item["contained_items"] = _parse_contained_items(row.get("contained_items", ""))
    item["other_abilities"] = str(row.get("other_abilities", "")).strip()
    item["held_status"]     = str(row.get("held_status", "")).strip()
    item["attack_status"]   = str(row.get("attack_status", "")).strip()
    if item["item_type"] in ("weapon", "charge_weapon"):
        item["type"]        = str(row.get("type", "")).strip()
        item["stat"]        = _STAT_MAP.get(str(row.get("stat", "")).strip().lower(), "physique")
        item["targets_stat"] = _STAT_MAP.get(str(row.get("targets_stat", "")).strip().lower(), "defense")
        item["damage"]      = str(row.get("damage", "0")).strip() or "0"
        item["range"]       = _int_or_zero(row.get("range", 0))
        raw_slot = str(row.get("slot", "")).strip().lower()
        item["slot"] = _SLOT_MAP.get(raw_slot, raw_slot) if raw_slot else "main_hand"
    if item["item_type"] == "charge_weapon":
        max_charges, recharge_period = _parse_uses(row.get("uses", "-"))
        item["max_charges"]      = max_charges
        item["charges"]          = max_charges
        item["recharge_period"]  = recharge_period
        item["destroy_on_empty"] = False
    if item["item_type"] == "utility_spell":
        max_charges, recharge_period = _parse_uses(row.get("uses", "-"))
        item["max_charges"]     = max_charges
        item["charges"]         = max_charges
        item["recharge_period"] = recharge_period
    if item["item_type"] == "gear":
        raw_slot = str(row.get("slot", "")).strip().lower()
        item["slot"]       = _SLOT_MAP.get(raw_slot, raw_slot)
        item["health"]     = _int_or_zero(row.get("health", 0))
        item["defense"]    = _int_or_zero(row.get("defense", 0))
        item["resistance"] = _int_or_zero(row.get("resistance", 0))
    if item["item_type"] == "container":
        raw_slot = str(row.get("slot", "")).strip().lower()
        if raw_slot:
            item["slot"] = _SLOT_MAP.get(raw_slot, raw_slot)
    max_light_raw = str(row.get("max_light_turns", "")).strip()
    if max_light_raw:
        item["max_light_turns"] = _int_or_zero(max_light_raw)
    fuel_item_raw = str(row.get("fuel_item_id", "")).strip()
    if fuel_item_raw:
        item["fuel_item_id"] = fuel_item_raw
    return item


# ---------------------------------------------------------------------------
# Normalisation — actions
# ---------------------------------------------------------------------------

def _normalise_action(row: dict) -> dict:
    action_id = str(row.get("action_id", "")).strip()
    action: dict = {
        "action_id":            action_id,
        "label":                str(row.get("label", "")).strip(),
        "button_style":         str(row.get("button_style", "secondary")).strip(),
        "action_type":          str(row.get("action_type", "combat")).strip(),
        "description":          str(row.get("description", "")).strip(),
        "requires_target":      str(row.get("requires_target", "none")).strip(),
        "requires_destination": _bool(row.get("requires_destination", False)),
    }
    range_raw = str(row.get("range_requirement", "")).strip()
    if range_raw == "" or range_raw.lower() == "null":
        action["range_requirement"] = None
    elif range_raw.lower() == "weapon":
        action["range_requirement"] = "weapon"
    else:
        action["range_requirement"] = _int_or_zero(range_raw)

    for cons_field in ("consumes_act", "consumes_move", "consumes_oracle"):
        raw = str(row.get(cons_field, "")).strip()
        if raw:
            action[cons_field] = _bool(raw)

    effect_tags = _parse_json_cell(row.get("effect_tags", ""), "effect_tags", action_id)
    action["effect_tags"] = effect_tags if effect_tags is not None else []
    return action


# ---------------------------------------------------------------------------
# Normalisation — conditions
# ---------------------------------------------------------------------------

def _normalise_condition(row: dict) -> dict:
    condition_id = str(row.get("condition_id", "")).strip()
    condition: dict = {
        "condition_id": condition_id,
        "label":        str(row.get("label", "")).strip(),
        "duration_type": str(row.get("duration_type", "rounds")).strip(),
    }

    stackable_raw = str(row.get("stackable", "")).strip()
    if stackable_raw:
        condition["stackable"] = _bool(stackable_raw)

    hooks: dict = {}
    for hook_col in _HOOK_COLUMNS:
        value = _parse_hook_cell(row.get(hook_col, ""), hook_col, condition_id)
        if value is not None:
            hooks[hook_col] = value
    condition["hooks"] = hooks

    stat_mods = _parse_json_cell(row.get("stat_modifiers", ""), "stat_modifiers", condition_id)
    condition["stat_modifiers"] = stat_mods if isinstance(stat_mods, dict) else {}

    grants = _parse_json_cell(row.get("grants_actions", ""), "grants_actions", condition_id)
    condition["grants_actions"] = grants if isinstance(grants, list) else []

    tags = _parse_json_cell(row.get("tags", ""), "tags", condition_id)
    if isinstance(tags, list) and tags:
        condition["tags"] = tags

    return condition


# ---------------------------------------------------------------------------
# Normalisation — jobs
# ---------------------------------------------------------------------------

def _normalise_job(row: dict) -> dict:
    key = str(row.get("key", "")).strip().upper()
    job: dict = {
        "key":          key,
        "display_name": str(row.get("display_name", "")).strip(),
        "hit_die":      str(row.get("hit_die", "1d8")).strip(),
        "base_save":    _int_or_zero(row.get("base_save", 2)),
        "primary_stat": str(row.get("primary_stat", "")).strip(),
        "max_level":    _int_or_zero(row.get("max_level", 5)),
        "description":  str(row.get("description", "")).strip(),
    }

    stat_rolls = _parse_json_cell(row.get("stat_rolls", ""), "stat_rolls", key)
    if isinstance(stat_rolls, dict):
        job["stat_rolls"] = stat_rolls

    skills_raw = str(row.get("skills", "")).strip()
    skills = []
    for line in skills_raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if ":" in line:
            skill_id, _, level_str = line.partition(":")
            skills.append({"id": skill_id.strip(), "level": _int_or_zero(level_str.strip())})
        else:
            print(f"  WARNING: {key} — unrecognised skill grant line: {line!r}", file=sys.stderr)
    job["skills"] = skills

    return job


# ---------------------------------------------------------------------------
# Normalisation — skills
# ---------------------------------------------------------------------------

def _normalise_skill(row: dict, skill_id: str) -> dict:
    skill: dict = {
        "name": str(row.get("name", "")).strip(),
        "type": _int_or_zero(row.get("type", 0)),
    }

    for opt_str in ("desc", "dm_notes", "action_id", "stat", "rank"):
        val = str(row.get(opt_str, "")).strip()
        if val:
            skill[opt_str] = val

    bonus_raw = str(row.get("bonus", "")).strip()
    if bonus_raw:
        skill["bonus"] = _int_or_zero(bonus_raw)

    uses_raw = str(row.get("uses", "")).strip()
    if uses_raw:
        skill["uses"] = _int_or_zero(uses_raw)

    uses_scaling_raw = str(row.get("uses_scaling", "")).strip()
    if uses_scaling_raw:
        parsed = _parse_json_cell(uses_scaling_raw, "uses_scaling", skill_id)
        if isinstance(parsed, list):
            skill["uses_scaling"] = parsed

    recharge_period_raw = str(row.get("recharge_period", "")).strip()
    if recharge_period_raw:
        skill["recharge_period"] = recharge_period_raw

    check = _parse_json_cell(row.get("check", ""), "check", skill_id)
    if isinstance(check, dict):
        skill["check"] = check

    return skill


# ---------------------------------------------------------------------------
# Sheet fetchers
# ---------------------------------------------------------------------------

def _get_items_from_sheet(sheet) -> list[dict]:
    rows = sheet.get_all_records()
    return [
        _normalise_item(row)
        for row in rows
        if row.get("name", "")
    ]


def _get_actions_from_sheet(sheet) -> list[dict]:
    rows = sheet.get_all_records()
    return [
        _normalise_action(row)
        for row in rows
        if row.get("action_id", "")
    ]


def _get_conditions_from_sheet(sheet) -> list[dict]:
    rows = sheet.get_all_records()
    return [
        _normalise_condition(row)
        for row in rows
        if row.get("condition_id", "")
    ]


def _get_jobs_from_sheet(sheet) -> list[dict]:
    rows = sheet.get_all_records()
    return [
        _normalise_job(row)
        for row in rows
        if row.get("key", "")
    ]


def _get_skills_from_sheet(sheet) -> dict:
    """Returns {"definitions": {skill_id: {...}, ...}}"""
    rows = sheet.get_all_records()
    definitions = {}
    for row in rows:
        skill_id = str(row.get("skill_id", "")).strip()
        if not skill_id:
            continue
        definitions[skill_id] = _normalise_skill(row, skill_id)
    return {"definitions": definitions}


# ---------------------------------------------------------------------------
# Sync — write JSON files
# ---------------------------------------------------------------------------

def _sync_items(book, data_dir: Path) -> None:
    item_data = {}
    for tab in ItemSheet:
        worksheet = book.worksheet(tab)
        item_data[tab.value] = _get_items_from_sheet(worksheet)
        print(f"  items/{tab.value}: {len(item_data[tab.value])} items", file=sys.stderr)

    out_path = data_dir / "items" / "items.json"
    out_path.write_text(json.dumps(item_data, indent=2, sort_keys=True) + "\n")
    print(f"  Written: {out_path.relative_to(_PROJECT_ROOT)}", file=sys.stderr)


def _sync_actions(book, data_dir: Path) -> None:
    sheet = book.worksheet(ENTITY_SHEET_NAMES["actions"])
    actions = _get_actions_from_sheet(sheet)
    print(f"  actions: {len(actions)} rows", file=sys.stderr)

    actions_dir = data_dir / "actions"
    existing = {p.stem for p in actions_dir.glob("*.json")}
    sheet_ids = {a["action_id"] for a in actions}

    for stale in existing - sheet_ids:
        print(
            f"  WARNING: data/actions/{stale}.json has no matching row in the sheet — not deleted",
            file=sys.stderr,
        )

    for action in actions:
        out_path = actions_dir / f"{action['action_id']}.json"
        out_path.write_text(_compact_json_dumps(action) + "\n")
    print(f"  Written: {len(actions)} action files", file=sys.stderr)


def _sync_conditions(book, data_dir: Path) -> None:
    sheet = book.worksheet(ENTITY_SHEET_NAMES["conditions"])
    conditions = _get_conditions_from_sheet(sheet)
    print(f"  conditions: {len(conditions)} rows", file=sys.stderr)

    conditions_dir = data_dir / "conditions"
    existing = {p.stem for p in conditions_dir.glob("*.json")}
    sheet_ids = {c["condition_id"] for c in conditions}

    for stale in existing - sheet_ids:
        print(
            f"  WARNING: data/conditions/{stale}.json has no matching row in the sheet — not deleted",
            file=sys.stderr,
        )

    for condition in conditions:
        out_path = conditions_dir / f"{condition['condition_id']}.json"
        out_path.write_text(_compact_json_dumps(condition) + "\n")
    print(f"  Written: {len(conditions)} condition files", file=sys.stderr)


def _sync_jobs(book, data_dir: Path) -> None:
    sheet = book.worksheet(ENTITY_SHEET_NAMES["jobs"])
    jobs = _get_jobs_from_sheet(sheet)
    print(f"  jobs: {len(jobs)} rows", file=sys.stderr)

    classes_dir = data_dir / "classes"
    existing = {p.stem.upper() for p in classes_dir.glob("*.json")}
    sheet_keys = {j["key"] for j in jobs}

    for stale in existing - sheet_keys:
        print(
            f"  WARNING: data/classes/{stale.lower()}.json has no matching row in the sheet — not deleted",
            file=sys.stderr,
        )

    for job in jobs:
        out_path = classes_dir / f"{job['key'].lower()}.json"
        out_path.write_text(_compact_json_dumps(job) + "\n")
    print(f"  Written: {len(jobs)} class files", file=sys.stderr)


def _sync_skills(book, data_dir: Path) -> None:
    sheet = book.worksheet(ENTITY_SHEET_NAMES["skills"])
    skills_data = _get_skills_from_sheet(sheet)
    count = len(skills_data["definitions"])
    print(f"  skills: {count} definitions", file=sys.stderr)

    out_path = data_dir / "jobskills" / "skills.json"
    out_path.write_text(_compact_json_dumps(skills_data) + "\n")
    print(f"  Written: {out_path.relative_to(_PROJECT_ROOT)}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CSV export — JSON → CSV (for one-time sheet seeding)
# ---------------------------------------------------------------------------

def _hook_to_cell(hook) -> str:
    """Serialize a hook entry to a spreadsheet cell value."""
    if hook is None:
        return ""
    if isinstance(hook, str):
        return hook
    return json.dumps(hook)


def _uses_to_str(max_charges: int, recharge_period: str) -> str:
    """Reverse of _parse_uses: (max_charges, recharge_period) → shorthand string."""
    if max_charges == -1 or recharge_period == "infinite":
        return "-"
    period_char = {"day": "d", "encounter": "e"}.get(recharge_period)
    if period_char:
        return f"{max_charges}/{period_char}"
    return str(max_charges)


def _tags_to_str(tags: list) -> str:
    """Reverse of _parse_tags: ["Shabby", "Magic"] → "[Shabby][Magic]"."""
    if not tags:
        return ""
    return "".join(f"[{t}]" for t in tags)


def _contained_items_to_str(items: list) -> str:
    """Reverse of _parse_contained_items: ["a", "b"] → "a,b"."""
    return ",".join(items)


# Preferred column order for item CSVs (fields absent from a sheet are omitted).
_ITEM_FIELD_ORDER = [
    "item_id", "item_type", "name", "description", "rank",
    "is_light", "purchaseable", "price", "tags",
    "type", "stat", "targets_stat", "damage", "range", "slot",
    "uses", "destroy_on_empty",
    "health", "defense", "resistance",
    "contained_items",
    "fuel_item_id", "max_light_turns",
    "other_abilities", "held_status", "attack_status",
]

_ITEM_BOOL_FIELDS = {"is_light", "purchaseable", "destroy_on_empty"}
_ITEM_INT_FIELDS = {"health", "defense", "resistance"}


def _item_to_csv_row(item: dict) -> dict:
    """Reverse-normalise a stored item dict to a spreadsheet row dict."""
    row = {}
    for key, value in item.items():
        if key in ("max_charges", "charges", "recharge_period"):
            continue  # folded into "uses"
        if key == "tags":
            row["tags"] = _tags_to_str(value)
        elif key == "contained_items":
            row["contained_items"] = _contained_items_to_str(value)
        elif key in _ITEM_BOOL_FIELDS:
            row[key] = "TRUE" if value else "FALSE"
        elif key in _ITEM_INT_FIELDS:
            row[key] = int(value) if isinstance(value, float) else value
        else:
            row[key] = value

    if "max_charges" in item:
        row["uses"] = _uses_to_str(item["max_charges"], item.get("recharge_period", "infinite"))

    return row


def _export_items_csv(data_dir: Path, exports_dir: Path) -> None:
    items_path = data_dir / "items" / "items.json"
    all_items: dict = json.loads(items_path.read_text())

    for tab in ItemSheet:
        items = all_items.get(tab.value, [])
        if not items:
            print(f"  items/{tab.value}: no items, skipping", file=sys.stderr)
            continue

        rows = [_item_to_csv_row(item) for item in items]

        # Build ordered fieldnames: preferred order first, then any extras.
        present = set().union(*(r.keys() for r in rows))
        fieldnames = [f for f in _ITEM_FIELD_ORDER if f in present]
        fieldnames += sorted(present - set(fieldnames))

        safe_name = tab.value.lower().replace("/", "_").replace(" ", "_")
        out = exports_dir / f"items_{safe_name}.csv"
        with out.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        print(f"  Written: exports/items_{safe_name}.csv ({len(rows)} rows)", file=sys.stderr)


def _export_actions_csv(data_dir: Path, exports_dir: Path) -> None:
    actions_dir = data_dir / "actions"
    fieldnames = [
        "action_id", "label", "button_style", "action_type", "description",
        "requires_target", "requires_destination", "range_requirement",
        "consumes_act", "consumes_move", "consumes_oracle", "effect_tags",
    ]
    rows = []
    for p in sorted(actions_dir.glob("*.json")):
        data = json.loads(p.read_text())
        row = {
            "action_id":            data.get("action_id", ""),
            "label":                data.get("label", ""),
            "button_style":         data.get("button_style", ""),
            "action_type":          data.get("action_type", ""),
            "description":          data.get("description", ""),
            "requires_target":      data.get("requires_target", ""),
            "requires_destination": data.get("requires_destination", False),
            "range_requirement":    "" if data.get("range_requirement") is None else data["range_requirement"],
            "effect_tags":          json.dumps(data.get("effect_tags", [])),
        }
        for cons_field in ("consumes_act", "consumes_move", "consumes_oracle"):
            if cons_field in data:
                row[cons_field] = "TRUE" if data[cons_field] else "FALSE"
            else:
                row[cons_field] = ""
        rows.append(row)
    out = exports_dir / "actions.csv"
    with out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  Written: exports/actions.csv ({len(rows)} rows)", file=sys.stderr)


def _export_conditions_csv(data_dir: Path, exports_dir: Path) -> None:
    conditions_dir = data_dir / "conditions"
    fieldnames = [
        "condition_id", "label", "duration_type",
        *_HOOK_COLUMNS,
        "stat_modifiers", "grants_actions", "stackable", "tags",
    ]
    rows = []
    for p in sorted(conditions_dir.glob("*.json")):
        data = json.loads(p.read_text())
        hooks = data.get("hooks", {})
        row = {
            "condition_id": data.get("condition_id", ""),
            "label":        data.get("label", ""),
            "duration_type": data.get("duration_type", "rounds"),
            "stat_modifiers": json.dumps(data.get("stat_modifiers", {})),
            "grants_actions": json.dumps(data.get("grants_actions", [])),
            "stackable":    data.get("stackable", False),
            "tags":         json.dumps(data.get("tags", [])) if data.get("tags") else "",
        }
        for hook_col in _HOOK_COLUMNS:
            row[hook_col] = _hook_to_cell(hooks.get(hook_col))
        rows.append(row)
    out = exports_dir / "conditions.csv"
    with out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  Written: exports/conditions.csv ({len(rows)} rows)", file=sys.stderr)


def _export_jobs_csv(data_dir: Path, exports_dir: Path) -> None:
    classes_dir = data_dir / "classes"
    fieldnames = [
        "key", "display_name", "hit_die", "base_save", "primary_stat",
        "stat_rolls", "max_level", "description", "skills",
    ]
    rows = []
    for p in sorted(classes_dir.glob("*.json")):
        data = json.loads(p.read_text())
        rows.append({
            "key":          data.get("key", ""),
            "display_name": data.get("display_name", ""),
            "hit_die":      data.get("hit_die", ""),
            "base_save":    data.get("base_save", ""),
            "primary_stat": data.get("primary_stat", ""),
            "stat_rolls":   json.dumps(data.get("stat_rolls", {})),
            "max_level":    data.get("max_level", ""),
            "description":  data.get("description", ""),
            "skills":       "\n".join(
                f"{s['id']}:{s['level']}" for s in data.get("skills", [])
            ),
        })
    out = exports_dir / "jobs.csv"
    with out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  Written: exports/jobs.csv ({len(rows)} rows)", file=sys.stderr)


def _export_skills_csv(data_dir: Path, exports_dir: Path) -> None:
    skills_path = data_dir / "jobskills" / "skills.json"
    definitions = json.loads(skills_path.read_text()).get("definitions", {})
    fieldnames = [
        "skill_id", "name", "type", "desc", "dm_notes",
        "action_id", "stat", "bonus", "rank", "uses", "uses_scaling",
        "recharge_period", "check",
    ]
    rows = []
    for skill_id, skill in definitions.items():
        rows.append({
            "skill_id":       skill_id,
            "name":           skill.get("name", ""),
            "type":           skill.get("type", ""),
            "desc":           skill.get("desc", ""),
            "dm_notes":       skill.get("dm_notes", ""),
            "action_id":      skill.get("action_id", ""),
            "stat":           skill.get("stat", ""),
            "bonus":          skill.get("bonus", ""),
            "rank":           skill.get("rank", ""),
            "uses":           skill.get("uses", ""),
            "uses_scaling":   json.dumps(skill["uses_scaling"]) if skill.get("uses_scaling") else "",
            "recharge_period": skill.get("recharge_period", ""),
            "check":          json.dumps(skill["check"]) if skill.get("check") else "",
        })
    out = exports_dir / "skills.csv"
    with out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  Written: exports/skills.csv ({len(rows)} rows)", file=sys.stderr)


def export_csv(data_dir: Path, exports_dir: Path) -> None:
    exports_dir.mkdir(exist_ok=True)
    print("Exporting JSON → CSV files...", file=sys.stderr)
    _export_items_csv(data_dir, exports_dir)
    _export_actions_csv(data_dir, exports_dir)
    _export_conditions_csv(data_dir, exports_dir)
    _export_jobs_csv(data_dir, exports_dir)
    _export_skills_csv(data_dir, exports_dir)
    print("Done. Import the CSV files into your Google Sheet as new tabs.", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync game data between Google Sheets and data/*.json files."
    )
    parser.add_argument(
        "--export-csv",
        action="store_true",
        help="Export existing JSON files to exports/*.csv for one-time sheet seeding.",
    )
    args = parser.parse_args()

    if args.export_csv:
        export_csv(_DATA_DIR, _EXPORTS_DIR)
        return

    # Sheet → JSON sync (requires API credentials)
    google_api_key  = os.getenv("GOOGLE_API_KEY")
    google_sheet_key = os.getenv("GOOGLE_SHEET_KEY")
    if not google_api_key:
        sys.exit("Error: GOOGLE_API_KEY environment variable not set.")
    if not google_sheet_key:
        sys.exit("Error: GOOGLE_SHEET_KEY environment variable not set.")

    import gspread
    gc   = gspread.api_key(google_api_key)
    book = gc.open_by_key(google_sheet_key)

    print("Syncing Google Sheets → JSON files...", file=sys.stderr)
    _sync_items(book, _DATA_DIR)
    _sync_actions(book, _DATA_DIR)
    _sync_conditions(book, _DATA_DIR)
    _sync_jobs(book, _DATA_DIR)
    _sync_skills(book, _DATA_DIR)
    print("Done.", file=sys.stderr)


if __name__ == "__main__":
    main()
