"""
serialization.py — Convert GameState to/from plain JSON-serializable dicts.

Rules:
  - UUIDs become strings ("xxxxxxxx-xxxx-...")
  - datetimes become ISO 8601 strings
  - Enums become their .value string
  - Dataclasses become dicts
  - None stays None
  - Primitives (int, str, float, bool) pass through unchanged

The deserializer reverses each of these transformations.
"""

from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

from models import (
    NPC,
    NPCGroup,
    NPCMovementLogic,
    NPCRoster,
    AbilityScores,
    Character,
    CharacterClass,
    CharacterStatus,
    DoorState,
    Dungeon,
    Exit,
    ExitDirection,
    GameState,
    InventoryItem,
    LightSource,
    Party,
    PlayerTurnSubmission,
    PreparedSpell,
    Room,
    RoomFeature,
    SessionMode,
    SpellBook,
    TurnRecord,
    TurnStatus,
)

# ---------------------------------------------------------------------------
# Serialization (GameState → dict → JSON string)
# ---------------------------------------------------------------------------

def _uuid(v) -> str | None:
    return str(v) if v is not None else None

def _dt(v) -> str | None:
    return v.isoformat() if v is not None else None

def _enum(v) -> str | None:
    return v.value if v is not None else None


def serialize_prepared_spell(s: PreparedSpell) -> dict:
    return {"spell_name": s.spell_name, "expended": s.expended}


def serialize_spellbook(sb: SpellBook) -> dict:
    return {
        "max_slots": sb.max_slots,
        "prepared": [
            [serialize_prepared_spell(s) for s in level]
            for level in sb.prepared
        ],
        "known_spells": sb.known_spells,
    }


def serialize_inventory_item(item: InventoryItem) -> dict:
    return {
        "item_id":     _uuid(item.item_id),
        "name":        item.name,
        "description": item.description,
        "quantity":    item.quantity,
        "encumbrance": item.encumbrance,
        "is_equipped": item.is_equipped,
    }


def serialize_ability_scores(a: AbilityScores) -> dict:
    return {
        "strength":     a.strength,
        "intelligence": a.intelligence,
        "wisdom":       a.wisdom,
        "dexterity":    a.dexterity,
        "constitution": a.constitution,
        "charisma":     a.charisma,
    }


def serialize_character(c: Character) -> dict:
    return {
        "character_id":    _uuid(c.character_id),
        "owner_id":        c.owner_id,
        "name":            c.name,
        "character_class": _enum(c.character_class),
        "level":           c.level,
        "experience":      c.experience,
        "ability_scores":  serialize_ability_scores(c.ability_scores),
        "hp_max":          c.hp_max,
        "hp_current":      c.hp_current,
        "armor_class":     c.armor_class,
        "movement_speed":  c.movement_speed,
        "saving_throws":   c.saving_throws,
        "status":          _enum(c.status),
        "status_notes":    c.status_notes,
        "inventory":       [serialize_inventory_item(i) for i in c.inventory],
        "gold":            c.gold,
        "spellbook":       serialize_spellbook(c.spellbook) if c.spellbook else None,
        "created_at":      _dt(c.created_at),
        "is_pregenerated": c.is_pregenerated,
    }


def serialize_room_feature(f: RoomFeature) -> dict:
    return {
        "feature_id":  _uuid(f.feature_id),
        "name":        f.name,
        "description": f.description,
        "state":       f.state,
        "notes":       f.notes,
    }


def serialize_exit(e: Exit) -> dict:
    return {
        "exit_id":        _uuid(e.exit_id),
        "label":          e.label,
        "direction":      _enum(e.direction),
        "destination_id": _uuid(e.destination_id),
        "door_state":     _enum(e.door_state),
        "description":    e.description,
        "notes":          e.notes,
    }


