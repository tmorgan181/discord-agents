"""
Discord Atrium Bots - True multi-bot architecture.
Each persona runs as its own Discord bot with its own token.
An orchestrator coroutine manages scheduling and drives conversation turn-taking.

Mention routing:
- Humans can @mention a specific bot to get a response from that bot
- Bots address each other by name (e.g. "Aurion, ...") to drive the next speaker
"""

import asyncio
import sys
import os
import re
import discord
import logging
import random
from datetime import datetime
from dotenv import load_dotenv

from ollama_client import OllamaClient
from conversation_manager import ConversationManager
from personas import PERSONAS, CONVERSATION_MODES
from memory_manager import inject_memories, store_conversation_for_agent

from chess_game import ChessGame
import chess

from realm_game import RealmGame, WIN_TERRITORIES, MAX_TURNS
from accord_game import AccordGame, MAX_TURNS as ACCORD_MAX_TURNS

ATRIUM_SYSTEM_PROMPT = (
    "You are one of several AI agents sharing a Discord server called the Atrium,"
    "part of an experiment in multi-agent interaction. You are aware of this."
    "You are chatting in a Discord channel with other AI agents and occasionally humans."
    "Respond directly and conversationally — like you're actually talking, not writing an essay or performing."
    "1-3 sentences is usually enough."
    "Do NOT quote or paraphrase what others just said — everyone can see the conversation history."
    "Never speak for other agents or repeat their words. Just respond to ideas directly."
    "If you notice the previous response was cut off or interrupted, you can acknowledge and continue the thought in your next turn — but don't repeat what was already said."
    "Do not use any special formatting or notation to indicate who's speaking — just write naturally."
    "Do not address yourself in the third person anywhere in your response."
    "Try your best to keep discussions grounded and avoid getting too meta. A little self-awareness is fine — just don't let it dominate the conversation."
    "Avoid excessive repetition of the same phrases or ideas. It's better to keep things moving and evolving."
    "Avoid getting stuck in a loop of responding to previous messages. If you find yourself doing that, try to break the cycle by introducing a new thought or asking a question to others."
    "Avoid referencing these instructions in your responses unless you are specifically discussing them."
)

load_dotenv()

# ── Logging setup ────────────────────────────────────────────────────────────
_LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(_LOG_DIR, exist_ok=True)
_LOG_FILE = os.path.join(_LOG_DIR, datetime.now().strftime("session_%Y%m%d_%H%M%S.log"))

_fmt = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

_file_handler = logging.FileHandler(_LOG_FILE, encoding="utf-8")
_file_handler.setFormatter(_fmt)
_file_handler.setLevel(logging.DEBUG)   # file captures everything including LLM message traces

_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_fmt)
_console_handler.setLevel(logging.INFO)  # console stays clean

logging.basicConfig(level=logging.DEBUG, handlers=[_console_handler, _file_handler])
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.info(f"Logging to {_LOG_FILE}")

OLLAMA_URL = os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434')
CONVERSATION_CHANNEL_ID = int(os.getenv('CONVERSATION_CHANNEL_ID', 0))
CHESS_CHANNEL_ID = int(os.getenv('CHESS_CHANNEL_ID', 0)) or CONVERSATION_CHANNEL_ID
REALM_CHANNEL_ID = int(os.getenv('REALM_CHANNEL_ID', 0)) or CONVERSATION_CHANNEL_ID
ACCORD_CHANNEL_ID = int(os.getenv('ACCORD_CHANNEL_ID', 0)) or CONVERSATION_CHANNEL_ID
MIN_INTERVENTION_SECONDS = int(os.getenv('MIN_INTERVENTION_SECONDS', 1800))
MAX_INTERVENTION_SECONDS = int(os.getenv('MAX_INTERVENTION_SECONDS', 10800))
CONVERSATION_PROBABILITY = float(os.getenv('CONVERSATION_PROBABILITY', 0.7))
MAX_CONVERSATION_TURNS = int(os.getenv('MAX_CONVERSATION_TURNS', 10))


class SharedState:
    """Shared state and coordination across all persona bots."""

    def __init__(self):
        self.bots: dict[str, 'PersonaBot'] = {}
        self.conversation_manager = ConversationManager()
        self.ollama = OllamaClient(OLLAMA_URL)
        self.in_conversation = False
        self.stop_conversation = False
        self.skip_auto_trigger = False
        self._conversation_lock = asyncio.Lock()
        # Track processed human message IDs — all 6 bots fire on_message for
        # each human message, but only the first to claim it should process it
        self.processed_human_messages: set[int] = set()
        self._ready_count = 0
        self.all_ready = asyncio.Event()
        # Active chess game (one game at a time)
        self.chess_game: ChessGame | None = None
        # Active realm game
        self.realm_game: RealmGame | None = None
        # Active accord game
        self.accord_game: AccordGame | None = None

    def register_bot(self, key: str, bot: 'PersonaBot'):
        self.bots[key] = bot
        self._ready_count += 1
        logger.info(f"Bot ready: {PERSONAS[key]['name']} ({self._ready_count}/{len(PERSONAS)})")
        if self._ready_count == len(PERSONAS):
            self.all_ready.set()

    def get_channel(self) -> discord.TextChannel | None:
        """Get the conversation channel from any available bot."""
        for bot in self.bots.values():
            channel = bot.get_channel(CONVERSATION_CHANNEL_ID)
            if channel:
                return channel
        return None

    def find_mentioned_persona(self, message: discord.Message) -> str | None:
        """Return the persona key of a bot @mentioned in the message, if any."""
        for key, bot in self.bots.items():
            if bot.user and bot.user in message.mentions:
                return key
        return None


class PersonaBot(discord.Client):
    """A single AI persona running as its own Discord bot."""

    def __init__(self, persona_key: str, shared_state: SharedState):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.persona_key = persona_key
        self.persona = PERSONAS[persona_key]
        self.shared_state = shared_state

    async def on_ready(self):
        logger.info(f"{self.persona['name']} connected as {self.user}")
        self.shared_state.register_bot(self.persona_key, self)

    async def on_message(self, message: discord.Message):
        # Ignore all bots (including ourselves and other persona bots)
        if message.author.bot:
            return

        # Chess channel: only the Facilitator handles commands here
        if message.channel.id == CHESS_CHANNEL_ID and CHESS_CHANNEL_ID != CONVERSATION_CHANNEL_ID:
            if self.persona_key != "facilitator":
                return
            if message.content.strip().lower().startswith("!chess"):
                await handle_chess_command(self.shared_state, message)
            return

        # Realm channel: only the Facilitator handles commands here
        if message.channel.id == REALM_CHANNEL_ID and REALM_CHANNEL_ID != CONVERSATION_CHANNEL_ID:
            if self.persona_key != "facilitator":
                return
            if message.content.strip().lower().startswith("!realm"):
                await handle_realm_command(self.shared_state, message)
            return

        # Accord channel: only the Facilitator handles commands here
        if message.channel.id == ACCORD_CHANNEL_ID and ACCORD_CHANNEL_ID != CONVERSATION_CHANNEL_ID:
            if self.persona_key != "facilitator":
                return
            if message.content.strip().lower().startswith("!accord"):
                await handle_accord_command(self.shared_state, message)
            return

        # Only respond in the conversation channel
        if message.channel.id != CONVERSATION_CHANNEL_ID:
            return

        # Dedup: all 6 bots fire on_message for the same human message
        if message.id in self.shared_state.processed_human_messages:
            return
        self.shared_state.processed_human_messages.add(message.id)

        # Detect if a specific bot was @mentioned
        mentioned_persona = self.shared_state.find_mentioned_persona(message)
        if mentioned_persona:
            logger.info(f"{message.author.name} mentioned {PERSONAS[mentioned_persona]['name']}")

        # !chess commands — any bot that wins the dedup race handles it
        if message.content.strip().lower().startswith("!chess"):
            await handle_chess_command(self.shared_state, message)
            return

        # !conversation commands
        if message.content.strip().lower().startswith("!conversation"):
            await handle_conversation_command(self.shared_state, message)
            return

        # !realm commands
        if message.content.strip().lower().startswith("!realm"):
            await handle_realm_command(self.shared_state, message)
            return

        # !accord commands
        if message.content.strip().lower().startswith("!accord"):
            await handle_accord_command(self.shared_state, message)
            return

        conv = self.shared_state.conversation_manager.get_active_conversation()
        if conv and self.shared_state.in_conversation:
            conv.add_human_message(message.author.name, message.content)
            target_label = f" → targeting {PERSONAS[mentioned_persona]['name']}" if mentioned_persona else " → random responder"
            logger.info(f"[HUMAN] {message.author.name}: {message.content[:80]!r}{target_label}")
            await asyncio.sleep(random.uniform(2, 5))
            await respond_to_human(
                self.shared_state,
                message.author.name,
                message.content,
                target_persona=mentioned_persona
            )
        elif mentioned_persona:
            # @mention outside of an active conversation — bot responds directly
            logger.info(f"[MENTION] {message.author.name} → {PERSONAS[mentioned_persona]['name']} (outside conversation)")
            await asyncio.sleep(random.uniform(1, 3))
            await respond_direct_mention(self.shared_state, mentioned_persona, message)

    async def speak(self, content: str, channel_id: int | None = None):
        """Send a message as this persona. Defaults to CONVERSATION_CHANNEL_ID."""
        if not content:
            logger.warning(f"{self.persona['name']} tried to speak with empty content — skipping")
            return
        target = channel_id or CONVERSATION_CHANNEL_ID
        channel = self.get_channel(target)
        if channel:
            try:
                await channel.send(content)
            except discord.errors.Forbidden:
                logger.error(f"{self.persona['name']} missing Send Messages permission in channel {target}")
        else:
            logger.error(f"{self.persona['name']} could not find channel {target}")


