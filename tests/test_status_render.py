"""
tests/test_status_render.py — section extraction and budget packing for the
status message (embed rendering path).

Covers:
  - render_status_sections: block coverage, per-field prose caps
  - pack_sections: total/field budgets, priority ordering, overflow markers
    (features drop from the end, say log drops oldest first)
  - render_status: legacy text format assembled from the same sections
  - build_status_embed: embed mapping (skipped when discord.py is absent,
    matching the project's no-discord-at-module-level test constraint)
"""

import pytest

from engine import (
    CAP_FEATURE_DESC,
    CAP_NPC_DESC,
    CAP_ROOM_DESC,
    CAP_SAY_ENTRY,
    CAP_SUBMISSION,
    STATUS_FIELD_CAP,
    STATUS_TOTAL_BUDGET,
    add_npc,
    enter_rounds,
    initialize_battlefield,
    pack_sections,
    render_status,
    render_status_sections,
    set_room,
    submit_turn,
)
from models import NPC, Room, RoomFeature


def _room(name="Test Room", description="A room.", num_features=0, feature_prose=100):
    room = Room(name=name, description=description)
    room.features = [
        RoomFeature(name=f"Feature {i}", description="f" * feature_prose, state="intact")
        for i in range(num_features)
    ]
    return room


def _packed_total(description, packed):
    """Replicates the embed char accounting: description + name/value per field."""
    return len(description) + sum(
        len(title) + len("\n".join(lines)) + len("```\n\n```")
        for title, lines in packed
    )


def _field_titles(packed):
    return [t for t, _ in packed]


def _field_lines(packed, title_prefix):
    return [
        line for t, lines in packed if t.startswith(title_prefix) for line in lines
    ]


# ---------------------------------------------------------------------------
# Section extraction
# ---------------------------------------------------------------------------

class TestSections:
    def test_description_shows_mode_and_gold(self, active_party_state):
        description, _ = render_status_sections(active_party_state)
        assert "Exploration" in description
        assert "Gold:" in description

    def test_expected_sections_present(self, active_party_state):
        set_room(active_party_state, _room(num_features=2))
        add_npc(active_party_state, NPC(name="Goblin", hp_max=4, hp_current=4))
        active_party_state.say_log.append('Aldric says "hello"')
        _, sections = render_status_sections(active_party_state)
        keys = {s.key for s in sections}
        assert {"party", "room", "features", "npcs", "say"} <= keys

    def test_empty_room_and_npc_omitted(self, active_party_state):
        _, sections = render_status_sections(active_party_state)
        keys = {s.key for s in sections}
        assert "room" not in keys
        assert "npcs" not in keys

    def test_feature_description_capped(self, active_party_state):
        set_room(active_party_state, _room(num_features=1, feature_prose=400))
        _, sections = render_status_sections(active_party_state)
        features = next(s for s in sections if s.key == "features")
        assert len(features.lines[0]) < CAP_FEATURE_DESC + 40  # name/bullet overhead
        assert features.lines[0].endswith("…")

    def test_room_description_capped(self, active_party_state):
        # register_room validates descriptions to ≤1000 chars; CAP_ROOM_DESC
        # is the tighter display cap applied on top.
        set_room(active_party_state, _room(description="d" * 1000))
        _, sections = render_status_sections(active_party_state)
        room = next(s for s in sections if s.key == "room")
        assert max(len(line) for line in room.lines) <= CAP_ROOM_DESC

    def test_say_entry_capped(self, active_party_state):
        active_party_state.say_log.append('Mira says "' + "s" * 400 + '"')
        _, sections = render_status_sections(active_party_state)
        say = next(s for s in sections if s.key == "say")
        assert len(say.lines[0]) <= CAP_SAY_ENTRY

    def test_npc_description_capped(self, active_party_state):
        set_room(active_party_state, _room())
        add_npc(active_party_state, NPC(
            name="Goblin", hp_max=4, hp_current=4, description="n" * 300,
        ))
        _, sections = render_status_sections(active_party_state)
        npcs = next(s for s in sections if s.key == "npcs")
        assert len(npcs.lines[0]) < CAP_NPC_DESC + 40

    def test_submission_text_capped(self, active_party_state):
        cid = active_party_state.party.member_ids[0]
        submit_turn(active_party_state, cid, "a" * 300)
        _, sections = render_status_sections(active_party_state)
        party = next(s for s in sections if s.key == "party")
        assert "…" in party.lines[0]
        assert len(party.lines[0]) < CAP_SUBMISSION + 60  # name/hp overhead


# ---------------------------------------------------------------------------
# Packing
# ---------------------------------------------------------------------------

