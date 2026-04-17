#!/usr/bin/env python3
"""
Generate player-facing equipment tables from items.json.

Writes scripts/equipment_table.html — open in a browser, select a table,
copy, and paste into Google Docs to preserve table formatting.

Usage:
    python scripts/equipment_table.py
"""

import json
import re
from pathlib import Path

DATA_PATH = Path(__file__).parent.parent / "data" / "items" / "items.json"
OUT_PATH = Path(__file__).parent.parent / "exports"
OUT_FILE = OUT_PATH / "equipment_table.html"

RANK_ORDER = {r: i for i, r in enumerate("EDCBAS") }
# Arcane ranks come after physical
RANK_ORDER.update({r: i + 6 for i, r in enumerate("VWXYZ")})


def rank_sort_key(item):
    rank = item.get("rank", "")
    return (RANK_ORDER.get(rank, 99), item.get("name", ""))


# ---------------------------------------------------------------------------
# Dice math
# ---------------------------------------------------------------------------

def parse_damage_expr(expr: str) -> list[tuple[int, int]]:
    """Parse 'XdY+AdB+...' into [(count, sides), ...]. Ignores flat bonuses."""
    terms = []
    for part in re.split(r"\+", expr):
        part = part.strip()
        m = re.fullmatch(r"(\d+)d(\d+)", part)
        if m:
            terms.append((int(m.group(1)), int(m.group(2))))
        # flat bonuses (no 'd') are intentionally ignored for mean/variance
    return terms


def damage_stats(expr: str) -> tuple[int, str]:
    """Return (mean_rounded, variance_label) for a damage expression."""
    terms = parse_damage_expr(expr)
    if not terms:
        return (0, "—")
    mean = sum(count * (sides + 1) / 2 for count, sides in terms)
    total_dice = sum(count for count, _ in terms)
    if total_dice >= 5:
        label = "precise"
    elif total_dice >= 3:
        label = "balanced"
    else:
        label = "random"
    return (round(mean), label)


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def esc(value) -> str:
    """HTML-escape a value for safe insertion."""
    s = str(value) if value is not None else ""
    return (s
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))


def th(text: str) -> str:
    return f"<th>{esc(text)}</th>"


def td(text, cls: str = "") -> str:
    attr = f' class="{cls}"' if cls else ""
    return f"<td{attr}>{esc(text)}</td>"


def build_table(headers: list[str], rows: list[list]) -> str:
    head = "".join(th(h) for h in headers)
    body_rows = []
    for row in rows:
        cells = "".join(td(cell) for cell in row)
        body_rows.append(f"    <tr>{cells}</tr>")
    body = "\n".join(body_rows)
    return (
        f'<table>\n'
        f'  <thead><tr>{head}</tr></thead>\n'
        f'  <tbody>\n{body}\n  </tbody>\n'
        f'</table>'
    )


# ---------------------------------------------------------------------------
# Per-type table builders
# ---------------------------------------------------------------------------

def weapons_table(items: list[dict]) -> str:
    items = sorted(items, key=rank_sort_key)
    headers = ["Name", "Type", "Rank", "Price", "Mean Damage", "Variance", "Stat", "Range", "Tags"]
    rows = []
    for w in items:
        mean, label = damage_stats(w.get("damage", ""))
        range_val = w.get("range", 0)
        range_str = "Melee" if range_val == 0 else f"{range_val} bands"
        tags = ", ".join(w.get("tags", [])) or "—"
        rows.append([
            w.get("name", ""),
            w.get("type", ""),
            w.get("rank", ""),
            w.get("price", ""),
            mean,
            label,
            w.get("stat", "").capitalize(),
            range_str,
            tags,
        ])
    return build_table(headers, rows)


def gear_table(items: list[dict]) -> str:
    items = sorted(items, key=rank_sort_key)
    headers = ["Name", "Slot", "Rank", "Price", "Defense", "Resistance", "HP Bonus", "Tags"]
    rows = []
    for g in items:
        slot = g.get("slot", "").replace("_", " ").capitalize()
        tags = ", ".join(g.get("tags", [])) or "—"
        rows.append([
            g.get("name", ""),
            slot,
            g.get("rank", ""),
            g.get("price", ""),
            int(g.get("defense", 0)),
            int(g.get("resistance", 0)),
            int(g.get("health", 0)),
            tags,
        ])
    return build_table(headers, rows)


def containers_table(items: list[dict]) -> str:
    items = sorted(items, key=rank_sort_key)
    headers = ["Name", "Rank", "Price", "Contained Spells"]
    rows = []
    for c in items:
        spells = ", ".join(c.get("contained_items", [])) or "—"
        rows.append([
            c.get("name", ""),
            c.get("rank", ""),
            c.get("price", ""),
            spells,
        ])
    return build_table(headers, rows)