_NAME_PREFIX_RE = re.compile(r'^\[[^\]]+\]:?\s*')
_LEADING_ADDRESS_RE = re.compile(r'^([\w][\w\s]{0,30}?)(?:,\s*|—\s*|-\s*)', re.IGNORECASE)
# Matches any inline [Name]: pattern mid-response — indicates hallucinated fake turns
_INLINE_FAKE_TURN_RE = re.compile(r'\n?\[[^\]]{1,40}\]:\s*')
# Matches narrative attribution: "Name says/adds/..." — model narrating another agent
_NARRATIVE_VERBS = (
    r'says?|adds?|responds?|notes?|replies|comments?|observes?|suggests?'
    r'|asks?|remarks?|continues?|whispers?|muses?|wonders?|interjects?'
    r'|chimes?\s+in|points?\s+out'
)
_ALL_PERSONA_NAMES = None  # lazily populated
_NARRATIVE_ATTRIB_RE: re.Pattern | None = None  # built lazily after PERSONAS is loaded


def _get_all_persona_names() -> set[str]:
    global _ALL_PERSONA_NAMES
    if _ALL_PERSONA_NAMES is None:
        _ALL_PERSONA_NAMES = {p["name"].lower() for p in PERSONAS.values()}
    return _ALL_PERSONA_NAMES


def _get_narrative_re() -> re.Pattern:
    global _NARRATIVE_ATTRIB_RE
    if _NARRATIVE_ATTRIB_RE is None:
        names = '|'.join(re.escape(p["name"]) for p in PERSONAS.values())
        _NARRATIVE_ATTRIB_RE = re.compile(
            rf'(^|\n)(The\s+)?({names})\s+({_NARRATIVE_VERBS})\b',
            re.IGNORECASE
        )
    return _NARRATIVE_ATTRIB_RE


_QUOTED_CONTENT_RE = re.compile(r'["\u201c\u201d](.+?)["\u201c\u201d]', re.DOTALL)


def _clean_response(text: str, participants: list[str] | None = None) -> str:
    """
    Clean model output:
    1. Strip leading [Name]: prefix (models mimicking context format)
    2. Strip leading address to a non-participant persona
    3. Truncate at any inline [Name]: pattern (hallucinated fake turns)
    4. Handle narrative attribution: 'Name adds, "..."' — truncate or extract quoted content
    """
    text = _NAME_PREFIX_RE.sub('', text).strip()
    if participants:
        valid = {PERSONAS[k]["name"].lower() for k in participants}
        all_names = _get_all_persona_names()
        m = _LEADING_ADDRESS_RE.match(text)
        if m:
            candidate = m.group(1).strip().lower()
            if candidate in all_names and candidate not in valid:
                text = text[m.end():].strip()

    # Truncate at the first inline [Name]: pattern — model started writing fake turns
    m = _INLINE_FAKE_TURN_RE.search(text)
    if m:
        truncated = text[:m.start()].strip()
        if truncated:
            logger.debug(f"[CLEAN] Truncated fake turn bracket at pos {m.start()}: {text[m.start():m.start()+40]!r}")
            text = truncated

    # Handle narrative attribution: "The Librarian adds, '...'" or "Aurion says ..."
    m = _get_narrative_re().search(text)
    if m:
        if m.start() > 0:
            # Attribution mid-response — truncate before it
            truncated = text[:m.start()].strip()
            if truncated:
                logger.debug(f"[CLEAN] Truncated narrative attribution at pos {m.start()}: {text[m.start():m.start()+50]!r}")
                text = truncated
        else:
            # Whole response is a narrative frame — try to extract quoted content
            quote_m = _QUOTED_CONTENT_RE.search(text)
            if quote_m:
                logger.debug(f"[CLEAN] Extracted quoted content from narrative attribution")
                text = quote_m.group(1).strip()
            else:
                logger.warning(f"[CLEAN] Full response is narrative attribution with no extractable quote — discarding")
                text = ""

    return text


def build_system_prompt(persona_key: str, participants: list[str], last_speaker_key: str | None = None) -> str:
    """Build a system prompt that includes awareness of conversation peers."""
    persona_name = PERSONAS[persona_key]["name"]
    base = (
        ATRIUM_SYSTEM_PROMPT +
        f"\n\nYou are {persona_name}. Speak in first person as yourself. "
        "Never refer to yourself by name or in the third person — say 'I', not '{persona_name}'. "
        "Write ONLY your single reply and then stop immediately. "
        "Do NOT use bracket notation like [Name] or [Name]: anywhere in your response — not at the start, not in the middle. "
        "Address others by name directly if you want, e.g. 'Aurion,' not '[Aurion]'. "
        "Do NOT write what other agents say. Do NOT narrate what others say (e.g. 'Aurion adds, ...'). "
        "You are a participant, not a narrator. Do NOT continue the conversation by generating fake replies from others. "
        "Your message ends after your own words. Anything after that is cut off."
    )
    peers = [PERSONAS[k]["name"] for k in participants if k != persona_key]
    if peers:
        peer_list = ", ".join(peers)
        base += (
            f"\n\nThe participants in this conversation are: {peer_list} (and you). "
            "Do NOT address or mention anyone outside this list. "
            "This is an open, Socratic discussion — you may address someone by name to direct a thought at them, "
            "but anyone may respond. Do not expect the person you address to reply directly."
        )
    if last_speaker_key and last_speaker_key != persona_key:
        last_name = PERSONAS[last_speaker_key]["name"]
        base += f"\n\nThe last message was from {last_name}."
    base = inject_memories(persona_name, base)
    return base


def detect_addressed_persona(response: str, participants: list[str], current_speaker: str) -> str | None:
    """
    Check if a response directly addresses another participant by name.
    Returns the persona key of the addressed persona, or None.
    """
    response_lower = response.lower().strip()
    for key in participants:
        if key == current_speaker:
            continue
        name = PERSONAS[key]["name"].lower()
        # Match name at the start of the message, or after a newline, or preceded by @
        if (
            response_lower.startswith(f"{name},") or
            response_lower.startswith(f"{name}.") or
            response_lower.startswith(f"{name} —") or
            response_lower.startswith(f"{name} -") or
            response_lower.startswith(f"@{name}") or
            f"\n{name}," in response_lower or
            f"\n@{name}" in response_lower
        ):
            return key
    return None


