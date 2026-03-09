"""
webui/app.py — FastAPI DM control panel.

All form inputs use `name` attributes that match the Form() parameter
names in these routes directly. No hx-include id-based lookups.
"""

from __future__ import annotations

import asyncio
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from discord_tasks import dispatch_oracle_answer, dispatch_turn_resolved
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, Form, Request, UploadFile, File
from fastapi.responses import Response
from fastapi.responses import HTMLResponse

import store
from store import archive_session, delete_session, repost_status, save_session_async
from serialization import deserialize_dungeon_file, serialize_dungeon_file
from engine import (
    add_exit,
    add_npc as eng_add_npc,
    answer_oracle,
    delete_exit,
    delete_feature,
    import_dungeon,
    move_party_to_room,
    register_room,
    set_turn_number,
    update_exit,
    update_feature,
    update_npc,
    remove_npc,
    update_room,
    close_turn,
    hold_session,
    open_turn,
    resolve_turn,
    resume_session,
    set_character_hp,
    set_character_status,
    set_exit_state,
    set_feature_state,
    set_light_source,
    set_npc_hp,
    set_npc_status,
    set_room,
    start_session,
)
from models import (
    CharacterStatus,
    DoorState,
    NPC,
    Room,
    RoomFeature,
)
from webui.templates import (
    archive_page,
    dashboard_fragment,
    session_list_page,
    session_page,
)

app = FastAPI(title="DM Control Panel")

_bot = None


def set_bot(bot) -> None:
    global _bot
    _bot = bot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _session_list() -> list[tuple[str, str]]:
    channel_ids = store.db.list_channels()
    result = []
    for cid in channel_ids:
        name = f"#{cid}"
        if _bot:
            ch = _bot.get_channel(int(cid))
            if ch:
                name = f"#{ch.name}"
        result.append((cid, name))
    return result


async def _sync_discord(channel_id: str) -> None:
    if _bot is None:
        return
    state = store.get_session(channel_id)
    if state is None:
        return
    channel = _bot.get_channel(int(channel_id))
    if channel is None:
        return
    await store.update_status(channel, state)


def _respond(channel_id: str, flash: str = "", error: str = "", sync: bool = True, view_room_id: str = "", edit_id: str = "") -> HTMLResponse:
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse('<div class="error">Session not found.</div>')
    if sync and _bot:
        asyncio.create_task(_sync_discord(channel_id))
    return HTMLResponse(dashboard_fragment(state, flash=flash, error=error, view_room_id=view_room_id, edit_id=edit_id))


# ---------------------------------------------------------------------------
# Room-view helpers
# ---------------------------------------------------------------------------

def _parse_uuid(s: str):
    """Return a UUID if s is a valid UUID string, else None."""
    if not s:
        return None
    try:
        return UUID(s)
    except ValueError:
        return None


def _resolve_view_room(state, view_room_id: str):
    """Return the Room for view_room_id, falling back to current_room."""
    from models import Room as _Room
    if view_room_id and state.dungeon:
        rid = _parse_uuid(view_room_id)
        if rid:
            room = state.dungeon.rooms.get(rid)
            if room:
                return room
    return state.current_room


# ---------------------------------------------------------------------------
# Index and session selection
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index():
    return session_list_page(_session_list())


@app.get("/session/{channel_id}", response_class=HTMLResponse)
async def session_view(channel_id: str, view_room: str = "", edit: str = ""):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("<p>Session not found.</p>", status_code=404)
    return session_page(state, _session_list(), view_room_id=view_room, edit_id=edit)


# ---------------------------------------------------------------------------
# Turn routes
# ---------------------------------------------------------------------------

