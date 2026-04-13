from __future__ import annotations

import os
import threading

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from mafia_game import MafiaGame, MafiaGameRunner, available_mafia_personas
from ollama_client import OllamaClient

load_dotenv()

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

_lock = threading.Lock()
_ollama = OllamaClient(os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"))
_runner = MafiaGameRunner(_ollama)
_state: dict[str, MafiaGame | None] = {"game": None}


def _current_game() -> MafiaGame | None:
    return _state["game"]


def _response_state():
    game = _current_game()
    return {
        "available_personas": available_mafia_personas(),
        "ollama_available": _ollama.is_available(),
        "game": game.snapshot() if game else None,
    }


@app.get("/")
def index():
    return render_template("mafia.html")


@app.get("/api/personas")
def personas():
    return jsonify(
        {
            "personas": available_mafia_personas(),
            "ollama_available": _ollama.is_available(),
        }
    )


@app.get("/api/state")
def state():
    return jsonify(_response_state())


@app.post("/api/game")
def create_game():
    payload = request.get_json(silent=True) or {}
    participants = payload.get("participants") or []
    seed = payload.get("seed")

    try:
        with _lock:
            game = MafiaGame(participants, seed=seed)
            _state["game"] = game
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(_response_state())


@app.post("/api/game/advance")
def advance_game():
    with _lock:
        game = _current_game()
        if not game:
            return jsonify({"error": "No game in progress."}), 400
        result = _runner.play_round(game)
        return jsonify({"result": result, **_response_state()})


@app.post("/api/game/reset")
def reset_game():
    with _lock:
        _state["game"] = None
    return jsonify(_response_state())


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8080, debug=True)