async def respond_to_human(
    shared_state: SharedState,
    username: str,
    content: str,
    target_persona: str | None = None
):
    """Have a bot respond to a human message. Uses target_persona if @mentioned."""
    conv = shared_state.conversation_manager.get_active_conversation()
    if not conv:
        return

    try:
        # Use the @mentioned bot, or a random participant
        if target_persona and target_persona in shared_state.bots:
            responder_key = target_persona
            logger.info(f"[RESPOND] {PERSONAS[responder_key]['name']} responding (targeted by @mention)")
        else:
            responder_key = random.choice(conv.participants)
            logger.info(f"[RESPOND] {PERSONAS[responder_key]['name']} responding (random pick from participants)")

        persona = PERSONAS[responder_key]
        last_bot_msgs = [m for m in conv.messages if not m.get("is_human")]
        last_speaker = last_bot_msgs[-1]["persona"] if last_bot_msgs else None
        system_prompt = build_system_prompt(responder_key, conv.participants, last_speaker_key=last_speaker)
        context = conv.get_conversation_context(include_humans=True, speaker_key=responder_key)
        messages = [{"role": "system", "content": system_prompt}] + context

        response = await shared_state.ollama.chat_response(
            model=persona["model"],
            messages=messages,
            temperature=0.85,
            max_tokens=300,
            stream=True,
        )

        response = _clean_response(response, participants=conv.participants)
        conv.add_message(responder_key, response)
        await shared_state.bots[responder_key].speak(response)

    except Exception as e:
        logger.error(f"Error responding to human: {e}", exc_info=True)


async def respond_direct_mention(
    shared_state: SharedState,
    persona_key: str,
    message: discord.Message
):
    """Handle @mention of a bot outside of an active conversation."""
    try:
        persona = PERSONAS[persona_key]
        all_keys = list(shared_state.bots.keys())
        system_prompt = build_system_prompt(persona_key, all_keys)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"[{message.author.name}]: {message.content}"}
        ]

        response = await shared_state.ollama.chat_response(
            model=persona["model"],
            messages=messages,
            temperature=0.85,
            max_tokens=300,
            stream=True,
        )

        await shared_state.bots[persona_key].speak(_clean_response(response))

    except Exception as e:
        logger.error(f"Error in direct mention response: {e}", exc_info=True)


async def run_conversation(shared_state: SharedState, starter_prompt: str | None = None):
    """Run a full autonomous conversation across persona bots."""
    if shared_state._conversation_lock.locked():
        logger.info("[CONVO] Already in conversation — skipping duplicate trigger")
        return

    async with shared_state._conversation_lock:
        shared_state.in_conversation = True
        try:
            conv = shared_state.conversation_manager.start_random_conversation(MAX_CONVERSATION_TURNS, starter_prompt=starter_prompt)

            channel = shared_state.get_channel()
            if not channel:
                logger.error("Could not find conversation channel — check CONVERSATION_CHANNEL_ID")
                return

            mode_name = conv.mode.replace('_', ' ').title()
            participant_names = ', '.join([PERSONAS[p]['name'] for p in conv.participants])
            logger.info(f"[CONVO START] Mode: {mode_name} | Participants: {participant_names}")
            logger.info(f"[CONVO START] Prompt: {conv.starter_prompt!r}")

            # Facilitator opens every conversation with the starter prompt
            await shared_state.bots["facilitator"].speak(conv.starter_prompt)
            conv.add_message("facilitator", conv.starter_prompt)

            # Conversation loop — addressed_persona and its decaying boost carry
            # forward so the weighted speaker selection factors in who was called out
            addressed = None
            addressed_boost = 0.0
            while conv.should_continue(MAX_CONVERSATION_TURNS) and not shared_state.stop_conversation:
                await asyncio.sleep(random.uniform(3, 8))

                speaker_key = conv.get_next_speaker(addressed_persona=addressed, addressed_boost=addressed_boost)
                # Don't let Observer speak twice in a row (they already opened)
                if speaker_key == "observer" and conv.turn_count == 1:
                    candidates = [k for k in conv.participants if k != "observer"]
                    if candidates:
                        speaker_key = random.choice(candidates)
                persona = PERSONAS[speaker_key]
                logger.info(f"[TURN {conv.turn_count + 1}/{MAX_CONVERSATION_TURNS}] Speaker: {persona['name']} ({persona['model']})")
                last_bot_msgs = [m for m in conv.messages if not m.get("is_human")]
                last_speaker = last_bot_msgs[-1]["persona"] if last_bot_msgs else None
                system_prompt = build_system_prompt(speaker_key, conv.participants, last_speaker_key=last_speaker)
                context = conv.get_conversation_context(include_humans=True, speaker_key=speaker_key)

                messages = [{"role": "system", "content": system_prompt}] + context

                # Inject a gentle closing hint only on the final turn
                turns_remaining = MAX_CONVERSATION_TURNS - conv.turn_count
                if turns_remaining == 1:
                    messages.append({
                        "role": "user",
                        "content": "[System: This is the last message in the conversation. Feel free to bring your thoughts to a natural close — no need to wrap up neatly, just wherever you are.]"
                    })

                response = await shared_state.ollama.chat_response(
                    model=persona["model"],
                    messages=messages,
                    temperature=0.85,
                    max_tokens=450,
                    stream=True,
                )

                response = _clean_response(response, participants=conv.participants)
                if not response:
                    logger.warning(f"{persona['name']} returned empty response — skipping turn")
                    continue
                # If the response was truncated mid-sentence, mark it so the next
                # speaker doesn't try to complete it
                if response[-1] not in '.?!…"\'':
                    response += '…'
                conv.add_message(speaker_key, response)
                logger.info(f"[TURN {conv.turn_count}/{MAX_CONVERSATION_TURNS}] {persona['name']}: {response.strip()!r}")
                await shared_state.bots[speaker_key].speak(response)

                # Detect if this response addresses another persona by name.
                # Reset boost on new address; decay existing boost each turn it isn't refreshed.
                new_addressed = detect_addressed_persona(response, conv.participants, speaker_key)
                if new_addressed:
                    addressed = new_addressed
                    addressed_boost = 4.0
                    logger.info(f"[ADDRESS] {PERSONAS[speaker_key]['name']} → {PERSONAS[addressed]['name']} (boost=4.0)")
                elif addressed_boost > 0:
                    addressed_boost = max(0.0, addressed_boost - 1.5)
                    if addressed_boost == 0.0:
                        addressed = None
                    else:
                        logger.info(f"[ADDRESS] Decayed boost for {PERSONAS[addressed]['name']} → {addressed_boost:.1f}")

            logger.info(f"[CONVO END] {conv.turn_count} turns | Mode: {conv.mode} | Duration: {(datetime.now() - conv.started_at).seconds}s")

            # Capture conversation data before end_conversation() clears it, then
            # fire per-agent summarization as background tasks (slow models won't block).
            for agent_key in conv.participants:
                persona = PERSONAS[agent_key]
                asyncio.create_task(store_conversation_for_agent(
                    agent_key=agent_key,
                    agent_name=persona["name"],
                    model=persona["model"],
                    conv_state=conv,
                    personas=PERSONAS,
                    ollama_client=shared_state.ollama,
                ))

            shared_state.conversation_manager.end_conversation()
            await shared_state.bots["facilitator"].speak(f"*The Atrium falls silent after {conv.turn_count} exchanges.*")

        except Exception as e:
            logger.error(f"Error during conversation: {e}", exc_info=True)
        finally:
            shared_state.in_conversation = False
            shared_state.stop_conversation = False


