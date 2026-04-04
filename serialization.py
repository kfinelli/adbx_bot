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

from engine.azure_constants import DEFAULT_EQUIPPED_SLOTS
from models import (
    NPC,
    ActiveCondition,
    AzureStats,
    Character,
    CharacterClass,
    CharacterStatus,
    CombatantState,
    CombatBattlefield,
    DoorState,
    Dungeon,
    EncounterEntry,
    Exit,
    ExitDirection,
    GameState,
    InventoryItem,
    JobExperience,
    LightSource,
    NPCGroup,
    NPCMovementLogic,
    NPCRoster,
    Party,
    PlayerTurnSubmission,
    RangeBand,
    Room,
    RoomFeature,
    SessionMode,
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


def serialize_inventory_item(item: InventoryItem) -> dict:
    return {
        "item_id":      item.item_id,   # str — no UUID wrapping needed
        "quantity":     item.quantity,
        "equipped":     item.equipped,
        "broken":       item.broken,
        "charges":      item.charges,
        "notes":        item.notes,
        "container_id": item.container_id,
    }


def serialize_job_experience(j: JobExperience) -> dict:
    return {
        "job_id":       j.job_id,
        "level":        j.level,
        "hp_bonus":     j.hp_bonus,
        "stat_bonuses": dict(j.stat_bonuses),
    }


def deserialize_job_experience(d: dict) -> JobExperience:
    return JobExperience(
        job_id=d["job_id"],
        level=d.get("level", 1),
        hp_bonus=d.get("hp_bonus", 0),
        stat_bonuses=d.get("stat_bonuses", {
            "physique": 0, "finesse": 0, "reason": 0, "savvy": 0,
        }),
    )


def serialize_ability_scores(a: AzureStats) -> dict:
    return {
        "physique": a.physique,
        "finesse":  a.finesse,
        "reason":   a.reason,
        "savvy":    a.savvy,
    }


def serialize_character(c: Character) -> dict:
    return {
        "character_id":    _uuid(c.character_id),
        "owner_id":        c.owner_id,
        "name":            c.name,
        "jobs":            {k: serialize_job_experience(v) for k, v in c.jobs.items()},
        "level":           c.level,
        "experience":      c.experience,
        "ability_scores":  serialize_ability_scores(c.ability_scores),
        "hp_max":          c.hp_max,
        "hp_current":      c.hp_current,
        "movement_speed":  c.movement_speed,
        "saving_throws":   c.saving_throws,
        "status":          _enum(c.status),
        "status_notes":    c.status_notes,
        "inventory":       [serialize_inventory_item(i) for i in c.inventory],
        "gold":            c.gold,
        "equipped_slots":   c.equipped_slots,  # dict[str, str|None] — no special encoding needed
        "created_at":       _dt(c.created_at),
        "is_pregenerated":  c.is_pregenerated,
        "active_conditions": [serialize_active_condition(cond) for cond in c.active_conditions],
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
        "auto_move":      e.auto_move,
        "description":    e.description,
        "notes":          e.notes,
    }


def serialize_room(r: Room) -> dict:
    return {
        "room_id":     _uuid(r.room_id),
        "name":        r.name,
        "description": r.description,
        "notes":       r.notes,
        "features":       [serialize_room_feature(f) for f in r.features],
        "exits":          [serialize_exit(e) for e in r.exits],
        "visited":        r.visited,
        "authored":       r.authored,
        "exploration_xp": r.exploration_xp,
        "random_encounter_modifier": r.random_encounter_modifier,
    }


def serialize_dungeon(d: Dungeon) -> dict:
    return {
        "dungeon_id":  _uuid(d.dungeon_id),
        "name":        d.name,
        "description": d.description,
        "rooms":       {str(k): serialize_room(v) for k, v in d.rooms.items()},
        "entrance_id": _uuid(d.entrance_id),
        "random_encounter_interval": d.random_encounter_interval,
        "random_encounter_roll":     d.random_encounter_roll,
        "random_encounter_roster": [
            {"npc_group": serialize_npc_group(e.npc_group), "weight": e.weight}
            for e in d.random_encounter_roster
        ],
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
        "defense":        n.defense,
        "resistance":     n.resistance,
        "ability_scores": serialize_ability_scores(n.ability_scores),
        "movement_speed": n.movement_speed,
        "attack_bonus":   n.attack_bonus,
        "damage_dice":    n.damage_dice,
        "morale":         n.morale,
        "saving_throw":   n.saving_throw,
        "hit_dice":       n.hit_dice,
        "status":            n.status,
        "notes":             n.notes,
        "active_conditions": [serialize_active_condition(cond) for cond in n.active_conditions],
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


def serialize_active_condition(c: ActiveCondition) -> dict:
    return {
        "condition_id":    c.condition_id,
        "duration_rounds": c.duration_rounds,
        "source_id":       _uuid(c.source_id),
        "stacks":          c.stacks,
    }


def serialize_combatant_state(cs: CombatantState) -> dict:
    return {
        "combatant_id":      _uuid(cs.combatant_id),
        "is_player":         cs.is_player,
        "range_band":        _enum(cs.range_band),
        "initiative":        cs.initiative,
        "acted_this_round":  cs.acted_this_round,
    }


def serialize_battlefield(bf: CombatBattlefield) -> dict:
    return {
        "combatants": {
            str(cid): serialize_combatant_state(cs)
            for cid, cs in bf.combatants.items()
        },
        "round_log": list(bf.round_log),
    }


def serialize_submission(s: PlayerTurnSubmission) -> dict:
    return {
        "character_id":  _uuid(s.character_id),
        "submitted_at":  _dt(s.submitted_at),
        "action_text":   s.action_text,
        "is_latest":     s.is_latest,
        "combat_action": s.combat_action,   # plain dict or None — passes through unchanged
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


def serialize_state(state: GameState, include_characters: bool = True) -> str:
    """Serialize a GameState to a JSON string for storage.

    Args:
        state: The GameState to serialize.
        include_characters: If True (default), characters are included in the JSON.
            Set to False when storing session state separately from character data.
    """
    d = {
        "session_id":           _uuid(state.session_id),
        "dungeon":              serialize_dungeon(state.dungeon) if state.dungeon else None,
        "current_room_id":      _uuid(state.current_room_id),
        "party":                serialize_party(state.party) if state.party else None,
        "characters":           {str(k): serialize_character(v) for k, v in state.characters.items()} if include_characters else {},
        "npc_roster":           serialize_npc_roster(state.npc_roster),
        "mode":                 _enum(state.mode),
        "turn_number":          state.turn_number,
        "current_turn":         serialize_turn_record(state.current_turn) if state.current_turn else None,
        "turn_history":         [serialize_turn_record(t) for t in state.turn_history],
        "say_log":              state.say_log,
        "oracles":              [serialize_oracle(o) for o in state.oracles],
        "oracle_counter":       state.oracle_counter,
        "session_active":       state.session_active,
        "battlefield":          serialize_battlefield(state.battlefield) if state.battlefield else None,
        "rounds_started_at_turn": state.rounds_started_at_turn,
        "last_encounter_check_turn": state.last_encounter_check_turn,
        "default_turn_hours":   state.default_turn_hours,
        "created_at":           _dt(state.created_at),
        "updated_at":           _dt(state.updated_at),
        "platform_channel_id":  state.platform_channel_id,
        "dm_user_id":           state.dm_user_id,
    }
    return json.dumps(d, indent=2)


def serialize_state_without_characters(state: GameState) -> str:
    """Serialize a GameState excluding character data (for separate storage)."""
    return serialize_state(state, include_characters=False)


# ---------------------------------------------------------------------------
# Deserialization (JSON string → dict → GameState)
# ---------------------------------------------------------------------------

def _load_uuid(v) -> UUID | None:
    return UUID(v) if v is not None else None

def _load_dt(v) -> datetime | None:
    return datetime.fromisoformat(v) if v is not None else None


def deserialize_inventory_item(d: dict) -> InventoryItem:
    return InventoryItem(
        item_id=d["item_id"],
        quantity=d.get("quantity", 1),
        equipped=d.get("equipped", False),
        broken=d.get("broken", False),
        charges=d.get("charges"),
        notes=d.get("notes", ""),
        container_id=d.get("container_id"),
    )


def deserialize_ability_scores(d: dict) -> AzureStats:
    return AzureStats(
        physique=d.get("physique", d.get("strength", 0)),
        finesse=d.get("finesse",  d.get("dexterity", 0)),
        reason=d.get("reason",   d.get("intelligence", 0)),
        savvy=d.get("savvy",     d.get("wisdom", 0)),
    )


def deserialize_character(d: dict) -> Character:
    # Merge saved slots onto the current ruleset defaults.
    # Characters saved before equipped_slots existed get all-None slots.
    # Unknown slot keys from future versions are silently dropped.
    valid_slot_keys = set(DEFAULT_EQUIPPED_SLOTS.keys())
    saved_slots: dict = d.get("equipped_slots", {})
    merged_slots = {
        **DEFAULT_EQUIPPED_SLOTS,
        **{k: v for k, v in saved_slots.items() if k in valid_slot_keys},
    }

    # Handle both new format ("jobs" dict) and old format ("character_class" string).
    if "jobs" in d:
        jobs = {k: deserialize_job_experience(v) for k, v in d["jobs"].items()}
    else:
        # Migrate: reconstruct a single JobExperience from the old character_class field.
        old_val  = d.get("character_class", "Knight")
        job_key  = CharacterClass(old_val).name.lower()
        jobs = {job_key: JobExperience(job_id=job_key, level=d.get("level", 1))}

    return Character(
        character_id=_load_uuid(d["character_id"]),
        owner_id=d["owner_id"],
        name=d["name"],
        jobs=jobs,
        level=d["level"],
        experience=d["experience"],
        ability_scores=deserialize_ability_scores(d["ability_scores"]),
        hp_max=d["hp_max"],
        hp_current=d["hp_current"],
        movement_speed=d["movement_speed"],
        saving_throws=d["saving_throws"],
        status=CharacterStatus(d["status"]),
        status_notes=d["status_notes"],
        inventory=[deserialize_inventory_item(i) for i in d["inventory"]],
        gold=d["gold"],
        equipped_slots=merged_slots,
        created_at=_load_dt(d["created_at"]),
        is_pregenerated=d["is_pregenerated"],
        active_conditions=[deserialize_active_condition(c) for c in d.get("active_conditions", [])],
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
        auto_move=d.get("auto_move", False),
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
        exploration_xp=d.get("exploration_xp", 0),
        random_encounter_modifier=d.get("random_encounter_modifier", 1.0),
    )


def deserialize_dungeon(d: dict) -> Dungeon:
    rooms = {UUID(k): deserialize_room(v) for k, v in d["rooms"].items()}
    return Dungeon(
        dungeon_id=_load_uuid(d["dungeon_id"]),
        name=d["name"],
        description=d["description"],
        rooms=rooms,
        entrance_id=_load_uuid(d["entrance_id"]),
        random_encounter_interval=d.get("random_encounter_interval", 6),
        random_encounter_roll=d.get("random_encounter_roll", "1d6"),
        random_encounter_roster=[
            EncounterEntry(
                npc_group=deserialize_npc_group(e["npc_group"]),
                weight=e.get("weight", 1),
            )
            for e in d.get("random_encounter_roster", [])
        ],
    )


def deserialize_npc(d: dict) -> NPC:
    return NPC(
        npc_id=_load_uuid(d["npc_id"]),
        name=d["name"],
        description=d["description"],
        hp_max=d["hp_max"],
        hp_current=d["hp_current"],
        defense=d["defense"],
        resistance=d["resistance"],
        ability_scores=deserialize_ability_scores(d["ability_scores"]),
        movement_speed=d["movement_speed"],
        attack_bonus=d["attack_bonus"],
        damage_dice=d["damage_dice"],
        morale=d["morale"],
        saving_throw=d["saving_throw"],
        hit_dice=d.get("hit_dice", 1),
        status=d["status"],
        notes=d["notes"],
        active_conditions=[deserialize_active_condition(c) for c in d.get("active_conditions", [])],
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


def deserialize_active_condition(d: dict) -> ActiveCondition:
    return ActiveCondition(
        condition_id=d["condition_id"],
        duration_rounds=d.get("duration_rounds"),
        source_id=_load_uuid(d.get("source_id")),
        stacks=d.get("stacks", 1),
    )


def deserialize_combatant_state(d: dict) -> CombatantState:
    return CombatantState(
        combatant_id=_load_uuid(d["combatant_id"]),
        is_player=d["is_player"],
        range_band=RangeBand(d["range_band"]),
        initiative=d.get("initiative", 0),
        acted_this_round=d.get("acted_this_round", False),
    )


def deserialize_battlefield(d: dict) -> CombatBattlefield:
    return CombatBattlefield(
        combatants={
            UUID(cid): deserialize_combatant_state(cs)
            for cid, cs in d.get("combatants", {}).items()
        },
        round_log=list(d.get("round_log", [])),
    )


def deserialize_submission(d: dict) -> PlayerTurnSubmission:
    return PlayerTurnSubmission(
        character_id=_load_uuid(d["character_id"]),
        submitted_at=_load_dt(d["submitted_at"]),
        action_text=d["action_text"],
        is_latest=d["is_latest"],
        combat_action=d.get("combat_action"),   # None for old saves and exploration turns
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


def deserialize_state(json_str: str, characters: dict | None = None) -> GameState:
    """Reconstruct a GameState from a JSON string.

    Args:
        json_str: The JSON string to deserialize.
        characters: Optional dict of Character objects keyed by UUID string.
            If provided, these characters are used instead of deserializing
            from the JSON (for loading session state with separate character store).
    """
    d = json.loads(json_str)

    # Deserialize NPC roster (new field)
    npc_roster_data = d.get("npc_roster")
    if npc_roster_data:
        npc_roster = deserialize_npc_roster(npc_roster_data)
    else:
        # Backward compatibility: if no npc_roster, create empty one
        npc_roster = NPCRoster()

    # Use provided characters or deserialize from JSON
    if characters is not None:
        char_dict = characters
    else:
        char_dict = {
            UUID(k): deserialize_character(v)
            for k, v in d.get("characters", {}).items()
        }

    return GameState(
        session_id=_load_uuid(d["session_id"]),
        dungeon=deserialize_dungeon(d["dungeon"]) if d["dungeon"] else None,
        current_room_id=_load_uuid(d["current_room_id"]),
        party=deserialize_party(d["party"]) if d["party"] else None,
        characters=char_dict,
        npc_roster=npc_roster,
        mode=SessionMode(d["mode"]),
        turn_number=d["turn_number"],
        current_turn=deserialize_turn_record(d["current_turn"]) if d["current_turn"] else None,
        turn_history=[deserialize_turn_record(t) for t in d["turn_history"]],
        say_log=d.get("say_log", []),
        oracles=[deserialize_oracle(o) for o in d.get("oracles", [])],
        oracle_counter=d.get("oracle_counter", 0),
        session_active=d.get("session_active", True),
        battlefield=(
            deserialize_battlefield(d["battlefield"])
            if d.get("battlefield") else None
        ),
        rounds_started_at_turn=d.get("rounds_started_at_turn", None),
        last_encounter_check_turn=d.get("last_encounter_check_turn", 0),
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
# Dungeon object and NPC roster — no session state, no characters, no party.
# This is the format used for the web UI importer/exporter and for sharing
# dungeons.
#
# Schema:
#   {
#     "format":  "adbx-dungeon",   // sentinel to catch wrong file types
#     "version": 3,
#     "dungeon": { ...serialize_dungeon() output... }
#     "npc_roster": { ... serialize_npc_roster() output ... }
#   }

_DUNGEON_FILE_FORMAT  = "adbx-dungeon"
_DUNGEON_FILE_VERSION = 3


def serialize_dungeon_file(dungeon: Dungeon, npc_roster: NPCRoster | None = None) -> str:
    """Produce a pretty-printed JSON string suitable for download."""
    doc = {
        "format":  _DUNGEON_FILE_FORMAT,
        "version": _DUNGEON_FILE_VERSION,
        "dungeon": serialize_dungeon(dungeon),
        "npc_roster": serialize_npc_roster(npc_roster) if npc_roster is not None else serialize_npc_roster(NPCRoster()),
    }
    return json.dumps(doc, indent=2)


def deserialize_dungeon_file(json_str: str) -> tuple[Dungeon, NPCRoster]:
    """
    Parse a dungeon file JSON string and return a (Dungeon, NPCRoster) tuple.
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
        dungeon = deserialize_dungeon(doc["dungeon"])
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"Dungeon data is malformed: {e}") from e

    # Deserialize NPC roster (may be missing in older files, but we require v2)
    npc_roster_data = doc.get("npc_roster")
    if npc_roster_data is None:
        npc_roster = NPCRoster()
    else:
        try:
            npc_roster = deserialize_npc_roster(npc_roster_data)
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"NPC roster data is malformed: {e}") from e

    return dungeon, npc_roster
