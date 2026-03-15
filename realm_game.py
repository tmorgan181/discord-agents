"""
Realm — A turn-based strategy simulation for Discord Atrium bots.

Territory is zero-sum: each faction starts with 2 territories (12 total).
The only way to gain territory is to RAID another faction and win.

Actions:
  TAX              - Gain 2 gold per territory owned
  RECRUIT          - Spend 3 gold, gain 2 army
  RAID <faction>   - Costs 1 gold; defender has 1.2× defense bonus; winner takes 1 territory
  TRADE <faction>  - Mutual only: both gain min(territories) × 4 gold; refused = trader gains nothing

Win condition: First faction to reach WIN_TERRITORIES territories wins.
If MAX_TURNS is reached, highest score (territory*2 + army) wins.
"""

import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

WIN_TERRITORIES = 6
MAX_TURNS = 12
RAID_COST = 1
DEFENDER_BONUS = 1.2  # defender army multiplier in raid probability

ACTIONS = ("TAX", "RECRUIT", "RAID", "TRADE")


@dataclass
class Faction:
    name: str
    gold: int = 5
    army: int = 3
    territory: int = 2

    @property
    def score(self) -> int:
        return self.territory * 100 + self.army * 10 + self.gold

    def tax_yield(self) -> int:
        return self.territory * 2


