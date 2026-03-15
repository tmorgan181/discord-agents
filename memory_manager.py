"""
Persistent memory system for Discord Atrium bots.

Each agent gets a JSON memory file in memories/{AgentName}.json.
After each conversation, their model summarizes it from their perspective.
Those summaries are injected into future system prompts automatically.
"""

import json
import os
import re
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

MEMORIES_DIR = os.path.join(os.path.dirname(__file__), "memories")


def _memory_path(agent_name: str) -> str:
    os.makedirs(MEMORIES_DIR, exist_ok=True)
    return os.path.join(MEMORIES_DIR, f"{agent_name}.json")


def load_memory(agent_name: str) -> dict:
    """Load an agent's memory file, returning a default structure if absent."""
    path = _memory_path(agent_name)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"[MEMORY] Failed to load memory for {agent_name}: {e}")
    return {"agent_name": agent_name, "conversations": [], "relationships": {}}


def save_memory(agent_name: str, data: dict) -> None:
    """Persist an agent's memory to disk."""
    path = _memory_path(agent_name)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info(f"[MEMORY] Saved memory for {agent_name} ({len(data['conversations'])} conversations)")
    except Exception as e:
        logger.error(f"[MEMORY] Failed to save memory for {agent_name}: {e}")


def _build_transcript(messages: list, personas: dict) -> str:
    """Convert conversation messages to a readable transcript string."""
    lines = []
    for msg in messages:
        if msg.get("is_human"):
            lines.append(f"{msg['username']}: {msg['content']}")
        else:
            persona_name = personas[msg["persona"]]["name"]
            lines.append(f"{persona_name}: {msg['content']}")
    return "\n".join(lines)


def _extract_participants(messages: list, personas: dict) -> list[str]:
    """Return unique agent display names that spoke in the conversation."""
    seen: set[str] = set()
    result: list[str] = []
    for msg in messages:
        if not msg.get("is_human") and msg.get("persona") in personas:
            name = personas[msg["persona"]]["name"]
            if name not in seen:
                seen.add(name)
                result.append(name)
    return result


def _count_direct_interactions(agent_name: str, messages: list, personas: dict) -> dict[str, int]:
    """
    Count how many times each other agent directly addressed `agent_name`,
    or was directly addressed by `agent_name`, using the same name-detection
    heuristics as detect_addressed_persona in bot.py.
    """
    counts: dict[str, int] = {}
    name_lower = agent_name.lower()

    for msg in messages:
        if msg.get("is_human"):
            continue
        speaker_name = personas[msg["persona"]]["name"]
        content_lower = msg["content"].lower()

        if speaker_name == agent_name:
            # Our agent spoke — check if they addressed someone
            for key, p in personas.items():
                other = p["name"]
                if other == agent_name:
                    continue
                o = other.lower()
                if (
                    content_lower.startswith(f"{o},")
                    or content_lower.startswith(f"{o}.")
                    or content_lower.startswith(f"{o} —")
                    or content_lower.startswith(f"{o} -")
                    or content_lower.startswith(f"@{o}")
                    or f"\n{o}," in content_lower
                ):
                    counts[other] = counts.get(other, 0) + 1
        else:
            # Someone else spoke — check if they addressed our agent
            if (
                content_lower.startswith(f"{name_lower},")
                or content_lower.startswith(f"{name_lower}.")
                or content_lower.startswith(f"{name_lower} —")
                or content_lower.startswith(f"{name_lower} -")
                or content_lower.startswith(f"@{name_lower}")
                or f"\n{name_lower}," in content_lower
            ):
                counts[speaker_name] = counts.get(speaker_name, 0) + 1

    return counts


