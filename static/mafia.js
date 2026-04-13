const state = {
  game: null,
  personas: [],
};

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "Request failed");
  }
  return data;
}

function selectedParticipants() {
  return [...document.querySelectorAll('input[name="participant"]:checked')].map((input) => input.value);
}

function renderPersonas(personas) {
  const grid = document.getElementById("persona-grid");
  grid.innerHTML = "";
  personas.forEach((persona) => {
    const card = document.createElement("label");
    card.className = "persona-card";
    card.innerHTML = `
      <input type="checkbox" name="participant" value="${persona.key}" checked>
      <span class="persona-card__emoji">${persona.emoji}</span>
      <span class="persona-card__name">${persona.name}</span>
      <span class="persona-card__model">${persona.model}</span>
    `;
    grid.appendChild(card);
  });
}

function renderRoster(game) {
  const list = document.getElementById("roster-list");
  list.innerHTML = "";

  if (!game) {
    list.innerHTML = `<p class="empty">Start a game to see the roster.</p>`;
    return;
  }

  game.players.forEach((player) => {
    const card = document.createElement("article");
    card.className = `player-card ${player.alive ? "" : "is-dead"}`;
    card.innerHTML = `
      <header>
        <div>
          <h3>${player.emoji} ${player.name}</h3>
          <p>${player.model}</p>
        </div>
        <span class="role role--${player.role}">${player.role}</span>
      </header>
      <dl>
        <div>
          <dt>Status</dt>
          <dd>${player.alive ? "Alive" : player.elimination_reason || "Dead"}</dd>
        </div>
        <div>
          <dt>Recent Vote</dt>
          <dd>${player.recent_vote || "—"}</dd>
        </div>
        <div>
          <dt>Recent Speech</dt>
          <dd>${player.recent_speech || "—"}</dd>
        </div>
      </dl>
    `;
    list.appendChild(card);
  });
}

function renderTimeline(game) {
  const list = document.getElementById("timeline-list");
  list.innerHTML = "";

  if (!game) {
    list.innerHTML = `<p class="empty">No events yet.</p>`;
    return;
  }

  if (!game.public_log.length) {
    list.innerHTML = `<p class="empty">No public log entries yet.</p>`;
    return;
  }

  [...game.public_log].reverse().forEach((item) => {
    const row = document.createElement("article");
    row.className = "timeline-item";
    row.innerHTML = `
      <div class="timeline-item__meta">
        <span>Round ${item.round || 0}</span>
        <span>${item.phase}</span>
      </div>
      <p>${item.message}</p>
    `;
    list.appendChild(row);
  });
}

function renderStatus(payload) {
  state.game = payload.game || null;
  if (payload.available_personas) {
    state.personas = payload.available_personas;
  }

  const pill = document.getElementById("ollama-pill");
  pill.textContent = payload.ollama_available ? "Ollama online" : "Ollama offline";
  pill.classList.toggle("is-offline", !payload.ollama_available);

  const message = document.getElementById("status-message");
  message.textContent = state.game ? state.game.status_message : "No game in progress.";

  renderRoster(state.game);
  renderTimeline(state.game);
}

async function loadState() {
  const payload = await fetchJson("/api/state");
  if (!state.personas.length) {
    renderPersonas(payload.available_personas);
  }
  renderStatus(payload);
}

async function createGame() {
  const participants = selectedParticipants();
  if (participants.length < 4) {
    window.alert("Select at least 4 agents for Mafia.");
    return;
  }

  const seedValue = document.getElementById("seed-input").value;
  const payload = await fetchJson("/api/game", {
    method: "POST",
    body: JSON.stringify({
      participants,
      seed: seedValue ? Number(seedValue) : null,
    }),
  });
  renderStatus(payload);
}

async function advanceGame() {
  const payload = await fetchJson("/api/game/advance", {
    method: "POST",
    body: JSON.stringify({}),
  });
  renderStatus(payload);
}

async function resetGame() {
  const payload = await fetchJson("/api/game/reset", {
    method: "POST",
    body: JSON.stringify({}),
  });
  renderStatus(payload);
}

document.getElementById("new-game-button").addEventListener("click", () => {
  createGame().catch((error) => window.alert(error.message));
});

document.getElementById("advance-button").addEventListener("click", () => {
  advanceGame().catch((error) => window.alert(error.message));
});

document.getElementById("reset-button").addEventListener("click", () => {
  resetGame().catch((error) => window.alert(error.message));
});

loadState().catch((error) => {
  document.getElementById("status-message").textContent = error.message;
});
