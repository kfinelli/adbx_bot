"""
dev_webui.py — Standalone template preview server.

Serves the web UI templates against fixture data so you can iterate on
templates.py without running the bot or connecting to Discord.

Usage:
    python dev_webui.py           # serves on http://localhost:8001
    python dev_webui.py 8080      # custom port

Every request re-imports templates.py from disk, so a browser refresh
picks up your latest changes immediately — no server restart needed.

Routes mirror the real app:
    /                   → session list page
    /session/<id>       → dashboard for the fixture session
    /archive            → archive page (empty)
    /characters         → character browser (if you've added that page)
"""

from __future__ import annotations

import importlib
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from engine import (
    add_exit,
    add_npc,
    create_character,
    give_item,
    register_room,
    start_session,
    submit_turn,
)
from engine.azure_engine import CharacterClass
from models import (
    NPC,
    DoorState,
    GameState,
    Party,
    Room,
    RoomFeature,
)

# ---------------------------------------------------------------------------
# Fixture data — edit freely to test different UI states
# ---------------------------------------------------------------------------

FIXTURE_CHANNEL_ID = "111222333444555666"
FIXTURE_SESSION_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _build_fixture_state() -> GameState:
    """
    Build a realistic GameState using the real engine — no mocking needed.
    Edit this function to test different states (on hold, closed turn, etc.)
    """
    state = GameState(
        platform_channel_id=FIXTURE_CHANNEL_ID,
        dm_user_id="999000999",
    )
    state.party = Party(name="The Delvers")

    for name, cls, owner in [
        ("Aldric",        CharacterClass.KNIGHT,    "u1"),
        ("Mira",          CharacterClass.MAGE, "u2"),
        ("Brother Tomas", CharacterClass.KNIGHT,     "u3"),
    ]:
        create_character(state, name, cls, "Pack A", owner_id=owner)

    state.party.leader_id = list(state.party.member_ids)[0]

    # Give each character some inventory for UI preview
    char_ids = list(state.party.member_ids)
    give_item(state, char_ids[0], "longsword")
    give_item(state, char_ids[0], "kite_shield")
    give_item(state, char_ids[0], "torch", 3)
    give_item(state, char_ids[1], "staff")
    give_item(state, char_ids[1], "dagger", 2)
    give_item(state, char_ids[2], "shortsword")
    give_item(state, char_ids[2], "short_bow")
    give_item(state, char_ids[2], "torch", 6)

    start_session(state)

    # Build a dungeon room
    room = Room(
        name="The Entrance Hall",
        description="A vaulted stone chamber. Torches flicker in iron sconces.",
        notes="Hidden pit trap near the east door (Search DC 12 to find, DC 10 to disarm).",
    )
    room.features.append(RoomFeature(
        name="Mosaic Floor",
        description="A cracked mosaic depicting a serpent devouring its own tail.",
        notes="Loose tile in the centre — false floor, drops 10ft.",
    ))
    register_room(state, room)
    state.current_room_id = room.room_id

    add_exit(state, "North Door",  "Heavy oak door, slightly ajar.", DoorState.OPEN)
    add_exit(state, "East Door",   "Iron-banded, locked from the other side.", DoorState.LOCKED)
    add_exit(state, "Trapdoor",    "Iron ring set into the floor. Leads down.", DoorState.OPEN)

    add_npc(state, NPC(name="Skeletal Guard",  hp_current=4, hp_max=8))
    add_npc(state, NPC(name="Stirge (wounded)", hp_current=1, hp_max=3))

    # Simulate one submission so the turn panel shows something
    submit_turn(state, char_ids[0], "Search: I check the mosaic floor carefully for loose tiles.")

    return state


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------

_FIXTURE_STATE = None  # built once so UUIDs are stable across requests


class PreviewHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        print(f"  {self.path}  →  {args[1]}")

    def _send(self, html: str, status: int = 200):
        body = html.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _reload_templates(self):
        """Re-import templates on every request so edits are picked up instantly."""
        import webui.templates as tmpl
        importlib.reload(tmpl)
        return tmpl

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)

        tmpl = self._reload_templates()
        state = _FIXTURE_STATE
        sessions = [(FIXTURE_CHANNEL_ID, "#fixture-channel")]

        try:
            if path == "/":
                html = tmpl.session_list_page(sessions)

            elif path == f"/session/{FIXTURE_CHANNEL_ID}":
                view_room_id = qs.get("view_room_id", [""])[0]
                html = tmpl.session_page(
                    state=state,
                    sessions=sessions,
                    view_room_id=view_room_id,
                )

            elif path == "/archive":
                html = tmpl.archive_page(sessions=sessions, entries=[])

            elif path == "/characters":
                view_char = qs.get("view_char", [""])[0]
                # Call character_page if it exists, otherwise show a placeholder
                if hasattr(tmpl, "character_page"):
                    html = tmpl.character_page(
                        sessions=sessions,
                        entries=state.characters,
                        view_char_id=view_char,
                    )
                else:
                    html = tmpl.page(
                        "Characters — DM Panel",
                        "<p style='padding:2rem;color:#888'>character_page() not yet defined in templates.py</p>",
                    )

            elif path == "/":
                html = tmpl.session_list_page(sessions)

            else:
                html = tmpl.page("404", f"<p style='padding:2rem;color:#888'>No preview route for <code>{path}</code></p>")
                self._send(html, 404)
                return

            self._send(html)

        except Exception:
            import traceback
            tb = traceback.format_exc()
            # Render the traceback in the browser so you don't have to watch the terminal
            error_html = f"""<!doctype html><html><body style="background:#1a0000;color:#ff6b6b;
font-family:monospace;padding:2rem"><h2>Template error</h2><pre>{tb}</pre></body></html>"""
            print(f"\nTemplate error on {path}:\n{tb}")
            self._send(error_html, 500)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8001
    _FIXTURE_STATE = _build_fixture_state()
    state = _FIXTURE_STATE   # validate fixtures at startup
    print(f"Fixture state: {len(state.characters)} characters, "
          f"session {FIXTURE_CHANNEL_ID[:8]}...")
    print(f"Preview server running at http://localhost:{port}")
    print("  /                          → session list")
    print(f"  /session/{FIXTURE_CHANNEL_ID[:16]}...  → dashboard")
    print("  /archive                   → archive page")
    print("  /characters                → character browser")
    print("\nEdit templates.py and refresh — no restart needed.\n")
    HTTPServer(("", port), PreviewHandler).serve_forever()