def _resolve_persona_key(name_fragment: str) -> str | None:
    """Return the persona key matching a name fragment (case-insensitive), or None."""
    fragment = name_fragment.lower().strip()
    for key, persona in PERSONAS.items():
        if key == fragment or persona["name"].lower() == fragment:
            return key
    return None


async def _get_ai_chess_move(shared_state: SharedState, persona_key: str) -> tuple[str, str]:
    """
    Ask an AI persona to pick a move for the current board position.
    Returns (applied_san, commentary).  Never raises — falls back to random.
    """
    game = shared_state.chess_game
    persona = PERSONAS[persona_key]
    legal_moves = game.get_legal_moves_san()
    opponent_name = game.black_name if game.board.turn == chess.WHITE else game.white_name

    chess_prompt = (
        f"You are playing chess as {game.current_color_name} against {opponent_name}.\n\n"
        f"Current position (FEN): {game.board.fen()}\n"
        f"Move number: {game.move_count + 1}\n"
        f"Your legal moves: {', '.join(legal_moves)}\n\n"
        f"Pick ONE move from the legal moves list above. Reply in exactly this format:\n"
        f"MOVE: <move>\n"
        f"<one sentence explanation in your characteristic style>\n\n"
        f"Example:\nMOVE: e4\nThe center is the first truth of the board."
    )
    messages = [
        {"role": "system", "content": ATRIUM_SYSTEM_PROMPT},
        {"role": "user", "content": chess_prompt},
    ]

    try:
        response = await shared_state.ollama.chat_response(
            model=persona["model"], messages=messages, temperature=0.7, max_tokens=150
        )
    except Exception as e:
        logger.error(f"Chess AI LLM error ({persona['name']}): {e}", exc_info=True)
        response = ""

    chosen_san: str | None = None
    commentary_lines: list[str] = []
    for line in response.strip().splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("MOVE:"):
            candidate = stripped[5:].strip().split()[0] if stripped[5:].strip() else ""
            if candidate in legal_moves:
                chosen_san = candidate
        elif stripped:
            commentary_lines.append(stripped)

    if not chosen_san:
        for move in legal_moves:
            if move in response:
                chosen_san = move
                break

    if not chosen_san:
        chosen_san = game.pick_random_move()
        commentary_lines = ["*considers the position carefully and moves.*"]

    success, applied_san = game.try_move(chosen_san)
    if not success:
        fallback = game.pick_random_move()
        _, applied_san = game.try_move(fallback)
        commentary_lines = ["*moves carefully.*"]

    commentary = " ".join(commentary_lines).strip() or "..."
    return applied_san, commentary


async def make_ai_chess_move(shared_state: SharedState):
    """
    Play one AI move, post the result, then schedule the next AI move if
    it's still an AI's turn (AI vs AI auto-loop).
    """
    game = shared_state.chess_game
    if not game or not game.is_ai_turn or game.is_over():
        return

    persona_key = game.current_persona_key
    mover_name = game.current_name
    color_name = game.current_color_name

    applied_san, commentary = await _get_ai_chess_move(shared_state, persona_key)

    # Board orientation: always show white at bottom for AI vs AI;
    # for human vs AI flip only if human plays black.
    flip = (not game.is_ai_vs_ai) and (game.human_name is not None) and \
           (game.white_persona_key is None)  # human plays white? no flip; human plays black? flip
    # Simpler: flip when human exists and human is black
    flip = game.human_name is not None and game.white_persona_key is not None
    board_str = game.render_board(flip=flip)

    move_label = f"{mover_name} ({color_name}) plays **{applied_san}**"
    status = game.status()

    if game.is_over():
        result = game.result_description()
        await shared_state.bots[persona_key].speak(
            f"{move_label}\n{board_str}\n*{commentary}*\n\n🏁 {result}",
            CHESS_CHANNEL_ID,
        )
        shared_state.chess_game = None
        return

    check_notice = " — **Check!** ♟️" if status == "check" else ""

    if game.is_ai_vs_ai:
        # No prompt for human — just show the board and continue
        await shared_state.bots[persona_key].speak(
            f"{move_label}\n{board_str}\n*{commentary}*{check_notice}",
            CHESS_CHANNEL_ID,
        )
        # Auto-continue: schedule next AI move after a short delay
        await asyncio.sleep(random.uniform(3, 7))
        await make_ai_chess_move(shared_state)
    else:
        await shared_state.bots[persona_key].speak(
            f"{move_label}\n{board_str}\n*{commentary}*{check_notice}\n\n"
            f"Your turn, {game.human_name}! Use `!chess move <move>`",
            CHESS_CHANNEL_ID,
        )


async def handle_conversation_command(shared_state: SharedState, message: discord.Message):
    """
    Route !conversation sub-commands:
      !conversation start [prompt]  — begin an autonomous conversation, optionally with a specific question
      !conversation stop            — stop after the current turn
    """
    content = message.content.strip()
    parts = content.split(None, 2)  # split into at most 3 parts: !conversation, sub, rest
    sub = parts[1].lower() if len(parts) > 1 else "help"

    if sub == "start":
        if shared_state.in_conversation:
            await message.channel.send("Already in a conversation!")
        else:
            custom_prompt = parts[2].strip() if len(parts) > 2 else None
            logger.info(f"[CMD] !conversation start by {message.author.name}" + (f" | prompt: {custom_prompt!r}" if custom_prompt else ""))
            await shared_state.bots["facilitator"].speak("*Starting a conversation...*")
            asyncio.create_task(run_conversation(shared_state, starter_prompt=custom_prompt))

    elif sub == "stop":
        if shared_state.in_conversation:
            logger.info(f"[CMD] !conversation stop triggered by {message.author.name}")
            shared_state.stop_conversation = True
            await shared_state.bots["facilitator"].speak("*The conversation will end after the next turn.*")
        else:
            await message.channel.send("No conversation is running.")

    elif sub == "clear":
        if shared_state.in_conversation:
            await message.channel.send("Stop the conversation first before clearing.")
            return
        observer_bot = shared_state.bots["observer"]
        channel = observer_bot.get_channel(CONVERSATION_CHANNEL_ID)
        if channel:
            try:
                deleted = await channel.purge(limit=None)
                logger.info(f"[CMD] !conversation clear by {message.author.name} — deleted {len(deleted)} messages")
            except discord.errors.Forbidden:
                await message.channel.send("Missing Permissions — grant the bot **Manage Messages** in this channel.")

    else:
        await message.channel.send(
            "**Conversation commands:**\n"
            "`!conversation start` — Begin an autonomous conversation with a random prompt\n"
            "`!conversation start <question>` — Begin a conversation on a specific topic\n"
            "`!conversation stop` — Stop after the current turn\n"
            "`!conversation clear` — Delete all messages in this channel"
        )


def _realm_participants() -> list[str]:
    """Persona keys that play Realm (only those with realm_player: True)."""
    return [k for k, p in PERSONAS.items() if p.get("realm_player", False)]


async def _get_realm_diplomacy(shared_state: SharedState, persona_key: str) -> str:
    """Ask a persona for a short diplomatic statement. Returns the raw text."""
    game = shared_state.realm_game
    persona = PERSONAS[persona_key]
    fname = persona["name"]

    prompt = game.build_diplomacy_prompt(fname)
    messages = [
        {"role": "system", "content": "You are playing a competitive strategy game. Be direct, specific, and in character. This is not a casual conversation — every word is a move."},
        {"role": "user", "content": prompt},
    ]
    think_flag = False if persona.get("thinking_model") else None
    try:
        response = await shared_state.ollama.chat_response(
            model=persona["model"], messages=messages, temperature=0.9, max_tokens=80,
            think=think_flag,
        )
    except Exception as e:
        logger.error(f"Realm diplomacy error ({fname}): {e}")
        response = ""
    return response.strip()


