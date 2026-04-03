"""
cogs/action_buttons.py — In-channel action buttons for EXPLORATION and ROUNDS mode.

EXPLORATION mode (unchanged from Phase 2)
------------------------------------------
ExplorationActionView is a persistent, shared view attached to the channel
status message.  Each button opens an ActionModal for free-text entry.

ROUNDS mode (Phase 3 — multi-step ephemeral flow)
---------------------------------------------------
CombatActionView is a shared, persistent view attached to the status message
while state.mode == ROUNDS.  It contains three buttons visible to all players:

  [Act]     [Oracle]     [Strife ↩]

Clicking Act sends a private ephemeral message containing ClassActionView —
a per-player, transient view with the action buttons for that character's class
(from ACTION_REGISTRY / CLASS_DEFINITIONS).

  • Buttons that require a target (e.g. Attack) replace the ephemeral via
    interaction.response.edit_message() with TargetSelectView — a Select
    menu of living NPCs in the room.
  • Buttons that require a destination (Move) replace the ephemeral with
    DestinationSelectView — a Select menu of the five range bands.
  • The Affect button opens an AffectModal directly (no second step needed).

On final selection / modal submit, submit_turn() is called with a CombatAction
and the ephemeral is replaced with "Action submitted."

Discord constraints observed
-----------------------------
- CombatActionView uses timeout=None and custom_id prefixes so it survives
  bot restarts (registered in on_ready).
- ClassActionView / TargetSelectView / DestinationSelectView use timeout=180
  and are NOT registered as persistent (they are ephemeral-only, transient).
- Every callback re-fetches get_session() at click time — never caches state
  on the view object.
- Select options are built from live state at click time, so stale menus
  show the freshest available targets.

Adding new action buttons
--------------------------
1. Add an entry to data/actions/<id>.json.
2. Update data/classes/<class>.json to include the new action_id.
No Python changes needed for the common case.
For actions requiring a new step type, add a new View class following
the TargetSelectView / DestinationSelectView pattern.
"""

from __future__ import annotations

from uuid import UUID

import discord
from discord.ext import commands

from discord_tasks import post_oracle_question
from engine import (
    ACTION_REGISTRY,
    CLASS_DEFINITIONS,
    CombatAction,
    abscond,
    ask_oracle,
    emote,
    enter_rounds,
    exit_rounds,
    instant_move,
    open_turn,
    say,
    submit_turn,
)
from models import RangeBand, SessionMode, TurnStatus
from store import (
    get_session,
    notify_dm_of_turn_close,
    repost_status,
    save_session_async,
    update_status,
)

# ---------------------------------------------------------------------------
# Shared helper — find a character by Discord user ID
# ---------------------------------------------------------------------------

def _find_character(state, owner_id: str):
    for char in state.characters.values():
        if char.owner_id == owner_id:
            return char
    return None


# ---------------------------------------------------------------------------
# Shared guard helpers
# ---------------------------------------------------------------------------

async def _guard_session(interaction: discord.Interaction):
    """
    Return state if the channel has an active, non-held session, else send
    an ephemeral error and return None.
    """
    state = get_session(str(interaction.channel_id))
    if state is None:
        await interaction.response.send_message(
            "No active session in this channel.", ephemeral=True
        )
        return None
    if not state.session_active:
        await interaction.response.send_message(
            "The session is on hold.", ephemeral=True
        )
        return None
    return state


async def _check_turn(interaction: discord.Interaction):
    """
    Validate session + open turn + character present.
    Returns (state, char) on success, (None, None) on failure.
    Used by exploration buttons.
    """
    state = await _guard_session(interaction)
    if state is None:
        return None, None

    if state.current_turn is None or state.current_turn.status != TurnStatus.OPEN:
        await interaction.response.send_message(
            "The turn is closed — waiting for DM resolution.", ephemeral=True
        )
        return None, None

    char = _find_character(state, str(interaction.user.id))
    if char is None:
        await interaction.response.send_message(
            "You don't have a character in this session.", ephemeral=True
        )
        return None, None

    return state, char


async def _check_combat_turn(interaction: discord.Interaction):
    """
    Validate session + ROUNDS mode + open turn + character present.
    Returns (state, char) on success, (None, None) on failure.
    Used by combat buttons.
    """
    state = await _guard_session(interaction)
    if state is None:
        return None, None

    if state.mode != SessionMode.ROUNDS:
        await interaction.response.send_message(
            "Not in combat — use the exploration buttons instead.", ephemeral=True
        )
        return None, None

    if state.current_turn is None or state.current_turn.status != TurnStatus.OPEN:
        await interaction.response.send_message(
            "The round is closed — waiting for resolution.", ephemeral=True
        )
        return None, None

    char = _find_character(state, str(interaction.user.id))
    if char is None:
        await interaction.response.send_message(
            "You don't have a character in this session.", ephemeral=True
        )
        return None, None

    return state, char


# ---------------------------------------------------------------------------
# Exploration: generic action modal
# ---------------------------------------------------------------------------

