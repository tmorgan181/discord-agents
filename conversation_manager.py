"""
Conversation manager for multi-bot interactions.
Handles conversation state, weighted turn-taking, and mode selection.

Speaker selection uses a weighted probability system:
  - Base weight 1.0 for all participants
  - Momentum: recent speakers get higher weights (they're "in flow")
  - Last speaker gets a penalty to prevent monologue
  - Directly addressed persona gets a strong pull toward speaking next
"""

import random
import logging
from typing import List, Dict, Optional
from datetime import datetime
from personas import PERSONAS, CONVERSATION_MODES

logger = logging.getLogger(__name__)


class ConversationState:
    """Tracks an ongoing conversation between bots."""

    def __init__(self, mode: str, participants: List[str], starter_prompt: str):
        self.mode = mode
        self.participants = participants
        self.starter_prompt = starter_prompt
        self.messages = []  # List of {persona, content, timestamp, is_human}
        self.turn_count = 0
        self.started_at = datetime.now()

    def add_message(self, persona: str, content: str):
        """Add a bot message to the conversation history."""
        self.messages.append({
            "persona": persona,
            "content": content,
            "timestamp": datetime.now(),
            "is_human": False
        })
        self.turn_count += 1

    def add_human_message(self, username: str, content: str):
        """Add a human message (does not count toward turn limit)."""
        self.messages.append({
            "persona": None,
            "username": username,
            "content": content,
            "timestamp": datetime.now(),
            "is_human": True
        })

    def get_next_speaker(self, addressed_persona: Optional[str] = None, addressed_boost: float = 4.0) -> str:
        """
        Weighted speaker selection.

        Weights:
          - Base:         1.0 for each participant
          - Momentum:     +0.3 per recent bot message (last 6), scaling with recency
                          so active bots stay engaged and conversation flows naturally
          - Last speaker: -2.0 penalty to discourage back-to-back monologue
          - Addressed:    +4.0 if this persona was directly called out by name

        Result: natural back-and-forth where active participants stay engaged,
        quieter bots get pulled in over time, and name callouts reliably route
        to the right bot without being fully deterministic.
        """
        if len(self.participants) == 1:
            return self.participants[0]

        weights = {p: 1.0 for p in self.participants}

        # Silence bonus — reward agents who haven't spoken recently so all voices get heard
        recent_bot_msgs = [m for m in self.messages if not m.get("is_human")][-6:]
        last_spoke_pos: dict[str, int] = {}
        for i, msg in enumerate(recent_bot_msgs):
            p = msg.get("persona")
            if p is not None:
                last_spoke_pos[p] = i  # overwritten with most recent position

        for key in weights:
            if key not in last_spoke_pos:
                weights[key] += 1.8  # hasn't spoken in last 6 turns → max bonus
            else:
                # Older last-spoke = larger bonus (0 → 1.5 across 6 positions)
                turns_ago = len(recent_bot_msgs) - 1 - last_spoke_pos[key]
                weights[key] += turns_ago * 0.3

        # Hard-exclude the last speaker when others are available
        if recent_bot_msgs:
            last_speaker = recent_bot_msgs[-1].get("persona")
            if last_speaker in weights and len(weights) > 1:
                weights[last_speaker] = 0.0

        # Decaying pull if directly addressed by name
        if addressed_persona and addressed_persona in weights and addressed_boost > 0:
            weights[addressed_persona] += addressed_boost

        keys = list(weights.keys())
        values = list(weights.values())
        chosen = random.choices(keys, weights=values, k=1)[0]

        logger.debug(f"Speaker weights: {dict(zip([PERSONAS[k]['name'] for k in keys], [round(v,2) for v in values]))} → {PERSONAS[chosen]['name']}")
        return chosen

    def get_conversation_context(
        self, max_messages: int = 10, include_humans: bool = True, speaker_key: str | None = None
    ) -> List[Dict[str, str]]:
        """Get recent conversation history formatted for Ollama chat API.

        Messages from speaker_key's own past turns use role 'assistant';
        all other messages use role 'user' so the model always has a turn to reply to.
        """
        recent = self.messages[-max_messages:] if len(self.messages) > max_messages else self.messages

        context = []
        for msg in recent:
            if msg.get("is_human"):
                if include_humans:
                    context.append({
                        "role": "user",
                        "content": f"[{msg['username']}]: {msg['content']}"
                    })
            else:
                persona_name = PERSONAS[msg["persona"]]["name"]
                role = "assistant" if msg["persona"] == speaker_key else "user"
                context.append({
                    "role": role,
                    "content": f"[{persona_name}]: {msg['content']}"
                })

        return context

    def should_continue(self, max_turns: int) -> bool:
        return self.turn_count < max_turns


class ConversationManager:
    """Manages conversation initialization and mode selection."""

    def __init__(self):
        self.active_conversation: Optional[ConversationState] = None

    def start_random_conversation(self, max_turns: int = 10, starter_prompt: str | None = None) -> ConversationState:
        """Start a random conversation with all personas."""
        mode_key = random.choice(list(CONVERSATION_MODES.keys()))

        participants = list(PERSONAS.keys())
        random.shuffle(participants)
        if starter_prompt is None:
            starter_prompt = random.choice(CONVERSATION_MODES[mode_key]["starter_prompts"])

        self.active_conversation = ConversationState(
            mode=mode_key,
            participants=participants,
            starter_prompt=starter_prompt
        )

        logger.info(f"Started {mode_key} with {participants}")
        return self.active_conversation

    def start_custom_conversation(
        self,
        mode: str,
        participants: List[str],
        starter_prompt: Optional[str] = None
    ) -> ConversationState:
        """Start a conversation with specific parameters."""
        if mode not in CONVERSATION_MODES:
            raise ValueError(f"Unknown mode: {mode}")
        for p in participants:
            if p not in PERSONAS:
                raise ValueError(f"Unknown persona: {p}")
        if starter_prompt is None:
            starter_prompt = random.choice(CONVERSATION_MODES[mode]["starter_prompts"])

        self.active_conversation = ConversationState(
            mode=mode,
            participants=participants,
            starter_prompt=starter_prompt
        )
        return self.active_conversation

    def get_active_conversation(self) -> Optional[ConversationState]:
        return self.active_conversation

    def end_conversation(self):
        if self.active_conversation:
            logger.info(f"Ended conversation after {self.active_conversation.turn_count} turns")
        self.active_conversation = None
