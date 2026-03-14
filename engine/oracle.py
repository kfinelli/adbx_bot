"""
Oracle and say/emote functions for the dungeon crawler engine.
"""

from models import GameState, Oracle

from .helpers import _ok, _err


class OracleManager:
    """Manages oracles, say, and emote functions."""

    def say(self, state: GameState, speaker: str, text: str):
        """Add a speech entry to the say log. Shown in status block, clears each turn."""
        entry = f'{speaker} says "{text}"'
        state.say_log.append(entry)
        state.updated_at = _now()
        return _ok(state, entry)

    def emote(self, state: GameState, speaker: str, text: str):
        """Add an emote entry to the say log. Like say but no quotes."""
        entry = f"{speaker} {text}"
        state.say_log.append(entry)
        state.updated_at = _now()
        return _ok(state, entry)

    def ask_oracle(
        self,
        state:          GameState,
        asker_name:     str,
        question:       str,
        asker_owner_id: str = None,
    ):
        """
        Create a new oracle entry. Returns EngineResult with .data containing the Oracle object
        so the platform layer can post the Discord message and store the message_id back.
        """
        state.oracle_counter += 1
        oracle = Oracle(
            number=state.oracle_counter,
            asker_name=asker_name,
            asker_owner_id=asker_owner_id,
            question=question,
        )
        state.oracles.append(oracle)
        state.updated_at = _now()
        result = _ok(state, f"Oracle #{oracle.number} posted.")
        result.data = oracle
        return result

    def answer_oracle(
        self,
        state:     GameState,
        number:    int,
        answer:    str,
    ):
        """
        DM answers an oracle by number. Returns EngineResult with .data containing the Oracle object
        so the platform layer can edit the Discord message in place.
        """
        # Match the *last* oracle with this number — oracles reset to #1 each turn
        # so there may be multiple oracles with the same number across turns.
        matches = [o for o in state.oracles if o.number == number]
        oracle = matches[-1] if matches else None
        if oracle is None:
            result = _err(state, f"Oracle #{number} not found.")
            result.data = None
            return result
        oracle.answer = answer
        state.updated_at = _now()
        result = _ok(state, f"Oracle #{number} answered.")
        result.data = oracle
        return result


def _now():
    """Get current UTC datetime."""
    from datetime import UTC, datetime
    return datetime.now(UTC)
