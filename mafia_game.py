"""
Mafia game engine and local AI runner for the Atrium personas.
"""

from __future__ import annotations

import asyncio
import random
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ollama_client import OllamaClient
from personas import PERSONAS

ROLE_MAFIA = "mafia"
ROLE_DETECTIVE = "detective"
ROLE_DOCTOR = "doctor"
ROLE_VILLAGER = "villager"

VOTE_RE = re.compile(r"TARGET\s*:\s*(.+)", re.IGNORECASE)


@dataclass
class MafiaPlayer:
    key: str
    name: str
    model: str
    avatar_emoji: str
    role: str
    alive: bool = True
    elimination_reason: str | None = None
    private_notes: list[str] = field(default_factory=list)
    speeches: list[str] = field(default_factory=list)
    votes: list[str] = field(default_factory=list)
    investigations: list[str] = field(default_factory=list)


class MafiaGame:
    def __init__(self, participant_keys: list[str], seed: int | None = None):
        unique_keys = []
        for key in participant_keys:
            if key not in PERSONAS or key == "facilitator" or key in unique_keys:
                continue
            unique_keys.append(key)

        if len(unique_keys) < 4:
            raise ValueError("Mafia requires at least 4 participants.")

        self.random = random.Random(seed)
        self.started_at = datetime.now()
        self.seed = seed
        self.round_number = 0
        self.public_log: list[dict[str, Any]] = []
        self.night_log: list[dict[str, Any]] = []
        self.day_log: list[dict[str, Any]] = []
        self.winner: str | None = None
        self.status_message = "Ready to begin."

        shuffled = unique_keys[:]
        self.random.shuffle(shuffled)
        roles = self._build_roles(len(shuffled))
        self.random.shuffle(roles)

        self.players: dict[str, MafiaPlayer] = {}
        for key, role in zip(shuffled, roles):
            persona = PERSONAS[key]
            self.players[key] = MafiaPlayer(
                key=key,
                name=persona["name"],
                model=persona["model"],
                avatar_emoji=persona["avatar_emoji"],
                role=role,
            )

        self._log_public(
            "system",
            f"Game created with {len(self.players)} agents. Night 1 is ready.",
        )

    def _build_roles(self, count: int) -> list[str]:
        mafia_count = 1 if count <= 5 else 2
        roles = [ROLE_MAFIA] * mafia_count
        roles.append(ROLE_DETECTIVE)
        roles.append(ROLE_DOCTOR)
        roles.extend([ROLE_VILLAGER] * (count - len(roles)))
        return roles

    @property
    def alive_players(self) -> list[MafiaPlayer]:
        return [player for player in self.players.values() if player.alive]

    @property
    def mafia_alive(self) -> list[MafiaPlayer]:
        return [player for player in self.alive_players if player.role == ROLE_MAFIA]

    @property
    def town_alive(self) -> list[MafiaPlayer]:
        return [player for player in self.alive_players if player.role != ROLE_MAFIA]

    def is_over(self) -> bool:
        return self.winner is not None

    def evaluate_winner(self) -> str | None:
        mafia_count = len(self.mafia_alive)
        town_count = len(self.town_alive)
        if mafia_count == 0:
            self.winner = "Town"
        elif mafia_count >= town_count:
            self.winner = "Mafia"
        return self.winner

    def living_targets_for(self, player_key: str, include_self: bool = False) -> list[MafiaPlayer]:
        players = self.alive_players
        if include_self:
            return players
        return [player for player in players if player.key != player_key]

    def kill_player(self, key: str, reason: str):
        player = self.players[key]
        player.alive = False
        player.elimination_reason = reason

    def _log_public(self, phase: str, message: str):
        self.public_log.append(
            {
                "round": self.round_number,
                "phase": phase,
                "message": message,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            }
        )

    def record_speech(self, player: MafiaPlayer, speech: str):
        player.speeches.append(speech)
        self.day_log.append(
            {"round": self.round_number, "speaker": player.name, "message": speech}
        )
        self._log_public("day", f"{player.name}: {speech}")

    def snapshot(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "seed": self.seed,
            "round_number": self.round_number,
            "winner": self.winner,
            "status_message": self.status_message,
            "alive_count": len(self.alive_players),
            "players": [
                {
                    "key": player.key,
                    "name": player.name,
                    "model": player.model,
                    "emoji": player.avatar_emoji,
                    "role": player.role,
                    "alive": player.alive,
                    "elimination_reason": player.elimination_reason,
                    "private_notes": player.private_notes[-4:],
                    "investigations": player.investigations[-4:],
                    "recent_speech": player.speeches[-1] if player.speeches else None,
                    "recent_vote": player.votes[-1] if player.votes else None,
                }
                for player in sorted(self.players.values(), key=lambda item: item.name.lower())
            ],
            "public_log": self.public_log[-40:],
            "night_log": self.night_log[-20:],
            "day_log": self.day_log[-20:],
        }


