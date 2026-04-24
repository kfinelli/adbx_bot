"""
webui/app.py — FastAPI DM control panel.

All form inputs use `name` attributes that match the Form() parameter
names in these routes directly. No hx-include id-based lookups.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from datetime import UTC
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, Response

import store
from discord_tasks import dispatch_oracle_answer, dispatch_turn_resolved, drain_level_ups
from engine import (
    add_exit,
    adjust_light_charges,
    adjust_skill_uses,
    adjust_spell_charges,
    answer_oracle,
    apply_condition,
    award_xp,
    close_turn,
    delete_exit,
    delete_feature,
    distribute_xp,
    give_item,
    hold_session,
    import_dungeon,
    move_party_to_room,
    open_turn,
    recharge_day_spells,
    register_room,
    remove_item,
    remove_npc,
    resolve_turn,
    resume_session,
    set_character_hp,
    set_character_status,
    set_exit_state,
    set_exit_visibility,
    set_feature_state,
    set_npc_hp,
    set_npc_status,
    set_npc_visibility,
    set_turn_number,
    start_session,
    unsubmit_turn,
    update_dungeon,
    update_exit,
    update_feature,
    update_npc,
    update_room,
)
from engine import (
    add_npc as eng_add_npc,
)
from engine import (
    copy_npc as eng_copy_npc,
)
from models import (
    NPC,
    CharacterStatus,
    DoorState,
    RangeBand,
    Room,
    RoomFeature,
)
from serialization import deserialize_dungeon_file, serialize_dungeon_file
from store import archive_session, repost_status, save_session_async, sync_character_to_sessions
from webui.templates import (
    archive_page,
    character_page,
    dashboard_fragment,
    session_list_page,
    session_page,
)

app = FastAPI(title="DM Control Panel")


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> HTMLResponse:
    """Return HTML on 422 so HTMX can swap it into #dashboard instead of silently failing."""
    errors = "; ".join(
        f"{e['loc'][-1]}: {e['msg']}" for e in exc.errors()
    )
    channel_id = request.path_params.get("channel_id", "")
    if channel_id:
        state = store.get_session(channel_id)
        if state:
            from webui.templates import dashboard_fragment
            try:
                html = dashboard_fragment(state, error=f"Validation error: {errors}")
                return HTMLResponse(html, status_code=422)
            except Exception:
                pass
    return HTMLResponse(
        f'<div id="dashboard"><div class="error">Validation error: {errors}</div></div>',
        status_code=422,
    )


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
    import traceback
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse('<div class="error">Session not found.</div>')
    if sync and _bot:
        asyncio.create_task(_sync_discord(channel_id))
    try:
        return HTMLResponse(dashboard_fragment(state, flash=flash, error=error, view_room_id=view_room_id, edit_id=edit_id))
    except Exception as exc:
        tb = traceback.format_exc()
        print(f"[_respond] dashboard_fragment error: {exc}\n{tb}", file=sys.stderr)
        return HTMLResponse(f'<div id="dashboard"><div class="error">Render error: {exc}</div></div>')


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
            asyncio.create_task(dispatch_turn_resolved(channel, state, narrative, bot=_bot))
    return _respond(channel_id, flash="Turn resolved.", sync=False)


@app.post("/session/{channel_id}/settimer", response_class=HTMLResponse)
async def route_settimer(channel_id: str, hours: Annotated[float, Form()]):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    if state.current_turn is None:
        return _respond(channel_id, error="No open turn.")
    from datetime import datetime, timedelta
    state.current_turn.due_at = datetime.now(UTC) + timedelta(hours=hours)
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


