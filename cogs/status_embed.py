"""
cogs/status_embed.py — assemble the pinned status message embed.

The engine (render_status_sections / pack_sections) produces budget-packed
section data; this module is the thin Discord-specific shell that maps it
onto a discord.Embed. Section bodies are wrapped in fenced code blocks to
keep the monospace look inside field values; the accent color encodes the
session mode (green = exploration, red = rounds, grey = pre-start).
"""

import discord

from engine import pack_sections, render_status_sections
from models import GameState, SessionMode

_MODE_COLORS = {
    SessionMode.PRE_START:   discord.Color.light_grey(),
    SessionMode.EXPLORATION: discord.Color.green(),
    SessionMode.ROUNDS:      discord.Color.red(),
}


def build_status_embed(state: GameState) -> discord.Embed:
    """
    Build the status embed for the current session state.

    pack_sections guarantees the 6000-char total / 1024-per-field budgets,
    so the embed is always sendable; overflow is reported via in-field
    marker lines, never by cutting content silently.
    """
    description, sections = render_status_sections(state)
    packed = pack_sections(description, sections)

    embed = discord.Embed(
        description=description,
        color=_MODE_COLORS.get(state.mode, discord.Color.green()),
    )
    for title, lines in packed:
        embed.add_field(
            name=title,
            value="```\n" + "\n".join(lines) + "\n```",
            inline=False,
        )
    return embed
