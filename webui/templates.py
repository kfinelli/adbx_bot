"""
webui/templates.py — HTML rendering functions for the DM control panel.

HTMX note: hx-include selects by CSS selector and sends fields by their
`name` attribute. All inputs that are included via hx-include must have
a `name` attribute matching what the server expects.
"""

from __future__ import annotations

import html as _html
from uuid import UUID as _UUID

from engine.azure_constants import XP_THRESHOLDS, RechargePeriod
from engine.character import CharacterManager
from engine.data_loader import ITEM_REGISTRY
from engine.item import ChargeWeapon
from models import (
    Character,
    CharacterStatus,
    DoorState,
    GameState,
    NPCMovementLogic,
    RangeBand,
    SessionMode,
    TurnStatus,
)

# ---------------------------------------------------------------------------
# Page chrome
# ---------------------------------------------------------------------------

def page(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <script src="https://unpkg.com/htmx.org@1.9.10"></script>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{
      font-family: system-ui, sans-serif;
      background: #1a1a2e;
      color: #e0e0e0;
      margin: 0;
      padding: 0;
    }}
    h1, h2, h3 {{ color: #c9a84c; margin-top: 0; }}
    a {{ color: #c9a84c; }}
    .layout {{
      display: grid;
      grid-template-columns: 220px 1fr;
      min-height: 100vh;
    }}
    .sidebar {{
      background: #16213e;
      padding: 1.5rem 1rem;
      border-right: 1px solid #0f3460;
    }}
    .sidebar h2 {{ font-size: 1rem; color: #888; text-transform: uppercase; letter-spacing: 1px; }}
    .sidebar a {{
      display: block;
      padding: 0.4rem 0.6rem;
      border-radius: 4px;
      text-decoration: none;
      margin-bottom: 2px;
    }}
    .sidebar a:hover {{ background: #0f3460; }}
    .sidebar a.active {{ background: #0f3460; color: #fff; }}
    .main {{ padding: 1.5rem 2rem; }}
    .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }}
    .card {{
      background: #16213e;
      border: 1px solid #0f3460;
      border-radius: 8px;
      padding: 1rem 1.25rem;
      margin-bottom: 1rem;
    }}
    .card h3 {{ margin-bottom: 0.75rem; font-size: 1rem; }}
    label {{ display: block; font-size: 0.85rem; color: #aaa; margin-bottom: 2px; margin-top: 8px; }}
    input[type=text], input[type=number], textarea, select {{
      width: 100%;
      background: #0f3460;
      border: 1px solid #1a4a8a;
      border-radius: 4px;
      color: #e0e0e0;
      padding: 0.4rem 0.6rem;
      font-size: 0.9rem;
    }}
    textarea {{ resize: vertical; min-height: 60px; }}
    button, a.btn {{
      background: #0f3460;
      border: 1px solid #1a4a8a;
      color: #e0e0e0;
      border-radius: 4px;
      padding: 0.35rem 0.8rem;
      cursor: pointer;
      font-size: 0.85rem;
      margin-top: 6px;
    }}
    button:hover, .btn:hover a.bt:hover {{ background: #1a4a8a; }}
    .btn-danger {{ border-color: #8a1a1a; }}
    .btn-danger:hover {{ background: #8a1a1a; }}
    .btn-success {{ border-color: #1a8a4a; }}
    .btn-success:hover {{ background: #1a8a4a; }}
    .btn-sm {{ padding: 0.2rem 0.5rem; font-size: 0.8rem; margin-top: 0; }}
    .tag {{
      display: inline-block;
      padding: 2px 8px;
      border-radius: 12px;
      font-size: 0.75rem;
      font-weight: bold;
    }}
    .tag-open {{ background: #1a4a1a; color: #4caf50; }}
    .tag-closed {{ background: #4a2a1a; color: #ff9800; }}
    .tag-hold {{ background: #3a1a4a; color: #9c27b0; }}
    .tag-dead {{ background: #3a1a1a; color: #f44336; }}
    .tag-condition {{ background: #1a3a4a; color: #64b5f6; border: 1px solid #1a5a7a; }}
    .hp-bar {{ font-family: monospace; }}
    .combat-sub {{
      margin-top: 0.5rem;
      padding: 0.5rem 0.75rem;
      background: #0f1e36;
      border-left: 3px solid #c9a84c;
      border-radius: 0 4px 4px 0;
      font-size: 0.82rem;
    }}
    .combat-sub label {{ margin-top: 4px; font-size: 0.78rem; }}
    .band-grid {{
      display: grid;
      grid-template-columns: repeat(5, 1fr);
      gap: 4px;
      margin: 0.4rem 0;
      font-size: 0.75rem;
      font-family: monospace;
    }}
    .band-cell {{
      background: #0f3460;
      border: 1px solid #1a4a8a;
      border-radius: 3px;
      padding: 4px 2px;
      text-align: center;
      min-height: 2.5rem;
    }}
    .band-cell.active {{ border-color: #c9a84c; background: #1a2a40; }}
    .band-label {{ color: #888; font-size: 0.7rem; margin-bottom: 2px; }}
    .band-name {{ color: #c9a84c; font-weight: bold; }}
    .flash {{
      background: #1a4a1a;
      border: 1px solid #1a8a4a;
      border-radius: 4px;
      padding: 0.5rem 1rem;
      margin-bottom: 1rem;
      color: #4caf50;
    }}
    .error {{
      background: #4a1a1a;
      border: 1px solid #8a1a1a;
      border-radius: 4px;
      padding: 0.5rem 1rem;
      margin-bottom: 1rem;
      color: #f44336;
    }}
    .row {{ display: flex; gap: 0.5rem; align-items: flex-end; flex-wrap: wrap; }}
    .row > * {{ flex: 1; min-width: 80px; }}
    .row > button {{ flex: 0; }}
    .divider {{ border: none; border-top: 1px solid #0f3460; margin: 1rem 0; }}
    .muted {{ color: #888; font-size: 0.85rem; }}
    .section-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 0.75rem;
    }}
    .section-header h3 {{ margin: 0; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.875rem; }}
    th {{ text-align: left; color: #888; padding: 4px 8px; border-bottom: 1px solid #0f3460; }}
    td {{ padding: 4px 8px; border-bottom: 1px solid #0f3460; vertical-align: middle; }}
    tr:last-child td {{ border-bottom: none; }}
  </style>
  <script>
    // Force HTMX to swap even on 4xx/5xx responses, so error messages
    // are shown and edit forms close rather than staying stuck open.
    document.addEventListener('htmx:beforeSwap', function(evt) {{
      if (evt.detail.xhr.status >= 400) {{
        evt.detail.shouldSwap = true;
      }}
    }});
  </script>
</head>
<body>
{body}
</body>
</html>"""


def _tab_bar(channel_id: str, active_tab: str) -> str:
    """Dashboard / NPC Roster tab bar rendered at the top of each session view."""
    tabs = [
        ("dashboard", "Dashboard", f"/session/{channel_id}"),
        ("npcs", "NPC Roster", f"/session/{channel_id}/npcs"),
    ]
    items = ""
    for key, label, href in tabs:
        if key == active_tab:
            items += f'<a href="{href}" style="background:#0f3460;color:#fff;padding:0.4rem 1.1rem;border-radius:4px 4px 0 0;text-decoration:none;border:1px solid #1a4a8a;border-bottom:none">{label}</a>'
        else:
            items += f'<a href="{href}" style="padding:0.4rem 1.1rem;border-radius:4px 4px 0 0;text-decoration:none;color:#aaa">{label}</a>'
    return f'<div style="display:flex;gap:2px;border-bottom:1px solid #0f3460;margin-bottom:1rem">{items}</div>'


def _sidebar(channel_id: str | None, sessions: list[tuple[str, str]]) -> str:
    links = ""
    for cid, name in sessions:
        active = 'class="active"' if cid == channel_id else ""
        links += f'<a href="/session/{cid}" {active}>{name}</a>\n'
    return f"""
<div class="sidebar">
  <h1 style="font-size:1.1rem; margin-bottom:1.5rem;">DM Panel</h1>
  <h2>Sessions</h2>
  {links or '<p class="muted">No active sessions</p>'}
  <hr class="divider" style="margin:1rem 0">
  <a href="/archive">Archive</a>
  <a href="/characters">Character Sheets</a>
</div>"""


def session_list_page(sessions: list[tuple[str, str]]) -> str:
    body = f"""
<div class="layout">
  {_sidebar(None, sessions)}
  <div class="main">
    <h1>DM Control Panel</h1>
    <p class="muted">Select a session from the sidebar, or start one in Discord with Embark.</p>
  </div>
</div>"""
    return page("DM Panel", body)


def session_page(
    state: GameState,
    sessions: list[tuple[str, str]],
    flash: str = "",
    error: str = "",
    view_room_id: str = "",
    edit_id: str = "",
) -> str:
    channel_id = state.platform_channel_id
    body = f"""
<div class="layout">
  {_sidebar(channel_id, sessions)}
  <div class="main">
    {dashboard_fragment(state, flash, error, view_room_id, edit_id)}
  </div>
</div>"""
    return page("DM Panel", body)


def dashboard_fragment(
    state: GameState,
    flash: str = "",
    error: str = "",
    view_room_id: str = "",
    edit_id: str = "",
) -> str:
    channel_id = state.platform_channel_id
    flash_html = f'<div class="flash">{flash}</div>' if flash else ""
    error_html = f'<div class="error">{error}</div>' if error else ""

    # Resolve the room being viewed in the right column.
    # Preference order: explicit view_room_id > party's current room > None
    from uuid import UUID as _UUID
    view_room = None
    resolved_view_id = ""
    if view_room_id and state.dungeon:
        try:
            _vid = _UUID(view_room_id)
            view_room = state.dungeon.rooms.get(_vid)
            if view_room:
                resolved_view_id = view_room_id
        except ValueError:
            pass
    if view_room is None and state.current_room_id and state.dungeon:
        view_room = state.dungeon.rooms.get(state.current_room_id)
        if view_room:
            resolved_view_id = str(state.current_room_id)
    if view_room is None:
        # Fall back to the ad-hoc current_room path (no dungeon graph)
        view_room = state.current_room
        if view_room:
            resolved_view_id = str(view_room.room_id)

    is_party_room = (
        view_room is not None
        and state.current_room_id is not None
        and view_room.room_id == state.current_room_id
    )

    return f"""
<div id="dashboard">
  {_tab_bar(channel_id, "dashboard")}
  {flash_html}{error_html}
  <div class="grid-2">
    <div>
      {combat_panel(state)}
      {turn_panel(state, edit_id)}
      {dungeon_panel(state, resolved_view_id)}
      {oracle_panel(state)}
      {party_panel(state)}
    </div>
    <div>
      {room_panel(state, view_room, is_party_room, resolved_view_id, edit_id)}
      {npc_panel(state, channel_id, resolved_view_id, edit_id)}
    </div>
  </div>
</div>"""


# ---------------------------------------------------------------------------
# Turn panel
# ---------------------------------------------------------------------------

def turn_panel(state: GameState, edit_id: str = "") -> str:
    channel_id = state.platform_channel_id
    turn = state.current_turn

    if not state.session_active:
        status_tag = '<span class="tag tag-hold">ON HOLD</span>'
    elif turn is None:
        status_tag = '<span class="muted">No active turn</span>'
    elif turn.status == TurnStatus.OPEN:
        status_tag = '<span class="tag tag-open">OPEN</span>'
    elif turn.status == TurnStatus.CLOSED:
        status_tag = '<span class="tag tag-closed">CLOSED</span>'
    else:
        status_tag = f'<span class="tag">{turn.status.value}</span>'

    mode = "Rounds" if state.mode == SessionMode.ROUNDS else "Exploration"

    due_str = ""
    if turn and turn.due_at:
        due_str = f'<div class="muted" style="margin-bottom:0.5rem">Due: {turn.due_at.strftime("%Y-%m-%d %H:%M UTC")}</div>'

    # Turn number — inline edit when edit_id == "turn_number"
    if edit_id == "turn_number":
        turn_number_html = f"""
<form hx-post="/session/{channel_id}/setturnumber"
      hx-target="#dashboard" hx-swap="outerHTML"
      style="display:inline-flex;align-items:center;gap:0.4rem;margin-left:0.5rem">
  <input type="number" name="turn_number" value="{state.turn_number}"
         min="0" style="width:70px">
  <button class="btn-sm" type="submit">Set</button>
  <a href="/session/{channel_id}" style="font-size:0.8rem;color:#888">cancel</a>
</form>"""
        heading = f'<div class="section-header" style="flex-wrap:wrap"><h3>Turn{turn_number_html} &mdash; {mode}</h3>{status_tag}</div>'
    else:
        edit_link = f'<a href="/session/{channel_id}?edit=turn_number" title="Edit turn number" style="font-size:0.8rem;color:#666;margin-left:0.4rem">✎</a>'
        heading = f'<div class="section-header"><h3>Turn {state.turn_number}{edit_link} &mdash; {mode}</h3>{status_tag}</div>'

    # Random encounter progress bar (DM-only, hidden from players)
    encounter_bar_html = ""
    if state.dungeon and state.dungeon.random_encounter_roster:
        interval = state.dungeon.random_encounter_interval
        turns_since = min(state.turn_number - state.last_encounter_check_turn, interval)
        pct = int((turns_since / interval) * 100) if interval > 0 else 100
        fill_color = "#c9a84c" if pct < 100 else "#f44336"
        encounter_bar_html = (
            f'<div class="muted" style="margin-bottom:0.5rem;font-size:0.82rem">'
            f'Random Encounter: {turns_since}/{interval} turns'
            f'<div style="background:#0f3460;border-radius:3px;height:8px;overflow:hidden;margin-top:3px">'
            f'<div style="background:{fill_color};width:{pct}%;height:100%;border-radius:3px"></div>'
            f'</div></div>'
        )

    # Submissions table
    subs_html = ""
    if turn and state.party:
        rows = ""
        for cid in state.party.member_ids:
            char = state.characters.get(cid)
            if not char:
                continue
            sub = state.latest_submission(cid)
            active_subs = [s for s in turn.submissions if s.character_id == cid and s.is_latest]
            sub_text = f'<em>"{"; ".join(s.action_text for s in active_subs)}"</em>' if active_subs else '<span class="muted">—</span>'
            unsubmit_btn = ""
            if sub and turn.status == TurnStatus.OPEN:
                cid_str = str(cid)
                unsubmit_btn = f""" <button class="btn-sm btn-danger"
                    hx-post="/session/{channel_id}/turn/{cid_str}/unsubmit"
                    hx-target="#dashboard" hx-swap="outerHTML"
                    hx-confirm="Send this turn back to {char.name} for revision?">Return</button>"""
            rows += f"<tr><td>{char.name}</td><td>{sub_text}{unsubmit_btn}</td></tr>"
        if rows:
            subs_html = f'<table><tr><th>Character</th><th>Submitted Action</th></tr>{rows}</table>'

    # Resolve form — only when there is a turn to resolve
    resolve_html = ""
    if turn and turn.status in (TurnStatus.OPEN, TurnStatus.CLOSED):
        resolve_html = f"""
<hr class="divider">
<form hx-post="/session/{channel_id}/resolve"
      hx-target="#dashboard" hx-swap="outerHTML">
  <label>Resolution narrative</label>
  <textarea name="narrative" rows="3" placeholder="Describe what happens..."></textarea>
  <button class="btn-success" type="submit">Resolve Turn</button>
</form>"""

    timer_html = f"""
<hr class="divider">
<form hx-post="/session/{channel_id}/settimer"
      hx-target="#dashboard" hx-swap="outerHTML">
  <div class="row">
    <div>
      <label>Override timer (hours from now)</label>
      <input type="number" name="hours" value="24" min="0.5" step="0.5">
    </div>
    <button type="submit">Set Timer</button>
  </div>
</form>
<form hx-post="/session/{channel_id}/setturnlength"
      hx-target="#dashboard" hx-swap="outerHTML">
  <div class="row">
    <div>
      <label>Default turn length (hours)</label>
      <input type="number" name="hours" value="{state.default_turn_hours}" min="1" step="1">
    </div>
    <button type="submit">Set Default</button>
  </div>
</form>"""

    if state.mode == SessionMode.PRE_START:
        session_controls_html = f"""
<button class="btn-success"
        hx-post="/session/{channel_id}/embark"
        hx-target="#dashboard" hx-swap="outerHTML"
        hx-confirm="Embark? This starts the session and opens the first turn."
        >Embark</button>"""
    elif state.session_active:
        session_controls_html = f"""
<button class="btn-danger"
        hx-post="/session/{channel_id}/hold"
        hx-target="#dashboard" hx-swap="outerHTML"
        hx-confirm="Put session on hold?">Hold Session</button>"""
    else:
        session_controls_html = f"""
<button class="btn-success"
        hx-post="/session/{channel_id}/resume"
        hx-target="#dashboard" hx-swap="outerHTML">Resume Session</button>"""

    end_session_html = f"""
<form hx-post="/session/{channel_id}/endsession"
      hx-confirm="End session permanently? This deletes all session data and cannot be undone."
      style="margin-top:0.5rem">
  <button class="btn-danger" type="submit" style="width:100%">End Session</button>
</form>"""

    return f"""
<div class="card">
  {heading}
  {due_str}
  {encounter_bar_html}
  {subs_html}
  {resolve_html}
  {timer_html}
  <hr class="divider">
  {session_controls_html}
  {end_session_html}
</div>"""


# ---------------------------------------------------------------------------
# Combat panel — battlefield overview (ROUNDS mode only)
# ---------------------------------------------------------------------------

_BAND_ORDER = [
    RangeBand.FAR_MINUS,
    RangeBand.CLOSE_MINUS,
    RangeBand.ENGAGE,
    RangeBand.CLOSE_PLUS,
    RangeBand.FAR_PLUS,
]

_BAND_DISPLAY = {
    RangeBand.FAR_MINUS:   "−Far",
    RangeBand.CLOSE_MINUS: "−Close",
    RangeBand.ENGAGE:      "Engage",
    RangeBand.CLOSE_PLUS:  "+Close",
    RangeBand.FAR_PLUS:    "+Far",
}


def combat_panel(state: GameState) -> str:
    """
    Battlefield overview card shown when state.mode == ROUNDS.
    Displays the five range bands as a grid, with each combatant's name
    placed in their current band.  Dead combatants are omitted.
    """
    if state.mode != SessionMode.ROUNDS or state.battlefield is None:
        return ""

    # Group combatant names by band
    by_band: dict[RangeBand, list[str]] = {b: [] for b in _BAND_ORDER}
    for cid, cs in state.battlefield.combatants.items():
        char = state.characters.get(cid)
        if char:
            if char.status == CharacterStatus.DEAD:
                continue
            name = char.name
        else:
            # NPC
            name = None
            for group in state.npc_roster.groups.values():
                for npc in group.npcs:
                    if npc.npc_id == cid:
                        name = npc.name
                        break
                if name:
                    break
            if name is None or _find_npc_dead(state, cid):
                continue
        by_band[cs.range_band].append(name)

    cells = ""
    for band in _BAND_ORDER:
        names = by_band[band]
        occupants = "<br>".join(
            f'<span style="color:#e0e0e0">{n}</span>' for n in names
        ) if names else '<span style="color:#444">—</span>'
        is_active = any(
            cs.range_band == band
            for cs in state.battlefield.combatants.values()
        )
        active_cls = " active" if is_active else ""
        cells += f"""
<div class="band-cell{active_cls}">
  <div class="band-label">{'← players' if band == RangeBand.FAR_MINUS else ('enemies →' if band == RangeBand.FAR_PLUS else '')}</div>
  <div class="band-name">{_BAND_DISPLAY[band]}</div>
  <div style="margin-top:4px;font-size:0.75rem">{occupants}</div>
</div>"""

    round_log_html = ""
    if state.battlefield.round_log:
        entries = "".join(
            f'<div style="margin-bottom:2px">{e}</div>'
            for e in state.battlefield.round_log
        )
        round_log_html = f"""
<hr class="divider">
<div class="section-header" style="margin-bottom:0.3rem">
  <h3 style="font-size:0.85rem;color:#888">Last Round Log</h3>
</div>
<div style="font-size:0.8rem;color:#aaa;font-family:monospace;
     max-height:8rem;overflow-y:auto;padding:0.4rem;
     background:#0a1628;border-radius:4px;border:1px solid #0f3460">
  {entries}
</div>"""

    return f"""
<div class="card">
  <div class="section-header">
    <h3>&#9876; Battlefield — Round {state.turn_number}</h3>
  </div>
  <div class="band-grid">{cells}</div>
  {round_log_html}
</div>"""


def _find_npc_dead(state: GameState, npc_id) -> bool:
    for group in state.npc_roster.groups.values():
        for npc in group.npcs:
            if npc.npc_id == npc_id:
                return npc.status == "dead"
    return False


# ---------------------------------------------------------------------------
# Combat sub-panel — per-combatant battlefield controls (ROUNDS mode only)
# ---------------------------------------------------------------------------

def _combat_subpanel(
    state:        GameState,
    combatant_id: str,        # str UUID
    channel_id:   str,
    view_room_id: str = "",
    is_player:    bool = True,
) -> str:
    """
    Bordered sub-panel rendered inside each character/NPC row during ROUNDS mode.
    Shows: current range band (selectable), initiative, active conditions with
    remove buttons, and an Apply Condition form.
    """
    if state.mode != SessionMode.ROUNDS or state.battlefield is None:
        return ""

    from uuid import UUID
    try:
        cid_uuid = UUID(combatant_id)
    except ValueError:
        return ""

    cs = state.battlefield.combatants.get(cid_uuid)
    if cs is None:
        return ""

    base = f"/session/{channel_id}"

    # --- Range band selector
    band_options = "".join(
        f'<option value="{b.value}" {"selected" if b == cs.range_band else ""}>'
        f'{_BAND_DISPLAY[b]}</option>'
        for b in _BAND_ORDER
    )
    band_form = f"""
<form hx-post="{base}/combatant/{combatant_id}/setband"
      hx-target="#dashboard" hx-swap="outerHTML" style="display:inline-flex;gap:4px;align-items:center">
  <input type="hidden" name="view_room_id" value="{view_room_id}">
  <select name="band" onchange="this.form.requestSubmit()" style="font-size:0.78rem;padding:2px 4px">
    {band_options}
  </select>
</form>"""

    # --- Initiative
    init_form = f"""
<form hx-post="{base}/combatant/{combatant_id}/setinitiative"
      hx-target="#dashboard" hx-swap="outerHTML"
      style="display:inline-flex;gap:4px;align-items:center;margin-left:0.75rem">
  <input type="hidden" name="view_room_id" value="{view_room_id}">
  <label style="margin:0;color:#888">Init:</label>
  <input type="number" name="initiative" value="{cs.initiative}"
         style="width:50px;font-size:0.78rem;padding:2px 4px">
  <button class="btn-sm" type="submit" style="margin-top:0">Set</button>
</form>"""

    # --- Active conditions with remove buttons
    from engine.data_loader import CONDITION_REGISTRY
    cid_uuid_for_cond = UUID(combatant_id) if not isinstance(combatant_id, UUID) else combatant_id
    _cond_char = state.characters.get(cid_uuid_for_cond)
    _cond_npc  = next((n for g in state.npc_roster.groups.values() for n in g.npcs if n.npc_id == cid_uuid_for_cond), None)
    _cond_owner = _cond_char if _cond_char else _cond_npc
    cond_chips = ""
    for cond in (_cond_owner.active_conditions if _cond_owner else []):
        cond_def = CONDITION_REGISTRY.get(cond.condition_id)
        label = cond_def.label if cond_def else cond.condition_id
        dur = f" {cond.duration_rounds}r" if cond.duration_rounds is not None else " ∞"
        cond_chips += f"""
<span class="tag tag-condition" style="margin-right:4px;margin-bottom:4px">
  {label}{dur}
  <form style="display:inline" hx-post="{base}/combatant/{combatant_id}/removecondition"
        hx-target="#dashboard" hx-swap="outerHTML">
    <input type="hidden" name="condition_id" value="{cond.condition_id}">
    <input type="hidden" name="view_room_id" value="{view_room_id}">
    <button type="submit" style="background:none;border:none;color:#64b5f6;
            cursor:pointer;padding:0 2px;margin:0;font-size:0.8rem;line-height:1"
            title="Remove condition">×</button>
  </form>
</span>"""

    # --- Apply condition form
    all_conditions = sorted(CONDITION_REGISTRY.keys())
    cond_options = "".join(
        f'<option value="{cid}">{CONDITION_REGISTRY[cid].label}</option>'
        for cid in all_conditions
    )
    apply_form = f"""
<form hx-post="{base}/combatant/{combatant_id}/applycondition"
      hx-target="#dashboard" hx-swap="outerHTML"
      style="display:flex;gap:4px;align-items:flex-end;flex-wrap:wrap;margin-top:4px">
  <input type="hidden" name="view_room_id" value="{view_room_id}">
  <div style="flex:1;min-width:90px">
    <label>Condition</label>
    <select name="condition_id" style="font-size:0.78rem">{cond_options}</select>
  </div>
  <div style="width:60px">
    <label>Rounds</label>
    <input type="number" name="duration" value="3" min="1"
           style="font-size:0.78rem;padding:2px 4px">
  </div>
  <button class="btn-sm" type="submit" style="margin-bottom:1px">Apply</button>
</form>"""

    conditions_html = (
        f'<div style="margin:4px 0">{cond_chips}</div>'
        if cond_chips else
        '<span class="muted" style="font-size:0.78rem">No conditions</span>'
    )

    return f"""
<div class="combat-sub">
  <div style="display:flex;align-items:center;flex-wrap:wrap;gap:0.25rem">
    <span class="muted" style="font-size:0.78rem;margin-right:4px">Band:</span>
    {band_form}
    {init_form}
  </div>
  <div style="margin-top:6px">
    {conditions_html}
    {apply_form}
  </div>
</div>"""


# ---------------------------------------------------------------------------
# Party panel
# ---------------------------------------------------------------------------

def party_panel(state: GameState) -> str:
    channel_id = state.platform_channel_id
    if not state.party:
        return '<div class="card"><h3>Party</h3><p class="muted">No party.</p></div>'

    light_parts = []
    for char_id in state.party.member_ids:
        char = state.characters.get(char_id)
        if char is None:
            continue
        for item_id in char.equipped_slots.values():
            if not item_id:
                continue
            defn = ITEM_REGISTRY.get(item_id)
            if defn is None or getattr(defn, "max_light_turns", None) is None:
                continue
            inv = next(
                (i for i in char.inventory if i.item_id == item_id and i.equipped), None
            )
            if inv and inv.charges is not None:
                light_parts.append(f"{defn.name} ({char.name}): {inv.charges} turns")
    light_str = ", ".join(light_parts) if light_parts else "No light source"

    rows = ""
    for cid in state.party.member_ids:
        char = state.characters.get(cid)
        if not char:
            continue
        is_leader = cid == state.party.leader_id
        leader_star = " &#9733;" if is_leader else ""
        status_tag = ""
        if char.status == CharacterStatus.DEAD:
            status_tag = '<span class="tag tag-dead">DEAD</span>'
        elif char.status_notes:
            status_tag = f'<span class="muted">{char.status_notes}</span>'

        cid_str = str(cid)
        make_leader_btn = "" if is_leader else f"""
<button class="btn-sm"
        hx-post="/session/{channel_id}/setleader/{cid_str}"
        hx-target="#dashboard" hx-swap="outerHTML">Make Leader</button>"""

        # Each control is its own form so name attributes are unambiguous
        combat_sub = _combat_subpanel(state, cid_str, channel_id, is_player=True)
        rows += f"""
<tr>
  <td><strong>{char.name}{leader_star}</strong><br>
      <span class="muted">{char.character_class.value} {char.level}</span></td>
  <td class="hp-bar">{char.hp_current}/{char.hp_max}</td>
  <td>{status_tag}</td>
  <td>
    <form hx-post="/session/{channel_id}/char/{cid_str}/sethp"
          hx-target="#dashboard" hx-swap="outerHTML" style="margin-bottom:4px">
      <div class="row">
        <input type="number" name="hp" value="{char.hp_current}"
               min="0" max="{char.hp_max}" style="width:60px;flex:0">
        <button class="btn-sm" type="submit">Set HP</button>
      </div>
    </form>
    <form hx-post="/session/{channel_id}/char/{cid_str}/setstatus"
          hx-target="#dashboard" hx-swap="outerHTML">
      <div class="row">
        <input type="text" name="notes" value="{char.status_notes}" placeholder="notes">
        <select name="status">
          {''.join(f'<option value="{s.value}" {"selected" if s == char.status else ""}>{s.value}</option>' for s in CharacterStatus)}
        </select>
        <button class="btn-sm" type="submit">Set</button>
      </div>
    </form>
    {make_leader_btn}
    <form hx-post="/session/{channel_id}/char/{cid_str}/rollsave"
          hx-target="#dashboard" hx-swap="outerHTML"
          style="display:inline-flex;gap:4px;align-items:center;margin-top:4px">
      <select name="stat" style="font-size:0.78rem;padding:2px 4px">
        <option value="physique">Physique</option>
        <option value="finesse">Finesse</option>
        <option value="reason">Reason</option>
        <option value="savvy">Savvy</option>
      </select>
      <button class="btn-sm" type="submit" style="margin-top:0">Roll Save</button>
    </form>
    {combat_sub}
  </td>
</tr>"""

    light_html = f"""
<hr class="divider">
<div class="section-header"><h3>Light Source</h3></div>
<p class="muted">{light_str}</p>"""

    return f"""
<div class="card">
  <div class="section-header">
    <h3>Party</h3>
    <span class="muted">Gold: {state.party.gold}</span>
  </div>
  <form hx-post="/session/{channel_id}/party/addgold"
        hx-target="#dashboard" hx-swap="outerHTML" style="margin-bottom:0.5rem">
    <div class="row">
      <div><label>Add Gold</label>
      <input type="number" name="amount" value="0" min="0"></div>
      <button type="submit">Add Gold</button>
    </div>
  </form>
  <form hx-post="/session/{channel_id}/party/addxp"
        hx-target="#dashboard" hx-swap="outerHTML" style="margin-bottom:0.5rem">
    <div class="row">
      <div><label>Distribute XP (split evenly)</label>
      <input type="number" name="amount" value="0" min="0"></div>
      <button type="submit">Distribute XP</button>
    </div>
  </form>
  <form hx-post="/session/{channel_id}/party/rechargedaily"
        hx-target="#dashboard" hx-swap="outerHTML" style="margin-bottom:0.5rem">
    <button type="submit">Recharge All Daily Spells</button>
  </form>
  <table>
    <tr><th>Character</th><th>HP</th><th>Status</th><th>Controls</th></tr>
    {rows}
  </table>
  {light_html}
</div>"""


# ---------------------------------------------------------------------------
# Room panel
# ---------------------------------------------------------------------------

def room_panel(
    state: GameState,
    room,                   # Room | None
    is_party_room: bool = False,
    view_room_id: str = "",
    edit_id: str = "",
) -> str:
    channel_id = state.platform_channel_id

    if not room:
        return '<div class="card"><h3>Room</h3><p class="muted">Select a room from the dungeon list, or create one.</p></div>'

    rid = str(room.room_id)
    party_badge = (
        ' <span class="tag tag-open" style="font-size:0.7rem;vertical-align:middle">Party here</span>'
        if is_party_room else ""
    )

    # --- Room header: inline edit or static display
    base_url = f"/session/{channel_id}?view_room={view_room_id}"
    if edit_id == f"room_{rid}":
        room_header_html = f"""
<form hx-post="/session/{channel_id}/room/{rid}/update"
      hx-target="#dashboard" hx-swap="outerHTML">
  <input type="hidden" name="view_room_id" value="{view_room_id}">
  <label>Name</label>
  <input type="text" name="name" value="{room.name}" required>
  <label>Description</label>
  <textarea name="description" rows="2">{room.description}</textarea>
  <label>DM Notes</label>
  <textarea name="notes" rows="1">{room.notes}</textarea>
  <div class="row" style="margin-top:0.5rem">
    <button type="submit">Save</button>
    <a href="{base_url}" style="align-self:center;font-size:0.85rem;color:#888">cancel</a>
  </div>
</form>"""
    else:
        edit_link = f'<a href="{base_url}&edit=room_{rid}" title="Edit room" style="font-size:0.8rem;color:#666;margin-left:0.4rem">✎</a>'
        room_header_html = f"""
<p class="muted">{room.description}</p>
{f'<p class="muted"><em>DM notes: {room.notes}</em></p>' if room.notes else ''}"""
        party_badge = party_badge + edit_link  # attach edit pencil to heading

    # --- Features
    features_html = ""
    for feat in room.features:
        fid = str(feat.feature_id)
        feat_base = f"{base_url}&edit={fid}"
        if edit_id == fid:
            features_html += f"""
<tr>
  <td colspan="2">
    <form hx-post="/session/{channel_id}/feature/{fid}/update"
          hx-target="#dashboard" hx-swap="outerHTML">
      <input type="hidden" name="view_room_id" value="{view_room_id}">
      <div class="row">
        <div><label>Name</label><input type="text" name="name" value="{feat.name}" required></div>
        <div><label>State</label><input type="text" name="state_str" value="{feat.state}"></div>
      </div>
      <label>Description</label>
      <textarea name="description" rows="2">{feat.description}</textarea>
      <label>Notes</label>
      <input type="text" name="notes" value="{feat.notes or ''}">
      <div class="row" style="margin-top:0.5rem">
        <button type="submit">Save</button>
        <a href="{base_url}" style="align-self:center;font-size:0.85rem;color:#888">cancel</a>
      </div>
    </form>
  </td>
</tr>"""
        else:
            features_html += f"""
<tr>
  <td><strong>{feat.name}</strong> <span class="muted">[{feat.state}]</span><br>
      <span class="muted">{feat.description}</span></td>
  <td style="white-space:nowrap">
    <form style="display:inline" hx-post="/session/{channel_id}/feature/{fid}/setstate"
          hx-target="#dashboard" hx-swap="outerHTML">
      <input type="hidden" name="view_room_id" value="{view_room_id}">
      <div class="row">
        <input type="text" name="state_str" value="{feat.state}" style="width:90px">
        <button class="btn-sm" type="submit">Set</button>
      </div>
    </form>
    <a href="{feat_base}" class="btn-sm" style="margin-left:2px">Edit</a>
    <form style="display:inline" hx-post="/session/{channel_id}/feature/{fid}/delete"
          hx-target="#dashboard" hx-swap="outerHTML"
          hx-confirm="Delete feature '{feat.name}'?">
      <input type="hidden" name="view_room_id" value="{view_room_id}">
      <button class="btn-sm btn-danger" type="submit">Del</button>
    </form>
  </td>
</tr>"""

    add_feature_html = f"""
<hr class="divider">
<div class="section-header"><h3>Add Feature</h3></div>
<form hx-post="/session/{channel_id}/addfeature"
      hx-target="#dashboard" hx-swap="outerHTML">
  <input type="hidden" name="view_room_id" value="{view_room_id}">
  <div class="row">
    <div><label>Name</label><input type="text" name="name" placeholder="Chandelier"></div>
    <div><label>State</label><input type="text" name="state_str" value="intact"></div>
  </div>
  <label>Description</label>
  <textarea name="description" rows="2" placeholder="Player-visible description"></textarea>
  <button type="submit">Add Feature</button>
</form>"""

    # --- Exits
    exits_html = ""
    for i, ex in enumerate(room.exits, 1):
        eid = str(ex.exit_id)
        dest_name = ""
        dest_visited = False
        if ex.destination_id and state.dungeon:
            dest_room = state.dungeon.rooms.get(ex.destination_id)
            if dest_room:
                dest_name = f" \u2192 {dest_room.name}"
                dest_visited = dest_room.visited
        exit_base = f"{base_url}&edit={eid}"
        if edit_id == eid:
            # Build destination options for all dungeon rooms
            dest_options = '<option value="">— none —</option>'
            if state.dungeon:
                for dr in sorted(state.dungeon.rooms.values(), key=lambda r: r.name):
                    sel = 'selected' if ex.destination_id == dr.room_id else ''
                    dest_options += f'<option value="{dr.room_id}" {sel}>{dr.name}</option>'
            door_options = "".join(
                f'<option value="{d.value}" {"selected" if d == ex.door_state else ""}>{d.value}</option>'
                for d in DoorState
            )
            exits_html += f"""
<tr>
  <td colspan="2">
    <form hx-post="/session/{channel_id}/exit/{eid}/update"
          hx-target="#dashboard" hx-swap="outerHTML">
      <input type="hidden" name="view_room_id" value="{view_room_id}">
      <div class="row">
        <div><label>Label</label><input type="text" name="label" value="{ex.label}" required></div>
        <div><label>Door State</label><select name="door_state">{door_options}</select></div>
      </div>
      <label>Destination</label>
      <select name="destination_id" style="width:100%">{dest_options}</select>
      <label>Description</label>
      <textarea name="description" rows="2">{ex.description}</textarea>
      <div style="margin-top:0.5rem">
        <label style="display:inline-flex;align-items:center;gap:0.4rem;cursor:pointer">
          <input type="checkbox" name="auto_move" value="1" {"checked" if ex.auto_move else ""}>
          auto-move (skip DM approval on /abscond)
        </label>
      </div>
      <div style="margin-top:0.5rem">
        <label style="display:inline-flex;align-items:center;gap:0.4rem;cursor:pointer">
          <input type="checkbox" name="hidden" value="1" {"checked" if ex.hidden else ""}>
          hidden (invisible to players until revealed)
        </label>
      </div>
      <div class="row" style="margin-top:0.5rem">
        <button type="submit">Save</button>
        <a href="{base_url}" style="align-self:center;font-size:0.85rem;color:#888">cancel</a>
      </div>
    </form>
  </td>
</tr>"""
        else:
            door_options = "".join(
                f'<option value="{d.value}" {"selected" if d == ex.door_state else ""}>{d.value}</option>'
                for d in DoorState
            )
            auto_badge = ' <span style="font-size:0.75rem;color:#4caf50;font-weight:600">[auto]</span>' if ex.auto_move else ""
            explored_badge = ' <span style="font-size:0.75rem;color:#888">[explored]</span>' if dest_visited else ""
            hidden_badge = ' <span style="font-size:0.75rem;color:#888">[hidden]</span>' if ex.hidden else ""
            row_style = ' style="opacity:0.55;font-style:italic"' if ex.hidden else ""
            vis_label = "Reveal" if ex.hidden else "Hide"
            vis_hidden_val = "false" if ex.hidden else "true"
            exits_html += f"""
<tr{row_style}>
  <td><strong>{i}. {ex.label.capitalize()}</strong>{dest_name}{auto_badge}{explored_badge}{hidden_badge}<br>
      <span class="muted">{ex.description}</span></td>
  <td style="white-space:nowrap">
    <form style="display:inline" hx-post="/session/{channel_id}/exit/{eid}/setstate"
          hx-target="#dashboard" hx-swap="outerHTML">
      <input type="hidden" name="view_room_id" value="{view_room_id}">
      <div class="row">
        <select name="door_state" onchange="this.form.requestSubmit()">{door_options}</select>
      </div>
    </form>
    <form style="display:inline" hx-post="/session/{channel_id}/exit/{eid}/setvisibility"
          hx-target="#dashboard" hx-swap="outerHTML">
      <input type="hidden" name="view_room_id" value="{view_room_id}">
      <input type="hidden" name="hidden" value="{vis_hidden_val}">
      <button class="btn-sm" type="submit">{vis_label}</button>
    </form>
    <a href="{exit_base}" class="btn-sm" style="margin-top:4px;display:inline-block">Edit</a>
    <form style="display:inline" hx-post="/session/{channel_id}/exit/{eid}/delete"
          hx-target="#dashboard" hx-swap="outerHTML"
          hx-confirm="Delete exit '{ex.label}'?">
      <input type="hidden" name="view_room_id" value="{view_room_id}">
      <button class="btn-sm btn-danger" type="submit">Del</button>
    </form>
  </td>
</tr>"""

    # Build destination room options (all authored rooms except this one)
    dest_options = '<option value="">— none —</option>'
    if state.dungeon:
        for r in sorted(state.dungeon.rooms.values(), key=lambda r: r.name.lower()):
            if r.room_id != room.room_id and r.authored:
                dest_options += f'<option value="{r.room_id}">{_html.escape(r.name)}</option>'

    add_exit_html = f"""
<hr class="divider">
<div class="section-header"><h3>Add Exit</h3></div>
<form hx-post="/session/{channel_id}/addexit"
      hx-target="#dashboard" hx-swap="outerHTML">
  <input type="hidden" name="view_room_id" value="{view_room_id}">
  <div class="row">
    <div><label>Label</label><input type="text" name="label" placeholder="north"></div>
    <div><label>Door State</label>
    <select name="door_state">
      {"".join(f'<option value="{d.value}">{d.value}</option>' for d in DoorState)}
    </select></div>
    <div><label>Destination</label>
    <select name="destination_id">{dest_options}</select></div>
  </div>
  <label>Description</label>
  <textarea name="description" rows="2" placeholder="Player-visible description"></textarea>
  <button type="submit">Add Exit</button>
</form>"""

    return f"""
<div class="card">
  <div class="section-header">
    <h3>Room: {room.name}{party_badge}</h3>
  </div>
  {room_header_html}

  <div class="section-header"><h3>Features</h3></div>
  {"<table><tr><th>Feature</th><th>Controls</th></tr>" + features_html + "</table>" if features_html else '<p class="muted">No features.</p>'}
  {add_feature_html}

  <hr class="divider">
  <div class="section-header"><h3>Exits</h3></div>
  {"<table><tr><th>Exit</th><th>Controls</th></tr>" + exits_html + "</table>" if exits_html else '<p class="muted">No exits.</p>'}
  {add_exit_html}
</div>"""


# ---------------------------------------------------------------------------
# Dungeon import / export panel (PRE_START only)
# ---------------------------------------------------------------------------

def dungeon_panel(state: GameState, view_room_id: str = "") -> str:
    channel_id = state.platform_channel_id
    dungeon = state.dungeon

    # --- Header summary + export + edit form
    if dungeon:
        safe_name = dungeon.name.replace(" ", "_").lower()
        _e_dname = _html.escape(dungeon.name, quote=True)
        _e_ddesc = _html.escape(dungeon.description or "", quote=True)
        _e_droll = _html.escape(dungeon.random_encounter_roll or "1d6", quote=True)
        summary = (
            f"<p><strong>{dungeon.name}</strong> &mdash; "
            f"{len(dungeon.rooms)} room(s)</p>"
        )
        export_btn = (
            f'<a class="btn btn-sm" href="/session/{channel_id}/dungeon/export" '
            f'download="{safe_name}.json">Export JSON</a>'
        )
        dungeon_edit_html = f"""
<hr class="divider">
<div class="section-header"><h3 style="font-size:0.9rem">Dungeon Settings</h3></div>
<form hx-post="/session/{channel_id}/dungeon/update"
      hx-target="#dashboard" hx-swap="outerHTML">
  <input type="hidden" name="view_room_id" value="{view_room_id}">
  <label>Name</label>
  <input type="text" name="name" value="{_e_dname}" required>
  <label>Description</label>
  <textarea name="description" rows="2">{_e_ddesc}</textarea>
  <div class="row">
    <div><label>Enc. Interval (turns)</label>
    <input type="number" name="random_encounter_interval"
           value="{dungeon.random_encounter_interval}" min="1" style="width:70px"></div>
    <div><label>Enc. Roll</label>
    <input type="text" name="random_encounter_roll" value="{_e_droll}" style="width:70px"></div>
  </div>
  <button type="submit">Save</button>
</form>"""
    else:
        summary = '<p class="muted">No dungeon loaded.</p>'
        export_btn = ""
        dungeon_edit_html = ""

    # --- Import form (PRE_START only)
    if state.mode == SessionMode.PRE_START:
        replace_note = (
            '<p class="muted" style="margin-top:0.75rem">Replace dungeon:</p>'
            if dungeon else ""
        )
        import_html = f"""
{replace_note}
<form hx-post="/session/{channel_id}/dungeon/import"
      hx-target="#dashboard" hx-swap="outerHTML"
      hx-encoding="multipart/form-data">
  <input type="file" name="file" accept=".json"
         onchange="this.form.requestSubmit()"
         style="color:#e0e0e0; margin-top:4px">
</form>"""
    else:
        import_html = ""

    # --- Room list
    if dungeon and dungeon.rooms:
        room_rows = ""
        # Sort: party room first, then alphabetical
        def _sort_key(r):
            is_current = (state.current_room_id == r.room_id)
            return (0 if is_current else 1, r.name.lower())
        for room in sorted(dungeon.rooms.values(), key=_sort_key):
            rid = str(room.room_id)
            is_party = (state.current_room_id == room.room_id)
            is_viewing = (rid == view_room_id)
            party_mark = '<span title="Party is here" style="color:#c9a84c">&#9733;</span>' if is_party else '<span style="color:transparent">&#9733;</span>'
            visited_note = '' if room.visited else ' <span class="muted" style="font-size:0.75rem">(unvisited)</span>'
            row_style = 'background:#0f2a50;' if is_viewing else ''
            # Enter-room button only available once session is active
            if state.mode != SessionMode.PRE_START:
                enter_btn = f"""<form style="display:inline" hx-post="/session/{channel_id}/enterroom/{rid}" hx-target="#dashboard" hx-swap="outerHTML"><button class="btn-sm" type="submit">Enter</button></form>"""
            else:
                enter_btn = ""
            view_link = f'/session/{channel_id}?view_room={rid}'
            room_rows += f"""
<tr style="{row_style}">
  <td style="width:1.2rem">{party_mark}</td>
  <td><a href="{view_link}" style="color:#e0e0e0;text-decoration:none">{room.name}{visited_note}</a></td>
  <td style="text-align:right">{enter_btn}</td>
</tr>"""

        room_list_html = f"""
<hr class="divider">
<div class="section-header" style="margin-bottom:0.4rem">
  <h3 style="font-size:0.9rem">Rooms</h3>
</div>
<table style="font-size:0.85rem">{room_rows}</table>"""
    else:
        room_list_html = ""

    # --- New room inline form
    new_room_html = f"""
<hr class="divider">
<div class="section-header"><h3 style="font-size:0.9rem">New Room</h3></div>
<form hx-post="/session/{channel_id}/setroom"
      hx-target="#dashboard" hx-swap="outerHTML">
  <input type="hidden" name="view_room_id" value="{view_room_id}">
  <input type="text" name="name" placeholder="Room name" style="margin-bottom:4px">
  <textarea name="description" rows="2" placeholder="Player-visible description"></textarea>
  <textarea name="notes" rows="1" placeholder="DM notes (optional)"></textarea>
  <button type="submit">Create Room</button>
</form>"""

    return f"""
<div class="card">
  <div class="section-header"><h3>&#128506; Dungeon</h3></div>
  {summary}
  {export_btn}
  {import_html}
  {dungeon_edit_html}
  {room_list_html}
  {new_room_html}
</div>"""


# ---------------------------------------------------------------------------
# Oracle panel
# ---------------------------------------------------------------------------

def oracle_panel(state: GameState) -> str:
    channel_id = state.platform_channel_id

    # Show oracles from the current turn (oracle_counter > 0 means some exist)
    # Unanswered ones are most urgent; show all current-turn oracles.
    current_turn_oracles = [o for o in state.oracles if o.answer is None]
    answered = [o for o in state.oracles if o.answer is not None]

    unanswered_html = ""
    for o in current_turn_oracles:
        unanswered_html += f"""
<div style="margin-bottom:1rem; padding:0.75rem; background:#0f2040; border-radius:6px; border-left:3px solid #c9a84c">
  <div style="margin-bottom:0.4rem">
    <strong>#{o.number}</strong>
    <span class="muted" style="margin-left:0.5rem">{o.asker_name} asks:</span>
  </div>
  <div style="margin-bottom:0.6rem; color:#e0e0e0">{o.question}</div>
  <form hx-post="/session/{channel_id}/oracle/{o.number}/answer"
        hx-target="#dashboard" hx-swap="outerHTML">
    <div class="row">
      <input type="text" name="answer" placeholder="Your answer..." autofocus>
      <button class="btn-success btn-sm" type="submit">Answer</button>
    </div>
  </form>
</div>"""

    answered_html = ""
    for o in answered[-5:]:  # show last 5 answered for reference
        answered_html += f"""
<div class="muted" style="margin-bottom:0.4rem; font-size:0.82rem">
  <strong>#{o.number} {o.asker_name}:</strong> {o.question}<br>
  <span style="color:#4caf50">&rsaquo; {o.answer}</span>
</div>"""

    body = unanswered_html or '<p class="muted">No pending oracles.</p>'
    if answered_html:
        body += f'<hr class="divider"><p class="muted" style="font-size:0.8rem;margin-bottom:0.4rem">ANSWERED</p>{answered_html}'

    return f"""
<div class="card">
  <div class="section-header"><h3>&#128302; Oracles</h3></div>
  {body}
</div>"""




def npc_panel(
    state: GameState,
    channel_id: str = "",
    view_room_id: str = "",
    edit_id: str = "",
) -> str:
    if not channel_id:
        channel_id = state.platform_channel_id
    base_url = f"/session/{channel_id}?view_room={view_room_id}"

    # Use npcs_in_current_room to get NPCs from the roster for the current room
    npcs_to_show = state.npcs_in_current_room if view_room_id == "" else []
    # If viewing a specific room, get NPCs from that room in the roster
    if view_room_id:
        from uuid import UUID
        try:
            room_uuid = UUID(view_room_id)
            npcs_to_show = state.npc_roster.get_npcs_in_room(room_uuid)
        except ValueError:
            npcs_to_show = []

    rows = ""
    for npc in npcs_to_show:
        nid = str(npc.npc_id)
        npc_base = f"{base_url}&edit={nid}"
        if edit_id == nid:
            _e_name   = _html.escape(npc.name, quote=True)
            _e_desc   = _html.escape(npc.description or "", quote=True)
            _e_notes  = _html.escape(npc.notes or "", quote=True)
            _e_dmg    = _html.escape(npc.damage_dice or "1d6", quote=True)
            rows += f"""
<tr>
  <td colspan="3">
    <form hx-post="/session/{channel_id}/npc/{nid}/update"
          hx-target="#dashboard" hx-swap="outerHTML">
      <input type="hidden" name="view_room_id" value="{view_room_id}">
      <div class="row">
        <div><label>Name</label><input type="text" name="name" value="{_e_name}" required></div>
        <div><label>HP Max</label><input type="number" name="hp_max" value="{npc.hp_max}" min="1" style="width:60px"></div>
        <div><label>HP Now</label><input type="number" name="hp_current" value="{npc.hp_current}" min="0" style="width:60px"></div>
        <div><label>DEF</label><input type="number" name="defense" value="{npc.defense}" min="0" style="width:55px"></div>
      </div>
      <div class="row">
        <div><label>HD</label><input type="number" name="hit_dice" value="{npc.hit_dice}" min="1" style="width:55px"></div>
        <div><label>RES</label><input type="number" name="resistance" value="{npc.resistance}" min="0" style="width:55px"></div>
        <div><label>Range</label><input type="number" name="weapon_range" value="{npc.weapon_range}" min="0" style="width:55px"></div>
        <div><label>Damage</label><input type="text" name="damage_dice" value="{_e_dmg}" style="width:70px"></div>
        <div><label>Dodge</label><span style="align-self:center;font-size:0.9rem">{npc.dodge}</span></div>
      </div>
      <label>Description</label>
      <input type="text" name="description" value="{_e_desc}">
      <label>Notes</label>
      <input type="text" name="notes" value="{_e_notes}">
      <div class="row" style="margin-top:0.5rem">
        <button type="submit">Save</button>
        <a href="{base_url}" style="align-self:center;font-size:0.85rem;color:#888">cancel</a>
      </div>
    </form>
  </td>
</tr>"""
        else:
            npc_combat_sub = _combat_subpanel(state, nid, channel_id, view_room_id, is_player=False)
            _e_name   = _html.escape(npc.name)
            _e_desc   = _html.escape(npc.description or "")
            _e_status = _html.escape(str(npc.status), quote=True)
            hidden_badge = ' <span style="font-size:0.75rem;color:#888">[hidden]</span>' if npc.hidden else ""
            row_style = ' style="opacity:0.55;font-style:italic"' if npc.hidden else ""
            vis_label = "Reveal" if npc.hidden else "Hide"
            vis_hidden_val = "false" if npc.hidden else "true"
            rows += f"""
<tr{row_style}>
  <td><strong>{_e_name}</strong>{hidden_badge}<br>
      <span class="muted">{_e_desc}</span>
      {npc_combat_sub}</td>
  <td class="hp-bar">{npc.hp_current}/{npc.hp_max}</td>
  <td style="white-space:nowrap">
    <form hx-post="/session/{channel_id}/npc/{nid}/sethp"
          hx-target="#dashboard" hx-swap="outerHTML" style="margin-bottom:4px">
      <input type="hidden" name="view_room_id" value="{view_room_id}">
      <div class="row">
        <input type="number" name="hp" value="{npc.hp_current}"
               min="0" max="{npc.hp_max}" style="width:60px;flex:0">
        <button class="btn-sm" type="submit">HP</button>
      </div>
    </form>
    <form hx-post="/session/{channel_id}/npc/{nid}/setstatus"
          hx-target="#dashboard" hx-swap="outerHTML" style="margin-bottom:4px">
      <input type="hidden" name="view_room_id" value="{view_room_id}">
      <div class="row">
        <input type="text" name="status" value="{_e_status}" placeholder="status">
        <button class="btn-sm" type="submit">Status</button>
      </div>
    </form>
    <form style="display:inline" hx-post="/session/{channel_id}/npc/{nid}/setvisibility"
          hx-target="#dashboard" hx-swap="outerHTML" style="margin-bottom:4px">
      <input type="hidden" name="view_room_id" value="{view_room_id}">
      <input type="hidden" name="hidden" value="{vis_hidden_val}">
      <button class="btn-sm" type="submit">{vis_label}</button>
    </form>
    <a href="{npc_base}" class="btn-sm">Edit</a>
    <form style="display:inline" hx-post="/session/{channel_id}/npc/{nid}/copy"
          hx-target="#dashboard" hx-swap="outerHTML">
      <input type="hidden" name="view_room_id" value="{view_room_id}">
      <button class="btn-sm" type="submit">Copy</button>
    </form>
    <form style="display:inline" hx-post="/session/{channel_id}/npc/{nid}/delete"
          hx-target="#dashboard" hx-swap="outerHTML"
          hx-confirm="Remove {_e_name}?">
      <input type="hidden" name="view_room_id" value="{view_room_id}">
      <button class="btn-sm btn-danger" type="submit">Del</button>
    </form>
  </td>
</tr>"""

    roster_link = f'<a href="/session/{channel_id}/npcs" style="font-size:0.82rem;color:#888">Manage full NPC roster &rarr;</a>'

    return f"""
<div class="card">
  <div class="section-header">
    <h3>NPCs in Room</h3>
    {roster_link}
  </div>
  {'<table><tr><th>NPC</th><th>HP</th><th>Controls</th></tr>' + rows + '</table>' if rows else '<p class="muted">No NPCs in this room.</p>'}
</div>"""


# ---------------------------------------------------------------------------
# NPC Roster page
# ---------------------------------------------------------------------------

def npc_roster_page(
    state: GameState,
    sessions: list[tuple[str, str]],
    flash: str = "",
    error: str = "",
    edit_id: str = "",
) -> str:
    channel_id = state.platform_channel_id
    body = f"""
<div class="layout">
  {_sidebar(channel_id, sessions)}
  <div class="main">
    {npc_roster_fragment(state, flash, error, edit_id)}
  </div>
</div>"""
    return page("NPC Roster — DM Panel", body)


def npc_roster_fragment(
    state: GameState,
    flash: str = "",
    error: str = "",
    edit_id: str = "",
) -> str:
    channel_id = state.platform_channel_id
    flash_html = f'<div class="flash">{flash}</div>' if flash else ""
    error_html = f'<div class="error">{error}</div>' if error else ""
    return f"""
<div id="npc-roster">
  {_tab_bar(channel_id, "npcs")}
  {flash_html}{error_html}
  {_npc_groups_section(state, channel_id, edit_id)}
  {_encounter_settings_section(state, channel_id)}
  {_encounter_roster_section(state, channel_id, edit_id)}
</div>"""


def _npc_groups_section(state: GameState, channel_id: str, edit_id: str) -> str:
    groups = list(state.npc_roster.groups.values())

    room_name_map: dict[str, str] = {}
    if state.dungeon:
        for rid, room in state.dungeon.rooms.items():
            room_name_map[str(rid)] = room.name

    cards = ""
    for group in groups:
        gid = str(group.group_id)
        room_name = room_name_map.get(str(group.current_room_id), "—") if group.current_room_id else "—"
        logic_label = group.movement_logic.value.capitalize() if group.movement_logic else "Stationary"
        group_display_name = _html.escape(group.name or "(unnamed group)")
        npc_count = len(group.npcs)

        if edit_id == f"group:{gid}":
            _e_gname = _html.escape(group.name or "", quote=True)
            logic_options = "".join(
                f'<option value="{m.value}" {"selected" if group.movement_logic == m else ""}>{m.value.capitalize()}</option>'
                for m in NPCMovementLogic
            )
            room_options = '<option value="">— None —</option>' + "".join(
                f'<option value="{rid}" {"selected" if str(group.current_room_id) == rid else ""}>{_html.escape(rname)}</option>'
                for rid, rname in sorted(room_name_map.items(), key=lambda x: x[1])
            )
            possible_checkboxes = "".join(
                f'<label style="display:flex;align-items:center;gap:0.4rem;font-size:0.82rem;margin-top:2px"><input type="checkbox" name="possible_rooms" value="{rid}" {"checked" if _UUID(rid) in group.possible_rooms else ""}> {_html.escape(rname)}</label>'
                for rid, rname in sorted(room_name_map.items(), key=lambda x: x[1])
            )
            group_header = f"""
<form hx-post="/session/{channel_id}/group/{gid}/update"
      hx-target="#npc-roster" hx-swap="outerHTML">
  <div class="row">
    <div><label>Group Name</label><input type="text" name="name" value="{_e_gname}" placeholder="(optional)"></div>
    <div style="flex:0;min-width:130px"><label>Movement</label>
    <select name="movement_logic">{logic_options}</select></div>
    <div style="flex:0;min-width:160px"><label>Current Room</label>
    <select name="current_room_id">{room_options}</select></div>
  </div>
  <details style="margin-top:0.5rem"><summary style="cursor:pointer;font-size:0.82rem;color:#aaa">Possible rooms</summary>
  <div style="margin-top:0.3rem">{possible_checkboxes or "<span class='muted'>No rooms in dungeon.</span>"}</div>
  </details>
  <div class="row" style="margin-top:0.5rem">
    <button type="submit">Save Group</button>
    <a href="/session/{channel_id}/npcs" style="align-self:center;font-size:0.85rem;color:#888">cancel</a>
  </div>
</form>"""
        else:
            group_header = f"""
<div style="display:flex;gap:0.5rem;align-items:center;flex-wrap:wrap">
  <strong>{group_display_name}</strong>
  <span class="muted" style="font-size:0.8rem">{logic_label} &bull; {room_name} &bull; {npc_count} NPC(s)</span>
  <a href="/session/{channel_id}/npcs?edit=group:{gid}" class="btn-sm">Edit Group</a>
  <form style="display:inline" hx-post="/session/{channel_id}/group/{gid}/delete"
        hx-target="#npc-roster" hx-swap="outerHTML"
        hx-confirm="Delete group '{group.name or 'unnamed'}' and all its NPCs?">
    <button class="btn-sm btn-danger" type="submit">Delete Group</button>
  </form>
</div>"""

        npc_rows = ""
        for npc in group.npcs:
            nid = str(npc.npc_id)
            _e_nname = _html.escape(npc.name)
            _e_ndesc = _html.escape(npc.description or "")
            if edit_id == f"npc:{nid}":
                _eq_name = _html.escape(npc.name, quote=True)
                _eq_desc = _html.escape(npc.description or "", quote=True)
                _eq_notes = _html.escape(npc.notes or "", quote=True)
                _eq_dmg = _html.escape(npc.damage_dice or "1d6", quote=True)
                npc_rows += f"""
<tr>
  <td colspan="8">
    <form hx-post="/session/{channel_id}/npc/{nid}/update"
          hx-target="#npc-roster" hx-swap="outerHTML">
      <input type="hidden" name="from_page" value="npcs">
      <div class="row">
        <div><label>Name</label><input type="text" name="name" value="{_eq_name}" required></div>
        <div><label>HP Max</label><input type="number" name="hp_max" value="{npc.hp_max}" min="1" style="width:60px"></div>
        <div><label>HP Now</label><input type="number" name="hp_current" value="{npc.hp_current}" min="0" style="width:60px"></div>
        <div><label>DEF</label><input type="number" name="defense" value="{npc.defense}" min="0" style="width:55px"></div>
        <div><label>RES</label><input type="number" name="resistance" value="{npc.resistance}" min="0" style="width:55px"></div>
        <div><label>HD</label><input type="number" name="hit_dice" value="{npc.hit_dice}" min="1" style="width:55px"></div>
        <div><label>Range</label><input type="number" name="weapon_range" value="{npc.weapon_range}" min="0" style="width:55px"></div>
        <div><label>Damage</label><input type="text" name="damage_dice" value="{_eq_dmg}" style="width:70px"></div>
      </div>
      <div class="row">
        <div><label>Description</label><input type="text" name="description" value="{_eq_desc}"></div>
        <div><label>Notes</label><input type="text" name="notes" value="{_eq_notes}"></div>
      </div>
      <div class="row" style="margin-top:0.5rem">
        <button type="submit">Save NPC</button>
        <a href="/session/{channel_id}/npcs" style="align-self:center;font-size:0.85rem;color:#888">cancel</a>
      </div>
    </form>
  </td>
</tr>"""
            else:
                npc_rows += f"""
<tr>
  <td><strong>{_e_nname}</strong><br><span class="muted">{_e_ndesc}</span></td>
  <td class="hp-bar">{npc.hp_current}/{npc.hp_max}</td>
  <td>{npc.defense}</td>
  <td>{npc.resistance}</td>
  <td>{npc.damage_dice}</td>
  <td>{npc.hit_dice}</td>
  <td>{npc.weapon_range}</td>
  <td style="white-space:nowrap">
    <a href="/session/{channel_id}/npcs?edit=npc:{nid}" class="btn-sm">Edit</a>
    <form style="display:inline" hx-post="/session/{channel_id}/npc/{nid}/copy"
          hx-target="#npc-roster" hx-swap="outerHTML">
      <input type="hidden" name="from_page" value="npcs">
      <button class="btn-sm" type="submit">Copy</button>
    </form>
    <form style="display:inline" hx-post="/session/{channel_id}/npc/{nid}/delete"
          hx-target="#npc-roster" hx-swap="outerHTML"
          hx-confirm="Remove {_html.escape(npc.name, quote=True)}?">
      <input type="hidden" name="from_page" value="npcs">
      <button class="btn-sm btn-danger" type="submit">Del</button>
    </form>
  </td>
</tr>"""

        npc_table = f"""
<table style="margin-top:0.5rem;font-size:0.85rem">
  <tr><th>NPC</th><th>HP</th><th>DEF</th><th>RES</th><th>DMG</th><th>HD</th><th>Rng</th><th>Actions</th></tr>
  {npc_rows}
</table>""" if npc_rows else '<p class="muted" style="font-size:0.85rem;margin-top:0.5rem">No NPCs in this group.</p>'

        add_npc_form = f"""
<details style="margin-top:0.5rem"><summary style="cursor:pointer;font-size:0.82rem;color:#aaa">+ Add NPC to group</summary>
<form hx-post="/session/{channel_id}/group/{gid}/addnpc"
      hx-target="#npc-roster" hx-swap="outerHTML" style="margin-top:0.5rem">
  <div class="row">
    <div><label>Name</label><input type="text" name="name" placeholder="Goblin A" required></div>
    <div><label>HP</label><input type="number" name="hp" value="4" min="1" style="width:65px"></div>
    <div><label>DEF</label><input type="number" name="defense" value="0" min="0" style="width:55px"></div>
    <div><label>RES</label><input type="number" name="resistance" value="0" min="0" style="width:55px"></div>
    <div><label>Damage</label><input type="text" name="damage_dice" value="1d6" style="width:70px"></div>
    <div><label>HD</label><input type="number" name="hit_dice" value="1" min="1" style="width:55px"></div>
    <div><label>Range</label><input type="number" name="weapon_range" value="0" min="0" style="width:55px"></div>
  </div>
  <div><label>Description</label><input type="text" name="description" placeholder="Brief description"></div>
  <button type="submit" style="margin-top:0.4rem">Add NPC</button>
</form>
</details>"""

        promote_form = f"""
<form style="display:inline" hx-post="/session/{channel_id}/group/{gid}/promote_encounter"
      hx-target="#npc-roster" hx-swap="outerHTML" style="margin-top:0.3rem">
  <input type="number" name="weight" value="1" min="1" style="width:50px;display:inline;padding:2px 4px">
  <button class="btn-sm" type="submit" title="Add a copy of this group to the random encounter roster">+ Encounter Roster</button>
</form>"""

        cards += f"""
<div class="card" style="margin-bottom:0.75rem">
  {group_header}
  {npc_table}
  {add_npc_form}
  <div style="margin-top:0.5rem;padding-top:0.5rem;border-top:1px solid #0f3460;font-size:0.82rem;color:#888">
    Add to encounter roster with weight: {promote_form}
  </div>
</div>"""

    new_group_form = f"""
<div class="card">
  <div class="section-header"><h3 style="font-size:0.95rem">Create New Group</h3></div>
  <form hx-post="/session/{channel_id}/addgroup"
        hx-target="#npc-roster" hx-swap="outerHTML">
    <div class="row">
      <div><label>Group Name (optional)</label><input type="text" name="group_name" placeholder="e.g. Goblin Patrol"></div>
      <div style="flex:0;min-width:130px"><label>Movement</label>
      <select name="movement_logic">
        {''.join(f'<option value="{m.value}">{m.value.capitalize()}</option>' for m in NPCMovementLogic)}
      </select></div>
    </div>
    <hr class="divider" style="margin:0.5rem 0">
    <p style="font-size:0.82rem;color:#aaa;margin:0 0 0.4rem">First NPC (required):</p>
    <div class="row">
      <div><label>Name</label><input type="text" name="name" placeholder="Goblin A" required></div>
      <div><label>HP</label><input type="number" name="hp" value="4" min="1" style="width:65px"></div>
      <div><label>DEF</label><input type="number" name="defense" value="0" min="0" style="width:55px"></div>
      <div><label>RES</label><input type="number" name="resistance" value="0" min="0" style="width:55px"></div>
      <div><label>Damage</label><input type="text" name="damage_dice" value="1d6" style="width:70px"></div>
      <div><label>HD</label><input type="number" name="hit_dice" value="1" min="1" style="width:55px"></div>
      <div><label>Range</label><input type="number" name="weapon_range" value="0" min="0" style="width:55px"></div>
    </div>
    <div><label>Description</label><input type="text" name="description" placeholder="Brief description"></div>
    <button type="submit" style="margin-top:0.5rem">Create Group</button>
  </form>
</div>"""

    no_groups_html = '<div class="card"><p class="muted">No NPC groups in the dungeon yet.</p></div>' if not cards else ""
    return f"""
<h3 style="color:#c9a84c;margin-bottom:0.5rem">NPC Groups</h3>
{no_groups_html}
{cards}
{new_group_form}"""


def _encounter_settings_section(state: GameState, channel_id: str) -> str:
    if not state.dungeon:
        return ""
    dungeon = state.dungeon
    _e_roll = _html.escape(dungeon.random_encounter_roll or "1d6", quote=True)
    return f"""
<div class="card">
  <div class="section-header"><h3>Random Encounter Settings</h3></div>
  <form hx-post="/session/{channel_id}/dungeon/update"
        hx-target="#npc-roster" hx-swap="outerHTML">
    <input type="hidden" name="name" value="{_html.escape(dungeon.name, quote=True)}">
    <input type="hidden" name="description" value="{_html.escape(dungeon.description or '', quote=True)}">
    <input type="hidden" name="from_page" value="npcs">
    <div class="row">
      <div><label>Check every N turns</label>
      <input type="number" name="random_encounter_interval" value="{dungeon.random_encounter_interval}" min="1" style="width:80px"></div>
      <div><label>Roll expression</label>
      <input type="text" name="random_encounter_roll" value="{_e_roll}" style="width:80px"></div>
    </div>
    <p class="muted" style="margin:0.3rem 0 0.5rem">Encounter fires when roll &le; 1 (modified by room hazard). Roll checked after every N turns.</p>
    <button type="submit">Save Settings</button>
  </form>
</div>"""


def _encounter_roster_section(state: GameState, channel_id: str, edit_id: str) -> str:
    if not state.dungeon:
        return ""
    roster = state.dungeon.random_encounter_roster

    rows = ""
    for entry in roster:
        eg = entry.npc_group
        egid = str(eg.group_id)
        group_display = _html.escape(eg.name or "(unnamed)")
        npc_count = len(eg.npcs)

        npc_detail_rows = ""
        for npc in eg.npcs:
            nid = str(npc.npc_id)
            _e_nname = _html.escape(npc.name)
            if edit_id == f"enc_npc:{nid}":
                _eq_name = _html.escape(npc.name, quote=True)
                _eq_desc = _html.escape(npc.description or "", quote=True)
                _eq_dmg = _html.escape(npc.damage_dice or "1d6", quote=True)
                npc_detail_rows += f"""
<tr>
  <td colspan="7">
    <form hx-post="/session/{channel_id}/encounter_roster/{egid}/npc/{nid}/update"
          hx-target="#npc-roster" hx-swap="outerHTML">
      <div class="row">
        <div><label>Name</label><input type="text" name="name" value="{_eq_name}" required></div>
        <div><label>HP</label><input type="number" name="hp_max" value="{npc.hp_max}" min="1" style="width:60px"></div>
        <div><label>DEF</label><input type="number" name="defense" value="{npc.defense}" min="0" style="width:55px"></div>
        <div><label>RES</label><input type="number" name="resistance" value="{npc.resistance}" min="0" style="width:55px"></div>
        <div><label>HD</label><input type="number" name="hit_dice" value="{npc.hit_dice}" min="1" style="width:55px"></div>
        <div><label>Range</label><input type="number" name="weapon_range" value="{npc.weapon_range}" min="0" style="width:55px"></div>
        <div><label>Damage</label><input type="text" name="damage_dice" value="{_eq_dmg}" style="width:70px"></div>
      </div>
      <div><label>Description</label><input type="text" name="description" value="{_eq_desc}"></div>
      <div class="row" style="margin-top:0.4rem">
        <button type="submit">Save</button>
        <a href="/session/{channel_id}/npcs" style="align-self:center;font-size:0.85rem;color:#888">cancel</a>
      </div>
    </form>
  </td>
</tr>"""
            else:
                npc_detail_rows += f"""
<tr>
  <td><strong>{_e_nname}</strong></td>
  <td>{npc.hp_max}</td>
  <td>{npc.defense}</td>
  <td>{npc.resistance}</td>
  <td>{npc.hit_dice}</td>
  <td>{npc.weapon_range}</td>
  <td style="white-space:nowrap">
    <a href="/session/{channel_id}/npcs?edit=enc_npc:{nid}" class="btn-sm">Edit</a>
    <form style="display:inline" hx-post="/session/{channel_id}/encounter_roster/{egid}/npc/{nid}/delete"
          hx-target="#npc-roster" hx-swap="outerHTML"
          hx-confirm="Remove {_html.escape(npc.name, quote=True)} from template?">
      <button class="btn-sm btn-danger" type="submit">Del</button>
    </form>
  </td>
</tr>"""

        npc_subtable = f"""
<table style="font-size:0.82rem;margin-left:1rem;margin-top:0.3rem">
  <tr><th>NPC</th><th>HP</th><th>DEF</th><th>RES</th><th>HD</th><th>Rng</th><th>Actions</th></tr>
  {npc_detail_rows}
</table>"""

        add_to_entry_form = f"""
<details style="margin-left:1rem;margin-top:0.3rem"><summary style="cursor:pointer;font-size:0.8rem;color:#aaa">+ Add NPC to template</summary>
<form hx-post="/session/{channel_id}/encounter_roster/{egid}/addnpc"
      hx-target="#npc-roster" hx-swap="outerHTML" style="margin-top:0.4rem">
  <div class="row">
    <div><label>Name</label><input type="text" name="name" placeholder="Goblin A" required></div>
    <div><label>HP</label><input type="number" name="hp" value="4" min="1" style="width:65px"></div>
    <div><label>DEF</label><input type="number" name="defense" value="0" min="0" style="width:55px"></div>
    <div><label>Damage</label><input type="text" name="damage_dice" value="1d6" style="width:70px"></div>
    <div><label>HD</label><input type="number" name="hit_dice" value="1" min="1" style="width:55px"></div>
  </div>
  <button type="submit" style="margin-top:0.3rem">Add NPC</button>
</form></details>"""

        rows += f"""
<div style="border:1px solid #0f3460;border-radius:6px;padding:0.75rem;margin-bottom:0.5rem">
  <div style="display:flex;gap:0.5rem;align-items:center;flex-wrap:wrap">
    <strong>{group_display}</strong>
    <span class="muted" style="font-size:0.8rem">{npc_count} NPC(s)</span>
    <form style="display:inline;display:flex;gap:0.3rem;align-items:center"
          hx-post="/session/{channel_id}/encounter_roster/{egid}/update_weight"
          hx-target="#npc-roster" hx-swap="outerHTML">
      <label style="margin:0;font-size:0.8rem;color:#aaa">Weight:</label>
      <input type="number" name="weight" value="{entry.weight}" min="1" style="width:55px;display:inline;padding:2px 4px">
      <button class="btn-sm" type="submit">Set</button>
    </form>
    <form style="display:inline" hx-post="/session/{channel_id}/encounter_roster/{egid}/delete"
          hx-target="#npc-roster" hx-swap="outerHTML"
          hx-confirm="Remove '{_html.escape(eg.name or 'unnamed', quote=True)}' from encounter roster?">
      <button class="btn-sm btn-danger" type="submit">Remove</button>
    </form>
  </div>
  {npc_subtable}
  {add_to_entry_form}
</div>"""

    add_entry_form = f"""
<hr class="divider">
<div class="section-header"><h3 style="font-size:0.95rem">Add New Encounter Entry</h3></div>
<form hx-post="/session/{channel_id}/encounter_roster/add"
      hx-target="#npc-roster" hx-swap="outerHTML">
  <div class="row">
    <div><label>Group Name (optional)</label><input type="text" name="group_name" placeholder="e.g. Goblin Patrol"></div>
    <div style="flex:0;min-width:80px"><label>Weight</label><input type="number" name="weight" value="1" min="1" style="width:65px"></div>
  </div>
  <p style="font-size:0.82rem;color:#aaa;margin:0.4rem 0">First template NPC:</p>
  <div class="row">
    <div><label>Name</label><input type="text" name="name" placeholder="Goblin A" required></div>
    <div><label>HP</label><input type="number" name="hp" value="4" min="1" style="width:65px"></div>
    <div><label>DEF</label><input type="number" name="defense" value="0" min="0" style="width:55px"></div>
    <div><label>RES</label><input type="number" name="resistance" value="0" min="0" style="width:55px"></div>
    <div><label>Damage</label><input type="text" name="damage_dice" value="1d6" style="width:70px"></div>
    <div><label>HD</label><input type="number" name="hit_dice" value="1" min="1" style="width:55px"></div>
    <div><label>Range</label><input type="number" name="weapon_range" value="0" min="0" style="width:55px"></div>
  </div>
  <div><label>Description</label><input type="text" name="description" placeholder="Brief description"></div>
  <button type="submit" style="margin-top:0.5rem">Add to Encounter Roster</button>
</form>"""

    entry_count = len(roster)
    return f"""
<div class="card">
  <div class="section-header"><h3>Random Encounter Roster</h3>
    <span class="muted" style="font-size:0.85rem">{entry_count} entr{'y' if entry_count == 1 else 'ies'}</span>
  </div>
  {rows or '<p class="muted">No encounter entries yet.</p>'}
  {add_entry_form}
</div>"""


# ---------------------------------------------------------------------------
# Archive browser page
# ---------------------------------------------------------------------------

def archive_page(
    sessions: list[tuple[str, str]],
    entries: list[dict],
    flash: str = "",
    error: str = "",
) -> str:
    flash_html = f'<div class="flash">{flash}</div>' if flash else ""
    error_html = f'<div class="error">{error}</div>' if error else ""

    if not entries:
        rows_html = '<p class="muted">No archived sessions yet.</p>'
    else:
        rows = ""
        for e in entries:
            sid       = e["session_id"]
            cname     = e["channel_name"] or e["channel_id"]
            dm        = e["dm_user_id"] or "—"
            turns     = e["turn_number"]
            created   = (e["created_at"]  or "")[:10]
            archived  = (e["archived_at"] or "")[:10]
            rows += f"""
<tr>
  <td>
    <strong>#{cname}</strong><br>
    <span class="muted" style="font-size:0.75rem">{sid}</span>
  </td>
  <td class="muted">{dm}</td>
  <td style="text-align:center">{turns}</td>
  <td class="muted">{created}</td>
  <td class="muted">{archived}</td>
  <td style="white-space:nowrap;min-width:220px">
    <form hx-post="/archive/{sid}/resurrect"
          hx-target="body" hx-swap="innerHTML">
      <div class="row" style="margin-bottom:4px">
        <input type="text" name="channel_id" value="{e["channel_id"]}"
               placeholder="Discord channel ID"
               style="font-size:0.8rem;width:160px"
               title="Paste the target Discord channel ID. Original: {e["channel_id"]}">
        <button class="btn-sm btn-success" type="submit">Resurrect</button>
      </div>
    </form>
    <form hx-post="/archive/{sid}/delete"
          hx-target="body" hx-swap="innerHTML"
          hx-confirm="Permanently delete this archive entry? This cannot be undone.">
      <button class="btn-sm btn-danger" type="submit">Delete</button>
    </form>
  </td>
</tr>"""
        rows_html = f"""
<table>
  <tr>
    <th>Channel</th>
    <th>DM</th>
    <th>Turns</th>
    <th>Started</th>
    <th>Archived</th>
    <th>Actions</th>
  </tr>
  {rows}
</table>"""

    body = f"""
<div class="layout">
  {_sidebar(None, sessions)}
  <div class="main">
    {flash_html}{error_html}
    <div class="card">
      <div class="section-header"><h3>&#128196; Session Archive</h3></div>
      {rows_html}
    </div>
  </div>
</div>"""
    return page("Archive — DM Panel", body)

# ---------------------------------------------------------------------------
# Character browser page
# ---------------------------------------------------------------------------

def character_page(
        sessions: list[tuple[str, str]],
        entries: dict,
        flash: str = "",
        error: str = "",
        view_char_id: str = "",
        ) -> str:
    flash_html = f'<div class="flash">{flash}</div>' if flash else ""
    error_html = f'<div class="error">{error}</div>' if error else ""
    char_sheet_html =""

    if not entries:
        rows_html = '<p class="muted">No characters available.</p>'
    else:
        rows = ""
        for e in entries.values():
            cid       = e.character_id
            cname     = e.name
            cclass    = e.character_class.value
            clevel    = e.level
            status    = e.status.value
            created   = e.created_at.strftime("%Y/%m/%d")
            rows += f"""
<tr>
  <td>
    <strong>{cname}</strong><br>
    <span class="muted" style="font-size:0.75rem">{cid}</span>
  </td>
  <td class="muted">{cclass}</td>
  <td style="text-align:center">{clevel}</td>
  <td class="muted">{status}</td>
  <td class="muted">{created}</td>
  <td style="white-space:nowrap;min-width:220px">
    <a href="/characters?view_char={cid}" class="btn btn-sm btn-success">Show</a>
  </td>
</tr>"""

            if str(e.character_id) == view_char_id:
                char_sheet_html = f"""
    <div class="card">
      {character_sheet_panel(e)}
    </div>"""

        rows_html = f"""
<table>
  <tr>
    <th>Name</th>
    <th>Class</th>
    <th>Level</th>
    <th>Status</th>
    <th>Created</th>
    <th>Actions</th>
  </tr>
  {rows}
</table>"""

    body = f"""
<div class="layout">
  {_sidebar(None, sessions)}
  <div class="main">
    {flash_html}{error_html}
    <div class="card">
      <div class="section-header"><h3>Character Sheet Browser</h3></div>
      {rows_html}
    </div>
    {char_sheet_html}
  </div>
</div>"""
    return page("Characters — DM Panel", body)

def _xp_next(level: int) -> str:
    """Return the XP threshold for the next level, or '—' if at max."""
    idx = level  # XP_THRESHOLDS[level] = XP needed to reach level+1
    if idx < len(XP_THRESHOLDS):
        return str(XP_THRESHOLDS[idx])
    return "max"


def _sheet_stat_grid(stats: list[tuple[str, str]]) -> str:
    """Full-width grid of label/value stat cells."""
    cols = len(stats)
    cells = "".join(
        f'<div style="background:#0f1e36;border:1px solid #0f3460;border-radius:6px;'
        f'padding:0.5rem 0.25rem;text-align:center">'
        f'<div style="font-size:0.7rem;color:#888;text-transform:uppercase;'
        f'letter-spacing:0.05em;margin-bottom:0.25rem">{label}</div>'
        f'<div style="font-size:1rem;font-weight:bold;color:#e0e0e0">{value}</div>'
        f'</div>'
        for label, value in stats
    )
    return (
        f'<div style="display:grid;grid-template-columns:repeat({cols},1fr);'
        f'gap:0.4rem;margin-bottom:0.75rem">{cells}</div>'
    )


def character_sheet_panel(character: Character) -> str:
    ability_grid = _sheet_stat_grid([
        ("Physique", f"{character.effective_stat('physique'):+d}"),
        ("Finesse",  f"{character.effective_stat('finesse'):+d}"),
        ("Reason",   f"{character.effective_stat('reason'):+d}"),
        ("Savvy",    f"{character.effective_stat('savvy'):+d}"),
    ])
    combat_grid = _sheet_stat_grid([
        ("HP",   f"{character.hp_current}/{character.hp_max}"),
        ("Def",  character.defense),
        ("Res",  character.resistance),
        ("Move", f"{character.movement_speed}'"),
        ("Save", character.saving_throws.get("save", "—")),
    ])

    status_tag = ""
    if character.status == CharacterStatus.DEAD:
        status_tag = ' <span class="tag tag-dead">DEAD</span>'
    elif character.status_notes:
        status_tag = f' <span class="muted" style="font-size:0.8rem">({character.status_notes})</span>'

    cid_str = str(character.character_id)
    # Map container_id → contained InventoryItems for nested display.
    _contained_map: dict[str, list] = {}
    for _ci in character.inventory:
        if _ci.container_id:
            _contained_map.setdefault(_ci.container_id, []).append(_ci)

    def _recharge_tag(period) -> str:
        """Return a small [D] or [E] pill for DAY/ENCOUNTER spells; empty string otherwise."""
        if period == RechargePeriod.DAY:
            return ' <span class="muted" style="font-size:0.7rem;border:1px solid #555;border-radius:3px;padding:0 3px" title="Daily recharge">D</span>'
        if period == RechargePeriod.ENCOUNTER:
            return ' <span class="muted" style="font-size:0.7rem;border:1px solid #555;border-radius:3px;padding:0 3px" title="Encounter recharge">E</span>'
        return ""

    def _charge_controls(item_id: str, charges: int | None, max_charges: int, recharge_period=None, charge_endpoint: str = "spellcharge", recharge_endpoint: str = "spellrecharge", extra_fields: str = "") -> str:
        """Return inline charge badge + +/- and restore buttons for finite-charge items."""
        if charges is None or max_charges < 0:
            return ""
        badge = f'<span class="muted" style="font-size:0.8rem">({charges}/{max_charges})</span>'
        period_tag = _recharge_tag(recharge_period)
        minus_form = (
            f'<form method="post" action="/characters/{cid_str}/{charge_endpoint}" style="margin:0;display:inline">'
            f'<input type="hidden" name="item_id" value="{item_id}">'
            f'<input type="hidden" name="delta" value="-1">'
            f'{extra_fields}'
            f'<button class="btn-sm" type="submit" style="padding:1px 5px">−</button>'
            f'</form>'
        )
        plus_form = (
            f'<form method="post" action="/characters/{cid_str}/{charge_endpoint}" style="margin:0;display:inline">'
            f'<input type="hidden" name="item_id" value="{item_id}">'
            f'<input type="hidden" name="delta" value="1">'
            f'{extra_fields}'
            f'<button class="btn-sm" type="submit" style="padding:1px 5px">+</button>'
            f'</form>'
        )
        restore_form = (
            f'<form method="post" action="/characters/{cid_str}/{recharge_endpoint}" style="margin:0;display:inline">'
            f'<input type="hidden" name="item_id" value="{item_id}">'
            f'{extra_fields}'
            f'<button class="btn-sm" type="submit" style="padding:1px 5px" title="Restore to max">↺</button>'
            f'</form>'
        )
        return f' {badge}{period_tag} {minus_form}{plus_form}{restore_form}'

    def _skill_charge_controls(skill_id: str, current: int, max_uses: int, recharge_period: str | None = None) -> str:
        """Return inline uses badge + +/- and restore buttons for limited-use skills."""
        period_tag_str = ""
        if recharge_period == "encounter":
            period_tag_str = ' <span class="muted" style="font-size:0.7rem;border:1px solid #555;border-radius:3px;padding:0 3px" title="Encounter recharge">E</span>'
        elif recharge_period == "day":
            period_tag_str = ' <span class="muted" style="font-size:0.7rem;border:1px solid #555;border-radius:3px;padding:0 3px" title="Daily recharge">D</span>'
        badge = f'<span class="muted" style="font-size:0.8rem">({current}/{max_uses})</span>'
        minus_form = (
            f'<form method="post" action="/characters/{cid_str}/skillcharge" style="margin:0;display:inline">'
            f'<input type="hidden" name="skill_id" value="{skill_id}">'
            f'<input type="hidden" name="delta" value="-1">'
            f'<button class="btn-sm" type="submit" style="padding:1px 5px">−</button>'
            f'</form>'
        )
        plus_form = (
            f'<form method="post" action="/characters/{cid_str}/skillcharge" style="margin:0;display:inline">'
            f'<input type="hidden" name="skill_id" value="{skill_id}">'
            f'<input type="hidden" name="delta" value="1">'
            f'<button class="btn-sm" type="submit" style="padding:1px 5px">+</button>'
            f'</form>'
        )
        restore_form = (
            f'<form method="post" action="/characters/{cid_str}/skillrecharge" style="margin:0;display:inline">'
            f'<input type="hidden" name="skill_id" value="{skill_id}">'
            f'<button class="btn-sm" type="submit" style="padding:1px 5px" title="Restore to max">↺</button>'
            f'</form>'
        )
        return f' {badge}{period_tag_str} {minus_form}{plus_form}{restore_form}'

    item_rows = ""
    for inv_item in character.inventory:
        if inv_item.container_id:
            continue  # rendered under its container below
        defn = ITEM_REGISTRY.get(inv_item.item_id)
        item_name = defn.name if defn else inv_item.item_id
        qty_str = f'<span class="muted"> ×{inv_item.quantity}</span>' if inv_item.quantity > 1 else ""
        equip_str = ' <span class="tag tag-open" style="font-size:0.7rem;padding:1px 5px">equip</span>' if inv_item.equipped else ""
        # Show charge controls for standalone ChargeWeapons with finite charges.
        # Also show a charge badge for light-emitting items (torches, lanterns).
        standalone_charges = ""
        if (isinstance(defn, ChargeWeapon)
                and inv_item.charges is not None
                and defn.maxCharges >= 0):
            standalone_charges = _charge_controls(
                inv_item.item_id, inv_item.charges, defn.maxCharges,
                recharge_period=defn.rechargePeriod,
            )
        elif (isinstance(defn, ChargeWeapon)
              and inv_item.charges is not None
              and defn.maxCharges < 0):
            standalone_charges = ' <span class="muted" style="font-size:0.8rem">(∞)</span>'
        elif (defn is not None
              and getattr(defn, "max_light_turns", None) is not None
              and inv_item.charges is not None):
            _equipped_field = f'<input type="hidden" name="equipped" value="{"1" if inv_item.equipped else "0"}">'
            standalone_charges = _charge_controls(
                inv_item.item_id, inv_item.charges, defn.max_light_turns,
                charge_endpoint="lightcharge",
                recharge_endpoint="lightrecharge",
                extra_fields=_equipped_field,
            )
        item_rows += (
            f'<tr>'
            f'<td>{item_name}{qty_str}{equip_str}{standalone_charges}</td>'
            f'<td style="width:1%;white-space:nowrap">'
            f'<form method="post" action="/characters/{cid_str}/removeitem" style="margin:0">'
            f'<input type="hidden" name="item_id" value="{inv_item.item_id}">'
            f'<input type="hidden" name="quantity" value="{inv_item.quantity}">'
            f'<button class="btn-sm btn-danger" type="submit">✕</button>'
            f'</form>'
            f'</td>'
            f'</tr>'
        )
        for _child in _contained_map.get(inv_item.instance_id, []):
            _cdefn = ITEM_REGISTRY.get(_child.item_id)
            _cname = _cdefn.name if _cdefn else _child.item_id
            if _child.charges is not None and _cdefn is not None and hasattr(_cdefn, "maxCharges"):
                if _cdefn.maxCharges < 0:
                    _charges_display = ' <span class="muted" style="font-size:0.8rem">(∞)</span>'
                else:
                    _charges_display = _charge_controls(
                        _child.item_id, _child.charges, _cdefn.maxCharges,
                        recharge_period=getattr(_cdefn, "rechargePeriod", None),
                    )
            else:
                _charges_display = ""
            item_rows += (
                f'<tr style="opacity:0.85">'
                f'<td style="padding-left:1.5rem;font-size:0.9rem">'
                f'<span class="muted">└</span> {_cname}{_charges_display}'
                f'</td>'
                f'<td></td>'
                f'</tr>'
            )
    empty_row = '<tr><td colspan="2" class="muted">Empty</td></tr>'

    item_options = "\n".join(
        f'<option value="{iid}">{item.name}</option>'
        for iid, item in sorted(ITEM_REGISTRY.items(), key=lambda x: x[1].name)
    )

    slots_pct = int(character.slots_used / character.inventory_size * 100) if character.inventory_size else 0
    bar_color = "#c9a84c" if slots_pct < 80 else "#f44336"

    active_skills = CharacterManager.get_active_skills(character)
    if active_skills:
        skill_rows = ""
        for s in active_skills:
            if s.uses is not None:
                job_exp = character.jobs.get(s.source)
                job_level = job_exp.level if job_exp else character.level
                max_uses = CharacterManager.get_skill_max_uses(s, job_level)
                current_uses = character.skill_uses.get(s.skill_id, max_uses)
                uses_controls = _skill_charge_controls(s.skill_id, current_uses, max_uses, s.recharge_period)
            else:
                uses_controls = ""
            skill_rows += (
                f'<tr>'
                f'<td style="font-weight:500;white-space:nowrap;padding-right:1rem">{s.name}{uses_controls}</td>'
                f'<td class="muted" style="font-size:0.9rem">{s.description}</td>'
                f'</tr>'
            )
    else:
        skill_rows = '<tr><td colspan="2" class="muted">None</td></tr>'
    skills_html = f'<table style="margin-bottom:0.75rem">{skill_rows}</table>'

    return f"""
<div class="card" style="max-width:640px">
  <div class="section-header" style="margin-bottom:0.25rem">
    <h2 style="margin:0;font-size:1.3rem">{character.name}{status_tag}</h2>
    <span class="muted">{character.gold} gp</span>
  </div>
  <div class="muted" style="margin-bottom:1rem">
    {character.character_class.value} &nbsp;·&nbsp;
    Level {character.level} &nbsp;·&nbsp;
    {character.experience} XP
  </div>

  <div style="font-size:0.75rem;color:#888;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:0.4rem">Ability Scores</div>
  {ability_grid}

  <div style="font-size:0.75rem;color:#888;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:0.4rem">Combat</div>
  {combat_grid}

  <div style="font-size:0.75rem;color:#888;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:0.4rem">Skills</div>
  {skills_html}

  <hr class="divider">

  <div class="section-header" style="margin-bottom:0.5rem">
    <h3 style="margin:0">Inventory</h3>
    <div style="text-align:right">
      <span class="muted" style="font-size:0.8rem">{character.slots_used}/{character.inventory_size} slots</span>
      <div style="width:80px;height:4px;background:#0f3460;border-radius:2px;margin-top:3px">
        <div style="width:{slots_pct}%;height:100%;background:{bar_color};border-radius:2px"></div>
      </div>
    </div>
  </div>
  <table style="margin-bottom:0.75rem">
    {item_rows if item_rows else empty_row}
  </table>
  <form method="post" action="/characters/{cid_str}/additem">
    <div class="row">
      <select name="item_id">{item_options}</select>
      <input type="number" name="quantity" value="1" min="1" style="width:70px;flex:0">
      <button type="submit">Add item</button>
    </div>
  </form>
  <div style="margin-top:0.5rem">
    <form method="post" action="/characters/{cid_str}/rechargedaily" style="margin:0;display:inline">
      <button class="btn-sm" type="submit">Recharge All (Day)</button>
    </form>
  </div>

  <hr class="divider">

  <div class="section-header" style="margin-bottom:0.5rem">
    <h3 style="margin:0">Experience</h3>
    <span class="muted" style="font-size:0.8rem">{character.experience} / {_xp_next(character.level)} XP</span>
  </div>
  <form method="post" action="/characters/{cid_str}/addxp">
    <div class="row">
      <input type="number" name="amount" value="100" min="1" style="width:100px;flex:0">
      <button type="submit">Award XP</button>
    </div>
  </form>
</div>"""
