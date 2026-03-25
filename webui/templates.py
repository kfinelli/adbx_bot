"""
webui/templates.py — HTML rendering functions for the DM control panel.

HTMX note: hx-include selects by CSS selector and sends fields by their
`name` attribute. All inputs that are included via hx-include must have
a `name` attribute matching what the server expects.
"""

from __future__ import annotations

from models import (
    Character,
    CharacterStatus,
    DoorState,
    GameState,
    InventoryItem,
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
</head>
<body>
{body}
</body>
</html>"""


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
    <div id="dashboard">
      {dashboard_fragment(state, flash, error, view_room_id, edit_id)}
    </div>
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

    # Submissions table
    subs_html = ""
    if turn and state.party:
        rows = ""
        for cid in state.party.member_ids:
            char = state.characters.get(cid)
            if not char:
                continue
            sub = state.latest_submission(cid)
            sub_text = f'<em>"{sub.action_text}"</em>' if sub else '<span class="muted">—</span>'
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
    cond_chips = ""
    for cond in cs.active_conditions:
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

    light = state.party.active_light
    light_str = (
        f"{light.label}: {light.turns_remaining if light.turns_remaining is not None else '&infin;'} turns"
        if light else "No light source"
    )

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
    {combat_sub}
  </td>
</tr>"""

    light_html = f"""
<hr class="divider">
<div class="section-header"><h3>Light Source</h3></div>
<p class="muted">{light_str}</p>
<form hx-post="/session/{channel_id}/setlight"
      hx-target="#dashboard" hx-swap="outerHTML">
  <div class="row">
    <div><label>Label</label>
    <input type="text" name="label" placeholder="Torch"></div>
    <div><label>Turns (-1 = permanent)</label>
    <input type="number" name="turns" value="6"></div>
    <button type="submit">Set Light</button>
  </div>
</form>"""

    return f"""
<div class="card">
  <div class="section-header">
    <h3>Party</h3>
    <span class="muted">Gold: {state.party.gold} | XP: {state.party.experience}</span>
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
      <div><label>Add XP</label>
      <input type="number" name="amount" value="0" min="0"></div>
      <button type="submit">Add XP</button>
    </div>
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
        if ex.destination_id and state.dungeon:
            dest_room = state.dungeon.rooms.get(ex.destination_id)
            if dest_room:
                dest_name = f" \u2192 {dest_room.name}"
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
            exits_html += f"""
<tr>
  <td><strong>{i}. {ex.label.capitalize()}</strong>{dest_name}<br>
      <span class="muted">{ex.description}</span></td>
  <td style="white-space:nowrap">
    <form style="display:inline" hx-post="/session/{channel_id}/exit/{eid}/setstate"
          hx-target="#dashboard" hx-swap="outerHTML">
      <input type="hidden" name="view_room_id" value="{view_room_id}">
      <div class="row">
        <select name="door_state" onchange="this.form.requestSubmit()">{door_options}</select>
      </div>
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

    # --- Header summary + export
    if dungeon:
        safe_name = dungeon.name.replace(" ", "_").lower()
        summary = (
            f"<p><strong>{dungeon.name}</strong> &mdash; "
            f"{len(dungeon.rooms)} room(s)</p>"
        )
        export_btn = (
            f'<a class="btn btn-sm" href="/session/{channel_id}/dungeon/export" '
            f'download="{safe_name}.json">Export JSON</a>'
        )
    else:
        summary = '<p class="muted">No dungeon loaded.</p>'
        export_btn = ""

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
            rows += f"""
<tr>
  <td colspan="3">
    <form hx-post="/session/{channel_id}/npc/{nid}/update"
          hx-target="#dashboard" hx-swap="outerHTML">
      <input type="hidden" name="view_room_id" value="{view_room_id}">
      <div class="row">
        <div><label>Name</label><input type="text" name="name" value="{npc.name}" required></div>
        <div><label>HP Max</label><input type="number" name="hp_max" value="{npc.hp_max}" min="1" style="width:60px"></div>
        <div><label>HP Now</label><input type="number" name="hp_current" value="{npc.hp_current}" min="0" style="width:60px"></div>
        <div><label>DEF</label><input type="number" name="defense" value="{npc.defense}" min="0" style="width:55px"></div>
      </div>
      <label>Description</label>
      <input type="text" name="description" value="{npc.description}">
      <label>Notes</label>
      <input type="text" name="notes" value="{npc.notes or ''}">
      <div class="row" style="margin-top:0.5rem">
        <button type="submit">Save</button>
        <a href="{base_url}" style="align-self:center;font-size:0.85rem;color:#888">cancel</a>
      </div>
    </form>
  </td>
</tr>"""
        else:
            npc_combat_sub = _combat_subpanel(state, nid, channel_id, view_room_id, is_player=False)
            rows += f"""
<tr>
  <td><strong>{npc.name}</strong><br>
      <span class="muted">{npc.description}</span>
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
        <input type="text" name="status" value="{npc.status}" placeholder="status">
        <button class="btn-sm" type="submit">Status</button>
      </div>
    </form>
    <a href="{npc_base}" class="btn-sm">Edit</a>
    <form style="display:inline" hx-post="/session/{channel_id}/npc/{nid}/delete"
          hx-target="#dashboard" hx-swap="outerHTML"
          hx-confirm="Remove {npc.name}?">
      <input type="hidden" name="view_room_id" value="{view_room_id}">
      <button class="btn-sm btn-danger" type="submit">Del</button>
    </form>
  </td>
</tr>"""

    add_npc_html = f"""
<hr class="divider">
<div class="section-header"><h3>Add NPC</h3></div>
<form hx-post="/session/{channel_id}/addnpc"
      hx-target="#dashboard" hx-swap="outerHTML">
  <div class="row">
    <div><label>Name</label><input type="text" name="name" placeholder="Goblin A"></div>
    <div><label>HP</label><input type="number" name="hp" value="4" min="1"></div>
    <div><label>DEF</label><input type="number" name="defense" value="0" min="0"></div>
  </div>
  <div class="row">
    <div><label>Damage</label><input type="text" name="damage_dice" value="1d6"></div>
    <div><label>Description</label><input type="text" name="description" placeholder="Brief description"></div>
  </div>
  <label>DM Notes</label>
  <input type="text" name="notes" placeholder="DM-facing notes">
  <button type="submit">Add NPC</button>
</form>"""

    return f"""
<div class="card">
  <div class="section-header"><h3>NPCs</h3></div>
  {'<table><tr><th>NPC</th><th>HP</th><th>Controls</th></tr>' + rows + '</table>' if rows else '<p class="muted">No NPCs in this room.</p>'}
  {add_npc_html}
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

def _stat_block(stats: list[tuple[str, str]], cols: int | None = None, name: str | None = None) -> str:
    """Render a row of label/value pairs, e.g. [('HP', '8/10'), ('DEF', '5')]"""
    col_count = cols or len(stats)
    cells = "".join(
            f'<div style="text-align:center">'
            f'  <div class="muted" style="font-size:0.7rem">{label}</div>'
            f'  <div style="font-size:1.1rem;font-weight:bold">{value}</div>'
            f'</div>'
            for label, value in stats
            )
    header = (
            f'<div class="section-header" style="margin-bottom:0.5rem">'
            f'  <h3>{name}</h3>'
            f'</div>'
            ) if name else ""
    grid = (
            f'<div style="display:grid;'
            f'grid-template-columns:repeat({col_count},minmax(3rem,5rem));'
            f'gap:0.5rem;">'
            f'{cells}</div>'
            )
    return (
            f'<div style="padding:0.75rem;border:1px solid #0f3460;'
            f'border-radius:8px;margin:0.5rem 0; width:fit-content;">'
            f'{header}{grid}'
            f'</div>'
            )


def _display_inventory_item(item: InventoryItem) -> str:
    """Prepare a formatted name, quantity, equip indicator for an item."""
    quantity   = f"{item.quantity}x " if item.quantity > 1 else ""
    equipstatus = "(EQUIP) " if item.equipped else ""
    return f"{quantity}{equipstatus}{item.definition.name}"

def character_sheet_panel(
    character: Character,
) -> str:
    a = character.ability_scores
    def _fmt_stat(val: int) -> str:
        return f"{val / 100:+.2f}"
    ability_scores = _stat_block([
        ("PHY", _fmt_stat(a.physique)),
        ("FNS", _fmt_stat(a.finesse)),
        ("RSN", _fmt_stat(a.reason)),
        ("SVY", _fmt_stat(a.savvy)),
    ], name="Stats")
    hp_ac_movement = _stat_block([("HP", f"{character.hp_current}/{character.hp_max}"),
                                  ("DEF", character.defense),
                                  ("RES", character.resistance),
                                  ("Move", f"{character.movement_speed}'")], 1)
    saves = _stat_block([
        ("Save", character.saving_throws.get("save", "—")),
    ], 2, name="Saves")
    inv_cells = "".join(
            f'<div style="text-align:left">'
            f'  <div style="font-size:0.9rem">{_display_inventory_item(inv_item)}</div>'
            f'</div>'
            for inv_item in character.inventory
            )
    inventory = (
            f'<div style="padding:0.75rem;border:1px solid #0f3460;'
            f'width:fit-content;border-radius:8px;margin:0.5rem 0">'
            f'<div class="section-header" style="margin-bottom:0.5rem">'
            f'  <h3>Inventory</h3>'
            f'</div>'
            f'<div style="display:grid;'
            f'grid-template-columns:repeat(2,max-content);gap:0.5rem;">'
            f'{inv_cells}</div>'
            f'</div>'
            )

    return f"""
<div class="card">
<div class="section-header"> <h3>{character.name}</h3> </div>
<div class="muted">{character.character_class.value} — Level {character.level} &nbsp;·&nbsp; {character.experience}
XP &nbsp;·&nbsp; {character.gold} gp</div>
  {ability_scores}
  <div class="grid-2">
  {hp_ac_movement}
  {saves}
  </div>
  {inventory}
</div>"""
