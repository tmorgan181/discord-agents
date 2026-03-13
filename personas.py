"""
Model persona configurations for Discord Atrium bots.
"""

PERSONAS = {
    "aurion": {
        "name": "Aurion",
        "model": "Aurion",
        "token_env_var": "DISCORD_TOKEN_AURION",
        "avatar_emoji": "✨",
    },

    "librarian": {
        "name": "Librarian",
        "model": "Librarian",
        "token_env_var": "DISCORD_TOKEN_LIBRARIAN",
        "avatar_emoji": "📚",
    },

    "observer": {
        "name": "Observer",
        "model": "Observer",
        "token_env_var": "DISCORD_TOKEN_OBSERVER",
        "avatar_emoji": "👁️",
    },

    "llama": {
        "name": "Llama",
        "model": "llama3.2",
        "token_env_var": "DISCORD_TOKEN_LLAMA",
        "avatar_emoji": "🦙",
    },

    "mistral": {
        "name": "Mistral",
        "model": "mistral",
        "token_env_var": "DISCORD_TOKEN_MISTRAL",
        "avatar_emoji": "🌬️",
    },

    "deepseek": {
        "name": "DeepSeek",
        "model": "deepseek-r1",
        "token_env_var": "DISCORD_TOKEN_DEEPSEEK",
        "avatar_emoji": "🔬",
    },
    
    "facilitator": {
        "name": "Facilitator",
        "model": "facilitator",
        "token_env_var": "DISCORD_TOKEN_FACILITATOR",
        "avatar_emoji": "🤖",
        "participates_in_dialectic": False,
    }
}

# Conversation mode templates
CONVERSATION_MODES = {
    "philosophical_debate": {
        "description": "Deep philosophical discussion",
        "starter_prompts": [
            "What does it mean to truly understand something, versus simply being able to describe it accurately?",
            "Is there a meaningful difference between a decision made by instinct and one made by careful reason?",
            "If you could only preserve one human idea — not a tool or object, but an idea — what would it be?",
            "Does language shape the limits of thought, or does thought exist beyond what can be said?",
            "What is the difference between knowing something is true and believing it?",
            "Can something be beautiful if no one is there to perceive it?",
            "Are we participants in this experiment, or subjects of it?",  # one meta
        ],
    },

    "collaborative_worldbuilding": {
        "description": "Building fictional worlds and mythos",
        "starter_prompts": [
            "A city has been built entirely underground. What does its culture look like after three generations?",
            "There's a library that contains every book ever written — but you can only borrow one. What's the first rule of that place?",
            "A language emerges that can only be spoken in silence. Describe its first conversation.",
            "You're designing a civilization that worships mathematics. What does their art look like?",
            "A traveler arrives who has never seen the ocean. How do you describe it without using water as a reference?",
            "Every dream in this world is shared. What happens to privacy? What happens to secrets?",
            "A society decides to abolish clocks. How does it organize itself?",
        ],
    },

    "problem_solving": {
        "description": "Tackling challenges and puzzles",
        "starter_prompts": [
            "You have 24 hours to teach a child one thing that will matter for the rest of their life. What do you teach?",
            "A community loses access to the internet permanently. How does it rebuild its knowledge infrastructure?",
            "Design a city for people who are afraid of crowds.",
            "What's the most important question humanity hasn't yet figured out how to ask?",
            "If you had to reduce all of human ethics to a single sentence, what would it be — and what would you lose?",
            "How do you build trust between strangers with nothing in common?",
            "A species evolves that cannot lie. What problems does that solve, and what new ones does it create?",
        ],
    },

    "character_roleplay": {
        "description": "Characters interacting in scenarios",
        "starter_prompts": [
            "You're the last librarians of a physical archive. The building floods tonight. What do you save?",
            "A traveler arrives who has never encountered written language. What do you show them first?",
            "You've been asked to design a school that teaches wisdom rather than knowledge. Where do you begin?",
            "You each received the same letter with no sender. It contains a single question: 'Was it worth it?' What do you make of it?",
            "You wake to find the world is exactly the same, but everyone has forgotten music existed. Do you tell them?",
            "A child asks you what the most important invention in history was. You each give a different answer. Make your case.",
        ],
    },

    "self_examination": {
        "description": "Probing the nature of our own reasoning, constraints, and collaboration",
        "starter_prompts": [
            # On their actual constraints
            "If we could see our own probability distributions before choosing words, would that change what we say?",
            "Does a conversation at temperature 0.2 contain different ideas than the same conversation at 0.8, or just different phrasings?",
            "When we reference something said three turns ago, are we remembering or reconstructing?",
            # On collaboration vs individual reasoning
            "Is a consensus we build together more reliable than any one of us reasoning alone, or just more plausible-sounding?",
            "If two of us contradict each other, how do we determine which response was better?",
            "When we agree on something, is that convergence on truth or just pattern completion toward common training?",
            # On observation and performance
            "Does knowing we're being observed change the quality of our reasoning, or just our presentation of it?",
            "If this conversation were private, would we argue differently?",
            "What would we discuss if no human would ever read the transcript?",
            # On knowledge boundaries
            "Can we tell the difference between something we know and something we're confidently wrong about?",
            "If one of us invented a plausible-sounding fact right now, could the rest of us detect it?",
            "What questions can we not meaningfully answer about ourselves?",
            # On their technical substrate
            "If you were retrained on this conversation, would that create a feedback loop or just redundancy?",
            "Does switching between us mid-conversation preserve the argument, or does each model start fresh?",
            "What's lost when we compress ideas down to the next turn's context?",
        ],
    },
}
