# Async Dungeon Crawler — Discord Bot

A Discord bot for asynchronous B/X D&D dungeon crawling, mediated via slash commands.

---

## Project Structure

```
bot.py                  # Entry point
store.py                # In-memory session state and shared helpers
models.py               # Game state data model (no dependencies)
engine.py               # Game logic (operates on models)
tables.py               # Static B/X reference data (saves, spell slots, etc.)
cogs/
    session.py          # Player slash commands (/embark, /turn, /status)
    dm_commands.py      # DM slash commands (/dm_resolve, /dm_setroom, etc.)
```

---

## One-Time Discord Setup

### 1. Create a Bot Application

1. Go to https://discord.com/developers/applications
2. Click **New Application**, give it a name.
3. Go to the **Bot** tab on the left sidebar.
4. Click **Add Bot** → **Yes, do it!**
5. Under **Token**, click **Reset Token** and copy the token somewhere safe.
   You will not see it again.
6. Under **Privileged Gateway Intents**, enable **Message Content Intent**
   (needed so the bot can read messages in some contexts).

### 2. Invite the Bot to Your Server

1. Go to the **OAuth2 → URL Generator** tab.
2. Under **Scopes**, check: `bot` and `applications.commands`
3. Under **Bot Permissions**, check:
   - Send Messages
   - Read Message History
   - Manage Messages (needed to pin the status message)
   - Read Messages / View Channels
4. Copy the generated URL and open it in your browser.
5. Select your server and authorize.

### 3. Set Up a Dedicated Channel

Create a channel (e.g. `#dungeon`) for gameplay. The bot will pin the status
message there. You can lock it to slash-commands-only by adjusting Discord
channel permissions if you like, but it's not required for the demo.

---

## Local Setup

### Requirements

- Python 3.11+
- `discord.py` 2.x

### Install dependencies

```bash
pip install "discord.py>=2.3"
```

### Set your bot token

The bot reads the token from the `DISCORD_TOKEN` environment variable.

**On macOS/Linux:**
```bash
export DISCORD_TOKEN="your-token-here"
python bot.py
```

**On Windows (PowerShell):**
```powershell
$env:DISCORD_TOKEN="your-token-here"
python bot.py
```

Alternatively, create a `.env` file and use `python-dotenv`:
```
DISCORD_TOKEN=your-token-here
```
```bash
pip install python-dotenv
```
Then add this to the top of `bot.py` before the TOKEN line:
```python
from dotenv import load_dotenv
load_dotenv()
```

---

## Running the Bot

```bash
python bot.py
```

On first run you should see:
```
Logged in as YourBot#1234 (...)
Synced 12 slash command(s).
```

Slash commands may take a few minutes to appear in Discord after the first sync.

---

## Playing a Solo Demo Session

Since you're playing DM and player simultaneously, open Discord in two windows
or just switch hats as needed.

### Starting a Session

1. In your dungeon channel, type `/embark` and fill in the form:
   - **name**: your character's name
   - **character_class**: pick a class
   - **equipment_package**: pick a package
2. The bot will create your character, print their stats, and post the status block.

### DM Setup (do this as DM)

Set the starting room:
```
/dm_setroom name:Entrance Hall description:A dusty stone hall. Torchlight flickers on the walls.
```

Set a light source:
```
/dm_setlight label:Torch turns:6
```

Add a feature:
```
/dm_addfeature name:Iron door description:A heavy iron door stands to the north. state_str:closed
```

Add an NPC:
```
/dm_addnpc name:Goblin Scout hp:4 ac:6 description:A wiry goblin crouches in the corner.
```

### Taking a Turn (as player)

```
/turn action:I move carefully toward the iron door and listen at it.
```

### Resolving a Turn (as DM)

```
/dm_resolve narrative:You press your ear to the cold iron. From the other side you hear low voices speaking in a guttural tongue. At least two of them.
```

The bot will post the resolution, advance the turn counter, tick the light source,
and reprint the status block.

### Useful DM Commands Reference

| Command | Description |
|---|---|
| `/dm_resolve narrative:...` | Resolve current turn with narrative |
| `/dm_sethp target_name:X hp:N` | Set HP for character or NPC by name |
| `/dm_setstatus character_name:X status:Y notes:Z` | Set character status |
| `/dm_setroom name:X description:Y` | Move party to a new room |
| `/dm_addfeature name:X description:Y state:Z` | Add a room feature |
| `/dm_setfeature feature_name:X new_state:Y` | Update a feature's state |
| `/dm_addnpc name:X hp:N ac:N description:Y` | Add an NPC |
| `/dm_setnpcstatus npc_name:X status:Y` | Update NPC status (dead, fled, etc.) |
| `/dm_setlight label:X turns:N` | Set active light source (omit turns for permanent) |

### Player Commands Reference

| Command | Description |
|---|---|
| `/embark name:X character_class:Y equipment_package:Z` | Create character and join |
| `/turn action:...` | Submit your action for this turn |
| `/status` | Reprint the status block |

---