@app.post("/session/{channel_id}/resolve", response_class=HTMLResponse)
async def route_resolve(channel_id: str, narrative: Annotated[str, Form()]):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    if state.current_turn is not None:
        close_turn(state)
    result = resolve_turn(state, narrative)
    if not result.ok:
        return _respond(channel_id, error=result.error, sync=False)
    open_turn(state)
    await save_session_async(state)
    if _bot:
        channel = _bot.get_channel(int(channel_id))
        if channel:
            asyncio.create_task(dispatch_turn_resolved(channel, state, narrative))
    return _respond(channel_id, flash="Turn resolved.", sync=False)


@app.post("/session/{channel_id}/settimer", response_class=HTMLResponse)
async def route_settimer(channel_id: str, hours: Annotated[float, Form()]):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    if state.current_turn is None:
        return _respond(channel_id, error="No open turn.")
    from datetime import datetime, timedelta, timezone
    state.current_turn.due_at = datetime.now(timezone.utc) + timedelta(hours=hours)
    await save_session_async(state)
    return _respond(channel_id, flash=f"Timer set to {hours}h from now.")


@app.post("/session/{channel_id}/setturnlength", response_class=HTMLResponse)
async def route_setturnlength(channel_id: str, hours: Annotated[float, Form()]):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    state.default_turn_hours = hours
    await save_session_async(state)
    return _respond(channel_id, flash=f"Default turn length set to {hours}h.")


@app.post("/session/{channel_id}/hold", response_class=HTMLResponse)
async def route_hold(channel_id: str):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    result = hold_session(state)
    if not result.ok:
        return _respond(channel_id, error=result.error)
    return _respond(channel_id, flash="Session placed on hold.")


@app.post("/session/{channel_id}/resume", response_class=HTMLResponse)
async def route_resume(channel_id: str):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    result = resume_session(state)
    if not result.ok:
        return _respond(channel_id, error=result.error)
    return _respond(channel_id, flash="Session resumed.")


# ---------------------------------------------------------------------------
# Character routes
# ---------------------------------------------------------------------------

@app.post("/session/{channel_id}/char/{char_id}/sethp", response_class=HTMLResponse)
async def route_char_sethp(
    channel_id: str,
    char_id: str,
    hp: Annotated[int, Form()],
):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    result = set_character_hp(state, UUID(char_id), hp)
    if not result.ok:
        return _respond(channel_id, error=result.error)
    return _respond(channel_id)


@app.post("/session/{channel_id}/char/{char_id}/setstatus", response_class=HTMLResponse)
async def route_char_setstatus(
    channel_id: str,
    char_id: str,
    status: Annotated[str, Form()],
    notes: Annotated[str, Form()] = "",
):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    try:
        char_status = CharacterStatus(status)
    except ValueError:
        return _respond(channel_id, error=f"Unknown status: {status}")
    result = set_character_status(state, UUID(char_id), char_status, notes)
    if not result.ok:
        return _respond(channel_id, error=result.error)
    return _respond(channel_id)


@app.post("/session/{channel_id}/setleader/{char_id}", response_class=HTMLResponse)
async def route_setleader(channel_id: str, char_id: str):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    cid = UUID(char_id)
    if cid not in state.characters:
        return _respond(channel_id, error="Character not found.")
    state.party.leader_id = cid
    await save_session_async(state)
    char = state.characters[cid]
    return _respond(channel_id, flash=f"{char.name} is now party leader.")


# ---------------------------------------------------------------------------
# Light source
# ---------------------------------------------------------------------------

@app.post("/session/{channel_id}/setlight", response_class=HTMLResponse)
async def route_setlight(
    channel_id: str,
    label: Annotated[str, Form()],
    turns: Annotated[int, Form()],
):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    turns_remaining = None if turns < 0 else turns
    result = set_light_source(state, label, turns_remaining)
    if not result.ok:
        return _respond(channel_id, error=result.error)
    return _respond(channel_id)


# ---------------------------------------------------------------------------
# Room routes
# ---------------------------------------------------------------------------