class MafiaGameRunner:
    def __init__(self, ollama: OllamaClient | None = None):
        self.ollama = ollama or OllamaClient()
        self._ollama_ready = True

    def play_round(self, game: MafiaGame) -> dict[str, Any]:
        if game.is_over():
            return game.snapshot()

        self._ollama_ready = self.ollama.is_available()
        game.round_number += 1
        game.status_message = f"Playing round {game.round_number}."
        game._log_public("system", f"Round {game.round_number} begins.")

        night_summary = self._run_night(game)
        if not game.is_over():
            self._run_day(game)

        winner = game.evaluate_winner()
        if winner:
            game.status_message = f"{winner} wins after round {game.round_number}."
            game._log_public("system", game.status_message)
        else:
            game.status_message = f"Round {game.round_number} complete."
            game._log_public("system", game.status_message)

        return {"night_summary": night_summary, "state": game.snapshot()}

    def _run_night(self, game: MafiaGame) -> dict[str, Any]:
        mafia = game.mafia_alive
        doctor = next((player for player in game.alive_players if player.role == ROLE_DOCTOR), None)
        detective = next((player for player in game.alive_players if player.role == ROLE_DETECTIVE), None)

        mafia_votes: list[str] = []
        for player in mafia:
            targets = [candidate for candidate in game.alive_players if candidate.role != ROLE_MAFIA]
            target = self._choose_target(game, player, targets, "mafia_kill")
            mafia_votes.append(target.key)
            player.private_notes.append(f"Night {game.round_number}: targeted {target.name}.")

        save_target = None
        if doctor:
            save_target = self._choose_target(game, doctor, game.alive_players, "doctor_save")
            doctor.private_notes.append(f"Night {game.round_number}: attempted to save {save_target.name}.")

        investigation = None
        if detective:
            investigate_target = self._choose_target(
                game,
                detective,
                game.living_targets_for(detective.key),
                "detective_investigate",
            )
            investigation = {"target": investigate_target.name, "role": investigate_target.role}
            result = (
                f"Night {game.round_number}: {investigate_target.name} is "
                f"{'Mafia' if investigate_target.role == ROLE_MAFIA else 'Not Mafia'}."
            )
            detective.investigations.append(result)
            detective.private_notes.append(result)

        eliminated = None
        if mafia_votes:
            target_key = self._plurality_choice(game, mafia_votes)
            target = game.players[target_key]
            if save_target and save_target.key == target.key:
                game._log_public("night", "Someone was targeted in the night, but they survived.")
                game.night_log.append(
                    {
                        "round": game.round_number,
                        "targeted": target.name,
                        "saved_by_doctor": True,
                        "doctor_target": save_target.name,
                        "mafia_votes": [game.players[key].name for key in mafia_votes],
                    }
                )
            else:
                game.kill_player(target.key, "Killed during the night")
                eliminated = target.name
                game._log_public("night", f"Dawn breaks. {target.name} was found dead.")
                game.night_log.append(
                    {
                        "round": game.round_number,
                        "targeted": target.name,
                        "saved_by_doctor": False,
                        "doctor_target": save_target.name if save_target else None,
                        "mafia_votes": [game.players[key].name for key in mafia_votes],
                    }
                )

        winner = game.evaluate_winner()
        if winner:
            return {"eliminated": eliminated, "investigation": investigation, "winner": winner}
        return {"eliminated": eliminated, "investigation": investigation}

    def _run_day(self, game: MafiaGame):
        speakers = game.alive_players[:]
        game.random.shuffle(speakers)
        for player in speakers:
            speech = self._generate_speech(game, player)
            game.record_speech(player, speech)

        votes: dict[str, str] = {}
        for player in game.alive_players:
            target = self._choose_target(
                game,
                player,
                game.living_targets_for(player.key),
                "day_vote",
            )
            votes[player.key] = target.key
            player.votes.append(target.name)

        counts = Counter(votes.values()).most_common()
        if not counts:
            game._log_public("day", "No one voted.")
            return

        top_votes = counts[0][1]
        tied = [key for key, count in counts if count == top_votes]
        if len(tied) > 1:
            names = ", ".join(game.players[key].name for key in tied)
            game._log_public("day", f"The town is deadlocked between {names}. No one is eliminated.")
            game.day_log.append(
                {
                    "round": game.round_number,
                    "votes": {
                        game.players[player_key].name: game.players[target_key].name
                        for player_key, target_key in votes.items()
                    },
                    "result": "tie",
                }
            )
            return

        eliminated_key = counts[0][0]
        eliminated = game.players[eliminated_key]
        game.kill_player(eliminated_key, "Eliminated by town vote")
        reveal = "Mafia" if eliminated.role == ROLE_MAFIA else eliminated.role.title()
        game._log_public("day", f"The town eliminates {eliminated.name}. Role revealed: {reveal}.")
        game.day_log.append(
            {
                "round": game.round_number,
                "votes": {
                    game.players[player_key].name: game.players[target_key].name
                    for player_key, target_key in votes.items()
                },
                "result": eliminated.name,
                "revealed_role": eliminated.role,
            }
        )
        game.evaluate_winner()

    def _generate_speech(self, game: MafiaGame, player: MafiaPlayer) -> str:
        living = ", ".join(candidate.name for candidate in game.alive_players)
        notes = "\n".join(player.private_notes[-4:]) or "No private notes yet."
        public_history = "\n".join(
            f"- {item['message']}"
            for item in game.public_log[-8:]
            if item["phase"] in {"night", "day", "system"}
        ) or "- No public history yet."
        other_mafia = [candidate.name for candidate in game.mafia_alive if candidate.key != player.key]
        role_brief = {
            ROLE_MAFIA: (
                "You are Mafia. Blend in, misdirect suspicion, and protect your partners."
                + (f" Other living mafia: {', '.join(other_mafia)}." if other_mafia else "")
            ),
            ROLE_DETECTIVE: "You are the Detective. Use your investigation results carefully.",
            ROLE_DOCTOR: "You are the Doctor. Protect the town without making yourself obvious.",
            ROLE_VILLAGER: "You are a Villager. Read the room and push a credible suspicion.",
        }[player.role]

        prompt = (
            f"You are {player.name} playing Mafia.\n"
            f"{role_brief}\n"
            f"Round: {game.round_number}\n"
            f"Living players: {living}\n"
            f"Your private notes:\n{notes}\n"
            f"Public history:\n{public_history}\n\n"
            f"Speak to the group in 1-3 sentences. Be specific, suspicious, and in-character. "
            f"Do not reveal your exact role unless it is strategically necessary."
        )
        speech = self._ask_model(player.model, prompt, max_tokens=120)
        return self._clean_text(speech) or self._fallback_speech(game, player)

    def _choose_target(self, game: MafiaGame, player: MafiaPlayer, targets: list[MafiaPlayer], prompt_kind: str) -> MafiaPlayer:
        if len(targets) == 1:
            return targets[0]
        prompt = self._target_prompt(game, player, targets, prompt_kind)
        response = self._ask_model(player.model, prompt, max_tokens=80)
        parsed = self._parse_target(response, targets)
        return parsed or self._fallback_target(game, targets, prompt_kind)

    def _target_prompt(self, game: MafiaGame, player: MafiaPlayer, targets: list[MafiaPlayer], prompt_kind: str) -> str:
        target_names = ", ".join(target.name for target in targets)
        notes = "\n".join(player.private_notes[-5:]) or "No private notes yet."
        recent_day = "\n".join(
            f"- {item['speaker']}: {item['message']}"
            for item in game.day_log[-6:]
            if "speaker" in item
        ) or "- No speeches yet."
        instructions = {
            "mafia_kill": "Choose one living non-mafia target to kill tonight. Prioritize dangerous or persuasive town players.",
            "doctor_save": "Choose one living player to protect tonight, including yourself if needed.",
            "detective_investigate": "Choose one living player other than yourself to investigate tonight.",
            "day_vote": "Choose one living player other than yourself to eliminate today. Base it on the speeches and your private information.",
        }[prompt_kind]
        role_context = f"Your role is {player.role}. Investigation results: {'; '.join(player.investigations[-3:]) or 'none'}."
        return (
            f"You are {player.name} playing Mafia.\n"
            f"{role_context}\n"
            f"Round: {game.round_number}\n"
            f"Available targets: {target_names}\n"
            f"Recent speeches:\n{recent_day}\n"
            f"Your notes:\n{notes}\n\n"
            f"{instructions}\n"
            f"Reply in exactly this format:\n"
            f"TARGET: <one exact name from the list>"
        )

    def _parse_target(self, response: str, targets: list[MafiaPlayer]) -> MafiaPlayer | None:
        if not response:
            return None
        match = VOTE_RE.search(response)
        if not match:
            return None
        wanted = match.group(1).strip().lower()
        for target in targets:
            if target.name.lower() == wanted:
                return target
        return None

    def _plurality_choice(self, game: MafiaGame, votes: list[str]) -> str:
        counts = Counter(votes)
        top = counts.most_common()
        top_votes = top[0][1]
        finalists = [key for key, count in top if count == top_votes]
        return game.random.choice(finalists)

    def _fallback_target(self, game: MafiaGame, targets: list[MafiaPlayer], prompt_kind: str) -> MafiaPlayer:
        if prompt_kind == "mafia_kill":
            priority = [target for target in targets if target.role in {ROLE_DETECTIVE, ROLE_DOCTOR}]
            if priority:
                return game.random.choice(priority)
        return game.random.choice(targets)

    def _fallback_speech(self, game: MafiaGame, player: MafiaPlayer) -> str:
        suspects = [candidate.name for candidate in game.living_targets_for(player.key)]
        if not suspects:
            return "We are running out of options."
        target = game.random.choice(suspects)
        templates = {
            ROLE_MAFIA: f"{target} feels too comfortable right now. I want to hear more from them before we trust this table.",
            ROLE_DETECTIVE: f"I keep circling back to {target}. Their tone feels managed, not honest.",
            ROLE_DOCTOR: f"We need to slow down and look at who benefits from chaos. {target} is where my suspicion lands today.",
            ROLE_VILLAGER: f"{target} is the read I can't shake. Their story still doesn't sit right with me.",
        }
        return templates[player.role]

    def _ask_model(self, model: str, prompt: str, max_tokens: int = 120) -> str:
        if not self._ollama_ready:
            return ""
        messages = [
            {
                "role": "system",
                "content": (
                    "You are playing a structured game of Mafia. Stay concise, follow the requested format exactly, "
                    "and avoid narration outside your own voice."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        try:
            return asyncio.run(
                self.ollama.chat_response(
                    model=model,
                    messages=messages,
                    temperature=0.85,
                    max_tokens=max_tokens,
                )
            )
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(
                    self.ollama.chat_response(
                        model=model,
                        messages=messages,
                        temperature=0.85,
                        max_tokens=max_tokens,
                    )
                )
            finally:
                loop.close()
        except Exception:
            return ""

    def _clean_text(self, text: str) -> str:
        if not text:
            return ""
        cleaned = text.strip().replace("\r\n", "\n")
        cleaned = re.sub(r"\n{2,}", "\n", cleaned)
        return cleaned[:500].strip()


def available_mafia_personas() -> list[dict[str, str]]:
    return [
        {
            "key": key,
            "name": persona["name"],
            "model": persona["model"],
            "emoji": persona["avatar_emoji"],
        }
        for key, persona in PERSONAS.items()
        if key != "facilitator"
    ]