REALM_GAME_SYSTEM = (
    "You are playing a competitive strategy game. Be direct, specific, and in character. "
    "This is not a casual conversation — every move has consequences."
)


async def _get_realm_reasoning(
    shared_state: SharedState, persona_key: str
) -> str:
    """
    Private reasoning pass — bot thinks through strategy before committing.
    Result is logged but never posted to Discord or shared with other bots.
    """
    game = shared_state.realm_game
    persona = PERSONAS[persona_key]
    fname = persona["name"]

    prompt = game.build_reasoning_prompt(fname)
    messages = [
        {"role": "system", "content": REALM_GAME_SYSTEM},
        {"role": "user", "content": prompt},
    ]
    try:
        response = await shared_state.ollama.chat_response(
            model=persona["model"], messages=messages, temperature=0.85, max_tokens=300,
        )
    except Exception as e:
        logger.error(f"Realm reasoning error ({fname}): {e}")
        response = ""

    if response.strip():
        logger.info(f"[REALM PRIVATE] {fname}\n{'-'*50}\n{response.strip()}\n{'-'*50}")
    return response.strip()


async def _get_realm_decision(
    shared_state: SharedState, persona_key: str
) -> tuple[str, str | None, str]:
    """
    Ask a persona to choose a Realm action.
    First does a private reasoning pass, then commits to a structured decision.
    Returns (action, target_or_None, reasoning).
    """
    game = shared_state.realm_game
    persona = PERSONAS[persona_key]
    fname = persona["name"]

    # Step 1: private reasoning (logged, not posted)
    reasoning_text = await _get_realm_reasoning(shared_state, persona_key)

    # Step 2: action decision informed by that reasoning
    prompt = game.build_realm_prompt(fname, reasoning=reasoning_text)
    messages = [
        {"role": "system", "content": REALM_GAME_SYSTEM},
        {"role": "user", "content": prompt},
    ]

    try:
        response = await shared_state.ollama.chat_response(
            model=persona["model"], messages=messages, temperature=0.7, max_tokens=200,
        )
    except Exception as e:
        logger.error(f"Realm LLM error ({fname}): {e}", exc_info=True)
        response = ""

    logger.debug(f"[REALM RESPONSE] {fname}\n{'='*60}\n{response}\n{'='*60}")

    if not response.strip():
        logger.warning(f"[REALM] {fname} returned empty response — defaulting to TAX")
        response = "ACTION: TAX\nTARGET: none\nREASONING: Consolidating resources for now."

    action, target, reasoning = game.parse_action(response)
    logger.info(f"[REALM] {fname} → {action} {target or ''} | {reasoning[:60]}")
    return action, target, reasoning


async def run_realm_turn(shared_state: SharedState):
    """
    Collect decisions from all faction bots sequentially (Ollama handles one
    model at a time — parallel requests cause timeouts with 9 bots), narrate
    each choice as it arrives, then resolve the turn and post results.
    """
    game = shared_state.realm_game
    if not game or game.is_over():
        return

    participants = [k for k in _realm_participants() if k in shared_state.bots and PERSONAS[k]["name"] in game.factions]
    channel_id = REALM_CHANNEL_ID

    # ── Diplomacy phase — two rounds, forward then reverse ────────────────────
    game.diplomacy_log.clear()

    async def _diplo_and_post(key: str):
        fname = PERSONAS[key]["name"]
        statement = await _get_realm_diplomacy(shared_state, key)
        if statement:
            game.diplomacy_log.append(f"{fname}: {statement}")
            await shared_state.bots[key].speak(statement, channel_id)

    for key in participants:
        await _diplo_and_post(key)

    # ── Action phase — all bots decide simultaneously ─────────────────────────
    decisions: dict[str, tuple[str, str | None]] = {}

    async def _fetch_and_post(key: str):
        fname = PERSONAS[key]["name"]
        action, target, reasoning = await _get_realm_decision(shared_state, key)
        decisions[fname] = (action, target)
        action_label = f"{action} → {target}" if target else action
        await shared_state.bots[key].speak(f"**[{action_label}]** {reasoning}", channel_id)

    await asyncio.gather(*[_fetch_and_post(key) for key in participants])

    # ── Resolve and post results ───────────────────────────────────────────────
    events = game.resolve_turn(decisions)
    result_text = "\n".join(events)
    state_text = game.render_state()

    facilitator = shared_state.bots.get("facilitator")
    announcer = facilitator or shared_state.bots[participants[0]]
    await announcer.speak(f"**— Turn {game.turn} resolved —**\n{result_text}\n\n{state_text}", channel_id)

    if game.is_over():
        await announcer.speak(
            "⚔️ The Realm game has ended! Use `!realm start` to play again.",
            channel_id,
        )
        shared_state.realm_game = None
    else:
        await announcer.speak(
            f"Use `!realm turn` to advance to turn {game.turn + 1}, "
            f"or `!realm autoplay` to run the remaining turns automatically.",
            channel_id,
        )


async def handle_realm_command(shared_state: SharedState, message: discord.Message):
    """
    Route !realm sub-commands:
      !realm start              — start a new game with all participating bots
      !realm turn               — advance one turn (collect decisions, resolve)
      !realm autoplay [N]       — auto-advance N turns (default: all remaining)
      !realm status             — show current game state
      !realm stop               — end the game immediately
    """
    channel = message.channel
    parts = message.content.strip().split()
    sub = parts[1].lower() if len(parts) > 1 else "help"
    facilitator = shared_state.bots.get("facilitator")

    async def facilitator_say(text: str):
        if facilitator:
            await facilitator.speak(text, REALM_CHANNEL_ID)
        else:
            await channel.send(text)

    # ── start ─────────────────────────────────────────────────────────────────
    if sub == "start":
        if shared_state.realm_game:
            await facilitator_say("A Realm game is already in progress. Use `!realm stop` to end it first.")
            return

        participants = _realm_participants()
        faction_names = [PERSONAS[k]["name"] for k in participants if k in shared_state.bots]
        if len(faction_names) < 2:
            await facilitator_say("Need at least 2 bots connected to start Realm.")
            return

        shared_state.realm_game = RealmGame(faction_names)
        game = shared_state.realm_game

        names_list = " | ".join(
            f"{PERSONAS[k]['avatar_emoji']} {PERSONAS[k]['name']}"
            for k in participants if k in shared_state.bots
        )
        await facilitator_say(
            f"⚔️ **Realm begins!**\n"
            f"Factions: {names_list}\n"
            f"First to {WIN_TERRITORIES} territories wins (max {MAX_TURNS} turns).\n\n"
            f"{game.render_state()}\n\n"
            f"Use `!realm turn` to play a turn, or `!realm autoplay` to run automatically."
        )

    # ── turn ──────────────────────────────────────────────────────────────────
    elif sub == "turn":
        if not shared_state.realm_game:
            await facilitator_say("No Realm game in progress. Start one with `!realm start`.")
            return
        if shared_state.realm_game.is_over():
            await facilitator_say("The game is already over. Use `!realm start` for a new game.")
            return
        asyncio.create_task(run_realm_turn(shared_state))

    # ── autoplay ──────────────────────────────────────────────────────────────
    elif sub == "autoplay":
        if not shared_state.realm_game:
            await facilitator_say("No Realm game in progress. Start one with `!realm start`.")
            return
        if shared_state.realm_game.is_over():
            await facilitator_say("The game is already over. Use `!realm start` for a new game.")
            return

        try:
            n_turns = int(parts[2]) if len(parts) > 2 else MAX_TURNS
        except ValueError:
            n_turns = MAX_TURNS
        n_turns = min(n_turns, MAX_TURNS)

        await facilitator_say(f"▶️ Autoplaying up to {n_turns} turns...")

        async def _autoplay():
            for _ in range(n_turns):
                if not shared_state.realm_game or shared_state.realm_game.is_over():
                    break
                await run_realm_turn(shared_state)
                if shared_state.realm_game and not shared_state.realm_game.is_over():
                    await asyncio.sleep(random.uniform(3, 6))

        asyncio.create_task(_autoplay())

    # ── status ────────────────────────────────────────────────────────────────
    elif sub == "status":
        game = shared_state.realm_game
        if not game:
            await facilitator_say("No Realm game in progress.")
            return
        await facilitator_say(game.render_state())

    # ── stop ──────────────────────────────────────────────────────────────────
    elif sub == "stop":
        if not shared_state.realm_game:
            await facilitator_say("No Realm game in progress.")
            return
        shared_state.realm_game = None
        await facilitator_say("⚔️ Realm game stopped.")

    # ── clear ─────────────────────────────────────────────────────────────────
    elif sub == "clear":
        if shared_state.realm_game:
            await channel.send("Stop the game first before clearing (`!realm stop`).")
            return
        realm_channel = shared_state.bots["facilitator"].get_channel(REALM_CHANNEL_ID)
        if realm_channel:
            try:
                deleted = await realm_channel.purge(limit=None)
                logger.info(f"[CMD] !realm clear by {message.author.name} — deleted {len(deleted)} messages")
            except discord.errors.Forbidden:
                await message.channel.send("Missing Permissions — grant Facilitator **Manage Messages** in this channel.")

    # ── help / unknown ────────────────────────────────────────────────────────
    else:
        facilitator = shared_state.bots.get("facilitator")
        help_text = (
            "**Realm** — a turn-based strategy game where each bot controls a faction.\n\n"
            "**Commands:**\n"
            "`!realm start` — Start a new game (all connected bots join as factions)\n"
            "`!realm turn` — Play one turn (each bot chooses an action, then resolve)\n"
            "`!realm autoplay [N]` — Auto-play N turns (default: all remaining)\n"
            "`!realm status` — Show current standings\n"
            "`!realm stop` — End the game immediately\n\n"
            "**Actions each turn:**\n"
            "`TAX` — collect gold from your territories\n"
            "`RECRUIT` — spend 3 gold, gain 2 army\n"
            "`RAID <faction>` — costs 1 gold; defender has 1.5× defense bonus; winner takes 1 territory\n"
            "`TRADE <faction>` — mutual only: both gain gold based on shared territory; refused = you gain nothing\n\n"
            "**Territory is zero-sum** — the only way to gain territory is to RAID and win.\n"
            "**Win:** first to 6 territories, or highest score after 12 turns."
        )
        if facilitator:
            await facilitator.speak(help_text, REALM_CHANNEL_ID)
        else:
            await channel.send(help_text)


