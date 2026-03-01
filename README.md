# Async Dungeon Crawler — Discord Bot

A Discord bot for asynchronous B/X D&D dungeon crawling, mediated via slash commands.

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
pip install "discord.py>=2.3" python-multipart fastapi "uvicorn[standard]"

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

Alternatively, create a `.env` file and use `python-dotenv`, check `.env.example` for deatils.

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

## Playing a Session

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

---
