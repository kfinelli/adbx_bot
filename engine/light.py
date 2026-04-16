"""
Light source management for the dungeon crawler engine.
"""

from models import GameState, LightSource
from validation import validate_non_empty_string

from .helpers import _err, _now, _ok
from .strings import fmt_string, get_string


class LightManager:
    """Manages light sources."""

    def set_light_source(
        self,
        state:           GameState,
        label:           str,
        turns_remaining: int | None,
    ):
        """
        DM command: set the active light source.
        Deactivates all previous sources and creates a new active one.
        """
        if state.party is None:
            return _err(state, get_string("errors.no_party"))

        # Validate label
        label_result = validate_non_empty_string(label, "Light source label", max_length=50)
        if not label_result:
            return _err(state, label_result.error)

        # Validate turns_remaining if provided (allow 0 for exhausted lights)
        if turns_remaining is not None and turns_remaining >= 0:
            if turns_remaining < 0:
                return _err(state, get_string("light.errors.negative_turns"))
        elif turns_remaining is not None and turns_remaining < 0:
            return _err(state, get_string("light.errors.negative_turns"))

        # Deactivate all existing light sources
        for light in state.party.light_sources:
            light.is_active = False

        # Create and activate the new light source
        new_light = LightSource(
            label=label_result.value,
            turns_remaining=turns_remaining,
            is_active=True,
        )
        state.party.light_sources.append(new_light)
        state.updated_at = _now()

        duration = f"{turns_remaining} turns" if turns_remaining is not None else "permanent"
        return _ok(state, fmt_string("light.set", label=label_result.value, duration=duration))


def _tick_light(state: GameState) -> None:
    """Decrement the active light source by one turn. Called by resolve_turn."""
    if state.party is None:
        return
    light = state.party.active_light
    if light is None or light.turns_remaining is None:
        return  # no light, or permanent/magical source
    light.turns_remaining = max(0, light.turns_remaining - 1)