def spells_table(items: list[dict]) -> str:
    items = sorted(items, key=rank_sort_key)
    headers = ["Name", "Type", "Rank", "Charges", "Recharge", "Mean Damage", "Variance", "Targets", "Range", "Tags"]
    rows = []
    for s in items:
        mean, label = damage_stats(s.get("damage", ""))
        charges = s.get("max_charges", "")
        if charges == -1:
            charges = "∞"
        recharge = s.get("recharge_period", "—") or "—"
        range_val = s.get("range", 0)
        range_str = "Melee" if range_val == 0 else ("1 band" if range_val == 1 else f"{range_val} bands")
        tags = ", ".join(s.get("tags", [])) or "—"
        rows.append([
            s.get("name", ""),
            s.get("type", ""),
            s.get("rank", ""),
            charges,
            recharge,
            mean,
            label,
            s.get("targets_stat", "").replace("_", " ").capitalize(),
            range_str,
            tags,
        ])
    return build_table(headers, rows)


def misc_table(items: list[dict]) -> str:
    items = sorted(items, key=lambda x: x.get("name", ""))
    headers = ["Name", "Price", "Notes"]
    rows = []
    for i in items:
        notes = i.get("description", "") or i.get("other_abilities", "") or "—"
        rows.append([i.get("name", ""), i.get("price", ""), notes])
    return build_table(headers, rows)


# ---------------------------------------------------------------------------
# Load & categorise
# ---------------------------------------------------------------------------

def load_all_items() -> list[dict]:
    with open(DATA_PATH) as f:
        data = json.load(f)
    items = []
    for _cat, entries in data.items():
        if isinstance(entries, list):
            items.extend(entries)
        elif isinstance(entries, dict):
            for _subcat, subitems in entries.items():
                if isinstance(subitems, list):
                    items.extend(subitems)
    return [i for i in items if isinstance(i, dict)]


# ---------------------------------------------------------------------------
# HTML page assembly
# ---------------------------------------------------------------------------

CSS = """
body {
  font-family: Georgia, serif;
  font-size: 13px;
  margin: 2em 3em;
  color: #111;
}
h1 { font-size: 1.5em; }
h2 { font-size: 1.15em; margin-top: 2em; border-bottom: 1px solid #999; padding-bottom: 0.2em; }
p.note { font-style: italic; color: #555; font-size: 0.9em; }
table {
  border-collapse: collapse;
  width: 100%;
  margin-top: 0.5em;
}
th {
  background: #e8e8e8;
  font-weight: bold;
  text-align: left;
  padding: 4px 8px;
  border: 1px solid #bbb;
}
td {
  padding: 3px 8px;
  border: 1px solid #ccc;
  vertical-align: top;
}
tr:nth-child(even) td { background: #f7f7f7; }
"""


def build_page(sections: list[tuple[str, str]]) -> str:
    body_parts = [
        "<!DOCTYPE html>",
        "<html><head>",
        "<meta charset='utf-8'>",
        f"<style>{CSS}</style>",
        "</head><body>",
        "<h1>Equipment Reference</h1>",
        "<p class='note'>To copy a table into Google Docs: open this file in a browser, "
        "click inside the table, select all rows (Ctrl+A may select the page — "
        "instead drag to select the table rows), copy, then paste into your document.</p>",
    ]
    for title, table_html in sections:
        body_parts.append(f"<h2>{esc(title)}</h2>")
        body_parts.append(table_html)
    body_parts.append("</body></html>")
    return "\n".join(body_parts)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    all_items = load_all_items()
    purchaseable = [i for i in all_items if i.get("purchaseable")]

    weapons    = [i for i in purchaseable if i.get("item_type") == "weapon"]
    gear       = [i for i in purchaseable if i.get("item_type") == "gear"]
    containers = [i for i in purchaseable if i.get("item_type") == "container"]
    misc       = [i for i in purchaseable if i.get("item_type") == "item"]
    spells     = [i for i in all_items if i.get("item_type") == "charge_weapon"]

    sections = []
    if weapons:
        sections.append(("Weapons", weapons_table(weapons)))
    if gear:
        sections.append(("Armor & Gear", gear_table(gear)))
    if containers:
        sections.append(("Spellbooks", containers_table(containers)))
    if spells:
        sections.append(("Spells", spells_table(spells)))
    if misc:
        sections.append(("Miscellaneous Items", misc_table(misc)))

    html = build_page(sections)
    OUT_PATH.mkdir(exist_ok=True)
    OUT_FILE.write_text(html, encoding="utf-8")
    print(f"Written to {OUT_FILE}")
    print(f"  {len(weapons)} weapons, {len(gear)} gear, {len(containers)} spellbooks, {len(spells)} spells, {len(misc)} misc items")


if __name__ == "__main__":
    main()