class ActionModal(discord.ui.Modal):
    """
    A single-field modal for exploration turn actions.
    Calls submit_turn() with a free-text description.
    """

    def __init__(
        self,
        title: str,
        input_label: str,
        placeholder: str,
        channel_id: str,
        action_prefix: str,
    ):
        super().__init__(title=title, timeout=300)
        self.channel_id = channel_id
        self.action_prefix = action_prefix

        self.detail = discord.ui.TextInput(
            label=input_label,
            placeholder=placeholder,
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=500,
        )
        self.add_item(self.detail)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        state = get_session(self.channel_id)
        if state is None:
            await interaction.response.send_message(
                "No active session in this channel.", ephemeral=True
            )
            return

        char = _find_character(state, str(interaction.user.id))
        if char is None:
            await interaction.response.send_message(
                "You don't have a character in this session.", ephemeral=True
            )
            return

        if state.mode == SessionMode.ROUNDS:
            await interaction.response.send_message(
                "Combat is active — use the Act button to submit your action.",
                ephemeral=True,
            )
            return

        if not state.session_active:
            await interaction.response.send_message(
                "The session is currently on hold.", ephemeral=True
            )
            return

        if state.current_turn is None or state.current_turn.status != TurnStatus.OPEN:
            await interaction.response.send_message(
                "No open turn — the DM needs to resolve the previous turn first.",
                ephemeral=True,
            )
            return

        action_text = f"{self.action_prefix}{self.detail.value}"
        turn_number = state.turn_number

        result = submit_turn(state, char.character_id, action_text)
        if not result.ok:
            await interaction.response.send_message(
                f"{result.error}", ephemeral=True
            )
            return

        await interaction.response.send_message("Turn submitted!", ephemeral=True)
        channel = interaction.channel
        if channel is not None:
            await update_status(channel, state)
            if result.notify_dm:
                await notify_dm_of_turn_close(channel, state, turn_number)


# ---------------------------------------------------------------------------
# Exploration: ExplorationActionView  (persistent — shared, timeout=None)
# ---------------------------------------------------------------------------