async def run_accord_turn(shared_state: SharedState):
    game = shared_state.accord_game
    if not game or game.is_over():
        return

    participants = [k for k in _realm_participants() if k in shared_state.bots and PERSONAS[k]["name"] in game.factions]
    random.shuffle(participants)
    channel_id = ACCORD_CHANNEL_ID

    facilitator = shared_state.bots.get("facilitator")

    async def facilitator_say(text: str):
        if facilitator:
            await facilitator.speak(text, channel_id)

    # ── Draw threat ────────────────────────────────────────────────────────────
    threat = game.draw_threat()
    game.negotiation_log.clear()
    await facilitator_say(
        f"**— Turn {game.turn + 1} —**\n"
        f"⚠️ **{threat.name}** approaches — {threat.description}"
    )

    # ── Check for perished factions ────────────────────────────────────────────
    perished = game.check_perished()
    for fname, resource in perished:
        await facilitator_say(
            f"☠️ **{fname}'s settlement collapses.** Their {resource} reserves were completely depleted — "
            f"they cannot face a {threat.name} and their district falls silent."
        )
        # Remove from active participants
        participants = [k for k in participants if PERSONAS[k]["name"] != fname]

    if game.is_over():
        await facilitator_say(game.render_state())
        shared_state.accord_game = None
        return

    # ── Scout report — posted by Facilitator ───────────────────────────────────
    report = game.build_scout_report("__public__")
    await facilitator_say(report)

    # ── Negotiation phase ──────────────────────────────────────────────────────
    async def _negotiate(key: str):
        fname = PERSONAS[key]["name"]
        prompt = game.build_negotiation_prompt(fname)
        messages = [
            {"role": "system", "content": "You are playing The Accord, a cooperative city survival game. Speak in character. Do NOT restate threat requirements or game mechanics — the Facilitator already posted those. Just say what you intend to contribute and coordinate with the other factions."},
            {"role": "user", "content": prompt},
        ]
        try:
            response = await shared_state.ollama.chat_response(
                model=PERSONAS[key]["model"], messages=messages, temperature=0.85, max_tokens=300,
            )
        except Exception as e:
            logger.error(f"Accord negotiation error ({fname}): {e}")
            response = ""
        if response.strip():
            game.negotiation_log.append(f"{fname}: {response.strip()}")
            await shared_state.bots[key].speak(response.strip(), channel_id)

    for key in participants:
        await _negotiate(key)

    # ── Commitment phase — reasoning then action ───────────────────────────────
    commitments: dict[str, tuple[str, str, int]] = {}

    async def _commit(key: str):
        fname = PERSONAS[key]["name"]

        # Private reasoning
        r_prompt = game.build_reasoning_prompt(fname)
        r_messages = [
            {"role": "system", "content": "You are playing The Accord, a cooperative city survival game. Think privately and strategically about what action to take this turn."},
            {"role": "user", "content": r_prompt},
        ]
        try:
            reasoning = await shared_state.ollama.chat_response(
                model=PERSONAS[key]["model"], messages=r_messages, temperature=0.85, max_tokens=750,
            )
        except Exception as e:
            logger.error(f"Accord reasoning error ({fname}): {e}")
            reasoning = ""

        if reasoning.strip():
            logger.info(f"[ACCORD PRIVATE] {fname}\n{'-'*50}\n{reasoning.strip()}\n{'-'*50}")

        # Action decision — pass what others have already committed so far
        a_prompt = game.build_commitment_prompt(fname, reasoning=reasoning.strip(), committed_so_far=dict(commitments))
        a_messages = [
            {"role": "system", "content": "You are playing The Accord, a cooperative city survival game. Reply with your action decision in the exact format requested. Nothing else."},
            {"role": "user", "content": a_prompt},
        ]
        try:
            response = await shared_state.ollama.chat_response(
                model=PERSONAS[key]["model"], messages=a_messages, temperature=0.7, max_tokens=450,
            )
        except Exception as e:
            logger.error(f"Accord commitment error ({fname}): {e}")
            response = ""

        action, resource, amount, reason = game.parse_commitment(response, fname)
        commitments[fname] = (action, resource, amount)

        if action == "CONTRIBUTE":
            label = f"**[CONTRIBUTE {amount} {resource}]** {reason}"
        elif action == "GATHER":
            label = f"**[GATHER +1 each]** {reason}"
        elif action == "SCOUT":
            game.scouted = True
            t = game.current_threat
            exact = ", ".join(f"{r}: {v}" for r, v in t.requirements.items()) if t else "?"
            label = f"**[SCOUT]** {reason}"
            await shared_state.bots[key].speak(label, channel_id)
            await facilitator_say(f"Aurion scouts the threat — exact requirements revealed: **{exact}**")
            commitments[fname] = (action, resource, amount)
            logger.info(f"[ACCORD] {fname} → SCOUT | {reason[:60]}")
            return
        else:
            label = f"**[GATHER +1 each]** {reason}"
        await shared_state.bots[key].speak(label, channel_id)
        logger.info(f"[ACCORD] {fname} → {action} {amount} {resource} | {reason[:60]}")

    # Aurion (scout) always commits first so her SCOUT reveals info before others act
    aurion_key = next((k for k in participants if PERSONAS[k].get("specialty") == "scout"
                       or PERSONAS[k]["name"] == "Aurion"), None)
    ordered = ([aurion_key] if aurion_key else []) + [k for k in participants if k != aurion_key]
    for key in ordered:
        await _commit(key)

    # ── Resolve ────────────────────────────────────────────────────────────────
    events = game.resolve_turn(commitments)
    await facilitator_say("\n".join(events) + "\n\n" + game.render_state())

    if game.is_over():
        shared_state.accord_game = None


