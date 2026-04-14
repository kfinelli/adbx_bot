"""
scripts/google_sheets_sync.py — Export item data from Google Sheets to data/items/items.json.

Run from the project root:
    python scripts/google_sheets_sync.py > data/items/items.json

Requires GOOGLE_API_KEY and GOOGLE_SHEET_KEY in the environment or .env file.

Normalisation applied here so data/items/items.json is clean native format
and data_loader.py needs no translation layer:
  - is_light / purchaseable: "TRUE"/"FALSE" strings → booleans
  - tags: "[Shabby][Magic]" bracket string → ["Shabby", "Magic"] list
  - slot: title-case sheet value → Slot enum value (e.g. "Arms" → "arms")
  - stat: title-case sheet value → Stat enum value (e.g. "Physique" → "physique")
  - uses: shorthand string → {max_charges, recharge_period} fields,
          then the raw "uses" key is dropped
  - numeric fields (health, defense, resistance, damage, range, price):
    empty string → 0
"""

import json
import os
import sys
from enum import StrEnum

import gspread

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

GOOGLE_API_KEY  = os.getenv("GOOGLE_API_KEY")
GOOGLE_SHEET_KEY = os.getenv("GOOGLE_SHEET_KEY")

if not GOOGLE_API_KEY:
    sys.exit("Error: GOOGLE_API_KEY environment variable not set.")
if not GOOGLE_SHEET_KEY:
    sys.exit("Error: GOOGLE_SHEET_KEY environment variable not set.")


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


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

_SLOT_MAP = {
    "head":      "head",
    "body":      "body",
    "arms":      "arms",
    "legs":      "legs",
    "accessory": "accessory",
    # hand slots — normalise all variants to canonical underscore form
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
    """Convert "TRUE"/"FALSE" sheet strings (or actual bools) to bool."""
    if isinstance(value, bool):
        return value
    return str(value).strip().upper() == "TRUE"


def _int_or_zero(value) -> int:
    """Convert numeric sheet values to int, treating empty/None as 0."""
    if value in ("", None):
        return 0
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return 0

def _float_or_zero(value) -> int:
    """Return float or 0 if empty"""
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
    # strip leading/trailing brackets then split on ']['
    s = s.strip("[]")
    if not s:
        return []
    return [t.strip().title() for t in s.split("][") if t.strip()]

def _parse_contained_items(tags_str: str) -> list[str]:
    """Parse "fulmin1,fulmin2" → ["fulmin1", "fulmin2"]. Empty string → []."""
    s = str(tags_str).strip()
    if not s:
        return []
    # strip leading/trailing brackets then split on ']['
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


def _normalise_item(row: dict) -> dict:
    """
    Convert a raw gspread row dict (new column names) into the clean native
    format expected by data_loader.py / createItemFromData().
    """
    item: dict = {}

    # --- identity ---
    item["item_id"]    = str(row.get("item_id", "")).strip()
    item["item_type"]  = str(row.get("item_type", "item")).strip()
    item["name"]       = str(row.get("name", "")).strip()
    item["description"] = str(row.get("description", "")).strip()
    item["rank"]       = str(row.get("rank", "")).strip()

    # --- booleans ---
    item["is_light"]    = _bool(row.get("is_light", False))
    item["purchaseable"] = _bool(row.get("purchaseable", False))

    # --- price ---
    item["price"] = _int_or_zero(row.get("price", 0))

    # --- tags ---
    item["tags"] = _parse_tags(row.get("tags", ""))

    # --- contained items ---
    if item["item_type"] == "container":
        item["contained_items"] = _parse_contained_items(row.get("contained_items",""))

    # --- text ability fields ---
    item["other_abilities"] = str(row.get("other_abilities", "")).strip()
    item["held_status"]     = str(row.get("held_status", "")).strip()
    item["attack_status"]   = str(row.get("attack_status", "")).strip()

    # --- weapon fields ---
    if item["item_type"] in ("weapon", "charge_weapon"):
        item["type"]   = str(row.get("type", "")).strip()
        item["stat"]   = _STAT_MAP.get(str(row.get("stat", "")).strip().lower(), "physique")
        item["targets_stat"]   = _STAT_MAP.get(str(row.get("targets_stat", "")).strip().lower(), "defense")
        item["damage"] = str(row.get("damage", "0")).strip() or "0"
        item["range"]  = _int_or_zero(row.get("range", 0))
        # Slot for weapons: canonical underscore form, default main_hand.
        raw_slot = str(row.get("slot", "")).strip().lower()
        item["slot"] = _SLOT_MAP.get(raw_slot, raw_slot) if raw_slot else "main_hand"

    # --- charge weapon fields ---
    if item["item_type"] == "charge_weapon":
        max_charges, recharge_period = _parse_uses(row.get("uses", "-"))
        item["max_charges"]     = max_charges
        item["charges"]         = max_charges
        item["recharge_period"] = recharge_period
        item["destroy_on_empty"] = False

    # --- utility spell fields ---
    if item["item_type"] == "utility_spell":
        max_charges, recharge_period = _parse_uses(row.get("uses", "-"))
        item["max_charges"]     = max_charges
        item["charges"]         = max_charges
        item["recharge_period"] = recharge_period

    # --- gear fields ---
    if item["item_type"] == "gear":
        raw_slot = str(row.get("slot", "")).strip().lower()
        item["slot"]       = _SLOT_MAP.get(raw_slot, raw_slot)
        item["health"]     = _float_or_zero(row.get("health", 0))
        item["defense"]    = _float_or_zero(row.get("defense", 0))
        item["resistance"] = _float_or_zero(row.get("resistance", 0))

    # --- container fields ---
    if item["item_type"] == "container":
        raw_slot = str(row.get("slot", "")).strip().lower()
        if raw_slot:
            item["slot"] = _SLOT_MAP.get(raw_slot, raw_slot)
        # If no slot specified, omit the key (container is inventory-only).

    return item


# ---------------------------------------------------------------------------
# Sheet fetcher
# ---------------------------------------------------------------------------

def _get_items_from_sheet(sheet) -> list[dict]:
    rows = sheet.get_all_records()
    return [
        _normalise_item(row)
        for row in rows
        if row.get("name", "")
    ]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    gc     = gspread.api_key(GOOGLE_API_KEY)
    book   = gc.open_by_key(GOOGLE_SHEET_KEY)

    item_data = {}
    for tab in ItemSheet:
        worksheet = book.worksheet(tab)
        item_data[tab.value] = _get_items_from_sheet(worksheet)

    print(json.dumps(item_data, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
