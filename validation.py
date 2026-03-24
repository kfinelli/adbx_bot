"""
validation.py — Input validation utilities for the dungeon crawler engine.

Provides validators for common input types and patterns used throughout
the engine and DM commands. All validators return (is_valid, error_message)
tuples for consistent error handling.
"""

from __future__ import annotations

import re
from typing import Any, TypeVar

T = TypeVar('T')


# ---------------------------------------------------------------------------
# Generic validation result type
# ---------------------------------------------------------------------------

class ValidationResult:
    """Result of a validation check."""

    def __init__(self, is_valid: bool = True, error: str = "", value: Any = None):
        self.is_valid = is_valid
        self.error = error
        self.value = value if value is not None else None

    def __bool__(self) -> bool:
        return self.is_valid

    @classmethod
    def ok(cls, value: Any = None) -> ValidationResult:
        return cls(is_valid=True, value=value)

    @classmethod
    def fail(cls, error: str) -> ValidationResult:
        return cls(is_valid=False, error=error)


# ---------------------------------------------------------------------------
# String validators
# ---------------------------------------------------------------------------

def validate_non_empty_string(
    value: Any,
    field_name: str = "Value",
    max_length: int | None = None,
    min_length: int = 1,
) -> ValidationResult:
    """Validate that a value is a non-empty string within length bounds."""
    if not isinstance(value, str):
        return ValidationResult.fail(f"{field_name} must be a string.")

    stripped = value.strip()
    if len(stripped) < min_length:
        return ValidationResult.fail(f"{field_name} cannot be empty.")

    if max_length and len(stripped) > max_length:
        return ValidationResult.fail(f"{field_name} exceeds maximum length of {max_length} characters.")

    return ValidationResult.ok(stripped)


def validate_identifier(
    value: Any,
    field_name: str = "Identifier",
    max_length: int = 50,
) -> ValidationResult:
    """Validate an identifier (alphanumeric with spaces, underscores, hyphens)."""
    result = validate_non_empty_string(value, field_name, max_length=max_length)
    if not result:
        return result

    # Allow letters, numbers, spaces, underscores, hyphens, and basic punctuation
    if not re.match(r'^[\w\s\-\'\.]+$', result.value):
        return ValidationResult.fail(
            f"{field_name} contains invalid characters. Use only letters, numbers, spaces, hyphens, and apostrophes."
        )

    return result


def validate_room_name(value: Any) -> ValidationResult:
    """Validate a room name."""
    return validate_identifier(value, "Room name", max_length=100)


def validate_character_name(value: Any) -> ValidationResult:
    """Validate a character name."""
    return validate_identifier(value, "Character name", max_length=50)


def validate_npc_name(value: Any) -> ValidationResult:
    """Validate an NPC name."""
    return validate_identifier(value, "NPC name", max_length=50)


def validate_feature_name(value: Any) -> ValidationResult:
    """Validate a room feature name."""
    return validate_identifier(value, "Feature name", max_length=80)


def validate_description(
    value: Any,
    field_name: str = "Description",
    max_length: int = 500,
    allow_empty: bool = True,
) -> ValidationResult:
    """Validate a description field."""
    if not isinstance(value, str):
        return ValidationResult.fail(f"{field_name} must be a string.")

    stripped = value.strip()
    if not allow_empty and len(stripped) == 0:
        return ValidationResult.fail(f"{field_name} cannot be empty.")

    if len(stripped) > max_length:
        return ValidationResult.fail(f"{field_name} exceeds maximum length of {max_length} characters.")

    return ValidationResult.ok(stripped)


# ---------------------------------------------------------------------------
# Numeric validators
# ---------------------------------------------------------------------------

def validate_bounded_int(
    value: Any,
    field_name: str = "Value",
    min_value: int = 1,
    max_value: int | None = None,
) -> ValidationResult:
    """Validate that a value is an integer within bounds."""
    if not isinstance(value, int):
        try:
            value = int(value)
        except (TypeError, ValueError):
            return ValidationResult.fail(f"{field_name} must be a whole number.")

    if value < min_value:
        return ValidationResult.fail(f"{field_name} must be at least {min_value}.")

    if max_value is not None and value > max_value:
        return ValidationResult.fail(f"{field_name} cannot exceed {max_value}.")

    return ValidationResult.ok(value)


def validate_hp_value(value: Any, max_hp: int | None = None) -> ValidationResult:
    """Validate an HP value."""
    field_name = "HP"
    if not isinstance(value, int):
        try:
            value = int(value)
        except (TypeError, ValueError):
            return ValidationResult.fail(f"{field_name} must be a whole number.")

    if value < 0:
        return ValidationResult.fail(f"{field_name} cannot be negative.")

    if max_hp is not None and value > max_hp:
        return ValidationResult.fail(f"{field_name} cannot exceed {max_hp}.")

    return ValidationResult.ok(value)


