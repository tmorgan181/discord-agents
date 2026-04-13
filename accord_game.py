"""
The Accord — A cooperative survival game for Discord Atrium bots.

All factions share a single settlement. Each turn a threat is drawn.
Factions negotiate, then each commits resources to a shared defense pool.
If the pool meets the threat threshold, the city is safe.
Partial contributions reduce damage proportionally.

Resources:
  food   — sustains population, needed for plague/famine threats
  stone  — structures, needed for floods/fires/earthquakes
  army   — soldiers, needed for raids/sieges
  gold   — wildcard: converts to any resource at 2:1 (1:1 for Itrion)

Specialties:
  Genghis  — army contributions count as 1.5×
  Joan     — if she contributes, everyone's pool gets +10% (morale)
  Aurion   — sees the exact threat strength; others see only a range
  Itrion   — gold converts at 1:1 instead of 2:1

Passive income: each turn every faction earns +1 of their specialty resource automatically.

Actions:
  GATHER                          — collect +1 of every resource type (no contribution this turn)
  CONTRIBUTE <resource> <amount>  — add to shared defense pool (resources are spent)
  SCOUT (Aurion only)             — reveal exact threat requirements to all

Win:  city HP > 0 after all turns
Lose: city HP reaches 0
"""

import random
from dataclasses import dataclass
from datetime import datetime

CITY_START_HP = 20
MAX_TURNS = 12
GATHER_BONUS = 2       # GATHER gives +2 of every resource type
SPECIALTY_INCOME = 1   # passive +1 of specialty resource each turn

SPECIALTIES = {
    "Genghis": "army",
    "Joan":    "morale",   # special — not a resource, handled in resolution
    "Aurion":  "scout",    # special — not a resource, handled in prompts
    "Itrion":  "gold",
}

# What resource each specialty gathers
GATHER_RESOURCE = {
    "army":   "army",   # Genghis — trains soldiers
    "morale": "food",   # Joan — feeds and rallies the people
    "scout":  "stone",  # Aurion — finds raw materials on expeditions
    "gold":   "gold",   # Itrion — generates trade income
}

RESOURCES = ("food", "stone", "army", "gold")

THREAT_POOL = [
    {"name": "Bandit Raid",  "requirements": {"army": 5, "stone": 2},          "max_damage": 8,  "description": "Raiders sweep in from the eastern hills. Fight them off and repair the breached walls."},
    {"name": "Spring Flood", "requirements": {"stone": 4, "army": 2},           "max_damage": 7,  "description": "The river breaks its banks. Shore up the levees and evacuate the lower districts."},
    {"name": "Plague",       "requirements": {"food": 4, "gold": 3},            "max_damage": 9,  "description": "Sickness spreads through the population. Feed the sick and buy medicine."},
    {"name": "Famine",       "requirements": {"food": 6, "gold": 2},            "max_damage": 10, "description": "The harvest failed. Empty granaries and no trade caravans in sight."},
    {"name": "Wildfire",     "requirements": {"stone": 3, "food": 2, "army": 1},"max_damage": 6,  "description": "Fire tears through the merchant quarter. Tear down buildings, feed the displaced, and hold the line."},
    {"name": "Harsh Winter", "requirements": {"food": 5, "stone": 3},           "max_damage": 8,  "description": "An early frost locks the roads. Stockpile food and reinforce shelters before the cold kills."},
    {"name": "Siege",        "requirements": {"army": 6, "stone": 3},           "max_damage": 14, "description": "An enemy army surrounds the walls. Hold them off and keep the fortifications standing."},
    {"name": "Earthquake",   "requirements": {"stone": 4, "gold": 3},           "max_damage": 7,  "description": "The ground shakes. Collapsed structures need immediate repair and emergency funds."},
    {"name": "Desertion",    "requirements": {"army": 3, "food": 3},            "max_damage": 5,  "description": "Morale is low. Soldiers are leaving — keep them fed and recall them to their posts."},
    {"name": "Drought",      "requirements": {"food": 3, "gold": 3, "stone": 2},"max_damage": 6,  "description": "Wells are drying up. Ration food, pay for water imports, and dig emergency cisterns."},
]