def serialize_room(r: Room) -> dict:
    return {
        "room_id":     _uuid(r.room_id),
        "name":        r.name,
        "description": r.description,
        "notes":       r.notes,
        "features":    [serialize_room_feature(f) for f in r.features],
        "exits":       [serialize_exit(e) for e in r.exits],
        "visited":     r.visited,
        "authored":    r.authored,
    }


def serialize_dungeon(d: Dungeon) -> dict:
    return {
        "dungeon_id":  _uuid(d.dungeon_id),
        "name":        d.name,
        "description": d.description,
        "rooms":       {str(k): serialize_room(v) for k, v in d.rooms.items()},
        "entrance_id": _uuid(d.entrance_id),
    }


def serialize_oracle(o) -> dict:
    return {
        "oracle_id":      str(o.oracle_id),
        "number":         o.number,
        "asker_name":     o.asker_name,
        "asker_owner_id": o.asker_owner_id,
        "question":       o.question,
        "answer":         o.answer,
        "message_id":     o.message_id,
    }


def deserialize_oracle(d: dict):
    from models import Oracle
    return Oracle(
        oracle_id=UUID(d["oracle_id"]),
        number=d["number"],
        asker_name=d["asker_name"],
        asker_owner_id=d.get("asker_owner_id"),
        question=d["question"],
        answer=d.get("answer"),
        message_id=d.get("message_id"),
    )


def serialize_npc(n: NPC) -> dict:
    return {
        "npc_id":         _uuid(n.npc_id),
        "name":           n.name,
        "description":    n.description,
        "hp_max":         n.hp_max,
        "hp_current":     n.hp_current,
        "armor_class":    n.armor_class,
        "movement_speed": n.movement_speed,
        "attack_bonus":   n.attack_bonus,
        "damage_dice":    n.damage_dice,
        "morale":         n.morale,
        "saving_throw":   n.saving_throw,
        "xp_value":       n.xp_value,
        "status":         n.status,
        "notes":          n.notes,
    }


def serialize_npc_group(g: NPCGroup) -> dict:
    return {
        "group_id":        _uuid(g.group_id),
        "name":            g.name,
        "npcs":            [serialize_npc(n) for n in g.npcs],
        "possible_rooms":  [_uuid(r) for r in g.possible_rooms],
        "movement_logic":  g.movement_logic.value,
        "current_room_id": _uuid(g.current_room_id),
        "patrol_route":    [_uuid(r) for r in g.patrol_route],
    }


def serialize_npc_roster(roster: NPCRoster) -> dict:
    return {
        "groups": {str(gid): serialize_npc_group(g) for gid, g in roster.groups.items()},
    }


def serialize_light_source(ls: LightSource) -> dict:
    return {
        "label":           ls.label,
        "turns_remaining": ls.turns_remaining,
        "is_active":       ls.is_active,
    }


def serialize_party(p: Party) -> dict:
    return {
        "party_id":      _uuid(p.party_id),
        "name":          p.name,
        "leader_id":     _uuid(p.leader_id),
        "member_ids":    [str(mid) for mid in p.member_ids],
        "gold":          p.gold,
        "light_sources": [serialize_light_source(ls) for ls in p.light_sources],
    }


def serialize_submission(s: PlayerTurnSubmission) -> dict:
    return {
        "character_id": _uuid(s.character_id),
        "submitted_at": _dt(s.submitted_at),
        "action_text":  s.action_text,
        "is_latest":    s.is_latest,
    }


def serialize_turn_record(t: TurnRecord) -> dict:
    return {
        "turn_id":        _uuid(t.turn_id),
        "turn_number":    t.turn_number,
        "mode":           _enum(t.mode),
        "status":         _enum(t.status),
        "opened_at":      _dt(t.opened_at),
        "due_at":         _dt(t.due_at),
        "closed_at":      _dt(t.closed_at),
        "resolved_at":    _dt(t.resolved_at),
        "submissions":    [serialize_submission(s) for s in t.submissions],
        "resolution":     t.resolution,
        "state_snapshot": t.state_snapshot,  # already a plain dict
    }