def validate_turn_hours(value: Any) -> ValidationResult:
    """Validate turn duration in hours."""
    if not isinstance(value, (int, float)):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return ValidationResult.fail("Turn length must be a number.")

    if value <= 0:
        return ValidationResult.fail("Turn length must be greater than 0.")

    if value > 168:  # 1 week max
        return ValidationResult.fail("Turn length cannot exceed 168 hours (1 week).")

    return ValidationResult.ok(value)


# ---------------------------------------------------------------------------
# Enum/choice validators
# ---------------------------------------------------------------------------

def validate_enum_choice(
    value: Any,
    enum_class: type,
    field_name: str = "Value",
) -> ValidationResult:
    """Validate that a value is a valid enum member or string value."""
    from enum import Enum

    if isinstance(value, Enum) and isinstance(value, enum_class):
        return ValidationResult.ok(value)

    if isinstance(value, str):
        try:
            return ValidationResult.ok(enum_class(value.lower()))
        except ValueError:
            valid_values = [e.value for e in enum_class]
            return ValidationResult.fail(
                f"Invalid {field_name.lower()}. Valid options: {', '.join(valid_values)}"
            )

    return ValidationResult.fail(f"{field_name} must be one of: {[e.value for e in enum_class]}")


def validate_door_state(value: Any) -> ValidationResult:
    """Validate a door state value."""
    from models import DoorState
    return validate_enum_choice(value, DoorState, "Door state")


def validate_character_status(value: Any) -> ValidationResult:
    """Validate a character status value."""
    from models import CharacterStatus
    return validate_enum_choice(value, CharacterStatus, "Status")


# ---------------------------------------------------------------------------
# UUID validators
# ---------------------------------------------------------------------------

def validate_uuid_string(value: Any, field_name: str = "ID") -> ValidationResult:
    """Validate a UUID string format."""
    from uuid import UUID

    if not isinstance(value, str):
        return ValidationResult.fail(f"{field_name} must be a valid UUID string.")

    try:
        uuid_obj = UUID(value)
        return ValidationResult.ok(uuid_obj)
    except ValueError:
        return ValidationResult.fail(f"{field_name} is not a valid UUID format.")


# ---------------------------------------------------------------------------
# Composite validators for specific use cases
# ---------------------------------------------------------------------------

def validate_npc_creation(
    name: Any,
    hp: Any,
    defense: Any = 0,
    description: Any = "",
    damage_dice: Any = "1d6",
    notes: Any = "",
) -> dict[str, ValidationResult]:
    """Validate all parameters for NPC creation."""
    results = {}

    results['name'] = validate_npc_name(name)
    results['hp'] = validate_hp_value(hp)
    results['def'] = validate_bounded_int(defense, "Defense", min_value=0)
    results['description'] = validate_description(description, "NPC description", allow_empty=True)
    results['damage_dice'] = validate_non_empty_string(damage_dice, "Damage dice", max_length=20)
    results['notes'] = validate_description(notes, "NPC notes", max_length=500, allow_empty=True)

    return results


def validate_room_creation(
    name: Any,
    description: Any = "",
    notes: Any = "",
) -> dict[str, ValidationResult]:
    """Validate all parameters for room creation."""
    results = {}

    results['name'] = validate_room_name(name)
    results['description'] = validate_description(description, "Room description", allow_empty=True)
    results['notes'] = validate_description(notes, "Room notes", max_length=1000, allow_empty=True)

    return results


def validate_feature_creation(
    name: Any,
    description: Any,
    state_str: Any = "intact",
) -> dict[str, ValidationResult]:
    """Validate all parameters for room feature creation."""
    results = {}

    results['name'] = validate_feature_name(name)
    results['description'] = validate_description(description, "Feature description")
    results['state'] = validate_non_empty_string(state_str, "Feature state", max_length=100)

    return results


def validate_exit_creation(
    label: Any,
    description: Any,
    door_state: Any = "open",
    notes: Any = "",
) -> dict[str, ValidationResult]:
    """Validate all parameters for exit creation."""
    results = {}

    results['label'] = validate_non_empty_string(label, "Exit label", max_length=50)
    results['description'] = validate_description(description, "Exit description")
    results['door_state'] = validate_door_state(door_state)
    results['notes'] = validate_description(notes, "Exit notes", max_length=500, allow_empty=True)

    return results


# ---------------------------------------------------------------------------
# Helper function to aggregate validation results
# ---------------------------------------------------------------------------

def aggregate_validation_results(
    results: dict[str, ValidationResult],
) -> tuple[bool, str]:
    """
    Check if all validations passed.
    Returns (all_valid, combined_error_message).
    """
    errors = []
    for result in results.values():
        if not result:
            errors.append(result.error)

    if errors:
        return False, "; ".join(errors)

    return True, ""


def validate_all_or_fail(
    results: dict[str, ValidationResult],
) -> tuple[bool, str]:
    """Alias for aggregate_validation_results for clearer intent."""
    return aggregate_validation_results(results)