@app.post("/session/{channel_id}/turn/{char_id}/unsubmit", response_class=HTMLResponse)
async def route_unsubmit_turn(
    channel_id: str,
    char_id: str,
):
    """DM rejects a player's turn submission, sending it back for revision."""
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    result = unsubmit_turn(state, UUID(char_id))
    if not result.ok:
        return _respond(channel_id, error=result.error)
    await save_session_async(state)
    # Notify the player via Discord DM
    if _bot:
        char = state.characters.get(UUID(char_id))
        if char and char.owner_id:
            try:
                user = await _bot.fetch_user(int(char.owner_id))
                await user.send(
                    f"**Turn Submission Returned**\n\n"
                    f"The DM has returned your turn submission for **{char.name}**. "
                    f"Please review and re-submit your action for the current turn."
                )
            except Exception:
                pass  # Could not DM user (privacy settings, etc.)
    return _respond(channel_id, flash=result.message)


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


@app.post("/session/{channel_id}/char/{char_id}/rollsave", response_class=HTMLResponse)
async def route_char_rollsave(
    channel_id: str,
    char_id: str,
    stat: Annotated[str, Form()] = "physique",
):
    """Roll a saving throw for a character against base_save + chosen stat."""
    import random

    from engine.azure_constants import POWER_LEVEL
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    char = state.characters.get(UUID(char_id))
    if char is None:
        return _respond(channel_id, error="Character not found.")

    stat_val = getattr(char.ability_scores, stat, None)
    if stat_val is None:
        return _respond(channel_id, error=f"Unknown stat: {stat}")

    base_save = char.saving_throws.get("save", 0)
    threshold = base_save + stat_val
    raw = random.randint(1, 10 * POWER_LEVEL)

    if raw < threshold:
        msg = (
            f"**{char.name} PASSES** their save! "
            f"(rolled {raw} < threshold {threshold} = save {base_save} + {stat} {stat_val})"
        )
    else:
        msg = (
            f"**{char.name} FAILS** their save. "
            f"(rolled {raw} ≥ threshold {threshold} = save {base_save} + {stat} {stat_val})"
        )
    return _respond(channel_id, flash=msg)


@app.post("/session/{channel_id}/char/{char_id}/additem", response_class=HTMLResponse)
async def route_char_additem(
    channel_id: str,
    char_id: str,
    item_id: Annotated[str, Form()],
    quantity: Annotated[int, Form()] = 1,
):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    result = give_item(state, UUID(char_id), item_id, quantity)
    if not result.ok:
        return _respond(channel_id, error=result.error)
    await save_session_async(state)
    return _respond(channel_id, flash=result.message)


@app.post("/session/{channel_id}/char/{char_id}/removeitem", response_class=HTMLResponse)
async def route_char_removeitem(
    channel_id: str,
    char_id: str,
    item_id: Annotated[str, Form()],
    quantity: Annotated[int, Form()] = 1,
):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    result = remove_item(state, UUID(char_id), item_id, quantity)
    if not result.ok:
        return _respond(channel_id, error=result.error)
    await save_session_async(state)
    return _respond(channel_id, flash=result.message)


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
# Party routes (gold, XP)
# ---------------------------------------------------------------------------

@app.post("/session/{channel_id}/party/addgold", response_class=HTMLResponse)
async def route_party_addgold(
    channel_id: str,
    amount: Annotated[int, Form()],
):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    if state.party is None:
        return _respond(channel_id, error="No party.")
    state.party.gold += amount
    await save_session_async(state)
    return _respond(channel_id, flash=f"Added {amount} gold to party.")


@app.post("/session/{channel_id}/party/addxp", response_class=HTMLResponse)
async def route_party_addxp(
    channel_id: str,
    amount: Annotated[int, Form()],
):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    if state.party is None:
        return _respond(channel_id, error="No party.")
    from models import CharacterStatus
    active = [c for c in state.characters.values() if c.status == CharacterStatus.ACTIVE]
    n = len(active)
    distribute_xp(state, amount)
    await save_session_async(state)
    if _bot:
        asyncio.create_task(drain_level_ups(_bot, state))
    each = amount // n if n else 0
    return _respond(channel_id, flash=f"Distributed {amount} XP among {n} active characters ({each} each).")