class ExplorationActionView(discord.ui.View):
    """
    Buttons attached to the status message during EXPLORATION mode.
    Each button opens an ActionModal.  The view is persistent so buttons
    survive bot restarts.  Buttons are disabled while the turn is closed.
    """

    def __init__(
        self,
        channel_id: str,
        turn_is_open: bool = True,
        leader_character_id=None,
    ):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.leader_character_id = leader_character_id
        if not turn_is_open:
            for item in self.children:
                if getattr(item, "custom_id", None) != "action:character":
                    item.disabled = True

    # row 0 ----------------------------------------------------------------

    @discord.ui.button(
        label="Search", style=discord.ButtonStyle.secondary,
        custom_id="action:search", row=0,
    )
    async def search(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        state, char = await _check_turn(interaction)
        if state is None:
            return
        await interaction.response.send_modal(ActionModal(
            title="Search",
            input_label="Describe your search",
            placeholder=(
                "A 10×10 area. You can specify a feature from the list. "
                "More detail can lead to automatic success."
            ),
            channel_id=str(interaction.channel_id),
            action_prefix="Search: ",
        ))

    @discord.ui.button(
        label="Disarm Trap", style=discord.ButtonStyle.secondary,
        custom_id="action:disarm_trap", row=0,
    )
    async def disarm_trap(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        state, char = await _check_turn(interaction)
        if state is None:
            return
        await interaction.response.send_modal(ActionModal(
            title="Disarm Trap",
            input_label="Describe the trap and your approach",
            placeholder="More detail may lead to automatic success.",
            channel_id=str(interaction.channel_id),
            action_prefix="Disarm Trap: ",
        ))

    @discord.ui.button(
        label="Listen", style=discord.ButtonStyle.secondary,
        custom_id="action:listen", row=0,
    )
    async def listen(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        state, char = await _check_turn(interaction)
        if state is None:
            return
        await interaction.response.send_modal(ActionModal(
            title="Listen",
            input_label="What are you listening for or at?",
            placeholder="Typically used to listen at doors",
            channel_id=str(interaction.channel_id),
            action_prefix="Listen: ",
        ))

    @discord.ui.button(
        label="Force Open Door", style=discord.ButtonStyle.secondary,
        custom_id="action:force_door", row=0,
    )
    async def force_open_door(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        state, char = await _check_turn(interaction)
        if state is None:
            return
        await interaction.response.send_modal(ActionModal(
            title="Force Open Door",
            input_label="Which door, and how?",
            placeholder="Specify an exit number.",
            channel_id=str(interaction.channel_id),
            action_prefix="Force Open Door: ",
        ))

    # row 1 ----------------------------------------------------------------

    @discord.ui.button(
        label="Pick Lock", style=discord.ButtonStyle.secondary,
        custom_id="action:pick_lock", row=1,
    )
    async def pick_lock(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        state, char = await _check_turn(interaction)
        if state is None:
            return
        await interaction.response.send_modal(ActionModal(
            title="Pick Lock",
            input_label="Which lock?",
            placeholder=(
                "Specify an exit number or a room feature (e.g, a locked chest), "
                "describe how you pick the lock"
            ),
            channel_id=str(interaction.channel_id),
            action_prefix="Pick Lock: ",
        ))

    @discord.ui.button(
        label="Craft", style=discord.ButtonStyle.secondary,
        custom_id="action:craft", row=1,
    )
    async def craft(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        state, char = await _check_turn(interaction)
        if state is None:
            return
        await interaction.response.send_modal(ActionModal(
            title="Craft",
            input_label="What are you making or repairing?",
            placeholder="Describe your action",
            channel_id=str(interaction.channel_id),
            action_prefix="Craft: ",
        ))

    @discord.ui.button(
        label="Other Turn Action", style=discord.ButtonStyle.secondary,
        custom_id="action:other_turn", row=1,
    )
    async def other_turn_action(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        state, char = await _check_turn(interaction)
        if state is None:
            return
        await interaction.response.send_modal(ActionModal(
            title="Other Action",
            input_label="Describe your action",
            placeholder="Anything that takes a full 10-minute dungeon turn.",
            channel_id=str(interaction.channel_id),
            action_prefix="",
        ))

    @discord.ui.button(
        label="Oracle", style=discord.ButtonStyle.primary,
        custom_id="action:oracle", row=1,
    )
    async def oracle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        channel_id = str(interaction.channel_id)
        state = get_session(channel_id)
        if state is None:
            await interaction.response.send_message(
                "No active session in this channel.", ephemeral=True
            )
            return
        if not state.session_active:
            await interaction.response.send_message("The session is on hold.", ephemeral=True)
            return
        char = _find_character(state, str(interaction.user.id))
        if char is None:
            await interaction.response.send_message(
                "You don't have a character in this session.", ephemeral=True
            )
            return
        await interaction.response.send_modal(_OracleModal(channel_id=channel_id))

    # row 2 — utility ------------------------------------------------------

    @discord.ui.button(
        label="Abscond", style=discord.ButtonStyle.secondary,
        custom_id="action:abscond", row=2,
    )
    async def abscond_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        channel_id = str(interaction.channel_id)
        state = get_session(channel_id)
        if state is None:
            await interaction.response.send_message(
                "No active session in this channel.", ephemeral=True
            )
            return
        char = _find_character(state, str(interaction.user.id))
        if char is None or (
            state.party is not None and state.party.leader_id != char.character_id
        ):
            await interaction.response.send_message(
                "Only the party leader can use Abscond.", ephemeral=True
            )
            return
        await interaction.response.send_modal(_AbscondModal(channel_id=channel_id))

    @discord.ui.button(
        label="Say", style=discord.ButtonStyle.secondary,
        custom_id="action:say", row=2,
    )
    async def say_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        channel_id = str(interaction.channel_id)
        state = get_session(channel_id)
        if state is None:
            await interaction.response.send_message(
                "No active session in this channel.", ephemeral=True
            )
            return
        char = _find_character(state, str(interaction.user.id))
        if char is None:
            await interaction.response.send_message(
                "You don't have a character in this session.", ephemeral=True
            )
            return
        await interaction.response.send_modal(_SayEmoteModal(channel_id=channel_id, is_emote=False))

    @discord.ui.button(
        label="Emote", style=discord.ButtonStyle.secondary,
        custom_id="action:emote", row=2,
    )
    async def emote_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        channel_id = str(interaction.channel_id)
        state = get_session(channel_id)
        if state is None:
            await interaction.response.send_message(
                "No active session in this channel.", ephemeral=True
            )
            return
        char = _find_character(state, str(interaction.user.id))
        if char is None:
            await interaction.response.send_message(
                "You don't have a character in this session.", ephemeral=True
            )
            return
        await interaction.response.send_modal(_SayEmoteModal(channel_id=channel_id, is_emote=True))

    @discord.ui.button(
        label="Strife", style=discord.ButtonStyle.danger,
        custom_id="action:strife", row=2,
    )
    async def strife_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        channel_id = str(interaction.channel_id)
        state = get_session(channel_id)
        if state is None:
            await interaction.response.send_message(
                "No active session in this channel.", ephemeral=True
            )
            return
        user_id = str(interaction.user.id)
        char = _find_character(state, user_id)
        is_dm = state.dm_user_id == user_id
        is_leader = (
            char is not None
            and state.party is not None
            and state.party.leader_id == char.character_id
        )
        if not (is_dm or is_leader):
            await interaction.response.send_message(
                "Only the DM or party leader can toggle combat rounds.", ephemeral=True
            )
            return
        if state.mode == SessionMode.ROUNDS:
            result = exit_rounds(state)
            narrative = "Combat ended — returning to exploration."
        else:
            result = enter_rounds(state)
            narrative = "Combat begins!"
        if not result.ok:
            await interaction.response.send_message(f"{result.error}", ephemeral=True)
            return
        if state.current_turn is None or state.current_turn.status != TurnStatus.OPEN:
            open_turn(state)
        await interaction.response.send_message(narrative, ephemeral=True)
        await repost_status(interaction.channel, state, narrative=narrative)

    # row 3 ----------------------------------------------------------------

    @discord.ui.button(
        label="View Character", style=discord.ButtonStyle.secondary,
        custom_id="action:character", row=3,
    )
    async def character_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from cogs.character_views import EquipMenuView, _character_sheet, _find_character
        channel_id = str(interaction.channel_id)
        state = get_session(channel_id)
        if state is None:
            await interaction.response.send_message(
                "No active session in this channel.", ephemeral=True
            )
            return
        char = _find_character(state, str(interaction.user.id))
        if char is None:
            await interaction.response.send_message(
                "You don't have a character in this session.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        sheet = _character_sheet(char, state)
        try:
            dm_channel = await interaction.user.create_dm()
            await dm_channel.send(
                content=sheet,
                view=EquipMenuView(char, state, channel_id),
            )
            await interaction.edit_original_response(
                content="Your character sheet has been sent to your DMs."
            )
        except discord.Forbidden:
            await interaction.edit_original_response(content=sheet)


# ---------------------------------------------------------------------------
# Combat: CombatActionView  (persistent — shared, timeout=None)
# ---------------------------------------------------------------------------

class CombatActionView(discord.ui.View):
    """
    Shared, persistent view attached to the status message during ROUNDS mode.

    Three buttons visible to all players:
      [Act]  — opens a private ephemeral with ClassActionView
      [Oracle] — opens the Oracle modal (no turn consumed)
      [Strife ↩] — ends combat (DM / party leader only)

    The view is kept minimal and persistent so it survives bot restarts.
    Per-character action selection happens in the transient ephemeral layer.
    """

    def __init__(self, channel_id: str, turn_is_open: bool = True):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        if not turn_is_open:
            # Disable Act when round is closed; Oracle, Strife, and View Character remain active
            for item in self.children:
                if getattr(item, "custom_id", None) == "combat:act":
                    item.disabled = True

    @discord.ui.button(
        label="⚔ Act", style=discord.ButtonStyle.danger,
        custom_id="combat:act", row=0,
    )
    async def act_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """
        Step 1 — send a private ephemeral with the player's class-specific
        action buttons.
        """
        state, char = await _check_combat_turn(interaction)
        if state is None:
            return

        # Check if this character has already submitted this round
        if state.latest_submission(char.character_id) is not None:
            await interaction.response.send_message(
                "You've already submitted this round. "
                "Wait for resolution or ask the DM to un-submit your action.",
                ephemeral=True,
            )
            return

        view = _build_class_action_view(
            char=char,
            state=state,
            channel_id=str(interaction.channel_id),
        )
        await interaction.response.send_message(
            f"**{char.name}** — choose your action:",
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(
        label="Move", style=discord.ButtonStyle.primary,
        custom_id="combat:move", row=0,
    )
    async def move_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Instant move — resolves immediately outside the round queue."""
        state, char = await _check_combat_turn(interaction)
        if state is None:
            return

        cs = state.battlefield.combatants.get(char.character_id) if state.battlefield else None
        if cs is not None and cs.used_move:
            await interaction.response.send_message(
                "You've already moved this round.", ephemeral=True
            )
            return

        current_band = cs.range_band if cs is not None else None
        view = DestinationSelectView(
            action_id="move",
            char_id=char.character_id,
            channel_id=self.channel_id,
            current_band=current_band,
            instant_resolve=True,
        )
        await interaction.response.send_message(
            f"**{char.name}** — choose where to move:",
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(
        label="Oracle", style=discord.ButtonStyle.primary,
        custom_id="combat:oracle", row=0,
    )
    async def oracle_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Oracle works the same in combat as in exploration, but is limited to 1 use per round."""
        channel_id = str(interaction.channel_id)
        state = get_session(channel_id)
        if state is None:
            await interaction.response.send_message(
                "No active session in this channel.", ephemeral=True
            )
            return
        if not state.session_active:
            await interaction.response.send_message("The session is on hold.", ephemeral=True)
            return
        char = _find_character(state, str(interaction.user.id))
        if char is None:
            await interaction.response.send_message(
                "You don't have a character in this session.", ephemeral=True
            )
            return
        # Check oracle use limit in ROUNDS mode
        if state.mode == SessionMode.ROUNDS and state.battlefield:
            cs = state.battlefield.combatants.get(char.character_id)
            if cs is not None and cs.used_oracle:
                await interaction.response.send_message(
                    "You've already used Oracle this round.", ephemeral=True
                )
                return
        await interaction.response.send_modal(
            _OracleModal(channel_id=channel_id, char_id=char.character_id)
        )

    @discord.ui.button(
        label="Strife ↩", style=discord.ButtonStyle.secondary,
        custom_id="combat:strife_end", row=0,
    )
    async def end_strife_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """End combat — DM or party leader only."""
        channel_id = str(interaction.channel_id)
        state = get_session(channel_id)
        if state is None:
            await interaction.response.send_message(
                "No active session in this channel.", ephemeral=True
            )
            return
        user_id = str(interaction.user.id)
        char = _find_character(state, user_id)
        is_dm = state.dm_user_id == user_id
        is_leader = (
            char is not None
            and state.party is not None
            and state.party.leader_id == char.character_id
        )
        if not (is_dm or is_leader):
            await interaction.response.send_message(
                "Only the DM or party leader can end combat.", ephemeral=True
            )
            return
        result = exit_rounds(state)
        if not result.ok:
            await interaction.response.send_message(f"{result.error}", ephemeral=True)
            return
        if state.current_turn is None or state.current_turn.status != TurnStatus.OPEN:
            open_turn(state)
        narrative = "Combat ended — returning to exploration."
        await interaction.response.send_message(narrative, ephemeral=True)
        await repost_status(interaction.channel, state, narrative=narrative)

    # row 1 ----------------------------------------------------------------

    @discord.ui.button(
        label="View Character", style=discord.ButtonStyle.secondary,
        custom_id="combat:character", row=1,
    )
    async def character_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from cogs.character_views import EquipMenuView, _character_sheet, _find_character
        channel_id = str(interaction.channel_id)
        state = get_session(channel_id)
        if state is None:
            await interaction.response.send_message(
                "No active session in this channel.", ephemeral=True
            )
            return
        char = _find_character(state, str(interaction.user.id))
        if char is None:
            await interaction.response.send_message(
                "You don't have a character in this session.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        sheet = _character_sheet(char, state)
        try:
            dm_channel = await interaction.user.create_dm()
            await dm_channel.send(
                content=sheet,
                view=EquipMenuView(char, state, channel_id),
            )
            await interaction.edit_original_response(
                content="Your character sheet has been sent to your DMs."
            )
        except discord.Forbidden:
            await interaction.edit_original_response(content=sheet)


# ---------------------------------------------------------------------------
# Combat: ClassActionView  (transient ephemeral — timeout=180)
# ---------------------------------------------------------------------------

def _build_class_action_view(char, state, channel_id: str) -> discord.ui.View:
    """
    Build a ClassActionView for char based on their class's combat_actions list
    plus any actions granted by active status conditions on the battlefield.

    The view is ephemeral-only (timeout=180) and NOT registered as persistent.
    """
    # Base action IDs from class definition
    cls_key = char.character_class.name   # e.g. "KNIGHT"
    cls_def = CLASS_DEFINITIONS.get(cls_key)
    action_ids = list(cls_def.combat_actions) if cls_def else ["attack", "move", "affect"]

    # Add any condition-granted actions
    if state.battlefield:
        cs = state.battlefield.combatants.get(char.character_id)
        if cs:
            from engine import CONDITION_REGISTRY
            for cond in cs.active_conditions:
                cond_def = CONDITION_REGISTRY.get(cond.condition_id)
                if cond_def:
                    for granted in cond_def.grants_actions:
                        if granted not in action_ids:
                            action_ids.append(granted)

    return ClassActionView(
        action_ids=action_ids,
        char_id=char.character_id,
        channel_id=channel_id,
    )


class ClassActionView(discord.ui.View):
    """
    Per-player ephemeral view listing the character's available combat actions.
    Each button either:
      • opens TargetSelectView (requires_target actions)
      • opens DestinationSelectView (requires_destination actions)
      • opens AffectModal (affect type)
    Built dynamically from ACTION_REGISTRY entries in action_ids.
    """

    def __init__(self, action_ids: list[str], char_id: UUID, channel_id: str):
        super().__init__(timeout=180)
        self.char_id = char_id
        self.channel_id = channel_id

        for action_id in action_ids:
            action_def = ACTION_REGISTRY.get(action_id)
            if action_def is None:
                continue

            style_map = {
                "primary":   discord.ButtonStyle.primary,
                "secondary": discord.ButtonStyle.secondary,
                "danger":    discord.ButtonStyle.danger,
                "success":   discord.ButtonStyle.success,
            }
            style = style_map.get(action_def.button_style, discord.ButtonStyle.secondary)

            btn = discord.ui.Button(
                label=action_def.label,
                style=style,
                custom_id=f"class_action:{action_id}:{char_id}",
            )
            btn.callback = self._make_callback(action_id)
            self.add_item(btn)

    def _make_callback(self, action_id: str):
        """Return an async callback for a given action_id."""
        async def callback(interaction: discord.Interaction) -> None:
            state, char = await _check_combat_turn(interaction)
            if state is None:
                return

            # Re-verify this is the right character
            owner_char = _find_character(state, str(interaction.user.id))
            if owner_char is None or owner_char.character_id != self.char_id:
                await interaction.response.send_message(
                    "This action panel doesn't belong to your character.", ephemeral=True
                )
                return

            action_def = ACTION_REGISTRY.get(action_id)
            if action_def is None:
                await interaction.response.send_message(
                    f"Unknown action '{action_id}'.", ephemeral=True
                )
                return

            if action_def.action_type == "affect":
                await interaction.response.send_modal(
                    AffectModal(char_id=self.char_id, channel_id=self.channel_id)
                )
                return

            if action_def.requires_target:
                # Build target select from living NPCs in current room
                npc_targets = [
                    n for n in state.npcs_in_current_room if n.status != "dead"
                ]
                if not npc_targets:
                    await interaction.response.send_message(
                        "No valid targets in this room.", ephemeral=True
                    )
                    return
                view = TargetSelectView(
                    action_id=action_id,
                    char_id=self.char_id,
                    channel_id=self.channel_id,
                    npc_targets=npc_targets,
                    then_destination=action_def.requires_destination,
                )
                await interaction.response.edit_message(
                    content=f"**{owner_char.name}** — select a target:",
                    view=view,
                )
                return

            if action_def.requires_destination:
                view = DestinationSelectView(
                    action_id=action_id,
                    char_id=self.char_id,
                    channel_id=self.channel_id,
                    current_band=(
                        state.battlefield.combatants[self.char_id].range_band
                        if state.battlefield and self.char_id in state.battlefield.combatants
                        else None
                    ),
                )
                await interaction.response.edit_message(
                    content=f"**{owner_char.name}** — select destination:",
                    view=view,
                )
                return

            # Action needs neither target nor destination — submit immediately
            action = CombatAction(action_id=action_id)
            await _submit_combat_action(interaction, self.char_id, self.channel_id, action)

        return callback


# ---------------------------------------------------------------------------
# Combat: TargetSelectView  (transient ephemeral — timeout=180)
# ---------------------------------------------------------------------------

class TargetSelectView(discord.ui.View):
    """
    Second-step ephemeral view for actions that require a target.
    Presents a Select menu of living NPCs in the current room.
    Built fresh at click time so the list is always current.

    then_destination: if True, selecting a target chains into DestinationSelectView
    rather than submitting immediately.  Used by actions that require both a
    target and a destination (e.g. Charge).
    """

    def __init__(
        self,
        action_id:        str,
        char_id:          UUID,
        channel_id:       str,
        npc_targets,
        then_destination: bool = False,
    ):
        super().__init__(timeout=180)
        self.action_id        = action_id
        self.char_id          = char_id
        self.channel_id       = channel_id
        self.then_destination = then_destination

        options = [
            discord.SelectOption(
                label=npc.name,
                value=str(npc.npc_id),
                description=f"HP: {npc.hp_current}/{npc.hp_max}",
            )
            for npc in npc_targets[:25]   # Discord SelectMenu max 25 options
        ]

        select = discord.ui.Select(
            placeholder="Choose a target…",
            options=options,
            custom_id=f"target_select:{action_id}:{char_id}",
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        state, char = await _check_combat_turn(interaction)
        if state is None:
            return

        owner_char = _find_character(state, str(interaction.user.id))
        if owner_char is None or owner_char.character_id != self.char_id:
            await interaction.response.edit_message(
                content="This panel doesn't belong to your character.", view=None
            )
            return

        target_id = UUID(interaction.data["values"][0])
        partial = CombatAction(action_id=self.action_id, target_id=target_id)

        current_band = (
            state.battlefield.combatants[self.char_id].range_band
            if state.battlefield and self.char_id in state.battlefield.combatants
            else None
        )

        owner_char_obj = state.characters.get(self.char_id)
        weapons = owner_char_obj.equipped_weapons() if owner_char_obj else []

        if len(weapons) > 1:
            view = WeaponPickerView(
                char_id=self.char_id,
                channel_id=self.channel_id,
                weapons=weapons,
                partial=partial,
                then_destination=self.then_destination,
                current_band=current_band,
            )
            await interaction.response.edit_message(
                content=f"**{owner_char.name}** — select a weapon:",
                view=view,
            )
        elif self.then_destination:
            view = DestinationSelectView(
                action_id=self.action_id,
                char_id=self.char_id,
                channel_id=self.channel_id,
                current_band=current_band,
                partial_action=partial,
            )
            await interaction.response.edit_message(
                content=f"**{owner_char.name}** — select destination:",
                view=view,
            )
        else:
            await _submit_combat_action(interaction, self.char_id, self.channel_id, partial)


# ---------------------------------------------------------------------------
# Combat: WeaponPickerView  (transient ephemeral — timeout=180)
# ---------------------------------------------------------------------------


class WeaponPickerView(discord.ui.View):
    """
    Optional third-step ephemeral view shown when a character has multiple
    weapons available (e.g. a spellcaster with a spellbook containing several
    spells).  Skipped transparently when only one weapon is equipped.

    partial          : CombatAction already populated with action_id + target_id.
    then_destination : if True, chains into DestinationSelectView after pick.
    current_band     : player's current RangeBand, forwarded to DestinationSelectView.
    """

    def __init__(
        self,
        char_id:          UUID,
        channel_id:       str,
        weapons:          list,
        partial:          CombatAction,
        then_destination: bool,
        current_band,
    ):
        super().__init__(timeout=180)
        self.char_id          = char_id
        self.channel_id       = channel_id
        self.partial          = partial
        self.then_destination = then_destination
        self.current_band     = current_band

        options = []
        for inv_item, weapon_def in weapons[:25]:
            if inv_item.charges is not None:
                max_c = getattr(weapon_def, "maxCharges", -1)
                desc = "∞" if max_c < 0 else f"{inv_item.charges}/{max_c} charges"
            else:
                dmg = getattr(weapon_def, "damage", None)
                desc = dmg if dmg else ""
            options.append(discord.SelectOption(
                label=weapon_def.name,
                value=inv_item.item_id,
                description=desc,
            ))

        select = discord.ui.Select(
            placeholder="Choose a weapon…",
            options=options,
            custom_id=f"weapon_select:{partial.action_id}:{char_id}",
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        state, char = await _check_combat_turn(interaction)
        if state is None:
            return

        owner_char = _find_character(state, str(interaction.user.id))
        if owner_char is None or owner_char.character_id != self.char_id:
            await interaction.response.edit_message(
                content="This panel doesn't belong to your character.", view=None
            )
            return

        self.partial.weapon_id = interaction.data["values"][0]

        if self.then_destination:
            view = DestinationSelectView(
                action_id=self.partial.action_id,
                char_id=self.char_id,
                channel_id=self.channel_id,
                current_band=self.current_band,
                partial_action=self.partial,
            )
            await interaction.response.edit_message(
                content=f"**{owner_char.name}** — select destination:",
                view=view,
            )
        else:
            await _submit_combat_action(interaction, self.char_id, self.channel_id, self.partial)


# ---------------------------------------------------------------------------
# Combat: DestinationSelectView  (transient ephemeral — timeout=180)
# ---------------------------------------------------------------------------

_BAND_LABELS: dict[RangeBand, str] = {
    RangeBand.FAR_MINUS:   "Far −  (-far)",
    RangeBand.CLOSE_MINUS: "Close −  (-close)",
    RangeBand.ENGAGE:      "Engage  (engage)",
    RangeBand.CLOSE_PLUS:  "Close +  (+close)",
    RangeBand.FAR_PLUS:    "Far +  (+far)",
}


class DestinationSelectView(discord.ui.View):
    """
    Ephemeral view for actions that require a destination range band.

    Used in two scenarios:
      • Standalone (Move): action_id + destination → submit.
      • Chained after TargetSelectView (Charge): partial_action carries the
        already-chosen target_id; selecting a destination completes the action.
    """

    def __init__(
        self,
        action_id:       str,
        char_id:         UUID,
        channel_id:      str,
        current_band:    RangeBand | None,
        partial_action:  CombatAction | None = None,
        instant_resolve: bool = False,
    ):
        super().__init__(timeout=180)
        self.action_id       = action_id
        self.char_id         = char_id
        self.channel_id      = channel_id
        self.current_band    = current_band
        self.partial_action  = partial_action   # carries target_id for chained flows
        self.instant_resolve = instant_resolve  # True for the top-level Move button

        options = [
            discord.SelectOption(
                label=_BAND_LABELS[band],
                value=band.value,
                description="◀ current position" if band == current_band else None,
            )
            for band in RangeBand
        ]

        select = discord.ui.Select(
            placeholder="Choose destination…",
            options=options,
            custom_id=f"dest_select:{action_id}:{char_id}",
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        state, char = await _check_combat_turn(interaction)
        if state is None:
            return

        owner_char = _find_character(state, str(interaction.user.id))
        if owner_char is None or owner_char.character_id != self.char_id:
            await interaction.response.edit_message(
                content="This panel doesn't belong to your character.", view=None
            )
            return

        destination = RangeBand(interaction.data["values"][0])

        if self.instant_resolve:
            # Top-level Move button: resolve immediately without entering the turn queue.
            state = get_session(self.channel_id)
            result = instant_move(state, self.char_id, destination)
            if not result.ok:
                await interaction.response.edit_message(
                    content=result.error, view=None
                )
                return
            await save_session_async(state)
            await interaction.response.edit_message(
                content=f"Moved to **{destination.value.replace('_', ' ')}**.", view=None
            )
            await update_status(interaction.channel, state)
            return

        if self.partial_action is not None:
            # Chained flow: merge destination into the partial action
            action = CombatAction(
                action_id=self.partial_action.action_id,
                target_id=self.partial_action.target_id,
                destination=destination,
                free_text=self.partial_action.free_text,
                weapon_id=self.partial_action.weapon_id,
            )
        else:
            action = CombatAction(action_id=self.action_id, destination=destination)

        await _submit_combat_action(interaction, self.char_id, self.channel_id, action)


# ---------------------------------------------------------------------------
# Combat: AffectModal  (free-text fallback, always requires DM resolution)
# ---------------------------------------------------------------------------

class AffectModal(discord.ui.Modal):
    """
    Free-text combat action modal.  Submits as an Affect CombatAction,
    which bypasses auto-resolution and hands the round to the DM.
    """

    def __init__(self, char_id: UUID, channel_id: str) -> None:
        super().__init__(title="Affect — Free Action", timeout=300)
        self.char_id    = char_id
        self.channel_id = channel_id

        self.text = discord.ui.TextInput(
            label="Describe your action",
            placeholder=(
                "Describe what your character does. "
                "The DM will resolve the round manually."
            ),
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=500,
        )
        self.add_item(self.text)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        state = get_session(self.channel_id)
        if state is None:
            await interaction.response.send_message(
                "No active session.", ephemeral=True
            )
            return

        char = state.characters.get(self.char_id)
        if char is None:
            await interaction.response.send_message(
                "Character not found.", ephemeral=True
            )
            return

        if state.current_turn is None or state.current_turn.status != TurnStatus.OPEN:
            await interaction.response.send_message(
                "The round is no longer open.", ephemeral=True
            )
            return

        action      = CombatAction(action_id="affect", free_text=self.text.value)
        action_text = f"Affect: {self.text.value}"
        turn_number = state.turn_number

        result = submit_turn(
            state, char.character_id, action_text, combat_action=action.to_dict()
        )
        if not result.ok:
            await interaction.response.send_message(f"{result.error}", ephemeral=True)
            return

        await interaction.response.send_message("Action submitted.", ephemeral=True)
        channel = interaction.channel
        if channel is None:
            return

        if result.auto_resolved:
            from discord_tasks import dispatch_turn_resolved
            await dispatch_turn_resolved(channel, state, result.message)
        elif result.notify_dm:
            await notify_dm_of_turn_close(channel, state, turn_number)
        else:
            await update_status(channel, state)


# ---------------------------------------------------------------------------
# Shared combat submission helper
# ---------------------------------------------------------------------------

async def _submit_combat_action(
    interaction: discord.Interaction,
    char_id:     UUID,
    channel_id:  str,
    action:      CombatAction,
) -> None:
    """
    Final step of every structured combat flow: submit the action, edit the
    ephemeral to a confirmation, then update the channel.

    Three outcomes after submit_turn():
      • result.auto_resolved → post narrative + fresh status block + ping players
        (identical to the DM resolve path via dispatch_turn_resolved)
      • result.notify_dm     → all submissions in, at least one Affect; notify DM
      • neither              → partial submissions; silently edit existing status block

    Uses interaction.response.edit_message() so the ephemeral is replaced
    in-place rather than spawning additional messages.
    """
    state = get_session(channel_id)
    if state is None:
        await interaction.response.edit_message(
            content="Session ended.", view=None
        )
        return

    char = state.characters.get(char_id)
    if char is None:
        await interaction.response.edit_message(
            content="Character not found.", view=None
        )
        return

    if state.current_turn is None or state.current_turn.status != TurnStatus.OPEN:
        await interaction.response.edit_message(
            content="The round closed while you were choosing. Please wait for the next round.",
            view=None,
        )
        return

    action_def  = ACTION_REGISTRY.get(action.action_id)
    label       = action_def.label if action_def else action.action_id
    action_text = f"{label}: {action.free_text}" if action.free_text else label
    turn_number = state.turn_number

    result = submit_turn(
        state, char_id, action_text, combat_action=action.to_dict()
    )
    if not result.ok:
        await interaction.response.edit_message(
            content=f"{result.error}", view=None
        )
        return

    await interaction.response.edit_message(
        content="Action submitted.", view=None
    )

    channel = interaction.channel
    if channel is None:
        return

    if result.auto_resolved:
        # Every player used a structured action — round resolved automatically.
        # Post the narrative as a standalone message, then a fresh status block,
        # then ping players that the next round is open.  Same path as DM resolve.
        from discord_tasks import dispatch_turn_resolved
        await dispatch_turn_resolved(channel, state, result.message)
    elif result.notify_dm:
        # At least one Affect — DM must resolve manually.
        await notify_dm_of_turn_close(channel, state, turn_number)
    else:
        # Still waiting on other players — silently update the existing status block.
        await update_status(channel, state)


# ---------------------------------------------------------------------------
# Utility modals (shared by both modes)
# ---------------------------------------------------------------------------

class _AbscondModal(discord.ui.Modal, title="Abscond"):
    exit_number = discord.ui.TextInput(
        label="Exit number",
        placeholder="Enter the number of the exit to take (see status block).",
        required=True,
        max_length=4,
    )

    def __init__(self, *, channel_id: str) -> None:
        super().__init__()
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            num = int(self.exit_number.value.strip())
        except ValueError:
            await interaction.response.send_message("Please enter a number.", ephemeral=True)
            return
        state = get_session(self.channel_id)
        if state is None:
            await interaction.response.send_message("No active session.", ephemeral=True)
            return
        char = _find_character(state, str(interaction.user.id))
        if char is None:
            await interaction.response.send_message(
                "You don't have a character in this session.", ephemeral=True
            )
            return
        turn_number = state.turn_number
        result = abscond(state, char.character_id, num)
        if not result.ok:
            await interaction.response.send_message(f"{result.error}", ephemeral=True)
            return
        await interaction.response.send_message("Moving out.", ephemeral=True)
        if result.auto_resolved:
            from discord_tasks import dispatch_turn_resolved
            await dispatch_turn_resolved(interaction.channel, state, result.message)
        elif result.notify_dm:
            await notify_dm_of_turn_close(interaction.channel, state, turn_number)
        else:
            await update_status(interaction.channel, state)


class _SayEmoteModal(discord.ui.Modal):
    def __init__(self, *, channel_id: str, is_emote: bool) -> None:
        super().__init__(title="Emote" if is_emote else "Say")
        self.channel_id = channel_id
        self.is_emote   = is_emote
        self.text = discord.ui.TextInput(
            label="Describe the action" if is_emote else "What do you say?",
            placeholder=(
                "Describe what your character does (e.g. 'nods sagely')."
                if is_emote else "Speak in character."
            ),
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=500,
        )
        self.add_item(self.text)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        state = get_session(self.channel_id)
        if state is None:
            await interaction.response.send_message("No active session.", ephemeral=True)
            return
        char = _find_character(state, str(interaction.user.id))
        if char is None:
            await interaction.response.send_message(
                "You don't have a character in this session.", ephemeral=True
            )
            return
        if self.is_emote:
            emote(state, char.name, self.text.value)
        else:
            say(state, char.name, self.text.value)
        await interaction.response.send_message("Done.", ephemeral=True)
        await update_status(interaction.channel, state)


class _OracleModal(discord.ui.Modal):
    def __init__(self, *, channel_id: str, char_id: UUID | None = None) -> None:
        super().__init__(title="Oracle")
        self.channel_id = channel_id
        self.char_id    = char_id
        self.question = discord.ui.TextInput(
            label="Question or brief interaction",
            placeholder=(
                "Describe a short action that takes less than a full turn, "
                "or ask a question"
            ),
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=500,
        )
        self.add_item(self.question)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        state = get_session(self.channel_id)
        if state is None:
            await interaction.response.send_message("No active session.", ephemeral=True)
            return
        char = _find_character(state, str(interaction.user.id))
        asker = char.name if char else interaction.user.display_name
        result = ask_oracle(
            state, asker, self.question.value,
            asker_owner_id=str(interaction.user.id),
        )
        if not result.ok:
            await interaction.response.send_message(f"{result.error}", ephemeral=True)
            return
        oracle = result.data
        # Mark oracle used for this round (ROUNDS mode only)
        if self.char_id is not None and state.battlefield:
            cs = state.battlefield.combatants.get(self.char_id)
            if cs is not None:
                cs.used_oracle = True
        await interaction.response.send_message("Oracle submitted.", ephemeral=True)
        msg = await post_oracle_question(interaction.channel, oracle)
        oracle.message_id = msg.id
        await save_session_async(state)
        await update_status(interaction.channel, state)


# ---------------------------------------------------------------------------
# View factory — called by store.py
# ---------------------------------------------------------------------------

def build_action_view(state) -> discord.ui.View | None:
    """
    Return the appropriate action view for the current session state,
    or None if no buttons should be shown (PRE_START, on hold, etc.).

    Called by store._build_view() → store.update_status() / repost_status().
    """
    if state is None or not state.session_active:
        return None

    if state.mode == SessionMode.PRE_START:
        return None

    channel_id   = str(state.platform_channel_id)
    turn_is_open = (
        state.current_turn is not None
        and state.current_turn.status == TurnStatus.OPEN
    )

    if state.mode == SessionMode.ROUNDS:
        return CombatActionView(channel_id=channel_id, turn_is_open=turn_is_open)

    # EXPLORATION
    leader_character_id = state.party.leader_id if state.party else None
    return ExplorationActionView(
        channel_id=channel_id,
        turn_is_open=turn_is_open,
        leader_character_id=leader_character_id,
    )


# ---------------------------------------------------------------------------
# Status block: battlefield section
# ---------------------------------------------------------------------------

def render_battlefield_section(state) -> str:
    """
    Return a plain-text battlefield diagram for inclusion in the ROUNDS
    status block.  Shows which combatants occupy each range band.

    Called from engine/__init__.py render_status() when state.mode == ROUNDS.
    """
    if state.battlefield is None:
        return ""

    band_names = {
        RangeBand.FAR_MINUS:   "-far",
        RangeBand.CLOSE_MINUS: "-close",
        RangeBand.ENGAGE:      "engage",
        RangeBand.CLOSE_PLUS:  "+close",
        RangeBand.FAR_PLUS:    "+far",
    }

    # Group combatant names by band
    bands: dict[RangeBand, list[str]] = {b: [] for b in RangeBand}
    for cid, cs in state.battlefield.combatants.items():
        if cs.is_player:
            char = state.characters.get(cid)
            name = char.name if char else str(cid)[:8]
        else:
            # Find NPC name
            name = str(cid)[:8]
            for group in state.npc_roster.groups.values():
                for npc in group.npcs:
                    if npc.npc_id == cid:
                        name = npc.name
                        break
        bands[cs.range_band].append(name)

    lines = []
    for band, label in band_names.items():
        occupants = ", ".join(bands[band]) if bands[band] else "—"
        lines.append(f"  {label:<8} {occupants}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Cog registration
# ---------------------------------------------------------------------------

class ActionButtonsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        # Re-register persistent views so buttons on pre-existing status
        # messages keep working after a bot restart.
        self.bot.add_view(ExplorationActionView(channel_id="__persistent__"))
        self.bot.add_view(CombatActionView(channel_id="__persistent__"))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ActionButtonsCog(bot))