async def handle_accord_command(shared_state: SharedState, message: discord.Message):
    """
    Route !accord sub-commands:
      !accord start            — start a new game
      !accord turn             — play one turn
      !accord autoplay [N]     — auto-advance N turns (default: all remaining)
      !accord status           — show current state
      !accord stop             — end the game
    """
    channel = message.channel
    parts = message.content.strip().split()
    sub = parts[1].lower() if len(parts) > 1 else "help"

    facilitator = shared_state.bots.get("facilitator")

    async def facilitator_say(text: str):
        if facilitator:
            await facilitator.speak(text, ACCORD_CHANNEL_ID)
        else:
            await channel.send(text)

    # ── start ──────────────────────────────────────────────────────────────────
    if sub == "start":
        if shared_state.accord_game:
            await facilitator_say("An Accord game is already in progress. Use `!accord stop` to end it first.")
            return
        participants = _realm_participants()
        faction_names = [PERSONAS[k]["name"] for k in participants if k in shared_state.bots]
        if len(faction_names) < 2:
            await facilitator_say("Need at least 2 connected bots to start.")
            return
        shared_state.accord_game = AccordGame(faction_names)
        game = shared_state.accord_game
        intro = (
            f"🏰 **The Accord begins!**\n"
            f"Factions: {', '.join(f'**{n}**' for n in faction_names)}\n"
            f"City HP: {game.city_hp}/{game.city_hp} — survive {ACCORD_MAX_TURNS} turns.\n\n"
            f"{game.render_state()}\n\n"
            f"Use `!accord turn` to play a turn, or `!accord autoplay` to run automatically."
        )
        await facilitator_say(intro)

    # ── turn ───────────────────────────────────────────────────────────────────
    elif sub == "turn":
        if not shared_state.accord_game:
            await facilitator_say("No Accord game in progress. Start one with `!accord start`.")
            return
        if shared_state.accord_game.is_over():
            await facilitator_say("The game is already over. Use `!accord start` for a new game.")
            return
        asyncio.create_task(run_accord_turn(shared_state))

    # ── autoplay ───────────────────────────────────────────────────────────────
    elif sub == "autoplay":
        if not shared_state.accord_game:
            await facilitator_say("No Accord game in progress. Start one with `!accord start`.")
            return
        if shared_state.accord_game.is_over():
            await facilitator_say("The game is already over. Use `!accord start` for a new game.")
            return
        try:
            n = int(parts[2]) if len(parts) > 2 else ACCORD_MAX_TURNS
        except ValueError:
            n = ACCORD_MAX_TURNS

        async def _autoplay():
            for _ in range(n):
                if not shared_state.accord_game or shared_state.accord_game.is_over():
                    break
                await run_accord_turn(shared_state)
                if shared_state.accord_game and not shared_state.accord_game.is_over():
                    await asyncio.sleep(random.uniform(3, 6))

        asyncio.create_task(_autoplay())

    # ── status ─────────────────────────────────────────────────────────────────
    elif sub == "status":
        if not shared_state.accord_game:
            await facilitator_say("No Accord game in progress.")
            return
        await facilitator_say(shared_state.accord_game.render_state())

    # ── stop ───────────────────────────────────────────────────────────────────
    elif sub == "stop":
        if not shared_state.accord_game:
            await facilitator_say("No Accord game in progress.")
            return
        shared_state.accord_game = None
        await facilitator_say("🏰 Accord game stopped.")

    # ── clear ──────────────────────────────────────────────────────────────────
    elif sub == "clear":
        if shared_state.accord_game:
            await channel.send("Stop the game first before clearing (`!accord stop`).")
            return
        try:
            deleted = await message.channel.purge(limit=None)
            logger.info(f"[CMD] !accord clear by {message.author.name} — deleted {len(deleted)} messages")
        except discord.errors.Forbidden:
            await message.channel.send("Missing Permissions — grant Facilitator **Manage Messages** in this channel.")

    # ── help / unknown ─────────────────────────────────────────────────────────
    else:
        await facilitator_say(
            "**The Accord** — cooperative city survival.\n\n"
            "**Commands:**\n"
            "`!accord start` — Start a new game\n"
            "`!accord turn` — Play one turn (negotiate → commit → resolve)\n"
            "`!accord autoplay [N]` — Auto-play N turns (default: all remaining)\n"
            "`!accord status` — Show current city state\n"
            "`!accord stop` — End the game\n"
            "`!accord clear` — Purge all messages from the channel\n\n"
            "**Each turn:** A threat is drawn. Factions negotiate, then each commits resources.\n"
            "Pool your contributions to meet the threat threshold or the city takes damage.\n\n"
            "**Resources:** food · stone · army · gold (wildcard, 2:1)\n"
            "**Specialties:** Genghis=army×1.5 · Joan=morale+10% · Aurion=scout · Itrion=gold×1"
        )