@app.post("/session/{channel_id}/party/rechargedaily", response_class=HTMLResponse)
async def route_party_rechargedaily(channel_id: str):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    if state.party is None:
        return _respond(channel_id, error="No party.")
    count = 0
    for char in state.characters.values():
        result = recharge_day_spells(state, char.character_id)
        if result.ok and "no daily" not in result.message:
            count += 1
    await save_session_async(state)
    return _respond(channel_id, flash=f"Recharged daily spells for {count} character(s).")


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
    register_room(state, room)
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
        asyncio.create_task(drain_level_ups(_bot, state))
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
    destination_id: Annotated[str, Form()] = "",
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
    result = add_exit(state, label, description, ds, room_id=rid, destination_id=dest)
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


@app.post("/session/{channel_id}/exit/{exit_id}/setvisibility", response_class=HTMLResponse)
async def route_exit_setvisibility(
    channel_id: str,
    exit_id: str,
    hidden: Annotated[bool, Form()] = False,
    view_room_id: Annotated[str, Form()] = "",
):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    rid = _parse_uuid(view_room_id)
    result = set_exit_visibility(state, UUID(exit_id), hidden, room_id=rid)
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
    defense: Annotated[int, Form()] = 0,
    damage_dice: Annotated[str, Form()] = "1d6",
    hit_dice: Annotated[int, Form()] = 1,
    resistance: Annotated[int, Form()] = 0,
    weapon_range: Annotated[int, Form()] = 0,
    description: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
    hidden: Annotated[bool, Form()] = False,
    view_room_id: Annotated[str, Form()] = "",
):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    npc = NPC(
        name=name, hp_max=hp, hp_current=hp,
        defense=defense, damage_dice=damage_dice,
        hit_dice=hit_dice,
        resistance=resistance, weapon_range=weapon_range,
        description=description, notes=notes,
        hidden=hidden,
    )
    room_id = UUID(view_room_id) if view_room_id else None
    eng_add_npc(state, npc, room_id=room_id)
    await save_session_async(state)
    return _respond(channel_id, view_room_id=view_room_id)


@app.post("/session/{channel_id}/npc/{npc_id}/sethp", response_class=HTMLResponse)
async def route_npc_sethp(
    channel_id: str,
    npc_id: str,
    hp: Annotated[int, Form()],
    view_room_id: Annotated[str, Form()] = "",
):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    result = set_npc_hp(state, UUID(npc_id), hp)
    if not result.ok:
        return _respond(channel_id, error=result.error, view_room_id=view_room_id)
    await save_session_async(state)
    return _respond(channel_id, view_room_id=view_room_id)


@app.post("/session/{channel_id}/npc/{npc_id}/setstatus", response_class=HTMLResponse)
async def route_npc_setstatus(
    channel_id: str,
    npc_id: str,
    status: Annotated[str, Form()],
    view_room_id: Annotated[str, Form()] = "",
):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    result = set_npc_status(state, UUID(npc_id), status)
    if not result.ok:
        return _respond(channel_id, error=result.error, view_room_id=view_room_id)
    await save_session_async(state)
    return _respond(channel_id, view_room_id=view_room_id)


@app.post("/session/{channel_id}/npc/{npc_id}/setvisibility", response_class=HTMLResponse)
async def route_npc_setvisibility(
    channel_id: str,
    npc_id: str,
    hidden: Annotated[bool, Form()] = False,
    view_room_id: Annotated[str, Form()] = "",
):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    result = set_npc_visibility(state, UUID(npc_id), hidden)
    if not result.ok:
        return _respond(channel_id, error=result.error, view_room_id=view_room_id)
    await save_session_async(state)
    return _respond(channel_id, view_room_id=view_room_id)