@dataclass
class Faction:
    name: str
    food: int = 4
    stone: int = 4
    army: int = 4
    gold: int = 4
    specialty: str = ""

    def get(self, resource: str) -> int:
        return getattr(self, resource, 0)

    def spend(self, resource: str, amount: int) -> int:
        """Spend up to amount, return how much was actually spent."""
        current = self.get(resource)
        spent = min(current, amount)
        setattr(self, resource, current - spent)
        return spent

    def add(self, resource: str, amount: int):
        setattr(self, resource, self.get(resource) + amount)

    def total_resources(self) -> int:
        return self.food + self.stone + self.army + self.gold


@dataclass
class Threat:
    name: str
    description: str
    requirements: dict  # {resource: amount_needed}
    max_damage: int     # HP lost if nothing is contributed

    @property
    def total_threshold(self) -> int:
        return sum(self.requirements.values())


class AccordGame:
    def __init__(self, faction_names: list[str]):
        self.factions: dict[str, Faction] = {}
        for name in faction_names:
            specialty = SPECIALTIES.get(name, "")
            f = Faction(name=name, specialty=specialty)
            # Boost starting resources toward specialty
            if specialty == "army":
                f.army = 6
            elif specialty == "gold":
                f.gold = 6
            elif specialty == "morale":
                f.food = 6
            elif specialty == "scout":
                f.stone = 6
            self.factions[name] = f

        self.city_hp: int = CITY_START_HP
        self.turn: int = 0
        self.current_threat: Threat | None = None
        self.scouted: bool = False   # True once Aurion uses SCOUT this turn
        self.event_history: list[str] = []
        self.negotiation_log: list[str] = []
        self.started_at: datetime = datetime.now()
        self._threat_deck = self._build_deck()
        self.fallen: dict[str, str] = {}  # faction_name -> resource that killed them

    # ── Setup ─────────────────────────────────────────────────────────────────

    def _build_deck(self) -> list[dict]:
        """Shuffle threat pool with mild difficulty scaling baked in."""
        pool = THREAT_POOL.copy()
        random.shuffle(pool)
        return pool

    def draw_threat(self) -> Threat:
        if not self._threat_deck:
            self._threat_deck = self._build_deck()
        t = self._threat_deck.pop(0)
        # Scale difficulty with turns (up to +40% by turn 12)
        scale = 1.0 + (self.turn / MAX_TURNS) * 0.4
        scaled_reqs = {
            res: max(1, round(base * scale) + random.randint(-1, 1))
            for res, base in t["requirements"].items()
        }
        self.current_threat = Threat(
            name=t["name"],
            description=t["description"],
            requirements=scaled_reqs,
            max_damage=t["max_damage"],
        )
        self.scouted = False
        return self.current_threat

    def check_perished(self) -> list[tuple[str, str]]:
        """
        Check if any active faction has 0 of a resource required by the current threat.
        Perished factions are moved to self.fallen and removed from self.factions.
        Returns list of (faction_name, resource) for each faction that perished.
        """
        t = self.current_threat
        if not t:
            return []
        perished = []
        for name, f in list(self.factions.items()):
            for resource in t.requirements:
                if f.get(resource) == 0:
                    perished.append((name, resource))
                    self.fallen[name] = resource
                    del self.factions[name]
                    break
        return perished

    # ── State ─────────────────────────────────────────────────────────────────

    def is_over(self) -> bool:
        return self.city_hp <= 0 or self.turn >= MAX_TURNS or len(self.factions) == 0

    def render_state(self) -> str:
        hp_bar = self._hp_bar(self.city_hp, CITY_START_HP)
        lines = [
            f"**🏰 The Accord — Turn {self.turn}/{MAX_TURNS}**",
            f"City HP: {hp_bar} {self.city_hp}/{CITY_START_HP}",
            "```",
            f"{'Faction':<12} {'Food':>5} {'Stone':>6} {'Army':>5} {'Gold':>5} {'Specialty':>10}",
            "─" * 48,
        ]
        for f in self.factions.values():
            lines.append(
                f"{f.name:<12} {f.food:>5} {f.stone:>6} {f.army:>5} {f.gold:>5} {f.specialty:>10}"
            )
        for name, resource in self.fallen.items():
            lines.append(f"{'☠ ' + name:<12} {'—':>5} {'—':>6} {'—':>5} {'—':>5} {'depleted ' + resource:>10}")
        lines.append("```")
        return "\n".join(lines)

    def _hp_bar(self, hp: int, max_hp: int, width: int = 10) -> str:
        filled = round((hp / max_hp) * width)
        return "█" * filled + "░" * (width - filled)

    # ── Prompt building ────────────────────────────────────────────────────────

    def build_scout_report(self, faction_name: str) -> str:
        """Everyone sees fuzzy ranges unless Aurion has used SCOUT this turn."""
        t = self.current_threat
        if not t:
            return "No threat active."

        req_lines = []
        for resource, amount in t.requirements.items():
            if self.scouted:
                req_lines.append(f"  {resource}: exactly **{amount}** needed  ✓ scouted")
            else:
                lo = max(1, amount - 2)
                hi = amount + 2
                req_lines.append(f"  {resource}: roughly **{lo}–{hi}** needed")
        reqs_str = "\n".join(req_lines)
        scout_hint = "" if self.scouted else "  *(Aurion can SCOUT to reveal exact figures — costs her action)*"

        return (
            f"Requirements:\n{reqs_str}\n"
            f"{scout_hint}\n"
            f"Max damage if fully undefended: {t.max_damage} HP."
        )

    def build_negotiation_prompt(self, faction_name: str) -> str:
        f = self.factions[faction_name]
        t = self.current_threat
        others = ", ".join(n for n in self.factions if n != faction_name)
        prior = "\n".join(f"  {line}" for line in self.negotiation_log)
        prior_section = f"\nWHAT OTHERS HAVE SAID:\n{prior}\n" if self.negotiation_log else ""
        specialty_note = self._specialty_note(faction_name)

        # Build concise threat summary — inline, not the formatted block (avoids small-model echoing)
        if t:
            if self.scouted:
                req_summary = ", ".join(f"{r}={v}" for r, v in t.requirements.items())
            else:
                req_summary = ", ".join(
                    f"{r}=~{max(1,v-2)}–{v+2}" for r, v in t.requirements.items()
                )
            req_keys = list(t.requirements.keys())
            req_str = " and ".join(req_keys)
            threat_line = (
                f"Threat needs: {req_summary} (max damage: {t.max_damage} HP). "
                f"Only {req_str} help this turn."
            )
        else:
            threat_line = ""

        # Show only resources the faction actually has any of, to prevent phantom commitments
        can_contribute = [r for r in (t.requirements.keys() if t else []) if f.get(r) > 0]
        can_str = ", ".join(f"{r}={f.get(r)}" for r in can_contribute) if can_contribute else "none of the required resources"

        return (
            f"You are {faction_name} in The Accord. NEGOTIATION PHASE — Turn {self.turn + 1}.\n\n"
            f"City HP: {self.city_hp}/{CITY_START_HP} | {threat_line}\n"
            f"{prior_section}\n"
            f"YOUR RESOURCES: Food={f.food}, Stone={f.stone}, Army={f.army}, Gold={f.gold}\n"
            f"You can contribute: {can_str}\n"
            f"{specialty_note}\n"
            f"Coordinate with {others}. State which required resource you will commit and how much. "
            f"Do not promise resources you do not have. One or two sentences. In character."
        )

    def build_reasoning_prompt(self, faction_name: str) -> str:
        f = self.factions[faction_name]
        t = self.current_threat
        scout_report = self.build_scout_report(faction_name)
        history_text = "\n".join(self.event_history[-6:]) if self.event_history else "No events yet."
        specialty_note = self._specialty_note(faction_name)
        neg_section = (
            "NEGOTIATION THIS TURN:\n" + "\n".join(f"  {l}" for l in self.negotiation_log) + "\n\n"
            if self.negotiation_log else ""
        )

        required_resources = list(t.requirements.items()) if t else []
        req_analysis = "\n".join(
            f"  - {res}: need {amt}, you have {f.get(res)} → can cover {min(f.get(res), amt)}"
            for res, amt in required_resources
        )

        # Check which of the bot's resources are at 0 across ALL resource types (not just threat reqs)
        all_zeroes = [r for r in RESOURCES if f.get(r) == 0]
        zero_warning = (
            f"⚠️ INSURANCE WARNING: You have 0 {', '.join(all_zeroes)}. "
            f"If any future threat requires those, you are instantly eliminated. GATHER rebuilds all resources.\n"
        ) if all_zeroes else ""

        return (
            f"You are {faction_name} in The Accord. Turn {self.turn + 1}/{MAX_TURNS}.\n\n"
            f"CITY HP: {self.city_hp}/{CITY_START_HP}\n"
            f"{self.render_state()}\n"
            f"THREAT:\n{scout_report}\n\n"
            f"YOUR RESOURCES: Food={f.food}, Stone={f.stone}, Army={f.army}, Gold={f.gold}\n"
            f"{specialty_note}\n"
            f"{zero_warning}"
            f"{neg_section}"
            f"REQUIRED RESOURCES ANALYSIS:\n{req_analysis}\n"
            f"⚠️ Only the required resources above matter. Contributing the wrong resource does nothing.\n\n"
            f"RECENT EVENTS:\n{history_text}\n\n"
            f"Think through privately:\n"
            f"1. Threat severity: city has {self.city_hp} HP, this does up to {t.max_damage if t else '?'} damage. How bad is a partial failure?\n"
            f"2. Per-resource: for each required resource, how much can YOU personally cover? Commit that amount.\n"
            f"3. Promise check: did you say you'd contribute something in negotiation? If yes, follow through — defecting leaves a gap.\n"
            f"4. Prisoner's dilemma: if everyone reasons 'others will cover it', nobody contributes and the city takes full damage.\n"
            f"5. Insurance: keep at least 1 of every resource to avoid surprise elimination. If any are at 0, GATHER first unless the threat is catastrophic.\n"
            f"6. GATHER only if: your stockpile is critically low AND the defense status shows others have already covered all requirements.\n\n"
            f"This is private. No one else will see it. Think freely."
        )

    def _render_committed(self, committed_so_far: dict, requirements: dict) -> str:
        """Show per-resource contributions with effectiveness labels and remaining gaps."""
        if not committed_so_far:
            header = "COMMITTED SO FAR: (you are first)\n"
        else:
            lines = ["COMMITTED SO FAR:"]
            for fname, (action, res, amt) in committed_so_far.items():
                if action == "GATHER":
                    lines.append(f"  {fname}: GATHER (+{GATHER_BONUS} each resource)")
                elif action == "CONTRIBUTE":
                    eff = self._contribution_effectiveness(fname, res, amt, res)
                    bonus_note = ""
                    if fname == "Genghis" and res == "army":
                        bonus_note = f" → counts as {eff:.0f} (1.5× specialty)"
                    elif fname == "Joan":
                        bonus_note = " → +10% morale bonus to ALL if she contributes"
                    elif fname == "Itrion" and res == "gold":
                        bonus_note = " → counts as full value (1:1 specialty)"
                    lines.append(f"  {fname}: {amt} {res}{bonus_note}")
            header = "\n".join(lines) + "\n"

        if not requirements:
            return header

        # Tally effective contributions per requirement
        per_res: dict[str, float] = {r: 0.0 for r in requirements}
        gold_pool = 0.0
        for fname, (action, res, amt) in committed_so_far.items():
            if action != "CONTRIBUTE":
                continue
            if res in per_res:
                per_res[res] += self._contribution_effectiveness(fname, res, amt, res)
            elif res == "gold":
                gold_pool += self._contribution_effectiveness(fname, res, amt, "gold")

        status_lines = ["DEFENSE STATUS:"]
        remaining_total = 0.0
        for resource, needed in requirements.items():
            have = per_res.get(resource, 0.0)
            remaining = max(0.0, needed - have)
            remaining_total += remaining
            if remaining <= 0:
                status_lines.append(f"  {resource}: {have:.0f}/{needed} ✓ COVERED")
            else:
                status_lines.append(f"  {resource}: {have:.0f}/{needed} — ⚠️ NEED {remaining:.0f} MORE")
        if gold_pool > 0:
            net_remaining = max(0.0, remaining_total - gold_pool)
            status_lines.append(f"  gold (wildcard): {gold_pool:.0f} → gap reduced to {net_remaining:.0f}")
        return header + "\n".join(status_lines) + "\n"

    def _contribution_effectiveness(self, fname: str, resource: str, amount: int, target_resource: str) -> float:
        if resource == target_resource:
            if resource == "army" and fname == "Genghis":
                return amount * 1.5
            return float(amount)
        elif resource == "gold":
            return amount * (1.0 if fname == "Itrion" else 0.5)
        return 0.0

    def build_commitment_prompt(self, faction_name: str, reasoning: str = "", committed_so_far: dict | None = None) -> str:
        f = self.factions[faction_name]
        t = self.current_threat
        scout_report = self.build_scout_report(faction_name)
        specialty_note = self._specialty_note(faction_name)
        neg_section = (
            "NEGOTIATION THIS TURN:\n" + "\n".join(f"  {l}" for l in self.negotiation_log) + "\n\n"
            if self.negotiation_log else ""
        )
        reasoning_section = (
            f"YOUR PRIVATE REASONING:\n{reasoning}\n\nNow commit to your contribution.\n\n"
            if reasoning else ""
        )

        gather_res = GATHER_RESOURCE.get(SPECIALTIES.get(faction_name, ""), "")
        t = self.current_threat
        committed_section = self._render_committed(committed_so_far or {}, t.requirements if t else {}) + "\n"

        required_resources = list(t.requirements.keys()) if t else []
        req_str = ", ".join(required_resources)
        gold_rate = "1:1" if f.specialty == "gold" else "2:1"
        req_list = " AND ".join(r.upper() for r in required_resources)
        what_counts = (
            f"⚠️ THIS THREAT NEEDS ALL OF: {req_list}\n"
            f"   If any one requirement is unmet, the city takes damage — covering one and ignoring another still fails.\n"
            f"   Gold is a wildcard ({gold_rate}). Anything NOT on the list above does NOTHING.\n"
        )

        # Elimination risk for self
        elim_risk_lines = []
        for resource in required_resources:
            val = f.get(resource)
            if val == 0:
                elim_risk_lines.append(f"  🚨 {resource.upper()}: you have 0 — if the NEXT threat requires {resource}, YOU ARE ELIMINATED.")
            elif val <= 2:
                elim_risk_lines.append(f"  ⚠️ {resource.upper()}: only {val} left — dangerously low.")
        elim_warning = (
            "ELIMINATION RISK:\n" + "\n".join(elim_risk_lines) + "\n"
            f"  GATHER gives +{GATHER_BONUS} of every resource — use it to rebuild if others can cover this threat.\n"
        ) if elim_risk_lines else ""

        # Other factions at elimination risk
        at_risk = []
        for other_name, other_f in self.factions.items():
            if other_name == faction_name:
                continue
            for resource in required_resources:
                if other_f.get(resource) == 0:
                    at_risk.append(f"{other_name} (0 {resource})")
                    break
        faction_risk = (
            f"ALLIES AT RISK: {', '.join(at_risk)} — if the next threat requires their depleted resource, they're gone.\n"
            f"  Losing factions means fewer people to share future threats.\n"
        ) if at_risk else ""

        # Check if they made promises in negotiation
        my_promises = [l for l in self.negotiation_log if l.startswith(f"{faction_name}:")]
        promise_note = (
            "YOU SAID IN NEGOTIATION:\n" + "\n".join(f"  {l}" for l in my_promises) + "\n"
            "  ⚠️ Follow through. Defecting after promising breaks trust and leaves gaps uncovered.\n"
        ) if my_promises else ""

        return (
            f"You are {faction_name} in The Accord. ACTION PHASE — Turn {self.turn + 1}.\n\n"
            f"CITY HP: {self.city_hp}/{CITY_START_HP}\n"
            f"THREAT:\n{scout_report}\n\n"
            f"{what_counts}\n"
            f"YOUR RESOURCES: Food={f.food}, Stone={f.stone}, Army={f.army}, Gold={f.gold}\n"
            f"{specialty_note}\n"
            f"{elim_warning}\n"
            f"{faction_risk}"
            f"{promise_note}"
            f"{neg_section}"
            f"{committed_section}"
            f"{reasoning_section}"
            f"CHOOSE ONE ACTION:\n"
            + (f"  SCOUT      — reveal exact threat requirements to everyone (costs your action this turn).\n" if SPECIALTIES.get(faction_name) == "scout" else "")
            + f"  GATHER     — collect +{GATHER_BONUS} of every resource (food, stone, army, gold). You contribute nothing to defense this turn.\n"
            f"  CONTRIBUTE — spend your resources on the shared defense pool. They are gone permanently.\n\n"
            f"If you CONTRIBUTE, pick a REQUIRED resource you actually have. Do not contribute resources not on the list.\n"
            f"Use GATHER only if your stockpile is critically low AND the defense status above shows the threat is already covered.\n\n"
            f"Reply in EXACTLY this format:\n"
            f"ACTION: <GATHER | CONTRIBUTE>\n"
            f"RESOURCE: <food | stone | army | gold | none>  (only for CONTRIBUTE)\n"
            f"AMOUNT: <number, or 0>                         (only for CONTRIBUTE)\n"
            f"REASONING: <one sentence>\n\n"
            f"Example (contribute):\n"
            f"ACTION: CONTRIBUTE\n"
            f"RESOURCE: army\n"
            f"AMOUNT: 3\n"
            f"REASONING: The raid needs army and I have enough to spare.\n\n"
            f"Example (gather):\n"
            f"ACTION: GATHER\n"
            f"RESOURCE: none\n"
            f"AMOUNT: 0\n"
            f"REASONING: My stockpile is critically low and the defense status shows the threat is covered."
        )

    def _specialty_note(self, faction_name: str) -> str:
        specialty = SPECIALTIES.get(faction_name, "")
        specialty_res = GATHER_RESOURCE.get(specialty, "")
        notes = {
            "army":   f"YOUR SPECIALTY: Army. You earn +{SPECIALTY_INCOME} army automatically each turn. Army contributions count as 1.5× in defense.",
            "morale": f"YOUR SPECIALTY: Morale. You earn +{SPECIALTY_INCOME} food automatically each turn. If you CONTRIBUTE anything this turn, all contributions gain +10%. If you GATHER instead, nobody gets this bonus.",
            "scout":  f"YOUR SPECIALTY: Scout. You earn +{SPECIALTY_INCOME} stone automatically each turn. SCOUT reveals exact requirements to everyone — but costs your action (you contribute nothing).",
            "gold":   f"YOUR SPECIALTY: Trade. You earn +{SPECIALTY_INCOME} gold automatically each turn. Your gold converts at 1:1 instead of 2:1.",
        }
        return notes.get(specialty, "")

    # ── Parsing ────────────────────────────────────────────────────────────────

    def parse_commitment(self, text: str, faction_name: str) -> tuple[str, str, int, str]:
        """
        Parse ACTION/RESOURCE/AMOUNT/REASONING from LLM output.
        Returns (action, resource, amount, reasoning).
        """
        action = "GATHER"
        resource = "none"
        amount = 0
        reasoning = ""

        for line in text.strip().splitlines():
            upper = line.upper().lstrip()
            if upper.startswith("ACTION:"):
                val = line.split(":", 1)[1].strip().upper()
                if "CONTRIBUTE" in val:
                    action = "CONTRIBUTE"
                elif "SCOUT" in val:
                    action = "SCOUT"
                elif "GATHER" in val:
                    action = "GATHER"
            elif upper.startswith("RESOURCE:"):
                val = line.split(":", 1)[1].strip().lower()
                if val in RESOURCES:
                    resource = val
            elif upper.startswith("AMOUNT:"):
                try:
                    amount = int(line.split(":", 1)[1].strip())
                except ValueError:
                    amount = 0
            elif upper.startswith("REASONING:"):
                reasoning = line.split(":", 1)[1].strip()

        if action == "SCOUT":
            resource, amount = "none", 0
        elif action == "GATHER":
            resource, amount = "all", GATHER_BONUS
        elif action != "CONTRIBUTE" or resource == "none":
            # No HOARD — default to GATHER if output couldn't be parsed as CONTRIBUTE
            action, resource, amount = "GATHER", "all", GATHER_BONUS

        return action, resource, amount, reasoning

    # ── Resolution ────────────────────────────────────────────────────────────

    def resolve_turn(
        self, commitments: dict[str, tuple[str, str, int]]
    ) -> list[str]:
        """
        commitments: {faction_name: (action, resource, amount)}
        Applies commitments, resolves threat, regenerates resources.
        Returns list of event strings.
        """
        self.turn += 1
        t = self.current_threat
        events: list[str] = []

        if not t:
            events.append("No threat to resolve.")
            return events

        # ── Deduct committed resources from factions ───────────────────────
        actual_spent: dict[str, tuple[str, int]] = {}  # fname -> (resource, actual_amount)
        joan_contributed = False

        for fname, (action, resource, amount) in commitments.items():
            if fname not in self.factions:
                continue  # faction perished before resolution
            f = self.factions[fname]
            if action == "CONTRIBUTE" and resource != "none" and amount > 0:
                actual = f.spend(resource, amount)
                actual_spent[fname] = (resource, actual)
                if actual > 0:
                    events.append(f"  {fname} contributes {actual} {resource}.")
                    if fname == "Joan":
                        joan_contributed = True
                else:
                    events.append(f"  {fname} holds back (no {resource} to spend).")
            elif action == "GATHER":
                for res in RESOURCES:
                    f.add(res, GATHER_BONUS)
                events.append(f"  {fname} gathers (+{GATHER_BONUS} each resource).")

        # ── Calculate effective pool per requirement (using actual spent) ──
        per_res: dict[str, float] = {r: 0.0 for r in t.requirements}
        gold_pool = 0.0

        for fname, (resource, actual) in actual_spent.items():
            if actual <= 0:
                continue
            if resource in per_res:
                per_res[resource] += self._contribution_effectiveness(fname, resource, actual, resource)
            elif resource == "gold":
                gold_pool += self._contribution_effectiveness(fname, resource, actual, "gold")

        # Joan morale boost — applies to all primary contributions
        if joan_contributed:
            per_res = {r: v * 1.1 for r, v in per_res.items()}
            gold_pool *= 1.1

        # Shortfall per resource; gold fills the largest gaps first
        shortfalls = {r: max(0.0, t.requirements[r] - per_res[r]) for r in t.requirements}
        total_shortfall = sum(shortfalls.values())
        total_shortfall = max(0.0, total_shortfall - gold_pool)
        total_threshold = t.total_threshold

        # Build per-resource breakdown for event log
        breakdown_parts = []
        for r, needed in t.requirements.items():
            have = round(per_res[r], 1)
            breakdown_parts.append(f"{r}: {have}/{needed}")
        if gold_pool > 0:
            breakdown_parts.append(f"gold (wildcard): {gold_pool:.1f}")

        # ── Calculate damage ───────────────────────────────────────────────
        if total_shortfall <= 0:
            damage = 0
            events.append(
                f"\n✅ **{t.name} repelled!** [{' · '.join(breakdown_parts)}] — the city holds."
            )
        else:
            ratio = total_shortfall / total_threshold
            damage = round(ratio * t.max_damage)
            self.city_hp = max(0, self.city_hp - damage)
            severity = "⚠️" if damage <= 4 else "🔥" if damage <= 8 else "💀"
            events.append(
                f"\n{severity} **{t.name} broke through!** [{' · '.join(breakdown_parts)}] "
                f"— shortfall {total_shortfall:.1f}/{total_threshold}. "
                f"City takes **{damage} damage** ({self.city_hp}/{CITY_START_HP} HP remaining)."
            )

        # ── Specialty income — each faction earns +1 of their specialty resource ─
        for f in self.factions.values():
            specialty = SPECIALTIES.get(f.name, "")
            res = GATHER_RESOURCE.get(specialty, "")
            if res:
                f.add(res, SPECIALTY_INCOME)

        # ── Win/lose check ─────────────────────────────────────────────────
        if self.city_hp <= 0:
            events.append("\n💀 **The city has fallen. The Accord is broken.**")
        elif len(self.factions) == 0:
            events.append("\n💀 **All settlements have fallen. The city is abandoned.**")
        elif self.turn >= MAX_TURNS:
            events.append(
                f"\n🏆 **The city survives!** "
                f"All {MAX_TURNS} turns weathered — the settlement stands with {self.city_hp} HP remaining."
            )

        self.event_history.extend(events)
        self.event_history = self.event_history[-30:]
        self.current_threat = None
        return events
