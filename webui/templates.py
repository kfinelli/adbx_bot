"""
webui/templates.py — HTML rendering functions for the DM control panel.

HTMX note: hx-include selects by CSS selector and sends fields by their
`name` attribute. All inputs that are included via hx-include must have
a `name` attribute matching what the server expects.
"""

from __future__ import annotations
from models import (
    CharacterStatus, DoorState, GameState, NPC, Room,
    RoomFeature, Exit, TurnStatus, SessionMode,
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
    button, .btn {{
      background: #0f3460;
      border: 1px solid #1a4a8a;
      color: #e0e0e0;
      border-radius: 4px;
      padding: 0.35rem 0.8rem;
      cursor: pointer;
      font-size: 0.85rem;
      margin-top: 6px;
    }}
    button:hover, .btn:hover {{ background: #1a4a8a; }}
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
    .hp-bar {{ font-family: monospace; }}
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
  <h1 style="font-size:1.1rem; margin-bottom:1.5rem;">&#127922; DM Panel</h1>
  <h2>Sessions</h2>
  {links or '<p class="muted">No active sessions</p>'}
</div>"""


def session_list_page(sessions: list[tuple[str, str]]) -> str:
    body = f"""
<div class="layout">
  {_sidebar(None, sessions)}
  <div class="main">
    <h1>DM Control Panel</h1>
    <p class="muted">Select a session from the sidebar, or start one in Discord with /embark.</p>
  </div>
</div>"""
    return page("DM Panel", body)


def session_page(state: GameState, sessions: list[tuple[str, str]], flash: str = "", error: str = "") -> str:
    channel_id = state.platform_channel_id
    body = f"""
<div class="layout">
  {_sidebar(channel_id, sessions)}
  <div class="main">
    <div id="dashboard">
      {dashboard_fragment(state, flash, error)}
    </div>
  </div>
</div>"""
    return page(f"DM Panel", body)


def dashboard_fragment(state: GameState, flash: str = "", error: str = "") -> str:
    channel_id = state.platform_channel_id
    flash_html = f'<div class="flash">{flash}</div>' if flash else ""
    error_html = f'<div class="error">{error}</div>' if error else ""
    return f"""
<div id="dashboard">
  {flash_html}{error_html}
  <div class="grid-2">
    <div>
      {turn_panel(state)}
      {oracle_panel(state)}
      {party_panel(state)}
    </div>
    <div>
      {room_panel(state)}
      {npc_panel(state)}
    </div>
  </div>
</div>"""


# ---------------------------------------------------------------------------
# Turn panel
# ---------------------------------------------------------------------------

def turn_panel(state: GameState) -> str:
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
            rows += f"<tr><td>{char.name}</td><td>{sub_text}</td></tr>"
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

    # Timer controls — inputs inside forms so name attributes work cleanly
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

    if state.session_active:
        hold_html = f"""
<button class="btn-danger"
        hx-post="/session/{channel_id}/hold"
        hx-target="#dashboard" hx-swap="outerHTML"
        hx-confirm="Put session on hold?">Hold Session</button>"""
    else:
        hold_html = f"""
<button class="btn-success"
        hx-post="/session/{channel_id}/resume"
        hx-target="#dashboard" hx-swap="outerHTML">Resume Session</button>"""

    return f"""
<div class="card">
  <div class="section-header">
    <h3>Turn {state.turn_number} &mdash; {mode}</h3>
    {status_tag}
  </div>
  {due_str}
  {subs_html}
  {resolve_html}
  {timer_html}
  <hr class="divider">
  {hold_html}
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
    <span class="muted">Gold: {state.party.gold}</span>
  </div>
  <table>
    <tr><th>Character</th><th>HP</th><th>Status</th><th>Controls</th></tr>
    {rows}
  </table>
  {light_html}
</div>"""


# ---------------------------------------------------------------------------
# Room panel
# ---------------------------------------------------------------------------

def room_panel(state: GameState) -> str:
    channel_id = state.platform_channel_id
    room = state.current_room

    set_room_html = f"""
<hr class="divider">
<div class="section-header"><h3>Set / Update Room</h3></div>
<form hx-post="/session/{channel_id}/setroom"
      hx-target="#dashboard" hx-swap="outerHTML">
  <label>Name</label>
  <input type="text" name="name" value="{room.name if room else ''}" placeholder="Room name">
  <label>Description</label>
  <textarea name="description" placeholder="Player-visible description">{room.description if room else ''}</textarea>
  <label>DM Notes</label>
  <textarea name="notes" placeholder="DM-facing notes">{room.notes if room else ''}</textarea>
  <button type="submit">Set Room</button>
</form>"""

    if not room:
        return f'<div class="card"><h3>Room</h3><p class="muted">No current room.</p>{set_room_html}</div>'

    # Features — each state update is its own form
    features_html = ""
    for feat in room.features:
        fid = str(feat.feature_id)
        features_html += f"""
<tr>
  <td><strong>{feat.name}</strong><br>
      <span class="muted">{feat.description}</span></td>
  <td>
    <form hx-post="/session/{channel_id}/feature/{fid}/setstate"
          hx-target="#dashboard" hx-swap="outerHTML">
      <div class="row">
        <input type="text" name="state_str" value="{feat.state}">
        <button class="btn-sm" type="submit">Set</button>
      </div>
    </form>
  </td>
</tr>"""

    add_feature_html = f"""
<hr class="divider">
<div class="section-header"><h3>Add Feature</h3></div>
<form hx-post="/session/{channel_id}/addfeature"
      hx-target="#dashboard" hx-swap="outerHTML">
  <div class="row">
    <div><label>Name</label><input type="text" name="name" placeholder="Chandelier"></div>
    <div><label>State</label><input type="text" name="state_str" value="intact"></div>
  </div>
  <label>Description</label>
  <textarea name="description" rows="2" placeholder="Player-visible description"></textarea>
  <button type="submit">Add Feature</button>
</form>"""

    # Exits — select triggers on change
    exits_html = ""
    for i, ex in enumerate(room.exits, 1):
        eid = str(ex.exit_id)
        options = ''.join(
            f'<option value="{d.value}" {"selected" if d == ex.door_state else ""}>{d.value}</option>'
            for d in DoorState
        )
        exits_html += f"""
<tr>
  <td><strong>{i}. {ex.label.capitalize()}</strong><br>
      <span class="muted">{ex.description}</span></td>
  <td>
    <form hx-post="/session/{channel_id}/exit/{eid}/setstate"
          hx-target="#dashboard" hx-swap="outerHTML">
      <div class="row">
        <select name="door_state" onchange="this.form.requestSubmit()">
          {options}
        </select>
      </div>
    </form>
  </td>
</tr>"""

    add_exit_html = f"""
<hr class="divider">
<div class="section-header"><h3>Add Exit</h3></div>
<form hx-post="/session/{channel_id}/addexit"
      hx-target="#dashboard" hx-swap="outerHTML">
  <div class="row">
    <div><label>Label</label><input type="text" name="label" placeholder="north"></div>
    <div><label>Door State</label>
    <select name="door_state">
      {''.join(f'<option value="{d.value}">{d.value}</option>' for d in DoorState)}
    </select></div>
  </div>
  <label>Description</label>
  <textarea name="description" rows="2" placeholder="Player-visible description"></textarea>
  <button type="submit">Add Exit</button>
</form>"""

    return f"""
<div class="card">
  <div class="section-header"><h3>Room: {room.name}</h3></div>
  <p class="muted">{room.description}</p>
  {f'<p class="muted"><em>DM notes: {room.notes}</em></p>' if room.notes else ''}

  <div class="section-header"><h3>Features</h3></div>
  {'<table><tr><th>Feature</th><th>State</th></tr>' + features_html + '</table>' if features_html else '<p class="muted">No features.</p>'}
  {add_feature_html}

  <hr class="divider">
  <div class="section-header"><h3>Exits</h3></div>
  {'<table><tr><th>Exit</th><th>Door State</th></tr>' + exits_html + '</table>' if exits_html else '<p class="muted">No exits.</p>'}
  {add_exit_html}

  {set_room_html}
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




def npc_panel(state: GameState) -> str:
    channel_id = state.platform_channel_id

    rows = ""
    for npc in state.npcs:
        nid = str(npc.npc_id)
        rows += f"""
<tr>
  <td><strong>{npc.name}</strong><br>
      <span class="muted">{npc.description}</span></td>
  <td class="hp-bar">{npc.hp_current}/{npc.hp_max}</td>
  <td>
    <form hx-post="/session/{channel_id}/npc/{nid}/sethp"
          hx-target="#dashboard" hx-swap="outerHTML" style="margin-bottom:4px">
      <div class="row">
        <input type="number" name="hp" value="{npc.hp_current}"
               min="0" max="{npc.hp_max}" style="width:60px;flex:0">
        <button class="btn-sm" type="submit">HP</button>
      </div>
    </form>
    <form hx-post="/session/{channel_id}/npc/{nid}/setstatus"
          hx-target="#dashboard" hx-swap="outerHTML">
      <div class="row">
        <input type="text" name="status" value="{npc.status}" placeholder="status">
        <button class="btn-sm" type="submit">Status</button>
      </div>
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
    <div><label>AC</label><input type="number" name="ac" value="7" min="1"></div>
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