@app.post("/session/{channel_id}/oracle/{number}/answer", response_class=HTMLResponse)
async def route_oracle_answer(
    channel_id: str,
    number: int,
    answer: Annotated[str, Form()],
):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    result = answer_oracle(state, number, answer)
    if not result.ok:
        return _respond(channel_id, error=result.error)
    oracle = result.data
    await save_session_async(state)
    if _bot:
        channel = _bot.get_channel(int(channel_id))
        if channel:
            asyncio.create_task(dispatch_oracle_answer(_bot, channel, oracle))
    return _respond(channel_id, flash="Oracle answered.")


# ---------------------------------------------------------------------------
# Dungeon import / export / update
# ---------------------------------------------------------------------------

@app.post("/session/{channel_id}/dungeon/update", response_class=HTMLResponse)
async def route_dungeon_update(
    channel_id: str,
    name: Annotated[str, Form()],
    description: Annotated[str, Form()] = "",
    random_encounter_interval: Annotated[int, Form()] = 6,
    random_encounter_roll: Annotated[str, Form()] = "1d6",
    view_room_id: Annotated[str, Form()] = "",
):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    result = update_dungeon(state, name, description, random_encounter_interval, random_encounter_roll)
    if not result.ok:
        return _respond(channel_id, error=result.error, view_room_id=view_room_id)
    await save_session_async(state)
    return _respond(channel_id, flash=result.message, view_room_id=view_room_id)


@app.post("/session/{channel_id}/dungeon/import", response_class=HTMLResponse)
async def route_dungeon_import(channel_id: str, file: UploadFile = File(...)):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    raw = await file.read()
    try:
        dungeon, npc_roster = deserialize_dungeon_file(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        return _respond(channel_id, error=f"Import failed: {e}")
    result = import_dungeon(state, dungeon, npc_roster)
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
    json_str = serialize_dungeon_file(state.dungeon, state.npc_roster)
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
    auto_move: Annotated[str, Form()] = "",
    hidden: Annotated[str, Form()] = "",
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
    result = update_exit(state, UUID(exit_id), label, description, ds, destination_id=dest, notes=notes, auto_move=bool(auto_move), hidden=bool(hidden), room_id=rid)
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
    hp_max: Annotated[int, Form()],
    hp_current: Annotated[int, Form()],
    defense: Annotated[int, Form()],
    description: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
    hit_dice: Annotated[int, Form()] = 1,
    resistance: Annotated[int, Form()] = 0,
    weapon_range: Annotated[int, Form()] = 0,
    damage_dice: Annotated[str, Form()] = "1d6",
    view_room_id: Annotated[str, Form()] = "",
):
    import traceback
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    try:
        result = update_npc(
            state, UUID(npc_id), name, description, hp_max, hp_current, defense,
            notes, hit_dice, resistance, weapon_range, damage_dice,
        )
        if not result.ok:
            return _respond(channel_id, error=result.error, view_room_id=view_room_id)
        await save_session_async(state)
        return _respond(channel_id, flash=result.message, view_room_id=view_room_id)
    except Exception as exc:
        tb = traceback.format_exc()
        print(f"[route_npc_update] ERROR for npc_id={npc_id}: {exc}\n{tb}", file=sys.stderr)
        return _respond(channel_id, error=f"Server error: {exc}", view_room_id=view_room_id)


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


@app.post("/session/{channel_id}/npc/{npc_id}/copy", response_class=HTMLResponse)
async def route_npc_copy(
    channel_id: str,
    npc_id: str,
    view_room_id: Annotated[str, Form()] = "",
):
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    rid = _parse_uuid(view_room_id)
    result = eng_copy_npc(state, UUID(npc_id), room_id=rid)
    if not result.ok:
        return _respond(channel_id, error=result.error, view_room_id=view_room_id)
    await save_session_async(state)
    return _respond(channel_id, view_room_id=view_room_id)



# ---------------------------------------------------------------------------
# Combatant battlefield controls (ROUNDS mode)
# ---------------------------------------------------------------------------