@app.post("/session/{channel_id}/setroom", response_class=HTMLResponse)
async def route_setroom(
    channel_id: str,
    name: Annotated[str, Form()],
    description: Annotated[str, Form()],
    notes: Annotated[str, Form()] = "",
    view_room_id: Annotated[str, Form()] = "",
):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    if not name.strip():
        return _respond(channel_id, error="Room name is required.", view_room_id=view_room_id)
    room = Room(name=name, description=description, notes=notes)
    result = register_room(state, room)
    await save_session_async(state)
    # After creating, switch the view to the new room but don't move the party
    new_view_id = str(room.room_id)
    return _respond(channel_id, flash=f"Room created: {name}.", view_room_id=new_view_id)


@app.post("/session/{channel_id}/enterroom/{room_id}", response_class=HTMLResponse)
async def route_enterroom(channel_id: str, room_id: str):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    rid = _parse_uuid(room_id)
    if rid is None:
        return _respond(channel_id, error=f"Invalid room ID: {room_id}")
    result = move_party_to_room(state, rid)
    if not result.ok:
        return _respond(channel_id, error=result.error, view_room_id=room_id)
    await save_session_async(state)
    if _bot:
        channel = _bot.get_channel(int(channel_id))
        if channel:
            asyncio.create_task(store.update_status(channel, state))
    return _respond(channel_id, flash=result.message, view_room_id=room_id)


@app.post("/session/{channel_id}/addfeature", response_class=HTMLResponse)
async def route_addfeature(
    channel_id: str,
    name: Annotated[str, Form()],
    description: Annotated[str, Form()],
    state_str: Annotated[str, Form()] = "intact",
    view_room_id: Annotated[str, Form()] = "",
):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    room = _resolve_view_room(state, view_room_id)
    if room is None:
        return _respond(channel_id, error="No room selected.", view_room_id=view_room_id)
    room.features.append(RoomFeature(name=name, description=description, state=state_str))
    await save_session_async(state)
    return _respond(channel_id, view_room_id=view_room_id)


@app.post("/session/{channel_id}/feature/{feature_id}/setstate", response_class=HTMLResponse)
async def route_feature_setstate(
    channel_id: str,
    feature_id: str,
    state_str: Annotated[str, Form()],
    view_room_id: Annotated[str, Form()] = "",
):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    rid = _parse_uuid(view_room_id)
    result = set_feature_state(state, UUID(feature_id), state_str, room_id=rid)
    if not result.ok:
        return _respond(channel_id, error=result.error, view_room_id=view_room_id)
    await save_session_async(state)
    return _respond(channel_id, view_room_id=view_room_id)


@app.post("/session/{channel_id}/addexit", response_class=HTMLResponse)
async def route_addexit(
    channel_id: str,
    label: Annotated[str, Form()],
    description: Annotated[str, Form()],
    door_state: Annotated[str, Form()] = "open",
    view_room_id: Annotated[str, Form()] = "",
):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    try:
        ds = DoorState(door_state)
    except ValueError:
        return _respond(channel_id, error=f"Unknown door state: {door_state}", view_room_id=view_room_id)
    rid = _parse_uuid(view_room_id)
    result = add_exit(state, label, description, ds, room_id=rid)
    if not result.ok:
        return _respond(channel_id, error=result.error, view_room_id=view_room_id)
    await save_session_async(state)
    return _respond(channel_id, view_room_id=view_room_id)


@app.post("/session/{channel_id}/exit/{exit_id}/setstate", response_class=HTMLResponse)
async def route_exit_setstate(
    channel_id: str,
    exit_id: str,
    door_state: Annotated[str, Form()],
    view_room_id: Annotated[str, Form()] = "",
):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    try:
        ds = DoorState(door_state)
    except ValueError:
        return _respond(channel_id, error=f"Unknown door state: {door_state}", view_room_id=view_room_id)
    rid = _parse_uuid(view_room_id)
    result = set_exit_state(state, UUID(exit_id), ds, room_id=rid)
    if not result.ok:
        return _respond(channel_id, error=result.error, view_room_id=view_room_id)
    await save_session_async(state)
    return _respond(channel_id, view_room_id=view_room_id)