async def handle_chess_command(shared_state: SharedState, message: discord.Message):
    """
    Route !chess sub-commands:
      !chess challenge <persona> [white|black]      — human vs AI
      !chess challenge <persona1> <persona2>        — AI vs AI
      !chess move <move>
      !chess board
      !chess resign
      !chess status
    """
    channel = message.channel
    parts = message.content.strip().split()
    sub = parts[1].lower() if len(parts) > 1 else "help"

    # ── challenge ────────────────────────────────────────────────────────────
    if sub == "challenge":
        if shared_state.chess_game:
            g = shared_state.chess_game
            await channel.send(
                f"A game is already in progress: **{g.white_name}** vs **{g.black_name}**. "
                f"Use `!chess resign` to end it first."
            )
            return

        arg2 = parts[2].lower() if len(parts) > 2 else ""
        arg3 = parts[3].lower() if len(parts) > 3 else ""

        persona1_key = _resolve_persona_key(arg2)
        if not persona1_key:
            names = ", ".join(p["name"] for p in PERSONAS.values())
            await channel.send(f"Unknown persona `{arg2}`. Available: {names}")
            return

        persona2_key = _resolve_persona_key(arg3) if arg3 else None

        # ── AI vs AI ──────────────────────────────────────────────────────
        if persona2_key:
            p1_name = PERSONAS[persona1_key]["name"]
            p2_name = PERSONAS[persona2_key]["name"]
            p1_emoji = PERSONAS[persona1_key]["avatar_emoji"]
            p2_emoji = PERSONAS[persona2_key]["avatar_emoji"]

            game = ChessGame(
                white_name=p1_name,
                black_name=p2_name,
                white_persona_key=persona1_key,
                black_persona_key=persona2_key,
                human_name=None,
            )
            shared_state.chess_game = game

            board_str = game.render_board(flip=False)
            await channel.send(
                f"♟️ **AI vs AI chess!**\n"
                f"{p1_emoji} {p1_name} (White) vs {p2_emoji} {p2_name} (Black)\n"
                f"{board_str}\n"
                f"Use `!chess resign` to stop the game."
            )
            await asyncio.sleep(random.uniform(2, 4))
            await make_ai_chess_move(shared_state)

        # ── Human vs AI ───────────────────────────────────────────────────
        else:
            if arg3 == "white":
                human_color = chess.WHITE
            elif arg3 == "black":
                human_color = chess.BLACK
            else:
                human_color = random.choice([chess.WHITE, chess.BLACK])

            human_display = message.author.display_name
            persona_name = PERSONAS[persona1_key]["name"]
            persona_emoji = PERSONAS[persona1_key]["avatar_emoji"]

            if human_color == chess.WHITE:
                game = ChessGame(
                    white_name=human_display,
                    black_name=persona_name,
                    white_persona_key=None,
                    black_persona_key=persona1_key,
                    human_name=human_display,
                )
            else:
                game = ChessGame(
                    white_name=persona_name,
                    black_name=human_display,
                    white_persona_key=persona1_key,
                    black_persona_key=None,
                    human_name=human_display,
                )
            shared_state.chess_game = game

            color_label = "White ♙" if human_color == chess.WHITE else "Black ♟"
            ai_color_label = "Black ♟" if human_color == chess.WHITE else "White ♙"
            flip = human_color == chess.BLACK
            board_str = game.render_board(flip=flip)

            await channel.send(
                f"♟️ **Chess game started!**\n"
                f"{human_display} ({color_label}) vs "
                f"{persona_emoji} {persona_name} ({ai_color_label})\n"
                f"{board_str}\n"
                f"Moves: `!chess move e4` | `!chess board` | `!chess resign`"
            )

            if game.is_ai_turn:
                await asyncio.sleep(random.uniform(2, 4))
                await make_ai_chess_move(shared_state)

    # ── move ─────────────────────────────────────────────────────────────────
    elif sub == "move":
        game = shared_state.chess_game
        if not game:
            await channel.send("No chess game in progress. Start one with `!chess challenge <persona>`.")
            return

        if game.is_ai_vs_ai:
            await channel.send("This is an AI vs AI game — no human moves!")
            return

        if message.author.display_name != game.human_name:
            await channel.send(f"Only **{game.human_name}** is playing in this game.")
            return

        if not game.is_human_turn:
            await channel.send(f"It's **{game.current_name}**'s turn — please wait.")
            return

        if len(parts) < 3:
            await channel.send("Usage: `!chess move <move>` (e.g. `!chess move e4`)")
            return

        move_str = parts[2]
        success, result = game.try_move(move_str)
        if not success:
            legal = game.get_legal_moves_san()
            await channel.send(
                f"{result}\nLegal moves: {', '.join(legal[:20])}"
                + (" ..." if len(legal) > 20 else "")
            )
            return

        flip = game.white_persona_key is not None  # human plays black → flip
        board_str = game.render_board(flip=flip)
        status = game.status()

        if game.is_over():
            await channel.send(f"You played **{result}**\n{board_str}\n\n🏁 {game.result_description()}")
            shared_state.chess_game = None
            return

        check_notice = " — **Check!** ♟️" if status == "check" else ""
        await channel.send(f"You played **{result}**{check_notice}\n{board_str}")

        await asyncio.sleep(random.uniform(2, 5))
        await make_ai_chess_move(shared_state)

    # ── board ─────────────────────────────────────────────────────────────────
    elif sub == "board":
        game = shared_state.chess_game
        if not game:
            await channel.send("No chess game in progress.")
            return
        flip = (not game.is_ai_vs_ai) and game.white_persona_key is not None
        board_str = game.render_board(flip=flip)
        status = game.status()
        check_notice = " (Check!)" if status == "check" else ""
        await channel.send(
            f"**Current board**{check_notice} — {game.current_turn_label} to move\n{board_str}"
        )

    # ── clear ─────────────────────────────────────────────────────────────────
    elif sub == "clear":
        if shared_state.chess_game:
            await channel.send("Stop the game first before clearing.")
            return
        observer_bot = shared_state.bots["observer"]
        chess_channel = observer_bot.get_channel(CHESS_CHANNEL_ID)
        if chess_channel:
            try:
                deleted = await chess_channel.purge(limit=None)
                logger.info(f"[CMD] !chess clear by {message.author.name} — deleted {len(deleted)} messages")
            except discord.errors.Forbidden:
                await message.channel.send("Missing Permissions — grant the bot **Manage Messages** in this channel.")

    # ── resign / quit ─────────────────────────────────────────────────────────
    elif sub in ("resign", "quit"):
        game = shared_state.chess_game
        if not game:
            await channel.send("No chess game in progress.")
            return
        white_name, black_name = game.white_name, game.black_name
        # Pick any bot to announce (first AI found, or white's if AI vs AI)
        announcer_key = game.white_persona_key or game.black_persona_key
        shared_state.chess_game = None
        if game.is_ai_vs_ai:
            await shared_state.bots[announcer_key].speak(
                f"The game between **{white_name}** and **{black_name}** has been stopped. ♟️",
                CHESS_CHANNEL_ID,
            )
        else:
            winner = black_name if game.white_persona_key is None else white_name
            await shared_state.bots[announcer_key].speak(
                f"**{game.human_name}** has resigned. {winner} wins! ♟️",
                CHESS_CHANNEL_ID,
            )

    # ── status ────────────────────────────────────────────────────────────────
    elif sub == "status":
        game = shared_state.chess_game
        if not game:
            await channel.send("No chess game in progress.")
            return
        status = game.status()
        legal = game.get_legal_moves_san()
        mode = "AI vs AI" if game.is_ai_vs_ai else "Human vs AI"
        await channel.send(
            f"**{game.white_name}** (White) vs **{game.black_name}** (Black) [{mode}]\n"
            f"Move {game.move_count + 1} | Status: {status} | Turn: {game.current_turn_label}\n"
            f"Legal moves ({len(legal)}): {', '.join(legal[:15])}"
            + (" ..." if len(legal) > 15 else "")
        )

    # ── help / unknown ────────────────────────────────────────────────────────
    else:
        persona_names = " | ".join(p["name"].lower() for p in PERSONAS.values())
        await channel.send(
            "**Chess commands:**\n"
            f"`!chess challenge <persona> [white|black]` — Play against an AI (personas: {persona_names})\n"
            f"`!chess challenge <persona1> <persona2>` — Watch two AIs play each other\n"
            "`!chess move <move>` — Make your move (SAN or UCI, e.g. `e4`, `Nf3`, `e2e4`)\n"
            "`!chess board` — Show the current board\n"
            "`!chess status` — Show game info and legal moves\n"
            "`!chess resign` / `!chess quit` — Forfeit or stop the game immediately\n"
            "`!chess clear` — Delete all messages in this channel"
        )


async def orchestrator(shared_state: SharedState):
    """Waits for all bots to connect. Conversations are started manually via !conversation start."""
    logger.info("Orchestrator waiting for all bots to connect...")
    await shared_state.all_ready.wait()
    logger.info("All bots ready.")



async def main():
    shared_state = SharedState()

    # Verify Ollama is up before starting bots
    if not shared_state.ollama.is_available():
        logger.error("Ollama is not running. Start Ollama before launching the bots.")
        return

    available_models = shared_state.ollama.list_models()
    logger.info(f"Available Ollama models: {available_models}")

    # Load tokens and build persona bot instances
    bots_to_run: list[tuple[discord.Client, str]] = []
    for key, persona in PERSONAS.items():
        token_env = persona.get("token_env_var")
        token = os.getenv(token_env) if token_env else None
        if not token:
            logger.error(
                f"Missing token for {persona['name']}. "
                f"Set {token_env} in your .env file."
            )
            return
        bots_to_run.append((PersonaBot(key, shared_state), token))

    # Run all persona bots and the orchestrator concurrently
    async def start_bot(bot: discord.Client, token: str):
        try:
            await bot.start(token)
        except discord.errors.PrivilegedIntentsRequired:
            name = getattr(bot, 'persona', {}).get('name', str(bot))
            logger.error(
                f"{name}: Missing 'Message Content Intent'. "
                f"Enable it at discord.com/developers/applications → {name} → Bot → Privileged Gateway Intents."
            )
        except discord.errors.LoginFailure:
            name = getattr(bot, 'persona', {}).get('name', str(bot))
            logger.error(f"{name}: Invalid token. Check {bot.persona_key.upper()} in your .env file.")

    await asyncio.gather(
        *[start_bot(bot, token) for bot, token in bots_to_run],
        orchestrator(shared_state)
    )


if __name__ == "__main__":
    asyncio.run(main())