class RealmGame:
    def __init__(self, faction_names: list[str]):
        self.factions: dict[str, Faction] = {
            name: Faction(name=name) for name in faction_names
        }
        self.turn: int = 0
        self.started_at: datetime = datetime.now()
        self.winner: str | None = None
        self.event_history: list[str] = []
        self.faction_actions: dict[str, list[str]] = {name: [] for name in faction_names}

    # ── State ─────────────────────────────────────────────────────────────────

    def is_over(self) -> bool:
        return self.winner is not None or self.turn >= MAX_TURNS

    def render_state(self) -> str:
        turn_label = f"Turn {self.turn}/{MAX_TURNS}"
        win_label = f"(first to {WIN_TERRITORIES} territories wins · tiebreak: army → gold → sudden death)"
        lines = [f"**⚔️ Realm — {turn_label}** {win_label}", "```"]
        header = f"{'Faction':<14} {'Gold':>5} {'Army':>5} {'Territory':>10} {'Score':>6}"
        lines.append(header)
        lines.append("─" * len(header))
        for f in sorted(self.factions.values(), key=lambda x: -x.score):
            lines.append(
                f"{f.name:<14} {f.gold:>5} {f.army:>5} {f.territory:>10} {f.score:>6}"
            )
        lines.append("```")
        return "\n".join(lines)

    # ── Strategic context ──────────────────────────────────────────────────────

    def get_strategic_context(self, faction_name: str) -> str:
        f = self.factions[faction_name]
        by_territory = sorted(self.factions.values(), key=lambda x: -x.territory)
        by_army = sorted(self.factions.values(), key=lambda x: -x.army)
        territory_rank = [x.name for x in by_territory].index(faction_name) + 1
        army_rank = [x.name for x in by_army].index(faction_name) + 1
        leader = by_territory[0]
        turns_left = MAX_TURNS - self.turn
        need = WIN_TERRITORIES - f.territory

        lines = [
            f"Territory rank: #{territory_rank}/{len(self.factions)} | "
            f"Army rank: #{army_rank}/{len(self.factions)}",
            f"Leader: {leader.name} ({leader.territory} territories) | "
            f"Turns remaining: {turns_left}",
            f"You need {need} more {'territory' if need == 1 else 'territories'} to win.",
        ]

        # Note recent aggression patterns
        aggressive = [
            n for n, actions in self.faction_actions.items()
            if n != faction_name and actions and actions[-1].startswith("RAID")
        ]
        if aggressive:
            lines.append(f"Recently aggressive: {', '.join(aggressive)}")

        return "\n".join(lines)

    # ── Prompt building ────────────────────────────────────────────────────────

    def _raid_win_prob(self, attacker_army: int, defender_army: int) -> float:
        effective = defender_army * DEFENDER_BONUS
        total = attacker_army + effective
        return attacker_army / total if total > 0 else 0.5

    def build_realm_prompt(self, faction_name: str) -> str:
        f = self.factions[faction_name]
        others = {n: fac for n, fac in self.factions.items() if n != faction_name}
        history_text = (
            "\n".join(self.event_history[-8:]) if self.event_history else "No events yet — this is the first turn."
        )
        strategic = self.get_strategic_context(faction_name)

        # Build raid odds table
        raid_lines = []
        for n, fac in sorted(others.items(), key=lambda x: -x[1].territory):
            if fac.army == 0:
                raid_lines.append(
                    f"  RAID {n:<14} — 100% win chance  (UNDEFENDED — no army!)"
                )
            else:
                prob = self._raid_win_prob(f.army, fac.army)
                raid_lines.append(
                    f"  RAID {n:<14} — {prob:.0%} win chance  (your {f.army} army vs their {fac.army})"
                )
        raid_odds = "\n".join(raid_lines)

        # Warn if army is high but unused
        recent = self.faction_actions.get(faction_name, [])
        non_raid_streak = 0
        for act in reversed(recent):
            if act.startswith("RAID"):
                break
            non_raid_streak += 1

        streak_warning = ""
        if non_raid_streak >= 3 and f.army >= 5:
            streak_warning = (
                f"\n⚠️  WARNING: You have NOT raided in {non_raid_streak} turns with {f.army} army. "
                f"You are wasting your turns. RAID NOW or you cannot win.\n"
            )

        gold_warning = ""

        return (
            f"You are the {faction_name} faction in the Realm strategy game.\n\n"
            f"CURRENT STANDINGS:\n{self.render_state()}\n\n"
            f"YOUR RESOURCES: Gold={f.gold}, Army={f.army}, Territory={f.territory}\n"
            f"YOUR STRATEGIC POSITION:\n{strategic}\n"
            f"{streak_warning}{gold_warning}\n"
            f"RECENT EVENTS:\n{history_text}\n\n"
            f"AVAILABLE ACTIONS:\n"
            f"  TAX             — gain {f.tax_yield()} gold (territory × 2)\n"
            f"{'  RECRUIT         — spend 3 gold, gain 2 army (army cap: ' + str(f.territory * 3) + ')' if f.gold >= 3 and f.army < f.territory * 3 else '  RECRUIT         — UNAVAILABLE (need 3 gold, have ' + str(f.gold) + ('; army at cap' if f.army >= f.territory * 3 else '') + ')'}\n"
            f"  TRADE <faction> — mutual only: both gain min(territories) × 4 gold; refused = you gain nothing\n\n"
            f"RAID WIN PROBABILITIES (costs {RAID_COST} gold; winner takes 1 territory + target's gold÷territories):\n"
            f"{raid_odds if f.army > 0 else '  RAID — UNAVAILABLE (you have 0 army)'}\n\n"
            f"RULES:\n"
            f"- Territory is ZERO-SUM. RAID is the ONLY way to gain territory. No raids = no win.\n"
            f"- RECRUIT without RAIDing is pointless. Army only matters when used.\n"
            f"- Army is capped at 3× your territory ({f.territory * 3} max for you right now) — excess is lost.\n"
            f"- Gold is only useful if spent — hoarding it does not help you win.\n"
            f"- TRADE only works if both parties choose TRADE targeting each other simultaneously.\n"
            f"- If you have 5+ army and are behind on territory, you MUST RAID.\n\n"
            f"Reply in EXACTLY this format:\n"
            f"ACTION: <TAX | RECRUIT | RAID | TRADE>\n"
            f"TARGET: <faction name, or 'none'>\n"
            f"REASONING: <one sentence>\n\n"
            f"Example:\n"
            f"ACTION: RAID\n"
            f"TARGET: Aurion\n"
            f"REASONING: I have a 64% win chance and Aurion must be stopped before they reach {WIN_TERRITORIES}."
        )

    # ── Action parsing ─────────────────────────────────────────────────────────

    def parse_action(self, text: str) -> tuple[str, str | None, str]:
        """
        Parse structured LLM output (ACTION: / TARGET: / REASONING:).
        Returns (action, target_or_None, reasoning).
        Falls back gracefully to keyword scanning, then TAX.
        """
        action_val = ""
        target_val = ""
        reasoning_val = ""

        for line in text.strip().splitlines():
            upper = line.upper().lstrip()
            if upper.startswith("ACTION:"):
                action_val = line.split(":", 1)[1].strip().upper()
            elif upper.startswith("TARGET:"):
                target_val = line.split(":", 1)[1].strip()
            elif upper.startswith("REASONING:"):
                reasoning_val = line.split(":", 1)[1].strip()

        # Determine action
        action = "TAX"
        for a in ACTIONS:
            if a in action_val:
                action = a
                break
        else:
            # Structured format not found — fall back to scanning full text
            all_lines = text.strip().splitlines()
            first_line = all_lines[0].strip().upper() if all_lines else ""
            for a in ACTIONS:
                if first_line.startswith(a) or a in first_line:
                    action = a
                    break

        # Determine target
        target: str | None = None
        if action in ("RAID", "TRADE"):
            if target_val.lower() not in ("none", "", "n/a"):
                target = self._match_faction_name(target_val)
            # If structured target missing, scan full text
            if target is None:
                target = self._match_faction_name(text)

        # Reasoning fallback
        if not reasoning_val:
            lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
            # Use the last non-empty line that isn't an ACTION/TARGET line
            for line in reversed(lines):
                up = line.upper()
                if not up.startswith("ACTION:") and not up.startswith("TARGET:"):
                    reasoning_val = line
                    break

        return action, target, reasoning_val

    def _match_faction_name(self, text: str) -> str | None:
        text_lower = text.lower()
        for name in self.factions:
            if name.lower() in text_lower:
                return name
        return None

    # ── Resolution ────────────────────────────────────────────────────────────

    def resolve_turn(
        self, decisions: dict[str, tuple[str, str | None]]
    ) -> list[str]:
        """
        decisions: {faction_name: (action, target_or_None)}
        Mutates faction state. Returns list of event strings.
        Also updates event_history and faction_actions.
        """
        self.turn += 1
        events: list[str] = []
        raids: dict[str, str] = {}

        # Track what each faction did this turn
        for fname, (action, target) in decisions.items():
            action_desc = f"{action} {target}" if target else action
            self.faction_actions[fname].append(action_desc)

        # ── Pre-compute mutual trades ──────────────────────────────────────────
        # A trade only executes if BOTH parties chose TRADE targeting each other.
        mutual_trades: set[tuple[str, str]] = set()
        for fname, (action, target) in decisions.items():
            if action == "TRADE" and target and target in self.factions and target != fname:
                other_action, other_target = decisions.get(target, ("", None))
                if other_action == "TRADE" and other_target == fname:
                    mutual_trades.add(tuple(sorted([fname, target])))

        trades_done: set[tuple[str, str]] = set()

        # ── Non-raid / non-trade actions first ────────────────────────────────
        for fname, (action, target) in decisions.items():
            f = self.factions[fname]

            if action == "TAX":
                gain = f.tax_yield()
                f.gold += gain
                events.append(f"**{fname}** levies taxes, collecting {gain} gold.")

            elif action == "RECRUIT":
                army_cap = f.territory * 3
                if f.gold < 3:
                    gain = f.tax_yield()
                    f.gold += gain
                    events.append(
                        f"**{fname}** can't afford to recruit (needs 3 gold, has {f.gold - gain}) — taxes instead (+{gain} gold)."
                    )
                elif f.army >= army_cap:
                    gain = f.tax_yield()
                    f.gold += gain
                    events.append(
                        f"**{fname}** is already at army cap ({army_cap}) — taxes instead (+{gain} gold)."
                    )
                else:
                    f.gold -= 3
                    f.army += 2
                    events.append(f"**{fname}** recruits soldiers (+2 army, -3 gold).")

            elif action == "RAID":
                if target and target in self.factions and target != fname:
                    if f.army == 0:
                        gain = f.tax_yield()
                        f.gold += gain
                        events.append(
                            f"**{fname}** has no army to raid with — taxes instead (+{gain} gold)."
                        )
                    elif f.gold >= RAID_COST:
                        f.gold -= RAID_COST
                        raids[fname] = target
                    else:
                        gain = f.tax_yield()
                        f.gold += gain
                        events.append(
                            f"**{fname}** can't afford to raid (needs {RAID_COST} gold) — taxes instead (+{gain} gold)."
                        )
                else:
                    gain = f.tax_yield()
                    f.gold += gain
                    events.append(
                        f"**{fname}** finds no valid target to raid — taxes instead (+{gain} gold)."
                    )

            elif action == "TRADE":
                pair = tuple(sorted([fname, target or ""])) if target else None
                if pair and pair in mutual_trades:
                    if pair not in trades_done:
                        trades_done.add(pair)
                        gain = min(self.factions[fname].territory, self.factions[target].territory) * 4
                        self.factions[fname].gold += gain
                        self.factions[target].gold += gain
                        events.append(
                            f"**{fname}** and **{target}** strike a trade deal! "
                            f"(+{gain} gold each, based on {min(self.factions[fname].territory, self.factions[target].territory)} shared territories)"
                        )
                    # else: already recorded from the other side
                else:
                    # No mutual agreement — trader gets nothing
                    if target and target in self.factions:
                        events.append(
                            f"**{fname}** offered a trade to **{target}**, but was refused — lost turn, gained nothing."
                        )
                    else:
                        events.append(
                            f"**{fname}** attempted to trade but found no partner — lost turn, gained nothing."
                        )

        # ── Raids — phase 1: determine outcomes, apply army losses ───────────
        # Store (attacker, defender, won, attacker_army_before, defender_army_before)
        raid_results: list[tuple[str, str, bool, int, int]] = []
        for attacker_name, defender_name in raids.items():
            a = self.factions[attacker_name]
            d = self.factions[defender_name]
            effective_defender = d.army * DEFENDER_BONUS
            total = a.army + effective_defender
            win_prob = 1.0 if d.army == 0 else (a.army / total if total > 0 else 0.5)
            won = random.random() < win_prob
            a_before, d_before = a.army, d.army
            a.army = max(0, a.army - 1)          # attacker always risks 1
            if won:
                d.army = max(0, d.army - 1)      # defender loses 1 on loss
            raid_results.append((attacker_name, defender_name, won, a_before, d_before))

        # ── Raids — phase 2: distribute territory, contest if oversubscribed ──
        winners_by_defender: dict[str, list[str]] = defaultdict(list)
        for attacker_name, defender_name, won, a_before, d_before in raid_results:
            a = self.factions[attacker_name]
            d = self.factions[defender_name]
            if won:
                winners_by_defender[defender_name].append(attacker_name)
            else:
                events.append(
                    f"⚔️ **{attacker_name}** [{a_before}→{a.army}] raids "
                    f"**{defender_name}** [{d_before}] but is repelled!"
                )

        for defender_name, winner_names in winners_by_defender.items():
            d = self.factions[defender_name]
            available = d.territory  # territories left to claim

            if len(winner_names) <= available:
                # Straightforward — each winner claims one territory
                for wname in winner_names:
                    wa = self.factions[wname]
                    a_before = next(ab for an, _, won, ab, _ in raid_results if an == wname and won)
                    d_t_before = d.territory + 1
                    spoils = d.gold // d.territory if d.territory > 0 else 0
                    d.territory -= 1
                    wa.territory += 1
                    d.gold -= spoils
                    wa.gold += spoils
                    events.append(
                        f"⚔️ **{wname}** [{a_before}→{wa.army}] raids **{defender_name}** and wins! "
                        f"Seizes 1 territory ({wa.territory-1}→{wa.territory} vs {d_t_before}→{d.territory})"
                        + (f" and plunders {spoils} gold!" if spoils else ".")
                    )
            else:
                # More winners than territory — they clash for the spoils
                names_str = ", ".join(f"**{w}**" for w in winner_names)
                events.append(
                    f"⚔️ {names_str} all defeated **{defender_name}** but only "
                    f"{available} {'territory' if available == 1 else 'territories'} available — "
                    f"sudden death clash for the spoils!"
                )
                claimants = self._contest_spoils(winner_names, available, events)
                for wname in claimants:
                    wa = self.factions[wname]
                    spoils = d.gold // d.territory if d.territory > 0 else 0
                    d.territory -= 1
                    wa.territory += 1
                    d.gold -= spoils
                    wa.gold += spoils
                    events.append(
                        f"🏆 **{wname}** claims the contested territory from **{defender_name}** "
                        f"({wa.territory-1}→{wa.territory})"
                        + (f" and plunders {spoils} gold!" if spoils else ".")
                    )

        # ── Elimination check ─────────────────────────────────────────────────
        eliminated = [name for name, f in self.factions.items() if f.territory <= 0]
        for name in eliminated:
            f = self.factions.pop(name)
            self.faction_actions.pop(name, None)
            events.append(f"💀 **{name}** has been wiped from the map and is eliminated!")

        # ── Army cap (3× territory) ────────────────────────────────────────────
        for f in self.factions.values():
            cap = f.territory * 3
            if f.army > cap:
                f.army = cap

        # ── Win check ─────────────────────────────────────────────────────────
        if len(self.factions) == 1:
            f = next(iter(self.factions.values()))
            self.winner = f.name
            events.append(
                f"\n🏆 **{f.name}** is the last faction standing — the Realm is theirs!"
            )
            self.event_history.extend(events)
            return events

        for f in self.factions.values():
            if f.territory >= WIN_TERRITORIES:
                self.winner = f.name
                events.append(
                    f"\n🏆 **{f.name}** has conquered {WIN_TERRITORIES} territories — "
                    f"the Realm is theirs!"
                )
                self.event_history.extend(events)
                return events

        if self.turn >= MAX_TURNS:
            top_score = max(f.score for f in self.factions.values())
            leaders = [f for f in self.factions.values() if f.score == top_score]

            if len(leaders) == 1:
                self.winner = leaders[0].name
                events.append(
                    f"\n⏳ Time runs out! **{leaders[0].name}** holds the Realm "
                    f"with the highest score ({top_score})!"
                )
            else:
                # Tiebreak by gold
                top_gold = max(f.gold for f in leaders)
                gold_leaders = [f for f in leaders if f.gold == top_gold]

                if len(gold_leaders) == 1:
                    self.winner = gold_leaders[0].name
                    events.append(
                        f"\n⏳ Time runs out! Tied on score ({top_score}) — "
                        f"**{gold_leaders[0].name}** wins by gold ({top_gold})!"
                    )
                else:
                    # Still tied — sudden death battle royale
                    events.append(
                        f"\n⏳ Time runs out! {len(gold_leaders)} factions tied on score ({top_score}) "
                        f"and gold ({top_gold}) — **SUDDEN DEATH!**"
                    )
                    events.extend(self._sudden_death([f.name for f in gold_leaders]))

        self.event_history.extend(events)
        self.event_history = self.event_history[-24:]
        return events

    def _contest_spoils(self, contenders: list[str], slots: int, events: list[str]) -> list[str]:
        """
        Mini battle royale among raiders who all beat the same defender but there
        aren't enough territories for everyone. Fights until `slots` claimants remain.
        Returns the list of winners (length == slots).
        """
        alive = {name: max(self.factions[name].army, 1) for name in contenders}
        round_num = 0
        while len(alive) > slots:
            round_num += 1
            a, b = random.sample(list(alive), 2)
            total = alive[a] + alive[b]
            winner, loser = (a, b) if random.random() < alive[a] / total else (b, a)
            alive[loser] -= 1
            if alive[loser] <= 0:
                del alive[loser]
                events.append(f"  ⚔️ **{winner}** defeats **{loser}** in the clash!")
            else:
                events.append(f"  ⚔️ **{winner}** wounds **{loser}** ({alive[loser]} army left).")
        return list(alive.keys())

    def _sudden_death(self, contenders: list[str]) -> list[str]:
        """
        Battle royale among tied factions.
        Each round two random fighters clash (army-weighted). Loser loses 1 army;
        eliminated when army hits 0. Winner takes all gold from the fallen.
        """
        events = [
            "⚡ **SUDDEN DEATH** — the tied factions clash in a final battle royal!",
            f"Contenders: {', '.join(f'**{n}**' for n in contenders)}",
        ]
        alive = {name: max(self.factions[name].army, 1) for name in contenders}
        round_num = 0

        while len(alive) > 1:
            round_num += 1
            a, b = random.sample(list(alive), 2)
            total = alive[a] + alive[b]
            if random.random() < alive[a] / total:
                winner, loser = a, b
            else:
                winner, loser = b, a

            alive[loser] -= 1
            if alive[loser] <= 0:
                del alive[loser]
                # Winner loots the fallen faction's gold
                spoils = self.factions[loser].gold
                self.factions[winner].gold += spoils
                self.factions[loser].gold = 0
                events.append(
                    f"Round {round_num}: ⚔️ **{winner}** slays **{loser}**"
                    + (f" and seizes {spoils} gold!" if spoils else "!")
                )
            else:
                events.append(
                    f"Round {round_num}: **{winner}** wounds **{loser}** "
                    f"(army: {alive[loser]} remaining)."
                )

        champion = next(iter(alive))
        self.winner = champion
        total_gold = self.factions[champion].gold
        events.append(
            f"\n🏆 **{champion}** is the last one standing — "
            f"claims the Realm with {total_gold} gold in the treasury!"
        )
        return events

    def action_menu(self, faction_name: str) -> str:
        f = self.factions[faction_name]
        others = [n for n in self.factions if n != faction_name]
        return (
            f"TAX (gain {f.tax_yield()} gold) | "
            f"RECRUIT (3 gold → +2 army) | "
            f"RAID <faction> ({RAID_COST} gold, 1.5× defender bonus) | "
            f"TRADE <faction> (mutual only)  "
            f"[factions: {', '.join(others)}]"
        )