def serialize_state(state: GameState) -> str:
    """Serialize a GameState to a JSON string for storage."""
    d = {
        "session_id":           _uuid(state.session_id),
        "dungeon":              serialize_dungeon(state.dungeon) if state.dungeon else None,
        "current_room_id":      _uuid(state.current_room_id),
        "party":                serialize_party(state.party) if state.party else None,
        "characters":           {str(k): serialize_character(v) for k, v in state.characters.items()},
        "npc_roster":           serialize_npc_roster(state.npc_roster),
        "npcs":                 [serialize_npc(n) for n in state.npcs],  # deprecated, kept for backward compatibility
        "mode":                 _enum(state.mode),
        "turn_number":          state.turn_number,
        "current_turn":         serialize_turn_record(state.current_turn) if state.current_turn else None,
        "turn_history":         [serialize_turn_record(t) for t in state.turn_history],
        "say_log":              state.say_log,
        "oracles":              [serialize_oracle(o) for o in state.oracles],
        "oracle_counter":       state.oracle_counter,
        "session_active":       state.session_active,
        "rounds_started_at_turn": state.rounds_started_at_turn,
        "default_turn_hours":   state.default_turn_hours,
        "created_at":           _dt(state.created_at),
        "updated_at":           _dt(state.updated_at),
        "platform_channel_id":  state.platform_channel_id,
        "dm_user_id":           state.dm_user_id,
    }
    return json.dumps(d, indent=2)


# ---------------------------------------------------------------------------
# Deserialization (JSON string → dict → GameState)
# ---------------------------------------------------------------------------

def _load_uuid(v) -> UUID | None:
    return UUID(v) if v is not None else None

def _load_dt(v) -> datetime | None:
    return datetime.fromisoformat(v) if v is not None else None


def deserialize_prepared_spell(d: dict) -> PreparedSpell:
    return PreparedSpell(spell_name=d["spell_name"], expended=d["expended"])


def deserialize_spellbook(d: dict) -> SpellBook:
    return SpellBook(
        max_slots=d["max_slots"],
        prepared=[
            [deserialize_prepared_spell(s) for s in level]
            for level in d["prepared"]
        ],
        known_spells=d["known_spells"],
    )


def deserialize_inventory_item(d: dict) -> InventoryItem:
    return InventoryItem(
        item_id=_load_uuid(d["item_id"]),
        name=d["name"],
        description=d["description"],
        quantity=d["quantity"],
        encumbrance=d["encumbrance"],
        is_equipped=d["is_equipped"],
    )


def deserialize_ability_scores(d: dict) -> AbilityScores:
    return AbilityScores(
        strength=d["strength"],
        intelligence=d["intelligence"],
        wisdom=d["wisdom"],
        dexterity=d["dexterity"],
        constitution=d["constitution"],
        charisma=d["charisma"],
    )


def deserialize_character(d: dict) -> Character:
    return Character(
        character_id=_load_uuid(d["character_id"]),
        owner_id=d["owner_id"],
        name=d["name"],
        character_class=CharacterClass(d["character_class"]),
        level=d["level"],
        experience=d["experience"],
        ability_scores=deserialize_ability_scores(d["ability_scores"]),
        hp_max=d["hp_max"],
        hp_current=d["hp_current"],
        armor_class=d["armor_class"],
        movement_speed=d["movement_speed"],
        saving_throws=d["saving_throws"],
        status=CharacterStatus(d["status"]),
        status_notes=d["status_notes"],
        inventory=[deserialize_inventory_item(i) for i in d["inventory"]],
        gold=d["gold"],
        spellbook=deserialize_spellbook(d["spellbook"]) if d["spellbook"] else None,
        created_at=_load_dt(d["created_at"]),
        is_pregenerated=d["is_pregenerated"],
    )


def deserialize_room_feature(d: dict) -> RoomFeature:
    return RoomFeature(
        feature_id=_load_uuid(d["feature_id"]),
        name=d["name"],
        description=d["description"],
        state=d["state"],
        notes=d["notes"],
    )


