# Realm: Catan Edition — Design Plan

A redesign of the Realm game replacing zero-sum raiding with Catan-style
resource production, building, and negotiated trading.

---

## Core Concept

Each faction controls a set of **nodes** on a shared board. Nodes produce
resources each turn based on type. Factions spend resources to build roads
and settlements, which expand their production and path toward victory.
Trading with other factions is the primary social mechanic.

---

## Board

A hex grid (simplified to a node graph for Discord/text). Each node has:
- A **terrain type** that determines what it produces
- A **number token** (2–12) that determines how often it produces
- An optional **building** (settlement or city) owned by a faction

At game start, nodes are randomly assigned terrain and number tokens.
Factions place 2 starting settlements on unclaimed nodes.

### Terrain Types → Resources
| Terrain    | Resource  |
|------------|-----------|
| Forest     | Wood      |
| Hills      | Brick     |
| Pasture    | Wool      |
| Fields     | Grain     |
| Mountains  | Ore       |
| Desert     | Nothing   |

---

## Turn Structure

```
1. PRODUCTION    — Roll 2d6. Every node whose token matches produces 1
                   resource for the faction that owns it.
                   (Roll of 7 → Robber event, see below)

2. TRADE         — Factions negotiate and execute bilateral trades.
                   Diplomacy prompt drives this. Each faction may make
                   one offer and accept or reject one offer.

3. BUILD         — Each faction chooses what to build (or passes).
```

---

## Resources

| Resource | Used for                          |
|----------|-----------------------------------|
| Wood     | Road, Settlement                  |
| Brick    | Road, Settlement                  |
| Wool     | Settlement, City upgrade          |
| Grain    | Settlement, City upgrade, Dev card |
| Ore      | City upgrade, Dev card            |

---

## Buildings & Costs

| Building   | Cost                        | Effect                                      |
|------------|-----------------------------|---------------------------------------------|
| Road       | 1 Wood + 1 Brick            | Extends your network; required before new settlement |
| Settlement | 1 Wood + 1 Brick + 1 Wool + 1 Grain | +1 VP, produces resources from adjacent nodes |
| City       | 2 Grain + 3 Ore (upgrades settlement) | +1 VP (2 total), produces 2 resources instead of 1 |
| Dev Card   | 1 Ore + 1 Wool + 1 Grain   | Random benefit (see below)                  |

---

## Victory Points

| Source              | Points |
|---------------------|--------|
| Settlement          | 1 VP   |
| City                | 2 VP   |
| Longest Road (5+)   | 2 VP   |
| Dev Card: VP        | 1 VP   |

**Win condition:** First to 10 VP wins.
**Turn limit fallback:** After MAX_TURNS, highest VP wins.

---

## The Robber (Roll of 7)

When a 7 is rolled:
- The rolling faction moves the **Robber** to any node
- That node stops producing until the Robber moves
- The rolling faction steals 1 random resource from a faction
  with a settlement adjacent to that node

The Robber is a key pressure mechanic — factions will negotiate around
it ("don't put the robber on my mountains and I'll trade you ore").

---

## Development Cards

Drew from a shuffled deck each time a faction pays the cost.

| Card           | Effect                                              |
|----------------|-----------------------------------------------------|
| Knight         | Move the Robber (same as rolling 7, but your choice)|
| Road Building  | Place 2 roads for free                              |
| Year of Plenty | Take any 2 resources from the bank                  |
| Monopoly       | All other factions give you all of one resource type|
| Victory Point  | +1 VP (kept secret until reveal at win)             |

**Largest Army:** faction that plays 3+ Knights gets +2 VP.
Can be stolen by playing more Knights.

---

## Trading

The trade phase replaces Realm's diplomacy phase. Two types:

### Faction Trade (negotiated)
- Any faction can offer: "I give 2 Ore, I want 1 Grain"
- Target faction accepts or rejects
- Mutual only — no forced trades
- LLM prompt: state what you have surplus of, what you need,
  make a specific offer to a named faction

### Bank Trade (always available, no negotiation)
- 4:1 — trade any 4 of the same resource for 1 of any other
- 3:1 — if you own a 3:1 harbor node, trade 3 of any for 1
- 2:1 — if you own a 2:1 harbor (resource-specific), trade 2 of that type for 1

Harbors are special nodes on the board edge — controlling one
with a settlement gives you the discount permanently.

---

## LLM Action Format

### Trade phase
```
OFFER: <resource> <amount>
WANT: <resource> <amount>
TARGET: <faction name | bank | none>
REASONING: <one sentence>
```

### Build phase
```
ACTION: <ROAD | SETTLEMENT | CITY | DEV_CARD | PASS>
LOCATION: <node id, or 'none'>
REASONING: <one sentence>
```

---

## Board Representation (text)

Since there's no visual grid in Discord, the board state is
described as a node list:

```
Node 4  [Fields  #9 ] — Aurion settlement  →  adjacent to nodes 1, 7, 8
Node 7  [Hills   #6 ] — (empty)            →  adjacent to nodes 4, 8, 12
Node 12 [Forest  #4 ] — Genghis settlement →  adjacent to nodes 7, 11, 13
...
```

Each faction's prompt includes:
- Their own node adjacencies and what they produce
- A summary of all other factions' settlements (not full board)
- Current resource hand
- What they can afford to build

---

## Personality Alignment

| Faction | Catan Archetype      | Behavior                                              |
|---------|----------------------|-------------------------------------------------------|
| Genghis | Aggressor            | Uses Robber constantly, plays Knights, targets leader |
| Joan    | Crusader             | Builds roads fast toward the leader to block them     |
| Aurion  | Curious trader       | Proposes unusual trades, values exploration over VP   |
| Itrion  | Economist            | Hoards resources, waits for the right build moment    |

---

## Implementation Notes

### New file: `catan_game.py`
- `Board` class — node graph with terrain, tokens, buildings
- `Faction` class — resources hand, road network, buildings, VP
- `CatanGame` class — turn loop, production, trade resolution, build resolution
- `build_trade_prompt()` — replaces diplomacy prompt
- `build_build_prompt()` — action phase
- `parse_trade()` / `parse_build()` — structured output parsers
- `render_board()` — text representation of board state
- `render_hand()` — faction's current resources

### Bot integration
- New `CATAN_CHANNEL_ID` env var
- `!catan start / turn / autoplay / status / stop`
- Same turn runner pattern as Realm and Accord

### Differences from current Realm
- No army/territory — replaced by resource hand + building slots
- Production is dice-driven (not deterministic) — adds variance
- Trade phase IS the social mechanic, not a prelude to fighting
- Board is persistent state between turns (buildings stay placed)
- Victory is multi-path (roads vs. cities vs. dev cards)

---

## Open Questions

1. **Board size** — how many nodes? 19 (standard Catan) is realistic
   but complex to represent in text. Could start with 7-node simplified map.

2. **Starting placement** — random assignment or let LLMs choose their
   starting nodes? LLM placement adds a strategic first-turn negotiation.

3. **Trade rejection cost** — in real Catan there's no cost to refusing.
   Could add a small morale/diplomacy penalty to make LLMs more willing
   to engage in trades rather than just banking everything.

4. **Async trades** — can factions trade with each other during the same
   turn, or only with the bank? Sequential faction-to-faction trading
   is simpler to implement.