class TestPacking:
    def _stuffed_state(self, state):
        set_room(state, _room(
            description="d" * 900, num_features=12, feature_prose=280,
        ))
        add_npc(state, NPC(name="Goblin", hp_max=4, hp_current=4))
        state.say_log.extend(
            f'Player says "line {i:02d} ' + "s" * 220 + '"' for i in range(14)
        )
        return state

    def test_stuffed_state_fits_budget(self, active_party_state):
        state = self._stuffed_state(active_party_state)
        description, sections = render_status_sections(state)
        packed = pack_sections(description, sections)
        assert _packed_total(description, packed) <= STATUS_TOTAL_BUDGET
        for title, lines in packed:
            assert len("\n".join(lines)) + len("```\n\n```") <= STATUS_FIELD_CAP, title

    def test_sacred_sections_survive_overflow(self, active_party_state):
        state = self._stuffed_state(active_party_state)
        description, sections = render_status_sections(state)
        packed = pack_sections(description, sections)
        titles = _field_titles(packed)
        assert "Party" in titles
        assert "NPCs" in titles
        assert "Room" in titles

    def test_priority_order(self, active_party_state):
        state = self._stuffed_state(active_party_state)
        description, sections = render_status_sections(state)
        packed = pack_sections(description, sections)
        titles = _field_titles(packed)
        assert titles.index("Party") < titles.index("Room") < titles.index("Features")

    def test_say_log_drops_oldest_first(self, active_party_state):
        state = self._stuffed_state(active_party_state)
        description, sections = render_status_sections(state)
        packed = pack_sections(description, sections)
        say_lines = _field_lines(packed, "Say Log")
        assert say_lines[0].startswith("… +")
        assert "earlier entries" in say_lines[0]
        # Newest entries kept, oldest dropped; kept entries stay chronological.
        assert any("line 13" in line for line in say_lines)
        assert not any("line 00" in line for line in say_lines)
        kept = [line for line in say_lines if "line " in line]
        assert kept == sorted(kept)

    def test_features_drop_from_end(self, active_party_state):
        set_room(active_party_state, _room(num_features=12, feature_prose=280))
        description, sections = render_status_sections(active_party_state)
        features = next(s for s in sections if s.key == "features")
        # Constrain the budget so features alone must degrade.
        packed = pack_sections(description, [features], total_budget=1200)
        feature_lines = _field_lines(packed, "Features")
        assert feature_lines[-1].startswith("… +")
        assert "more features" in feature_lines[-1]
        assert any("Feature 0" in line for line in feature_lines)  # front kept
        dropped_n = int(feature_lines[-1].split("+")[1].split(" ")[0])
        kept_n = len([line for line in feature_lines if "Feature" in line])
        assert kept_n + dropped_n == 12

    def test_continuation_fields_numbered(self, active_party_state):
        set_room(active_party_state, _room(num_features=12, feature_prose=280))
        description, sections = render_status_sections(active_party_state)
        packed = pack_sections(description, sections)
        titles = _field_titles(packed)
        assert "Features" in titles and "Features (2)" in titles


# ---------------------------------------------------------------------------
# Legacy text rendering (same sections, pre-embed format)
# ---------------------------------------------------------------------------

class TestLegacyRender:
    def test_placeholders_on_empty_state(self, bare_state):
        status = render_status(bare_state)
        assert "Room: (none)" in status
        assert "NPCs: none" in status

    def test_section_headers_present(self, active_party_state):
        set_room(active_party_state, _room(num_features=1))
        add_npc(active_party_state, NPC(name="Goblin", hp_max=4, hp_current=4))
        status = render_status(active_party_state)
        assert "Features:" in status
        assert "NPCs:" in status
        assert "✵Test Room✵" in status

    def test_say_log_appended(self, active_party_state):
        active_party_state.say_log.append('Aldric says "hello"')
        status = render_status(active_party_state)
        assert 'Aldric says "hello"' in status


# ---------------------------------------------------------------------------
# Embed assembly (needs discord.py — skipped in minimal dev environments)
# ---------------------------------------------------------------------------

class TestEmbed:
    def test_embed_mapping(self, active_party_state):
        pytest.importorskip("discord")
        from cogs.status_embed import build_status_embed

        set_room(active_party_state, _room(num_features=2))
        embed = build_status_embed(active_party_state)
        assert embed.description
        assert len(embed.fields) >= 2  # Party, Room, Features
        for f in embed.fields:
            assert f.value.startswith("```\n") and f.value.endswith("\n```")
            assert len(f.value) <= STATUS_FIELD_CAP

    def test_embed_total_within_budget(self, active_party_state):
        pytest.importorskip("discord")
        from cogs.status_embed import build_status_embed

        set_room(active_party_state, _room(
            description="d" * 900, num_features=12, feature_prose=280,
        ))
        active_party_state.say_log.extend("x" * 240 for _ in range(14))
        embed = build_status_embed(active_party_state)
        total = len(embed.description or "") + sum(
            len(f.name) + len(f.value) for f in embed.fields
        )
        assert total <= 6000

    def test_rounds_embed_color_and_positions(self, active_party_state):
        pytest.importorskip("discord")
        import discord

        from cogs.status_embed import build_status_embed

        result = enter_rounds(active_party_state)
        assert result.ok
        active_party_state.battlefield = initialize_battlefield(active_party_state)
        embed = build_status_embed(active_party_state)
        assert embed.color == discord.Color.red()
        assert any(f.name == "Positions" for f in embed.fields)