@app.post("/session/{channel_id}/combatant/{combatant_id}/setband", response_class=HTMLResponse)
async def route_combatant_setband(
    channel_id:    str,
    combatant_id:  str,
    band:          Annotated[str, Form()],
    view_room_id:  Annotated[str, Form()] = "",
):
    """Move a combatant to a different range band."""
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    if state.battlefield is None:
        return _respond(channel_id, error="No active battlefield.", view_room_id=view_room_id)
    try:
        cid  = UUID(combatant_id)
        rb   = RangeBand(band)
    except ValueError as e:
        return _respond(channel_id, error=str(e), view_room_id=view_room_id)
    cs = state.battlefield.combatants.get(cid)
    if cs is None:
        return _respond(channel_id, error="Combatant not on battlefield.", view_room_id=view_room_id)
    cs.range_band = rb
    await save_session_async(state)
    return _respond(channel_id, view_room_id=view_room_id)


@app.post("/session/{channel_id}/combatant/{combatant_id}/setinitiative", response_class=HTMLResponse)
async def route_combatant_setinitiative(
    channel_id:    str,
    combatant_id:  str,
    initiative:    Annotated[int, Form()],
    view_room_id:  Annotated[str, Form()] = "",
):
    """Override a combatant's initiative value."""
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    if state.battlefield is None:
        return _respond(channel_id, error="No active battlefield.", view_room_id=view_room_id)
    try:
        cid = UUID(combatant_id)
    except ValueError as e:
        return _respond(channel_id, error=str(e), view_room_id=view_room_id)
    cs = state.battlefield.combatants.get(cid)
    if cs is None:
        return _respond(channel_id, error="Combatant not on battlefield.", view_room_id=view_room_id)
    cs.initiative = initiative
    await save_session_async(state)
    return _respond(channel_id, view_room_id=view_room_id)


@app.post("/session/{channel_id}/combatant/{combatant_id}/applycondition", response_class=HTMLResponse)
async def route_combatant_applycondition(
    channel_id:    str,
    combatant_id:  str,
    condition_id:  Annotated[str, Form()],
    duration:      Annotated[int, Form()] = 3,
    view_room_id:  Annotated[str, Form()] = "",
):
    """Apply a status condition to a combatant."""
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    try:
        cid = UUID(combatant_id)
    except ValueError as e:
        return _respond(channel_id, error=str(e), view_room_id=view_room_id)
    result = apply_condition(state, cid, condition_id, duration=max(1, duration))
    if not result.ok:
        return _respond(channel_id, error=result.error, view_room_id=view_room_id)
    await save_session_async(state)
    return _respond(channel_id, flash=result.message, view_room_id=view_room_id)


@app.post("/session/{channel_id}/combatant/{combatant_id}/removecondition", response_class=HTMLResponse)
async def route_combatant_removecondition(
    channel_id:    str,
    combatant_id:  str,
    condition_id:  Annotated[str, Form()],
    view_room_id:  Annotated[str, Form()] = "",
):
    """Remove a specific condition from a combatant."""
    state = store.get_session(channel_id)
    if state is None:
        return HTMLResponse("Session not found.", status_code=404)
    if state.battlefield is None:
        return _respond(channel_id, error="No active battlefield.", view_room_id=view_room_id)
    try:
        cid = UUID(combatant_id)
    except ValueError as e:
        return _respond(channel_id, error=str(e), view_room_id=view_room_id)
    char      = state.characters.get(cid)
    npc       = next((n for g in state.npc_roster.groups.values() for n in g.npcs if n.npc_id == cid), None)
    combatant = char if char else npc
    if combatant is None:
        return _respond(channel_id, error="Combatant not found.", view_room_id=view_room_id)
    before = len(combatant.active_conditions)
    combatant.active_conditions = [c for c in combatant.active_conditions if c.condition_id != condition_id]
    if len(combatant.active_conditions) == before:
        return _respond(channel_id, error=f"Condition '{condition_id}' not found.", view_room_id=view_room_id)
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