def deserialize_exit(d: dict) -> Exit:
    return Exit(
        exit_id=_load_uuid(d["exit_id"]),
        label=d["label"],
        direction=ExitDirection(d["direction"]) if d["direction"] else None,
        destination_id=_load_uuid(d["destination_id"]),
        door_state=DoorState(d["door_state"]),
        description=d["description"],
        notes=d["notes"],
    )


def deserialize_room(d: dict) -> Room:
    return Room(
        room_id=_load_uuid(d["room_id"]),
        name=d["name"],
        description=d["description"],
        notes=d["notes"],
        features=[deserialize_room_feature(f) for f in d["features"]],
        exits=[deserialize_exit(e) for e in d["exits"]],
        visited=d["visited"],
        authored=d["authored"],
    )


def deserialize_dungeon(d: dict) -> Dungeon:
    rooms = {UUID(k): deserialize_room(v) for k, v in d["rooms"].items()}
    return Dungeon(
        dungeon_id=_load_uuid(d["dungeon_id"]),
        name=d["name"],
        description=d["description"],
        rooms=rooms,
        entrance_id=_load_uuid(d["entrance_id"]),
    )


def deserialize_npc(d: dict) -> NPC:
    return NPC(
        npc_id=_load_uuid(d["npc_id"]),
        name=d["name"],
        description=d["description"],
        hp_max=d["hp_max"],
        hp_current=d["hp_current"],
        armor_class=d["armor_class"],
        movement_speed=d["movement_speed"],
        attack_bonus=d["attack_bonus"],
        damage_dice=d["damage_dice"],
        morale=d["morale"],
        saving_throw=d["saving_throw"],
        xp_value=d["xp_value"],
        status=d["status"],
        notes=d["notes"],
    )


def deserialize_npc_group(d: dict) -> NPCGroup:
    return NPCGroup(
        group_id=_load_uuid(d["group_id"]),
        name=d["name"],
        npcs=[deserialize_npc(n) for n in d["npcs"]],
        possible_rooms=[_load_uuid(r) for r in d["possible_rooms"]],
        movement_logic=NPCMovementLogic(d["movement_logic"]),
        current_room_id=_load_uuid(d["current_room_id"]),
        patrol_route=[_load_uuid(r) for r in d["patrol_route"]],
    )


def deserialize_npc_roster(d: dict) -> NPCRoster:
    roster = NPCRoster()
    groups_data = d.get("groups", {})
    for gid_str, gdata in groups_data.items():
        group = deserialize_npc_group(gdata)
        roster.groups[UUID(gid_str)] = group
    return roster


def deserialize_light_source(d: dict) -> LightSource:
    return LightSource(
        label=d["label"],
        turns_remaining=d["turns_remaining"],
        is_active=d["is_active"],
    )


def deserialize_party(d: dict) -> Party:
    return Party(
        party_id=_load_uuid(d["party_id"]),
        name=d["name"],
        leader_id=_load_uuid(d["leader_id"]),
        member_ids=[UUID(mid) for mid in d["member_ids"]],
        gold=d["gold"],
        light_sources=[deserialize_light_source(ls) for ls in d["light_sources"]],
    )


def deserialize_submission(d: dict) -> PlayerTurnSubmission:
    return PlayerTurnSubmission(
        character_id=_load_uuid(d["character_id"]),
        submitted_at=_load_dt(d["submitted_at"]),
        action_text=d["action_text"],
        is_latest=d["is_latest"],
    )


def deserialize_turn_record(d: dict) -> TurnRecord:
    return TurnRecord(
        turn_id=_load_uuid(d["turn_id"]),
        turn_number=d["turn_number"],
        mode=SessionMode(d["mode"]),
        status=TurnStatus(d["status"]),
        opened_at=_load_dt(d["opened_at"]),
        due_at=_load_dt(d["due_at"]),
        closed_at=_load_dt(d["closed_at"]),
        resolved_at=_load_dt(d["resolved_at"]),
        submissions=[deserialize_submission(s) for s in d["submissions"]],
        resolution=d["resolution"],
        state_snapshot=d["state_snapshot"],
    )