# ---------------------------------------------------------------------------
# NPC routes
# ---------------------------------------------------------------------------

@app.post("/session/{channel_id}/addnpc", response_class=HTMLResponse)
async def route_addnpc(
    channel_id: str,
    name: Annotated[str, Form()],
    hp: Annotated[int, Form()],
    ac: Annotated[int, Form()] = 9,
    damage_dice: Annotated[str, Form()] = "1d6",
    description: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    npc = NPC(
        name=name, hp_max=hp, hp_current=hp,
        armor_class=ac, damage_dice=damage_dice,
        description=description, notes=notes,
    )
    eng_add_npc(state, npc)
    return _respond(channel_id)


@app.post("/session/{channel_id}/npc/{npc_id}/sethp", response_class=HTMLResponse)
async def route_npc_sethp(
    channel_id: str,
    npc_id: str,
    hp: Annotated[int, Form()],
):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    result = set_npc_hp(state, UUID(npc_id), hp)
    if not result.ok:
        return _respond(channel_id, error=result.error)
    return _respond(channel_id)


@app.post("/session/{channel_id}/npc/{npc_id}/setstatus", response_class=HTMLResponse)
async def route_npc_setstatus(
    channel_id: str,
    npc_id: str,
    status: Annotated[str, Form()],
):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    result = set_npc_status(state, UUID(npc_id), status)
    if not result.ok:
        return _respond(channel_id, error=result.error)
    return _respond(channel_id)

@app.post("/session/{channel_id}/oracle/{number}/answer", response_class=HTMLResponse)
async def route_oracle_answer(
    channel_id: str,
    number: int,
    answer: Annotated[str, Form()],
):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    result, oracle = answer_oracle(state, number, answer)
    if not result.ok:
        return _respond(channel_id, error=result.error)
    await save_session_async(state)
    if _bot:
        channel = _bot.get_channel(int(channel_id))
        if channel:
            asyncio.create_task(dispatch_oracle_answer(_bot, channel, oracle))
    return _respond(channel_id, flash="Oracle answered.")


# ---------------------------------------------------------------------------
# Dungeon import / export
# ---------------------------------------------------------------------------

@app.post("/session/{channel_id}/dungeon/import", response_class=HTMLResponse)
async def route_dungeon_import(channel_id: str, file: UploadFile = File(...)):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    raw = await file.read()
    try:
        dungeon = deserialize_dungeon_file(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        return _respond(channel_id, error=f"Import failed: {e}")
    result = import_dungeon(state, dungeon)
    if not result.ok:
        return _respond(channel_id, error=result.error)
    await save_session_async(state)
    return _respond(channel_id, flash=result.message)


@app.get("/session/{channel_id}/dungeon/export")
async def route_dungeon_export(channel_id: str):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    if state.dungeon is None:
        return HTMLResponse("No dungeon loaded.", status_code=404)
    json_str = serialize_dungeon_file(state.dungeon)
    safe_name = state.dungeon.name.replace(" ", "_").lower()
    return Response(
        content=json_str,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.json"'},
    )


# ---------------------------------------------------------------------------
# Turn number
# ---------------------------------------------------------------------------

@app.post("/session/{channel_id}/setturnumber", response_class=HTMLResponse)
async def route_setturnumber(
    channel_id: str,
    turn_number: Annotated[int, Form()],
):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    result = set_turn_number(state, turn_number)
    if not result.ok:
        return _respond(channel_id, error=result.error)
    await save_session_async(state)
    return _respond(channel_id, flash=result.message)


# ---------------------------------------------------------------------------
# Room update
# ---------------------------------------------------------------------------

@app.post("/session/{channel_id}/room/{room_id}/update", response_class=HTMLResponse)
async def route_room_update(
    channel_id: str,
    room_id: str,
    name: Annotated[str, Form()],
    description: Annotated[str, Form()],
    notes: Annotated[str, Form()] = "",
    view_room_id: Annotated[str, Form()] = "",
):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    rid = _parse_uuid(room_id)
    if rid is None:
        return _respond(channel_id, error="Invalid room ID.", view_room_id=view_room_id)
    result = update_room(state, rid, name, description, notes)
    if not result.ok:
        return _respond(channel_id, error=result.error, view_room_id=view_room_id)
    await save_session_async(state)
    return _respond(channel_id, flash=result.message, view_room_id=view_room_id)


# ---------------------------------------------------------------------------
# Feature update / delete
# ---------------------------------------------------------------------------

@app.post("/session/{channel_id}/feature/{feature_id}/update", response_class=HTMLResponse)
async def route_feature_update(
    channel_id: str,
    feature_id: str,
    name: Annotated[str, Form()],
    description: Annotated[str, Form()],
    state_str: Annotated[str, Form()] = "intact",
    notes: Annotated[str, Form()] = "",
    view_room_id: Annotated[str, Form()] = "",
):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    rid = _parse_uuid(view_room_id)
    result = update_feature(state, UUID(feature_id), name, description, state_str, notes, room_id=rid)
    if not result.ok:
        return _respond(channel_id, error=result.error, view_room_id=view_room_id)
    await save_session_async(state)
    return _respond(channel_id, flash=result.message, view_room_id=view_room_id)


@app.post("/session/{channel_id}/feature/{feature_id}/delete", response_class=HTMLResponse)
async def route_feature_delete(
    channel_id: str,
    feature_id: str,
    view_room_id: Annotated[str, Form()] = "",
):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    rid = _parse_uuid(view_room_id)
    result = delete_feature(state, UUID(feature_id), room_id=rid)
    if not result.ok:
        return _respond(channel_id, error=result.error, view_room_id=view_room_id)
    await save_session_async(state)
    return _respond(channel_id, view_room_id=view_room_id)


# ---------------------------------------------------------------------------
# Exit update / delete
# ---------------------------------------------------------------------------

@app.post("/session/{channel_id}/exit/{exit_id}/update", response_class=HTMLResponse)
async def route_exit_update(
    channel_id: str,
    exit_id: str,
    label: Annotated[str, Form()],
    description: Annotated[str, Form()],
    door_state: Annotated[str, Form()],
    destination_id: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
    view_room_id: Annotated[str, Form()] = "",
):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    try:
        ds = DoorState(door_state)
    except ValueError:
        return _respond(channel_id, error=f"Unknown door state: {door_state}", view_room_id=view_room_id)
    rid = _parse_uuid(view_room_id)
    dest = _parse_uuid(destination_id)
    result = update_exit(state, UUID(exit_id), label, description, ds, destination_id=dest, notes=notes, room_id=rid)
    if not result.ok:
        return _respond(channel_id, error=result.error, view_room_id=view_room_id)
    await save_session_async(state)
    return _respond(channel_id, flash=result.message, view_room_id=view_room_id)


@app.post("/session/{channel_id}/exit/{exit_id}/delete", response_class=HTMLResponse)
async def route_exit_delete(
    channel_id: str,
    exit_id: str,
    view_room_id: Annotated[str, Form()] = "",
):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    rid = _parse_uuid(view_room_id)
    result = delete_exit(state, UUID(exit_id), room_id=rid)
    if not result.ok:
        return _respond(channel_id, error=result.error, view_room_id=view_room_id)
    await save_session_async(state)
    return _respond(channel_id, view_room_id=view_room_id)


# ---------------------------------------------------------------------------
# NPC update / delete
# ---------------------------------------------------------------------------

@app.post("/session/{channel_id}/npc/{npc_id}/update", response_class=HTMLResponse)
async def route_npc_update(
    channel_id: str,
    npc_id: str,
    name: Annotated[str, Form()],
    description: Annotated[str, Form()],
    hp_max: Annotated[int, Form()],
    hp_current: Annotated[int, Form()],
    armor_class: Annotated[int, Form()],
    notes: Annotated[str, Form()] = "",
    view_room_id: Annotated[str, Form()] = "",
):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    result = update_npc(state, UUID(npc_id), name, description, hp_max, hp_current, armor_class, notes)
    if not result.ok:
        return _respond(channel_id, error=result.error, view_room_id=view_room_id)
    await save_session_async(state)
    return _respond(channel_id, flash=result.message, view_room_id=view_room_id)


@app.post("/session/{channel_id}/npc/{npc_id}/delete", response_class=HTMLResponse)
async def route_npc_delete(
    channel_id: str,
    npc_id: str,
    view_room_id: Annotated[str, Form()] = "",
):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    result = remove_npc(state, UUID(npc_id))
    if not result.ok:
        return _respond(channel_id, error=result.error, view_room_id=view_room_id)
    await save_session_async(state)
    return _respond(channel_id, view_room_id=view_room_id)


# ---------------------------------------------------------------------------
# Embark / End session
# ---------------------------------------------------------------------------

@app.post("/session/{channel_id}/embark", response_class=HTMLResponse)
async def route_embark(channel_id: str):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    result = start_session(state)
    if not result.ok:
        return _respond(channel_id, error=result.error)
    await save_session_async(state)
    if _bot:
        channel = _bot.get_channel(int(channel_id))
        if channel:
            asyncio.create_task(repost_status(channel, state))
    return _respond(channel_id, flash="Session started. The adventure begins!")


@app.post("/session/{channel_id}/endsession", response_class=HTMLResponse)
async def route_endsession(channel_id: str):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    channel_name = ""
    if _bot:
        ch = _bot.get_channel(int(channel_id))
        if ch:
            channel_name = getattr(ch, "name", "")
            asyncio.create_task(ch.send("Session archived via DM panel."))
    await archive_session(channel_id, channel_name)
    return HTMLResponse("", headers={"HX-Redirect": "/"})


# ---------------------------------------------------------------------------
# Archive browser
# ---------------------------------------------------------------------------

@app.get("/archive", response_class=HTMLResponse)
async def archive_index(flash: str = "", error: str = ""):
    entries = await store.db.list_archive_async()
    return archive_page(_session_list(), entries, flash=flash, error=error)


@app.post("/archive/{session_id}/resurrect", response_class=HTMLResponse)
async def archive_resurrect(
    session_id: str,
    channel_id: Annotated[str, Form()],
):
    async def _err(msg: str) -> HTMLResponse:
        entries = await store.db.list_archive_async()
        return HTMLResponse(archive_page(_session_list(), entries, error=msg))

    channel_id = channel_id.strip()
    if not channel_id.isdigit():
        return await _err("Channel ID must be a numeric Discord channel ID.")

    if store.has_session(channel_id):
        return await _err(
            f"Channel {channel_id} already has an active session. "
            "End or archive it first, then retry."
        )

    # Verify the bot can actually see this channel (if bot is available)
    if _bot:
        ch = _bot.get_channel(int(channel_id))
        if ch is None:
            return await _err(
                f"Channel {channel_id} is not visible to the bot. "
                "Check the ID is correct and the bot is in that channel."
            )

    state = await store.db.resurrect_async(session_id, channel_id)
    if state is None:
        return await _err("Archive entry not found.")

    # Register in the in-memory cache and redirect to the panel
    store._sessions[channel_id] = state
    return HTMLResponse("", headers={"HX-Redirect": f"/session/{channel_id}"})


@app.post("/archive/{session_id}/delete", response_class=HTMLResponse)
async def archive_delete(session_id: str):
    await store.db.delete_archive_async(session_id)
    entries = await store.db.list_archive_async()
    return HTMLResponse(archive_page(_session_list(), entries, flash="Archive entry deleted."))