# ---------------------------------------------------------------------------
# Character sheet browser
# ---------------------------------------------------------------------------

@app.get("/characters", response_class=HTMLResponse)
async def character_index(view_char: str = "", flash: str = "", error: str = ""):
    # Get all characters from persistent character store
    char_ids = store.db.list_all_characters()
    entries = {}
    for cid in char_ids:
        char = store.db.load_character(cid)
        if char:
            entries[cid] = char
    return character_page(_session_list(), entries, flash=flash, error=error, view_char_id=view_char)


def _char_redirect(char_id: str, flash: str = "", error: str = "") -> Response:
    params = f"view_char={char_id}"
    if flash:
        params += f"&flash={flash}"
    if error:
        params += f"&error={error}"
    from fastapi.responses import RedirectResponse
    return RedirectResponse(f"/characters?{params}", status_code=303)


def _load_char_state(char_id: str):
    """Load a character from DB and wrap it in a minimal GameState for engine calls.

    IMPORTANT: After mutating the character and calling save_character_async(),
    you MUST also call sync_character_to_sessions(char). Without it, the next
    save_session_async() call (from a bot command, turn resolution, etc.) will
    write the stale in-memory copy back to DB, reverting your changes.
    """
    from models import GameState, Party
    char = store.db.load_character(char_id)
    if char is None:
        return None, None
    state = GameState(platform_channel_id="__char_edit__", dm_user_id="")
    state.party = Party(name="")
    state.characters = {char.character_id: char}
    return state, char


@app.post("/characters/{char_id}/additem", response_class=HTMLResponse)
async def route_character_additem(
    char_id: str,
    item_id: Annotated[str, Form()],
    quantity: Annotated[int, Form()] = 1,
):
    state, char = _load_char_state(char_id)
    if char is None:
        return _char_redirect(char_id, error="Character not found.")
    result = give_item(state, char.character_id, item_id, quantity)
    if not result.ok:
        return _char_redirect(char_id, error=result.error)
    await store.db.save_character_async(char)
    sync_character_to_sessions(char)
    return _char_redirect(char_id, flash=result.message)


@app.post("/characters/{char_id}/removeitem", response_class=HTMLResponse)
async def route_character_removeitem(
    char_id: str,
    item_id: Annotated[str, Form()],
    quantity: Annotated[int, Form()] = 1,
):
    state, char = _load_char_state(char_id)
    if char is None:
        return _char_redirect(char_id, error="Character not found.")
    result = remove_item(state, char.character_id, item_id, quantity)
    if not result.ok:
        return _char_redirect(char_id, error=result.error)
    await store.db.save_character_async(char)
    sync_character_to_sessions(char)
    return _char_redirect(char_id, flash=result.message)


@app.post("/characters/{char_id}/addxp", response_class=HTMLResponse)
async def route_character_addxp(
    char_id: str,
    amount: Annotated[int, Form()],
):
    state, char = _load_char_state(char_id)
    if char is None:
        return _char_redirect(char_id, error="Character not found.")
    result = award_xp(state, char.character_id, amount)
    await store.db.save_character_async(char)
    sync_character_to_sessions(char)
    if _bot:
        asyncio.create_task(drain_level_ups(_bot, state))
    return _char_redirect(char_id, flash=result.message)


@app.post("/characters/{char_id}/spellcharge", response_class=HTMLResponse)
async def route_character_spellcharge(
    char_id: str,
    item_id: Annotated[str, Form()],
    delta: Annotated[int, Form()],
):
    state, char = _load_char_state(char_id)
    if char is None:
        return _char_redirect(char_id, error="Character not found.")
    result = adjust_spell_charges(state, char.character_id, item_id, delta)
    if not result.ok:
        return _char_redirect(char_id, error=result.error)
    await store.db.save_character_async(char)
    sync_character_to_sessions(char)
    return _char_redirect(char_id, flash=result.message)


