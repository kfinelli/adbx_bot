"""
test_webui_resolve.py — Regression tests for the DM resolve route's Discord dispatch.

Bug: route_resolve passed the local `full_narrative` (the raw DM form input) to
dispatch_turn_resolved instead of `result.message`. resolve_turn appends the
random-encounter announcement (combat.encounter.appears) to its returned
message, so the announcement was silently dropped and never reached the channel.

webui.app transitively imports discord (via store and discord_tasks), so these
tests skip when discord.py is unavailable — same pattern as the embed tests in
test_status_render.py. Module-level imports stay discord/store-free per suite rules.
"""

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models import NPC, Dungeon, EncounterEntry, NPCGroup, Room


def _dungeon_with_roster(interval: int):
    """Single-room dungeon with a one-entry random-encounter roster."""
    room = Room(name="Crypt")
    dungeon = Dungeon(
        name="Test Dungeon",
        random_encounter_interval=interval,
        random_encounter_roll="1d6",
    )
    dungeon.rooms[room.room_id] = room
    dungeon.entrance_id = room.room_id
    npc = NPC(name="Skeleton 1", hp_max=6, hp_current=6)
    group = NPCGroup(name="Skeletons", npcs=[npc])
    dungeon.random_encounter_roster = [EncounterEntry(npc_group=group, weight=1)]
    return dungeon, room.room_id


@contextlib.contextmanager
def _patched_route(webui_app, state):
    """Patch webui.app's Discord/DB boundaries; yield (channel, dispatch mock)."""
    channel = MagicMock()
    bot = MagicMock()
    bot.get_channel.return_value = channel
    with (
        patch.object(webui_app.store, "get_session", return_value=state),
        patch.object(webui_app, "save_session_async", new=AsyncMock()),
        patch.object(webui_app, "dispatch_turn_resolved", new=AsyncMock()) as mock_dispatch,
        patch.object(webui_app, "_respond", new=MagicMock()),
        patch.object(webui_app, "_bot", bot),
    ):
        yield channel, mock_dispatch


class TestResolveRouteEncounterAnnouncement:
    async def test_encounter_announcement_included_in_dispatch(self, active_state):
        """When a random encounter fires on resolve, the announcement reaches Discord."""
        pytest.importorskip("discord")
        import webui.app as webui_app

        # Interval of 1 guarantees a check on resolve; the patched roll (1)
        # meets the default threshold (≤1) so the encounter always fires.
        dungeon, room_id = _dungeon_with_roster(interval=1)
        active_state.dungeon = dungeon
        active_state.current_room_id = room_id

        with (
            _patched_route(webui_app, active_state) as (channel, mock_dispatch),
            patch("engine.encounter.roll_dice_expr", return_value={"total": 1}),
        ):
            await webui_app.route_resolve("12345", narrative="The party advances.")
            await asyncio.sleep(0)  # let the fire-and-forget dispatch task run

        mock_dispatch.assert_called_once()
        dispatched_channel, _, narrative_sent = mock_dispatch.call_args.args
        assert dispatched_channel is channel
        assert "The party advances." in narrative_sent
        assert "Skeletons appears!" in narrative_sent

    async def test_no_encounter_dispatches_plain_narrative(self, active_state):
        """Control: without an encounter the DM narrative is dispatched unchanged."""
        pytest.importorskip("discord")
        import webui.app as webui_app

        dungeon, room_id = _dungeon_with_roster(interval=99)  # interval not reached
        active_state.dungeon = dungeon
        active_state.current_room_id = room_id

        with _patched_route(webui_app, active_state) as (_, mock_dispatch):
            await webui_app.route_resolve("12345", narrative="The party advances.")
            await asyncio.sleep(0)

        mock_dispatch.assert_called_once()
        assert mock_dispatch.call_args.args[2] == "The party advances."
