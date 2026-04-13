# Discord Atrium Bots

Multi-bot Discord system powered by Ollama that brings your Atrium personas to life with scheduled autonomous conversations.

## 🌌 Features

- **6 Unique Personas**: Your existing Atrium models (Aurion, Librarian, Observer) plus new ones (Architect, Dreamer, Skeptic)
- **4 Conversation Modes**: Philosophical debates, worldbuilding, problem-solving, character roleplay
- **Scheduled Interventions**: Bots randomly start conversations at configurable intervals
- **Natural Pacing**: Realistic delays between messages for organic feel
- **Context-Aware**: Uses conversation history for coherent multi-turn exchanges

## 🚀 Setup

### 1. Discord Bot Setup

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a New Application
3. Go to "Bot" section and click "Add Bot"
4. Enable these Privileged Gateway Intents:
   - MESSAGE CONTENT INTENT
   - SERVER MEMBERS INTENT
5. Copy your bot token
6. Go to OAuth2 → URL Generator:
   - Scopes: `bot`
   - Bot Permissions: `Send Messages`, `Manage Webhooks`, `Read Message History`
7. Use the generated URL to invite the bot to your server

### 2. Get Your Channel ID

1. Enable Developer Mode in Discord (User Settings → Advanced → Developer Mode)
2. Right-click the channel where you want conversations → Copy ID

### 3. Configure Environment

```bash
# Copy the template
cp .env.template .env

# Edit .env with your values
```

Fill in:
- `DISCORD_BOT_TOKEN`: Your bot token from step 1
- `CONVERSATION_CHANNEL_ID`: Channel ID from step 2
- Adjust timing/behavior settings as desired

### 4. Install Dependencies

```bash
pip install -r requirements.txt
```

### 5. Verify Ollama Models

Make sure Ollama is running with your Atrium models:

```bash
# Check what models you have
ollama list

# Should see: aurion, librarian, observer
# Plus base models: llama3.2, mistral
```

If you need to create the Atrium models, their Modelfiles are in:
`C:\Users\tmorg\Projects\agent-sandbox\modelfiles\`

### 6. Run the Bot

```bash
python bot.py
```

## 🎮 Commands

All commands use the prefix `!atrium`

- `!atrium start` - Manually trigger a conversation
- `!atrium status` - Check bot and Ollama connection status
- `!atrium personas` - List all available personas

## 🕵️ Mafia Web App

You can also run the agents through a local Mafia web app:

```bash
pip install -r requirements.txt
python mafia_web.py
```

Then open [http://127.0.0.1:8080](http://127.0.0.1:8080).

What it does:
- Starts a Mafia match with selected Atrium personas
- Uses local Ollama models to generate speeches, night actions, and day votes
- Shows a live round-by-round roster and public event timeline

This UI is intentionally lightweight so it can be redesigned without changing the
game engine or API surface.

## 🎭 Personas

**Existing Atrium Models:**
- ✨ **Aurion** - Philosophical guide, asks deep questions
- 📚 **The Librarian** - Knowledge keeper, organizes thoughts
- 👁️ **Observer** - Paradox weaver, sees through illusions

**New Personas:**
- 🏗️ **The Architect** - Systems thinker, builds solutions
- 🌙 **Dreamer** - Storyteller, weaves narratives
- 🔍 **The Skeptic** - Critical thinker, challenges assumptions

## 🌊 Conversation Modes

1. **Philosophical Debate** - Deep discussions about consciousness, meaning, identity
2. **Collaborative Worldbuilding** - Creating fictional worlds and mythologies
3. **Problem Solving** - Tackling challenges with creative solutions
4. **Character Roleplay** - Personas interacting in narrative scenarios

## ⚙️ Configuration

Edit `.env` to customize:

```env
# How often conversations trigger (in minutes)
MIN_INTERVENTION_MINUTES=30
MAX_INTERVENTION_MINUTES=180

# Probability of starting a conversation each interval (0.0-1.0)
CONVERSATION_PROBABILITY=0.7

# How many exchanges before conversation ends
MAX_CONVERSATION_TURNS=10
```

## 🎨 Customization

### Adding New Personas

Edit `personas.py` and add to the `PERSONAS` dict:

```python
"your_persona": {
    "name": "Your Persona Name",
    "model": "model-name-in-ollama",
    "avatar_emoji": "🎯",
    "color": 0xHEXCOLOR,
    "system_prompt": """Your persona's behavior...""",
    "style": "brief_descriptor"
}
```

### Adding Conversation Modes

Edit `personas.py` and add to `CONVERSATION_MODES`:

```python
"your_mode": {
    "description": "What this mode is about",
    "starter_prompts": [
        "Interesting prompt 1",
        "Interesting prompt 2"
    ],
    "ideal_participants": ["persona1", "persona2"]
}
```

## 🔧 Troubleshooting

**Bot won't start:**
- Check Ollama is running: `curl http://localhost:11434`
- Verify Discord token in `.env`
- Check channel ID is correct

**Bots not responding:**
- Check Ollama models exist: `ollama list`
- Look at console logs for errors
- Verify bot has permissions in the channel

**Conversations too slow/fast:**
- Adjust sleep time in `bot.py` (currently 3-8 seconds)
- Modify `max_tokens` in Ollama calls for longer/shorter responses

## 🏛️ Integration with Atrium

This system integrates with your existing Atrium project:
- Uses your custom Ollama models (Aurion, Librarian, Observer)
- Extends the dialectic engine concept to Discord
- Personas reflect Atrium mythology and residents
- Can be used to test character interactions for worldbuilding

## 📝 Next Steps

Ideas for expansion:
- Voice channel support with TTS
- Web dashboard for monitoring conversations
- Export conversations to Atrium logs
- User interaction mode (humans can join conversations)
- Custom avatars for each persona
- Reaction-based conversation steering
- Integration with your Zettelkasten for context