@app.post("/characters/{char_id}/spellrecharge", response_class=HTMLResponse)
async def route_character_spellrecharge(
    char_id: str,
    item_id: Annotated[str, Form()],
):
    state, char = _load_char_state(char_id)
    if char is None:
        return _char_redirect(char_id, error="Character not found.")
    # Use a large positive delta; adjust_spell_charges clamps to maxCharges.
    result = adjust_spell_charges(state, char.character_id, item_id, delta=9999)
    if not result.ok:
        return _char_redirect(char_id, error=result.error)
    await store.db.save_character_async(char)
    sync_character_to_sessions(char)
    return _char_redirect(char_id, flash=result.message)


@app.post("/characters/{char_id}/skillcharge", response_class=HTMLResponse)
async def route_character_skillcharge(
    char_id: str,
    skill_id: Annotated[str, Form()],
    delta: Annotated[int, Form()],
):
    state, char = _load_char_state(char_id)
    if char is None:
        return _char_redirect(char_id, error="Character not found.")
    result = adjust_skill_uses(state, char.character_id, skill_id, delta)
    if not result.ok:
        return _char_redirect(char_id, error=result.error)
    await store.db.save_character_async(char)
    sync_character_to_sessions(char)
    return _char_redirect(char_id, flash=result.message)


@app.post("/characters/{char_id}/skillrecharge", response_class=HTMLResponse)
async def route_character_skillrecharge(
    char_id: str,
    skill_id: Annotated[str, Form()],
):
    state, char = _load_char_state(char_id)
    if char is None:
        return _char_redirect(char_id, error="Character not found.")
    # Use a large positive delta; adjust_skill_uses clamps to max_uses.
    result = adjust_skill_uses(state, char.character_id, skill_id, delta=9999)
    if not result.ok:
        return _char_redirect(char_id, error=result.error)
    await store.db.save_character_async(char)
    sync_character_to_sessions(char)
    return _char_redirect(char_id, flash=result.message)


@app.post("/characters/{char_id}/lightcharge", response_class=HTMLResponse)
async def route_character_lightcharge(
    char_id: str,
    item_id: Annotated[str, Form()],
    delta: Annotated[int, Form()],
    equipped: Annotated[int, Form()],
):
    state, char = _load_char_state(char_id)
    if char is None:
        return _char_redirect(char_id, error="Character not found.")
    result = adjust_light_charges(state, char.character_id, item_id, delta, bool(equipped))
    if not result.ok:
        return _char_redirect(char_id, error=result.error)
    await store.db.save_character_async(char)
    sync_character_to_sessions(char)
    return _char_redirect(char_id, flash=result.message)


@app.post("/characters/{char_id}/lightrecharge", response_class=HTMLResponse)
async def route_character_lightrecharge(
    char_id: str,
    item_id: Annotated[str, Form()],
    equipped: Annotated[int, Form()],
):
    state, char = _load_char_state(char_id)
    if char is None:
        return _char_redirect(char_id, error="Character not found.")
    result = adjust_light_charges(state, char.character_id, item_id, delta=9999, equipped=bool(equipped))
    if not result.ok:
        return _char_redirect(char_id, error=result.error)
    await store.db.save_character_async(char)
    sync_character_to_sessions(char)
    return _char_redirect(char_id, flash=result.message)


@app.post("/characters/{char_id}/rechargedaily", response_class=HTMLResponse)
async def route_character_rechargedaily(char_id: str):
    state, char = _load_char_state(char_id)
    if char is None:
        return _char_redirect(char_id, error="Character not found.")
    result = recharge_day_spells(state, char.character_id)
    await store.db.save_character_async(char)
    sync_character_to_sessions(char)
    return _char_redirect(char_id, flash=result.message)

