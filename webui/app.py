"""
webui/app.py — FastAPI DM control panel.

All form inputs use `name` attributes that match the Form() parameter
names in these routes directly. No hx-include id-based lookups.
"""

from __future__ import annotations

import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse

import store
from store import notify_players_new_turn
from engine import (
    add_exit,
    add_npc as eng_add_npc,
    answer_oracle,
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
)
from models import (
    CharacterStatus,
    DoorState,
    NPC,
    Room,
    RoomFeature,
)
from webui.templates import (
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


def _respond(channel_id: str, flash: str = "", error: str = "", sync: bool = True) -> HTMLResponse:
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse('<div class="error">Session not found.</div>')
    if sync and _bot:
        asyncio.create_task(_sync_discord(channel_id))
    return HTMLResponse(dashboard_fragment(state, flash=flash, error=error))


# ---------------------------------------------------------------------------
# Index and session selection
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index():
    return session_list_page(_session_list())


@app.get("/session/{channel_id}", response_class=HTMLResponse)
async def session_view(channel_id: str):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("<p>Session not found.</p>", status_code=404)
    return session_page(state, _session_list())


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
    store.save_session(state)
    if _bot:
        channel = _bot.get_channel(int(channel_id))
        if channel:
            asyncio.create_task(store.repost_status(channel, state, narrative=narrative))
            asyncio.create_task(notify_players_new_turn(channel, state))
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
    store.save_session(state)
    return _respond(channel_id, flash=f"Timer set to {hours}h from now.")


@app.post("/session/{channel_id}/setturnlength", response_class=HTMLResponse)
async def route_setturnlength(channel_id: str, hours: Annotated[float, Form()]):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    state.default_turn_hours = hours
    store.save_session(state)
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
    store.save_session(state)
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
):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    room = Room(name=name, description=description, notes=notes)
    set_room(state, room)
    return _respond(channel_id, flash=f"Room set: {name}.")


@app.post("/session/{channel_id}/addfeature", response_class=HTMLResponse)
async def route_addfeature(
    channel_id: str,
    name: Annotated[str, Form()],
    description: Annotated[str, Form()],
    state_str: Annotated[str, Form()] = "intact",
):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    room = state.current_room
    if room is None:
        return _respond(channel_id, error="No current room.")
    room.features.append(RoomFeature(name=name, description=description, state=state_str))
    store.save_session(state)
    return _respond(channel_id)


@app.post("/session/{channel_id}/feature/{feature_id}/setstate", response_class=HTMLResponse)
async def route_feature_setstate(
    channel_id: str,
    feature_id: str,
    state_str: Annotated[str, Form()],
):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    result = set_feature_state(state, UUID(feature_id), state_str)
    if not result.ok:
        return _respond(channel_id, error=result.error)
    return _respond(channel_id)


@app.post("/session/{channel_id}/addexit", response_class=HTMLResponse)
async def route_addexit(
    channel_id: str,
    label: Annotated[str, Form()],
    description: Annotated[str, Form()],
    door_state: Annotated[str, Form()] = "open",
):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    try:
        ds = DoorState(door_state)
    except ValueError:
        return _respond(channel_id, error=f"Unknown door state: {door_state}")
    result = add_exit(state, label, description, ds)
    if not result.ok:
        return _respond(channel_id, error=result.error)
    return _respond(channel_id)


@app.post("/session/{channel_id}/exit/{exit_id}/setstate", response_class=HTMLResponse)
async def route_exit_setstate(
    channel_id: str,
    exit_id: str,
    door_state: Annotated[str, Form()],
):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    try:
        ds = DoorState(door_state)
    except ValueError:
        return _respond(channel_id, error=f"Unknown door state: {door_state}")
    result = set_exit_state(state, UUID(exit_id), ds)
    if not result.ok:
        return _respond(channel_id, error=result.error)
    return _respond(channel_id)


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
    store.save_session(state)
    if _bot:
        # Edit the Discord oracle message in place
        channel = _bot.get_channel(int(channel_id))
        if channel and oracle.message_id:
            async def _edit():
                try:
                    msg = await channel.fetch_message(oracle.message_id)
                    new_content = (
                        f"**Oracle #{oracle.number}** \u2014 "
                        f"{oracle.asker_name} asks: \"{oracle.question}\"\n"
                        f"> {oracle.answer}"
                    )
                    await msg.edit(content=new_content)
                except Exception:
                    pass
            asyncio.create_task(_edit())
        # DM the player who asked
        if oracle.asker_owner_id:
            async def _dm_player():
                try:
                    user = await _bot.fetch_user(int(oracle.asker_owner_id))
                    await user.send(
                        f"**Oracle #{oracle.number}** \u2014 "
                        f"The DM answered your question: \"{oracle.question}\"\n"
                        f"> {oracle.answer}"
                    )
                except Exception:
                    pass
            asyncio.create_task(_dm_player())
    return _respond(channel_id, flash="Oracle answered.")