def _update_relationships(memory: dict, agent_name: str, messages: list, personas: dict) -> None:
    """Increment relationship interaction counts after a conversation."""
    interactions = _count_direct_interactions(agent_name, messages, personas)
    for other_name, count in interactions.items():
        existing = memory["relationships"].get(other_name)
        if existing is None:
            memory["relationships"][other_name] = f"Directly exchanged {count} time(s) across conversations."
        else:
            # Try to increment an existing count embedded in the note
            m = re.search(r"(\d+) time\(s\)", existing)
            if m:
                new_count = int(m.group(1)) + count
                memory["relationships"][other_name] = re.sub(
                    r"\d+ time\(s\)", f"{new_count} time(s)", existing
                )
            else:
                memory["relationships"][other_name] = existing + f" (+{count} recent exchanges)"


async def summarize_conversation(
    agent_name: str,
    model: str,
    conv_messages: list,
    conv_topic: str,
    personas: dict,
    ollama_client,
) -> Optional[str]:
    """
    Ask the agent's own model to summarize the conversation from its perspective.
    Returns the summary string, or None on failure.
    """
    transcript = _build_transcript(conv_messages, personas)
    if not transcript.strip():
        return None

    prompt = (
        f"You are {agent_name}. You just participated in this conversation:\n\n"
        f"{transcript}\n\n"
        "Summarize this conversation from your perspective. What did you contribute? "
        "What patterns did you notice? Who did you interact with most? Keep it under 100 words."
    )

    try:
        summary = await ollama_client.chat_response(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=160,
            stream=False,
        )
        summary = summary.strip()
        logger.info(f"[MEMORY] Summary for {agent_name}: {summary[:80]!r}...")
        return summary if summary else None
    except Exception as e:
        logger.error(f"[MEMORY] Summarization failed for {agent_name}: {e}")
        return None


async def store_conversation_for_agent(
    agent_key: str,
    agent_name: str,
    model: str,
    conv_state,
    personas: dict,
    ollama_client,
) -> None:
    """
    After a conversation ends, generate + store a memory entry for one agent.
    Runs independently per-agent so slow models don't block others.
    """
    messages = conv_state.messages
    participants = _extract_participants(messages, personas)

    # Only store if this agent actually spoke
    agent_spoke = any(
        not m.get("is_human") and m.get("persona") == agent_key
        for m in messages
    )
    if not agent_spoke:
        logger.debug(f"[MEMORY] {agent_name} did not speak — skipping memory")
        return

    summary = await summarize_conversation(
        agent_name=agent_name,
        model=model,
        conv_messages=messages,
        conv_topic=conv_state.starter_prompt,
        personas=personas,
        ollama_client=ollama_client,
    )

    memory = load_memory(agent_name)

    turns_spoken = sum(
        1 for m in messages
        if not m.get("is_human") and m.get("persona") == agent_key
    )

    entry = {
        "timestamp": conv_state.started_at.isoformat(),
        "topic": conv_state.starter_prompt[:120],
        "summary": summary or "(summarization unavailable)",
        "participants": participants,
        "turns_spoken": turns_spoken,
        "key_points": [],  # reserved for future enrichment
    }
    memory["conversations"].append(entry)

    # Keep only the last 20 conversation entries
    memory["conversations"] = memory["conversations"][-20:]

    _update_relationships(memory, agent_name, messages, personas)
    save_memory(agent_name, memory)


def inject_memories(agent_name: str, base_prompt: str) -> str:
    """
    Load the agent's memory and append recent conversation summaries and
    relationship notes to their system prompt.
    """
    memory = load_memory(agent_name)

    parts: list[str] = []

    # Last 5 conversation summaries
    recent = [
        c for c in memory.get("conversations", [])[-5:]
        if c.get("summary") and c["summary"] != "(summarization unavailable)"
    ]
    if recent:
        summary_lines = "\n".join(f"- {c['summary']}" for c in recent)
        parts.append(f"Your recent conversation memories:\n{summary_lines}")

    # Relationship notes
    relationships = memory.get("relationships", {})
    if relationships:
        rel_lines = "\n".join(f"- {name}: {note}" for name, note in relationships.items())
        parts.append(f"Your relationships with other agents:\n{rel_lines}")

    if not parts:
        return base_prompt

    memory_block = "\n\n".join(parts)
    return base_prompt + f"\n\n{memory_block}"