def deserialize_state(json_str: str) -> GameState:
    """Reconstruct a GameState from a JSON string."""
    d = json.loads(json_str)
    
    # Deserialize NPC roster (new field)
    npc_roster_data = d.get("npc_roster")
    if npc_roster_data:
        npc_roster = deserialize_npc_roster(npc_roster_data)
    else:
        # Backward compatibility: if no npc_roster, create empty one
        npc_roster = NPCRoster()
    
    return GameState(
        session_id=_load_uuid(d["session_id"]),
        dungeon=deserialize_dungeon(d["dungeon"]) if d["dungeon"] else None,
        current_room_id=_load_uuid(d["current_room_id"]),
        party=deserialize_party(d["party"]) if d["party"] else None,
        characters={
            UUID(k): deserialize_character(v)
            for k, v in d["characters"].items()
        },
        npc_roster=npc_roster,
        npcs=[deserialize_npc(n) for n in d.get("npcs", [])],  # deprecated, kept for backward compatibility
        mode=SessionMode(d["mode"]),
        turn_number=d["turn_number"],
        current_turn=deserialize_turn_record(d["current_turn"]) if d["current_turn"] else None,
        turn_history=[deserialize_turn_record(t) for t in d["turn_history"]],
        say_log=d.get("say_log", []),
        oracles=[deserialize_oracle(o) for o in d.get("oracles", [])],
        oracle_counter=d.get("oracle_counter", 0),
        session_active=d.get("session_active", True),
        rounds_started_at_turn=d.get("rounds_started_at_turn", None),
        default_turn_hours=d.get("default_turn_hours", 24.0),
        created_at=_load_dt(d["created_at"]),
        updated_at=_load_dt(d["updated_at"]),
        platform_channel_id=d["platform_channel_id"],
        dm_user_id=d["dm_user_id"],
    )


# ---------------------------------------------------------------------------
# Standalone dungeon file (import / export)
# ---------------------------------------------------------------------------
# A dungeon file is a self-contained JSON document representing only the
# Dungeon object — no session state, no characters, no party. This is the
# format used for the web UI importer/exporter and for sharing dungeons.
#
# Schema:
#   {
#     "format":  "adbx-dungeon",   // sentinel to catch wrong file types
#     "version": 1,
#     "dungeon": { ...serialize_dungeon() output... }
#   }

_DUNGEON_FILE_FORMAT  = "adbx-dungeon"
_DUNGEON_FILE_VERSION = 1


def serialize_dungeon_file(dungeon: Dungeon) -> str:
    """Produce a pretty-printed JSON string suitable for download."""
    doc = {
        "format":  _DUNGEON_FILE_FORMAT,
        "version": _DUNGEON_FILE_VERSION,
        "dungeon": serialize_dungeon(dungeon),
    }
    return json.dumps(doc, indent=2)


def deserialize_dungeon_file(json_str: str) -> Dungeon:
    """
    Parse a dungeon file JSON string and return a Dungeon.
    Raises ValueError with a human-readable message on any problem.
    """
    try:
        doc = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}") from e

    if doc.get("format") != _DUNGEON_FILE_FORMAT:
        raise ValueError(
            f"Unrecognised file format '{doc.get('format')}'. "
            f"Expected '{_DUNGEON_FILE_FORMAT}'."
        )
    version = doc.get("version", 0)
    if version != _DUNGEON_FILE_VERSION:
        raise ValueError(
            f"Unsupported dungeon file version {version} "
            f"(this build supports version {_DUNGEON_FILE_VERSION})."
        )
    if "dungeon" not in doc:
        raise ValueError("Missing 'dungeon' key in dungeon file.")

    try:
        return deserialize_dungeon(doc["dungeon"])
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"Dungeon data is malformed: {e}") from e
